"""FastAPI 端點整合測試。

只測不需要網路、不需要 AI 模型的端點。
重的模型都是延遲載入，所以整個 app 可以直接 import。
"""
import json
import os
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.config import SONGS_DIR
from backend.services import marquee, song_quota
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


# --- 公平輪唱（排麥輪序）---

@pytest.fixture()
def rotation_off():
    """測完把輪唱關回去並清掉輪序統計，不影響其他測試與本機狀態。"""
    yield
    queue_manager.rotation_enabled = False
    queue_manager.rotation.reset()
    queue_manager.queue.clear()


def test_rotation_toggle_is_a_shared_control(rotation_off):
    """開關走 /api/control，所以包廂裡每一台裝置都會收到同一份規則。"""
    res = client.post("/api/control", json={"rotation_enabled": True})
    assert res.status_code == 200
    assert res.json()["state"]["rotation_enabled"] is True
    assert client.get("/api/queue").json()["rotation_enabled"] is True


def test_rotation_endpoint_reports_rounds(rotation_off):
    queue_manager.rotation_enabled = True
    queue_manager.queue.extend([
        {"queue_id": "r1", "song_id": "s1", "requested_by": "小明"},
        {"queue_id": "r2", "song_id": "s2", "requested_by": "小明"},
        {"queue_id": "r3", "song_id": "s3", "requested_by": "小美"},
    ])
    body = client.get("/api/rotation").json()
    assert body["enabled"] is True
    assert body["rounds"] == {"r1": 1, "r2": 2, "r3": 1}
    assert [s["name"] for s in body["singers"]] == ["小明", "小美"]
    assert body["named_count"] == 2


def test_rotation_reset_clears_counts_only(rotation_off):
    queue_manager.rotation.record_play({"requested_by": "小明"})
    queue_manager.queue.append({"queue_id": "r1", "song_id": "s1", "requested_by": "小美"})
    body = client.post("/api/rotation/reset").json()
    assert body["status"] == "success"
    assert queue_manager.rotation.counts() == {}
    # 佇列不動：已經排好的順序是大家看著排出來的
    assert [i["queue_id"] for i in queue_manager.queue] == ["r1"]
    assert body["rounds"] == {"r1": 1}


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


# --- 曲庫查歌（注音首字／字數）---

def test_find_keys_shape(fake_library_song):
    res = client.get("/api/library/find/keys")
    assert res.status_code == 200
    data = res.json()
    assert len([k for row in data["rows"] for k in row]) == 37
    assert data["total"] >= 1
    assert isinstance(data["bopomofo_available"], bool)
    # 「曲庫測試歌」是 5 個字，字數桶裡要看得到
    assert any(b["chars"] == 5 and b["count"] >= 1 for b in data["char_buckets"])


def test_find_by_text_and_chars(fake_library_song):
    res = client.get("/api/library/find", params={"q": "曲庫測試歌"})
    assert res.status_code == 200
    data = res.json()
    assert any(s["song_id"] == fake_library_song for s in data["songs"])
    assert data["query"] == "曲庫測試歌"

    res = client.get("/api/library/find", params={"chars": 5})
    assert any(s["song_id"] == fake_library_song for s in res.json()["songs"])

    # 查不到就是空清單，不是 500
    res = client.get("/api/library/find", params={"q": "這首歌不在曲庫裡"})
    assert res.status_code == 200
    assert res.json()["songs"] == []


def test_find_rejects_out_of_range_params():
    assert client.get("/api/library/find", params={"chars": -1}).status_code == 422
    assert client.get("/api/library/find", params={"limit": 999}).status_code == 422


# --- 歌星查歌 ---

def test_artist_find_keys_shape(fake_library_song):
    res = client.get("/api/library/artists/keys")
    assert res.status_code == 200
    data = res.json()
    assert len([k for row in data["rows"] for k in row]) == 37
    assert data["artist_count"] >= 1
    assert isinstance(data["bopomofo_available"], bool)


def test_artist_find_by_name_returns_his_songs(fake_library_song):
    res = client.get("/api/library/artists/find", params={"q": "測試歌手"})
    assert res.status_code == 200
    data = res.json()
    assert any(a["name"] == "測試歌手" for a in data["artists"])
    assert any(s["song_id"] == fake_library_song for s in data["songs"])

    # 指定歌手就只回他的歌
    res = client.get("/api/library/artists/find", params={"artist": "測試歌手"})
    data = res.json()
    assert data["selected"]["name"] == "測試歌手"
    assert all(s["artist_name"] == "測試歌手" for s in data["songs"])
    # 歌單另外回傳，歌手清單裡就不用再夾一份
    assert "songs" not in data["artists"][0]

    # 查不到就是空清單，不是 500
    res = client.get("/api/library/artists/find", params={"q": "這位歌手不在曲庫裡"})
    assert res.status_code == 200
    assert res.json()["artists"] == []


def test_artist_find_rejects_out_of_range_params():
    assert client.get("/api/library/artists/find",
                      params={"limit": 999}).status_code == 422


# --- 歌號點歌（六位數）---

@pytest.fixture()
def clean_song_numbers():
    """歌號簿是全域狀態且會寫進本機 cache，測完要原封不動還回去。

    這支 fixture 比其他的嚴格一點：號碼簿被測試污染的後果不是多一筆垃圾，
    而是本機曲庫的歌號會整批位移 —— 而那正是這個功能承諾不會發生的事。
    """
    book = main.song_numbers
    saved_records = {k: dict(v) for k, v in book._records.items()}
    saved_by_number = dict(book._by_number)
    saved_next = book._next
    yield book
    book._records = saved_records
    book._by_number = saved_by_number
    book._next = saved_next
    book._save()


def test_number_keypad_lists_only_ready_songs(fake_library_song, clean_song_numbers):
    res = client.get("/api/library/numbers")
    assert res.status_code == 200
    data = res.json()
    song = next(s for s in data["songs"] if s["song_id"] == fake_library_song)
    assert song["number"] >= 100001
    assert data["book"]["available"] is True
    assert data["library_total"] >= 1
    # 每個候選都是曲庫裡唱得到的歌（列一首點不下去的歌等於騙人）
    assert all(s["is_cached"] for s in data["songs"])


