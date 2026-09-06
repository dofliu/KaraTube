"""FastAPI 端點整合測試。

只測不需要網路、不需要 AI 模型的端點。
重的模型都是延遲載入，所以整個 app 可以直接 import。
"""
import json

import pytest
from fastapi.testclient import TestClient

from backend.config import SONGS_DIR
from backend.main import (
    app,
    favorites,
    queue_manager,
    score_history,
    settings,
    song_history,
    storage,
)

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


@pytest.fixture()
def fake_cached_song():
    """在本機快取目錄放一首 test_ 前綴的假歌，測完清掉。"""
    song_id = "test_cache_song_01"
    song_dir = SONGS_DIR / song_id
    song_dir.mkdir(parents=True, exist_ok=True)
    (song_dir / "metadata.json").write_text(
        json.dumps({"id": song_id, "title": "快取測試歌", "artist": "測試"}),
        encoding="utf-8")
    (song_dir / "instrumental.mp3").write_bytes(b"x" * 100)
    yield song_id
    storage.delete_song(song_id)


def test_cache_overview_shape(fake_cached_song):
    res = client.get("/api/cache")
    assert res.status_code == 200
    data = res.json()
    for key in ("songs", "song_count", "total_bytes", "disk_free_bytes",
                "complete_count", "incomplete_count"):
        assert key in data
    entry = next(s for s in data["songs"] if s["song_id"] == fake_cached_song)
    assert entry["title"] == "快取測試歌"
    assert entry["complete"] is False  # 缺 vocals.mp3 與 lyrics.json
    assert entry["size_bytes"] > 0


def test_cache_delete(fake_cached_song):
    res = client.delete(f"/api/cache/{fake_cached_song}")
    assert res.status_code == 200
    assert not (SONGS_DIR / fake_cached_song).exists()
    res = client.delete(f"/api/cache/{fake_cached_song}")
    assert res.status_code == 404


def test_cache_delete_refuses_song_in_use(fake_cached_song):
    """演唱中或佇列裡的歌不能刪快取，不然舞台會直接斷片。"""
    queue_manager.queue.append({"queue_id": "test-q-1", "song_id": fake_cached_song,
                                "status": "READY"})
    try:
        res = client.delete(f"/api/cache/{fake_cached_song}")
        assert res.status_code == 409
        assert (SONGS_DIR / fake_cached_song).exists()
    finally:
        queue_manager.queue[:] = [i for i in queue_manager.queue
                                  if i.get("queue_id") != "test-q-1"]


def test_cache_reprocess_missing_song_404():
    res = client.post("/api/cache/definitely_not_cached/reprocess")
    assert res.status_code == 404


def test_queue_add_records_requester(fake_cached_song):
    """多人包廂：點歌 API 帶 requested_by，佇列項目就記得是誰點的。"""
    saved_current = queue_manager.current_song
    # 先佔住舞台，讓新點的歌留在佇列裡（不然快取歌會直接上台）
    queue_manager.current_song = {"song_id": "occupied", "queue_id": "busy"}
    try:
        res = client.post("/api/queue/add",
                          json={"id": fake_cached_song, "requested_by": "  小美  "})
        assert res.status_code == 200
        item = res.json()["item"]
        assert item["requested_by"] == "小美"
        state = client.get("/api/queue").json()
        assert any(i.get("requested_by") == "小美" for i in state["queue"])
    finally:
        queue_manager.queue[:] = [i for i in queue_manager.queue
                                  if i.get("song_id") != fake_cached_song]
        queue_manager.current_song = saved_current


