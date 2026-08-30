"""FastAPI 端點整合測試。

只測不需要網路、不需要 AI 模型的端點。
重的模型都是延遲載入，所以整個 app 可以直接 import。
"""
import pytest
from fastapi.testclient import TestClient

from backend.main import app, favorites

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_favorites():
    """每個測試前後把測試用的收藏清乾淨，不汙染本機的 cache。"""
    for song_id in list(favorites.ids()):
        if song_id.startswith("test_"):
            favorites.remove(song_id)
    yield
    for song_id in list(favorites.ids()):
        if song_id.startswith("test_"):
            favorites.remove(song_id)


def test_server_info():
    res = client.get("/api/info")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "online"
    assert "web_url" in data and "player_url" in data


def test_qrcode_returns_png():
    res = client.get("/api/qrcode")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/png"
    assert res.content[:4] == b"\x89PNG"


def test_queue_state_shape():
    res = client.get("/api/queue")
    assert res.status_code == 200
    data = res.json()
    assert "queue" in data and "is_playing" in data


def test_queue_add_requires_id():
    res = client.post("/api/queue/add", json={})
    assert res.status_code == 400


def test_rankings_shape():
    res = client.get("/api/rankings")
    assert res.status_code == 200
    data = res.json()
    assert "rankings" in data and "total_plays" in data


def test_favorites_toggle_flow():
    song = {"id": "test_fav_001", "title": "測試收藏", "artist": "測試歌手"}

    res = client.post("/api/favorites/toggle", json=song)
    assert res.status_code == 200
    assert res.json()["favorited"] is True

    res = client.get("/api/favorites")
    assert res.status_code == 200
    data = res.json()
    assert "test_fav_001" in data["ids"]
    entry = next(f for f in data["favorites"] if f["song_id"] == "test_fav_001")
    assert entry["title"] == "測試收藏"

    res = client.post("/api/favorites/toggle", json=song)
    assert res.json()["favorited"] is False
    assert "test_fav_001" not in client.get("/api/favorites").json()["ids"]


def test_favorites_toggle_requires_id():
    res = client.post("/api/favorites/toggle", json={"title": "沒有 id"})
    assert res.status_code == 400


def test_favorites_delete():
    client.post("/api/favorites/toggle", json={"id": "test_fav_002", "title": "x"})
    res = client.delete("/api/favorites/test_fav_002")
    assert res.status_code == 200
    res = client.delete("/api/favorites/test_fav_002")
    assert res.status_code == 404


def test_lyrics_missing_song_returns_empty():
    res = client.get("/api/songs/nonexistent_song/lyrics")
    assert res.status_code == 200
    assert res.json()["lyrics"] == []


def test_frontend_is_served():
    res = client.get("/")
    assert res.status_code == 200
    assert "KaraTube" in res.text
