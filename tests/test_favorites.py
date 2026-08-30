"""我的最愛（收藏清單）單元測試。"""
from backend.services.favorites import Favorites


def make_favs(tmp_path):
    return Favorites(tmp_path / "favorites.json")


def test_add_and_list(tmp_path):
    favs = make_favs(tmp_path)
    entry = favs.add({"id": "abc", "title": "月亮代表我的心", "artist": "鄧麗君"})
    assert entry["song_id"] == "abc"
    assert favs.contains("abc")
    assert favs.ids() == ["abc"]
    assert favs.list_all()[0]["title"] == "月亮代表我的心"


def test_add_accepts_song_id_key(tmp_path):
    favs = make_favs(tmp_path)
    favs.add({"song_id": "xyz", "title": "歌"})
    assert favs.contains("xyz")


def test_add_without_id_is_ignored(tmp_path):
    favs = make_favs(tmp_path)
    assert favs.add({"title": "沒有 id"}) is None
    assert favs.list_all() == []


def test_remove(tmp_path):
    favs = make_favs(tmp_path)
    favs.add({"id": "abc", "title": "歌"})
    assert favs.remove("abc") is True
    assert favs.remove("abc") is False
    assert not favs.contains("abc")


def test_toggle_flips_state(tmp_path):
    favs = make_favs(tmp_path)
    r1 = favs.toggle({"id": "abc", "title": "歌"})
    assert r1["favorited"] is True
    r2 = favs.toggle({"id": "abc", "title": "歌"})
    assert r2["favorited"] is False
    assert not favs.contains("abc")


def test_persists_across_reload(tmp_path):
    favs = make_favs(tmp_path)
    favs.add({"id": "abc", "title": "歌", "thumbnail": "http://x/y.jpg"})
    reloaded = make_favs(tmp_path)
    assert reloaded.contains("abc")
    assert reloaded.list_all()[0]["thumbnail"] == "http://x/y.jpg"


def test_newest_first(tmp_path):
    favs = make_favs(tmp_path)
    favs.add({"id": "old", "title": "舊", })
    # 手動把舊的收藏時間往前調，避免同一秒內排序不穩定
    with favs._lock:
        favs._data["old"]["added_at"] = "2000-01-01T00:00:00"
    favs.add({"id": "new", "title": "新"})
    assert [f["song_id"] for f in favs.list_all()] == ["new", "old"]


def test_corrupt_file_starts_fresh(tmp_path):
    fav_file = tmp_path / "favorites.json"
    fav_file.write_text("oops not json", encoding="utf-8")
    favs = Favorites(fav_file)
    assert favs.list_all() == []
