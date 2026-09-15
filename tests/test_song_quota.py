"""
每人待唱上限（點歌額度）的規則測試。

這一層錯掉的後果是**點不了歌**，而且錯得最兇的那幾種寫法在畫面上都看起來
完全正常：一個人被莫名其妙擋下來、或是一條額度被整間人共用而沒有人知道。
所以 song_quota.py 開頭那六個決定，每一個都在這裡有一條測試釘著 ——
尤其是那幾個「另一種寫法也會動、但會在真實包廂裡壞掉」的決定。
"""
import pytest

from backend.services.song_quota import (
    DEFAULT_PENDING_LIMIT,
    MAX_PENDING_LIMIT,
    REASON_EXCEEDED,
    REASON_OFF,
    REASON_OK,
    REASON_PRIORITY,
    QuotaExceeded,
    check,
    coerce_limit,
    pending_counts,
    quota_summary,
    state,
)


def q(*pairs):
    """('小明', 'a') 這種 (暱稱, queue_id) 的簡寫，方便一行排出一整個佇列。"""
    return [{"queue_id": qid, "requested_by": name, "priority": False}
            for name, qid in pairs]


# --- 上限值的收斂 ---

def test_default_is_unlimited():
    """預設不限（決定五）：這是一條會改變「我點不點得了歌」的規則，要講好才開。"""
    assert DEFAULT_PENDING_LIMIT == 0


def test_coerce_limit_clamps_into_range():
    assert coerce_limit(3) == 3
    assert coerce_limit(-5) == 0
    assert coerce_limit(999) == MAX_PENDING_LIMIT
    assert coerce_limit("4") == 4


def test_coerce_limit_treats_garbage_as_unlimited():
    """
    看不懂的值一律當成「沒開」，不是拋錯。

    這個值會從手機端、設定檔、舊版前端三個地方送進來；其中任何一個送了怪東西時，
    正確的行為是這條規則沒生效，而不是整個點歌功能掛掉。
    """
    assert coerce_limit(None) == 0
    assert coerce_limit("") == 0
    assert coerce_limit("三首") == 0
    assert coerce_limit([3]) == 0


# --- 待唱計數 ---

def test_pending_counts_groups_by_normalized_nickname():
    queue = q(("小明", "a"), (" 小明 ", "b"), ("Amy", "c"), ("amy", "d"))
    assert pending_counts(queue) == {"小明": 2, "amy": 2}


def test_pending_counts_lumps_all_unnamed_together():
    """沒取暱稱的全部算同一位 —— 身分的定義跟輪唱借的是同一份（決定二）。"""
    queue = q(("", "a"), (None, "b"), ("   ", "c"))
    assert pending_counts(queue) == {"": 3}


# --- 決定一：算的是「待唱」，不是「今晚總共」 ---

def test_limit_counts_only_songs_still_waiting():
    """
    正在唱的那一首不佔額度。

    這是決定一那句承諾（「等一首唱完就空出一格」）唯一的實作依據：
    額度只看佇列。把已經上台的也算進去的話，唱完之後額度不會回來，
    這個功能就退化成一個「今晚最多幾首」的永久額度。
    """
    queue = q(("小明", "a"), ("小明", "b"))
    assert check(queue, "小明", 2)["allowed"] is False
    # 最前面那一首上台了（從佇列被取走）
    assert check(queue[1:], "小明", 2)["allowed"] is True


def test_rejection_says_when_they_can_add_again():
    """
    決定六：擋下來時要講滿「誰、現在幾首、什麼時候可以再點」。

    next_position 就是第三件事的依據 —— 他排最前面的那一首唱完就空出一格。
    """
    queue = q(("小美", "a"), ("小明", "b"), ("小明", "c"))
    verdict = check(queue, "小明", 2)
    assert verdict["allowed"] is False
    assert verdict["reason"] == REASON_EXCEEDED
    assert verdict["name"] == "小明"
    assert verdict["pending"] == 2
    assert verdict["limit"] == 2
    assert verdict["next_position"] == 2


# --- 決定二：沒取暱稱的那一桶照樣算一份額度 ---

def test_unnamed_bucket_shares_one_allowance():
    """
    沒取暱稱的所有人共用一份額度，而且這件事刻意就是這樣。

    反過來寫（沒取名的不受限）等於公告「把暱稱刪掉就無限點」——
    額度會在第一個發現這件事的人手上當場失效。
    """
    queue = q(("", "a"), ("", "b"))
    verdict = check(queue, "", 2)
    assert verdict["allowed"] is False
    assert verdict["anonymous"] is True


def test_naming_yourself_gets_you_your_own_allowance():
    """
    取暱稱是**有好處**的那一邊：桶子裡別人的歌不算在你頭上。

    這一條是決定二的整個重點。它一旦反過來（取名的受限、沒取名的不受限），
    使用者的最佳策略就是把名字刪掉。
    """
    queue = q(("", "a"), ("", "b"), ("", "c"))
    assert check(queue, "", 3)["allowed"] is False      # 桶子滿了
    assert check(queue, "小明", 3)["allowed"] is True    # 取了名就是自己的 0/3


