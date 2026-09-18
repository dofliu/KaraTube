"""曲庫查歌（注音首字／歌名字數／文字）單元測試。

這一支釘的主要是兩件「查不到會讓人以為曲庫裡沒有這首歌」的事：

1. 索引建在**歌名本體**上，不是整串 YouTube 標題
   （「周杰倫 - 稻香【Official MV】4K」的字數是 2，不是 20 幾）。
2. 多音字的**所有讀音**都收
   （「重」按 ㄓ 或 ㄔ 都要查得到；只留一個讀音的話，另一半使用者會以為壞了）。

pypinyin 沒裝的環境（注音查詢關閉）只跳過注音那幾條，其餘照跑 ——
文字與字數查詢在那種環境仍然必須是好的。
"""
import json

import pytest

from backend.services.library import LibraryIndex
from backend.services.song_index import (
    BOPOMOFO_AVAILABLE,
    BOPOMOFO_ROWS,
    INDEX_VERSION,
    SongFinder,
    char_initials,
    han_char_count,
    is_key_query,
    match_keys,
    normalize_query,
    title_core,
    title_keys,
)
from backend.services.storage import SongStorage

needs_bopomofo = pytest.mark.skipif(
    not BOPOMOFO_AVAILABLE, reason="這個環境沒有 pypinyin，注音查詢本來就關閉")


# --- 歌名本體萃取 ---

def test_title_core_strips_artist_and_promo_noise():
    assert title_core("周杰倫 Jay Chou - 稻香 Rice Fields【Official MV】4K",
                      "周杰倫").startswith("稻香")
    assert title_core("【MV完整版】田馥甄 - 小幸運", "田馥甄") == "小幸運"


def test_title_core_takes_quoted_song_name():
    # 《》「」是中文標題標出「歌名本身」的慣例，優先採用
    assert title_core("周杰倫《稻香》官方MV", "周杰倫") == "稻香"


def test_title_core_falls_back_to_bracket_when_stripped_to_artist_only():
    # 沒有分隔線、歌名被關在【】裡：剝完只剩歌手（或只剩歌手的英文名）時改用括號裡的
    assert title_core("五月天 Mayday【入陣曲 Into the Battle】Official Music Video",
                      "五月天").startswith("入陣曲")
    # 但英文歌不該因此撿到「Official Audio」這種剝不乾淨的附註
    assert title_core("Carpenters - Yesterday Once More (Official Audio)",
                      "Carpenters") == "Yesterday Once More"


def test_title_core_drops_space_separated_artist():
    assert title_core("江蕙 甲你攬牢牢 (高音質)", "江蕙") == "甲你攬牢牢"
    assert title_core("朋友 周華健", "周華健") == "朋友"


def test_title_core_keeps_song_name_equal_to_artist_name():
    # 〈朋友〉就是周華健的歌名。模糊比對會把歌名整個剝掉，所以只認完整的那一段
    assert title_core("周華健 - 朋友", "周華健") == "朋友"


def test_title_core_never_returns_empty():
    assert title_core("【Official MV】", "某歌手") != ""
    assert title_core("") == ""


def test_han_char_count_counts_only_han():
    assert han_char_count("稻香 Rice Fields") == 2
    assert han_char_count("Yesterday Once More") == 0
    assert han_char_count("聽說愛情回來過") == 7


# --- 注音首碼 ---

@needs_bopomofo
def test_char_initials_keeps_every_reading_of_a_polyphone():
    # 「重」ㄓㄨㄥˋ / ㄔㄨㄥˊ：只留一個的話，用另一個讀音查的人永遠查不到
    cand = char_initials("重")
    assert "ㄓ" in cand and "ㄔ" in cand


@needs_bopomofo
def test_title_keys_one_slot_per_word():
    assert title_keys("稻香") == ["ㄉ", "ㄒ"]
    # 英文一個單字一格，不是一個字母一格
    assert title_keys("Yesterday Once More") == ["Y", "O", "M"]


def test_title_keys_ignores_punctuation_and_spaces():
    keys = title_keys("A - B!")
    assert keys == ["A", "B"]


def test_char_initials_returns_empty_for_non_han():
    assert char_initials("A") == ""
    assert char_initials("") == ""


def test_bopomofo_rows_cover_37_symbols_without_duplicates():
    flat = [k for row in BOPOMOFO_ROWS for k in row]
    assert len(flat) == 37
    assert len(set(flat)) == 37


# --- 比對 ---

