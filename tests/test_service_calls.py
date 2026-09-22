"""服務鈴（包廂呼叫櫃檯）的單元測試。

這個功能真正要守住的不是「單子存不存得起來」，是那幾條**按第二次的時候
才看得到**的規則：

  * 再按一次是併進同一張，不是開第二張（櫃檯要一趟送完，不是五趟）。
  * 併單不重設等待時間（等最久的那一桌不該因為按最多次而排到最後面）。
  * 「已送出」與「櫃檯收到了」是兩種狀態，不是同一種。
  * 「客人自己取消」與「櫃檯處理完了」在紀錄上分得開。
  * 開太久的單標成過期，不是默默刪掉（刪掉客人會以為送出去了）。

少了任何一條，最糟的狀況都不是當機，而是「一顆按了之後看不出差別的鈴」——
然後那個人會推開包廂的門走出去，而這顆鍵存在的意義就是讓他不必。

時間一律用 `now=` 傳進去，測試不必真的等 45 分鐘。
"""
import json
from datetime import datetime, timedelta

import pytest

from backend.services import service_calls
from backend.services.service_calls import ServiceCallRejected, ServiceDesk

T0 = datetime(2026, 9, 22, 21, 0, 0)


def at(minutes: float) -> datetime:
    """開場後第幾分鐘。"""
    return T0 + timedelta(minutes=minutes)


# --- 收斂 ---

def test_clean_line_flattens_and_truncates():
    assert service_calls.clean_line("  少冰  兩杯 ", 40) == "少冰 兩杯"
    # 櫃檯那一列只有一行高，貼上來的換行原樣存起來會變成看不出來的空白
    assert service_calls.clean_line("第一行\n第二行", 40) == "第一行 第二行"
    assert service_calls.clean_line(None, 40) == ""
    assert len(service_calls.clean_line("字" * 200, 40)) == 40


def test_clean_items_keeps_spec_order_dedupes_and_drops_unknown():
    # 順序照 ITEM_SPEC，不照使用者按的先後 —— 兩張同樣內容的單要長得一樣
    assert service_calls.clean_items(["drink", "food", "drink"]) == ["food", "drink"]
    # 認不得的鍵直接丟掉：舊版手機頁面留在別人的分頁裡是常態，
    # 為了一個過時的鍵讓整張單送不出去，代價遠大於少一個品項
    assert service_calls.clean_items(["food", "teleport"]) == ["food"]
    assert service_calls.clean_items("food") == ["food"]
    assert service_calls.clean_items(None) == []
    assert service_calls.clean_items(123) == []


def test_coerce_stale_minutes_clamps_and_falls_back():
    assert service_calls.coerce_stale_minutes(60) == 60
    assert service_calls.coerce_stale_minutes(1) == service_calls.MIN_STALE_MINUTES
    assert service_calls.coerce_stale_minutes(99999) == service_calls.MAX_STALE_MINUTES
    assert service_calls.coerce_stale_minutes("很久") == service_calls.DEFAULT_STALE_MINUTES


# --- 按下去 ---

def test_ring_opens_one_call_with_a_clock():
    desk = ServiceDesk(None)
    call = desk.ring(["food"], note="少冰", by="阿明", now=T0)
    assert call["status"] == service_calls.STATUS_WAITING
    assert call["items"] == ["food"]
    assert call["note"] == "少冰"
    assert call["presses"] == 1
    assert call["merged"] is False
    assert call["waited_seconds"] == 0.0
    # 等待時間會自己走 —— 停住的等待時間看起來像系統當掉了
    assert desk.open_call(now=at(3))["waited_seconds"] == pytest.approx(180.0)


def test_ring_with_nothing_picked_is_rejected_with_the_menu():
    """空的單不靜靜收下。回絕時帶著品項清單，畫面才講得出「選一項再按」。"""
    desk = ServiceDesk(None)
    with pytest.raises(ServiceCallRejected) as exc:
        desk.ring([], now=T0)
    assert exc.value.reason == "empty"
    assert [s["key"] for s in exc.value.detail["items"]] == service_calls.ITEM_KEYS
    assert desk.open_call(now=T0) is None


def test_second_press_merges_instead_of_opening_a_second_call():
    """決定一：櫃檯看到的是一列，不是五列一模一樣的東西。"""
    desk = ServiceDesk(None)
    desk.ring(["food"], by="阿明", now=T0)
    again = desk.ring(["drink"], by="小美", now=at(3))
    assert again["merged"] is True
    assert again["items"] == ["food", "drink"]
    assert again["presses"] == 2
    assert again["pressed_by"] == ["阿明", "小美"]
    # 併單之後仍然只有一張開著（而且是同一張）
    assert desk.open_call(now=at(3))["id"] == again["id"]
    assert desk.history(now=at(3)) == []


