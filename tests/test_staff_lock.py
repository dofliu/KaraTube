"""
櫃檯管理鎖的單元測試。

守的是四件事：密碼不存明碼、鎖會自己鎖回去、猜錯要等、以及**一定解得開**
（環境變數救援、壞檔時照樣救得回來）—— 最後那一條是這個功能最容易
在半夜的店裡變成災難的地方。
"""
import json
import time
from datetime import datetime, timedelta

import pytest

from backend.services.staff_lock import (COOLDOWN_STEPS, DEFAULT_AUTO_LOCK_MINUTES,
                                         ENV_PIN, FREE_ATTEMPTS, MAX_AUTO_LOCK_MINUTES,
                                         MAX_SESSION_HOURS, MIN_AUTO_LOCK_MINUTES,
                                         StaffLock, clamp_auto_lock_minutes,
                                         cooldown_seconds, hash_pin, normalize_pin,
                                         verify_pin)


@pytest.fixture
def lock(tmp_path):
    return StaffLock(tmp_path / "staff_lock.json")


# --- PIN 的格式 ---

def test_normalize_pin_accepts_four_to_eight_digits():
    assert normalize_pin("1234") == "1234"
    assert normalize_pin(" 12345678 ") == "12345678"
    assert normalize_pin(123456) == "123456"


def test_normalize_pin_rejects_short_long_and_non_digits():
    assert normalize_pin("123") == ""
    assert normalize_pin("123456789") == ""
    assert normalize_pin("12a4") == ""       # 面板上打不出字母
    assert normalize_pin("") == ""
    assert normalize_pin(None) == ""
    assert normalize_pin(True) == ""         # bool 是 int 的子類


def test_hash_pin_never_keeps_the_plaintext():
    record = hash_pin("1234")
    assert "1234" not in json.dumps(record)
    assert verify_pin(record, "1234")
    assert not verify_pin(record, "1235")
    assert not verify_pin(None, "1234")
    assert not verify_pin(record, "")


def test_hash_pin_salts_differ_per_call():
    a, b = hash_pin("1234"), hash_pin("1234")
    assert a["salt"] != b["salt"]
    assert a["hash"] != b["hash"]


def test_verify_pin_survives_corrupt_records():
    assert not verify_pin({"algo": "pbkdf2_sha256", "salt": "zz", "hash": "aa"}, "1234")
    assert not verify_pin({"algo": "other", "salt": "ab", "hash": "cd"}, "1234")


# --- 預設狀態：什麼都不鎖 ---

def test_fresh_install_is_not_enabled(lock):
    state = lock.state()
    assert state["enabled"] is False
    assert state["locked"] is False
    assert state["pin_set"] is False
    # 沒啟用時任何 token（包含空的）都通得過 —— 家用不受影響
    assert lock.authorize("") is True


def test_unlock_without_enabling_says_so(lock):
    assert lock.unlock("1234")["status"] == "not_enabled"


# --- 設定密碼 ---

def test_set_pin_enables_and_locks_immediately(lock):
    assert lock.set_pin("1234")["status"] == "success"
    state = lock.state()
    assert state["enabled"] and state["locked"] and state["pin_set"]
    # 設完就是鎖著的：設密碼的人已經知道密碼，重打一次是三秒鐘的事
    assert lock.authorize("") is False


def test_set_pin_rejects_bad_format(lock):
    assert lock.set_pin("12")["status"] == "invalid"
    assert lock.state()["enabled"] is False


def test_change_pin_requires_current_pin_or_token(lock):
    lock.set_pin("1234")
    assert lock.set_pin("5678")["status"] == "denied"
    assert lock.set_pin("5678", current_pin="9999")["status"] == "denied"
    assert lock.set_pin("5678", current_pin="1234")["status"] == "success"
    assert lock.unlock("5678")["status"] == "success"


def test_change_pin_with_token_works(lock):
    lock.set_pin("1234")
    token = lock.unlock("1234")["token"]
    assert lock.set_pin("5678", token=token)["status"] == "success"
    # 換完密碼那一次解鎖也結束了（舊 token 不該繼續有效）
    assert lock.authorize(token) is False


# --- 解鎖 / 上鎖 ---

def test_unlock_then_authorize(lock):
    lock.set_pin("1234")
    result = lock.unlock("1234")
    assert result["status"] == "success"
    assert lock.authorize(result["token"]) is True
    assert lock.state()["locked"] is False


def test_wrong_token_never_passes(lock):
    lock.set_pin("1234")
    lock.unlock("1234")
    assert lock.authorize("not-the-token") is False
    assert lock.authorize("") is False


def test_lock_needs_no_credential(lock):
    lock.set_pin("1234")
    token = lock.unlock("1234")["token"]
    assert lock.lock()["status"] == "success"
    assert lock.authorize(token) is False
    # 已經鎖著再按一次也不該出錯
    assert lock.lock()["status"] == "success"