# --- 決定三：插播不受限，但照樣佔額度 ---

def test_priority_bypasses_the_limit():
    """插播是現場有人按下去的決定，機器的規則不該推翻它（與輪唱一致）。"""
    queue = q(("小明", "a"), ("小明", "b"))
    assert check(queue, "小明", 2, priority=True)["allowed"] is True
    assert check(queue, "小明", 2, priority=True)["reason"] == REASON_PRIORITY


def test_priority_songs_still_consume_the_allowance():
    """
    插播照樣算進待唱數 —— 不算的話它就變成「繞過額度」的那顆按鈕，
    而那顆按鈕在每一張歌卡上。
    """
    queue = [{"queue_id": "a", "requested_by": "小明", "priority": True}]
    assert check(queue, "小明", 1)["pending"] == 1
    assert check(queue, "小明", 1)["allowed"] is False


# --- 決定四：只擋新的，不回頭刪 ---

def test_lowering_the_limit_never_implies_removal():
    """
    上限調到比現有的還低時，check 只回「不准再加」，不回「該刪哪幾首」。

    這一條釘的是介面形狀而不是行為：只要這份結論裡沒有「該刪什麼」，
    上層就沒有東西可以拿來刪別人排好的歌。
    """
    queue = q(("小明", "a"), ("小明", "b"), ("小明", "c"))
    verdict = check(queue, "小明", 1)
    assert verdict["allowed"] is False
    assert verdict["pending"] == 3
    assert not any("remove" in key or "evict" in key for key in verdict)


# --- 決定五：0 = 不限 ---

def test_zero_limit_allows_everything():
    queue = q(*[("小明", f"q{i}") for i in range(30)])
    verdict = check(queue, "小明", 0)
    assert verdict["allowed"] is True
    assert verdict["reason"] == REASON_OFF
    # 不限的時候「還剩幾首」沒有意義，明確給 None 而不是一個假的大數字
    assert verdict["remaining"] is None


# --- check() 與 state() 的一首之差 ---

def test_check_counts_the_song_about_to_be_added():
    """check() 問的是「再加一首可不可以」，所以 remaining 扣掉正要加的那一首。"""
    queue = q(("小明", "a"))
    assert check(queue, "小明", 3)["reason"] == REASON_OK
    assert check(queue, "小明", 3)["remaining"] == 1   # 加完會變 2/3


def test_state_reports_where_they_stand_now():
    """
    state() 問的是「現在站在哪」。兩者共用一個函式的話，點完歌之後回報的
    剩餘量會固定少一首 —— 而使用者會拿它跟畫面上那一行「2/3」對照。
    """
    queue = q(("小明", "a"), ("小明", "b"))
    now = state(queue, "小明", 3)
    assert (now["pending"], now["remaining"], now["full"]) == (2, 1, False)
    assert state(queue, "小明", 2)["full"] is True
    assert state(queue, "小明", 0)["remaining"] is None


# --- 畫面用的全貌 ---

def test_summary_lists_only_people_with_songs_waiting():
    """唱完就沒歌的人不佔額度，列出來寫「0/3」只是佔掉手機上那一行的寬度。"""
    queue = q(("小明", "a"), ("小美", "b"), ("小明", "c"))
    summary = quota_summary(queue, 2)
    assert summary["limit"] == 2
    assert [s["name"] for s in summary["singers"]] == ["小明", "小美"]
    assert [s["pending"] for s in summary["singers"]] == [2, 1]
    assert [s["full"] for s in summary["singers"]] == [True, False]


def test_summary_orders_by_position_in_queue():
    """
    照「他下一首在佇列的第幾位」排 —— 跟輪序那一行同一個順序。
    兩行講的是同一排歌，順序不一樣的話使用者會以為它們是兩份資料。
    """
    queue = q(("小美", "a"), ("小明", "b"), ("小美", "c"))
    assert [s["name"] for s in quota_summary(queue, 3)["singers"]] == ["小美", "小明"]


def test_summary_is_empty_when_queue_is_empty():
    assert quota_summary([], 3) == {"limit": 3, "singers": []}


# --- 例外 ---

def test_quota_exceeded_carries_the_whole_verdict():
    """
    例外要帶著整份結論，API 層才講得出決定六那三件事。
    只丟一句字串的話，那句話就得在 QueueManager 裡拼 —— 那是畫面的工作。
    """
    queue = q(("小明", "a"))
    verdict = check(queue, "小明", 1)
    with pytest.raises(QuotaExceeded) as excinfo:
        raise QuotaExceeded(verdict)
    assert excinfo.value.verdict["pending"] == 1
    assert "小明" in str(excinfo.value)
