"""舞台訊息（跑馬燈）的單元測試。

這個功能真正要守住的不是「字存不存得起來」，是那幾條**不准做的事**：
訊息會自己消失、一則不能無限長、滿了要說得出下一步、釘住的也有天花板。
少了任何一條，最糟的狀況都不是當機，而是「一則沒有人記得、也沒有人知道
怎麼刪掉的字，一直掛在包廂螢幕上」。

時間一律用 `now=` 傳進去，測試不必真的等十分鐘。
"""
from datetime import datetime, timedelta

import pytest

from backend.services import marquee
from backend.services.marquee import MarqueeBoard, MarqueeRejected

T0 = datetime(2026, 9, 17, 21, 0, 0)


def at(minutes: float) -> datetime:
    """開場後第幾分鐘。"""
    return T0 + timedelta(minutes=minutes)


# --- 收斂 ---

def test_clean_text_flattens_and_truncates():
    assert marquee.clean_text("  餐點  到了  ") == "餐點 到了"
    # 換行在舞台那一條上沒有意義（它只有一行高），而貼上來的文字常常帶著換行
    assert marquee.clean_text("第一行\n第二行") == "第一行 第二行"
    assert marquee.clean_text("\r\n\t") == ""
    assert marquee.clean_text(None) == ""
    long_text = "字" * 200
    assert len(marquee.clean_text(long_text)) == marquee.MAX_TEXT_CHARS


def test_coerce_seconds_and_ttl_clamp_and_fall_back():
    assert marquee.coerce_seconds(12) == 12.0
    assert marquee.coerce_seconds(0.1) == marquee.MIN_SHOW_SECONDS
    assert marquee.coerce_seconds(9999) == marquee.MAX_SHOW_SECONDS
    # 看不懂的值退回預設值而不是拋錯：手機端送來怪東西時正確的行為是
    # 「照預設送出去」，不是讓整則訊息發不出去
    assert marquee.coerce_seconds("很久") == marquee.DEFAULT_SHOW_SECONDS
    assert marquee.coerce_seconds(None) == marquee.DEFAULT_SHOW_SECONDS
    assert marquee.coerce_ttl_minutes(30) == 30
    assert marquee.coerce_ttl_minutes(0) == marquee.MIN_TTL_MINUTES
    assert marquee.coerce_ttl_minutes(10**6) == marquee.MAX_TTL_MINUTES
    assert marquee.coerce_ttl_minutes("一下下") == marquee.DEFAULT_TTL_MINUTES


# --- 送出 ---

def test_post_returns_message_with_lifetime():
    board = MarqueeBoard()
    msg = board.post("您的餐點到了", sender="櫃檯", ttl_minutes=10, seconds=8, now=T0)
    assert msg["text"] == "您的餐點到了"
    assert msg["sender"] == "櫃檯"
    assert msg["seconds"] == 8.0
    assert msg["expires_at"] == at(10).isoformat(timespec="seconds")
    assert board.snapshot(T0)["count"] == 1


def test_empty_message_is_rejected_loudly():
    """靜靜地不收是最糟的回應：送出的人會以為螢幕上已經有字了。"""
    board = MarqueeBoard()
    with pytest.raises(MarqueeRejected) as exc:
        board.post("   \n  ", now=T0)
    assert exc.value.reason == "empty"
    assert exc.value.detail["max_chars"] == marquee.MAX_TEXT_CHARS


def test_message_expires_by_itself():
    """「您的餐點到了」在四十分鐘之後才出現，是比沒有訊息更糟的錯誤資訊。"""
    board = MarqueeBoard()
    board.post("您的餐點到了", ttl_minutes=10, now=T0)
    assert board.snapshot(at(9))["count"] == 1
    assert board.snapshot(at(11))["count"] == 0


def test_pinned_message_stays_but_not_forever():
    """釘住是「久一點」，不是「永遠」—— 散場之後還掛著的祝福是下一桌看到的第一個東西。"""
    board = MarqueeBoard()
    board.post("🎂 生日快樂", pinned=True, ttl_minutes=5, now=T0)
    # 釘住的不吃 ttl_minutes，它有自己的天花板
    assert board.snapshot(at(60))["count"] == 1
    assert board.snapshot(at(marquee.PINNED_MAX_HOURS * 60 - 1))["count"] == 1
    assert board.snapshot(at(marquee.PINNED_MAX_HOURS * 60 + 1))["count"] == 0