def test_number_keypad_prefix_and_next_digits(fake_library_song, clean_song_numbers):
    number = str(client.get("/api/library/numbers").json()["songs"][0]["number"])
    data = client.get("/api/library/numbers", params={"prefix": number[:3]}).json()
    assert data["prefix"] == number[:3]
    assert data["total"] >= 1
    # 還沒打完就要講得出「下一鍵按哪些還有歌」
    assert number[3] in data["next_digits"]
    # 打滿之後就沒有下一鍵了
    assert client.get("/api/library/numbers",
                      params={"prefix": number}).json()["next_digits"] == []


def test_number_lookup_returns_the_song(fake_library_song, clean_song_numbers):
    number = clean_song_numbers.number_of(fake_library_song)
    assert number is not None
    data = client.get(f"/api/library/number/{number}").json()
    assert data["status"] == "ready"
    assert data["song"]["song_id"] == fake_library_song
    assert data["number"] == number


def test_number_lookup_tells_gone_from_never_issued(fake_library_song,
                                                    clean_song_numbers):
    """已下架與打錯號碼是兩件事：前者該講得出原本是哪一首。"""
    number = clean_song_numbers.number_of(fake_library_song)
    storage.delete_song(fake_library_song)
    data = client.get(f"/api/library/number/{number}").json()
    assert data["status"] == "gone"
    assert data["song"] is None
    assert data["record"]["title"] == "曲庫測試歌"

    unknown = client.get("/api/library/number/999999").json()
    assert unknown["status"] == "unknown"
    assert unknown["song"] is None


def test_number_lookup_rejects_non_numbers(clean_song_numbers):
    data = client.get("/api/library/number/10023x").json()
    assert data["status"] == "invalid"
    # 五位數不是歌號（號碼從 100001 起跳）
    assert client.get("/api/library/number/12345").json()["status"] == "invalid"


def test_songbook_lists_retired_numbers_too(fake_library_song, clean_song_numbers):
    number = clean_song_numbers.number_of(fake_library_song)
    storage.delete_song(fake_library_song)
    rows = client.get("/api/library/songbook").json()["songs"]
    row = next(r for r in rows if r["number"] == number)
    assert row["in_library"] is False
    assert [r["number"] for r in rows] == sorted(r["number"] for r in rows)


def test_queued_cached_song_carries_its_number(fake_library_song, clean_song_numbers):
    """佇列上要印得出歌號 —— 包廂裡沒有人記得住一個沒被印出來的號碼。"""
    number = client.get("/api/library/numbers").json()["songs"][0]["number"]
    try:
        res = client.post("/api/queue/add", json={"id": fake_library_song,
                                                  "title": "曲庫測試歌",
                                                  "artist": "測試歌手"})
        assert res.status_code == 200
        # 快取秒播的歌會直接上台（佇列裡不會留），所以看回傳的那一筆
        item = res.json()["item"]
        assert item["song_id"] == fake_library_song
        assert item["number"] == number
    finally:
        queue_manager.queue = [i for i in queue_manager.queue
                               if i["song_id"] != fake_library_song]
        queue_manager.current_song = None
        queue_manager.is_playing = False


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


# --- 錄唱回放 ---

@pytest.fixture()
def clean_recordings(tmp_path, monkeypatch):
    """
    錄音庫換到 tmp_path，測試不會碰到本機真正的錄音。

    `main.recordings` 是模組層級的單例，端點直接引用它，所以要換掉的是
    那個名字本身（換 base_dir 不夠：索引已經在建構時讀進記憶體了）。
    """
    from backend.services.recordings import RecordingLibrary
    monkeypatch.setattr(main, "recordings", RecordingLibrary(tmp_path / "recordings"))
    yield main.recordings


def upload(song_id="test_rec_song", body=b"fake-audio-bytes", **params):
    query = {"song_id": song_id, "title": "錄音測試", "singer": "阿明",
             "duration_ms": 123_000, "score": 90_000, "grade": "S", "accuracy": 0.8}
    query.update(params)
    return client.post("/api/recordings", params=query, content=body,
                       headers={"content-type": "audio/webm;codecs=opus"})


def test_recording_upload_list_play_delete(clean_recordings):
    res = upload()
    assert res.status_code == 200
    rec = res.json()["recording"]
    assert rec["song_id"] == "test_rec_song"
    assert rec["mime"] == "audio/webm"       # codecs 參數要被剝掉才查得到副檔名
    assert res.json()["stats"]["count"] == 1

    listed = client.get("/api/recordings").json()
    assert [e["id"] for e in listed["recordings"]] == [rec["id"]]

    audio = client.get(f"/api/recordings/{rec['id']}/audio")
    assert audio.status_code == 200
    assert audio.content == b"fake-audio-bytes"
    assert "attachment" not in audio.headers.get("content-disposition", "")

    download = client.get(f"/api/recordings/{rec['id']}/audio?download=1")
    assert "attachment" in download.headers["content-disposition"]
    assert ".webm" in download.headers["content-disposition"]

    assert client.delete(f"/api/recordings/{rec['id']}").status_code == 200
    assert client.get("/api/recordings").json()["recordings"] == []


def test_recording_upload_requires_song_id(clean_recordings):
    assert client.post("/api/recordings", content=b"x").status_code == 422


def test_recording_rejects_empty_body(clean_recordings):
    assert upload(body=b"").status_code == 400


def test_recording_audio_unknown_id_is_404_not_a_path_escape(clean_recordings):
    upload()
    assert client.get("/api/recordings/nope/audio").status_code == 404
    # 路徑穿越要在 id 檢查那一關就死掉，不能讀到別的檔案
    escaped = client.get("/api/recordings/..%2F..%2Fetc%2Fpasswd/audio")
    assert escaped.status_code == 404
    assert client.delete("/api/recordings/nope").status_code == 404
    assert client.post("/api/recordings/nope/pin", json={}).status_code == 404


