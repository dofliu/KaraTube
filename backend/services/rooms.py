"""
多包廂（一台伺服器帶多組舞台與佇列）

商用點歌機最後一塊沒補的：一台主機接十間包廂，每一間有自己的舞台、自己的
佇列、自己的計時，而櫃檯在一個畫面上看得到全部。

這個模組只做兩件事 —— **哪些包廂存在**（名冊，落地在 `cache/rooms.json`）與
**每一間包廂的那一組服務長在哪**（`RoomRegistry`，把 QueueManager／計時／
舞台訊息／服務鈴包成一個 bundle）。真正的規則都還在原來各自的模組裡，
這裡不重寫任何一條。

---

**決定一：哪些東西分房、哪些東西整台機器共用。**

分錯邊的代價**不對稱**，所以判準是「分錯邊之後會壞掉的是什麼」：

分房的是「這一組客人現在正在做的事」——
佇列與播放狀態、輪唱、點歌額度、包廂計時、舞台訊息、服務鈴、已唱歷史。
這幾件共用的話，A 包廂按下切歌會切掉 B 包廂正在唱的那一首。那是整個
功能唯一不能出的錯。

共用的是「這台機器有什麼」——
曲庫、歌號、歌星與歌名索引、排程預處理、點唱排行、我的最愛、評分歷史、
錄音、系統設定、櫃檯管理鎖。這幾件分房的話：同一首歌會被下載十次、
佔十份磁碟、拿到十個不同的歌號 —— 而「這組號碼永遠是這首歌」是歌號功能
唯一的價值來源（見 song_numbers.py），一分房就沒了。排行分房則是每一間
都只剩十幾筆，排不出任何東西，而「今晚全店最多人唱的歌」本來就是店的問題
不是包廂的問題。最愛與個人紀錄跟著**人**走不跟房間走：同一群人換一間包廂
不該失去自己的東西。

**決定二：預設包廂不換檔名。**

升級的機器一格都不能掉。`default` 這一間沿用原本的 `room_timer.json` /
`service_calls.json`，新開的房才放進 `cache/rooms/<id>/`。不這樣做的話，
升級那一刻正在計時的包廂會歸零 —— 而客人買的兩小時不在畫面上就等於沒買，
櫃檯也沒有任何辦法把它算回來。

**決定三：認不得的房號一律拒絕，絕不靜靜退回 default。**

這個功能最糟的失敗是**唱到別人的包廂去**。手機上那個網址是掃 QR 掃來的、
是店員手打的，打錯一個字是常態；退回 default 的話那支手機會把歌點進別間
包廂，而**兩邊都不會發現**（點的人以為沒點成功，被點的人以為誰手滑了）。
所以房號只收 `[a-z0-9-]`、長度 1..24，不存在就 404，WebSocket 則直接
關掉連線 —— 響亮地失敗，因為它有得救（重掃一次 QR），靜靜地錯沒有。

**決定四：名冊裡永遠有 default，而且刪不掉。**

一台機器至少要有一間包廂，否則第一個連進來的人看到的是一個沒有任何房間、
也沒有任何按鈕可以建房間的畫面（建房間的按鈕在櫃檯那一頁，而那一頁要先
選一間房）。default 可以改名（叫「大包」「101」都行），但不能刪。
"""
import json
import logging
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("KaraTube.Rooms")

# 預設包廂。這個 id 是寫死的常數而不是設定值：舊網址（不帶 ?room=）要對得上它，
# 而「舊網址還能用」是這個功能對已經裝好的機器唯一的承諾。
DEFAULT_ROOM_ID = "default"
DEFAULT_ROOM_NAME = "主包廂"

# 一台機器最多幾間。上限存在的理由不是資料結構撐不住，是**那顆 CPU**：
# 每一間都可能同時點一首沒快取的歌，而流水線一次只跑得動一首
# （見 process_lane.py）。32 間已經遠超過一台單機該帶的數量，
# 這個數字的作用是擋住「迴圈建房間」把記憶體吃光，不是業務規則。
MAX_ROOMS = 32

# 房號的字元集。刻意只收小寫英數與連字號：它會出現在網址、QR code 與
# 檔案路徑上，而大小寫在 macOS/Windows 的檔案系統上不是兩個東西 ——
# `Room1` 與 `room1` 各建一間的話，兩間會共用同一個資料夾。
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,23}$")
MAX_NAME_LEN = 24


