"""
響度量測與自動音量平衡 (EBU R128 / ITU-R BS.1770-4)

商用 KTV 點歌機不會讓「上一首震耳欲聾、下一首要把音量轉到底才聽得到」這種事發生。
YouTube 上的來源千奇百怪：官方 MV 母帶壓到 -8 LUFS，個人上傳的現場版可能只有 -22 LUFS，
中間差了 14 dB —— 換算成主觀音量差不多是「三倍大聲」。

這裡在流水線跑完分離之後量一次伴奏軌的整合響度 (integrated loudness)，
把 `gain_db` 存進 metadata，播放端在 Web Audio 的正規化增益節點上套用，
所有歌曲聽起來就一樣大聲。量測只做一次，之後每次播放都是查表。

為什麼自己實作而不是直接抓 pyloudnorm：
  1. 這裡只需要 integrated loudness 一個數字，完整套件多帶一層相依。
  2. 純函數（吃 numpy 陣列、吐一個數字）才好在 CI 上用合成訊號測，
     不必準備音檔、也不必裝 librosa / torch。
K 加權濾波器係數依 BS.1770-4 的類比原型在任意取樣率下重新推導，
在 48kHz 會退化成規格書列出的那組參考係數。
"""
import logging
import math
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger("KaraTube.Loudness")

# --- BS.1770-4 K 加權：兩段濾波器的類比原型參數 ---
# 第一段：頭部遮蔽的高頻棚 (high shelf)，第二段：RLB 高通。
SHELF_GAIN_DB = 3.999843853973347
SHELF_Q = 0.7071752369554196
SHELF_FC = 1681.974450955533
HPF_Q = 0.5003270373238773
HPF_FC = 38.13547087602444

BLOCK_SEC = 0.400          # 400ms 一個量測區塊
BLOCK_OVERLAP = 0.75       # 75% 重疊 → 每 100ms 前進一個區塊
ABSOLUTE_GATE_LUFS = -70.0  # 絕對閘：比這安靜的區塊不算數（歌曲前後的無聲）
RELATIVE_GATE_LU = -10.0    # 相對閘：比整體平均低 10 LU 的區塊不算數
LOUDNESS_OFFSET = -0.691    # 規格書的校準常數


def shelf_coefficients(sample_rate: float) -> Tuple[np.ndarray, np.ndarray]:
    """K 加權第一段：高頻棚。回傳 (b, a)。"""
    vh = 10.0 ** (SHELF_GAIN_DB / 20.0)
    vb = vh ** 0.4996667741545416
    k = math.tan(math.pi * SHELF_FC / sample_rate)
    denom = 1.0 + k / SHELF_Q + k * k
    b = np.array([
        (vh + vb * k / SHELF_Q + k * k) / denom,
        2.0 * (k * k - vh) / denom,
        (vh - vb * k / SHELF_Q + k * k) / denom,
    ])
    a = np.array([
        1.0,
        2.0 * (k * k - 1.0) / denom,
        (1.0 - k / SHELF_Q + k * k) / denom,
    ])
    return b, a


def highpass_coefficients(sample_rate: float) -> Tuple[np.ndarray, np.ndarray]:
    """K 加權第二段：RLB 高通。回傳 (b, a)。"""
    k = math.tan(math.pi * HPF_FC / sample_rate)
    denom = 1.0 + k / HPF_Q + k * k
    b = np.array([1.0, -2.0, 1.0])
    a = np.array([
        1.0,
        2.0 * (k * k - 1.0) / denom,
        (1.0 - k / HPF_Q + k * k) / denom,
    ])
    return b, a


