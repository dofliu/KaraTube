"""本地歌曲快取（SongStorage）單元測試。"""
import json

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
