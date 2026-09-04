"""本地歌曲快取（SongStorage）單元測試。"""
import json
import os
import time

from backend.services.storage import SongStorage


def make_song(storage_dir, song_id, title="測試歌"):
    song_dir = storage_dir / song_id
    song_dir.mkdir(parents=True)
    (song_dir / "metadata.json").write_text(
        json.dumps({"id": song_id, "title": title}), encoding="utf-8")
    return song_dir


def test_get_song_metadata(tmp_path):
    storage = SongStorage(tmp_path)
    make_song(tmp_path, "abc", "我的歌")
    meta = storage.get_song_metadata("abc")
    assert meta["title"] == "我的歌"
    assert storage.get_song_metadata("missing") is None


def test_get_song_lyrics_missing_returns_empty(tmp_path):
    storage = SongStorage(tmp_path)
    assert storage.get_song_lyrics("missing") == []


def test_get_song_lyrics(tmp_path):
    storage = SongStorage(tmp_path)
    song_dir = make_song(tmp_path, "abc")
    lyrics = [{"start": 0.0, "end": 2.0, "text": "第一句"}]
    (song_dir / "lyrics.json").write_text(json.dumps(lyrics), encoding="utf-8")
    assert storage.get_song_lyrics("abc") == lyrics


def test_get_song_pitch_missing_returns_default(tmp_path):
    storage = SongStorage(tmp_path)
    assert storage.get_song_pitch("missing") == {"notes": [], "points": []}


def test_list_cached_songs(tmp_path):
    storage = SongStorage(tmp_path)
    make_song(tmp_path, "a", "歌A")
    make_song(tmp_path, "b", "歌B")
    # 沒有 metadata 的資料夾不該出現在清單裡
    (tmp_path / "broken").mkdir()
    songs = storage.list_cached_songs()
    assert sorted(s["title"] for s in songs) == ["歌A", "歌B"]


def test_delete_song(tmp_path):
    storage = SongStorage(tmp_path)
    make_song(tmp_path, "abc")
    assert storage.delete_song("abc") is True
    assert storage.get_song_metadata("abc") is None
    assert storage.delete_song("abc") is False


def make_complete_song(storage_dir, song_id, title="完整歌", payload=b"x" * 1000):
    """帶齊四個必要檔案的快取資料夾。"""
    song_dir = make_song(storage_dir, song_id, title)
    (song_dir / "instrumental.mp3").write_bytes(payload)
    (song_dir / "vocals.mp3").write_bytes(payload)
    (song_dir / "lyrics.json").write_text("[]", encoding="utf-8")
    return song_dir


def test_get_song_size(tmp_path):
    storage = SongStorage(tmp_path)
    make_complete_song(tmp_path, "abc", payload=b"x" * 1000)
    # 兩個 1000B 的 mp3 + metadata + lyrics，至少 2000B
    assert storage.get_song_size("abc") >= 2000
    assert storage.get_song_size("missing") == 0


def test_list_cache_entries_includes_broken_folders(tmp_path):
    storage = SongStorage(tmp_path)
    make_complete_song(tmp_path, "good1")
    # 只有 metadata、缺音檔 → 不完整
    make_song(tmp_path, "partial1")
    # 連 metadata 都沒有的壞資料夾，點歌清單看不到，但管理介面要看得到
    (tmp_path / "broken1").mkdir()

    entries = {e["song_id"]: e for e in storage.list_cache_entries()}
    assert set(entries) == {"good1", "partial1", "broken1"}
    assert entries["good1"]["complete"] is True
    assert entries["good1"]["missing_files"] == []
    assert entries["partial1"]["complete"] is False
    assert "instrumental.mp3" in entries["partial1"]["missing_files"]
    assert entries["broken1"]["complete"] is False
    assert "metadata.json" in entries["broken1"]["missing_files"]
    # 點歌用的清單依然把壞資料夾藏起來
    assert len(storage.list_cached_songs()) == 2


def test_cache_stats(tmp_path):
    storage = SongStorage(tmp_path)
    make_complete_song(tmp_path, "good1")
    (tmp_path / "broken1").mkdir()

    stats = storage.cache_stats()
    assert stats["song_count"] == 2
    assert stats["complete_count"] == 1
    assert stats["incomplete_count"] == 1
    assert stats["total_bytes"] >= 2000
    assert stats["disk_total_bytes"] > 0


def test_update_song_metadata_patches_in_place(tmp_path):
    """事後補算的響度要能寫回 metadata，而不是整個蓋掉原本的欄位。"""
    storage = SongStorage(tmp_path)
    make_complete_song(tmp_path, "abc", title="我的歌")
    updated = storage.update_song_metadata("abc", {"loudness": {"lufs": -18.0}})
    assert updated["title"] == "我的歌"
    assert updated["loudness"]["lufs"] == -18.0
    # 重新讀檔也要看得到
    assert storage.get_song_metadata("abc")["loudness"]["lufs"] == -18.0


def test_update_song_metadata_on_missing_song(tmp_path):
    assert SongStorage(tmp_path).update_song_metadata("nope", {"x": 1}) is None


def _age(song_dir, seconds_ago):
    """把資料夾的 mtime 往前調，模擬「比較舊的快取」。"""
    stamp = time.time() - seconds_ago
    os.utime(song_dir, (stamp, stamp))


def test_enforce_cache_limit_deletes_oldest_first(tmp_path):
    storage = SongStorage(tmp_path)
    _age(make_complete_song(tmp_path, "old", payload=b"x" * 2000), 3000)
    _age(make_complete_song(tmp_path, "mid", payload=b"x" * 2000), 2000)
    _age(make_complete_song(tmp_path, "new", payload=b"x" * 2000), 1000)

    total = storage.cache_stats()["total_bytes"]
    removed = storage.enforce_cache_limit(int(total * 0.7))

    assert [r["song_id"] for r in removed] == ["old"]
    assert {e["song_id"] for e in storage.list_cache_entries()} == {"mid", "new"}


def test_enforce_cache_limit_never_deletes_songs_in_use(tmp_path):
    """演唱中或還在佇列裡的歌被刪掉的話，舞台會直接斷片。"""
    storage = SongStorage(tmp_path)
    _age(make_complete_song(tmp_path, "old", payload=b"x" * 2000), 3000)
    _age(make_complete_song(tmp_path, "new", payload=b"x" * 2000), 1000)

    removed = storage.enforce_cache_limit(1000, protected_ids={"old"})

    assert [r["song_id"] for r in removed] == ["new"]
    assert {e["song_id"] for e in storage.list_cache_entries()} == {"old"}


def test_enforce_cache_limit_does_nothing_when_under_limit(tmp_path):
    storage = SongStorage(tmp_path)
    make_complete_song(tmp_path, "a")
    assert storage.enforce_cache_limit(10 * 1024 ** 3) == []
    assert len(storage.list_cache_entries()) == 1


def test_zero_limit_means_unlimited(tmp_path):
    storage = SongStorage(tmp_path)
    make_complete_song(tmp_path, "a")
    assert storage.enforce_cache_limit(0) == []
    assert len(storage.list_cache_entries()) == 1