def test_queue_reorder_endpoint():
    """拖曳排序走的 API：把第 2 首拉到第 0 位，順序要換、其他欄位不動。"""
    fake_items = [{"queue_id": f"test-reorder-{i}", "song_id": f"test_ro_{i}",
                   "status": "READY"} for i in range(3)]
    saved_queue = list(queue_manager.queue)
    queue_manager.queue[:] = fake_items
    try:
        res = client.post("/api/queue/reorder", json={"from_idx": 2, "to_idx": 0})
        assert res.status_code == 200
        state = client.get("/api/queue").json()
        ids = [i["queue_id"] for i in state["queue"] if i["queue_id"].startswith("test-reorder-")]
        assert ids == ["test-reorder-2", "test-reorder-0", "test-reorder-1"]

        # 超出範圍與負索引都不該動到佇列（拖曳中佇列可能被別人改掉）
        for payload in ({"from_idx": 99, "to_idx": 0}, {"from_idx": -1, "to_idx": 1}):
            res = client.post("/api/queue/reorder", json=payload)
            assert res.status_code == 200
            state = client.get("/api/queue").json()
            ids2 = [i["queue_id"] for i in state["queue"] if i["queue_id"].startswith("test-reorder-")]
            assert ids2 == ids
    finally:
        queue_manager.queue[:] = saved_queue


def test_queue_retry_unknown_item_404():
    res = client.post("/api/queue/no-such-queue-id/retry")
    assert res.status_code == 404


def test_lyrics_missing_song_returns_empty():
    res = client.get("/api/songs/nonexistent_song/lyrics")
    assert res.status_code == 200
    assert res.json()["lyrics"] == []


def test_frontend_is_served():
    res = client.get("/")
    assert res.status_code == 200
    assert "KaraTube" in res.text


# --- 系統設定 ---

@pytest.fixture()
def restore_settings():
    """設定是全域狀態，測完要還原，否則會把開發機的設定改掉。"""
    saved = settings.all()
    yield
    settings.update(saved)


def test_settings_endpoint_returns_values_defaults_and_spec():
    res = client.get("/api/settings")
    assert res.status_code == 200
    data = res.json()
    assert set(data["settings"]) == set(data["defaults"]) == set(data["spec"])
    # 前端要靠 spec 長出表單，型別與範圍不能少
    assert data["spec"]["loudness_target_lufs"]["type"] == "float"
    assert data["spec"]["whisper_model"]["choices"]
    assert data["runtime"]["active_whisper_model"]


def test_settings_update_and_reset(restore_settings):
    res = client.post("/api/settings", json={"loudness_target_lufs": -18.0,
                                             "cache_limit_gb": 20})
    assert res.status_code == 200
    assert res.json()["settings"]["loudness_target_lufs"] == -18.0
    assert client.get("/api/settings").json()["settings"]["cache_limit_gb"] == 20.0

    res = client.delete("/api/settings")
    assert res.status_code == 200
    assert res.json()["settings"]["loudness_target_lufs"] == -14.0


def test_settings_update_ignores_junk_instead_of_failing(restore_settings):
    """手機端送了越界值或舊欄位，機台不能回 500 ——夾回範圍、忽略未知欄位就好。"""
    res = client.post("/api/settings", json={"default_mic_reverb": 9.9,
                                             "totally_unknown": "x"})
    assert res.status_code == 200
    data = res.json()["settings"]
    assert data["default_mic_reverb"] == 1.0
    assert "totally_unknown" not in data


def test_apply_defaults_pushes_settings_into_live_controls(restore_settings):
    saved = queue_manager.get_full_state()
    try:
        client.post("/api/settings", json={"default_mic_reverb": 0.75,
                                           "default_pitch_shift": 3})
        res = client.post("/api/settings/apply-defaults")
        assert res.status_code == 200
        state = client.get("/api/queue").json()
        assert state["mic_reverb"] == 0.75
        assert state["pitch_shift"] == 3
    finally:
        queue_manager._apply_controls(saved)


def test_loudness_endpoint_404_for_unknown_song():
    res = client.get("/api/songs/nonexistent_song/loudness")
    assert res.status_code == 404