def test_recording_pin_toggles_and_survives_clear(clean_recordings):
    keep = upload(song_id="test_rec_keep").json()["recording"]
    upload(song_id="test_rec_drop")

    pinned = client.post(f"/api/recordings/{keep['id']}/pin", json={}).json()["recording"]
    assert pinned["pinned"] is True

    cleared = client.delete("/api/recordings").json()
    assert cleared["removed"] == 1
    assert [e["song_id"] for e in client.get("/api/recordings").json()["recordings"]] \
        == ["test_rec_keep"]

    # 要連保留的一起刪，得明確送 include_pinned=1
    assert client.delete("/api/recordings?include_pinned=1").json()["removed"] == 1
    assert client.get("/api/recordings").json()["recordings"] == []


def test_recording_quota_from_settings_evicts_oldest(clean_recordings, restore_settings):
    client.post("/api/settings", json={"recording_max_count": 2})
    for i in range(3):
        upload(song_id=f"test_rec_{i}")
    listed = client.get("/api/recordings").json()
    assert [e["song_id"] for e in listed["recordings"]] == ["test_rec_2", "test_rec_1"]
    assert listed["stats"]["max_count"] == 2
    assert listed["stats"]["remaining_count"] == 0


def test_recording_list_reports_whether_the_feature_is_on(clean_recordings, restore_settings):
    client.post("/api/settings", json={"recording_enabled": True})
    assert client.get("/api/recordings").json()["enabled"] is True
    client.post("/api/settings", json={"recording_enabled": False})
    assert client.get("/api/recordings").json()["enabled"] is False


# --- 錄音轉 MP3 ---
#
# 這一段測的是端點的行為，不是 ffmpeg 的行為：`fake_ffmpeg` 把
# KARATUBE_FFMPEG 指到一支假的 ffmpeg，所以有沒有真的裝 ffmpeg 都跑得過。

@pytest.fixture()
def fake_ffmpeg(tmp_path, monkeypatch):
    from backend.services.transcoder import reset_probe_cache
    from tests.fake_ffmpeg import write_fake_ffmpeg
    binary = write_fake_ffmpeg(tmp_path / "ffmpeg")
    monkeypatch.setenv("KARATUBE_FFMPEG", str(binary))
    reset_probe_cache()     # 探測結果是模組層級的快取
    yield binary
    reset_probe_cache()


@pytest.fixture()
def no_ffmpeg(tmp_path, monkeypatch):
    from backend.services.transcoder import reset_probe_cache
    monkeypatch.setenv("KARATUBE_FFMPEG", str(tmp_path / "definitely-not-ffmpeg"))
    reset_probe_cache()
    yield
    reset_probe_cache()


@pytest.mark.skipif(os.name == "nt", reason="假 ffmpeg 用的是 POSIX shell script")
def test_recording_mp3_download_transcodes_once_then_serves_the_cache(
        clean_recordings, restore_settings, fake_ffmpeg):
    rec = upload().json()["recording"]

    res = client.get(f"/api/recordings/{rec['id']}/audio?download=1&format=mp3")
    assert res.status_code == 200
    assert res.headers["content-type"] == "audio/mpeg"
    # 車機看的是副檔名，webm 的檔名配 mp3 的內容一樣打不開
    assert ".mp3" in res.headers["content-disposition"]
    assert res.content.startswith(b"ID3")

    listed = client.get("/api/recordings").json()
    assert listed["stats"]["mp3_count"] == 1
    # MP3 是額外的快取，不該讓錄音配額的用量憑空長大
    assert listed["stats"]["total_bytes"] == len(b"fake-audio-bytes")

    # 第二次直接吃快取：假 ffmpeg 換成一支只會失敗的，回應仍然要正常
    from tests.fake_ffmpeg import write_fake_ffmpeg
    write_fake_ffmpeg(fake_ffmpeg, "exit 1")
    again = client.get(f"/api/recordings/{rec['id']}/audio?format=mp3")
    assert again.status_code == 200
    assert again.content.startswith(b"ID3")


@pytest.mark.skipif(os.name == "nt", reason="假 ffmpeg 用的是 POSIX shell script")
def test_recording_mp3_cache_is_dropped_with_the_recording(
        clean_recordings, restore_settings, fake_ffmpeg):
    rec = upload().json()["recording"]
    client.get(f"/api/recordings/{rec['id']}/audio?format=mp3")
    mp3_path = main.recordings.mp3_path_for(rec["id"])
    assert mp3_path is not None

    client.delete(f"/api/recordings/{rec['id']}")
    assert not mp3_path.exists()


@pytest.mark.skipif(os.name == "nt", reason="假 ffmpeg 用的是 POSIX shell script")
def test_clearing_mp3_cache_keeps_the_recordings(
        clean_recordings, restore_settings, fake_ffmpeg):
    rec = upload().json()["recording"]
    client.get(f"/api/recordings/{rec['id']}/audio?format=mp3")

    cleared = client.delete("/api/recordings/mp3").json()
    assert cleared["removed"] == 1
    assert cleared["stats"]["mp3_count"] == 0
    # 錄音一個都沒少（清的是可以重轉的東西）
    assert len(client.get("/api/recordings").json()["recordings"]) == 1
    assert client.get(f"/api/recordings/{rec['id']}/audio").status_code == 200


def test_recording_mp3_says_why_when_ffmpeg_is_missing(
        clean_recordings, restore_settings, no_ffmpeg):
    rec = upload().json()["recording"]
    listed = client.get("/api/recordings").json()
    assert listed["mp3"]["available"] is False
    assert listed["mp3"]["reason"] == "not_installed"

    res = client.get(f"/api/recordings/{rec['id']}/audio?format=mp3")
    # 503 而不是 500：裝好 ffmpeg 之後同一個網址就會成功，原始錄音一直都在
    assert res.status_code == 503
    assert "ffmpeg" in res.json()["detail"]
    # 原始檔案照樣拿得到 —— 轉不了 MP3 不該讓那一次演唱也跟著拿不到
    assert client.get(f"/api/recordings/{rec['id']}/audio").status_code == 200


