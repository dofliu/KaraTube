"""
公平輪唱（排麥輪序）

包廂裡最常吵的從來不是音準，是**順序**。一個人連點五首，其他人就得等五首；
後到的朋友想唱一首，排在最後面，等到散場都還沒輪到。

商用點歌機沒有解這一題 —— 它們的佇列就是先到先唱（誰手快誰先唱）。真正在解
這一題的是卡拉OK 酒吧的 KJ：每個人輪流唱一首，唱完就排到隊伍後面去。
這一支就是那個 KJ。

設計上有三個決定，每一個都是「想過另一種做法、而且那一種會壞在哪」：

決定一：只決定「插進哪裡」，不重排既有的順序
    直覺的寫法是「每次狀態變動就照輪次把整個佇列重排一次」。這個寫法在畫面上
    是災難：佇列是所有人盯著看的東西（「我那首還有幾首就到了」），每多一首歌
    整排就跳一次位置，而且會把剛剛有人**手動拖曳**的結果與**插播**一起吃掉。
    所以輪序只在點歌的那一瞬間決定插入位置，插進去之後那一首就待在那裡，
    拖曳與插播照舊有效。

決定二：身分只認暱稱，不認裝置
    用裝置 id（或 IP）當身分很好寫，但包廂裡的手機是**傳來傳去**的 ——
    「欸你手機借我點一首」是這個場景的常態，同一支手機背後是好幾個人；
    反過來同一個人也可能一邊用點歌台、一邊用自己的手機點。
    所以身分就是他自己打的那個暱稱，正規化（收斂空白、不分大小寫）之後比對。

    沒取暱稱的人全部算**同一個人**。這件事看起來粗糙，其實是刻意的：
    一整間都沒取暱稱時，輪唱的行為就完全等於沒開（一路往後排），
    不會有人發現「我點的歌莫名其妙跑到後面去」而不知道發生什麼事。
    畫面上會提示「取個暱稱才排得進輪序」。

決定三：輪次要算「已經唱過的」，不是只算佇列裡的
    只算佇列裡待唱的首數會壞在唱完之後：A 唱完自己那首、佇列裡剩 B 的一首，
    A 再點一首時「待唱 0 首」＝ 第 1 輪，於是排到還沒唱過的 B 前面去 ——
    正好是這個功能要消滅的那件事。所以輪次 = 今晚已經唱過的 + 佇列裡待唱的 + 1。

純資料邏輯：不碰 DOM、不發網路請求、不做 I/O。
"""
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 沒取暱稱的人共用這一個 key（見決定二）
ANON_KEY = ""

# 一場的空檔預設值。與整晚打包的 recording_session_gap_hours 是**同一個概念**，
# 所以設定頁共用同一個欄位：系統裡「一場」只能有一個定義，兩個各自可調的話
# 使用者會看到「打包說這是同一場、輪序說換了一場」這種自相矛盾的畫面。
DEFAULT_SESSION_GAP_HOURS = 6.0

# 暱稱顯示長度，與 QueueManager 的 requested_by 一致
MAX_NAME_LEN = 24


def singer_key(name: Any) -> str:
    """
    把暱稱正規化成比對用的 key。

    收斂空白（「小明 」與「小明」是同一個人，手機輸入法很容易多打一個空白）、
    中間的連續空白併成一個、再 casefold（「Amy」與「amy」是同一個人）。
    """
    return " ".join(str(name or "").split()).casefold()


def display_name(name: Any) -> str:
    """畫面上顯示的暱稱：只收斂空白，保留原本的大小寫。"""
    return " ".join(str(name or "").split())[:MAX_NAME_LEN]


def _key_of(item: Dict[str, Any]) -> str:
    return singer_key(item.get("requested_by"))


def locked_prefix_len(queue: Sequence[Dict[str, Any]]) -> int:
    """
    佇列最前面那一段「輪序不准插到它前面」的長度 —— 也就是連續的插播。

    插播是有人**當場按下去**的決定（生日歌、朋友要走了想唱最後一首），
    輪序是機器的規則。機器的規則不該推翻現場的人剛剛做的決定，
    所以插播對輪序免疫；反過來輪序也不會把插播往後推。
    """
    n = 0
    for item in queue:
        if not item.get("priority"):
            break
        n += 1
    return n


def compute_rounds(queue: Sequence[Dict[str, Any]],
                   sung_counts: Optional[Dict[str, int]] = None) -> List[int]:
    """
    佇列裡每一首是那個人的「第幾輪」。

    第 N 輪的意思是「這是他今晚的第 N 首」（含已經唱完的）。
    輪次是算出來的、不是存在歌曲上的：有人被刪、有人唱完，剩下的都要跟著變，
    存在 queue item 上只會有一份對不上現況的舊資料。
    """
    sung = sung_counts or {}
    seen: Dict[str, int] = {}
    rounds: List[int] = []
    for item in queue:
        key = _key_of(item)
        seen[key] = seen.get(key, 0) + 1
        rounds.append(int(sung.get(key, 0)) + seen[key])
    return rounds


