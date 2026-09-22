"""
服務鈴（包廂呼叫櫃檯）

商用點歌機上那顆「服務」鍵：送餐、加冰塊、清潔、麥克風沒聲音、結帳。
這是舞台訊息（跑馬燈）的**反方向** —— 那一支是櫃檯把字打到包廂螢幕上，
這一支是包廂把一件事丟到櫃檯。兩條路都通了，包廂裡才不必有人推開門走出去
（而那個人通常正是唯一知道要加什麼的人）。

功能本身三行就寫得完：存一筆需求、亮給櫃檯看。難的是**按下去之後**。

一顆沒有回音的服務鈴會被按第二次、第三次、第五次。使用者不是沒耐性，是
他**沒有辦法分辨**「櫃檯看到了、正在弄」跟「這顆鍵根本沒作用」—— 而這兩件事
在畫面上如果長得一樣，任何人都會再按一次。於是一顆本來要減少打擾的鍵，
變成打擾的來源：櫃檯收到五張單，不知道是五個需求還是一個不耐煩的人，
於是送了五杯冰塊、或者一杯都沒送。

所以這一支的設計是從「按第二次的那個人在想什麼」倒推回來的：

決定一：一個包廂同時只有一張未結案的單
    已經有一張開著的時候再按，是**併進同一張**，不是開第二張。櫃檯看到的
    永遠是一列（「302 包廂：送餐＋加冰塊」），不是五列一模一樣的東西。
    併單才有辦法讓櫃檯一趟送完；五張單則是五趟，或者更常見的 —— 一趟都不送，
    因為看起來像機器壞了在洗版。

決定二：併單不重設等待時間
    等待時間一律從**第一次**按下去算起。每按一次就重算的話，等最久的那一桌
    在櫃檯的清單上會永遠排在最後面（因為他按最多次），而那正是最不該被排到
    最後面的一桌。按第二次改變的是單子的**內容**（多一個品項、多一次催），
    不是那個鐘。

決定三：「已送出」不等於「有人看到了」
    狀態分開四種：等待中 → 櫃檯收到 → 完成 / 取消。客人那一端看得到現在是
    哪一種、以及等了多久。最糟的做法是跳一個「已送出」的提示然後讓它自己
    消失 —— 三十秒後那個人手上什麼都沒有，他唯一能做的就是再按一次。
    這個功能的價值有一半在那個**一直在畫面上的狀態**，不在那顆鍵。

決定四：沒有「催單」鍵，按第二次就是催單
    再按一次會記成一次催（`presses`），櫃檯那一列看得到「按了 3 次」。
    催單是真的資訊（這桌等急了），值得讓櫃檯看見；但它不該變成第二張單。
    而且按下去要**有反應** —— 按了沒反應的人，下一步是推開門走出去。

決定五：客人可以自己取消，而且取消跟完成不是同一件事
    「不用了，我們自己去拿」要有出口：按不掉的鈴，大家就不敢按。
    但「客人自己取消」與「櫃檯處理完了」在紀錄上一定要分得開 ——
    揉成一個「已結案」的話，隔天想知道「昨晚有幾單沒服務到」就永遠問不出來。

決定六：一個字都不上舞台
    跟跑馬燈的取捨剛好相反。跑馬燈要上舞台是因為它的收件人是整個包廂；
    服務鈴的收件人是櫃檯，而它的回執屬於**按下去的那支手機**。
    台上那個人沒有點那份冰塊，單子的狀態對他沒有用 —— 把它放上舞台等於
    拿全場唯一一面大家都在看的螢幕，去講一件跟一個人有關的事。
    （櫃檯想講話有跑馬燈可以用，那是另一個明確的動作，不是這裡自動發生的。）

決定七：落地，但會過期
    跟跑馬燈相反，這一支**要存檔**：一張還開著的單對應的是現實世界裡一件
    還沒做完的事，伺服器重開不會讓那杯冰塊自己送到。但開太久的單
    （超過 STALE_MINUTES）標成「過期」而不是繼續亮著、也不是默默刪掉：
    默默刪掉會讓客人以為送出去了，繼續亮著則會讓昨晚那一桌的單出現在
    今天早上的櫃檯上。

這一支只管「現在有沒有人在叫、叫的是什麼、等多久了」。要怎麼顯示
（等待幾分鐘要不要變紅、按鍵長什麼樣）是畫面的事，寫在
`frontend/js/service-view.js`。
"""
import json
import logging
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger("KaraTube.ServiceCalls")

