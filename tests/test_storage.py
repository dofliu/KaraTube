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