@pytest.mark.skipif(os.name == "nt", reason="假 ffmpeg 用的是 POSIX shell script")
def test_recording_mp3_can_be_switched_off_in_settings(
        clean_recordings, restore_settings, fake_ffmpeg):
    rec = upload().json()["recording"]
    client.post("/api/settings", json={"recording_mp3_enabled": False})

    listed = client.get("/api/recordings").json()
    assert listed["mp3"]["available"] is False
    # 「設定關掉」與「機器沒有 ffmpeg」要分得出來：使用者要做的事不一樣
    assert listed["mp3"]["reason"] == "disabled"
    assert client.get(f"/api/recordings/{rec['id']}/audio?format=mp3").status_code == 503


def test_mp3_recheck_endpoint_reprobes(clean_recordings, restore_settings, no_ffmpeg):
    assert client.get("/api/recordings").json()["mp3"]["available"] is False
    res = client.post("/api/recordings/mp3/recheck")
    assert res.status_code == 200
    assert res.json()["mp3"]["reason"] == "not_installed"


# --- 整晚打包下載 ---
#
# 「今天晚上的通通給我一份」是收場時的那句話。一首一首按下載是二十三次
# 另存新檔，而且存出來散在資料夾裡分不出誰是誰、哪一首在前面。

def zip_from(res):
    import io
    import zipfile
    return zipfile.ZipFile(io.BytesIO(res.content))


def test_session_list_groups_tonight_into_one_session(clean_recordings):
    for i in range(3):
        upload(song_id=f"test_sess_{i}", singer="阿明" if i < 2 else "小美")
    res = client.get("/api/recordings/sessions").json()
    assert len(res["sessions"]) == 1

    session = res["sessions"][0]
    assert session["count"] == 3
    assert session["songs"] == 3
    assert session["singers"] == ["小美", "阿明"] or session["singers"] == ["阿明", "小美"]
    assert session["bytes"] == 3 * len(b"fake-audio-bytes")
    # 網址由伺服器組好，前端不自己拼 key
    assert session["zip_url"].endswith("/zip")
    # 清單裡不該外流內部的 entries（錄音檔名、路徑）
    assert "entries" not in session
    assert res["busy"] is False


def test_session_zip_downloads_every_take_with_a_manifest(clean_recordings):
    for i in range(2):
        upload(song_id=f"test_zip_{i}", title=f"歌{i}")
    key = client.get("/api/recordings/sessions").json()["sessions"][0]["key"]

    res = client.get(f"/api/recordings/sessions/{key}/zip")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"
    # 中文檔名走 RFC 5987，另外附一個 ASCII 退化版給舊瀏覽器
    disposition = res.headers["content-disposition"]
    assert "filename*=UTF-8''" in disposition and 'filename="' in disposition
    # 邊包邊送就算不出精確長度，宣告一個猜的長度會讓瀏覽器把整包當成下載失敗
    assert "content-length" not in {k.lower() for k in res.headers}

    zf = zip_from(res)
    assert zf.testzip() is None
    names = zf.namelist()
    assert len(names) == 3                      # 兩首 + 曲目清單
    assert names[-1] == "曲目.txt"
    assert names[0].startswith("01 ")           # 序號在前面才排得出今晚的順序
    assert zf.read(names[0]) == b"fake-audio-bytes"


def test_session_zip_can_pack_just_one_singers_takes(clean_recordings):
    upload(song_id="test_zip_a", singer="阿明")
    upload(song_id="test_zip_b", singer="小美")
    key = client.get("/api/recordings/sessions").json()["sessions"][0]["key"]

    res = client.get(f"/api/recordings/sessions/{key}/zip", params={"singer": "阿明"})
    assert res.status_code == 200
    zf = zip_from(res)
    assert len([n for n in zf.namelist() if n != "曲目.txt"]) == 1
    assert "只有 阿明 唱的" in zf.read("曲目.txt").decode("utf-8")

    # 那一場裡沒有這個人：404 而不是一個只有清單的空 zip
    empty = client.get(f"/api/recordings/sessions/{key}/zip", params={"singer": "沒這個人"})
    assert empty.status_code == 404


def test_session_zip_unknown_key_is_404(clean_recordings):
    upload()
    assert client.get("/api/recordings/sessions/20991231-2359/zip").status_code == 404
    # key 是拼進路徑的字串，穿越要在查表那一關就死掉
    assert client.get("/api/recordings/sessions/..%2F..%2Fetc/zip").status_code == 404


def test_only_one_night_can_be_packed_at_a_time(clean_recordings):
    """
    一包是幾百 MB，而那條網路正是舞台端串影片與 WebSocket 在走的。
    第二個人等一下就好（429：等一下再按同一個網址就會成功）。
    """
    upload()
    key = client.get("/api/recordings/sessions").json()["sessions"][0]["key"]
    token = main.night_gate.acquire()
    try:
        res = client.get(f"/api/recordings/sessions/{key}/zip")
        assert res.status_code == 429
        assert client.get("/api/recordings/sessions").json()["busy"] is True
    finally:
        main.night_gate.release(token)
    # 位子還回去之後同一個網址就會成功
    assert client.get(f"/api/recordings/sessions/{key}/zip").status_code == 200


def test_packing_releases_the_slot_for_the_next_one(clean_recordings):
    upload()
    key = client.get("/api/recordings/sessions").json()["sessions"][0]["key"]
    assert client.get(f"/api/recordings/sessions/{key}/zip").status_code == 200
    assert main.night_gate.busy is False
    assert client.get(f"/api/recordings/sessions/{key}/zip").status_code == 200


def test_session_gap_setting_changes_how_nights_are_split(clean_recordings, restore_settings):
    """設定頁調小空檔門檻，同一批錄音就會被切成更多場（用來驗設定真的有接上）。"""
    upload()
    client.post("/api/settings", json={"recording_session_gap_hours": 1})
    assert client.get("/api/recordings/sessions").json()["gap_hours"] == 1


def test_stage_options_carry_recording_switch(restore_settings):
    client.post("/api/settings", json={"recording_enabled": True,
                                       "recording_min_sing_seconds": 15})
    stage = settings.stage_options()
    assert stage["recording_enabled"] is True
    # 舞台端算的是毫秒，換算要在後端做完（兩邊各乘一次就差一個數量級）
    assert stage["recording_min_sing_ms"] == 15_000


# --- 錄音分享（一次性連結 / QR）---

