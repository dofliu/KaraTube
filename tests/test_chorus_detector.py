"""
副歌偵測與段落切分單元測試。

全部用合成歌詞（就是 lyrics.json 的形狀），不需要音檔也不需要模型，CI 上跑得動。
重點在證明「重複結構分析真的找得到副歌」，而不是「函式呼叫起來沒爆」。
"""
import pytest

from backend.pipeline import chorus_detector as C


def line(start, end, text):
    return {"line_idx": 0, "start": start, "end": end, "text": text, "words": []}


def build(texts, start=10.0, dur=3.0, gap=0.5):
    """把一串歌詞文字排成連續的時間軸。空字串代表「這裡空一段」（間奏）。"""
    lyrics, t = [], start
    for text in texts:
        if text is None:
            t += 8.0          # 間奏
            continue
        lyrics.append(line(round(t, 3), round(t + dur, 3), text))
        t += dur + gap
    return lyrics


VERSE_A = ["天空飄著雨", "我在街角等你", "傘下的影子", "被路燈拉得好長"]
CHORUS = ["說好不哭的", "眼淚卻先開口", "你走的那一天", "全世界都安靜了"]
VERSE_B = ["咖啡涼透了", "杯緣還留著唇印", "訊息沒有回", "螢幕暗了又亮"]


# --- 正規化 ---

def test_normalize_strips_punctuation_and_spacing():
    """同一句歌詞在不同來源的 LRC 裡標點不同，正規化後必須視為同一句。"""
    assert C.normalize_line("說好，不哭的！") == C.normalize_line("說好 不哭的")
    assert C.normalize_line("Don't Cry!") == C.normalize_line("dont cry")


def test_normalize_empty_for_pure_punctuation():
    assert C.normalize_line("♪♪♪") == ""
    assert C.normalize_line(None) == ""


# --- 副歌偵測 ---

def test_detects_repeated_four_line_chorus():
    lyrics = build(VERSE_A + CHORUS + VERSE_B + CHORUS)
    chorus = C.detect_chorus(lyrics)
    assert chorus is not None
    assert chorus["lines"] == 4
    assert chorus["repeats"] == 2
    # 第一次副歌 = 第 5~8 行
    assert chorus["start"] == pytest.approx(lyrics[4]["start"])
    assert chorus["end"] == pytest.approx(lyrics[7]["end"])
    assert chorus["text"].splitlines() == CHORUS


def test_reports_every_occurrence_in_order():
    lyrics = build(VERSE_A + CHORUS + VERSE_B + CHORUS + CHORUS)
    chorus = C.detect_chorus(lyrics)
    assert chorus["repeats"] == 3
    starts = [occ["start"] for occ in chorus["occurrences"]]
    assert starts == sorted(starts)
    assert chorus["occurrences"][0]["start"] == chorus["start"]


def test_prefers_longer_block_over_shorter_hook_at_equal_coverage():
    """
    四行副歌唱兩次（覆蓋 8 行），最後兩行的鉤子另外又唱兩次（合計四次、也覆蓋 8 行）。
    總覆蓋打平時要選一次比較長的完整副歌，那才是使用者想圈起來練的段落。
    """
    lyrics = build(VERSE_A + CHORUS + VERSE_B + CHORUS + CHORUS[2:] + ["音樂間奏過門"] + CHORUS[2:])
    chorus = C.detect_chorus(lyrics)
    assert chorus["lines"] == 4
    assert chorus["repeats"] == 2


def test_ignores_repetition_shorter_than_min_lines():
    """單行語助詞重複兩次不是副歌，整首沒有真正的重複區塊就回 None。"""
    lyrics = build(VERSE_A + ["啦啦啦"] + VERSE_B + ["啦啦啦"])
    assert C.detect_chorus(lyrics) is None


def test_single_line_hook_fallback_needs_three_repeats():
    """口水歌只有一句一直反覆：多行找不到時退回單行鉤子，但要重複夠多次。"""
    lyrics = build(VERSE_A + ["我不會哭"] + VERSE_B + ["我不會哭"] + ["別再說了"] + ["我不會哭"])
    chorus = C.detect_chorus(lyrics)
    assert chorus is not None
    assert chorus["lines"] == 1
    assert chorus["repeats"] == 3