def test_match_keys_finds_prefix_and_inner_run():
    keys = ["ㄐ", "ㄋ", "ㄌ", "ㄌ", "ㄌ"]  # 甲你攬牢牢
    assert match_keys(["ㄐ", "ㄋ"], keys) == 0
    # 使用者記得的常常是中間那幾個字
    assert match_keys(["ㄌ", "ㄌ", "ㄌ"], keys) == 2
    assert match_keys(["ㄐ", "ㄌ"], keys) is None  # 不連續不算


def test_match_keys_hits_any_reading_of_a_polyphone():
    keys = ["ㄓㄔ", "ㄌ"]  # 重來
    assert match_keys(["ㄓ", "ㄌ"], keys) == 0
    assert match_keys(["ㄔ", "ㄌ"], keys) == 0


def test_match_keys_rejects_query_longer_than_title():
    assert match_keys(["ㄅ", "ㄆ", "ㄇ"], ["ㄅ"]) is None
    assert match_keys([], ["ㄅ"]) is None


def test_normalize_query_drops_punctuation_and_upcases():
    assert normalize_query(" y o m ") == ["Y", "O", "M"]
    assert normalize_query("ㄉ ㄒ") == ["ㄉ", "ㄒ"]
    assert normalize_query("ㄉ，ㄒ。") == ["ㄉ", "ㄒ"]


def test_is_key_query_distinguishes_keys_from_typed_text():
    assert is_key_query("ㄉㄒ")
    assert is_key_query("YOM")       # 純英數：首碼與歌名兩種讀法都可能
    assert not is_key_query("稻香")   # 打得出字了就不必猜首碼
    assert not is_key_query("")


# --- SongFinder ---

def make_song(root, song_id, title, artist, lyrics_text="詞", complete=True, plays=0):
    song_dir = root / song_id
    song_dir.mkdir(parents=True)
    (song_dir / "metadata.json").write_text(
        json.dumps({"id": song_id, "title": title, "artist": artist, "duration": 200},
                   ensure_ascii=False), encoding="utf-8")
    (song_dir / "lyrics.json").write_text(
        json.dumps([{"start": 0, "end": 3, "text": lyrics_text}], ensure_ascii=False),
        encoding="utf-8")
    if complete:
        (song_dir / "instrumental.mp3").write_bytes(b"x")
        (song_dir / "vocals.mp3").write_bytes(b"x")
    return song_dir


def build_finder(tmp_path):
    storage = SongStorage(tmp_path)
    return SongFinder(storage, LibraryIndex(storage))


def test_index_is_cached_in_metadata(tmp_path):
    make_song(tmp_path, "s1", "周杰倫 - 稻香【Official MV】", "周杰倫 - Topic")
    finder = build_finder(tmp_path)
    finder.entries()
    meta = json.loads((tmp_path / "s1" / "metadata.json").read_text(encoding="utf-8"))
    assert meta["find_index"]["v"] == INDEX_VERSION
    assert meta["find_index"]["core"] == "稻香"
    assert meta["find_index"]["chars"] == 2


def test_char_count_counts_the_song_name_not_the_youtube_title(tmp_path):
    # 這是整支模組存在的理由：照整串標題算，這首歌的「字數」會是十幾個
    make_song(tmp_path, "s1", "周杰倫 Jay Chou - 稻香 Rice Fields【Official MV】4K",
              "周杰倫 - Topic")
    res = build_finder(tmp_path).search(chars=2)
    assert [s["song_id"] for s in res["songs"]] == ["s1"]
    assert build_finder(tmp_path).search(chars=20)["songs"] == []


@needs_bopomofo
def test_search_by_bopomofo_initials(tmp_path):
    make_song(tmp_path, "s1", "周杰倫 - 稻香", "周杰倫 - Topic")
    make_song(tmp_path, "s2", "五月天 - 溫柔", "五月天 - Topic")
    finder = build_finder(tmp_path)
    assert [s["song_id"] for s in finder.search(query="ㄉㄒ")["songs"]] == ["s1"]
    assert [s["song_id"] for s in finder.search(query="ㄨㄖ")["songs"]] == ["s2"]
    assert finder.search(query="ㄅㄆㄇ")["songs"] == []


@needs_bopomofo
def test_search_matches_any_reading_of_a_polyphone(tmp_path):
    make_song(tmp_path, "s1", "重來", "某歌手")
    finder = build_finder(tmp_path)
    assert finder.search(query="ㄓㄌ")["total"] == 1
    assert finder.search(query="ㄔㄌ")["total"] == 1