def _lfilter_numpy(b: Sequence[float], a: Sequence[float], x: np.ndarray) -> np.ndarray:
    """
    沒有 scipy 時的備援 IIR 濾波（直接第二型轉置）。

    每個時間點做一次迴圈、聲道方向向量化。整首歌用這條會慢，
    但正式環境有 scipy（requirements.txt 內），這裡只是讓
    「只裝 numpy 的 CI」也跑得動單元測試。
    """
    b = np.asarray(b, dtype=np.float64)
    a = np.asarray(a, dtype=np.float64)
    x = np.asarray(x, dtype=np.float64)
    y = np.empty_like(x)
    z1 = np.zeros(x.shape[1:], dtype=np.float64)
    z2 = np.zeros(x.shape[1:], dtype=np.float64)
    for n in range(x.shape[0]):
        xn = x[n]
        yn = b[0] * xn + z1
        z1 = b[1] * xn - a[1] * yn + z2
        z2 = b[2] * xn - a[2] * yn
        y[n] = yn
    return y


def _lfilter(b: Sequence[float], a: Sequence[float], x: np.ndarray) -> np.ndarray:
    try:
        from scipy.signal import lfilter as scipy_lfilter
    except ImportError:
        return _lfilter_numpy(b, a, x)
    return scipy_lfilter(np.asarray(b, dtype=np.float64), np.asarray(a, dtype=np.float64), x, axis=0)


def as_2d(samples: np.ndarray) -> np.ndarray:
    """把 (n,) 或 (n, ch) 統一成 (n, ch) 的 float64 陣列。"""
    arr = np.asarray(samples, dtype=np.float64)
    if arr.ndim == 1:
        return arr.reshape(-1, 1)
    if arr.ndim != 2:
        raise ValueError("samples 必須是 (n,) 或 (n, channels)")
    return arr


def k_weight(samples: np.ndarray, sample_rate: float) -> np.ndarray:
    """對每個聲道套用 K 加權（高頻棚 + RLB 高通）。"""
    x = as_2d(samples)
    b1, a1 = shelf_coefficients(sample_rate)
    b2, a2 = highpass_coefficients(sample_rate)
    return _lfilter(b2, a2, _lfilter(b1, a1, x))


def _channel_weights(channels: int) -> np.ndarray:
    """BS.1770 的聲道加權：L/R/C 為 1.0，環繞聲道 1.41。立體聲就是全 1。"""
    weights = np.ones(channels, dtype=np.float64)
    if channels >= 5:
        weights[4:] = 1.41
    return weights


def _block_mean_squares(filtered: np.ndarray, sample_rate: float) -> Optional[np.ndarray]:
    """切成 400ms／75% 重疊的區塊，回傳每個區塊各聲道的均方值 (blocks, ch)。"""
    block_len = int(round(BLOCK_SEC * sample_rate))
    step = max(1, int(round(block_len * (1.0 - BLOCK_OVERLAP))))
    n = filtered.shape[0]
    if block_len <= 0 or n < block_len:
        return None

    # 累積和一次算完所有區塊，避免對每個區塊重複 sum（整首歌有數千個區塊）
    squares = filtered ** 2
    cumulative = np.concatenate([np.zeros((1, squares.shape[1])), np.cumsum(squares, axis=0)])
    starts = np.arange(0, n - block_len + 1, step)
    sums = cumulative[starts + block_len] - cumulative[starts]
    return sums / block_len


