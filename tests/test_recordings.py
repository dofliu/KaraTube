"""錄唱回放（錄音庫）的單元測試。

這支功能會往磁碟寫檔，而且寫的是跟歌曲快取同一顆磁碟，所以測試的重點
不是「存得進去」，而是「什麼時候不存、什麼時候刪、刪的是不是該刪的那一筆」：

  * 配額滿了要從最舊的刪，但**標記保留的不能刪**；
  * 全部都保留而配額滿了，要照實說刪不動，而不是默默存進去把磁碟撐爆；
  * 索引與資料夾對不上（手動刪檔、斷電留下孤兒檔）要自己收拾；
  * 錄音 id 是外面送進來的字串，塞 `../` 不能讀到別的檔案。
"""
import json

import pytest

from backend.services.recordings import (
    DEFAULT_MAX_BYTES,
    HARD_MAX_UPLOAD_BYTES,
    RecordingLibrary,
    normalize_mime,
    suffix_for_mime,
)


def take(size: int = 1024) -> bytes:
    return b"\x00" * size


def meta(song_id: str = "abc123", **extra):
    base = {"song_id": song_id, "title": "測試歌", "artist": "測試歌手",
            "singer": "阿明", "duration_ms": 180_000, "score": 88_000,
            "grade": "A", "accuracy": 0.71, "mime": "audio/webm;codecs=opus"}
    base.update(extra)
    return base


@pytest.fixture()
def library(tmp_path):
    return RecordingLibrary(tmp_path / "recordings")


# --- mime 與副檔名 ---

def test_mime_with_codecs_still_gets_a_real_suffix():
    # 瀏覽器送來的一定帶 codecs 參數，整串拿去查表會查不到
    assert normalize_mime("audio/webm;codecs=opus") == "audio/webm"
    assert suffix_for_mime("audio/webm;codecs=opus") == ".webm"
    assert suffix_for_mime("audio/mp4") == ".m4a"       # Safari
    assert suffix_for_mime("application/x-evil") == ".webm"   # 認不得就退回預設


# --- 基本存取 ---

def test_save_then_read_back(library):
    result = library.save(take(2048), meta())
    assert result["status"] == "saved"
    rec = result["recording"]
    assert rec["bytes"] == 2048
    assert rec["song_id"] == "abc123"
    assert rec["pinned"] is False
    assert library.path_for(rec["id"]).read_bytes() == take(2048)

    listed = library.list_all()
    assert [e["id"] for e in listed] == [rec["id"]]


def test_list_is_newest_first_and_filterable(library):
    a = library.save(take(), meta("song-a", singer="阿明"))["recording"]
    b = library.save(take(), meta("song-b", singer="小美"))["recording"]
    assert [e["id"] for e in library.list_all()] == [b["id"], a["id"]]
    assert [e["song_id"] for e in library.list_all(song_id="song-a")] == ["song-a"]
    assert [e["singer"] for e in library.list_all(singer="小美")] == ["小美"]


def test_index_survives_restart(tmp_path):
    first = RecordingLibrary(tmp_path / "rec")
    rec = first.save(take(), meta())["recording"]

    second = RecordingLibrary(tmp_path / "rec")
    assert [e["id"] for e in second.list_all()] == [rec["id"]]
    assert second.path_for(rec["id"]) is not None


def test_broken_index_falls_back_to_empty_instead_of_crashing(tmp_path):
    base = tmp_path / "rec"
    base.mkdir()
    (base / "index.json").write_text("{ 這不是 JSON", encoding="utf-8")
    library = RecordingLibrary(base)
    assert library.list_all() == []
    # 壞掉之後照樣能存新的
    assert library.save(take(), meta())["status"] == "saved"


# --- 拒絕的情形 ---

def test_rejects_empty_and_missing_song_id(library):
    assert library.save(b"", meta())["reason"] == "empty"
    assert library.save(take(), meta(song_id=""))["reason"] == "missing_song"


def test_rejects_take_bigger_than_the_whole_quota(library):
    # 比配額還大：刪光別人也還是放不下，當場拒絕比先刪再失敗誠實
    result = library.save(take(4096), meta(), max_count=10, max_bytes=2048)
    assert result["status"] == "rejected"
    assert result["reason"] == "over_quota"
    assert library.list_all() == []


def test_hard_upload_ceiling_is_independent_of_settings(library):
    huge = b"\x00" * (HARD_MAX_UPLOAD_BYTES + 1)
    assert library.save(huge, meta(), max_bytes=0)["reason"] == "too_large"


# --- 配額 ---

def test_count_quota_evicts_oldest_first(library):
    kept = []
    for i in range(4):
        kept.append(library.save(take(), meta(f"song-{i}"), max_count=2)["recording"])
    ids = [e["id"] for e in library.list_all()]
    assert ids == [kept[3]["id"], kept[2]["id"]]
    # 被擠掉的檔案要真的從磁碟消失，不然配額算得到、空間卻沒放回來
    assert library.path_for(kept[0]["id"]) is None
    assert not (library.base_dir / kept[0]["file"]).exists()