# 包廂裡真正會按的那幾件事。順序就是畫面上按鍵的順序（最常按的在前面），
# 清單放在後端是刻意的：櫃檯那一列與客人那支手機必須講出同一個詞 ——
# 兩邊各寫一份的話，客人按的是「加冰塊」，櫃檯看到的是「飲料」。
ITEM_SPEC: List[Dict[str, str]] = [
    {"key": "service", "emoji": "🛎️", "label": "服務人員"},
    {"key": "food", "emoji": "🍽️", "label": "送餐／加點"},
    {"key": "drink", "emoji": "🧊", "label": "加冰塊／飲料"},
    {"key": "clean", "emoji": "🧹", "label": "清潔／收桌"},
    {"key": "gear", "emoji": "🎤", "label": "麥克風／音響"},
    {"key": "bill", "emoji": "🧾", "label": "結帳"},
    {"key": "other", "emoji": "✏️", "label": "其他"},
]

ITEM_KEYS = [spec["key"] for spec in ITEM_SPEC]
ITEM_LABELS = {spec["key"]: spec["label"] for spec in ITEM_SPEC}
ITEM_ORDER = {spec["key"]: idx for idx, spec in enumerate(ITEM_SPEC)}

# 備註的長度上限。櫃檯那一列只有一行，而且看的人正在忙 ——
# 一段一百字的備註在那一列上會被截掉，然後沒有人知道被截掉的是哪一半。
MAX_NOTE_CHARS = 40

# 名字（誰按的、誰收的）的長度上限，跟佇列的暱稱同一個數量級。
MAX_NAME_CHARS = 20

# 櫃檯回的那一句（「餐點五分鐘後到」）。跟備註同一個上限、同一個理由。
MAX_REPLY_CHARS = 40

# 開著的單超過幾分鐘就標成過期（見決定七）。45 分鐘是刻意抓得比
# 「送一趟餐」長很多：標得太早會把還在路上的單變成過期，
# 而過期的單客人會重按 —— 那就繞回這個功能要解決的問題本身。
DEFAULT_STALE_MINUTES = 45
MIN_STALE_MINUTES = 5
MAX_STALE_MINUTES = 480

# 紀錄留最近幾筆。櫃檯偶爾要回頭看「剛剛那桌叫的是什麼」，
# 但這不是帳務系統 —— 留一整晚就夠了。
MAX_HISTORY = 60

# 狀態。等待中 / 櫃檯收到 是「還開著」，其餘三種是結案（見決定三、決定五）。
STATUS_WAITING = "WAITING"
STATUS_ACKED = "ACKED"
STATUS_DONE = "DONE"
STATUS_CANCELLED = "CANCELLED"
STATUS_EXPIRED = "EXPIRED"

OPEN_STATUSES = (STATUS_WAITING, STATUS_ACKED)


def clean_line(text: Any, limit: int) -> str:
    """
    把送進來的字收成「一行」並裁到上限。

    跟舞台訊息同一個處理：換行在櫃檯那一列上沒有意義（它只有一行高），
    但手機鍵盤與貼上來的文字常常帶著換行，原樣存起來會在畫面上變成
    一個看不出來的空白。
    """
    raw = "" if text is None else str(text)
    flattened = " ".join(raw.replace("\r", " ").replace("\n", " ").split())
    return flattened[:limit]