def test_disable_returns_to_home_mode(lock):
    lock.set_pin("1234")
    assert lock.disable()["status"] == "denied"
    assert lock.disable(current_pin="1234")["status"] == "success"
    state = lock.state()
    assert state["enabled"] is False and state["pin_set"] is False
    assert lock.authorize("") is True


# --- 自動上鎖（滑動視窗 + 絕對上限）---

def test_authorize_slides_the_window(lock):
    lock.set_pin("1234")
    start = datetime(2026, 9, 22, 20, 0, 0)
    token = lock.unlock("1234", now=start)["token"]
    # 14 分鐘後動一下 → 續期；再過 10 分鐘（距離原本的到期已超過）仍然有效
    assert lock.authorize(token, now=start + timedelta(minutes=14)) is True
    assert lock.authorize(token, now=start + timedelta(minutes=24)) is True


def test_idle_beyond_auto_lock_minutes_locks(lock):
    lock.set_pin("1234")
    start = datetime(2026, 9, 22, 20, 0, 0)
    token = lock.unlock("1234", now=start)["token"]
    late = start + timedelta(minutes=DEFAULT_AUTO_LOCK_MINUTES + 1)
    assert lock.authorize(token, now=late) is False
    assert lock.state(now=late)["locked"] is True


def test_hard_cap_ends_a_very_long_shift(lock):
    lock.set_pin("1234")
    start = datetime(2026, 9, 22, 18, 0, 0)
    token = lock.unlock("1234", now=start)["token"]
    # 每 5 分鐘動一次，撐過絕對上限之後仍然要鎖
    now = start
    for _ in range(int(MAX_SESSION_HOURS * 60 / 5)):
        now += timedelta(minutes=5)
        lock.authorize(token, now=now)
    assert lock.authorize(token, now=now + timedelta(minutes=5)) is False


def test_tick_reports_the_moment_it_auto_locks(lock):
    lock.set_pin("1234")
    start = datetime(2026, 9, 22, 20, 0, 0)
    lock.unlock("1234", now=start)
    assert lock.tick(now=start + timedelta(minutes=1)) is False
    late = start + timedelta(minutes=DEFAULT_AUTO_LOCK_MINUTES + 1)
    assert lock.tick(now=late) is True
    # 只回報一次（廣播不該每五秒重來一遍）
    assert lock.tick(now=late) is False


def test_auto_lock_minutes_can_be_changed_and_is_clamped(lock):
    lock.set_pin("1234")
    token = lock.unlock("1234")["token"]
    assert lock.set_auto_lock_minutes(30, token=token)["auto_lock_minutes"] == 30
    assert lock.set_auto_lock_minutes(9999, token=token)["auto_lock_minutes"] == MAX_AUTO_LOCK_MINUTES
    assert lock.set_auto_lock_minutes(0, token=token)["auto_lock_minutes"] == MIN_AUTO_LOCK_MINUTES
    assert lock.set_auto_lock_minutes("abc", token=token)["auto_lock_minutes"] == MIN_AUTO_LOCK_MINUTES


def test_auto_lock_minutes_needs_authorization(lock):
    lock.set_pin("1234")
    assert lock.set_auto_lock_minutes(30)["status"] == "denied"


def test_clamp_auto_lock_minutes():
    assert clamp_auto_lock_minutes(15) == 15
    assert clamp_auto_lock_minutes(-5) == MIN_AUTO_LOCK_MINUTES
    assert clamp_auto_lock_minutes(10 ** 6) == MAX_AUTO_LOCK_MINUTES
    assert clamp_auto_lock_minutes(None, 20) == 20


# --- 猜錯要等 ---

def test_cooldown_steps_are_progressive_and_capped():
    assert cooldown_seconds(0) == 0
    assert cooldown_seconds(FREE_ATTEMPTS) == 0
    assert cooldown_seconds(FREE_ATTEMPTS + 1) == COOLDOWN_STEPS[0]
    assert cooldown_seconds(FREE_ATTEMPTS + 2) == COOLDOWN_STEPS[1]
    # 猜一百次也不會等到天荒地老：真正的邊界是「機器在櫃檯後面」
    assert cooldown_seconds(100) == COOLDOWN_STEPS[-1]


def test_wrong_pin_eventually_cools_down(lock):
    lock.set_pin("1234")
    now = datetime(2026, 9, 22, 20, 0, 0)
    for _ in range(FREE_ATTEMPTS):
        assert lock.unlock("0000", now=now)["status"] == "denied"
    blocked = lock.unlock("0000", now=now)
    assert blocked["status"] == "denied" and blocked["retry_after"] > 0
    # 冷卻中連正確的密碼都先擋下來（不然冷卻可以被繞過）
    assert lock.unlock("1234", now=now)["status"] == "cooldown"
    later = now + timedelta(seconds=COOLDOWN_STEPS[0] + 1)
    assert lock.unlock("1234", now=later)["status"] == "success"


