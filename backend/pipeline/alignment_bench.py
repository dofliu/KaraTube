"""
字幕對齊的端對端評估（合成人聲）

為什麼需要這一層：對齊相關的單元測試全綠，負責人實際唱的時候還是對不上。
那些測試量的是純函數（解析 LRC、仿射網格、偏移方向），沒有一條量過
「畫面上這個字開始變色的那一刻，跟喇叭裡唱出這個字的那一刻，差多少」。

這裡做的事：
  1. 合成一段「已知每個字從哪一刻開始」的人聲軌 —— 諧波 + 顫音、字與字之間
     有子音凹陷或連音、句中換氣、句尾拖長音、殘響、底噪；
  2. 從真實時間軸反推一份 LRC（套上指定的變速與偏移，再加人工打點的雜訊），
     跟網路上抓來的 LRC 一樣「大致對但不準」；
  3. 走跟真實歌曲**完全同一條**路徑：VocalActivity → align_lrc_to_audio；
  4. 量句首、逐字、句尾的誤差分佈。

合成人聲不是真的歌，它能證明的是「流水線在已知答案的情況下做得到」，
不能證明真實歌曲一定對得上。真實歌曲的對照基準要等負責人回報是哪幾首
（見 docs/PROGRESS_LOG.md）。

命令列：`python -m backend.pipeline.alignment_bench` 印出每個情境的誤差表。
"""
from typing import Any, Dict, List, Tuple

import numpy as np

SR = 16000
_CHARS = "我們一起唱歌吧天空很藍風在吹海浪聲音記得那年夏天的你"


def _fft_convolve(x: np.ndarray, h: np.ndarray) -> np.ndarray:
    n = x.size + h.size - 1
    size = 1 << (n - 1).bit_length()
    y = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(h, size), size)
    return y[:x.size]


