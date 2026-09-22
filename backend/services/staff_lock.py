"""
櫃檯管理鎖 (Staff Lock)：把「機器的事」跟「唱歌的事」分開

商用點歌機都有這一道：包廂裡的那台面板可以點歌、切歌、調音效，但是**刪曲庫、
清排行、改機台設定、開關計時**要櫃檯的密碼。KaraTube 到 v1.19 為止沒有這一道
—— 掃到 QR 的每一支手機都打得到 `DELETE /api/cache/{song_id}` 與
`POST /api/settings`。家裡唱歌無所謂（在場的都是自己人），但這正是
「可以放到店裡」與「只能放在家裡」之間那條線。

功能本身很小（一組 PIN、一個 token），難的全部在**鎖什麼、不鎖什麼**：

1. **唱歌的事一律不鎖。** 點歌、插播、切歌、重唱、拖曳排序、調音量與效果、
   評分、收藏、查歌、錄自己那一次 —— 這些永遠不需要密碼。理由很直接：
   客人付錢就是為了做這些，而櫃檯不一定在。鎖錯一條的代價不是「多按一次密碼」，
   是**整個晚上沒有人能唱歌**，而且沒有人知道要去哪裡解。
   所以這把鎖的清單寫在 `access_policy.py` 裡，一條一條列，新加的路由不歸類
   就會讓測試紅燈（見 tests/test_access_policy.py）—— 預設不是「鎖起來」，
   因為忘記歸類的代價是不對稱的。

2. **鎖的是「跨場次」與「整台機器」的動作。** 刪快取（下一組客人就找不到那首
   歌了）、清點唱排行與已唱歷史（跨場次的資料）、改系統設定、排程預處理、
   包廂計時與舞台訊息（本來就是櫃檯的工具）、清空所有錄音。判準是
   「這個動作會不會影響到下一組客人，或者做完就回不去了」。

3. **上鎖永遠不需要密碼。** 關門不需要鑰匙 —— 櫃檯做完事按一下就鎖回去，
   不必再打一次 PIN。要打密碼才能鎖的話，忙起來就沒有人鎖了。

4. **自己會鎖回去。** 櫃檯忘記上鎖是常態（他被叫去送餐了），所以 token 是
   **滑動視窗**：每做一個受保護的動作就續一次，閒置超過 `auto_lock_minutes`
   自動上鎖。另外有一個絕對上限（`MAX_SESSION_HOURS`，一個班次），
   免得一台一直在用的櫃檯機整週都是解鎖狀態。

5. **重開機一律回到上鎖。** token 只在記憶體裡。伺服器重開＝有人碰得到機器，
   那時候的正確狀態是鎖著，而不是沿用上一次的解鎖。

6. **忘記 PIN 一定要有解。** 沒有救援手段的鎖等於把機器變成磚頭，而這台機器
   在店裡、半夜、客人在等。兩條路都需要**實體碰得到伺服器**（這正是我們要的
   安全邊界）：環境變數 `KARATUBE_STAFF_PIN` 覆寫，或把 `cache/staff_lock.json`
   刪掉。兩條都寫在 docs/DEPLOYMENT.md 與設定頁上。

7. **壞檔 fail-closed。** 其他持久化資料（點唱統計、評分歷史）壞掉的做法是
   「重新開始」，因為重算一次就回來了。鎖不行 —— 壞檔就自動解鎖的話，
   這把鎖的保證會變成「只要那個檔案壞掉就沒事了」。所以讀不出來時維持上鎖、
   明說「請用環境變數解鎖或刪除檔案重設」，救援路徑照樣有效。

8. **這把鎖擋的是誤觸與跨場次的破壞，不是惡意。** 包廂的 Wi-Fi 上沒有身分，
   任何一支手機都可以假裝是另一支。PIN 走 PBKDF2 雜湊（不存明碼）、猜錯有
   遞增冷卻，但真正的安全邊界仍然是「那台伺服器放在櫃檯後面」。
   文件上照實講，不要讓人以為這是可以對外網開放的門。
"""
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("KaraTube.StaffLock")