@needs_bopomofo
def test_prefix_hits_rank_above_inner_hits(tmp_path):
    make_song(tmp_path, "inner", "甲你攬牢牢", "江蕙")
    make_song(tmp_path, "prefix", "牢牢", "某歌手")
    res = build_finder(tmp_path).search(query="ㄌㄌ")
    assert [s["song_id"] for s in res["songs"]] == ["prefix", "inner"]
    assert res["songs"][0]["match_pos"] == 0
    assert res["songs"][1]["match_pos"] == 2


def test_search_by_text_matches_song_name_and_artist(tmp_path):
    make_song(tmp_path, "s1", "周杰倫 - 稻香", "周杰倫 - Topic")
    make_song(tmp_path, "s2", "五月天 - 溫柔", "五月天 - Topic")
    finder = build_finder(tmp_path)
    assert [s["song_id"] for s in finder.search(query="稻香")["songs"]] == ["s1"]
    # 打歌手名也要查得到（商用機的「歌星查詢」這裡併進同一個框）
    assert [s["song_id"] for s in finder.search(query="五月天")["songs"]] == ["s2"]


def test_latin_query_tries_both_initials_and_text(tmp_path):
    make_song(tmp_path, "s1", "Carpenters - Yesterday Once More", "Carpenters - Topic")
    finder = build_finder(tmp_path)
    assert finder.search(query="YOM")["total"] == 1      # 首字母
    assert finder.search(query="yesterday")["total"] == 1  # 直接打字


def test_query_and_chars_filters_stack(tmp_path):
    make_song(tmp_path, "s1", "聽海", "張惠妹")
    make_song(tmp_path, "s2", "聽說愛情回來過", "丁噹")
    finder = build_finder(tmp_path)
    assert finder.search(query="聽", chars=2)["total"] == 1
    assert finder.search(query="聽")["total"] == 2


def test_empty_query_returns_whole_library(tmp_path):
    # 把條件清掉的那一下不該得到空畫面
    make_song(tmp_path, "s1", "稻香", "周杰倫")
    make_song(tmp_path, "s2", "溫柔", "五月天")
    res = build_finder(tmp_path).search()
    assert res["total"] == 2
    assert res["library_total"] == 2


def test_incomplete_songs_never_appear(tmp_path):
    # 查歌查到一首唱不了的歌，比查不到還糟
    make_song(tmp_path, "bad", "稻香", "周杰倫", complete=False)
    assert build_finder(tmp_path).search(query="稻香")["total"] == 0


def test_facets_buckets_by_char_count(tmp_path):
    make_song(tmp_path, "s1", "聽海", "張惠妹")
    make_song(tmp_path, "s2", "溫柔", "五月天")
    make_song(tmp_path, "s3", "聽說愛情回來過", "丁噹")
    make_song(tmp_path, "s4", "Carpenters - Yesterday Once More", "Carpenters - Topic")
    facets = build_finder(tmp_path).facets()
    buckets = {b["chars"]: b["count"] for b in facets["char_buckets"]}
    assert buckets == {2: 2, 7: 1}
    assert facets["latin_count"] == 1   # 英文歌不進字數桶，另外報一個數
    assert facets["total"] == 4
    assert facets["bopomofo_available"] is BOPOMOFO_AVAILABLE
    assert facets["rows"] == BOPOMOFO_ROWS


def test_search_survives_broken_metadata(tmp_path):
    make_song(tmp_path, "s1", "稻香", "周杰倫")
    (tmp_path / "s1" / "metadata.json").write_text("{壞掉的 JSON", encoding="utf-8")
    # 壞掉那首消失，整個查歌不該跟著炸
    assert build_finder(tmp_path).search(query="稻香")["total"] == 0


@needs_bopomofo
def test_next_keys_greys_out_the_dead_ends(tmp_path):
    # 商用點歌機會把「按下去必定落空」的鍵變灰，使用者才不會在鍵盤上亂試
    make_song(tmp_path, "s1", "稻香", "周杰倫")
    make_song(tmp_path, "s2", "稻草人", "某歌手")
    make_song(tmp_path, "s3", "溫柔", "五月天")
    finder = build_finder(tmp_path)
    assert finder.search()["next_keys"] == ["ㄉ", "ㄨ"]
    after = finder.search(query="ㄉ")["next_keys"]
    assert "ㄒ" in after and "ㄨ" not in after


def test_next_keys_is_empty_when_user_typed_text(tmp_path):
    # 直接打字的時候算不出「下一鍵」，回空清單讓呼叫端把整個鍵盤當成可按
    make_song(tmp_path, "s1", "稻香", "周杰倫")
    assert build_finder(tmp_path).search(query="稻香")["next_keys"] == []
