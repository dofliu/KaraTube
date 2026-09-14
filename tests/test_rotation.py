"""
公平輪唱（排麥輪序）的規則測試。

這一層錯掉的後果不是當機，是**不公平** —— 而且不公平在畫面上看起來完全正常
（佇列就是一排歌，沒有人看得出來它排錯了）。所以每一條規則都要有測試釘著，
尤其是那幾個「另一種寫法也會動、但會壞在真實場景」的決定。
"""
from datetime import datetime, timedelta

from backend.services.rotation import (
    ANON_KEY,
    DEFAULT_SESSION_GAP_HOURS,
    RotationTracker,
    compute_rounds,
    display_name,
    locked_prefix_len,
    plan_insert_index,
    rotation_summary,
    singer_key,
)


def item(singer: str, qid: str = "", priority: bool = False):
    return {"queue_id": qid or f"q-{singer}-{id(singer)}",
            "requested_by": singer, "priority": priority}


def q(*pairs):
    """('小明', 'a') 這種 (暱稱, queue_id) 的簡寫，方便一行排出一整個佇列。"""
    return [item(name, qid) for name, qid in pairs]


# --- 身分正規化 ---

def test_singer_key_normalizes_whitespace_and_case():
    assert singer_key("小明") == singer_key(" 小明 ")
    assert singer_key("Amy") == singer_key("amy") == singer_key("AMY")
    # 中間的連續空白併成一個：手機輸入法很容易多打一個
    assert singer_key("Da  vid") == singer_key("Da vid")


def test_singer_key_treats_all_unnamed_as_one_person():
    """沒取暱稱的全部算同一個人 —— 一整間都沒取名時輪唱就等於沒開。"""
    assert singer_key("") == singer_key(None) == singer_key("   ") == ANON_KEY


def test_display_name_keeps_case_but_trims():
    assert display_name("  Amy  ") == "Amy"
    assert display_name("小" * 40) == "小" * 24


# --- 輪次計算 ---

def test_rounds_count_from_one_per_singer():
    queue = q(("小明", "a"), ("小美", "b"), ("小明", "c"))
    assert compute_rounds(queue, {}) == [1, 1, 2]


def test_rounds_include_songs_already_sung():
    """輪次是「今晚的第幾首」，已經唱完的要算進去（決定三）。"""
    queue = q(("小明", "a"), ("小美", "b"))
    assert compute_rounds(queue, {singer_key("小明"): 2}) == [3, 1]


def test_rounds_for_unnamed_share_one_bucket():
    queue = q(("", "a"), ("", "b"), ("小明", "c"))
    assert compute_rounds(queue, {}) == [1, 2, 1]


# --- 插入位置 ---

def test_new_singer_jumps_ahead_of_second_round():
    """一個人連點三首，後到的人的第一首排在他的第二首前面 —— 這就是整個功能。"""
    queue = q(("小明", "a"), ("小明", "b"), ("小明", "c"))
    index, rnd = plan_insert_index(queue, {}, "小美")
    assert (index, rnd) == (1, 1)


def test_same_round_keeps_arrival_order():
    """同一輪之間維持先到先唱，不會因為輪唱而變成隨機。"""
    queue = q(("小明", "a"), ("小美", "b"), ("小明", "c"))
    index, rnd = plan_insert_index(queue, {}, "阿華")
    assert (index, rnd) == (2, 1)  # 排在兩首第一輪後面、小明的第二首前面


def test_heavy_requester_goes_to_the_back():
    queue = q(("小明", "a"), ("小美", "b"), ("小明", "c"))
    index, rnd = plan_insert_index(queue, {}, "小明")
    assert (index, rnd) == (3, 3)


def test_singer_who_already_sang_does_not_cut_in_front():
    """
    唱完一首之後再點，不能排到「還沒唱過的人」前面。

    只算佇列裡待唱首數的寫法會在這裡壞掉（小明待唱 0 首＝第 1 輪，
    於是排到小美的第一首前面）—— 正好是這個功能要消滅的那件事。
    """
    queue = q(("小美", "b"))
    index, rnd = plan_insert_index(queue, {singer_key("小明"): 1}, "小明")
    assert (index, rnd) == (1, 2)


def test_empty_queue_appends():
    assert plan_insert_index([], {}, "小明") == (0, 1)


def test_unnamed_requests_behave_like_plain_fifo():
    """全部沒取暱稱時，插入位置一律是最後面 —— 行為與沒開輪唱一模一樣。"""
    queue = q(("", "a"), ("", "b"))
    assert plan_insert_index(queue, {}, "")[0] == 2


# --- 插播免疫 ---

def test_locked_prefix_counts_leading_priority_items():
    queue = [item("小明", "a", priority=True), item("小美", "b", priority=True),
             item("阿華", "c")]
    assert locked_prefix_len(queue) == 2
    assert locked_prefix_len(q(("小明", "a"))) == 0


def test_rotation_never_inserts_before_priority_songs():
    """插播是現場按下去的決定，機器的排序規則不該推翻它。"""
    queue = [item("小明", "p", priority=True), item("小明", "a"), item("小明", "b")]
    index, _ = plan_insert_index(queue, {}, "小美")
    assert index == 1  # 插播那首仍然是下一首


def test_priority_songs_still_count_toward_their_round():
    """
    插播照樣佔掉那個人的輪次。不算的話，插播就變成「多唱一首又不影響排序」的漏洞。
    """
    queue = [item("小明", "p", priority=True)]
    _, rnd = plan_insert_index(queue, {}, "小明")
    assert rnd == 2