def test_rejects_chorus_span_longer_than_max():
    """整段主歌+副歌被當成一個重複區塊時（超過上限）不該拿來當練唱循環。"""
    long_block = [f"第 {i} 句歌詞" for i in range(40)]
    lyrics = build(long_block + long_block, dur=3.0, gap=0.5)
    chorus = C.detect_chorus(lyrics)
    # 找得到的話一定是被裁短過的子區塊，不會是那個 140 秒的整段
    assert chorus is None or (chorus["end"] - chorus["start"]) <= C.MAX_CHORUS_SECONDS


def test_confidence_rises_with_repeats():
    twice = C.detect_chorus(build(VERSE_A + CHORUS + VERSE_B + CHORUS))
    thrice = C.detect_chorus(build(VERSE_A + CHORUS + VERSE_B + CHORUS + CHORUS))
    assert 0.0 <= twice["confidence"] <= 1.0
    assert thrice["confidence"] > twice["confidence"]


def test_handles_empty_and_broken_input():
    assert C.detect_chorus(None) is None
    assert C.detect_chorus([]) is None
    assert C.detect_chorus([{"start": 1.0}]) is None
    # 時間軸壞掉（end <= start）的行要被濾掉，不能讓它算出負長度的循環
    broken = [line(5.0, 3.0, "壞掉的行"), line(9.0, 8.0, "壞掉的行")]
    assert C.detect_chorus(broken) is None


def test_ignores_non_dict_entries():
    lyrics = ["雜訊", None, 42] + build(VERSE_A + CHORUS + VERSE_B + CHORUS)
    assert C.detect_chorus(lyrics) is not None


# --- 段落切分 ---

def test_sections_split_on_long_silence():
    lyrics = build(VERSE_A + [None] + CHORUS + [None] + VERSE_B)
    sections = C.detect_sections(lyrics)
    kinds = [s["kind"] for s in sections]
    # 前奏（第一句在 10 秒）＋ 三個唱段 ＋ 兩段間奏
    assert kinds.count("interlude") == 2
    assert kinds[0] == "intro"
    assert sum(1 for k in kinds if k in ("verse", "chorus")) == 3


def test_sections_label_chorus_blocks():
    lyrics = build(VERSE_A + [None] + CHORUS + [None] + VERSE_B + [None] + CHORUS)
    chorus = C.detect_chorus(lyrics)
    sections = C.detect_sections(lyrics, chorus=chorus)
    labels = [s["label"] for s in sections if s["kind"] == "chorus"]
    assert labels == ["副歌 1", "副歌 2"]
    assert [s["label"] for s in sections if s["kind"] == "verse"] == ["主歌 1", "主歌 2"]


def test_sections_have_monotonic_non_overlapping_ranges():
    lyrics = build(VERSE_A + [None] + CHORUS + [None] + VERSE_B)
    sections = C.detect_sections(lyrics, duration=200.0)
    for prev, cur in zip(sections, sections[1:], strict=False):
        assert prev["end"] <= cur["start"] + 1e-6
        assert cur["end"] > cur["start"]
    assert [s["index"] for s in sections] == list(range(len(sections)))


def test_sections_add_outro_only_when_duration_known():
    lyrics = build(VERSE_A, start=10.0)
    assert not any(s["kind"] == "outro" for s in C.detect_sections(lyrics))
    with_outro = C.detect_sections(lyrics, duration=lyrics[-1]["end"] + 20)
    assert with_outro[-1]["kind"] == "outro"
    assert with_outro[-1]["end"] == pytest.approx(lyrics[-1]["end"] + 20)


def test_sections_skip_intro_when_singing_starts_immediately():
    lyrics = build(VERSE_A, start=0.5)
    assert C.detect_sections(lyrics)[0]["kind"] == "verse"


def test_sections_empty_for_no_lyrics():
    assert C.detect_sections([]) == []
    assert C.detect_sections(None) == []


def test_analyze_song_structure_combines_both():
    lyrics = build(VERSE_A + [None] + CHORUS + [None] + VERSE_B + [None] + CHORUS)
    result = C.analyze_song_structure(lyrics, duration=300.0)
    assert result["chorus"]["lines"] == 4
    assert any(s["kind"] == "chorus" for s in result["sections"])
