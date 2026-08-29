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

        hop = int(round(sr * FRAME_SEC))
        y, sr = librosa.load(str(audio_path), sr=sr, mono=True)

        self.sr = sr
        self.hop = hop
        self.frame_sec = hop / sr
        self.duration = float(len(y) / sr) if sr else 0.0

        rms = librosa.feature.rms(y=y, frame_length=hop * 4, hop_length=hop, center=True)[0]
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

        # 只在發聲幀累積能量，供行內逐字分配使用
        self.energy = np.where(self.active, np.maximum(self.rms, 1e-8), 0.0)
        self.cum_active = np.concatenate(([0.0], np.cumsum(self.active.astype(np.float64))))
        self.cum_energy = np.concatenate(([0.0], np.cumsum(self.energy)))
        self.onsets = self._detect_onsets()

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

    def voice_end_after(self, t_start: float, max_gap: float = 0.35, limit: float = None) -> float:
        """
        從 t_start 往後找這一句唱到哪裡結束：連續靜音超過 max_gap 就算句尾。
        """
        if self.n_frames == 0:
            return t_start
        limit = self.duration if limit is None else min(limit, self.duration)
        f = self.t2f(t_start)
        f_limit = int(np.clip(round(limit / self.frame_sec), 0, self.n_frames))
        gap_frames = int(max_gap / self.frame_sec)

        last_voice = f
        silence = 0
        while f < f_limit:
            if self.active[f]:
                last_voice = f
                silence = 0
            else:
                silence += 1
                if silence >= gap_frames:
                    break
            f += 1
        return self.f2t(last_voice + 1)

    def energy_split_times(self, t0: float, t1: float, fractions: List[float]) -> Optional[List[float]]:
        """
        依「累積發聲能量」把 [t0, t1] 切成 fractions 指定的比例點。
        句中的換氣空拍不會吃掉字的時間，長音會自然拿到比較多時間 —— 這是逐字不再均分的關鍵。
        """
        a = int(np.clip(round(t0 / self.frame_sec), 0, self.n_frames))
        b = int(np.clip(round(t1 / self.frame_sec), 0, self.n_frames))
        if b - a < 4:
            return None
        total = self.cum_energy[b] - self.cum_energy[a]
        if total <= 1e-9:
            return None
        seg = self.cum_energy[a:b + 1] - self.cum_energy[a]
        out = []
        for fr in fractions:
            target = fr * total
            k = int(np.searchsorted(seg, target, side="left"))
            out.append(self.f2t(a + min(k, b - a)))
        return out