def test_merging_does_not_reset_the_wait_clock():
    """決定二：等最久的那一桌不該因為按最多次而排到最後面。"""
    desk = ServiceDesk(None)
    desk.ring(["service"], now=T0)
    merged = desk.ring(["service"], now=at(8))
    # 從**第一次**按下去算起（8 分鐘），不是從剛剛那一次（0 分鐘）
    assert merged["waited_seconds"] == pytest.approx(480.0)
    assert merged["presses"] == 2


def test_merging_appends_the_note_instead_of_overwriting_it():
    """先寫「少冰」再寫「兩杯」的人要的是兩件事都送到。"""
    desk = ServiceDesk(None)
    desk.ring(["drink"], note="少冰", now=T0)
    merged = desk.ring(["drink"], note="兩杯", now=at(1))
    assert "少冰" in merged["note"] and "兩杯" in merged["note"]
    # 重複送同一句不會越接越長
    again = desk.ring(["drink"], note="兩杯", now=at(2))
    assert again["note"].count("兩杯") == 1


def test_items_label_reads_like_a_sentence():
    assert service_calls.items_label(["food", "drink"]) == "送餐／加點＋加冰塊／飲料"
    assert service_calls.items_label([]) == ""


# --- 狀態機 ---

def test_ack_is_its_own_state_not_a_side_effect_of_done():
    """決定三：「已送出」不等於「有人看到了」，而那正是客人還會不會再按一次的分水嶺。"""
    desk = ServiceDesk(None)
    desk.ring(["gear"], now=T0)
    acked = desk.ack(by="櫃檯", now=at(1))
    assert acked["status"] == service_calls.STATUS_ACKED
    assert acked["acked_by"] == "櫃檯"
    # 收下之後單子還開著（現實世界的事還沒做完）
    assert desk.open_call(now=at(1))["is_open"] is True
    # 重複按不報錯：櫃檯兩個人同時看到同一張單是常態
    assert desk.ack(now=at(2))["status"] == service_calls.STATUS_ACKED


def test_resolve_closes_with_a_reply_and_keeps_the_record():
    desk = ServiceDesk(None)
    desk.ring(["food"], now=T0)
    done = desk.resolve(reply="餐點五分鐘後到", by="櫃檯", now=at(4))
    assert done["status"] == service_calls.STATUS_DONE
    assert done["reply"] == "餐點五分鐘後到"
    assert desk.open_call(now=at(4)) is None
    rows = desk.history(now=at(4))
    assert len(rows) == 1 and rows[0]["status"] == service_calls.STATUS_DONE


def test_cancel_and_done_are_different_kinds_of_closed():
    """決定五：揉成一個「已結案」的話，「今晚有幾單沒服務到」就永遠問不出來。"""
    desk = ServiceDesk(None)
    desk.ring(["food"], now=T0)
    desk.resolve(now=at(2))
    desk.ring(["drink"], now=at(5))
    cancelled = desk.cancel(by="阿明", now=at(6))
    assert cancelled["status"] == service_calls.STATUS_CANCELLED
    snap = desk.snapshot(now=at(6))
    assert snap["unserved_count"] == 1
    assert snap["history_count"] == 2


def test_acting_on_a_closed_call_says_so_instead_of_doing_nothing():
    desk = ServiceDesk(None)
    with pytest.raises(ServiceCallRejected) as exc:
        desk.resolve(now=T0)
    assert exc.value.reason == "none_open"


def test_a_stale_screen_cannot_close_the_wrong_call():
    """
    客人取消之後馬上又按了一張新的，而櫃檯那個畫面還停在舊的。

    照著按下去會把**新的**那一張標成完成，而那件事根本還沒做 ——
    所以擋下來，並且把現在真正開著的那一張的 id 一起回去。
    """
    desk = ServiceDesk(None)
    first = desk.ring(["food"], now=T0)
    desk.cancel(now=at(1))
    second = desk.ring(["drink"], now=at(2))
    with pytest.raises(ServiceCallRejected) as exc:
        desk.resolve(call_id=first["id"], now=at(3))
    assert exc.value.reason == "stale"
    assert exc.value.detail["current_id"] == second["id"]
    # 新的那一張還好好地開著
    assert desk.open_call(now=at(3))["id"] == second["id"]


# --- 過期 ---

def test_open_call_expires_instead_of_staying_or_vanishing():
    """
    決定七：默默刪掉客人會以為送出去了，繼續亮著則會讓昨晚那一桌的單
    出現在今天早上的櫃檯上。所以是「標成過期」。
    """
    desk = ServiceDesk(None, stale_minutes=45)
    desk.ring(["service"], now=T0)
    assert desk.open_call(now=at(44)) is not None
    assert desk.open_call(now=at(46)) is None
    rows = desk.history(now=at(46))
    assert len(rows) == 1 and rows[0]["status"] == service_calls.STATUS_EXPIRED