@pytest.fixture()
def clean_shares(tmp_path, monkeypatch):
    """分享索引換到 tmp_path。跟錄音庫一樣是模組層級單例，要換掉名字本身。"""
    from backend.services.share_links import ShareLinkStore
    monkeypatch.setattr(main, "share_links", ShareLinkStore(tmp_path / "shares.json"))
    yield main.share_links


def share_of(rec_id, **body):
    return client.post(f"/api/recordings/{rec_id}/share", json=body)


def test_share_link_plays_and_downloads(clean_recordings, clean_shares):
    rec = upload().json()["recording"]

    share = share_of(rec["id"]).json()["share"]
    assert share["url"].endswith(f"/share/{share['token']}")
    assert share["expires_in_seconds"] > 0

    meta = client.get(f"/api/share/{share['token']}").json()
    assert meta["recording"]["title"] == "錄音測試"
    assert meta["recording"]["singer"] == "阿明"
    # 內部識別碼不從公開端點外流（分享頁不需要，外流只是多給一個把手）
    assert "song_id" not in meta["recording"]
    assert "recording_id" not in meta["share"]

    audio = client.get(f"/api/share/{share['token']}/audio")
    assert audio.status_code == 200 and audio.content == b"fake-audio-bytes"

    download = client.get(f"/api/share/{share['token']}/audio?download=1")
    assert "attachment" in download.headers["content-disposition"]

    qr = client.get(f"/api/share/{share['token']}/qr.png")
    assert qr.status_code == 200 and qr.headers["content-type"] == "image/png"


@pytest.mark.skipif(os.name == "nt", reason="假 ffmpeg 用的是 POSIX shell script")
def test_shared_link_offers_mp3_for_car_stereos(clean_recordings, clean_shares,
                                                restore_settings, fake_ffmpeg):
    rec = upload().json()["recording"]
    token = share_of(rec["id"]).json()["share"]["token"]

    meta = client.get(f"/api/share/{token}").json()
    assert meta["mp3_url"]

    res = client.get(meta["mp3_url"])
    assert res.status_code == 200
    assert res.headers["content-type"] == "audio/mpeg"
    assert ".mp3" in res.headers["content-disposition"]
    assert res.content.startswith(b"ID3")


def test_shared_link_hides_mp3_when_the_machine_cannot_transcode(
        clean_recordings, clean_shares, restore_settings, no_ffmpeg):
    rec = upload().json()["recording"]
    token = share_of(rec["id"]).json()["share"]["token"]
    # 拿到連結的人不是這台機器的管理員 —— 給他一顆按下去會壞的按鈕沒有意義
    assert client.get(f"/api/share/{token}").json()["mp3_url"] is None


def test_failed_mp3_does_not_burn_a_download_credit(clean_recordings, clean_shares,
                                                    restore_settings, no_ffmpeg):
    """
    「下載幾次就失效」的連結上，一次沒成功的轉檔不可以扣掉次數 ——
    那等於這個連結被一個沒有拿到檔案的動作燒掉了。
    """
    rec = upload().json()["recording"]
    token = share_of(rec["id"], max_downloads=1).json()["share"]["token"]

    assert client.get(f"/api/share/{token}/audio?download=1&format=mp3").status_code == 503
    assert client.get(f"/api/share/{token}").json()["share"]["downloads_left"] == 1
    # 次數還在，原始檔案照樣下載得到
    assert client.get(f"/api/share/{token}/audio?download=1").status_code == 200


def test_share_page_is_served_for_scanned_links(clean_recordings, clean_shares):
    rec = upload().json()["recording"]
    token = share_of(rec["id"]).json()["share"]["token"]
    page = client.get(f"/share/{token}")
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    # 靜態檔：歌名等等由頁面自己打 API 拿，不嵌進 HTML（那等於把別人取的標題當程式碼跑）
    assert "share-page.js" in page.text
    assert "錄音測試" not in page.text


def test_sharing_twice_keeps_the_same_qr(clean_recordings, clean_shares):
    rec = upload().json()["recording"]
    first = share_of(rec["id"]).json()["share"]["token"]
    assert share_of(rec["id"]).json()["share"]["token"] == first

    # 明確要新的才換（順便撤銷舊的：發錯人時舊連結必須立刻死掉）
    second = share_of(rec["id"], new=True).json()["share"]["token"]
    assert second != first
    assert client.get(f"/api/share/{first}").status_code == 410


def test_revoked_share_is_gone_immediately(clean_recordings, clean_shares):
    rec = upload().json()["recording"]
    token = share_of(rec["id"]).json()["share"]["token"]

    assert client.delete(f"/api/share/{token}").status_code == 200
    dead = client.get(f"/api/share/{token}")
    # 410 而不是 404：曾經有效而現在收回，前端才分得出「網址打錯」與「已失效」
    assert dead.status_code == 410
    assert "撤銷" in dead.json()["detail"]
    assert client.get(f"/api/share/{token}/audio").status_code == 410


def test_download_cap_counts_downloads_not_plays(clean_recordings, clean_shares):
    rec = upload().json()["recording"]
    token = share_of(rec["id"], max_downloads=1).json()["share"]["token"]

    # 播放（含拖進度條）不扣額度，否則使用者拖一下就把自己鎖在外面
    for _ in range(3):
        assert client.get(f"/api/share/{token}/audio").status_code == 200
    assert client.get(f"/api/share/{token}/audio?download=1").status_code == 200

    assert client.get(f"/api/share/{token}/audio").status_code == 410
    assert "次數" in client.get(f"/api/share/{token}").json()["detail"]


def test_share_dies_with_its_recording(clean_recordings, clean_shares):
    rec = upload().json()["recording"]
    token = share_of(rec["id"]).json()["share"]["token"]

    client.delete(f"/api/recordings/{rec['id']}")
    gone = client.get(f"/api/share/{token}")
    assert gone.status_code == 410
    assert "不在" in gone.json()["detail"]