def make_song(seed: int, *, n_lines: int = 32, scale: float = 1.0, offset: float = 0.0,
              lrc_noise: float = 0.12, lrc_bias: float = -0.15, breath_p: float = 0.5,
              reverb: float = 0.5, bleed_db: float = -38.0, legato_p: float = 0.25
              ) -> Tuple[np.ndarray, List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    回傳 (人聲訊號, 真實時間軸, LRC)。

    真實時間軸每一行是 {start, end, text, chars: [(字起點, 字終點), ...]}；
    LRC 是 parse_lrc_with_timestamps 的格式（{time, text, cjk}），滿足
    `音訊時間 ≈ scale × LRC 時間 + offset`。
    """
    rng = np.random.default_rng(seed)
    t = 12.0 + rng.uniform(0, 8)                          # 前奏
    lines: List[Dict[str, Any]] = []
    interludes = {n_lines // 4, (n_lines * 5) // 8}
    for li in range(n_lines):
        if li in interludes and li > 0:
            t += rng.uniform(12, 20)                      # 間奏
        n = int(rng.integers(6, 13))
        chars = []
        cur = t
        brk = int(rng.integers(3, n - 2)) if rng.random() < breath_p else -1
        for k in range(n):
            d = rng.uniform(0.22, 0.45) if k < n - 1 else rng.uniform(0.6, 1.4)   # 句尾拖長音
            chars.append((cur, cur + d))
            cur += d
            if k == brk:
                cur += rng.uniform(0.35, 0.7)             # 句中換氣
        lines.append({"start": chars[0][0], "end": chars[-1][1], "chars": chars,
                      "text": "".join(_CHARS[rng.integers(0, len(_CHARS))] for _ in range(n))})
        t = cur + (0.05 if rng.random() < legato_p else rng.uniform(0.4, 2.5))

    y = np.zeros(int((t + 10.0) * SR))
    for ln in lines:
        n_c = len(ln["chars"])
        loud = rng.uniform(0.5, 1.0)
        f0 = rng.uniform(180, 420)
        for ci, (a, b) in enumerate(ln["chars"]):
            i0, i1 = int(a * SR), int(b * SR)
            tt = np.arange(i1 - i0) / SR
            if rng.random() > 0.3:                        # 三成的字跟前一個字同音高（最難切）
                f0 = rng.uniform(180, 420)
            vib = f0 * (1 + 0.012 * np.sin(2 * np.pi * 5.5 * tt))
            ph = 2 * np.pi * np.cumsum(vib) / SR
            sig = sum((0.6 / h) * np.sin(h * ph) for h in range(1, 6))
            # 字與字的接縫：三成連音（幾乎沒有凹陷），其餘是子音造成的 4~20dB 凹陷
            g_in = rng.uniform(0.7, 0.95) if rng.random() < 0.3 else 10 ** (-rng.uniform(4, 20) / 20)
            g_out = rng.uniform(0.7, 0.95) if rng.random() < 0.3 else 10 ** (-rng.uniform(4, 20) / 20)
            env = (g_in + (1 - g_in) * np.minimum(1, tt / 0.04)) * \
                  (g_out + (1 - g_out) * np.minimum(1, tt[::-1] / 0.04))
            env = env * (1 + 0.12 * np.sin(2 * np.pi * 5.5 * tt + rng.uniform(0, 6)))
            if ci == n_c - 1:
                env = env * np.exp(-tt / 2.5)
            y[i0:i1] += sig * env * loud * rng.uniform(0.7, 1.0)
    if reverb > 0:
        ir_t = np.arange(int(0.8 * SR)) / SR
        ir = rng.standard_normal(ir_t.size) * np.exp(-ir_t / 0.18) * 0.03 * reverb
        ir[0] = 1.0
        y = _fft_convolve(y, ir)
    y = y + rng.standard_normal(y.size) * 10 ** (bleed_db / 20) * np.max(np.abs(y))
    y = y / (np.max(np.abs(y)) * 1.1)

    lrc = []
    for ln in lines:
        lt = (ln["start"] - offset) / scale + lrc_bias + rng.normal(0, lrc_noise)
        lrc.append({"time": round(max(0.0, lt), 2), "text": ln["text"], "cjk": ln["text"]})
    return y, lines, lrc


def measure(truth: List[Dict[str, Any]], aligned: List[Dict[str, Any]]) -> Dict[str, np.ndarray]:
    """
    逐行、逐字的誤差（秒，對齊結果 − 真實；正值＝字幕比聲音晚）。

    行數不同（長句被切開）時改用時間去配：每一個對齊後的字找它所屬的真實那一行。
    """
    starts, chars, ends = [], [], []
    if len(truth) == len(aligned):
        pairs = list(zip(truth, aligned, strict=True))
    else:
        flat_true = [c for ln in truth for c in ln["chars"]]
        flat_got = [w for ln in aligned for w in ln.get("words", [])]
        if len(flat_true) == len(flat_got):
            chars = [w["start"] - c[0] for w, c in zip(flat_got, flat_true, strict=True)]
        return {"start": np.array([]), "char": np.array(chars), "end": np.array([])}
    for tl, al in pairs:
        starts.append(al["start"] - tl["start"])
        ends.append(al["end"] - tl["end"])
        words = al.get("words", [])
        if len(words) == len(tl["chars"]):
            chars += [w["start"] - c[0] for w, c in zip(words, tl["chars"], strict=True)]
    return {"start": np.array(starts), "char": np.array(chars), "end": np.array(ends)}


def stats(errors: np.ndarray) -> Dict[str, float]:
    """中位數、p90、超過 300ms 的比例。300ms 大約是唱的人會覺得「字幕慢了／快了」的位置。"""
    a = np.abs(np.asarray(errors, dtype=np.float64))
    if a.size == 0:
        return {"median_ms": float("nan"), "p90_ms": float("nan"), "over_300ms": float("nan")}
    return {"median_ms": float(np.median(a) * 1000),
            "p90_ms": float(np.percentile(a, 90) * 1000),
            "over_300ms": float(np.mean(a > 0.3))}


SCENARIOS: List[Tuple[str, Dict[str, Any]]] = [
    ("原速", {}),
    ("片頭多 4.3 秒", {"offset": 4.3}),
    ("快 3%", {"scale": 1.03, "offset": -2.0}),
    ("慢 3%", {"scale": 0.97, "offset": 6.0}),
    ("快 6%", {"scale": 1.06, "offset": 1.0}),
    ("重殘響", {"reverb": 1.5}),
    ("句句換氣", {"breath_p": 0.9}),
]


def run(seed_base: int = 1, n_lines: int = 32) -> List[Dict[str, Any]]:
    from backend.pipeline.lyrics_aligner import LyricsAligner
    from backend.pipeline.vocal_activity import VocalActivity

    aligner = LyricsAligner()
    rows = []
    for i, (name, kw) in enumerate(SCENARIOS):
        y, truth, lrc = make_song(seed_base + i, n_lines=n_lines, **kw)
        va = VocalActivity.from_signal(y, SR)
        aligned, report = aligner.align_lrc_to_audio(lrc, va)
        err = measure(truth, aligned)
        rows.append({"name": name, "report": report,
                     **{k: stats(v) for k, v in err.items()}})
    return rows


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.WARNING)
    print(f"{'情境':<10}{'scale':>8}{'offset':>8}  {'句首 中位/p90':>14}  {'逐字 中位/p90/>300ms':>22}  {'句尾 中位/p90':>14}")
    for r in run():
        rep = r["report"]
        s, c, e = r["start"], r["char"], r["end"]
        print(f"{r['name']:<10}{rep['scale']:>8.4f}{rep['offset']:>8.2f}  "
              f"{s['median_ms']:>6.0f}/{s['p90_ms']:<6.0f}ms  "
              f"{c['median_ms']:>6.0f}/{c['p90_ms']:<6.0f}/{c['over_300ms'] * 100:>4.0f}%  "
              f"{e['median_ms']:>6.0f}/{e['p90_ms']:<6.0f}ms")