def test_expire_stale_is_what_the_heartbeat_calls():
    desk = ServiceDesk(None, stale_minutes=10)
    desk.ring(["clean"], now=T0)
    assert desk.expire_stale(now=at(5)) is None
    expired = desk.expire_stale(now=at(11))
    assert expired["status"] == service_calls.STATUS_EXPIRED
    # 已經收掉了就不會再收第二次
    assert desk.expire_stale(now=at(12)) is None


def test_a_broken_created_at_expires_rather_than_sticking_forever():
    """寧可讓客人重按一次（五秒），也不要留下一張沒有人知道怎麼關掉的單。"""
    desk = ServiceDesk(None)
    desk.ring(["bill"], now=T0)
    desk._open["created_at"] = "不是時間"
    assert desk.open_call(now=at(1)) is None
    assert desk.history(now=at(1))[0]["status"] == service_calls.STATUS_EXPIRED


def test_set_stale_minutes_does_not_revive_closed_calls():
    """一張昨天被標成過期的單，不會因為今天把門檻調長了就亮回來。"""
    desk = ServiceDesk(None, stale_minutes=10)
    desk.ring(["service"], now=T0)
    desk.expire_stale(now=at(11))
    desk.set_stale_minutes(240)
    assert desk.open_call(now=at(12)) is None
    assert desk.snapshot(now=at(12))["stale_minutes"] == 240


# --- 快照 ---

def test_snapshot_carries_the_menu_and_the_limits():
    """兩端載的是同一份：客人按的是「加冰塊」，櫃檯不能看到「飲料」。"""
    desk = ServiceDesk(None)
    snap = desk.snapshot(now=T0)
    assert snap["waiting"] is False and snap["call"] is None
    assert [s["key"] for s in snap["items"]] == service_calls.ITEM_KEYS
    assert snap["max_note_chars"] == service_calls.MAX_NOTE_CHARS
    assert snap["stale_minutes"] == service_calls.DEFAULT_STALE_MINUTES


def test_history_is_capped_and_newest_first():
    desk = ServiceDesk(None, max_history=3)
    for i in range(5):
        desk.ring(["service"], note=f"第 {i} 次", now=at(i))
        desk.resolve(now=at(i + 0.1))
    rows = desk.history(limit=10, now=at(9))
    assert len(rows) == 3
    assert rows[0]["note"] == "第 4 次"


def test_clear_history_leaves_the_open_call_alone():
    """按「清除紀錄」不會讓現實世界裡那杯冰塊自己送到。"""
    desk = ServiceDesk(None)
    desk.ring(["food"], now=T0)
    desk.resolve(now=at(1))
    desk.ring(["drink"], now=at(2))
    assert desk.clear_history() == 1
    assert desk.snapshot(now=at(3))["history_count"] == 0
    assert desk.open_call(now=at(3)) is not None


# --- 持久化 ---

def test_open_call_survives_a_restart(tmp_path):
    """一張還開著的單對應的是現實世界裡一件還沒做完的事（見決定七）。"""
    path = tmp_path / "service_calls.json"
    desk = ServiceDesk(path)
    desk.ring(["food", "drink"], note="少冰", by="阿明", now=T0)
    desk.resolve(now=at(1))
    desk.ring(["gear"], now=at(2))

    revived = ServiceDesk(path)
    call = revived.open_call(now=at(3))
    assert call is not None and call["items"] == ["gear"]
    # 等待時間照樣從第一次按下去算起，不從重開機那一刻算
    assert call["waited_seconds"] == pytest.approx(60.0)
    assert revived.snapshot(now=at(3))["history_count"] == 1


def test_a_corrupt_state_file_starts_empty_instead_of_blowing_up(tmp_path):
    """
    這裡沒有任何使用者記在腦子裡的東西（不像歌號簿），所以壞檔從空的開始。
    最壞的結果是一張沒服務到的單消失，而客人一分鐘之內就會再按一次。
    """
    path = tmp_path / "service_calls.json"
    path.write_text("{壞掉的 json", encoding="utf-8")
    desk = ServiceDesk(path)
    assert desk.open_call(now=T0) is None
    # 還能正常用（不是整個功能停掉）
    assert desk.ring(["service"], now=T0)["status"] == service_calls.STATUS_WAITING
    assert json.loads(path.read_text(encoding="utf-8"))["open"]["items"] == ["service"]


def test_a_state_file_that_is_a_list_is_also_survivable(tmp_path):
    path = tmp_path / "service_calls.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    desk = ServiceDesk(path)
    assert desk.snapshot(now=T0)["history_count"] == 0
