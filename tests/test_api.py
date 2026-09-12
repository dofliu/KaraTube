"""FastAPI 端點整合測試。

只測不需要網路、不需要 AI 模型的端點。
重的模型都是延遲載入，所以整個 app 可以直接 import。
"""
import json

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.config import SONGS_DIR
from backend.version import __version__
from backend.main import (
    app,
    batch_scheduler,
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
    saved_singer_bests = {k: dict(v) for k, v in score_history._singer_bests.items()}
    yield
    song_history._entries = saved_history
    song_history._save()
    score_history._entries = saved_scores
    score_history._bests = saved_bests
    score_history._singer_bests = saved_singer_bests
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
    assert data["version"] == __version__


def test_version_endpoint():
    res = client.get("/api/version")
    assert res.status_code == 200
    data = res.json()
    assert data["name"] == "KaraTube"
    assert data["version"] == __version__


def test_health_endpoint():
    """容器健康檢查打的就是這支，形狀不能變。"""
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_public_base_url_prefers_configured_host(monkeypatch):
    """
    容器與反向代理情境：自動偵測到的 bridge IP 手機連不進去，
    所以設了 KARATUBE_PUBLIC_HOST/PORT 就要以它們為準；80/443 不寫進網址。
    """
    monkeypatch.setattr(main, "PUBLIC_HOST", "karatube.local")
    monkeypatch.setattr(main, "PUBLIC_PORT", 80)
    assert main.public_base_url() == "http://karatube.local"

    monkeypatch.setattr(main, "PUBLIC_PORT", 443)
    assert main.public_base_url() == "https://karatube.local"

    monkeypatch.setattr(main, "PUBLIC_PORT", 9000)
    assert main.public_base_url() == "http://karatube.local:9000"


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


def test_scores_carry_section_verdict(snapshot_history_and_scores):
    """段落評分的結論要跟著成績一起入庫，回頭看歷史才知道哪一段老是唱壞。"""
    res = client.post("/api/scores", json={
        "song_id": "test_score_sections", "title": "段落評分", "score": 999,
        "best_section": "副歌 1", "worst_section": "主歌 2"})
    assert res.status_code == 200
    result = res.json()["result"]
    assert result["best_section"] == "副歌 1"
    assert result["worst_section"] == "主歌 2"

    best = client.get("/api/scores/test_score_sections/best").json()["best"]
    assert best["best_section"] == "副歌 1"


def test_duet_scores_flow(snapshot_history_and_scores):
    """對唱結算：兩位一起送、一起記，回傳的勝負與各自的個人最佳都要在。"""
    res = client.post("/api/scores/duet", json={
        "song_id": "test_duet_001", "title": "對唱測試",
        "a": {"singer": "小明", "score": 5000, "accuracy": 0.6,
              "max_combo": 40, "grade": "SS"},
        "b": {"singer": "小美", "score": 3000, "accuracy": 0.4,
              "max_combo": 12, "grade": "A"},
    })
    assert res.status_code == 200
    result = res.json()["result"]
    assert result["winner"] == "a"
    assert result["margin"] == 2000
    assert result["duet"] is True
    assert result["a"]["singer"] == "小明"
    assert result["b"]["is_new_best"] is True

    data = client.get("/api/scores").json()
    sung = [e for e in data["scores"] if e["song_id"] == "test_duet_001"]
    assert len(sung) == 2
    assert {e["singer"] for e in sung} == {"小明", "小美"}
    # 個人最佳（某人在某首歌）另外攤平成清單回傳
    assert any(b["singer"] == "小美" and b["score"] == 3000 for b in data["singer_bests"])


def test_duet_scores_reject_incomplete_payload(snapshot_history_and_scores):
    """缺一邊就整筆不收 —— 半場的對唱紀錄之後永遠說不清是誰的問題。"""
    res = client.post("/api/scores/duet", json={
        "song_id": "test_duet_bad", "a": {"singer": "小明", "score": 100}})
    assert res.status_code == 400
    res = client.post("/api/scores/duet", json={
        "a": {"score": 100}, "b": {"score": 50}})
    assert res.status_code == 400
    assert not any(e["song_id"] == "test_duet_bad"
                   for e in client.get("/api/scores").json()["scores"])


def test_duet_control_params_round_trip():
    """對唱開關與暱稱是共享控制參數，/api/control 要收得下也回得出來。"""
    res = client.post("/api/control", json={
        "duet_enabled": True, "duet_name_a": "小明", "duet_name_b": "小美"})
    assert res.status_code == 200
    state = res.json()["state"]
    assert state["duet_enabled"] is True
    assert state["duet_name_a"] == "小明"
    # 測完關掉，不要讓後面的測試（與本機）留在對唱模式
    client.post("/api/control", json={
        "duet_enabled": False, "duet_name_a": "", "duet_name_b": ""})


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


# --- 排程預處理 ---

@pytest.fixture()
def clean_batch_jobs():
    """排程任務是全域狀態且會寫進 cache，測完要還原。"""
    saved_jobs = [dict(j) for j in batch_scheduler.jobs]
    saved_force = batch_scheduler.force_run
    batch_scheduler.jobs = []
    batch_scheduler.force_run = False
    yield
    batch_scheduler.jobs = saved_jobs
    batch_scheduler.force_run = saved_force
    batch_scheduler._save()


@pytest.fixture()
def fake_expand(monkeypatch):
    """展開來源要連 YouTube，測試裡換成固定回覆。"""
    def _expand(lines, limit=200):
        return {
            "songs": [{"song_id": f"test_batch_{i}", "title": f"排程測試歌 {i}",
                       "artist": "測試", "thumbnail": "",
                       "url": f"https://www.youtube.com/watch?v=test_batch_{i}"}
                      for i, _ in enumerate(lines)],
            "failed": [],
        }
    monkeypatch.setattr(main.search_service, "expand_sources", _expand)


def test_batch_state_shape(clean_batch_jobs):
    res = client.get("/api/batch")
    assert res.status_code == 200
    data = res.json()
    for key in ("enabled", "window", "force_run", "can_run", "reason", "jobs", "pending_total"):
        assert key in data
    assert "start_hour" in data["window"] and "minutes_until" in data["window"]


def test_batch_create_and_lifecycle(clean_batch_jobs, fake_expand):
    res = client.post("/api/batch", json={"sources": "第一首\n第二首", "name": "週末歌單",
                                          "start_now": True})
    assert res.status_code == 200
    job = res.json()["job"]
    assert job["name"] == "週末歌單"
    assert len(job["items"]) == 2
    assert res.json()["state"]["force_run"] is True
    assert res.json()["state"]["pending_total"] == 2

    # 取消之後就不再有待處理的歌
    res = client.post(f"/api/batch/{job['job_id']}/cancel")
    assert res.status_code == 200
    assert client.get("/api/batch").json()["pending_total"] == 0

    res = client.delete(f"/api/batch/{job['job_id']}")
    assert res.status_code == 200
    assert client.get("/api/batch").json()["jobs"] == []


def test_batch_create_requires_sources(clean_batch_jobs):
    assert client.post("/api/batch", json={"sources": "   "}).status_code == 400


def test_batch_create_reports_when_nothing_found(clean_batch_jobs, monkeypatch):
    monkeypatch.setattr(main.search_service, "expand_sources",
                        lambda lines, limit=200: {"songs": [], "failed": list(lines)})
    res = client.post("/api/batch", json={"sources": "根本不存在的歌"})
    assert res.status_code == 404


def test_batch_force_toggle_and_unknown_job_404(clean_batch_jobs, fake_expand):
    client.post("/api/batch", json={"sources": "第一首"})
    assert client.post("/api/batch/force", json={"force": True}).json()["force_run"] is True
    assert client.post("/api/batch/force", json={"force": False}).json()["force_run"] is False

    assert client.post("/api/batch/no-such-job/retry").status_code == 404
    assert client.post("/api/batch/no-such-job/cancel").status_code == 404
    assert client.delete("/api/batch/no-such-job").status_code == 404


def test_batch_settings_drive_the_window(clean_batch_jobs, restore_settings):
    client.post("/api/settings", json={"batch_start_hour": 23, "batch_end_hour": 5,
                                       "batch_enabled": False})
    window = client.get("/api/batch").json()
    assert window["window"]["start_hour"] == 23
    assert window["window"]["end_hour"] == 5
    assert window["enabled"] is False


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


def test_song_trend_endpoint(snapshot_history_and_scores):
    """跨場次段落趨勢：唱三次之後端點才下結論，之前回報「還差幾場」。"""
    sections = [{"label": "主歌 1", "accuracy": 0.8, "note_frames": 200},
                {"label": "副歌 1", "accuracy": 0.4, "note_frames": 200}]

    res = client.get("/api/scores/test_trend_001/trend")
    assert res.status_code == 200
    # 從沒唱過也是 200：「還沒有資料」是畫面，404 不是
    assert res.json()["trend"]["status"] == "none"

    for _ in range(2):
        client.post("/api/scores", json={"song_id": "test_trend_001",
                                         "title": "趨勢測試", "score": 800,
                                         "sections": sections})
    trend = client.get("/api/scores/test_trend_001/trend").json()["trend"]
    assert trend["status"] == "insufficient"
    assert trend["needed"] == 1

    res = client.post("/api/scores", json={"song_id": "test_trend_001",
                                           "title": "趨勢測試", "score": 800,
                                           "sections": sections})
    # 結算回應本身就帶著趨勢，舞台端不必再打一次 API
    assert res.json()["result"]["trend"]["weak"]["label"] == "副歌 1"

    trend = client.get("/api/scores/test_trend_001/trend").json()["trend"]
    assert trend["status"] == "ok"
    assert trend["performances"] == 3
    assert trend["home"]["label"] == "主歌 1"
    assert [r["label"] for r in trend["sections"]] == ["主歌 1", "副歌 1"]


def test_trends_table_endpoint(snapshot_history_and_scores):
    sections = [{"label": "主歌 1", "accuracy": 0.75, "note_frames": 200},
                {"label": "副歌 1", "accuracy": 0.45, "note_frames": 200}]
    for _ in range(3):
        client.post("/api/scores", json={"song_id": "test_trend_table",
                                         "title": "總表測試", "score": 700,
                                         "singer": "小明", "sections": sections})
    res = client.get("/api/scores/trends?limit=5")
    assert res.status_code == 200
    rows = [t for t in res.json()["trends"] if t["song_id"] == "test_trend_table"]
    assert len(rows) == 1
    assert rows[0]["singer"] == "小明"
    assert rows[0]["weak"]["label"] == "副歌 1"

    # `trends` 不可以被當成 song_id 吃掉（路由順序的老問題）
    assert client.get("/api/scores/trends").json()["trends"] is not None


def test_trend_singer_query_separates_people(snapshot_history_and_scores):
    for singer, shape in (("甲", (0.8, 0.4)), ("乙", (0.4, 0.8))):
        for _ in range(3):
            client.post("/api/scores", json={
                "song_id": "test_trend_singers", "score": 600, "singer": singer,
                "sections": [{"label": "主歌 1", "accuracy": shape[0], "note_frames": 200},
                             {"label": "副歌 1", "accuracy": shape[1], "note_frames": 200}]})

    a = client.get("/api/scores/test_trend_singers/trend?singer=甲").json()["trend"]
    b = client.get("/api/scores/test_trend_singers/trend?singer=乙").json()["trend"]
    assert a["weak"]["label"] == "副歌 1"
    assert b["weak"]["label"] == "主歌 1"
