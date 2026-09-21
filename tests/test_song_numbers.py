"""歌號（六位數點歌）單元測試。

這一支釘的是歌號對使用者的那個承諾：**號碼絕不會變成別首歌**。
其餘的（前綴查詢、下一鍵、統計）都是附加的方便，只有這一條壞掉的時候，
使用者會照著記住的號碼點出一首完全不相干的歌，而且沒有任何畫面看得出哪裡錯了。
"""
import json

import pytest

from backend.services.library import LibraryIndex
from backend.services.song_numbers import (
    NUMBER_START,
    SongNumberBook,
    format_number,
    parse_number,
)
from backend.services.storage import SongStorage


def make_song(root, song_id, title="歌", artist="某歌手", complete=True):
    song_dir = root / song_id
    song_dir.mkdir(parents=True, exist_ok=True)
    (song_dir / "metadata.json").write_text(
        json.dumps({"id": song_id, "title": title, "artist": artist, "duration": 200},
                   ensure_ascii=False), encoding="utf-8")
    (song_dir / "lyrics.json").write_text(
        json.dumps([{"start": 0, "end": 3, "text": "詞"}], ensure_ascii=False),
        encoding="utf-8")
    if complete:
        (song_dir / "instrumental.mp3").write_bytes(b"x")
        (song_dir / "vocals.mp3").write_bytes(b"x")
    return song_dir


def entry(song_id, title="歌", cached_at=1000, artist="某歌手"):
    return {"song_id": song_id, "id": song_id, "title": title,
            "artist_name": artist, "cached_at": cached_at}


# --- 號碼解析 ---

def test_parse_number_takes_digits_only():
    assert parse_number("100237") == 100237
    assert parse_number(" 100237 ") == 100237
    assert parse_number(100237) == 100237
    assert parse_number("10023x") is None
    assert parse_number("#100237") is None
    assert parse_number("") is None
    # 六位數以下不是歌號（號碼從 100001 起跳，所以沒有五位數的歌號）
    assert parse_number("1234") is None
    assert parse_number(True) is None


def test_format_number_has_no_padding():
    assert format_number(100237) == "100237"
    assert format_number("nope") == ""


# --- 發號 ---

def test_first_song_gets_the_first_number(tmp_path):
    book = SongNumberBook(tmp_path / "book.json")
    rows = book.assign([entry("a")])
    assert rows[0]["number"] == NUMBER_START


def test_numbers_are_assigned_in_cached_order_not_scan_order(tmp_path):
    """同一份曲庫在兩台機器上要長出同一套號碼，所以發號順序不能靠掃描順序。"""
    first = SongNumberBook(tmp_path / "a.json")
    first.assign([entry("late", cached_at=200), entry("early", cached_at=100)])
    second = SongNumberBook(tmp_path / "b.json")
    second.assign([entry("early", cached_at=100), entry("late", cached_at=200)])
    assert first.number_of("early") == second.number_of("early") == NUMBER_START
    assert first.number_of("late") == second.number_of("late") == NUMBER_START + 1


def test_same_second_ties_break_on_song_id(tmp_path):
    book = SongNumberBook(tmp_path / "book.json")
    book.assign([entry("bbb", cached_at=100), entry("aaa", cached_at=100)])
    assert book.number_of("aaa") < book.number_of("bbb")


def test_number_is_stable_across_calls(tmp_path):
    book = SongNumberBook(tmp_path / "book.json")
    book.assign([entry("a")])
    before = book.number_of("a")
    book.assign([entry("a"), entry("b", cached_at=2000)])
    assert book.number_of("a") == before


def test_numbers_survive_restart(tmp_path):
    path = tmp_path / "book.json"
    SongNumberBook(path).assign([entry("a"), entry("b", cached_at=2000)])
    reopened = SongNumberBook(path)
    assert reopened.number_of("a") == NUMBER_START
    assert reopened.number_of("b") == NUMBER_START + 1


# --- 號碼絕不回收 ---

def test_deleted_song_does_not_free_its_number(tmp_path):
    """刪掉的歌留下墓碑：那組號碼不會被下一首歌撿走。"""
    book = SongNumberBook(tmp_path / "book.json")
    book.assign([entry("gone")])
    gone_number = book.number_of("gone")
    # 曲庫裡只剩另一首（被刪的那首不再出現在清單裡）
    book.assign([entry("fresh", cached_at=9000)])
    assert book.number_of("fresh") != gone_number
    # 而且舊號碼還查得到原本是誰
    record = book.lookup(gone_number)
    assert record["song_id"] == "gone"


def test_returning_song_reclaims_its_own_number(tmp_path):
    """砍掉重跑、或刪掉之後又點一次：歌回來拿回原號，不發新號。"""
    book = SongNumberBook(tmp_path / "book.json")
    book.assign([entry("x", title="稻香")])
    original = book.number_of("x")
    book.assign([entry("y", cached_at=5000)])           # 中間又進了別首歌
    rows = book.assign([entry("x", title="稻香 (重新處理)")])
    assert rows[0]["number"] == original


def test_tombstone_remembers_the_last_known_title(tmp_path):
    book = SongNumberBook(tmp_path / "book.json")
    book.assign([entry("x", title="舊標題")])
    book.assign([entry("x", title="稻香")])
    assert book.lookup(book.number_of("x"))["title"] == "稻香"


