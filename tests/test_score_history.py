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


# --- 對唱模式（兩位演唱者一起記）---


def duet_payload(score_a=5000, score_b=4000, name_a="小明", name_b="小美", song_id="d1"):
    return {
        "song_id": song_id,
        "title": "屋頂",
        "artist": "吳宗憲 / 溫嵐",
        "a": {"singer": name_a, "score": score_a, "accuracy": 0.62, "max_combo": 40,
              "grade": "SS", "best_section": "副歌 1"},
        "b": {"singer": name_b, "score": score_b, "accuracy": 0.51, "max_combo": 22,
              "grade": "S"},
    }


def test_duet_records_both_singers_in_one_shot(tmp_path):
    s = make_scores(tmp_path)
    r = s.record_duet(duet_payload())

    assert r["winner"] == "a"
    assert r["margin"] == 1000
    assert r["a"]["singer"] == "小明"
    assert r["b"]["singer"] == "小美"
    # 兩筆都進了歷史，而且兩筆都標記為對唱
    assert s.total_count() == 2
    assert all(e["duet"] is True for e in s.recent(10))
    # 歌曲資訊由兩位共用（前端只送一份）
    assert all(e["title"] == "屋頂" for e in s.recent(10))


def test_duet_personal_best_is_per_singer(tmp_path):
    s = make_scores(tmp_path)
    s.record_duet(duet_payload(score_a=5000, score_b=4000))
    # 第二輪：小美進步到 4500，但仍低於小明第一輪的 5000。
    # 個人最佳要跟「自己」比，不是跟包廂裡唱最好的人比。
    r = s.record_duet(duet_payload(score_a=4800, score_b=4500))

    assert r["b"]["is_new_best"] is True
    assert r["b"]["previous_best"] == 4000
    assert r["a"]["is_new_best"] is False
    assert r["a"]["best_score"] == 5000

    assert s.singer_best_for("d1", "小美")["score"] == 4500
    assert s.singer_best_for("d1", "小明")["score"] == 5000
    assert s.singer_best_for("d1", "沒唱過的人") is None


def test_duet_also_updates_song_level_best(tmp_path):
    s = make_scores(tmp_path)
    s.record_duet(duet_payload(score_a=5000, score_b=4000))
    # 這台機器在這首歌的最高分（不分是誰唱的）也要更新
    assert s.best_for("d1")["score"] == 5000
    assert s.best_for("d1")["singer"] == "小明"


def test_duet_tie_uses_same_threshold_as_stage(tmp_path):
    s = make_scores(tmp_path)
    # 差 2%（門檻 3%）→ 平手，與 frontend/js/duet-scorer.js 的判定一致
    r = s.record_duet(duet_payload(score_a=50000, score_b=49000))
    assert r["winner"] == "tie"
    # 差 10% → 分出勝負
    r2 = s.record_duet(duet_payload(score_a=50000, score_b=45000, song_id="d2"))
    assert r2["winner"] == "a"


def test_duet_zero_zero_is_a_tie_not_a_crash(tmp_path):
    s = make_scores(tmp_path)
    r = s.record_duet(duet_payload(score_a=0, score_b=0))
    assert r["winner"] == "tie"
    assert r["margin"] == 0


def test_duet_missing_side_records_nothing(tmp_path):
    s = make_scores(tmp_path)
    assert s.record_duet({"song_id": "x", "a": {"score": 100}}) is None
    assert s.record_duet({"a": {"score": 100}, "b": {"score": 50}}) is None
    assert s.record_duet("not a dict") is None
    # 一筆都不能進去：半場的對唱紀錄之後永遠說不清是誰的問題
    assert s.total_count() == 0


def test_duet_without_names_falls_back_to_mic_labels(tmp_path):
    s = make_scores(tmp_path)
    r = s.record_duet(duet_payload(name_a="", name_b=""))
    assert r["a"]["singer"] == "A 麥"
    assert r["b"]["singer"] == "B 麥"


def test_duet_bests_persist_across_restart(tmp_path):
    s = make_scores(tmp_path)
    s.record_duet(duet_payload(score_a=5000, score_b=4000))

    reloaded = make_scores(tmp_path)
    assert reloaded.singer_best_for("d1", "小明")["score"] == 5000
    assert len(reloaded.singer_bests()) == 2
    assert reloaded.total_count() == 2