def test_share_dies_when_quota_evicts_the_recording(clean_recordings, clean_shares,
                                                    restore_settings):
    client.post("/api/settings", json={"recording_max_count": 1})
    first = upload(song_id="test_share_evicted").json()["recording"]
    token = share_of(first["id"]).json()["share"]["token"]

    # 配額擠掉那一筆是無聲發生的（沒有人按刪除），連結還是要打不開
    upload(song_id="test_share_newer")
    assert client.get(f"/api/share/{token}").status_code == 410


def test_bad_tokens_are_404_and_never_reach_the_filesystem(clean_recordings, clean_shares):
    upload()
    assert client.get("/api/share/nope").status_code == 404
    assert client.get("/api/share/..%2F..%2Fetc%2Fpasswd").status_code == 404
    assert client.get("/api/share/nope/audio").status_code == 404
    assert client.delete("/api/share/nope").status_code == 404


def test_share_can_be_turned_off(clean_recordings, clean_shares, restore_settings):
    rec = upload().json()["recording"]
    client.post("/api/settings", json={"recording_share_enabled": False})
    assert share_of(rec["id"]).status_code == 403
    client.post("/api/settings", json={"recording_share_enabled": True})
    assert share_of(rec["id"]).status_code == 200


def test_share_ttl_comes_from_settings(clean_recordings, clean_shares, restore_settings):
    client.post("/api/settings", json={"recording_share_ttl_hours": 2})
    rec = upload().json()["recording"]
    share = share_of(rec["id"]).json()["share"]
    assert share["ttl_hours"] == 2
    assert 1 * 3600 < share["expires_in_seconds"] <= 2 * 3600


def test_share_of_unknown_recording_is_404(clean_recordings, clean_shares):
    assert share_of("nope").status_code == 404
    assert client.get("/api/recordings/nope/shares").status_code == 404


def test_share_list_shows_dead_links_with_a_reason(clean_recordings, clean_shares):
    rec = upload().json()["recording"]
    token = share_of(rec["id"]).json()["share"]["token"]
    client.delete(f"/api/share/{token}")

    shares = client.get(f"/api/recordings/{rec['id']}/shares").json()["shares"]
    # 失效的連結要留在清單上一段時間：畫面要說得出「為什麼那個 QR 打不開」
    assert [s["status"] for s in shares] == ["revoked"]
    assert shares[0]["active"] is False


# --- 每人待唱上限（點歌額度）---

@pytest.fixture()
def quota_off():
    """測完把額度關回去並清空佇列，不影響其他測試與本機狀態。"""
    yield
    queue_manager.pending_limit = 0
    queue_manager.queue.clear()


def test_pending_limit_is_a_shared_control(quota_off):
    """
    上限走 /api/control，所以包廂裡每一台裝置都看得到現在的規則。

    被擋下來的那支手機一定要看得到上限是多少，否則那句「上限 3 首」對他來說
    是一個憑空出現的數字。
    """
    res = client.post("/api/control", json={"pending_limit": 3})
    assert res.status_code == 200
    assert res.json()["state"]["pending_limit"] == 3
    assert client.get("/api/queue").json()["pending_limit"] == 3


def test_pending_limit_control_is_clamped(quota_off):
    assert client.post("/api/control", json={"pending_limit": -1}
                       ).json()["state"]["pending_limit"] == 0
    assert client.post("/api/control", json={"pending_limit": 9999}
                       ).json()["state"]["pending_limit"] == song_quota.MAX_PENDING_LIMIT


def test_quota_endpoint_reports_usage(quota_off):
    queue_manager.pending_limit = 2
    queue_manager.queue.extend([
        {"queue_id": "q1", "song_id": "s1", "requested_by": "小明"},
        {"queue_id": "q2", "song_id": "s2", "requested_by": "小美"},
        {"queue_id": "q3", "song_id": "s3", "requested_by": "小明"},
    ])
    body = client.get("/api/quota").json()
    assert body["limit"] == 2
    assert [(s["name"], s["pending"], s["full"]) for s in body["singers"]] == [
        ("小明", 2, True), ("小美", 1, False)]


def test_add_is_refused_with_409_and_the_whole_verdict(quota_off):
    """
    409（不是 429）：擋下來的理由不是「按太快」，是「佇列現在的狀態不收這一首」。

    回應要帶著整份結論，畫面才講得出「誰、現在幾首、什麼時候可以再點」——
    少講最後一件，使用者的下一個動作就是再按一次。
    """
    queue_manager.pending_limit = 1
    queue_manager.queue.append(
        {"queue_id": "q1", "song_id": "s1", "requested_by": "小明", "priority": False})
    res = client.post("/api/queue/add", json={"id": "newsong0001", "requested_by": "小明"})
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["error"] == "pending_limit_reached"
    assert detail["quota"]["name"] == "小明"
    assert detail["quota"]["pending"] == 1
    assert detail["quota"]["limit"] == 1
    assert detail["quota"]["next_position"] == 1
    # 被擋下來的歌完全沒有進到佇列裡
    assert [i["queue_id"] for i in queue_manager.queue] == ["q1"]


def test_add_reports_remaining_allowance(quota_off):
    """點成功時順便回報「還剩幾首」，畫面才講得出「還可以再排 1 首」。"""
    queue_manager.pending_limit = 3
    queue_manager.queue.append(
        {"queue_id": "q1", "song_id": "s1", "requested_by": "小明", "priority": False})
    res = client.post("/api/queue/add", json={"id": "newsong0002", "requested_by": "小明"})
    assert res.status_code == 200
    quota = res.json()["quota"]
    assert (quota["pending"], quota["remaining"], quota["full"]) == (2, 1, False)


# --- 包廂計時（歡唱時間）---

@pytest.fixture()
def room_off():
    """
    測完把計時關掉、佇列清空、設定還原。

    計時是**持久化**的全域狀態（cache/room_timer.json），不還原的話這支測試
    會在開發機上留下一場永遠在倒數的包廂。
    """
    saved = settings.all()
    yield
    settings.update(saved)
    queue_manager.room.stop()
    queue_manager._room_autostart_off = False
    queue_manager.queue.clear()
    queue_manager.current_song = None
    queue_manager.is_playing = False


