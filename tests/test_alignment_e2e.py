"""
字幕對齊的端對端評估 —— 量的是「畫面上的字」跟「喇叭裡的聲音」差多少。

對齊相關的單元測試一直是綠的，負責人實際唱的時候還是對不上：那些測試量的是
純函數，沒有一條走過「人聲軌 → 人聲活動分析 → 對齊 → 逐字時間」整條路。
這裡用合成人聲（已知每個字的真實起點，見 backend/pipeline/alignment_bench.py）
走完整條路，再對誤差分佈下門檻。

門檻的由來（同一組合成歌，改版前 → 改版後）：
  * 逐字起點中位誤差  370ms → 9ms；超過 300ms 的字  57% → 13%
  * 句中換氣的句子，走字提早 2~3 秒唱完 → 句尾 p90 誤差 < 0.35 秒
  * 快 6% 的上傳：整首歪 3~6 秒（變速網格只到 ±5%）→ 句首中位 10ms
門檻刻意留了餘裕：它守的是「不要退回去」，不是逼數字每一輪都更好看。
"""
import numpy as np
import pytest

from backend.pipeline.alignment_bench import make_song, measure, stats
from backend.pipeline.lyrics_aligner import LyricsAligner
from backend.pipeline.vocal_activity import VocalActivity


@pytest.fixture(scope="module")
def aligner():
    return LyricsAligner()


def _run(aligner, seed, **kw):
    y, truth, lrc = make_song(seed, n_lines=16, **kw)
    va = VocalActivity.from_signal(y, 16000)
    aligned, report = aligner.align_lrc_to_audio(lrc, va)
    return truth, aligned, report, va


@pytest.mark.parametrize("seed,kw", [
    (101, {}),
    (102, {"offset": 4.3}),
    (103, {"scale": 1.03, "offset": -2.0}),
    (104, {"scale": 0.97, "offset": 6.0}),
])
def test_each_character_lights_up_when_it_is_sung(aligner, seed, kw):
    """逐字走字：中位誤差要在 60ms 內，超過 300ms 的字不到四分之一。"""
    truth, aligned, report, _ = _run(aligner, seed, **kw)
    assert len(aligned) == len(truth), report
    err = measure(truth, aligned)
    s = stats(err["char"])
    assert s["median_ms"] < 60, s
    assert s["over_300ms"] < 0.25, s
    starts = stats(err["start"])
    assert starts["median_ms"] < 60, starts


def test_a_breath_in_the_middle_does_not_end_the_line(aligner):
    """句中換氣（0.35~0.7 秒）不能被當成句尾：舊版在這裡把後半句的字全部
    擠進前半句，整句走字提早兩三秒唱完。"""
    truth, aligned, report, _ = _run(aligner, 105, breath_p=1.0)
    err = measure(truth, aligned)
    assert np.percentile(np.abs(err["end"]), 90) < 0.35, stats(err["end"])
    # 而且不是「拉長到下一句」：句尾不能晚於下一句的真實起點
    for i in range(len(truth) - 1):
        assert aligned[i]["end"] <= truth[i + 1]["start"] + 0.10


def test_a_six_percent_speed_up_is_recovered(aligner):
    """變速 6% 在舊網格（±5%）之外：搜尋收斂到錯的 offset，整首字幕越唱越歪。"""
    truth, aligned, report, _ = _run(aligner, 106, scale=1.06, offset=1.0)
    assert report["scale"] == pytest.approx(1.06, abs=0.005)
    s = stats(measure(truth, aligned)["start"])
    assert s["median_ms"] < 60, s


def test_the_final_held_note_gets_the_time(aligner):
    """句尾拖長音：最後一個字要拿到比一般字長的時間（舊版當成一樣長，
    結果句中每個字都跑在聲音前面）。"""
    truth, aligned, _, _ = _run(aligner, 107)
    longer = 0
    for ln in aligned:
        w = ln["words"]
        durs = [x["end"] - x["start"] for x in w]
        if len(durs) >= 4 and durs[-1] > np.median(durs[:-1]):
            longer += 1
    assert longer >= 0.8 * len(aligned)


def test_numpy_rms_matches_the_definition():
    """分析路徑不依賴 librosa：均方根要跟 center=True、補零的定義一致。"""
    rng = np.random.default_rng(0)
    y = rng.standard_normal(16000 + 77)
    got = VocalActivity._frame_rms(y, 640, 160)
    padded = np.concatenate((np.zeros(320), y, np.zeros(320)))
    for k in (0, 7, len(got) - 1):
        frame = padded[k * 160:k * 160 + 640]
        assert got[k] == pytest.approx(np.sqrt(np.mean(frame ** 2)), rel=1e-6)
    assert len(got) == 1 + len(y) // 160


def test_silence_yields_no_syllable_boundaries():
    va = VocalActivity.from_signal(np.zeros(16000 * 3), 16000)
    assert va.syllable_boundaries(0.5, 2.5) == []
    assert va.n_frames > 0


@pytest.mark.parametrize("seed,kw", [
    (111, {"shift_at": 9, "shift_sec": 7.0}),      # MV 中段插了一段
    (112, {"shift_at": 10, "shift_sec": -6.0}),    # 間奏剪短（n_lines=16 的第二段間奏在第 10 行前）
    (113, {"shift_at": 10, "shift_sec": 2.5}),     # 只差兩秒半：超出行首吸附半徑，仍要救
])
def test_a_section_that_differs_from_the_lrc_version_is_not_left_behind(aligner, seed, kw):
    """影片版本跟 LRC 的錄音版本中段長度不同：全域 (scale, offset) 只能對上一半，
    舊版後半段整段差 7~9 秒（「唱到一半字幕突然跳開」）。"""
    truth, aligned, report, _ = _run(aligner, seed, **kw)
    assert len(aligned) == len(truth), report
    err = measure(truth, aligned)
    assert np.percentile(np.abs(err["start"]), 90) < 0.30, stats(err["start"])
    assert stats(err["char"])["median_ms"] < 60
    assert report.get("shifts", 0) >= 1, report


@pytest.mark.parametrize("seed,kw", [
    (121, {}), (122, {"scale": 1.03, "offset": -2.0}), (123, {"reverb": 1.5}),
    (124, {"drums_db": -14.0, "adlib_db": -8.0}),
])
def test_a_song_without_structural_differences_is_not_split(aligner, seed, kw):
    """分段平移不能自己發明結構差異：同一個版本的歌一段都不准搬。"""
    _, _, report, _ = _run(aligner, seed, **kw)
    assert report.get("shifts", 0) == 0, report