def test_loudness_endpoint_uses_stored_measurement(restore_settings):
    """已量測過的歌不重算，而且增益要跟著目前的目標響度走。"""
    song_id = "test_loudness_song"
    song_dir = SONGS_DIR / song_id
    song_dir.mkdir(parents=True, exist_ok=True)
    (song_dir / "metadata.json").write_text(json.dumps({
        "id": song_id, "title": "響度測試",
        # 峰值留 12 dB 空間，這樣測到的是「目標響度算出來的增益」本身，
        # 而不是峰值保護的夾限（那條有自己的單元測試）
        "loudness": {"lufs": -20.0, "peak_dbfs": -12.0, "target_lufs": -14.0, "gain_db": 6.0},
    }), encoding="utf-8")
    try:
        data = client.get(f"/api/songs/{song_id}/loudness").json()
        assert data["measured"] is True
        assert data["gain_db"] == pytest.approx(6.0)

        # 目標改成 -18 之後，同一首歌的增益要重算成 +2 dB
        client.post("/api/settings", json={"loudness_target_lufs": -18.0})
        assert client.get(f"/api/songs/{song_id}/loudness").json()["gain_db"] == pytest.approx(2.0)

        # 關掉自動音量平衡就一律 0 dB
        client.post("/api/settings", json={"loudness_normalize": False})
        data = client.get(f"/api/songs/{song_id}/loudness").json()
        assert data["enabled"] is False
        assert data["gain_db"] == 0.0
    finally:
        storage.delete_song(song_id)


def test_loudness_endpoint_reports_unmeasurable_song_as_zero_gain():
    """沒有音檔可量的歌回 0 dB，播放端就照原音量播，不會爆音也不會忽然變小聲。"""
    song_id = "test_loudness_nofile"
    song_dir = SONGS_DIR / song_id
    song_dir.mkdir(parents=True, exist_ok=True)
    (song_dir / "metadata.json").write_text(
        json.dumps({"id": song_id, "title": "沒有音檔"}), encoding="utf-8")
    try:
        data = client.get(f"/api/songs/{song_id}/loudness").json()
        assert data["measured"] is False
        assert data["gain_db"] == 0.0
    finally:
        storage.delete_song(song_id)


# --- 練唱模式：曲式分析與跳轉 ---

@pytest.fixture()
def fake_song_with_lyrics():
    """放一首帶「主歌-副歌-主歌-副歌」歌詞的假歌，測完清掉。"""
    song_id = "test_sections_song"
    song_dir = SONGS_DIR / song_id
    song_dir.mkdir(parents=True, exist_ok=True)
    (song_dir / "metadata.json").write_text(
        json.dumps({"id": song_id, "title": "段落測試歌", "artist": "測試", "duration": 200}),
        encoding="utf-8")

    def line(start, text):
        return {"line_idx": 0, "start": start, "end": start + 3.0, "text": text, "words": []}

    verse1 = ["第一段第一句", "第一段第二句", "第一段第三句", "第一段第四句"]
    chorus = ["副歌第一句", "副歌第二句", "副歌第三句", "副歌第四句"]
    verse2 = ["第二段第一句", "第二段第二句", "第二段第三句", "第二段第四句"]

    lyrics, t = [], 12.0
    for block in (verse1, chorus, verse2, chorus):
        for text in block:
            lyrics.append(line(round(t, 3), text))
            t += 3.5
        t += 9.0  # 段落之間的間奏
    (song_dir / "lyrics.json").write_text(
        json.dumps(lyrics, ensure_ascii=False), encoding="utf-8")
    yield song_id
    storage.delete_song(song_id)


def test_sections_endpoint_finds_chorus_and_blocks(fake_song_with_lyrics):
    res = client.get(f"/api/songs/{fake_song_with_lyrics}/sections")
    assert res.status_code == 200
    data = res.json()
    assert data["song_id"] == fake_song_with_lyrics
    assert data["duration"] == 200

    chorus = data["chorus"]
    assert chorus is not None
    assert chorus["lines"] == 4
    assert chorus["repeats"] == 2
    assert chorus["end"] > chorus["start"]

    kinds = [s["kind"] for s in data["sections"]]
    assert kinds[0] == "intro"          # 第一句在 12 秒
    assert kinds.count("chorus") == 2
    assert kinds.count("verse") == 2
    assert "outro" in kinds             # metadata 有 duration 才標得出尾奏