def test_room_endpoint_reports_the_clock_and_the_rules(room_off):
    """
    快照要帶著規則一起回：「時間到會讓你唱完這一首」與「只是提醒、不會停」
    是兩句完全不同的話，而決定是哪一句的是設定，不是計時器。
    """
    settings.update({"room_timer_enabled": True, "room_timer_minutes": 90,
                     "room_timer_extend_minutes": 20})
    res = client.post("/api/room/start", json={})
    assert res.status_code == 200
    room = res.json()["room"]
    assert room["active"] is True
    assert room["state"] == "running"
    assert room["total_seconds"] == 90 * 60
    assert room["enabled"] is True
    assert room["expire_action"] == "finish_song"
    assert room["extend_minutes"] == 20
    # 同一份資料也跟著每一次狀態廣播走，畫面不必自己去輪詢
    assert client.get("/api/queue").json()["room"]["total_seconds"] == 90 * 60
    assert client.get("/api/room").json()["total_seconds"] == 90 * 60


def test_room_start_takes_an_explicit_length(room_off):
    settings.update({"room_timer_enabled": True})
    room = client.post("/api/room/start", json={"minutes": 45}).json()["room"]
    assert room["total_seconds"] == 45 * 60


def test_room_pause_and_resume(room_off):
    settings.update({"room_timer_enabled": True})
    client.post("/api/room/start", json={"minutes": 60})
    assert client.post("/api/room/pause").json()["room"]["state"] == "paused"
    assert client.post("/api/room/resume").json()["room"]["state"] == "running"


def test_room_extend_adds_time_to_the_same_session(room_off):
    settings.update({"room_timer_enabled": True})
    client.post("/api/room/start", json={"minutes": 60})
    room = client.post("/api/room/extend", json={"minutes": 30}).json()["room"]
    assert room["total_seconds"] == 90 * 60


def test_room_stop_clears_the_session(room_off):
    settings.update({"room_timer_enabled": True})
    client.post("/api/room/start", json={"minutes": 60})
    room = client.post("/api/room/stop").json()["room"]
    assert room["active"] is False
    assert room["state"] == "off"


def test_add_after_time_up_is_refused_with_409_and_the_snapshot(room_off):
    """
    時間到跟額度滿在畫面上是同一種語氣：說明規則，並且講出下一步
    （續時就接著唱）—— 所以整份快照原封不動送出去。
    """
    settings.update({"room_timer_enabled": True, "room_timer_expire_action": "finish_song"})
    client.post("/api/room/start", json={"minutes": 10})
    # 把開場時間挪到過去，讓這一場已經過期
    queue_manager.room._started_at -= timedelta(minutes=11)

    res = client.post("/api/queue/add", json={"id": "roomsong001", "requested_by": "小明"})
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["error"] == "room_time_up"
    assert detail["room"]["expired"] is True
    assert detail["room"]["extend_minutes"] > 0
    # 被擋下來的歌完全沒有進到佇列裡
    assert queue_manager.queue == []


def test_notify_only_never_refuses_a_song(room_off):
    settings.update({"room_timer_enabled": True,
                     "room_timer_expire_action": "notify_only",
                     "room_timer_autostart": False})
    client.post("/api/room/start", json={"minutes": 10})
    queue_manager.room._started_at -= timedelta(minutes=11)
    res = client.post("/api/queue/add", json={"id": "roomsong002", "requested_by": "小明"})
    assert res.status_code == 200
    assert res.json()["room"]["expired"] is True


# --- 舞台訊息（跑馬燈）---

@pytest.fixture()
def marquee_clean():
    """測完把訊息清乾淨、設定還原（訊息是全域狀態，會漏到下一支測試）。"""
    saved = settings.all()
    main.marquee_board.clear()
    yield
    main.marquee_board.clear()
    settings.update(saved)


def test_marquee_post_and_list(marquee_clean):
    """送出去的那一則要立刻出現在清單上，而且帶著「什麼時候會消失」。"""
    settings.update({"marquee_enabled": True, "marquee_ttl_minutes": 10})
    res = client.post("/api/marquee", json={"text": "您的餐點到了", "sender": "櫃檯"})
    assert res.status_code == 200
    body = res.json()
    assert body["message"]["text"] == "您的餐點到了"
    assert body["message"]["expires_at"]
    state = client.get("/api/marquee").json()
    assert state["count"] == 1
    assert state["enabled"] is True
    # 規則跟著清單一起送：舞台要知道「沒在播歌時要不要用大字卡」，
    # 點歌台要知道預設幾分鐘後消失。分兩支端點拿的話會有一邊是舊的。
    assert state["default_ttl_minutes"] == 10
    assert "card_when_idle" in state


def test_marquee_empty_text_is_409_not_500(marquee_clean):
    """擋下來的語氣是「說明」不是「錯誤」—— 跟點歌額度、歡唱時間同一種。"""
    settings.update({"marquee_enabled": True})
    res = client.post("/api/marquee", json={"text": "   "})
    assert res.status_code == 409
    assert res.json()["detail"]["error"] == "empty"


def test_marquee_refuses_when_the_feature_is_off(marquee_clean):
    settings.update({"marquee_enabled": False})
    res = client.post("/api/marquee", json={"text": "測試"})
    assert res.status_code == 409
    assert res.json()["detail"]["error"] == "disabled"
    assert client.get("/api/marquee").json()["enabled"] is False


def test_marquee_full_says_how_many_and_what_to_do(marquee_clean):
    settings.update({"marquee_enabled": True})
    for i in range(marquee.MAX_MESSAGES):
        assert client.post("/api/marquee", json={"text": f"訊息 {i}"}).status_code == 200
    res = client.post("/api/marquee", json={"text": "再一則"})
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["error"] == "full"
    assert detail["max_messages"] == marquee.MAX_MESSAGES


def test_marquee_delete_one_and_clear_all(marquee_clean):
    settings.update({"marquee_enabled": True})
    first = client.post("/api/marquee", json={"text": "打錯字了"}).json()["message"]
    client.post("/api/marquee", json={"text": "🎂 生日快樂", "pinned": True})
    res = client.delete(f"/api/marquee/{first['id']}")
    assert res.json()["removed"] is True
    assert res.json()["marquee"]["count"] == 1
    # 撤不到也回 success：畫面要的結果是「它不在了」，而它確實不在了
    assert client.delete(f"/api/marquee/{first['id']}").json()["removed"] is False
    # 「把剛剛那幾則清掉」跟「連生日祝福也拿掉」是兩個不同的意思
    assert client.delete("/api/marquee?include_pinned=false").json()["removed"] == 0
    assert client.delete("/api/marquee").json()["removed"] == 1
    assert client.get("/api/marquee").json()["count"] == 0