# PIN 的長度。四碼是包廂面板的慣例（櫃檯要在客人面前很快按完），
# 八碼是上限 —— 再長就會有人寫在紙上貼在機器旁邊，那比四碼更不安全。
MIN_PIN_DIGITS = 4
MAX_PIN_DIGITS = 8

# 閒置多久自動上鎖。15 分鐘是「櫃檯去送個餐回來還在解鎖狀態」與
# 「被叫走之後那台面板一直開著」之間的位置。
DEFAULT_AUTO_LOCK_MINUTES = 15
MIN_AUTO_LOCK_MINUTES = 1
MAX_AUTO_LOCK_MINUTES = 240

# 解鎖狀態的絕對上限（小時）。滑動視窗會一直續期，所以要有一個天花板：
# 一個班次結束時，那把鎖一定回到上鎖狀態，不管中間有多忙。
MAX_SESSION_HOURS = 8

# 猜錯幾次之前不罰。三次是「手殘 + 記錯一碼」的合理額度；
# 從第四次開始每次都要等，等待時間遞增。
FREE_ATTEMPTS = 3
# 冷卻秒數（第 4、5、6... 次猜錯）。上限刻意只有五分鐘：
# 真正的安全邊界是實體接觸，而無上限的冷卻會變成「任何人都能讓櫃檯進不去」。
COOLDOWN_STEPS = (5, 15, 60, 180, 300)

# 救援用的環境變數。設了它不會自動啟用鎖，只是多一把備用鑰匙 ——
# 忘記 PIN 的時候不用刪檔案（刪檔案會連 auto_lock_minutes 一起重設）。
ENV_PIN = "KARATUBE_STAFF_PIN"

# PBKDF2 的迭代次數。PIN 只有四到八碼、字元集只有數字，暴力破解的成本
# 幾乎全靠這個數字撐 —— 但驗證發生在人按下確認之後，慢個 0.1 秒沒有人感覺得到。
PBKDF2_ROUNDS = 200_000
PBKDF2_ALGO = "pbkdf2_sha256"

# 檔案格式版本。改了雜湊參數或欄位就 +1。
LOCK_VERSION = 1

TOKEN_BYTES = 24


def normalize_pin(raw: Any) -> str:
    """
    把使用者打的那一串變成 PIN，認不得回空字串。

    只收數字：這組密碼是在包廂面板的數字鍵盤上打的，允許字母只會讓
    「在手機上設好、在面板上打不出來」這種事發生一次就毀掉整個功能。
    """
    if isinstance(raw, bool):  # bool 是 int 的子類，先擋掉
        return ""
    text = str(raw if raw is not None else "").strip()
    if not text.isdigit():
        return ""
    if not (MIN_PIN_DIGITS <= len(text) <= MAX_PIN_DIGITS):
        return ""
    return text


def clamp_auto_lock_minutes(value: Any, fallback: int = DEFAULT_AUTO_LOCK_MINUTES) -> int:
    """自動上鎖的分鐘數夾回合法範圍。設定頁是給人用的，滑錯一格不該回 500。"""
    try:
        minutes = int(float(value))
    except (TypeError, ValueError):
        return fallback
    return max(MIN_AUTO_LOCK_MINUTES, min(MAX_AUTO_LOCK_MINUTES, minutes))


def cooldown_seconds(failed: int) -> int:
    """猜錯 `failed` 次之後要等幾秒。前 FREE_ATTEMPTS 次不罰。"""
    over = max(0, int(failed) - FREE_ATTEMPTS)
    if over <= 0:
        return 0
    return COOLDOWN_STEPS[min(over, len(COOLDOWN_STEPS)) - 1]


def hash_pin(pin: str, salt: Optional[str] = None, rounds: int = PBKDF2_ROUNDS) -> Dict[str, Any]:
    """PIN 的雜湊紀錄。明碼一秒都不存 —— 那個檔案會跟著備份到處跑。"""
    use_salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"),
                                 bytes.fromhex(use_salt), rounds)
    return {"algo": PBKDF2_ALGO, "rounds": rounds, "salt": use_salt, "hash": digest.hex()}