def test_manual_edit_cannot_hand_out_a_remembered_number(tmp_path):
    """有人手動刪掉號碼簿裡的一筆之後，續發也不該發出那組已經有人記住的號碼。"""
    path = tmp_path / "book.json"
    book = SongNumberBook(path)
    book.assign([entry("a"), entry("b", cached_at=2000), entry("c", cached_at=3000)])
    highest = book.number_of("c")
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["songs"].pop("b")          # 手動刪掉中間那一筆
    raw["next"] = NUMBER_START     # 連下一號都被改回開頭
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    reopened = SongNumberBook(path)
    reopened.assign([entry("d", cached_at=4000)])
    assert reopened.number_of("d") > highest


def test_broken_book_serves_no_numbers_and_is_not_overwritten(tmp_path):
    """號碼簿讀不出來時：寧可整個功能停掉，也不要重發一套騙人的號碼。"""
    path = tmp_path / "book.json"
    path.write_text("{壞掉的 JSON", encoding="utf-8")
    book = SongNumberBook(path)
    rows = book.assign([entry("a")])
    assert "number" not in rows[0]
    assert book.ensure("a") is None
    assert book.stats()["available"] is False
    # 原檔留著讓人去修或還原備份
    assert path.read_text(encoding="utf-8") == "{壞掉的 JSON"


# --- 單首發號 ---

def test_ensure_assigns_once_and_matches_assign(tmp_path):
    book = SongNumberBook(tmp_path / "book.json")
    number = book.ensure("a", "稻香", "周杰倫")
    assert number == NUMBER_START
    assert book.ensure("a") == number          # 再叫一次不發新號
    rows = book.assign([entry("a")])
    assert rows[0]["number"] == number


# --- 查號與鍵盤 ---

def test_lookup_unknown_number_returns_none(tmp_path):
    book = SongNumberBook(tmp_path / "book.json")
    book.assign([entry("a")])
    assert book.lookup(999999) is None
    assert book.lookup("abc") is None


def test_candidates_only_list_songs_you_can_actually_order(tmp_path):
    book = SongNumberBook(tmp_path / "book.json")
    book.assign([entry("a"), entry("b", cached_at=2000)])
    picked = book.candidates("", ready_ids={"a"})
    assert picked["song_ids"] == ["a"]
    assert picked["total"] == 1


def test_candidates_filter_by_prefix(tmp_path):
    book = SongNumberBook(tmp_path / "book.json")
    ids = [f"s{i}" for i in range(12)]
    book.assign([entry(sid, cached_at=1000 + i) for i, sid in enumerate(ids)])
    picked = book.candidates("10001", ready_ids=ids)
    # 100010 ~ 100012 開頭是 10001
    assert set(picked["song_ids"]) == {"s9", "s10", "s11"}


def test_next_digits_say_which_key_still_has_songs(tmp_path):
    book = SongNumberBook(tmp_path / "book.json")
    book.assign([entry("a"), entry("b", cached_at=2000)])   # 100001, 100002
    picked = book.candidates("10000", ready_ids={"a", "b"})
    assert picked["next_digits"] == ["1", "2"]
    # 打滿之後沒有下一鍵可按
    assert book.candidates("100001", ready_ids={"a", "b"})["next_digits"] == []


def test_candidates_respect_limit(tmp_path):
    book = SongNumberBook(tmp_path / "book.json")
    ids = [f"s{i}" for i in range(10)]
    book.assign([entry(sid, cached_at=1000 + i) for i, sid in enumerate(ids)])
    picked = book.candidates("", ready_ids=ids, limit=3)
    assert len(picked["song_ids"]) == 3
    assert picked["total"] == 10        # 「符合幾首」講的是全部，不是這一頁


def test_stats_count_retired_numbers(tmp_path):
    book = SongNumberBook(tmp_path / "book.json")
    book.assign([entry("a"), entry("b", cached_at=2000)])
    stats = book.stats(ready_ids={"a"})
    assert stats["assigned"] == 2
    assert stats["in_library"] == 1
    assert stats["retired"] == 1
    assert stats["available"] is True


def test_records_are_sorted_by_number(tmp_path):
    book = SongNumberBook(tmp_path / "book.json")
    book.assign([entry("a"), entry("b", cached_at=2000)])
    numbers = [r["number"] for r in book.records()]
    assert numbers == sorted(numbers)


# --- 與曲庫索引的整合 ---

def test_library_entries_carry_numbers(tmp_path):
    songs = tmp_path / "songs"
    songs.mkdir()
    make_song(songs, "s1", "周杰倫 - 稻香")
    book = SongNumberBook(tmp_path / "book.json")
    library = LibraryIndex(SongStorage(songs), song_numbers=book)
    entries = library.entries()
    assert entries[0]["number"] == NUMBER_START


def test_incomplete_songs_get_no_number(tmp_path):
    """檔案不齊的歌唱不了，也就不該佔掉一組號碼（那組號碼會變成永遠的墓碑）。"""
    songs = tmp_path / "songs"
    songs.mkdir()
    make_song(songs, "half", "沒跑完的歌", complete=False)
    book = SongNumberBook(tmp_path / "book.json")
    LibraryIndex(SongStorage(songs), song_numbers=book).entries()
    assert book.number_of("half") is None


def test_library_without_a_book_still_works(tmp_path):
    songs = tmp_path / "songs"
    songs.mkdir()
    make_song(songs, "s1")
    entries = LibraryIndex(SongStorage(songs)).entries()
    assert entries and "number" not in entries[0]


@pytest.mark.parametrize("bad", ["", "   ", "abc"])
def test_candidates_ignore_non_digits_in_prefix(tmp_path, bad):
    book = SongNumberBook(tmp_path / "book.json")
    book.assign([entry("a")])
    assert book.candidates(bad, ready_ids={"a"})["song_ids"] == ["a"]
