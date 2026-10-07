"""
人聲活動分析 (Vocal Activity Detection)

歌詞對齊需要的不是「Whisper 猜到什麼字」，而是「人聲在哪一刻開始、在哪一刻停」。
分離後的 vocals 軌把這件事變得非常乾淨：靜音段會直接塌到底噪，
所以用能量包絡就能取得比 Whisper word timestamp 更可靠的時間證據。
"""
import logging
import numpy as np
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger("KaraTube.VocalActivity")

FRAME_SEC = 0.01  # 10ms 一格；KTV 走字的可感知誤差約在 50ms，10ms 解析度綽綽有餘


def _run_length_filter(flags: np.ndarray, min_on: int, min_off: int) -> np.ndarray:
    """
    去毛刺：太短的發聲視為雜訊、太短的靜音視為咬字之間的停頓。
    直接對二值序列做，比 median filter 更能保住真正的起唱邊緣。
    """
    if flags.size == 0:
        return flags
    out = flags.copy()

    # 先補短靜音（避免一個字的塞音把一句話切成兩段）
    idx = 0
    n = out.size
    while idx < n:
        if not out[idx]:
            j = idx
            while j < n and not out[j]:
                j += 1
            # 只補「中間」的短靜音，開頭與結尾的靜音必須保留
            if idx > 0 and j < n and (j - idx) < min_off:
                out[idx:j] = True
            idx = j
        else:
            idx += 1

    # 再殺短促發聲
    idx = 0
    while idx < n:
        if out[idx]:
            j = idx
            while j < n and out[j]:
                j += 1
            if (j - idx) < min_on:
                out[idx:j] = False
            idx = j
        else:
            idx += 1

    return out