def test_marquee_message_text_is_capped(marquee_clean):
    """舞台那一條只有一行。一段一百字的訊息在上面沒有人讀得完。"""
    settings.update({"marquee_enabled": True})
    msg = client.post("/api/marquee", json={"text": "字" * 300}).json()["message"]
    assert len(msg["text"]) == marquee.MAX_TEXT_CHARS


# --- 自動接歌（沒有人點歌時，機器自己接一首）---

class StubLibrary:
    """假曲庫：API 測試不該依賴開發機上真的快取了哪幾首歌。"""

    def __init__(self, entries):
        self._entries = list(entries)

    def entries(self):
        return [dict(e) for e in self._entries]


class StubProcessor:
    """假流水線：確保測試絕對不會真的去 YouTube 下載。"""

    async def process_song(self, url, progress_callback=None):
        return {"title": "測試歌", "artist": "測試", "thumbnail": ""}


@pytest.fixture()
def autofill_clean():
    """
    測完把設定、曲庫來源與自動接歌的狀態全部還原。

    自動接歌會動到**共用的佇列**（它真的會讓一首歌上台），不還原的話
    開發機上會留下一首沒有人點、卻一直顯示在播的歌。
    """
    saved_settings = settings.all()
    saved_library = queue_manager.library
    saved_processor = queue_manager.processor
    saved_queue = list(queue_manager.queue)
    saved_current = queue_manager.current_song
    entries = [
        {"song_id": "autofill001", "title": "自動接歌測試 A", "artist": "甲",
         "thumbnail": "", "plays": 0},
        {"song_id": "autofill002", "title": "自動接歌測試 B", "artist": "乙",
         "thumbnail": "", "plays": 7},
    ]
    queue_manager.library = StubLibrary(entries)
    queue_manager.processor = StubProcessor()
    queue_manager.queue.clear()
    queue_manager.current_song = None
    queue_manager.autofill_recent = []
    queue_manager.autofill_streak = 0
    queue_manager.autofill_last = None
    yield entries
    settings.update(saved_settings)
    queue_manager.library = saved_library
    queue_manager.processor = saved_processor
    queue_manager.queue[:] = saved_queue
    queue_manager.current_song = saved_current
    queue_manager.is_playing = bool(saved_current)
    queue_manager.autofill_recent = []
    queue_manager.autofill_streak = 0
    queue_manager.autofill_last = None


def test_autofill_endpoint_reports_the_rules(autofill_clean):
    """
    規則跟狀態一起回：「20 秒沒人點就接、最多連著接 3 首」是一句看得懂的話，
    少講任何一半，使用者都無法預期一台會自己出聲的機器下一步要做什麼。
    """
    settings.update({"autofill_enabled": True, "autofill_source": "favorites",
                     "autofill_idle_seconds": 45, "autofill_stop_after": 2})
    body = client.get("/api/autofill").json()
    assert body["enabled"] is True
    assert body["source"] == "favorites"
    assert body["idle_seconds"] == 45
    assert body["stop_after"] == 2
    assert body["streak"] == 0
    assert body["playing"] is False
    # 同一份資料也跟著每一次狀態廣播走，畫面不必自己去輪詢
    assert client.get("/api/queue").json()["autofill"]["idle_seconds"] == 45


def test_autofill_is_off_by_default(autofill_clean):
    """預設關著：會讓機器自己發出聲音的規則，包廂要先講好才開。"""
    settings.reset()
    assert client.get("/api/autofill").json()["enabled"] is False


def test_autofill_preview_says_which_song_and_why(autofill_clean):
    """
    設定頁要能先看一眼「現在接的話會接哪一首」——
    機器自己放歌時，使用者第一個想問的就是「它為什麼放這首」。
    """
    settings.update({"autofill_enabled": True, "autofill_source": "mixed"})
    body = client.get("/api/autofill/preview").json()
    assert body["song"]["song_id"] in {"autofill001", "autofill002"}
    assert body["reason"]
    assert body["pool"] == 2
    # 只挑不播：按幾次都不會影響等一下真正接歌的結果
    assert queue_manager.current_song is None
    assert queue_manager.autofill_recent == []


def test_autofill_preview_is_404_when_the_library_is_empty(autofill_clean):
    """曲庫空的不是錯誤，是「這台機器還沒有歌可以挑」。"""
    queue_manager.library = StubLibrary([])
    assert client.get("/api/autofill/preview").status_code == 404


def test_random_pick_adds_a_song_as_a_human_request(autofill_clean):
    """🎲 來一首：挑法跟自動接歌共用，但挑出來的歌算人點的。"""
    settings.update({"autofill_enabled": False})     # 這顆鍵不需要先開自動接歌
    res = client.post("/api/autofill/random", json={"requested_by": "小明"})
    assert res.status_code == 200
    body = res.json()
    assert body["item"]["requested_by"] == "小明"
    assert body["item"].get("auto") is not True
    assert body["reason"]
    assert body["placement"]["position"] >= 0


def test_random_pick_is_404_when_the_library_is_empty(autofill_clean):
    queue_manager.library = StubLibrary([])
    res = client.post("/api/autofill/random", json={})
    assert res.status_code == 404


def test_random_pick_is_refused_when_the_quota_is_full(autofill_clean):
    """這顆鍵是點歌的捷徑，不是繞過規則的後門。"""
    queue_manager.pending_limit = 1
    try:
        client.post("/api/autofill/random", json={"requested_by": "小明"})  # 上台
        client.post("/api/autofill/random", json={"requested_by": "小明"})  # 排一首
        res = client.post("/api/autofill/random", json={"requested_by": "小明"})
        assert res.status_code == 409
        assert res.json()["detail"]["error"] == "pending_limit_reached"
    finally:
        queue_manager.pending_limit = 0
