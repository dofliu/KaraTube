"""
錄音分享連結 (Share Links)

唱完那一次錄下來了，下一句話一定是「傳給我」。商用點歌機與線上 K 歌 App
（全民K歌、唱吧）都有這一步：產一個連結或 QR，當事人自己把那一次帶走。
沒有這一步，錄音就只活在包廂那台機器上 —— 而那台機器的配額遲早會把它擠掉。

這支只管「誰能拿到哪一筆、拿到什麼時候為止」，錄音檔本身由 RecordingLibrary 管。

幾個刻意的決定：

**不是「開啟一次就作廢」的一次性連結。**
名字上很誘人，實作上會害到當事人自己：`<audio>` 播一個檔案不是一次請求 ——
拖進度條會發 Range 請求、Safari 會為了同一個檔案再發一次、手機切背景回來
也可能重連。第一個請求就把連結燒掉，使用者會看到歌播到一半死掉，
而且重新整理也救不回來。所以這裡的「一次性」是**時效性**：

  * 預設 24 小時後自動失效（時間到就是真的打不開，不是畫面上不顯示）；
  * 隨時可以手動撤銷（送出去才後悔的那種）；
  * 可以另外設「下載幾次就失效」—— 但只算**明確的下載**（`download=1`），
    不算播放時的那幾個請求，不然使用者拖一次進度條就把自己的額度用光。

**沒有「永不過期」這個選項。**
錄到的是包廂裡所有人的聲音。一個永遠有效的公開連結，是事後想收也收不回來
的那種東西 —— 少一個選項換「最長 30 天」的上限，這個交換划算。

**索引不放在錄音資料夾裡。**
`RecordingLibrary._reconcile()` 會把錄音資料夾裡「不在索引上」的檔案當成
斷電留下的孤兒檔刪掉，分享索引放進去會在下次開機時無聲消失
（所有已發出去的連結一起死）。所以放 `cache/` 底下。

**錄音沒了，連結就該死。**
配額把那一筆擠掉、或有人按了刪除之後，舊連結不該還能打開別的東西。
token 與錄音 id 是分開的兩張表，所以由呼叫端在放行之前再確認一次錄音還在
（`prune()` 負責把對不到的連結收掉，讓索引不會無限長大）。
"""
import json
import logging
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from secrets import token_urlsafe
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger("KaraTube.ShareLinks")

# token 由伺服器產生：16 bytes → 22 個字元的 urlsafe base64（128 bits）。
# 猜中的機率遠低於「有人在同一個區網裡直接掃 port」，這一關不是瓶頸。
TOKEN_BYTES = 16
# 進來的 token 一律先過這個形狀。長度放寬是為了讓舊索引在調整 TOKEN_BYTES
# 之後還能解得開，字元集則卡死在 urlsafe base64 —— 斜線與點進不來，
# 就算哪天有人把 token 拼進路徑也穿不出去。
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")

DEFAULT_TTL_HOURS = 24
MIN_TTL_HOURS = 1
MAX_TTL_HOURS = 24 * 30          # 30 天。沒有「永不過期」是刻意的，見模組說明
MAX_DOWNLOAD_CAP = 999

# 撤銷／過期的連結留這麼久再從索引清掉。留一段時間是為了讓點下去的人
# 看到「這個連結已失效」而不是「找不到頁面」—— 後者會被當成系統壞了。
TOMBSTONE_DAYS = 7