class RoomError(Exception):
    """房間操作被拒絕。`reason` 是給畫面分辨的代碼，`detail` 是補充資料。"""

    def __init__(self, reason: str, detail: Optional[Dict[str, Any]] = None):
        self.reason = reason
        self.detail = detail or {}
        super().__init__(reason)


def normalize_id(raw: Any) -> str:
    """
    把使用者／網址給的房號收斂成正規形式。認不得就回空字串（呼叫端負責拒絕）。

    只做「大小寫」與「前後空白」這兩種收斂 —— 不做音譯、不砍非法字元。
    砍掉非法字元的話，`房間A` 會變成空字串、`ro om` 會變成 `room`，
    而使用者看到的是「我打的那個房號變成了另一間」。
    """
    text = str(raw or "").strip().lower()
    if not text:
        return ""
    return text if ID_PATTERN.match(text) else ""


def slug_from_name(name: Any, taken: Optional[List[str]] = None) -> str:
    """
    從房名生一個房號（「101 包廂」→ `101`、「Party Room」→ `party-room`）。

    生不出來（整個名字都是中文）就退回 `room-N`。中文不音譯是刻意的：
    音譯出來的 `bao-xiang` 沒有人認得，而房號是要**用喊的**、用手打的
    （「掃不到 QR 就打 room-3」），一個沒有人念得出來的房號等於沒有房號。
    """
    taken = list(taken or [])
    text = str(name or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:24]
    candidate = text if ID_PATTERN.match(text or "") else ""
    if not candidate:
        n = 2
        while f"room-{n}" in taken:
            n += 1
        candidate = f"room-{n}"
    if candidate not in taken:
        return candidate
    suffix = 2
    while f"{candidate[:21]}-{suffix}" in taken:
        suffix += 1
    return f"{candidate[:21]}-{suffix}"


def clean_name(raw: Any, fallback: str = "") -> str:
    """房名：截到 24 字、壓掉換行。空的就用 fallback（通常是房號本身）。"""
    text = " ".join(str(raw or "").split())[:MAX_NAME_LEN].strip()
    return text or fallback


def room_dir(cache_dir: Path, room_id: str) -> Optional[Path]:
    """這一間的資料夾。default 回 None —— 它的檔案留在 cache/ 原地（決定二）。"""
    if room_id == DEFAULT_ROOM_ID:
        return None
    return Path(cache_dir) / "rooms" / room_id


def state_path(cache_dir: Path, room_id: str, filename: str) -> Path:
    """
    這一間的某個狀態檔要放哪。

    default 是 `cache/<filename>`（原路徑，升級不掉資料），
    其他房間是 `cache/rooms/<id>/<filename>`。
    """
    folder = room_dir(cache_dir, room_id)
    return (Path(cache_dir) / filename) if folder is None else (folder / filename)