def _blocks_to_loudness(mean_squares: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """每個區塊的響度 (LKFS)。全靜音的區塊給 -inf，交給閘門處理。"""
    weighted = mean_squares @ weights
    with np.errstate(divide="ignore"):
        return LOUDNESS_OFFSET + 10.0 * np.log10(weighted)


def integrated_loudness(samples: np.ndarray, sample_rate: float) -> float:
    """
    整合響度 (LUFS)，含 BS.1770 的雙重閘門。

    全靜音或訊號短於 400ms 時回傳 -inf —— 呼叫端要自己決定這種歌怎麼辦
    （我們的做法是不套用任何增益，寧可不動也不要亂調）。
    """
    x = as_2d(samples)
    if x.shape[0] == 0:
        return float("-inf")
    filtered = k_weight(x, sample_rate)
    mean_squares = _block_mean_squares(filtered, sample_rate)
    if mean_squares is None:
        return float("-inf")

    weights = _channel_weights(x.shape[1])
    block_loudness = _blocks_to_loudness(mean_squares, weights)

    # 第一道：絕對閘 -70 LUFS
    above_absolute = block_loudness > ABSOLUTE_GATE_LUFS
    if not np.any(above_absolute):
        return float("-inf")

    # 第二道：相對閘 = 通過絕對閘者的平均能量 - 10 LU
    mean_energy = float(np.mean(mean_squares[above_absolute] @ weights))
    relative_gate = LOUDNESS_OFFSET + 10.0 * math.log10(mean_energy) + RELATIVE_GATE_LU
    gated = above_absolute & (block_loudness > relative_gate)
    if not np.any(gated):
        return float("-inf")

    gated_energy = float(np.mean(mean_squares[gated] @ weights))
    if gated_energy <= 0:
        return float("-inf")
    return LOUDNESS_OFFSET + 10.0 * math.log10(gated_energy)


def peak_dbfs(samples: np.ndarray) -> float:
    """取樣點峰值（dBFS）。全靜音回傳 -inf。"""
    arr = np.asarray(samples, dtype=np.float64)
    if arr.size == 0:
        return float("-inf")
    peak = float(np.max(np.abs(arr)))
    if peak <= 0:
        return float("-inf")
    return 20.0 * math.log10(peak)


def gain_db_for_target(
    lufs: float,
    target_lufs: float,
    peak: Optional[float] = None,
    max_gain_db: float = 12.0,
    min_gain_db: float = -12.0,
    headroom_dbfs: float = -1.0,
) -> float:
    """
    要套多少增益才能把這首歌拉到目標響度。

    三重保險，寧可少調也不要調壞：
      1. 量不到響度（靜音軌、壞檔）→ 0 dB，完全不動。
      2. 增益夾在 ±12 dB —— 真的差這麼多通常是檔案有問題，硬拉只會把底噪放大。
      3. 峰值保護：拉完的峰值不超過 headroom_dbfs，避免削波破音。
    """
    if lufs is None or not math.isfinite(lufs):
        return 0.0
    gain = float(target_lufs) - float(lufs)
    gain = max(min_gain_db, min(max_gain_db, gain))
    if peak is not None and math.isfinite(peak):
        gain = min(gain, headroom_dbfs - float(peak))
    # 峰值保護只該擋「拉太大聲」，不該反過來變成非要衰減不可
    if gain < min_gain_db:
        gain = min_gain_db
    return round(gain, 2)


def analyze_samples(samples: np.ndarray, sample_rate: float,
                    target_lufs: float = -14.0) -> Dict[str, Any]:
    """量測一段音訊並算出建議增益。回傳可以直接塞進 metadata 的 dict。"""
    lufs = integrated_loudness(samples, sample_rate)
    peak = peak_dbfs(samples)
    return {
        "lufs": round(lufs, 2) if math.isfinite(lufs) else None,
        "peak_dbfs": round(peak, 2) if math.isfinite(peak) else None,
        "target_lufs": float(target_lufs),
        "gain_db": gain_db_for_target(lufs, target_lufs, peak),
        "sample_rate": int(sample_rate),
    }


def analyze_audio_file(path: Path, target_lufs: float = -14.0,
                       max_seconds: float = 600.0) -> Optional[Dict[str, Any]]:
    """
    量測音檔的整合響度。讀檔失敗（缺 soundfile、檔案壞掉）回傳 None，
    呼叫端要能接受「這首沒有響度資料」而不是整條流水線掛掉。
    """
    try:
        import soundfile as sf
    except ImportError:
        logger.info("未安裝 soundfile，跳過響度量測")
        return None

    path = Path(path)
    if not path.exists():
        return None
    try:
        with sf.SoundFile(str(path)) as f:
            sample_rate = f.samplerate
            # 超長檔（演唱會全場錄影）只量前 10 分鐘就夠代表整體響度了
            frames = int(min(f.frames, max_seconds * sample_rate)) if max_seconds else f.frames
            data = f.read(frames=frames, dtype="float64", always_2d=True)
    except Exception as e:
        logger.warning(f"響度量測讀檔失敗 {path.name}: {e}")
        return None

    try:
        return analyze_samples(data, sample_rate, target_lufs)
    except Exception as e:
        logger.warning(f"響度量測失敗 {path.name}: {e}")
        return None