def _parse_time(value: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def clamp_ttl_hours(hours: Any, default: int = DEFAULT_TTL_HOURS) -> int:
    """
    把外面送進來的時效夾進合法範圍。看不懂（或是 0 與負數）就用預設值。

    小數先四捨五入再夾下限，所以「0.5 小時」變成 1 小時而不是掉回預設的 24 ——
    要求半小時的人拿到一整天，是這個函式最不該犯的錯（往長的方向猜）。
    """
    try:
        value = float(hours)
    except (TypeError, ValueError):
        value = float(default)
    if value <= 0:
        value = float(default)
    return max(MIN_TTL_HOURS, min(MAX_TTL_HOURS, int(round(value))))


def clamp_max_downloads(value: Any) -> int:
    """下載次數上限。0 代表不限（時效還是在）。"""
    try:
        count = int(float(value))
    except (TypeError, ValueError):
        return 0
    return max(0, min(MAX_DOWNLOAD_CAP, count))


class ShareLinkStore:
    def __init__(self, index_file: Path):
        self.index_file = Path(index_file)
        self._lock = threading.Lock()
        self._entries: List[Dict[str, Any]] = []
        self._load()

    # --- 持久化 ---

    def _load(self):
        if not self.index_file.exists():
            return
        try:
            raw = json.loads(self.index_file.read_text(encoding="utf-8"))
            entries = raw.get("shares", []) if isinstance(raw, dict) else []
        except Exception as e:
            logger.warning(f"分享連結索引讀取失敗，當成沒有任何連結: {e}")
            entries = []
        self._entries = [e for e in entries
                         if isinstance(e, dict) and TOKEN_RE.match(str(e.get("token", "")))]

    def _save(self):
        try:
            self.index_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "shares": self._entries,
            }
            self.index_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"分享連結索引寫入失敗: {e}")

    # --- 狀態判斷 ---

    @staticmethod
    def status_of(entry: Dict[str, Any], now: Optional[datetime] = None) -> str:
        """
        一筆連結現在的狀態：`active` / `revoked` / `expired` / `exhausted`。

        分這麼細是因為畫面上要講的話不一樣：「已撤銷」是自己按的，
        「已過期」是時間到的，「下載次數已用完」是當事人已經拿走了 ——
        三種都回「無效」的話，使用者會不知道該不該重發一個。
        """
        if entry.get("revoked"):
            return "revoked"
        expires = _parse_time(entry.get("expires_at"))
        if expires is not None and (now or datetime.now()) >= expires:
            return "expired"
        cap = int(entry.get("max_downloads") or 0)
        if cap > 0 and int(entry.get("downloads") or 0) >= cap:
            return "exhausted"
        return "active"

    def _decorate(self, entry: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
        """給畫面看的一筆：補上狀態與「還剩多久」。"""
        now = now or datetime.now()
        out = dict(entry)
        status = self.status_of(entry, now)
        out["status"] = status
        out["active"] = status == "active"
        expires = _parse_time(entry.get("expires_at"))
        # 剩餘秒數給前端倒數用。已失效一律 0，不給負數 ——
        # 負數在畫面上會變成「還剩 -3 小時」這種沒人看得懂的字。
        out["expires_in_seconds"] = max(0, int((expires - now).total_seconds())) if expires else 0
        cap = int(entry.get("max_downloads") or 0)
        out["downloads_left"] = max(0, cap - int(entry.get("downloads") or 0)) if cap > 0 else None
        return out

    # --- 建立 ---

    def create(self, recording_id: str, ttl_hours: Any = DEFAULT_TTL_HOURS,
               max_downloads: Any = 0, reuse: bool = True) -> Dict[str, Any]:
        """
        給一筆錄音產一個分享連結。

        `reuse=True`（預設）時，這筆錄音若已經有一個還有效的連結就直接回傳它，
        不另外產新的 —— 已經掃進別人手機相簿的那個 QR 應該繼續有效，
        每按一次分享就讓上一個悄悄失效，是使用者無法理解的行為。
        要換一個（例如發錯人了）就 `reuse=False`，並記得撤銷舊的。
        """
        rec_id = str(recording_id or "").strip()
        if not rec_id:
            raise ValueError("recording_id is required")
        ttl = clamp_ttl_hours(ttl_hours)
        cap = clamp_max_downloads(max_downloads)
        now = datetime.now()

        with self._lock:
            if reuse:
                for entry in self._entries:
                    if entry.get("recording_id") == rec_id and \
                            self.status_of(entry, now) == "active":
                        return self._decorate(entry, now)

            entry = {
                "token": token_urlsafe(TOKEN_BYTES),
                "recording_id": rec_id,
                "created_at": now.isoformat(timespec="seconds"),
                "expires_at": (now + timedelta(hours=ttl)).isoformat(timespec="seconds"),
                "ttl_hours": ttl,
                "max_downloads": cap,
                "downloads": 0,
                "views": 0,
                "last_used_at": "",
                "revoked": False,
            }
            self._entries.append(entry)
            self._sweep(now)
            self._save()
            return self._decorate(entry, now)

    # --- 查詢 ---

    def resolve(self, token: str) -> Tuple[Optional[Dict[str, Any]], str]:
        """
        拿 token 換那一筆。回傳 `(entry|None, reason)`。

        reason 在成功時是 `ok`，失敗時是 `not_found` / `revoked` / `expired`
        / `exhausted` —— 呼叫端照這個決定要顯示哪一句話。
        """
        if not TOKEN_RE.match(str(token or "")):
            return None, "not_found"
        with self._lock:
            entry = next((e for e in self._entries if e.get("token") == token), None)
            if entry is None:
                return None, "not_found"
            status = self.status_of(entry)
            if status != "active":
                # 「錄音已經不在了」在索引裡是一種撤銷（狀態欄還是 revoked），
                # 但對著連結點下去的人要聽到的是「那一次不在了」而不是
                # 「連結被撤銷了」—— 後者聽起來像是有人針對他收回了權限。
                if status == "revoked" and entry.get("revoked_reason") == "gone":
                    return None, "gone"
                return None, status
            # 回裝飾過的版本：呼叫端要的是「還剩多久、還能下載幾次」，
            # 讓每一個端點各自去減一次日期，遲早會有一個算錯。
            return self._decorate(entry), "ok"

    def list_for(self, recording_id: str) -> List[Dict[str, Any]]:
        """某一筆錄音的所有連結，新的排前面（含已失效的，畫面要能說明）。"""
        rec_id = str(recording_id or "")
        with self._lock:
            items = [e for e in self._entries if e.get("recording_id") == rec_id]
            now = datetime.now()
            return [self._decorate(e, now) for e in reversed(items)]

    def list_all(self) -> List[Dict[str, Any]]:
        with self._lock:
            now = datetime.now()
            return [self._decorate(e, now) for e in reversed(self._entries)]

    def active_count(self) -> int:
        with self._lock:
            now = datetime.now()
            return sum(1 for e in self._entries if self.status_of(e, now) == "active")

    # --- 使用與撤銷 ---

    def note_view(self, token: str):
        """有人打開了分享頁。只是統計，不影響額度。"""
        with self._lock:
            for entry in self._entries:
                if entry.get("token") == token:
                    entry["views"] = int(entry.get("views") or 0) + 1
                    entry["last_used_at"] = datetime.now().isoformat(timespec="seconds")
                    self._save()
                    return

    def note_download(self, token: str) -> Optional[Dict[str, Any]]:
        """
        記一次**下載**。只有明確的下載才算進額度 ——
        播放時的 Range 請求、Safari 的第二次請求都不算，
        不然使用者拖一次進度條就把自己的次數用光了。
        """
        with self._lock:
            for entry in self._entries:
                if entry.get("token") == token:
                    entry["downloads"] = int(entry.get("downloads") or 0) + 1
                    entry["last_used_at"] = datetime.now().isoformat(timespec="seconds")
                    self._save()
                    return self._decorate(entry)
        return None

    def revoke(self, token: str, reason: str = "") -> bool:
        """
        撤銷一個連結。`reason="gone"` 代表「錄音本身沒了」——
        兩者在索引裡都是 revoked，但對使用者要講的話不一樣。
        """
        if not TOKEN_RE.match(str(token or "")):
            return False
        with self._lock:
            for entry in self._entries:
                if entry.get("token") == token:
                    if entry.get("revoked"):
                        return True     # 撤銷第二次不是錯誤
                    self._mark_revoked(entry, reason)
                    self._save()
                    return True
        return False

    def revoke_for_recording(self, recording_id: str, reason: str = "") -> int:
        """這筆錄音的所有連結一次撤銷（刪除錄音時跟著做）。"""
        rec_id = str(recording_id or "")
        with self._lock:
            hit = 0
            for entry in self._entries:
                if entry.get("recording_id") == rec_id and not entry.get("revoked"):
                    self._mark_revoked(entry, reason)
                    hit += 1
            if hit:
                self._save()
            return hit

    @staticmethod
    def _mark_revoked(entry: Dict[str, Any], reason: str = ""):
        entry["revoked"] = True
        entry["revoked_at"] = datetime.now().isoformat(timespec="seconds")
        if reason:
            entry["revoked_reason"] = reason

    def prune(self, valid_recording_ids: Optional[Iterable[str]] = None) -> int:
        """
        對帳：錄音已經不在的連結標成失效，失效夠久的整筆丟掉。回傳動到幾筆。

        錄音被配額擠掉是**無聲**發生的（沒有人按刪除），所以不能只靠
        刪除時撤銷；這支由清單／建立流程順手呼叫。

        刻意不直接刪掉那些連結，而是先標成「錄音已經不在了」：整筆刪掉的話，
        掃過 QR 的人點進來看到的是「這個連結不存在」—— 那句話會被理解成
        「網址打錯了」，於是他會再掃一次、再點一次，永遠得不到答案。
        """
        with self._lock:
            now = datetime.now()
            touched = 0
            if valid_recording_ids is not None:
                valid = set(valid_recording_ids)
                for entry in self._entries:
                    if entry.get("recording_id") not in valid and not entry.get("revoked"):
                        self._mark_revoked(entry, "gone")
                        touched += 1
            before = len(self._entries)
            self._sweep(now)
            removed = before - len(self._entries)
            if touched or removed:
                self._save()
            return touched + removed

    def _sweep(self, now: datetime):
        """把失效超過 TOMBSTONE_DAYS 的連結丟掉。呼叫端必須已經持有鎖。"""
        cutoff = now - timedelta(days=TOMBSTONE_DAYS)

        def keep(entry: Dict[str, Any]) -> bool:
            if self.status_of(entry, now) == "active":
                return True
            # 失效時間點取「撤銷時間」與「到期時間」的較晚者；兩個都讀不出來
            # 就用建立時間 —— 讀不出來的壞資料留著也沒有用。
            marks = [_parse_time(entry.get("revoked_at")),
                     _parse_time(entry.get("expires_at")),
                     _parse_time(entry.get("created_at"))]
            marks = [m for m in marks if m is not None]
            return bool(marks) and max(marks) >= cutoff

        self._entries = [e for e in self._entries if keep(e)]