def test_successful_unlock_clears_the_counter(lock):
    lock.set_pin("1234")
    now = datetime(2026, 9, 22, 20, 0, 0)
    lock.unlock("0000", now=now)
    lock.unlock("1234", now=now)
    assert lock.state(now=now)["attempts_left"] == FREE_ATTEMPTS


# --- 持久化 ---

def test_pin_survives_restart_but_the_unlock_does_not(tmp_path):
    path = tmp_path / "staff_lock.json"
    first = StaffLock(path)
    first.set_pin("4321")
    token = first.unlock("4321")["token"]
    assert first.authorize(token) is True

    again = StaffLock(path)                 # 重開伺服器
    assert again.state()["enabled"] is True
    assert again.state()["locked"] is True  # 重開＝有人碰得到機器，正確狀態是鎖著
    assert again.authorize(token) is False
    assert again.unlock("4321")["status"] == "success"


def test_saved_file_has_no_plaintext_pin(tmp_path):
    path = tmp_path / "staff_lock.json"
    StaffLock(path).set_pin("864213")
    assert "864213" not in path.read_text(encoding="utf-8")


def test_auto_lock_minutes_persist(tmp_path):
    path = tmp_path / "staff_lock.json"
    first = StaffLock(path)
    first.set_pin("1234")
    first.set_auto_lock_minutes(45, current_pin="1234")
    assert StaffLock(path).state()["auto_lock_minutes"] == 45


# --- 壞檔 fail-closed ---

def test_corrupt_file_stays_locked(tmp_path):
    path = tmp_path / "staff_lock.json"
    path.write_text("{ 這不是 JSON", encoding="utf-8")
    lock = StaffLock(path)
    state = lock.state()
    assert state["enabled"] is True and state["locked"] is True and state["broken"] is True
    # 壞檔就自動解鎖的話，這把鎖的保證會變成「檔案壞掉就沒事了」
    assert lock.authorize("") is False
    result = lock.unlock("1234")
    assert result["status"] == "denied" and result.get("broken") is True
    assert ENV_PIN in result["message"]      # 現場唯一有用的那句話


def test_enabled_without_pin_is_treated_as_corrupt(tmp_path):
    path = tmp_path / "staff_lock.json"
    path.write_text(json.dumps({"version": 1, "enabled": True}), encoding="utf-8")
    assert StaffLock(path).state()["broken"] is True


def test_corrupt_file_is_left_alone_for_a_human(tmp_path):
    path = tmp_path / "staff_lock.json"
    path.write_text("{ 壞掉的內容", encoding="utf-8")
    StaffLock(path)
    # 蓋掉的話連「本來的 PIN 是什麼」都救不回來了
    assert path.read_text(encoding="utf-8") == "{ 壞掉的內容"


def test_resetting_the_pin_repairs_a_corrupt_book(tmp_path, monkeypatch):
    path = tmp_path / "staff_lock.json"
    path.write_text("壞檔", encoding="utf-8")
    lock = StaffLock(path)
    monkeypatch.setenv(ENV_PIN, "999999")
    token = lock.unlock("999999")["token"]        # 救援密碼進得去
    assert lock.set_pin("1234", token=token)["status"] == "success"
    assert lock.state()["broken"] is False
    monkeypatch.delenv(ENV_PIN)
    assert lock.unlock("1234")["status"] == "success"


# --- 救援（忘記密碼）---

def test_env_pin_always_works(lock, monkeypatch):
    lock.set_pin("1234")
    monkeypatch.setenv(ENV_PIN, "778899")
    assert lock.unlock("778899")["status"] == "success"
    assert lock.unlock("1234")["status"] == "success"   # 原來的也還在


def test_env_pin_does_not_enable_the_lock_by_itself(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_PIN, "778899")
    lock = StaffLock(tmp_path / "staff_lock.json")
    assert lock.state()["enabled"] is False
    assert lock.authorize("") is True


def test_env_pin_shows_up_in_state(lock, monkeypatch):
    monkeypatch.setenv(ENV_PIN, "778899")
    assert lock.state()["env_pin_set"] is True
    monkeypatch.delenv(ENV_PIN)
    assert lock.state()["env_pin_set"] is False


def test_state_never_leaks_the_secret(lock):
    lock.set_pin("135790")
    token = lock.unlock("135790")["token"]
    blob = json.dumps(lock.state(), ensure_ascii=False)
    assert "135790" not in blob
    assert token not in blob


# --- 併發 ---

def test_concurrent_authorize_is_safe(lock):
    import threading
    lock.set_pin("1234")
    token = lock.unlock("1234")["token"]
    results = []

    def hammer():
        for _ in range(50):
            results.append(lock.authorize(token))
            time.sleep(0)

    threads = [threading.Thread(target=hammer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(results) and len(results) == 200