def test_byte_quota_evicts_until_it_fits(library):
    library.save(take(1000), meta("a"), max_count=0, max_bytes=2500)
    library.save(take(1000), meta("b"), max_count=0, max_bytes=2500)
    library.save(take(1000), meta("c"), max_count=0, max_bytes=2500)
    assert [e["song_id"] for e in library.list_all()] == ["c", "b"]


def test_pinned_recordings_survive_eviction(library):
    first = library.save(take(), meta("keep-me"), max_count=2)["recording"]
    library.set_pinned(first["id"], True)
    library.save(take(), meta("b"), max_count=2)
    library.save(take(), meta("c"), max_count=2)

    ids = [e["song_id"] for e in library.list_all()]
    # 最舊的那一筆被標記保留，所以被擠掉的是次舊的 b
    assert "keep-me" in ids and "b" not in ids


def test_all_pinned_and_full_is_refused_not_saved_then_deleted(library):
    """
    舊的全被標記保留、配額又滿了：要當場拒絕並說明白。

    最容易寫錯的版本是「照樣存，然後跑一輪清理」—— 清理時唯一可刪的就是
    剛存進去的那一筆，結果回報存檔成功，使用者回頭卻找不到那一次。
    """
    for i in range(2):
        rec = library.save(take(), meta(f"s{i}"), max_count=2)["recording"]
        library.set_pinned(rec["id"], True)

    result = library.save(take(), meta("new"), max_count=2)
    assert result["status"] == "rejected"
    assert result["reason"] == "quota_pinned"
    assert "保留" in result["message"]
    # 拒絕就要收乾淨：兩筆保留的原封不動，資料夾裡不留半個殘檔
    assert [e["song_id"] for e in library.list_all()] == ["s1", "s0"]
    assert sorted(p.name for p in library.base_dir.iterdir()) == sorted(
        ["index.json"] + [e["file"] for e in library.list_all()])


def test_stats_reports_remaining_not_just_used(library):
    library.save(take(1000), meta(), max_count=10, max_bytes=10_000)
    stats = library.stats(max_count=10, max_bytes=10_000)
    assert stats["count"] == 1
    assert stats["total_bytes"] == 1000
    assert stats["remaining_count"] == 9
    assert stats["remaining_bytes"] == 9000
    # 0 = 不限制，「還剩多少」這時候沒有意義
    assert library.stats(max_count=0, max_bytes=0)["remaining_bytes"] is None


# --- 索引與資料夾對帳 ---

def test_missing_file_is_dropped_from_index_on_reload(tmp_path):
    base = tmp_path / "rec"
    first = RecordingLibrary(base)
    rec = first.save(take(), meta())["recording"]
    (base / rec["file"]).unlink()      # 有人手動刪掉音檔

    second = RecordingLibrary(base)
    # 留著只會在清單上出現一筆點下去沒聲音的項目
    assert second.list_all() == []


def test_orphan_file_without_index_entry_is_cleaned_up(tmp_path):
    base = tmp_path / "rec"
    base.mkdir()
    orphan = base / "20260101-010101-abcdef.webm"
    orphan.write_bytes(take())
    (base / "index.json").write_text(json.dumps({"recordings": []}), encoding="utf-8")

    RecordingLibrary(base)
    # 寫檔成功但索引沒存到就斷電會留下這種檔案，不清掉就永遠佔著配額外的空間
    assert not orphan.exists()


# --- 安全性 ---

@pytest.mark.parametrize("bad_id", [
    "../../etc/passwd",
    "..%2f..%2fetc%2fpasswd",
    "20260101-010101-abcdef/../../secret",
    "index.json",
    "",
    "20260101-010101-ABCDEF",   # 大寫不是伺服器會產生的格式
])
def test_bogus_ids_never_resolve_to_a_path(library, bad_id):
    library.save(take(), meta())
    assert library.get(bad_id) is None
    assert library.path_for(bad_id) is None
    assert library.delete(bad_id) is False
    assert library.set_pinned(bad_id, True) is None


# --- 刪除 ---

def test_delete_removes_entry_and_file(library):
    rec = library.save(take(), meta())["recording"]
    path = library.base_dir / rec["file"]
    assert library.delete(rec["id"]) is True
    assert not path.exists()
    assert library.delete(rec["id"]) is False   # 第二次就找不到了


def test_clear_keeps_pinned_by_default(library):
    keep = library.save(take(), meta("keep"))["recording"]
    library.set_pinned(keep["id"], True)
    library.save(take(), meta("drop"))

    assert library.clear() == 1
    assert [e["song_id"] for e in library.list_all()] == ["keep"]
    # 要連保留的一起刪就得明講
    assert library.clear(keep_pinned=False) == 1
    assert library.list_all() == []


def test_default_quota_constants_are_sane():
    # 預設配額若比單檔硬上限還小，第一次錄音就會被自己的預設值拒收
    assert DEFAULT_MAX_BYTES > HARD_MAX_UPLOAD_BYTES
