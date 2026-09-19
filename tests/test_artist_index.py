"""歌星查歌（注音首字查歌手 → 翻開歌單）單元測試。

這一支釘的主要是「同一個人在曲庫裡有三種寫法」這件事：

1. 「周杰倫」「Jay Chou」「周杰倫 Jay Chou」要併成**一位**歌手，歌單是完整的那份
   （分成三位的話，使用者按 ㄓㄐㄌ 只會看到其中幾首，並以為曲庫裡就這幾首）。
2. 併要保守：「張惠妹」與「張惠」不是同一個人，漢字不同就不准併。
3. 「未知歌手」不進注音索引，但照樣列出來（排最後）—— 那些歌是真的存在的。

pypinyin 沒裝的環境只跳過注音那幾條，用打字查歌手照樣必須是好的。
"""
import json

import pytest

from backend.services.artist_index import (
    ArtistFinder,
    merge_tokens,
    name_keys,
    name_parts,
)
from backend.services.library import LibraryIndex
from backend.services.song_index import BOPOMOFO_AVAILABLE, BOPOMOFO_ROWS, SongFinder
from backend.services.storage import SongStorage

needs_bopomofo = pytest.mark.skipif(
    not BOPOMOFO_AVAILABLE, reason="這個環境沒有 pypinyin，注音查詢本來就關閉")


def make_song(tmp_path, song_id, title, artist, complete=True):
    song_dir = tmp_path / song_id
    song_dir.mkdir(parents=True)
    (song_dir / "metadata.json").write_text(
        json.dumps({"id": song_id, "title": title, "artist": artist, "duration": 200},
                   ensure_ascii=False), encoding="utf-8")
    (song_dir / "lyrics.json").write_text(
        json.dumps([{"start": 0, "end": 3, "text": "啦啦啦"}], ensure_ascii=False),
        encoding="utf-8")
    if complete:
        (song_dir / "instrumental.mp3").write_bytes(b"x")
        (song_dir / "vocals.mp3").write_bytes(b"x")
    return song_dir


def build_finder(tmp_path):
    storage = SongStorage(tmp_path)
    return ArtistFinder(SongFinder(storage, LibraryIndex(storage)))


# --- 名字拆解 ---

def test_name_parts_splits_han_and_latin():
    assert name_parts("周杰倫 Jay Chou") == ("周杰倫", "Jay Chou")
    assert name_parts("五月天") == ("五月天", "")
    assert name_parts("Carpenters") == ("", "Carpenters")


def test_merge_tokens_compares_whole_segments():
    # 整段比，不是子字串比：張惠妹與張惠不共用任何一段
    assert set(merge_tokens("張惠妹")) & set(merge_tokens("張惠")) == set()
    assert "han:周杰倫" in merge_tokens("周杰倫 Jay Chou")
    assert "lat:jaychou" in merge_tokens("周杰倫 Jay Chou")


def test_merge_tokens_ignores_one_letter_names():
    # 一個字母誰都可能撞到，不能拿來當「同一個人」的證據
    assert merge_tokens("A") == []


@needs_bopomofo
def test_name_keys_indexes_han_and_latin_separately():
    variants = name_keys("周杰倫 Jay Chou")
    assert ["J", "C"] in variants
    han = [v for v in variants if v != ["J", "C"]][0]
    assert len(han) == 3 and "ㄓ" in han[0]


# --- 合併 ---

def test_same_artist_written_three_ways_becomes_one(tmp_path):
    # 這是整支模組存在的理由：不併的話歌手清單會出現三個周杰倫，
    # 每一個手上只有他一部分的歌
    make_song(tmp_path, "s1", "稻香", "周杰倫 - Topic")
    make_song(tmp_path, "s2", "晴天", "Jay Chou")
    make_song(tmp_path, "s3", "青花瓷", "周杰倫 Jay Chou")
    groups = build_finder(tmp_path).groups()
    assert len(groups) == 1
    assert groups[0]["name"] == "周杰倫"          # 介面是中文的，顯示名取漢字
    assert groups[0]["count"] == 3               # 歌單是完整的那一份
    assert "Jay Chou" in groups[0]["aliases"]


