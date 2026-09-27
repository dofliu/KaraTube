"""今晚擂台（backend/services/contest.py）"""
from datetime import datetime, timedelta

import pytest

from backend.services import contest


def take(name, song_id, accuracy, *, title="", score=1000, note_frames=3000, grade="A"):
    return {
        "performer": name, "song_id": song_id, "title": title or song_id,
        "accuracy": accuracy, "score": score, "note_frames": note_frames, "grade": grade,
    }


@pytest.fixture
def board(tmp_path):
    return contest.ContestBoard(tmp_path / "contest.json")


def sing(board, name, songs, **kw):
    """把 `{song_id: accuracy}` 一首一首記進去，回傳最後一次的結果。"""
    out = None
    for song_id, accuracy in songs.items():
        out = board.record(take(name, song_id, accuracy, **kw))
    return out


# --- 決定一：排名用命中率，不是那個會隨歌長膨脹的累加分數 ---

def test_long_song_high_score_does_not_beat_accurate_short_song():
    """
    整個功能唯一不能弄錯的一件事：五分鐘唱到 70% 的人不該贏過
    兩分半唱到 95% 的人（見 contest.py 決定一）。
    """
    b = contest.ContestBoard(None)
    sing(b, "長歌哥", {"L1": 0.70, "L2": 0.70, "L3": 0.70}, score=9000, note_frames=18000)
    sing(b, "準哥", {"S1": 0.95, "S2": 0.95, "S3": 0.95}, score=2000, note_frames=6000)
    ranked = b.snapshot()["ranked"]
    assert [r["name"] for r in ranked] == ["準哥", "長歌哥"]
    assert ranked[0]["points"] == 95


# --- 決定二：代表分＝最好的三首平均；唱不滿三首不排名 ---

def test_needs_three_distinct_songs_to_rank(board):
    result = sing(board, "小明", {"a": 0.9, "b": 0.9})
    assert result["accepted"] is True
    assert result["qualified"] is False
    assert result["need"] == 1
    assert board.snapshot()["ranked"] == []
    assert [r["name"] for r in board.snapshot()["waiting"]] == ["小明"]

    result = board.record(take("小明", "c", 0.9))
    assert result["qualified"] is True
    assert result["rank"] == 1


def test_rank_score_is_mean_of_best_three(board):
    sing(board, "小明", {"a": 0.60, "b": 0.90, "c": 0.80, "d": 0.70})
    row = board.snapshot()["ranked"][0]
    # 最好的三首是 0.90 / 0.80 / 0.70 → 80，第四首（0.60）不拉低平均
    assert row["points"] == 80
    assert row["songs"] == 4


def test_singing_more_never_lowers_the_rank_score(board):
    """決定二的核心：多唱只可能變好，否則想守名次的人會停止唱歌。"""
    sing(board, "小明", {"a": 0.90, "b": 0.85, "c": 0.80})
    before = board.snapshot()["ranked"][0]["points"]
    board.record(take("小明", "d", 0.10))  # 唱壞一首
    assert board.snapshot()["ranked"][0]["points"] == before


def test_more_songs_alone_does_not_win(board):
    """不是加總：唱十首的人不會因為唱得多就贏過唱三首唱得好的人。"""
    sing(board, "麥霸", {f"m{i}": 0.70 for i in range(10)})
    sing(board, "阿華", {"a": 0.88, "b": 0.86, "c": 0.84})
    assert [r["name"] for r in board.snapshot()["ranked"]] == ["阿華", "麥霸"]


# --- 決定三：同一首只算最好的那一次 ---

def test_same_song_counts_once_at_its_best(board):
    for accuracy in (0.50, 0.95, 0.60):
        board.record(take("小明", "a", accuracy))
    row = board.snapshot()["waiting"][0]
    assert row["songs"] == 1       # 三次都是同一首 → 一首
    assert row["takes"] == 3       # 但「唱了幾次」照算
    assert row["best_points"] == 95


def test_repeating_one_song_cannot_reach_the_threshold(board):
    for _ in range(8):
        board.record(take("刷榜哥", "a", 0.99))
    assert board.snapshot()["ranked"] == []


# --- 決定四：沒取暱稱的不進榜 ---

def test_unnamed_takes_are_counted_but_never_ranked(board):
    for song in ("a", "b", "c"):
        result = board.record(take("", song, 0.99))
    assert result["accepted"] is False
    assert result["reason"] == "no_name"
    snap = board.snapshot()
    assert snap["ranked"] == [] and snap["waiting"] == []
    assert snap["unnamed_takes"] == 3
    assert snap["total_takes"] == 3


def test_whitespace_only_name_is_unnamed(board):
    assert board.record(take("   ", "a", 0.9))["reason"] == "no_name"