def plan_insert_index(queue: Sequence[Dict[str, Any]],
                      sung_counts: Optional[Dict[str, int]],
                      singer: Any) -> Tuple[int, int]:
    """
    這一首該插在佇列的哪一格，以及它是這位演唱者的第幾輪。

    規則：插在「第一首輪次比我大的歌」前面 —— 也就是排在所有同輪次（與更早輪次）
    的歌後面。同輪次之間維持先到先唱，這樣「大家各唱一首」的那一輪裡，
    順序還是照點歌的先後，不會因為輪唱而變成隨機。

    找不到輪次比我大的就排到最後（我是目前排最多的那個人）。
    插播段（見 locked_prefix_len）一律跳過，絕不插到它前面。
    """
    rounds = compute_rounds(queue, sung_counts)
    key = singer_key(singer)
    pending = sum(1 for item in queue if _key_of(item) == key)
    my_round = int((sung_counts or {}).get(key, 0)) + pending + 1

    for i in range(locked_prefix_len(queue), len(queue)):
        if rounds[i] > my_round:
            return i, my_round
    return len(queue), my_round


def rotation_summary(queue: Sequence[Dict[str, Any]],
                     sung_counts: Optional[Dict[str, int]] = None,
                     names: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """
    給畫面用的輪序全貌：每一首的輪次 + 每個人唱了幾首／還有幾首。

    `names` 是「唱過但佇列裡已經沒有歌」的人的暱稱（佇列上找不到他的名字了，
    但今晚的統計要算他一份），由 RotationTracker 提供。
    """
    sung = dict(sung_counts or {})
    rounds = compute_rounds(queue, sung)

    people: Dict[str, Dict[str, Any]] = {}

    def slot(key: str, name: str = "") -> Dict[str, Any]:
        entry = people.get(key)
        if entry is None:
            entry = {
                "key": key,
                "name": name,
                "anonymous": key == ANON_KEY,
                "sung": int(sung.get(key, 0)),
                "pending": 0,
                "next_round": None,
                # 他下一首在佇列的第幾格。排序用的，不外流給畫面。
                "_at": None,
            }
            people[key] = entry
        elif name and not entry["name"]:
            entry["name"] = name
        return entry

    for key, count in sung.items():
        slot(key, display_name((names or {}).get(key, "")))
        people[key]["sung"] = int(count)

    for at, (item, rnd) in enumerate(zip(queue, rounds, strict=True)):
        entry = slot(_key_of(item), display_name(item.get("requested_by")))
        entry["pending"] += 1
        if entry["next_round"] is None:
            entry["next_round"] = rnd
            entry["_at"] = at

    # 排序：還有歌要唱的排前面，而且照**他下一首在佇列的位置** —— 也就是真正的
    # 上台順序。照輪次排看起來也對，但手動拖曳過的佇列會讓兩者分岔，
    # 那時畫面講的順序就跟實際播的順序不一樣了。
    # 都唱完的排後面（今晚唱得多的在前），同分照 key，免得每次廣播人名都在跳。
    def sort_key(entry: Dict[str, Any]):
        if entry["pending"]:
            return (0, entry["_at"], "")
        return (1, -entry["sung"], entry["key"])

    singers = sorted(people.values(), key=sort_key)
    for entry in singers:
        entry.pop("_at", None)

    return {
        "rounds": {item.get("queue_id"): rnd for item, rnd in zip(queue, rounds, strict=True)},
        "singers": singers,
        # 有幾個真的分得出來的人（沒取暱稱的那一桶不算）。畫面靠它決定要不要
        # 提示「取個暱稱才排得進輪序」—— 全部都沒取名時輪唱等於沒開。
        "named_count": sum(1 for entry in people.values() if not entry["anonymous"]),
    }


class RotationTracker:
    """
    今晚誰唱了幾首。

    刻意只活在記憶體裡：佇列本來就不持久化（重開機之後是空的），輪序沒有理由
    比佇列活得久 —— 重開機後佇列空空如也、輪序卻還記得「你已經唱了五首」，
    第一首歌就會被排到莫名其妙的位置，而且沒有人看得出為什麼。
    """

    def __init__(self):
        self._sung: Dict[str, int] = {}
        self._names: Dict[str, str] = {}
        self._last_play_at: Optional[datetime] = None

    def counts(self) -> Dict[str, int]:
        return dict(self._sung)

    def names(self) -> Dict[str, str]:
        return dict(self._names)

    @property
    def last_play_at(self) -> Optional[datetime]:
        return self._last_play_at

    def record_play(self, item: Dict[str, Any],
                    gap_hours: float = DEFAULT_SESSION_GAP_HOURS,
                    now: Optional[datetime] = None) -> bool:
        """
        有一首歌真的上台了。回傳「這一首是不是新的一場的第一首」。

        隔了夠久（預設 6 小時）就是下一桌客人了，統計要歸零 —— 不歸零的話
        昨晚唱了八首的那個暱稱，今晚第一首就被排到所有人後面。

        時間**倒退**（NTP 校時、有人改系統時間）時不算換場：差值是負的，
        本來就不會超過門檻。寧可少切一場，也不要把同一場切成兩半。
        """
        now = now or datetime.now()
        rolled = False
        if (self._last_play_at is not None and gap_hours > 0
                and now - self._last_play_at > timedelta(hours=gap_hours)):
            self._sung.clear()
            self._names.clear()
            rolled = True
        self._last_play_at = now

        key = _key_of(item)
        self._sung[key] = self._sung.get(key, 0) + 1
        name = display_name(item.get("requested_by"))
        if name:
            self._names[key] = name
        return rolled

    def reset(self):
        """手動重新開始輪序（換一批客人、或是大家講好重新排）。"""
        self._sung.clear()
        self._names.clear()
        self._last_play_at = None