# --- 畫面用的全貌 ---

def test_summary_reports_rounds_and_people():
    queue = q(("小明", "a"), ("小美", "b"), ("小明", "c"))
    summary = rotation_summary(queue, {singer_key("小明"): 1})
    assert summary["rounds"] == {"a": 2, "b": 1, "c": 3}
    names = [s["name"] for s in summary["singers"]]
    assert names == ["小明", "小美"]  # 小明的歌排在佇列第一格，他先上台
    by_name = {s["name"]: s for s in summary["singers"]}
    assert by_name["小明"]["sung"] == 1 and by_name["小明"]["pending"] == 2
    assert by_name["小美"]["sung"] == 0 and by_name["小美"]["pending"] == 1
    assert summary["named_count"] == 2


def test_summary_keeps_people_who_have_no_songs_left():
    """唱完就從佇列消失的人，今晚的統計還是要算他一份。"""
    summary = rotation_summary(q(("小美", "b")), {singer_key("小明"): 2},
                               {singer_key("小明"): "小明"})
    tail = summary["singers"][-1]
    assert tail["name"] == "小明" and tail["pending"] == 0 and tail["sung"] == 2


def test_summary_marks_unnamed_bucket():
    summary = rotation_summary(q(("", "a"), ("小明", "b")), {})
    anon = [s for s in summary["singers"] if s["anonymous"]]
    assert len(anon) == 1 and anon[0]["pending"] == 1
    assert summary["named_count"] == 1


def test_summary_lists_people_in_real_stage_order():
    """
    人名的順序＝真正的上台順序（他下一首在佇列的位置），不是輪次。

    手動拖曳過的佇列會讓兩者分岔 —— 照輪次排的話，畫面上寫的順序就跟實際
    播出去的順序不一樣，而使用者信的是畫面。
    """
    queue = q(("bob", "a"), ("amy", "b"))
    assert [s["key"] for s in rotation_summary(queue, {})["singers"]] == ["bob", "amy"]
    reversed_queue = list(reversed(queue))
    assert [s["key"] for s in rotation_summary(reversed_queue, {})["singers"]] == ["amy", "bob"]
    # 有人被拖到前面（第 2 輪排在第 1 輪前面）時，跟著佇列走
    dragged = q(("bob", "a"), ("bob", "b"), ("amy", "c"))
    dragged = [dragged[1], dragged[2], dragged[0]]
    summary = rotation_summary(dragged, {})
    assert [s["key"] for s in summary["singers"]] == ["bob", "amy"]


def test_summary_order_is_stable_for_people_with_nothing_left():
    """都唱完的人照唱得多的在前、同分照 key —— 不然每次廣播人名都在跳。"""
    summary = rotation_summary([], {"bob": 1, "amy": 1, "cat": 3})
    assert [s["key"] for s in summary["singers"]] == ["cat", "amy", "bob"]


# --- 今晚唱了幾首 ---

def test_tracker_counts_plays():
    tracker = RotationTracker()
    tracker.record_play(item("小明"))
    tracker.record_play(item("小明"))
    tracker.record_play(item("小美"))
    assert tracker.counts() == {singer_key("小明"): 2, singer_key("小美"): 1}
    assert tracker.names()[singer_key("小明")] == "小明"


def test_tracker_rolls_over_after_a_long_gap():
    """隔了夠久就是下一桌客人 —— 昨晚唱八首的人今晚不該從第九輪開始排。"""
    tracker = RotationTracker()
    start = datetime(2026, 9, 14, 23, 0, 0)
    tracker.record_play(item("小明"), gap_hours=6, now=start)
    rolled = tracker.record_play(item("小明"), gap_hours=6,
                                 now=start + timedelta(hours=7))
    assert rolled is True
    assert tracker.counts() == {singer_key("小明"): 1}


def test_tracker_does_not_roll_over_within_the_same_night():
    tracker = RotationTracker()
    start = datetime(2026, 9, 14, 21, 0, 0)
    tracker.record_play(item("小明"), gap_hours=6, now=start)
    # 跨午夜（21:05 → 隔天 02:30）是同一場，不能歸零
    rolled = tracker.record_play(item("小明"), gap_hours=6,
                                 now=start + timedelta(hours=5, minutes=30))
    assert rolled is False
    assert tracker.counts() == {singer_key("小明"): 2}


def test_tracker_ignores_clock_going_backwards():
    """NTP 校時讓時間倒退時不算換場：差值是負的，本來就不該超過門檻。"""
    tracker = RotationTracker()
    start = datetime(2026, 9, 14, 23, 0, 0)
    tracker.record_play(item("小明"), gap_hours=6, now=start)
    rolled = tracker.record_play(item("小明"), gap_hours=6,
                                 now=start - timedelta(hours=9))
    assert rolled is False
    assert tracker.counts() == {singer_key("小明"): 2}


def test_tracker_reset_clears_everything():
    tracker = RotationTracker()
    tracker.record_play(item("小明"))
    tracker.reset()
    assert tracker.counts() == {} and tracker.names() == {}
    assert tracker.last_play_at is None


def test_default_gap_matches_the_night_export_default():
    """輪序與整晚打包共用「一場」的定義，預設值要一致。"""
    from backend.services.settings import SETTINGS_SPEC
    assert SETTINGS_SPEC["recording_session_gap_hours"]["default"] == DEFAULT_SESSION_GAP_HOURS