def test_same_person_different_spellings_merge(board):
    """身分規則跟公平輪唱同一套：收斂空白、不分大小寫。"""
    board.record(take("Amy", "a", 0.9))
    board.record(take("amy", "b", 0.9))
    board.record(take(" Amy ", "c", 0.9))
    snap = board.snapshot()
    assert len(snap["ranked"]) == 1
    # 畫面上用最新的寫法
    assert snap["ranked"][0]["name"] == "Amy"
    assert snap["ranked"][0]["songs"] == 3


# --- 決定七：沒有導唱音符的歌是「沒得算」，不是 0 分 ---

def test_song_without_guide_notes_is_not_a_song(board):
    result = board.record(take("小明", "a", 0.0, note_frames=0))
    assert result["accepted"] is False
    assert result["reason"] == "no_pitch_data"
    assert board.snapshot()["total_takes"] == 0


def test_no_pitch_data_does_not_dilute_the_average(board):
    sing(board, "小明", {"a": 0.90, "b": 0.90, "c": 0.90})
    board.record(take("小明", "d", 0.0, note_frames=10))
    assert board.snapshot()["ranked"][0]["points"] == 90


def test_zero_accuracy_with_real_notes_still_counts(board):
    """唱了整首但一句都沒中是真的 0 分，跟「沒得算」不一樣。"""
    sing(board, "小明", {"a": 0.0, "b": 0.0, "c": 0.0})
    assert board.snapshot()["ranked"][0]["points"] == 0


# --- 決定五：一場照空檔切 ---

def test_board_rolls_over_after_the_session_gap(board):
    t0 = datetime(2026, 9, 27, 21, 0)
    for song in ("a", "b", "c"):
        board.record(take("小明", song, 0.9), now=t0)
    assert board.snapshot(t0)["ranked"][0]["name"] == "小明"

    next_night = t0 + timedelta(hours=20)
    board.record(take("阿華", "x", 0.8), now=next_night)
    snap = board.snapshot(next_night)
    assert snap["ranked"] == []
    assert [r["name"] for r in snap["waiting"]] == ["阿華"]


def test_reading_the_board_also_rolls_a_stale_session(board):
    """一早開機看到的不該是昨晚那一場的歌王（他已經回家了）。"""
    t0 = datetime(2026, 9, 27, 21, 0)
    for song in ("a", "b", "c"):
        board.record(take("小明", song, 0.9), now=t0)
    assert board.snapshot(t0 + timedelta(hours=8))["ranked"] == []


def test_a_long_night_past_midnight_is_one_session(board):
    """九點唱到凌晨兩點半是同一場（照日曆日切會把它切成兩半）。"""
    t0 = datetime(2026, 9, 27, 21, 0)
    board.record(take("小明", "a", 0.9), now=t0)
    board.record(take("小明", "b", 0.9), now=t0 + timedelta(hours=3))
    board.record(take("小明", "c", 0.9), now=t0 + timedelta(hours=5, minutes=30))
    assert board.snapshot(t0 + timedelta(hours=5, minutes=31))["ranked"][0]["songs"] == 3


def test_clock_going_backwards_does_not_split_a_session(board):
    """NTP 校時讓時間倒退時差值是負的，寧可少切一場也不要切錯。"""
    t0 = datetime(2026, 9, 27, 21, 0)
    board.record(take("小明", "a", 0.9), now=t0)
    board.record(take("小明", "b", 0.9), now=t0 - timedelta(hours=10))
    assert board.snapshot(t0)["waiting"][0]["songs"] == 2


def test_gap_hours_is_configurable(board):
    board.set_gap_hours(2)
    t0 = datetime(2026, 9, 27, 21, 0)
    board.record(take("小明", "a", 0.9), now=t0)
    board.record(take("阿華", "x", 0.9), now=t0 + timedelta(hours=3))
    assert board.snapshot(t0 + timedelta(hours=3))["singers"] == 1


def test_bad_gap_hours_is_ignored(board):
    board.set_gap_hours("很久")
    board.set_gap_hours(0)
    assert board.snapshot()["gap_hours"] == 6.0


# --- 決定六：榜要存檔 ---

def test_board_survives_a_restart(tmp_path):
    path = tmp_path / "contest.json"
    first = contest.ContestBoard(path)
    sing(first, "小明", {"a": 0.9, "b": 0.8, "c": 0.7})
    again = contest.ContestBoard(path)
    row = again.snapshot()["ranked"][0]
    assert row["name"] == "小明" and row["points"] == 80


def test_broken_state_file_falls_back_to_an_empty_board(tmp_path):
    path = tmp_path / "contest.json"
    path.write_text("{ not json", encoding="utf-8")
    board = contest.ContestBoard(path)
    assert board.snapshot()["ranked"] == []
    # 壞檔之後還能照常記分（榜壞掉不該讓包廂不能唱歌）
    sing(board, "小明", {"a": 0.9, "b": 0.9, "c": 0.9})
    assert board.snapshot()["ranked"][0]["name"] == "小明"


# --- 排名與同分 ---