def test_sections_endpoint_survives_song_without_lyrics():
    """還在處理中、或根本沒抓到歌詞的歌不能讓端點爆掉 —— 前端要退回手動設 A-B 點。"""
    res = client.get("/api/songs/no_such_song_at_all/sections")
    assert res.status_code == 200
    data = res.json()
    assert data["chorus"] is None
    assert data["sections"] == []


def test_seek_endpoint_clamps_position():
    assert client.post("/api/seek", json={"position": 42.5}).json()["position"] == 42.5
    assert client.post("/api/seek", json={"position": -3}).json()["position"] == 0.0
    # 少帶欄位也不能回 500，跳回開頭就好
    assert client.post("/api/seek", json={}).json()["position"] == 0.0


def test_control_endpoint_accepts_loop_range():
    try:
        res = client.post("/api/control", json={"loop_start": 30.0, "loop_end": 55.0,
                                                "loop_enabled": True})
        assert res.status_code == 200
        state = res.json()["state"]
        assert state["loop_start"] == 30.0
        assert state["loop_end"] == 55.0
        assert state["loop_enabled"] is True
    finally:
        queue_manager.clear_loop()


# --- 曲庫分類瀏覽 / 新歌榜 / 推薦歌單 ---

@pytest.fixture()
def fake_library_song():
    """在本機快取目錄放一首檔案齊全的假歌（會被曲庫索引到），測完清掉。"""
    song_id = "test_library_song_01"
    song_dir = SONGS_DIR / song_id
    song_dir.mkdir(parents=True, exist_ok=True)
    (song_dir / "metadata.json").write_text(
        json.dumps({"id": song_id, "title": "曲庫測試歌", "artist": "測試歌手 - Topic",
                    "duration": 210}, ensure_ascii=False), encoding="utf-8")
    (song_dir / "lyrics.json").write_text(
        json.dumps([{"start": 0, "end": 3, "text": "這是一首國語的測試歌曲歌詞"}],
                   ensure_ascii=False), encoding="utf-8")
    (song_dir / "instrumental.mp3").write_bytes(b"x" * 10)
    (song_dir / "vocals.mp3").write_bytes(b"x" * 10)
    yield song_id
    storage.delete_song(song_id)


def test_library_facets_shape(fake_library_song):
    res = client.get("/api/library")
    assert res.status_code == 200
    data = res.json()
    assert [lang["key"] for lang in data["language_spec"]] == \
        [lang["key"] for lang in data["languages"]]
    assert data["total"] >= 1
    mandarin = next(lang for lang in data["languages"] if lang["key"] == "mandarin")
    assert mandarin["count"] >= 1
    assert any(a["name"] == "測試歌手" for a in data["artists"])


def test_library_songs_filter_by_language_and_artist(fake_library_song):
    res = client.get("/api/library/songs", params={"language": "mandarin"})
    assert res.status_code == 200
    assert any(s["song_id"] == fake_library_song for s in res.json()["songs"])

    res = client.get("/api/library/songs", params={"artist": "測試歌手"})
    song = next(s for s in res.json()["songs"] if s["song_id"] == fake_library_song)
    assert song["language_label"] == "國語"
    assert song["is_cached"] is True

    # 篩到沒有歌的分類要回空清單，不是 500
    res = client.get("/api/library/songs", params={"language": "korean",
                                                   "artist": "不存在的歌手"})
    assert res.status_code == 200
    assert res.json()["songs"] == []


def test_library_songs_rejects_bad_sort():
    assert client.get("/api/library/songs", params={"sort": "隨便排"}).status_code == 422


def test_library_new_and_recommend(fake_library_song):
    res = client.get("/api/library/new", params={"limit": 5})
    assert res.status_code == 200
    data = res.json()
    assert data["new_days"] > 0
    song = next(s for s in data["songs"] if s["song_id"] == fake_library_song)
    assert song["is_new"] is True

    res = client.get("/api/library/recommend", params={"limit": 5})
    assert res.status_code == 200
    recs = res.json()["songs"]
    assert len(recs) <= 5
    assert all(r["reason"] and r["reason_tag"] for r in recs)