def verify_pin(record: Optional[Dict[str, Any]], pin: str) -> bool:
    """對一組雜湊紀錄驗 PIN。比對用 compare_digest（時間差也是資訊）。"""
    if not record or not pin:
        return False
    try:
        salt = str(record.get("salt", ""))
        rounds = int(record.get("rounds", PBKDF2_ROUNDS))
        expected = str(record.get("hash", ""))
        if record.get("algo") != PBKDF2_ALGO or not salt or not expected:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"),
                                     bytes.fromhex(salt), rounds).hex()
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def env_pin() -> str:
    """環境變數裡的救援 PIN（沒設或格式不對就當成沒有）。"""
    return normalize_pin(os.environ.get(ENV_PIN, ""))


class StaffLock:
    """
    一把鎖：啟用狀態、PIN、目前這一次解鎖的 token。

    執行緒安全（FastAPI 的 threadpool 與心跳迴圈會同時碰到它）。
    token 只在記憶體裡，重開機就回到上鎖 —— 見模組說明第 5 點。
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._enabled = False
        self._pin: Optional[Dict[str, Any]] = None
        self._auto_lock_minutes = DEFAULT_AUTO_LOCK_MINUTES
        self._broken = False           # 檔案讀不出來（fail-closed，見第 7 點）
        self._token = ""               # 目前這一次解鎖
        self._expires_at: Optional[datetime] = None   # 閒置到期（滑動）
        self._hard_expires_at: Optional[datetime] = None  # 這一次解鎖的絕對上限
        self._failed = 0
        self._cooldown_until: Optional[datetime] = None
        self._load()

    # --- 持久化 ---

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("staff_lock.json 不是物件")
            self._enabled = bool(data.get("enabled"))
            pin = data.get("pin")
            self._pin = pin if isinstance(pin, dict) and pin.get("hash") else None
            self._auto_lock_minutes = clamp_auto_lock_minutes(
                data.get("auto_lock_minutes"), DEFAULT_AUTO_LOCK_MINUTES)
            if self._enabled and not self._pin:
                # 啟用中卻沒有 PIN：這個組合解不開，跟壞檔是同一種處境
                raise ValueError("staff_lock.json 啟用中但沒有 PIN")
        except Exception as e:
            # 壞檔維持上鎖（fail-closed）。原檔**不動** —— 讓人去修或還原備份，
            # 蓋掉的話連「本來的 PIN 是什麼」都救不回來了。
            logger.error(f"櫃檯管理鎖讀取失敗，維持上鎖：{e}")
            self._broken = True
            self._enabled = True
            self._pin = None

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": LOCK_VERSION,
                "enabled": self._enabled,
                "pin": self._pin,
                "auto_lock_minutes": self._auto_lock_minutes,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except Exception as e:
            logger.error(f"櫃檯管理鎖寫入失敗：{e}")

    # --- 狀態 ---

    def _unlocked(self, now: datetime) -> bool:
        """現在是解鎖狀態嗎（沒啟用也算解鎖：所有動作都通）。"""
        if not self._enabled:
            return True
        if not self._token:
            return False
        if self._expires_at and now >= self._expires_at:
            return False
        if self._hard_expires_at and now >= self._hard_expires_at:
            return False
        return True

    def _clear_session(self) -> None:
        self._token = ""
        self._expires_at = None
        self._hard_expires_at = None

    def state(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """
        給前端畫鎖頭用的整包狀態。**永遠不含 PIN 或 token**。

        `recoverable` 讓設定頁決定要不要把救援說明印出來 —— 壞檔的時候那段話
        是現場唯一有用的資訊（而那時候使用者正在看一個他解不開的鎖）。
        """
        now = now or datetime.now()
        with self._lock:
            unlocked = self._unlocked(now)
            cooldown = 0
            if self._cooldown_until and now < self._cooldown_until:
                cooldown = int((self._cooldown_until - now).total_seconds()) + 1
            remaining = 0
            if unlocked and self._enabled and self._expires_at:
                remaining = max(0, int((self._expires_at - now).total_seconds()))
            return {
                "enabled": self._enabled,
                "locked": self._enabled and not unlocked,
                "pin_set": bool(self._pin),
                "broken": self._broken,
                "auto_lock_minutes": self._auto_lock_minutes,
                "unlock_seconds_left": remaining,
                "cooldown_seconds": cooldown,
                "attempts_left": max(0, FREE_ATTEMPTS - self._failed) if self._enabled else 0,
                "env_pin_set": bool(env_pin()),
                "recoverable": True,
                "env_var": ENV_PIN,
                "file": str(self.path),
            }

    # --- 授權（中介層每一個受保護的請求都會呼叫一次）---

    def authorize(self, token: Any, now: Optional[datetime] = None) -> bool:
        """
        這個 token 現在通得過嗎？通得過就順便續期（滑動視窗）。

        續期只發生在**受保護的動作**上，不是每一次輪詢 —— 點歌台每五秒問一次
        狀態就永遠不會自動上鎖的話，這個功能等於沒有。
        """
        now = now or datetime.now()
        with self._lock:
            if not self._enabled:
                return True
            supplied = str(token or "")
            if not supplied or not self._token:
                return False
            if not hmac.compare_digest(supplied, self._token):
                return False
            if not self._unlocked(now):
                self._clear_session()
                return False
            self._expires_at = now + timedelta(minutes=self._auto_lock_minutes)
            if self._hard_expires_at and self._expires_at > self._hard_expires_at:
                self._expires_at = self._hard_expires_at
            return True

    def tick(self, now: Optional[datetime] = None) -> bool:
        """
        時間自己過去了：到期就上鎖，回傳「這一次有沒有真的鎖上」。

        心跳迴圈呼叫它，好讓自動上鎖是**被看見的**（每支手機上的鎖頭立刻變色），
        而不是等到下一次有人按了什麼才發現自己早就被鎖在外面。
        """
        now = now or datetime.now()
        with self._lock:
            if not self._enabled or not self._token:
                return False
            if self._unlocked(now):
                return False
            self._clear_session()
            return True

    # --- 解鎖 / 上鎖 ---

    def unlock(self, pin: Any, now: Optional[datetime] = None) -> Dict[str, Any]:
        """
        打 PIN 解鎖。回傳 `{"status": ...}`：success / denied / cooldown / not_enabled。

        環境變數裡的救援 PIN 一律有效（也包含壞檔的時候）—— 見模組說明第 6 點。
        """
        now = now or datetime.now()
        with self._lock:
            if not self._enabled:
                return {"status": "not_enabled", "message": "櫃檯管理鎖沒有啟用"}
            if self._cooldown_until and now < self._cooldown_until:
                wait = int((self._cooldown_until - now).total_seconds()) + 1
                return {"status": "cooldown", "retry_after": wait,
                        "message": f"密碼錯太多次，請等 {wait} 秒再試"}

            candidate = normalize_pin(pin)
            rescue = env_pin()
            ok = bool(candidate) and (
                verify_pin(self._pin, candidate)
                or (bool(rescue) and hmac.compare_digest(candidate, rescue))
            )
            if not ok:
                self._failed += 1
                wait = cooldown_seconds(self._failed)
                if wait:
                    self._cooldown_until = now + timedelta(seconds=wait)
                left = max(0, FREE_ATTEMPTS - self._failed)
                if self._broken:
                    # 壞檔時「密碼錯誤」是誤導：那組 PIN 可能完全正確，
                    # 只是本子讀不出來。照實說，並指出唯一還有效的那條路。
                    return {"status": "denied", "broken": True, "retry_after": wait,
                            "attempts_left": left,
                            "message": f"密碼檔讀不出來，只有環境變數 {ENV_PIN} 的救援密碼有效"}
                return {"status": "denied", "retry_after": wait, "attempts_left": left,
                        "message": (f"密碼錯誤，請等 {wait} 秒再試" if wait else "密碼錯誤")}

            self._failed = 0
            self._cooldown_until = None
            self._token = secrets.token_urlsafe(TOKEN_BYTES)
            self._hard_expires_at = now + timedelta(hours=MAX_SESSION_HOURS)
            self._expires_at = now + timedelta(minutes=self._auto_lock_minutes)
            # 呼叫端自己補 state()（在這裡再拿一次鎖只是多繞一圈）
            return {"status": "success", "token": self._token,
                    "expires_in": self._auto_lock_minutes * 60}

    def lock(self) -> Dict[str, Any]:
        """上鎖。永遠不需要密碼（關門不需要鑰匙），已經鎖著再按也不會出錯。"""
        with self._lock:
            self._clear_session()
            return {"status": "success"}

    # --- 設定 PIN / 停用 / 調整自動上鎖 ---

    def _authorized_for_change(self, current_pin: Any, token: Any,
                               now: datetime) -> bool:
        """
        改設定的憑據：目前這一次的 token，或是再打一次現行 PIN。

        兩條都收，是因為兩種現場都存在：已經解鎖的櫃檯機（不該再問一次），
        以及從手機上直接改（那支手機沒有 token）。
        """
        if not self._enabled:
            return True   # 還沒啟用：第一次設定不需要憑據（也還沒有東西可保護）
        if self.authorize(token, now):
            return True
        candidate = normalize_pin(current_pin)
        rescue = env_pin()
        return bool(candidate) and (
            verify_pin(self._pin, candidate)
            or (bool(rescue) and hmac.compare_digest(candidate, rescue))
        )

    def set_pin(self, new_pin: Any, current_pin: Any = None, token: Any = None,
                now: Optional[datetime] = None) -> Dict[str, Any]:
        """
        設定或更換 PIN（第一次設定就等於啟用這把鎖）。

        設完**立刻上鎖**：設定密碼的人已經知道密碼，讓他重打一次是三秒鐘的事；
        設完還留在解鎖狀態的話，那台面板會一路開到自動上鎖為止，
        而設密碼的那個人早就走了。
        """
        now = now or datetime.now()
        with self._lock:
            pin = normalize_pin(new_pin)
            if not pin:
                return {"status": "invalid",
                        "message": f"密碼要 {MIN_PIN_DIGITS}–{MAX_PIN_DIGITS} 位數字"}
            if not self._authorized_for_change(current_pin, token, now):
                return {"status": "denied", "message": "要先解鎖或輸入目前的密碼"}
            self._pin = hash_pin(pin)
            self._enabled = True
            self._broken = False      # 重設過了，本子又讀得懂了
            self._failed = 0
            self._cooldown_until = None
            self._clear_session()
            self._save()
            return {"status": "success"}

    def disable(self, current_pin: Any = None, token: Any = None,
                now: Optional[datetime] = None) -> Dict[str, Any]:
        """停用這把鎖（回到家用模式：什麼都不鎖）。要現行 PIN 或已解鎖的 token。"""
        now = now or datetime.now()
        with self._lock:
            if not self._enabled:
                return {"status": "success"}
            if not self._authorized_for_change(current_pin, token, now):
                return {"status": "denied", "message": "要先解鎖或輸入目前的密碼"}
            self._enabled = False
            self._pin = None
            self._broken = False
            self._failed = 0
            self._cooldown_until = None
            self._clear_session()
            self._save()
            return {"status": "success"}

    def set_auto_lock_minutes(self, minutes: Any, current_pin: Any = None,
                              token: Any = None,
                              now: Optional[datetime] = None) -> Dict[str, Any]:
        """
        調整「閒置多久自動上鎖」。

        這個數字**不放在 `settings.json`** 而是跟 PIN 存在一起：系統設定本身是
        被這把鎖保護的東西，把鎖的參數放進被鎖的抽屜裡，壞檔或誤設的時候
        會變成互相卡住。
        """
        now = now or datetime.now()
        with self._lock:
            if not self._authorized_for_change(current_pin, token, now):
                return {"status": "denied", "message": "要先解鎖或輸入目前的密碼"}
            self._auto_lock_minutes = clamp_auto_lock_minutes(minutes, self._auto_lock_minutes)
            if self._token and self._expires_at:
                # 已經解鎖的那一次立刻照新的算，不然改短了還要等舊的到期
                self._expires_at = now + timedelta(minutes=self._auto_lock_minutes)
                if self._hard_expires_at and self._expires_at > self._hard_expires_at:
                    self._expires_at = self._hard_expires_at
            self._save()
            return {"status": "success", "auto_lock_minutes": self._auto_lock_minutes}