class VocalActivity:
    """vocals.mp3 的 10ms 能量包絡 / 發聲旗標 / 起唱點索引。"""

    def __init__(self, audio_path: Path, sr: int = 16000):
        import librosa

        y, sr = librosa.load(str(audio_path), sr=sr, mono=True)
        self._analyze(np.asarray(y, dtype=np.float64), int(sr))

    @classmethod
    def from_signal(cls, y: np.ndarray, sr: int = 16000) -> "VocalActivity":
        """
        直接吃一段已經解碼好的單聲道訊號。

        端對端的對齊評估（tests/test_alignment_e2e.py）靠這個入口：合成一段
        「已知每個字從哪一刻開始」的人聲，走跟真實歌曲**完全同一條**分析路徑，
        再量字幕跟真正的起唱點差多少。這一層不需要 librosa，CI 也跑得動。
        """
        obj = cls.__new__(cls)
        obj._analyze(np.asarray(y, dtype=np.float64).reshape(-1), int(sr))
        return obj

    @staticmethod
    def _frame_rms(y: np.ndarray, frame_length: int, hop: int) -> np.ndarray:
        """
        與 librosa.feature.rms(center=True) 同一個定義：前後各補 frame_length/2 個零，
        每 hop 取一窗算均方根。自己算是為了讓分析路徑只依賴 numpy。
        """
        if y.size == 0:
            return np.zeros(0)
        pad = frame_length // 2
        padded = np.concatenate((np.zeros(pad), y, np.zeros(pad)))
        n_frames = 1 + (padded.size - frame_length) // hop
        if n_frames <= 0:
            return np.zeros(0)
        sq = np.concatenate(([0.0], np.cumsum(padded * padded)))
        starts = np.arange(n_frames) * hop
        power = (sq[starts + frame_length] - sq[starts]) / frame_length
        return np.sqrt(np.maximum(power, 0.0))

    @staticmethod
    def _spectral_flux(y: np.ndarray, sr: int, hop: int, n_frames: int) -> np.ndarray:
        """
        換字的聲學證據：頻譜的「正向變化量」（spectral flux），每格 10ms。

        中文一字一音，換字的那一刻不是換音高、就是出現子音 —— 兩者都會讓頻譜
        突然冒出原本沒有的能量。只看音量包絡抓不到它：殘響把字與字之間的凹陷
        填平了，端對端評估裡 6dB 以上的凹陷只找得到一成多的換字點。

        做法跟一般的 onset strength 一樣：對數幅度譜（125Hz~4kHz，人聲的範圍）
        隔兩格相減、只留增加的部分、整列加總。只用 numpy。
        """
        if n_frames == 0 or y.size == 0 or sr <= 0:
            return np.zeros(n_frames)
        n_fft = 512 if sr <= 24000 else 1024
        pad = n_fft // 2
        yp = np.concatenate((np.zeros(pad), y, np.zeros(pad)))
        n = min(n_frames, 1 + (yp.size - n_fft) // hop)
        if n <= 2:
            return np.zeros(n_frames)
        lo_bin = max(1, int(round(125.0 * n_fft / sr)))
        hi_bin = min(n_fft // 2, int(round(4000.0 * n_fft / sr)))
        win = np.hanning(n_fft)
        peak = 0.0
        mags = []
        offsets = np.arange(n_fft)[None, :]
        for s0 in range(0, n, 2048):                     # 分塊：整首歌一次展開會吃掉幾百 MB
            starts = hop * np.arange(s0, min(n, s0 + 2048))[:, None]
            X = np.abs(np.fft.rfft(yp[starts + offsets] * win, axis=1))[:, lo_bin:hi_bin]
            peak = max(peak, float(X.max()) if X.size else 0.0)
            mags.append(X)
        M = np.vstack(mags)
        L = np.log1p(100.0 * M / (peak + 1e-12))
        d = np.maximum(L[2:] - L[:-2], 0.0).sum(axis=1)
        flux = np.concatenate((np.zeros(2), d))
        flux = np.convolve(flux, np.ones(3) / 3.0, mode="same")
        out = np.zeros(n_frames)
        out[:flux.size] = flux[:n_frames]
        return out

    def _analyze(self, y: np.ndarray, sr: int) -> None:
        hop = int(round(sr * FRAME_SEC))

        self.sr = sr
        self.hop = hop
        self.frame_sec = hop / sr
        self.duration = float(len(y) / sr) if sr else 0.0

        rms = self._frame_rms(y, hop * 4, hop)
        self.rms = rms.astype(np.float64)
        self.n_frames = int(self.rms.size)

        peak = float(np.max(self.rms)) if self.n_frames else 0.0
        if peak <= 0:
            self.db = np.full(self.n_frames, -80.0)
            self.threshold = -80.0
            self.active = np.zeros(self.n_frames, dtype=bool)
        else:
            self.db = 20.0 * np.log10(np.maximum(self.rms, 1e-10) / peak)
            # 自適應門檻：以 20% 分位當底噪，往上抬 10dB。
            # 夾在 -55 ~ -25dB 之間，避免整首都很吵或整首都很輕的極端案例失控。
            floor = float(np.percentile(self.db, 20))
            self.threshold = float(min(-25.0, max(-55.0, floor + 10.0)))
            raw = self.db > self.threshold
            self.active = _run_length_filter(raw, min_on=6, min_off=12)  # 60ms / 120ms

        self.cum_active = np.concatenate(([0.0], np.cumsum(self.active.astype(np.float64))))
        self.onsets = self._detect_onsets()
        self.flux = self._spectral_flux(y, sr, hop, self.n_frames)

        logger.info(
            f"VocalActivity: {self.duration:.1f}s, 門檻 {self.threshold:.1f}dB, "
            f"發聲佔比 {self.active.mean() * 100 if self.n_frames else 0:.1f}%, "
            f"起唱點 {len(self.onsets)} 個"
        )

    # --- 座標轉換 ---
    def t2f(self, t: float) -> int:
        return int(np.clip(round(t / self.frame_sec), 0, max(0, self.n_frames - 1)))

    def f2t(self, f: float) -> float:
        return float(f * self.frame_sec)

    # --- 區間查詢 ---
    def activity_mean(self, t0: float, t1: float) -> float:
        """[t0, t1) 之間的發聲佔比，0~1。"""
        if t1 <= t0 or self.n_frames == 0:
            return 0.0
        a = int(np.clip(round(t0 / self.frame_sec), 0, self.n_frames))
        b = int(np.clip(round(t1 / self.frame_sec), 0, self.n_frames))
        if b <= a:
            return 0.0
        return float((self.cum_active[b] - self.cum_active[a]) / (b - a))

    def _detect_onsets(self, min_silence: float = 0.30, min_voice: float = 0.20) -> np.ndarray:
        """
        起唱點 = 前面至少靜了 min_silence、後面至少唱了 min_voice 的上升邊緣。
        這種點才值得拿來吸附歌詞行首；一般連唱中的能量起伏會被濾掉。
        """
        if self.n_frames == 0:
            return np.array([], dtype=np.float64)
        sil = int(min_silence / self.frame_sec)
        voi = int(min_voice / self.frame_sec)
        a = self.active
        rises = np.flatnonzero((~a[:-1]) & a[1:]) + 1
        keep = []
        for r in rises:
            pre = a[max(0, r - sil):r]
            post = a[r:r + voi]
            if pre.size and pre.any():
                continue
            if post.size < voi or not post.all():
                continue
            keep.append(self.f2t(r))
        return np.array(keep, dtype=np.float64)

    def nearest_onset(self, t: float, window: float = 0.75) -> Optional[float]:
        """回傳 t 附近 window 秒內最近的起唱點；沒有就回 None（表示這行是連唱，不該亂吸附）。"""
        if self.onsets.size == 0:
            return None
        i = int(np.argmin(np.abs(self.onsets - t)))
        return float(self.onsets[i]) if abs(self.onsets[i] - t) <= window else None

    def voice_end_after(self, t_start: float, max_gap: float = 0.35, limit: float = None,
                        next_start: Optional[float] = None, lead: float = 0.6,
                        min_gap: float = 0.2) -> float:
        """
        從 t_start 往後找這一句唱到哪裡結束：連續靜音超過 max_gap 就算句尾。

        `next_start`（下一句的起點）讓 max_gap 可以放寬到容得下句中換氣：
        一段至少 min_gap 的靜音之後重新開口、而開口的位置已經在下一句起點前
        lead 秒之內 —— 那是下一句，不是這一句換完氣接著唱。少了這一條，
        兩句之間只隔半秒的歌會整句黏到下一句去，走字被拉長到下一句開頭。
        """
        if self.n_frames == 0:
            return t_start
        limit = self.duration if limit is None else min(limit, self.duration)
        f = self.t2f(t_start)
        f_limit = int(np.clip(round(limit / self.frame_sec), 0, self.n_frames))
        gap_frames = int(max_gap / self.frame_sec)
        min_gap_frames = max(1, int(min_gap / self.frame_sec))
        resume_after = None if next_start is None else next_start - lead

        last_voice = f
        silence = 0
        while f < f_limit:
            if self.active[f]:
                if (resume_after is not None and silence >= min_gap_frames
                        and self.f2t(f) >= resume_after):
                    break
                last_voice = f
                silence = 0
            else:
                silence += 1
                if silence >= gap_frames:
                    break
            f += 1
        return self.f2t(last_voice + 1)

    def syllable_boundaries(self, t0: float, t1: float, min_z: float = 5.0,
                            reach: float = 0.05, hold: float = 0.15) -> List[Tuple[float, float]]:
        """
        [t0, t1] 之間「像是換字」的位置與強度（0~1），供逐字時間吸附用。

        兩種證據：
          * 頻譜變化的峰值（見 `_spectral_flux`）—— 換音高或出現子音。門檻用這一句
            自己的中位數與 MAD 標準化（z 分數 ≥ min_z），所以大聲的副歌與輕聲的主歌
            用的是同一把尺。峰值必須是前後 reach 秒內的最大值。
          * 靜音後重新開口 —— 句中換氣之後的第一個字，強度 1。

        端對端評估（tests/test_alignment_e2e.py 的合成人聲）上，這一組門檻找到
        九成以上的換字點、誤報約 3%。同一個音高上的連音沒有頻譜變化，找不到 ——
        那一格留給呼叫端按先驗插值，不硬猜。
        """
        if self.n_frames == 0 or t1 - t0 < 0.1:
            return []
        a = int(np.clip(round(t0 / self.frame_sec), 0, self.n_frames - 1))
        b = int(np.clip(round(t1 / self.frame_sec), 0, self.n_frames))
        if b - a < 6:
            return []
        act = self.active[a:b]
        out: List[Tuple[float, float]] = []
        for i in range(1, b - a):
            if act[i] and not act[i - 1]:
                out.append((self.f2t(a + i), 1.0))

        flux = getattr(self, "flux", None)
        if flux is None or flux.size < b:
            return out
        e = flux[a:b]
        med = float(np.median(e))
        mad = float(np.median(np.abs(e - med))) + 1e-9
        r = max(1, int(round(reach / self.frame_sec)))
        hold_f = max(1, int(round(hold / self.frame_sec)))
        reopen = [t for t, _ in out]
        for i in range(1, e.size - 1):
            if not act[i]:
                continue
            z = (e[i] - med) / mad
            if z < min_z or e[i] < e[max(0, i - r):i + r + 1].max():
                continue
            t = self.f2t(a + i)
            if any(abs(t - u) < 0.08 for u in reopen):
                continue                                   # 跟換氣後開口是同一個點
            # 換字之後要接著唱至少 hold 秒。句尾剛收音、殘響正在散開的那一刻，
            # 頻譜也會突然「冒出」原本沒有的能量（殘響把諧波之間的空隙填滿），
            # 但它後面緊接著就是換氣的靜音 —— 那是上一個字的尾巴，不是下一個字。
            g = a + i
            if not self.active[g:min(self.n_frames, g + hold_f)].all():
                continue
            out.append((t, float(min(1.0, z / (3.0 * min_z)))))
        out.sort()
        return out