def clean_items(items: Any) -> List[str]:
    """
    收下認得的品項，照 ITEM_SPEC 的順序排、去重。

    不認得的鍵**直接丟掉**而不是報錯：舊版手機頁面留在別人的瀏覽器分頁裡是
    常態，為了一個過時的鍵讓整張單送不出去，代價遠大於少一個品項。
    但全部都不認得時會變成空的，由 `ring()` 擋下來 —— 那時候該講的是
    「你沒有選任何一項」，而不是靜靜地開一張空白單。
    """
    if items is None:
        return []
    if isinstance(items, (str, bytes)):
        raw: Sequence[Any] = [items]
    elif isinstance(items, Sequence):
        raw = items
    else:
        return []
    picked = {str(item).strip().lower() for item in raw if str(item).strip()}
    return [key for key in ITEM_KEYS if key in picked]


def coerce_stale_minutes(value: Any, default: int = DEFAULT_STALE_MINUTES) -> int:
    """過期門檻（分鐘）。看不懂的值退回預設值，不是拋錯。"""
    try:
        minutes = int(round(float(value)))
    except (TypeError, ValueError):
        return int(default)
    return int(min(MAX_STALE_MINUTES, max(MIN_STALE_MINUTES, minutes)))


def items_label(items: Sequence[str]) -> str:
    """把品項講成一句話（「送餐／加點＋加冰塊／飲料」）。給紀錄與通知用。"""
    names = [ITEM_LABELS[key] for key in items if key in ITEM_LABELS]
    return "＋".join(names)


class ServiceCallRejected(Exception):
    """
    這張單不收（或這個動作做不了）。

    跟額度、計時、舞台訊息那幾支同一個寫法：帶著原因與現況丟出來，
    API 層才講得出「你沒有選任何一項」或「這張單已經結案了」——
    只丟一句字串的話，那句話就得在服務層裡拼，而那是畫面的工作。
    """

    def __init__(self, reason: str, detail: Optional[Dict[str, Any]] = None):
        self.reason = reason
        self.detail = detail or {}
        super().__init__(reason)


