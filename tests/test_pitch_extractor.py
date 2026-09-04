"""
音準導唱線相關純函數的單元測試。

`extract_pitch` 本身要 librosa 與真實音檔，CI 上跑不動；
但把 F0 曲線變成「導唱線上那一根根音符方塊」的 `_segment_into_notes`
是純資料處理，而且它決定了舞台上看到的音符對不對 ——
切太碎會變成一堆短棒，黏太過會把兩個音併成一條長棒。

`_run_length_filter` 則是人聲活動偵測的去毛刺，
歌詞行首要吸附到哪個起唱點完全靠它。
"""
import numpy as np

from backend.pipeline.pitch_extractor import PitchExtractor
from backend.pipeline.vocal_activity import _run_length_filter


def points(pairs):
    return [[round(t, 3), midi] for t, midi in pairs]


def steady(start, end, midi, step=0.05):
    t = start
    out = []
    while t < end - 1e-9:
        out.append([round(t, 3), midi])
        t += step
    return out


# --- 音符切分 ---

def test_steady_pitch_becomes_one_note():
    notes = PitchExtractor()._segment_into_notes(steady(0.0, 1.0, 60.0))
    assert len(notes) == 1
    assert notes[0]["midi"] == 60
    assert notes[0]["start"] == 0.0
    assert notes[0]["end"] >= 0.9


def test_pitch_jump_splits_into_two_notes():
    """跳超過 1.5 個半音就是換了一個音，不能黏成同一條方塊。"""
    notes = PitchExtractor()._segment_into_notes(steady(0.0, 0.6, 60.0) + steady(0.6, 1.2, 67.0))
    assert [n["midi"] for n in notes] == [60, 67]


def test_small_vibrato_stays_one_note():
    """
    歌手的抖音（流行唱腔約 ±0.5 個半音）不該被切成一堆短棒。
    容許值是「與目前音符中位數相差 1.5 個半音以內」，抖音遠在裡面。
    """
    wobbly = [[round(0.05 * i, 3), 60.0 + (0.5 if i % 2 else -0.5)] for i in range(20)]
    notes = PitchExtractor()._segment_into_notes(wobbly)
    assert len(notes) == 1


def test_unvoiced_gap_ends_the_note():
    pitch = steady(0.0, 0.5, 60.0) + [[0.5, 0.0], [0.55, 0.0]] + steady(0.6, 1.1, 60.0)
    notes = PitchExtractor()._segment_into_notes(pitch)
    assert len(notes) == 2


def test_long_time_gap_splits_even_at_same_pitch():
    """同一個音高但中間空了 0.25 秒以上，是兩次發聲。"""
    pitch = steady(0.0, 0.4, 60.0) + steady(1.5, 1.9, 60.0)
    notes = PitchExtractor()._segment_into_notes(pitch)
    assert len(notes) == 2


def test_notes_shorter_than_min_duration_are_dropped():
    """一瞬間的雜訊不該在導唱線上留下一根閃一下的短棒。"""
    pitch = steady(0.0, 0.5, 60.0) + [[0.9, 72.0]] + steady(1.4, 1.9, 60.0)
    notes = PitchExtractor()._segment_into_notes(pitch, min_duration=0.1)
    assert [n["midi"] for n in notes] == [60, 60]


def test_no_voiced_points_gives_no_notes():
    assert PitchExtractor()._segment_into_notes(points([(0.0, 0.0), (0.1, 0.0)])) == []
    assert PitchExtractor()._segment_into_notes([]) == []


def test_note_midi_uses_median_so_one_outlier_cannot_shift_it():
    pitch = steady(0.0, 1.0, 60.0)
    pitch[3][1] = 60.9  # 一格偏高，中位數不受影響
    notes = PitchExtractor()._segment_into_notes(pitch)
    assert notes[0]["midi"] == 60


# --- 人聲活動去毛刺 ---

def test_short_burst_is_removed():
    """一格的爆音不是起唱點，歌詞行首吸附到那裡會整句提早。"""
    flags = np.array([False] * 5 + [True] * 2 + [False] * 5)
    assert not _run_length_filter(flags, min_on=4, min_off=3).any()


def test_long_voice_is_kept():
    flags = np.array([False] * 3 + [True] * 10 + [False] * 3)
    assert _run_length_filter(flags, min_on=4, min_off=3).sum() == 10


def test_short_internal_silence_is_filled():
    """一個字的塞音會讓能量短暫掉下去，不能因此把一句話切成兩段。"""
    flags = np.array([True] * 6 + [False] * 2 + [True] * 6)
    assert _run_length_filter(flags, min_on=4, min_off=4).all()


def test_leading_and_trailing_silence_is_never_filled():
    """開頭與結尾的靜音必須留著，否則歌曲第一個起唱點會被抹掉。"""
    flags = np.array([False] * 2 + [True] * 8 + [False] * 2)
    out = _run_length_filter(flags, min_on=4, min_off=5)
    assert not out[0] and not out[1]
    assert not out[-1] and not out[-2]


def test_empty_input():
    assert _run_length_filter(np.array([], dtype=bool), 3, 3).size == 0