class RoomRoster:
    """
    包廂名冊。落地成 `cache/rooms.json`，重開機之後房間還在
    —— 房號印在 QR code 上貼在包廂門口，它不能因為伺服器重開就換一組。
    """

    def __init__(self, state_file: Optional[Path] = None):
        self._file = Path(state_file) if state_file else None
        self._lock = threading.RLock()
        self._rooms: List[Dict[str, Any]] = []
        self._load()
        self._ensure_default()

    # --- 持久化 ---

    def _load(self):
        if not self._file or not self._file.exists():
            return
        try:
            raw = json.loads(self._file.read_text(encoding="utf-8"))
            rows = raw.get("rooms", []) if isinstance(raw, dict) else []
        except Exception as e:
            # 壞檔不是致命傷（跟櫃檯管理鎖不同：這裡沒有安全邊界）。
            # 退回「只有一間 default」，那正好是升級前的世界。
            logger.warning(f"包廂名冊讀取失敗，退回單一包廂: {e}")
            return
        out: List[Dict[str, Any]] = []
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            rid = normalize_id(row.get("id"))
            if not rid or rid in seen:
                continue
            seen.add(rid)
            out.append({
                "id": rid,
                "name": clean_name(row.get("name"), rid),
                "created_at": str(row.get("created_at") or ""),
            })
        self._rooms = out[:MAX_ROOMS]

    def _save(self):
        if not self._file:
            return
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            payload = {"updated_at": datetime.now().isoformat(timespec="seconds"),
                       "rooms": self._rooms}
            self._file.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        except Exception as e:
            logger.warning(f"包廂名冊寫入失敗: {e}")

    def _ensure_default(self):
        with self._lock:
            if not any(r["id"] == DEFAULT_ROOM_ID for r in self._rooms):
                self._rooms.insert(0, {
                    "id": DEFAULT_ROOM_ID,
                    "name": DEFAULT_ROOM_NAME,
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                })
                self._save()

    # --- 查詢 ---

    def ids(self) -> List[str]:
        with self._lock:
            return [r["id"] for r in self._rooms]

    def has(self, room_id: str) -> bool:
        return normalize_id(room_id) in self.ids()

    def rows(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._rooms]

    def get(self, room_id: str) -> Optional[Dict[str, Any]]:
        rid = normalize_id(room_id)
        with self._lock:
            for r in self._rooms:
                if r["id"] == rid:
                    return dict(r)
        return None

    # --- 異動 ---

    def add(self, name: Any = "", room_id: Any = None) -> Dict[str, Any]:
        """
        開一間。`room_id` 不給就從名字生一個（`slug_from_name`）。

        重複的房號一律拒絕而不是「自動加個 -2」—— 櫃檯打的那個房號是他
        要貼在門口的那一個，被機器偷偷改掉之後，門口那張 QR 會指到別間。
        """
        with self._lock:
            if len(self._rooms) >= MAX_ROOMS:
                raise RoomError("too_many_rooms", {"limit": MAX_ROOMS})
            taken = [r["id"] for r in self._rooms]
            if room_id is None or str(room_id).strip() == "":
                rid = slug_from_name(name, taken)
            else:
                rid = normalize_id(room_id)
                if not rid:
                    raise RoomError("bad_room_id", {"allowed": "a-z 0-9 - （1~24 字）"})
            if rid in taken:
                raise RoomError("room_exists", {"id": rid})
            row = {
                "id": rid,
                "name": clean_name(name, rid),
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
            self._rooms.append(row)
            self._save()
            return dict(row)

    def rename(self, room_id: str, name: Any) -> Dict[str, Any]:
        rid = normalize_id(room_id)
        with self._lock:
            for r in self._rooms:
                if r["id"] == rid:
                    r["name"] = clean_name(name, rid)
                    self._save()
                    return dict(r)
        raise RoomError("no_such_room", {"id": rid})

    def remove(self, room_id: str) -> Dict[str, Any]:
        """
        關掉一間。default 刪不掉（決定四）。

        這裡只管名冊；「裡面還有人在唱歌要不要攔下來」是 RoomRegistry 的事
        —— 名冊不認識佇列。
        """
        rid = normalize_id(room_id)
        if rid == DEFAULT_ROOM_ID:
            raise RoomError("default_room_locked", {"id": rid})
        with self._lock:
            for i, r in enumerate(self._rooms):
                if r["id"] == rid:
                    self._rooms.pop(i)
                    self._save()
                    return dict(r)
        raise RoomError("no_such_room", {"id": rid})


class RoomRegistry:
    """
    房號 → 那一間的那一組服務。

    `factory(room_id)` 由 main.py 提供（它才認識 QueueManager 要哪些依賴），
    這裡只負責「每一間都有一份、而且只建一次」。
    """

    def __init__(self, roster: RoomRoster, factory: Callable[[str], Any]):
        self._roster = roster
        self._factory = factory
        self._lock = threading.RLock()
        self._bundles: Dict[str, Any] = {}
        for rid in roster.ids():
            self._build(rid)

    def _build(self, room_id: str) -> Any:
        bundle = self._factory(room_id)
        self._bundles[room_id] = bundle
        return bundle

    def ids(self) -> List[str]:
        return self._roster.ids()

    def rows(self) -> List[Dict[str, Any]]:
        return self._roster.rows()

    def get(self, room_id: Any) -> Optional[Any]:
        """
        這一間的那一組服務。認不得的房號回 None ——
        呼叫端負責變成 404（決定三：絕不靜靜退回 default）。
        """
        rid = normalize_id(room_id)
        if not rid:
            return None
        with self._lock:
            return self._bundles.get(rid)

    def items(self) -> List[Any]:
        """照名冊順序列出每一間的服務組。櫃檯總覽與心跳迴圈都靠它。"""
        with self._lock:
            return [self._bundles[r] for r in self._roster.ids() if r in self._bundles]

    def create(self, name: Any = "", room_id: Any = None) -> Dict[str, Any]:
        with self._lock:
            row = self._roster.add(name, room_id)
            self._build(row["id"])
            return row

    def rename(self, room_id: str, name: Any) -> Dict[str, Any]:
        return self._roster.rename(room_id, name)

    def remove(self, room_id: str) -> Dict[str, Any]:
        with self._lock:
            row = self._roster.remove(room_id)
            self._bundles.pop(row["id"], None)
            return row