def test_different_artists_are_never_merged(tmp_path):
    make_song(tmp_path, "s1", "聽海", "張惠妹")
    make_song(tmp_path, "s2", "偷故事的人", "張惠")
    names = {g["name"] for g in build_finder(tmp_path).groups()}
    assert names == {"張惠妹", "張惠"}


def test_same_english_name_does_not_merge_different_han_names(tmp_path):
    # 英文名撞名時，漢字那一關要擋下來
    make_song(tmp_path, "s1", "歌一", "林俊傑 JJ")
    make_song(tmp_path, "s2", "歌二", "林宥嘉 JJ")
    names = {g["name"] for g in build_finder(tmp_path).groups()}
    assert names == {"林俊傑 JJ", "林宥嘉 JJ"}


def test_english_only_spelling_is_not_merged_without_evidence(tmp_path):
    # 「周杰倫」與「Jay Chou」之間沒有任何一段字相同，曲庫裡也沒有寫著兩者是
    # 同一個人的那一筆。這種時候寧可漏併（清單多一位）也不要猜 ——
    # 猜錯的代價是別人的歌混進歌單，使用者會當成系統壞掉
    make_song(tmp_path, "s1", "稻香", "周杰倫 - Topic")
    make_song(tmp_path, "s2", "晴天", "Jay Chou")
    assert len(build_finder(tmp_path).groups()) == 2


def test_unknown_artist_is_listed_last_and_flagged(tmp_path):
    make_song(tmp_path, "s1", "某一首歌", "")
    make_song(tmp_path, "s2", "稻香", "周杰倫 - Topic")
    groups = build_finder(tmp_path).groups()
    assert groups[-1]["unknown"] is True
    assert groups[-1]["keys"] == []      # 不是名字，不進注音索引
    assert groups[-1]["count"] == 1      # 但歌照樣看得到


# --- 查詢 ---

@needs_bopomofo
def test_search_by_bopomofo_initials(tmp_path):
    make_song(tmp_path, "s1", "稻香", "周杰倫 - Topic")
    make_song(tmp_path, "s2", "溫柔", "五月天 - Topic")
    res = build_finder(tmp_path).search(query="ㄓㄐㄌ")
    assert [a["name"] for a in res["artists"]] == ["周杰倫"]
    assert [s["song_id"] for s in res["songs"]] == ["s1"]


@needs_bopomofo
def test_english_alias_finds_the_same_artist(tmp_path):
    # 按 ㄓㄐㄌ 或按 J C 都要落在同一位，而且是同一份歌單
    # 曲庫裡有一筆「周杰倫 Jay Chou」把兩種寫法串起來，三首歌就該是同一位的
    make_song(tmp_path, "s1", "稻香", "周杰倫 - Topic")
    make_song(tmp_path, "s2", "晴天", "Jay Chou")
    make_song(tmp_path, "s3", "青花瓷", "周杰倫 Jay Chou")
    finder = build_finder(tmp_path)
    by_han = finder.search(query="ㄓㄐㄌ")
    by_latin = finder.search(query="JC")
    assert by_han["selected"]["id"] == by_latin["selected"]["id"] == "周杰倫"
    assert by_han["song_total"] == by_latin["song_total"] == 3


def test_search_by_typed_name(tmp_path):
    make_song(tmp_path, "s1", "稻香", "周杰倫 - Topic")
    make_song(tmp_path, "s2", "溫柔", "五月天 - Topic")
    res = build_finder(tmp_path).search(query="五月")
    assert [a["name"] for a in res["artists"]] == ["五月天"]


def test_single_match_is_auto_selected(tmp_path):
    # 畫面上只剩一位歌手時，再要求使用者點一下沒有任何資訊量
    make_song(tmp_path, "s1", "稻香", "周杰倫 - Topic")
    make_song(tmp_path, "s2", "溫柔", "五月天 - Topic")
    res = build_finder(tmp_path).search(query="五月")
    assert res["auto_selected"] is True
    assert res["selected"]["name"] == "五月天"
    assert [s["song_id"] for s in res["songs"]] == ["s2"]