def test_old_file_without_singer_bests_still_loads(tmp_path):
    """對唱模式之前存下來的評分歷史檔沒有 singer_bests 那一區。"""
    path = tmp_path / "score_history.json"
    path.write_text(json.dumps({
        "entries": [{"song_id": "a", "score": 900}],
        "bests": {"a": {"song_id": "a", "score": 900}},
    }, ensure_ascii=False), encoding="utf-8")

    s = ScoreHistory(path)
    assert s.total_count() == 1
    assert s.singer_bests() == []
    assert s.best_for("a")["score"] == 900


def test_clear_also_wipes_singer_bests(tmp_path):
    s = make_scores(tmp_path)
    s.record_duet(duet_payload())
    s.clear()
    assert s.singer_bests() == []
    assert s.total_count() == 0


def test_solo_record_keeps_empty_singer_field(tmp_path):
    """單人演唱的紀錄不該憑空多出一個名字（歷史清單會突然出現「A 麥」）。"""
    s = make_scores(tmp_path)
    r = s.record({"song_id": "a", "score": 1000})
    assert r["singer"] == ""
    assert r["duet"] is False


def test_duet_records_duel_section_per_singer(tmp_path):
    """段落對決的主場段落要跟著各自那一筆進歷史（回頭看才知道誰擅長哪一段）。"""
    s = make_scores(tmp_path)
    payload = duet_payload()
    payload["a"]["duel_section"] = "副歌 2"
    payload["b"]["duel_section"] = "主歌 1"
    r = s.record_duet(payload)

    assert r["a"]["duel_section"] == "副歌 2"
    assert r["b"]["duel_section"] == "主歌 1"
    # 主場段落存進紀錄本身，不只是回傳值（重開機後歷史還看得到）
    assert {e["duel_section"] for e in s.recent(10)} == {"副歌 2", "主歌 1"}
    assert s.singer_best_for("d1", "小明")["duel_section"] == "副歌 2"


def test_single_singer_has_no_duel_section(tmp_path):
    """一個人唱沒有對手，主場段落一律空字串（不是 None，欄位形狀要一致）。"""
    s = make_scores(tmp_path)
    r = s.record({"song_id": "solo", "score": 100, "best_section": "副歌 1"})
    assert r["duel_section"] == ""


def test_duel_section_label_is_truncated(tmp_path):
    """段落標籤跟 best_section 一樣截斷，壞資料塞不爆歷史檔。"""
    s = make_scores(tmp_path)
    payload = duet_payload()
    payload["a"]["duel_section"] = "副" * 100
    r = s.record_duet(payload)
    assert len(r["a"]["duel_section"]) == 24


# --- 跨場次段落趨勢（1.7.0） ---

def sing(store, song_id="t1", singer="", **sections):
    return store.record({
        "song_id": song_id, "title": "練唱曲", "score": 1000, "singer": singer,
        "sections": [{"label": k, "accuracy": v, "note_frames": 200}
                     for k, v in sections.items()],
    })


def test_sections_are_stored_compacted(tmp_path):
    """入庫的是標籤與命中率兩個欄位，單場才有意義的欄位不留。"""
    s = make_scores(tmp_path)
    s.record({"song_id": "a", "score": 10, "sections": [
        {"label": "副歌 1", "accuracy": 0.5, "note_frames": 300,
         "grade": "S", "start": 12.5, "end": 40.0, "perfect_frames": 9},
    ]})
    assert s.recent(1)[0]["sections"] == [{"label": "副歌 1", "accuracy": 0.5}]


def test_too_short_sections_never_enter_history(tmp_path):
    """兩幀的段落命中率是運氣不是實力 —— 舞台端濾過，API 這一層再濾一次。"""
    s = make_scores(tmp_path)
    s.record({"song_id": "a", "score": 10, "sections": [
        {"label": "短", "accuracy": 1.0, "note_frames": 3},
        {"label": "夠長", "accuracy": 0.4, "note_frames": 120},
        {"label": "舞台端說不算", "accuracy": 0.9, "note_frames": 500, "graded": False},
    ]})
    assert [r["label"] for r in s.recent(1)[0]["sections"]] == ["夠長"]