def test_board_full_is_rejected_with_a_next_step():
    board = MarqueeBoard()
    for i in range(marquee.MAX_MESSAGES):
        board.post(f"訊息 {i}", now=T0)
    with pytest.raises(MarqueeRejected) as exc:
        board.post("再一則", now=T0)
    assert exc.value.reason == "full"
    assert exc.value.detail["max_messages"] == marquee.MAX_MESSAGES


def test_expired_messages_do_not_keep_the_board_full():
    """滿了的那一刻常常有一半是早就該消失的訊息，為了它們擋下一則新的
    會讓人以為這個功能壞了。"""
    board = MarqueeBoard()
    for i in range(marquee.MAX_MESSAGES):
        board.post(f"訊息 {i}", ttl_minutes=5, now=T0)
    msg = board.post("新的一則", now=at(6))
    assert msg["text"] == "新的一則"
    assert board.snapshot(at(6))["count"] == 1


# --- 順序 ---

def test_urgent_first_pinned_last():
    """緊急的插到最前面（那正是「緊急」的全部意思）；
    釘住的排最後 —— 它會待上好幾個小時，每一輪都該讓剛送出來的先講。"""
    board = MarqueeBoard()
    board.post("🎂 生日快樂", pinned=True, now=T0)
    board.post("普通訊息", now=at(1))
    board.post("餐點到了", urgent=True, now=at(2))
    texts = [m["text"] for m in board.messages(at(3))]
    assert texts == ["餐點到了", "普通訊息", "🎂 生日快樂"]


def test_same_priority_keeps_arrival_order():
    board = MarqueeBoard()
    board.post("先來的", now=T0)
    board.post("後來的", now=at(1))
    assert [m["text"] for m in board.messages(at(2))] == ["先來的", "後來的"]


# --- 撤掉 ---

def test_remove_one_message():
    board = MarqueeBoard()
    msg = board.post("打錯字了", now=T0)
    assert board.remove(msg["id"]) is True
    assert board.snapshot(T0)["count"] == 0
    # 撤不到也不該爆掉：畫面要的結果是「它不在了」，而它確實不在了
    assert board.remove(msg["id"]) is False
    assert board.remove("沒有這個 id") is False


def test_clear_can_spare_the_pinned_ones():
    """「把剛剛那幾則清掉」跟「連生日祝福也拿掉」是兩個不同的意思。"""
    board = MarqueeBoard()
    board.post("🎂 生日快樂", pinned=True, now=T0)
    board.post("餐點到了", now=T0)
    assert board.clear(include_pinned=False, now=T0) == 1
    assert [m["text"] for m in board.messages(T0)] == ["🎂 生日快樂"]
    assert board.clear(include_pinned=True, now=T0) == 1
    assert board.snapshot(T0)["count"] == 0


def test_prune_drops_expired_and_reports_how_many():
    board = MarqueeBoard()
    board.post("十分鐘", ttl_minutes=10, now=T0)
    board.post("三十分鐘", ttl_minutes=30, now=T0)
    assert board.prune(at(5)) == 0
    assert board.prune(at(15)) == 1
    assert board.snapshot(at(15))["count"] == 1


def test_broken_expiry_is_treated_as_expired():
    """寧可少播一則，也不要留下一則永遠不會消失、而且沒有人知道怎麼刪的字。"""
    board = MarqueeBoard()
    board.post("壞掉的那一則", now=T0)
    board._messages[0]["expires_at"] = "not-a-time"
    assert board.snapshot(T0)["count"] == 0


def test_snapshot_carries_the_limits_for_the_ui():
    """畫面要講「最多八則、最多 40 字」，那兩個數字得跟著快照走 ——
    寫死在前端的話，改了伺服器的上限就會有一邊在說謊。"""
    snap = MarqueeBoard().snapshot(T0)
    assert snap["max_messages"] == marquee.MAX_MESSAGES
    assert snap["max_chars"] == marquee.MAX_TEXT_CHARS
    assert snap["updated_at"] == T0.isoformat(timespec="seconds")