def test_empty_query_lists_every_artist_and_song(tmp_path):
    # 把條件清掉的那一下不該得到空畫面
    make_song(tmp_path, "s1", "稻香", "周杰倫 - Topic")
    make_song(tmp_path, "s2", "溫柔", "五月天 - Topic")
    res = build_finder(tmp_path).search()
    assert res["artist_total"] == 2
    assert res["song_total"] == 2
    assert res["selected"] is None and res["auto_selected"] is False


def test_selected_artist_survives_a_query_that_excludes_him(tmp_path):
    # 選好歌手之後又多按一個鍵：他要留在畫面上，使用者才知道該退一格
    make_song(tmp_path, "s1", "稻香", "周杰倫 - Topic")
    make_song(tmp_path, "s2", "溫柔", "五月天 - Topic")
    res = build_finder(tmp_path).search(query="五月", artist_id="周杰倫")
    assert res["selected"]["name"] == "周杰倫"
    assert [s["song_id"] for s in res["songs"]] == ["s1"]


def test_picking_an_artist_opens_his_whole_song_list(tmp_path):
    make_song(tmp_path, "s1", "稻香", "周杰倫 - Topic")
    make_song(tmp_path, "s2", "晴天", "周杰倫 Jay Chou")
    make_song(tmp_path, "s3", "溫柔", "五月天 - Topic")
    res = build_finder(tmp_path).search(artist_id="周杰倫")
    # 歌單是併過之後完整的那一份（兩種寫法的歌都在），別人的歌不會混進來
    assert {s["song_id"] for s in res["songs"]} == {"s1", "s2"}
    # 歌名顯示的是萃出來的本體，不是整串 YouTube 標題
    assert {s["core_title"] for s in res["songs"]} == {"稻香", "晴天"}
    assert res["auto_selected"] is False


def test_incomplete_songs_never_appear(tmp_path):
    # 查到一位歌手、點進去卻是一首唱不了的歌，比查不到還糟
    make_song(tmp_path, "bad", "稻香", "周杰倫 - Topic", complete=False)
    assert build_finder(tmp_path).search()["artist_total"] == 0


@needs_bopomofo
def test_next_keys_greys_out_the_dead_ends(tmp_path):
    make_song(tmp_path, "s1", "稻香", "周杰倫 - Topic")
    make_song(tmp_path, "s2", "溫柔", "五月天 - Topic")
    res = build_finder(tmp_path).search()
    assert "ㄓ" in res["next_keys"] and "ㄨ" in res["next_keys"]
    after = build_finder(tmp_path).search(query="ㄓ")["next_keys"]
    assert "ㄐ" in after and "ㄩ" not in after


def test_next_keys_is_empty_when_user_typed_text(tmp_path):
    make_song(tmp_path, "s1", "稻香", "周杰倫 - Topic")
    assert build_finder(tmp_path).search(query="周杰倫")["next_keys"] == []


def test_facets_counts_artists_and_unknowns(tmp_path):
    make_song(tmp_path, "s1", "稻香", "周杰倫 - Topic")
    make_song(tmp_path, "s2", "晴天", "周杰倫 Jay Chou")
    make_song(tmp_path, "s3", "某一首歌", "")
    facets = build_finder(tmp_path).facets()
    assert facets["artist_count"] == 1       # 併過之後只有一位
    assert facets["unknown_count"] == 1
    assert facets["total"] == 3
    assert facets["rows"] == BOPOMOFO_ROWS
    assert facets["bopomofo_available"] is BOPOMOFO_AVAILABLE


def test_search_survives_broken_metadata(tmp_path):
    # metadata 壞掉的那一首讀不出歌手，但整個歌星查歌不該跟著炸 ——
    # 它會掉進「未知歌手」，使用者看得到那首歌還在
    make_song(tmp_path, "s1", "稻香", "周杰倫 - Topic")
    (tmp_path / "s1" / "metadata.json").write_text("{壞掉的 JSON", encoding="utf-8")
    res = build_finder(tmp_path).search()
    assert res["artist_total"] == 1
    assert res["artists"][0]["unknown"] is True
    assert build_finder(tmp_path).search(query="ㄓㄐㄌ")["artist_total"] == 0