def test_ties_break_on_best_single_song_then_song_count(board):
    sing(board, "甲", {"a": 0.90, "b": 0.80, "c": 0.70})   # 平均 80，最佳 90
    sing(board, "乙", {"d": 0.85, "e": 0.80, "f": 0.75})   # 平均 80，最佳 85
    ranked = board.snapshot()["ranked"]
    assert [r["points"] for r in ranked] == [80, 80]
    assert [r["name"] for r in ranked] == ["甲", "乙"]


def test_ranking_is_stable_across_reads(board):
    for who in ("甲", "乙", "丙"):
        sing(board, who, {f"{who}{i}": 0.80 for i in range(3)})
    order = [r["name"] for r in board.snapshot()["ranked"]]
    for _ in range(5):
        assert [r["name"] for r in board.snapshot()["ranked"]] == order


def test_waiting_list_puts_the_closest_first(board):
    board.record(take("差兩首", "a", 0.9))
    sing(board, "差一首", {"b": 0.5, "c": 0.5})
    assert [r["name"] for r in board.snapshot()["waiting"]] == ["差一首", "差兩首"]


# --- 結算畫面要的那一句話 ---

def test_taking_the_lead_is_distinct_from_holding_it(board):
    sing(board, "甲", {"a": 0.80, "b": 0.80, "c": 0.80})
    result = sing(board, "乙", {"d": 0.90, "e": 0.90, "f": 0.90})
    assert result["is_leader"] is True and result["took_lead"] is True
    # 已經是第一的人再唱一首：還是第一，但不是「搶下」
    again = board.record(take("乙", "g", 0.95))
    assert again["is_leader"] is True and again["took_lead"] is False


def test_verdict_reports_rank_movement(board):
    sing(board, "甲", {"a": 0.90, "b": 0.90, "c": 0.90})
    sing(board, "乙", {"d": 0.50, "e": 0.50, "f": 0.50})
    result = board.record(take("乙", "g", 0.99))
    # 乙 最好的三首變成 0.99/0.50/0.50 → 66，還是輸給 90
    assert result["rank"] == 2 and result["previous_rank"] == 2
    assert result["leader"] == {"name": "甲", "points": 90}


def test_verdict_leader_is_none_before_anyone_qualifies(board):
    result = board.record(take("小明", "a", 0.9))
    assert result["leader"] is None and result["board_size"] == 0


# --- 櫃檯總覽用的一句話 ---

def test_leader_summary(board):
    assert board.leader() is None
    sing(board, "小明", {"a": 0.9, "b": 0.9, "c": 0.9})
    assert board.leader() == {"name": "小明", "points": 90, "songs": 3}


# --- 重設 ---

def test_reset_starts_a_new_session(board):
    sing(board, "小明", {"a": 0.9, "b": 0.9, "c": 0.9})
    before = board.snapshot()["session_id"]
    after = board.reset(now=datetime(2026, 9, 27, 23, 30))
    assert after["ranked"] == [] and after["unnamed_takes"] == 0
    assert after["session_id"] == "20260927-2330" != before


# --- 邊界與防呆 ---

def test_singer_cap_evicts_the_least_likely_to_rank(board):
    # 先放一位唱滿三首的（他不該被擠掉）
    sing(board, "常客", {"a": 0.9, "b": 0.9, "c": 0.9})
    for i in range(contest.MAX_SINGERS + 5):
        board.record(take(f"路人{i}", f"s{i}", 0.5))
    snap = board.snapshot()
    assert snap["singers"] <= contest.MAX_SINGERS
    assert snap["ranked"][0]["name"] == "常客"


def test_song_detail_is_trimmed_but_take_count_is_not(board):
    n = contest.MAX_SONGS_PER_SINGER + 10
    for i in range(n):
        board.record(take("鐵人", f"s{i}", 0.5 + (i % 10) / 100))
    row = board.snapshot()["ranked"][0]
    assert row["songs"] == contest.MAX_SONGS_PER_SINGER
    assert row["takes"] == n


def test_garbage_values_do_not_crash(board):
    assert board.record({})["accepted"] is False
    assert board.record({"song_id": "a", "performer": "x",
                         "accuracy": "很準", "note_frames": "很多"})["accepted"] is False
    board.record({"song_id": "a", "performer": "x", "accuracy": "很準",
                  "note_frames": 5000, "score": None})
    assert board.snapshot()["waiting"][0]["points"] == 0


def test_accuracy_is_clamped(board):
    sing(board, "小明", {"a": 5.0, "b": -1.0, "c": 0.5})
    assert board.snapshot()["ranked"][0]["points"] == 50   # (100 + 0 + 50) / 3


def test_missing_song_id_is_rejected(board):
    assert board.record(take("小明", "", 0.9))["reason"] == "no_pitch_data"


def test_session_id_from_bad_input():
    assert contest.session_id_from("not a date") == ""
    assert contest.session_id_from(None) == ""
