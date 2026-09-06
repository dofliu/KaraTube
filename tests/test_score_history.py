"""評分歷史：唱畢結算、個人最佳與擊敗比例。"""
import json

from backend.services.score_history import ScoreHistory


def make_scores(tmp_path):
    return ScoreHistory(tmp_path / "score_history.json")


def test_first_record_is_personal_best(tmp_path):
    s = make_scores(tmp_path)
    r = s.record({"song_id": "a", "title": "初登場", "score": 1200,
                  "accuracy": 0.5, "max_combo": 30, "grade": "S"})
    assert r["is_new_best"] is True
    assert r["best_score"] == 1200
    assert r["previous_best"] is None
    # 沒有任何歷史成績可比，擊敗比例不硬掰
    assert r["beat_percent"] is None


def test_new_best_and_beat_percent(tmp_path):
    s = make_scores(tmp_path)
    s.record({"song_id": "a", "score": 1000})
    s.record({"song_id": "b", "score": 2000})
    s.record({"song_id": "c", "score": 3000})

    # 2500 分擊敗 1000 與 2000 兩筆，共 3 筆歷史 → 67%
    r = s.record({"song_id": "a", "score": 2500})
    assert r["is_new_best"] is True
    assert r["previous_best"] == 1000
    assert r["beat_percent"] == 67

    # 沒破該曲紀錄
    r2 = s.record({"song_id": "b", "score": 1500})
    assert r2["is_new_best"] is False
    assert r2["best_score"] == 2000


def test_record_requires_song_id_and_valid_score(tmp_path):
    s = make_scores(tmp_path)
    assert s.record({"score": 100}) is None
    assert s.record({"song_id": "x", "score": "not-a-number"}) is None
    assert s.record({"song_id": "x", "score": -5}) is None
    assert s.total_count() == 0


def test_accuracy_and_combo_are_clamped(tmp_path):
    s = make_scores(tmp_path)
    r = s.record({"song_id": "a", "score": 10, "accuracy": 5.0, "max_combo": -3})
    assert r["accuracy"] == 1.0
    assert r["max_combo"] == 0


def test_section_verdict_is_recorded(tmp_path):
    s = make_scores(tmp_path)
    r = s.record({"song_id": "a", "score": 800,
                  "best_section": "副歌 1", "worst_section": "主歌 2"})
    assert r["best_section"] == "副歌 1"
    assert r["worst_section"] == "主歌 2"
    assert s.best_for("a")["worst_section"] == "主歌 2"


def test_section_verdict_defaults_to_empty(tmp_path):
    # 沒有歌詞的歌切不出段落，段落評分不會有結論 —— 欄位要是空字串而不是 None，
    # 前端才能一律用 `if (r.best_section)` 判斷要不要顯示。
    s = make_scores(tmp_path)
    r = s.record({"song_id": "a", "score": 800})
    assert r["best_section"] == ""
    assert r["worst_section"] == ""
    r2 = s.record({"song_id": "a", "score": 810, "best_section": None})
    assert r2["best_section"] == ""


def test_section_labels_are_truncated(tmp_path):
    # 段落標籤是伺服器自己產的，但成績單是外部 POST 進來的，長度還是要守
    s = make_scores(tmp_path)
    r = s.record({"song_id": "a", "score": 100, "best_section": "副" * 100})
    assert len(r["best_section"]) == 24


def test_best_for_and_recent(tmp_path):
    s = make_scores(tmp_path)
    s.record({"song_id": "a", "score": 500})
    s.record({"song_id": "a", "score": 900})
    s.record({"song_id": "a", "score": 700})

    assert s.best_for("a")["score"] == 900
    assert s.best_for("nope") is None
    recent = s.recent(limit=2)
    assert [e["score"] for e in recent] == [700, 900]  # 最新在前


def test_persistence(tmp_path):
    path = tmp_path / "score_history.json"
    s1 = ScoreHistory(path)
    s1.record({"song_id": "a", "title": "留下來", "score": 800})

    s2 = ScoreHistory(path)
    assert s2.total_count() == 1
    assert s2.best_for("a")["score"] == 800


def test_corrupt_file_recovers(tmp_path):
    path = tmp_path / "score_history.json"
    path.write_text("{broken", encoding="utf-8")
    s = ScoreHistory(path)
    assert s.total_count() == 0
    assert s.record({"song_id": "ok", "score": 1}) is not None


def test_clear(tmp_path):
    s = make_scores(tmp_path)
    s.record({"song_id": "a", "score": 100})
    s.clear()
    assert s.total_count() == 0
    assert s.best_for("a") is None
    data = json.loads((tmp_path / "score_history.json").read_text(encoding="utf-8"))
    assert data["entries"] == [] and data["bests"] == {}
