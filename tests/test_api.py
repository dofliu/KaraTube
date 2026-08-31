"""FastAPI 端點整合測試。

只測不需要網路、不需要 AI 模型的端點。
重的模型都是延遲載入，所以整個 app 可以直接 import。
"""
import pytest
from fastapi.testclient import TestClient

from backend.main import app, favorites, song_history, score_history

client = TestClient(app)


@pytest.fixture()
def snapshot_history_and_scores():
    """測試前後還原已唱歷史與評分歷史，不汙染本機的 cache。"""
    saved_history = [dict(e) for e in song_history._entries]
    saved_scores = [dict(e) for e in score_history._entries]
    saved_bests = {k: dict(v) for k, v in score_history._bests.items()}
    yield
    song_history._entries = saved_history
    song_history._save()
    score_history._entries = saved_scores
    score_history._bests = saved_bests
    score_history._save()


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


def test_history_shape():
    res = client.get("/api/history")
    assert res.status_code == 200
    data = res.json()
    assert "history" in data and "today_count" in data and "total_count" in data


def test_scores_flow(snapshot_history_and_scores):
    payload = {"song_id": "test_score_001", "title": "測試結算",
               "score": 1234, "accuracy": 0.42, "max_combo": 17, "grade": "S"}
    res = client.post("/api/scores", json=payload)
    assert res.status_code == 200
    result = res.json()["result"]
    assert result["score"] == 1234
    assert result["is_new_best"] is True
    assert "beat_percent" in result

    res = client.get("/api/scores")
    assert res.status_code == 200
    data = res.json()
    assert any(e["song_id"] == "test_score_001" for e in data["scores"])
    assert data["bests"]["test_score_001"]["score"] == 1234

    res = client.get("/api/scores/test_score_001/best")
    assert res.status_code == 200
    assert res.json()["best"]["score"] == 1234


def test_scores_requires_song_id():
    res = client.post("/api/scores", json={"score": 100})
    assert res.status_code == 400


def test_scores_rejects_bad_score():
    res = client.post("/api/scores", json={"song_id": "test_bad", "score": "xyz"})
    assert res.status_code == 400


def test_best_score_unknown_song_is_null():
    res = client.get("/api/scores/definitely_never_sung/best")
    assert res.status_code == 200
    assert res.json()["best"] is None


def test_lyrics_missing_song_returns_empty():
    res = client.get("/api/songs/nonexistent_song/lyrics")
    assert res.status_code == 200
    assert res.json()["lyrics"] == []


def test_frontend_is_served():
    res = client.get("/")
    assert res.status_code == 200
    assert "KaraTube" in res.text