def test_sections_are_capped_per_entry(tmp_path):
    from backend.services.score_history import MAX_SECTIONS_PER_ENTRY
    s = make_scores(tmp_path)
    s.record({"song_id": "a", "score": 10, "sections": [
        {"label": f"第 {i} 段", "accuracy": 0.5, "note_frames": 99} for i in range(40)]})
    assert len(s.recent(1)[0]["sections"]) == MAX_SECTIONS_PER_ENTRY


def test_trend_appears_after_three_performances(tmp_path):
    s = make_scores(tmp_path)
    r1 = sing(s, 主歌1=0.8, 副歌1=0.4)
    assert r1["trend"]["status"] == "insufficient"
    sing(s, 主歌1=0.78, 副歌1=0.42)
    r3 = sing(s, 主歌1=0.5, 副歌1=0.15)

    # 結算當下看到的那句話要把剛唱完的這一次算進去
    assert r3["trend"]["status"] == "ok"
    assert r3["trend"]["performances"] == 3
    assert r3["trend"]["weak"]["label"] == "副歌1"
    assert s.trend_for("t1")["home"]["label"] == "主歌1"


def test_trend_is_per_singer(tmp_path):
    """段落弱點是這個人的弱點，不是這首歌的難點 —— 兩個人不混算。"""
    s = make_scores(tmp_path)
    for _ in range(3):
        sing(s, singer="小明", 主歌1=0.8, 副歌1=0.4)
        sing(s, singer="小美", 主歌1=0.4, 副歌1=0.8)

    assert s.trend_for("t1", "小明")["weak"]["label"] == "副歌1"
    assert s.trend_for("t1", "小美")["weak"]["label"] == "主歌1"
    # 單人（沒有名字）的紀錄自成一組，不會被有名字的場次灌進來
    assert s.trend_for("t1", "")["status"] == "none"


def test_trends_table_lists_only_conclusive_songs(tmp_path):
    s = make_scores(tmp_path)
    for _ in range(3):
        sing(s, song_id="夠多場", 主歌1=0.8, 副歌1=0.4)
    sing(s, song_id="只唱一次", 主歌1=0.8, 副歌1=0.4)

    rows = s.trends()
    assert [t["song_id"] for t in rows] == ["夠多場"]
    assert rows[0]["best_score"] == 1000


def test_trends_are_sorted_by_last_sung(tmp_path):
    s = make_scores(tmp_path)
    for song in ("先唱的", "後唱的"):
        for _ in range(3):
            sing(s, song_id=song, 主歌1=0.8, 副歌1=0.4)
    assert [t["song_id"] for t in s.trends()][0] == "後唱的"


def test_trend_survives_restart(tmp_path):
    s = make_scores(tmp_path)
    for _ in range(3):
        sing(s, 主歌1=0.8, 副歌1=0.4)
    reopened = make_scores(tmp_path)
    assert reopened.trend_for("t1")["weak"]["label"] == "副歌1"


def test_legacy_entries_without_sections_are_not_a_trend(tmp_path):
    """1.7.0 之前的紀錄沒有段落資料，要說「沒資料」而不是當成 0%。"""
    s = make_scores(tmp_path)
    for _ in range(5):
        s.record({"song_id": "old", "score": 900})
    assert s.trend_for("old")["status"] == "none"
    assert s.trends() == []


def test_duet_sides_each_get_their_own_trend(tmp_path):
    s = make_scores(tmp_path)
    payload = duet_payload()
    for side, shape in (("a", (0.8, 0.4)), ("b", (0.4, 0.8))):
        payload[side]["sections"] = [
            {"label": "主歌1", "accuracy": shape[0], "note_frames": 200},
            {"label": "副歌1", "accuracy": shape[1], "note_frames": 200},
        ]
    for _ in range(3):
        r = s.record_duet(payload)
    assert r["a"]["trend"]["weak"]["label"] == "副歌1"
    assert r["b"]["trend"]["weak"]["label"] == "主歌1"