class ServiceDesk:
    """
    現在有沒有人在叫櫃檯。

    同時只有一張開著的單（見決定一），所以沒有「單號清單」這種東西 ——
    `self._open` 要嘛是那一張，要嘛是 None。結案的往 `self._history` 疊。

    時間一律從外面傳進來（`now`），測試不必真的等 45 分鐘。
    """

    def __init__(self, state_file: Optional[Path] = None,
                 stale_minutes: int = DEFAULT_STALE_MINUTES,
                 max_history: int = MAX_HISTORY):
        self._lock = threading.RLock()
        self._state_file = Path(state_file) if state_file else None
        self._stale_minutes = coerce_stale_minutes(stale_minutes)
        self._max_history = max(1, int(max_history))
        self._open: Optional[Dict[str, Any]] = None
        self._history: List[Dict[str, Any]] = []
        self._load()

    # --- 持久化 ---

    def _load(self) -> None:
        """
        讀回上次的狀態。

        壞檔就從空的開始（而不是像歌號簿那樣整個功能停掉）：這裡沒有任何
        使用者記在腦子裡的東西，最壞的結果是一張沒服務到的單消失，
        而客人一分鐘之內就會再按一次 —— 那顆鍵本來就是為了這個而存在。
        紀錄掉了更是無所謂（它不是帳）。
        """
        if not self._state_file or not self._state_file.exists():
            return
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("服務鈴存檔不是一個物件")
            raw_open = data.get("open")
            self._open = dict(raw_open) if isinstance(raw_open, dict) else None
            raw_history = data.get("history")
            if isinstance(raw_history, list):
                self._history = [dict(row) for row in raw_history if isinstance(row, dict)]
            logger.info("服務鈴：讀回 %s（紀錄 %d 筆）",
                        "一張未結案的單" if self._open else "沒有未結案的單",
                        len(self._history))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("服務鈴存檔讀不出來（%s），從空的開始", exc)
            self._open = None
            self._history = []

    def _save(self) -> None:
        """寫回存檔。寫不出來只記一筆 log —— 服務鈴不該因為磁碟滿了就按不動。"""
        if not self._state_file:
            return
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {"open": self._open, "history": self._history[: self._max_history]}
            self._state_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning("服務鈴存檔寫不進去：%s", exc)

    # --- 內部 ---

    def _close(self, call: Dict[str, Any], status: str, now: datetime,
               by: str = "", reply: str = "") -> Dict[str, Any]:
        """把開著的那張單結掉，疊進紀錄。回傳結掉的那一張。"""
        call["status"] = status
        call["closed_at"] = now.isoformat(timespec="seconds")
        call["closed_by"] = clean_line(by, MAX_NAME_CHARS)
        if reply:
            call["reply"] = clean_line(reply, MAX_REPLY_CHARS)
        self._open = None
        self._history.insert(0, call)
        del self._history[self._max_history:]
        self._save()
        return dict(call)

    def _age(self, now: datetime) -> Optional[Dict[str, Any]]:
        """
        開太久的單標成過期（見決定七）。回傳剛剛被標掉的那一張（沒有就 None）。

        懶惰執行：每一次查詢／操作前呼叫一次就夠了，不需要背景執行緒 ——
        沒有人在看的時候，一張單是「開著」還是「過期」沒有差別。
        """
        call = self._open
        if not call:
            return None
        try:
            created = datetime.fromisoformat(str(call.get("created_at")))
        except (TypeError, ValueError):
            # 建立時間壞掉的單當成過期：寧可讓客人重按一次（五秒），
            # 也不要留下一張永遠不會消失、而且沒有人知道怎麼關掉的單。
            logger.warning("服務鈴：單子的建立時間壞掉，直接標成過期")
            return self._close(call, STATUS_EXPIRED, now)
        if now - created < timedelta(minutes=self._stale_minutes):
            return None
        logger.info("服務鈴：一張開了超過 %d 分鐘的單標成過期", self._stale_minutes)
        return self._close(call, STATUS_EXPIRED, now)

    def _waited_seconds(self, call: Dict[str, Any], now: datetime) -> float:
        """從**第一次**按下去到現在幾秒（見決定二）。算不出來回 0。"""
        try:
            created = datetime.fromisoformat(str(call.get("created_at")))
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, (now - created).total_seconds())

    def _decorate(self, call: Optional[Dict[str, Any]],
                  now: datetime) -> Optional[Dict[str, Any]]:
        """給畫面用的衍生欄位。存檔裡不放這些 —— 它們每一秒都不一樣。"""
        if not call:
            return None
        out = dict(call)
        out["waited_seconds"] = round(self._waited_seconds(call, now), 1)
        out["items_label"] = items_label(out.get("items") or [])
        out["is_open"] = out.get("status") in OPEN_STATUSES
        return out

    # --- 查詢 ---

    def open_call(self, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
        """現在開著的那一張（沒有就 None）。查之前會先把過期的收掉。"""
        now = now or datetime.now()
        with self._lock:
            self._age(now)
            return self._decorate(self._open, now)

    def expire_stale(self, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
        """
        心跳呼叫這一支：開太久的單標成過期，回傳剛剛被標掉的那一張。

        `open_call()` 自己也會做同一件事，但那要有人去查才會發生 ——
        而一張沒有人理的單，正好是沒有人會去查的那一張。畫面上一直寫著
        「等待中 47 分鐘」比沒有那顆鍵更糟：它看起來像系統還在處理。
        """
        now = now or datetime.now()
        with self._lock:
            expired = self._age(now)
            return self._decorate(expired, now) if expired else None

    def history(self, limit: int = 20,
                now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """結案的單，最新在前。"""
        now = now or datetime.now()
        with self._lock:
            self._age(now)
            rows = self._history[: max(0, int(limit))]
            return [self._decorate(dict(row), now) or {} for row in rows]

    def snapshot(self, history_limit: int = 12,
                 now: Optional[datetime] = None) -> Dict[str, Any]:
        """畫面與 API 看的都是這一份。"""
        now = now or datetime.now()
        with self._lock:
            self._age(now)
            current = self._decorate(self._open, now)
            rows = [self._decorate(dict(row), now) or {}
                    for row in self._history[: max(0, int(history_limit))]]
            # 「今晚有幾單沒服務到」問得出來，正是決定五要分開兩種結案的理由。
            unserved = sum(1 for row in self._history
                           if row.get("status") in (STATUS_CANCELLED, STATUS_EXPIRED))
            return {
                "call": current,
                "waiting": bool(current),
                "history": rows,
                "history_count": len(self._history),
                "unserved_count": unserved,
                "items": [dict(spec) for spec in ITEM_SPEC],
                "max_note_chars": MAX_NOTE_CHARS,
                "max_reply_chars": MAX_REPLY_CHARS,
                "stale_minutes": self._stale_minutes,
                "updated_at": now.isoformat(timespec="seconds"),
            }

    # --- 操作 ---

    def ring(self, items: Any, note: Any = "", by: Any = "",
             now: Optional[datetime] = None) -> Dict[str, Any]:
        """
        按下服務鈴。

        已經有一張開著就併進去（見決定一），回傳的 `merged` 告訴畫面該說
        「已送出」還是「已經併進剛剛那一張，櫃檯看得到你按了 3 次」——
        這兩句話不能講成同一句：第二次按下去如果得到跟第一次一模一樣的回應，
        那個人會以為第一次根本沒送出去。
        """
        now = now or datetime.now()
        picked = clean_items(items)
        if not picked:
            raise ServiceCallRejected("empty", {"items": [dict(s) for s in ITEM_SPEC]})
        who = clean_line(by, MAX_NAME_CHARS)
        text = clean_line(note, MAX_NOTE_CHARS)
        stamp = now.isoformat(timespec="seconds")

        with self._lock:
            self._age(now)
            call = self._open
            if call:
                # 併單：內容累加，等待時間那個鐘不動（見決定二）。
                merged_items = sorted(set(call.get("items") or []) | set(picked),
                                      key=lambda key: ITEM_ORDER.get(key, 99))
                call["items"] = merged_items
                if text:
                    # 後來補的備註接在後面而不是覆蓋：先寫「少冰」再寫「兩杯」的人
                    # 要的是兩件事都送到，不是第二句把第一句吃掉。
                    previous = str(call.get("note") or "")
                    joined = f"{previous}；{text}" if previous and text not in previous else (
                        previous or text)
                    call["note"] = clean_line(joined, MAX_NOTE_CHARS)
                call["presses"] = int(call.get("presses") or 1) + 1
                if who:
                    names = list(call.get("pressed_by") or [])
                    if who not in names:
                        names.append(who)
                    call["pressed_by"] = names[:6]
                call["updated_at"] = stamp
                self._save()
                logger.info("服務鈴：併進現有的單（第 %d 次按）— %s",
                            call["presses"], items_label(call["items"]))
                result = self._decorate(call, now) or {}
                result["merged"] = True
                return result

            call = {
                "id": uuid.uuid4().hex[:12],
                "status": STATUS_WAITING,
                "items": picked,
                "note": text,
                "by": who,
                "pressed_by": [who] if who else [],
                "presses": 1,
                "created_at": stamp,
                "updated_at": stamp,
                "acked_at": None,
                "acked_by": "",
                "closed_at": None,
                "closed_by": "",
                "reply": "",
            }
            self._open = call
            self._save()
            logger.info("服務鈴：新的一張單 — %s%s", items_label(picked),
                        f"（{text}）" if text else "")
            result = self._decorate(call, now) or {}
            result["merged"] = False
            return result

    def _require_open(self, call_id: Any, now: datetime) -> Dict[str, Any]:
        """
        取出開著的那一張，並確認它就是呼叫端以為的那一張。

        `call_id` 可以不給（「處理現在這一張」），但給了就必須對得上：
        客人取消之後馬上又按了一張新的時，櫃檯手上那個畫面可能還停在舊的，
        照著按下去會把**新的**那一張標成完成 —— 而那件事根本還沒做。
        """
        self._age(now)
        call = self._open
        if not call:
            raise ServiceCallRejected("none_open", {})
        if call_id and str(call_id) != str(call.get("id")):
            raise ServiceCallRejected("stale", {"current_id": call.get("id")})
        return call

    def ack(self, call_id: Any = None, by: Any = "",
            now: Optional[datetime] = None) -> Dict[str, Any]:
        """
        櫃檯收到了。

        這是狀態機上唯一一個**不改變現實世界**的動作，卻是整個功能最重要的
        一步（見決定三）：它把「等待中」變成「有人看到了」，而那正是客人
        還會不會再按一次的分水嶺。所以它刻意做成一顆單獨的鍵，
        不是「完成」的附帶效果。
        """
        now = now or datetime.now()
        with self._lock:
            call = self._require_open(call_id, now)
            if call.get("status") == STATUS_ACKED:
                # 重複按不報錯：櫃檯兩個人同時看到同一張單是常態，
                # 而「已經有人收了」不是一個需要跳紅字的狀況。
                return self._decorate(call, now) or {}
            call["status"] = STATUS_ACKED
            call["acked_at"] = now.isoformat(timespec="seconds")
            call["acked_by"] = clean_line(by, MAX_NAME_CHARS)
            call["updated_at"] = call["acked_at"]
            self._save()
            logger.info("服務鈴：櫃檯收到（%s）", items_label(call.get("items") or []))
            return self._decorate(call, now) or {}

    def resolve(self, call_id: Any = None, reply: Any = "", by: Any = "",
                now: Optional[datetime] = None) -> Dict[str, Any]:
        """櫃檯處理完了。`reply` 是回給包廂的那一句（「餐點五分鐘後到」）。"""
        now = now or datetime.now()
        with self._lock:
            call = self._require_open(call_id, now)
            logger.info("服務鈴：完成（%s）", items_label(call.get("items") or []))
            return self._close(call, STATUS_DONE, now, by=by,
                               reply=clean_line(reply, MAX_REPLY_CHARS))

    def cancel(self, call_id: Any = None, by: Any = "",
               now: Optional[datetime] = None) -> Dict[str, Any]:
        """
        包廂自己取消（「不用了，我們自己去拿」）。

        跟 `resolve` 分成兩個狀態是刻意的（見決定五）：按不掉的鈴大家就不敢按，
        而「客人自己算了」跟「櫃檯服務完了」在紀錄上長得一樣的話，
        「今晚有幾單沒服務到」就永遠問不出來。
        """
        now = now or datetime.now()
        with self._lock:
            call = self._require_open(call_id, now)
            logger.info("服務鈴：包廂自己取消（%s）", items_label(call.get("items") or []))
            return self._close(call, STATUS_CANCELLED, now, by=by)

    def set_stale_minutes(self, minutes: Any) -> int:
        """
        設定頁改了「開多久算過期」時套用，下一次查詢生效。

        不回頭重算已經結案的單：一張昨天被標成過期的單，不會因為今天把門檻
        調長了就變回「還開著」—— 那件事早就過去了，而重新亮起來的單
        看起來就像機器壞了。
        """
        with self._lock:
            self._stale_minutes = coerce_stale_minutes(minutes, self._stale_minutes)
            return self._stale_minutes

    def clear_history(self) -> int:
        """
        清掉紀錄（換一桌客人時）。開著的那一張不動 ——
        它對應的是現實世界裡還沒做完的事，不會因為按了「清除紀錄」就做完了。
        """
        with self._lock:
            removed = len(self._history)
            self._history = []
            self._save()
            return removed
