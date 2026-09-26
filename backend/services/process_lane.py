"""
處理車道（一台機器只有一顆 CPU，流水線要排隊而且要輪流）

多包廂之前，點一首沒快取的歌就直接 `asyncio.create_task` 把流水線丟出去。
一間包廂的時候最多重疊兩三首，勉強撐得住；十間包廂同時點新歌，十個 Demucs
一起跑的結果是**每一間都等更久**（記憶體換頁與快取互相踩，總時間比排隊跑
還長），而且沒有任何一間看得出自己排在哪。

所以整台機器開一條車道：同時只跑一首，入場照規則挑。

---

**決定一：不是先到先跑。**

先到先跑的話，A 包廂一次貼十首播放清單，B 包廂的第一首歌要等完那十首
（一首十分鐘 = 一個半小時）。B 那一桌從頭到尾不知道發生什麼事，只看到
進度條停在 0%。所以入場是**輪流**的：挑「最久沒輪到的那一間」，
同一間的第二首要等其他每一間都排過一輪之後才會再輪到。

**決定二：台上沒歌在唱的包廂優先。**

輪流之上還有一條例外。一間包廂如果現在**沒有歌在播**，它等的是「有沒有
得唱」；正在唱歌的包廂等的是「下一首」。前者的等待是一片安靜，後者的
等待是背景 —— 同樣三分鐘，一個是尷尬，一個沒有人察覺。
這不會餓死任何人：一間包廂拿到歌就開始唱了，下一次它就不再是急件。

**決定三：排程預處理永遠排最後。**

半夜的批次任務跟客人搶的是同一顆 CPU。它已經會在有人唱歌時讓開
（`busy_cb`），但那只擋得住「還沒開始的那一首」；已經跑到一半的 Demucs
不會因為有人點歌就停下來。車道解的是後半段：批次任務照樣要排隊，
而且永遠排在所有包廂後面 —— 它等一小時沒有人會知道，客人等三分鐘所有人都在看。

**決定四：等待要看得見。**

排隊最難受的不是久，是不知道還要多久。所以每一次有人進場或離場，
還在等的每一張票都會收到自己的新位置，佇列上那一行會寫
「排隊等處理（前面還有 2 首）」而不是一個不動的 0%。
位置是**估計值**（急件會插到前面去），所以畫面上講的是「前面還有幾首」
而不是「還要幾分鐘」—— 後者猜錯了會比不講更糟。
"""
import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("KaraTube.ProcessLane")

# 入場順位的三個等級（數字小的先）。
CLASS_URGENT = 0   # 台上沒歌在唱的包廂
CLASS_ROOM = 1     # 一般包廂
CLASS_BATCH = 2    # 排程預處理


def sort_key(ticket: Dict[str, Any], served: Dict[str, int]) -> tuple:
    """
    入場順序的排序鍵（小的先）：`(等級, 這一間上次輪到的序號, 進場序號)`。

    第二項是輪流的全部內容 —— 從來沒輪到過的房間是 -1，所以一定排在
    已經跑過一首的房間前面；跑過的房間帶著「上一次輪到時的序號」，
    誰的越舊誰越前面。第三項只在前兩項打平時決定（同一間同時排兩首）。

    拆成純函式是為了讓「輪流真的有輪到」可以用一張表測出來，
    不必真的跑十個 Demucs。
    """
    return (int(ticket.get("klass", CLASS_ROOM)),
            int(served.get(ticket.get("room", ""), -1)),
            int(ticket.get("seq", 0)))


def order_tickets(tickets: List[Dict[str, Any]],
                  served: Dict[str, int]) -> List[Dict[str, Any]]:
    """把等待中的票照入場順序排好。第一張就是下一個進場的。"""
    return sorted(tickets, key=lambda t: sort_key(t, served))


class _Ticket:
    __slots__ = ("room", "seq", "klass", "urgent_fn", "on_wait", "admitted")

    def __init__(self, room: str, seq: int, klass: int,
                 urgent_fn: Optional[Callable[[], bool]],
                 on_wait: Optional[Callable[[int, int], None]]):
        self.room = room
        self.seq = seq
        self.klass = klass
        self.urgent_fn = urgent_fn
        self.on_wait = on_wait
        self.admitted = asyncio.Event()

    def snapshot(self) -> Dict[str, Any]:
        """排序用的純資料。急件與否在**挑的當下**才問 —— 一間包廂可能在
        排隊的這三分鐘裡唱完了手上那一首，那一刻它才變成急件。"""
        klass = self.klass
        if klass == CLASS_ROOM and self.urgent_fn is not None:
            try:
                if self.urgent_fn():
                    klass = CLASS_URGENT
            except Exception:
                pass
        return {"room": self.room, "seq": self.seq, "klass": klass, "ticket": self}


class ProcessLane:
    """
    整台機器的流水線車道。

    用法是把真正要跑的工作包成一個 coroutine factory 交給 `run()`：
    車道負責「什麼時候輪到你」，工作本身在做什麼它不認識。
    """

    def __init__(self, max_concurrent: int = 1):
        self._max = max(1, int(max_concurrent))
        self._running: Dict[str, int] = {}
        self._running_count = 0
        self._waiting: List[_Ticket] = []
        # 每一間上一次輪到時的序號。輪流全靠這張表（見 sort_key）。
        self._served: Dict[str, int] = {}
        self._seq = 0

    # --- 內部 ---

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _pump(self):
        """有空位就放人進來。挑法見 sort_key。"""
        while self._running_count < self._max and self._waiting:
            ordered = order_tickets([t.snapshot() for t in self._waiting], self._served)
            chosen = ordered[0]["ticket"]
            self._waiting.remove(chosen)
            self._served[chosen.room] = self._next_seq()
            self._running_count += 1
            self._running[chosen.room] = self._running.get(chosen.room, 0) + 1
            chosen.admitted.set()

    def _notify(self):
        """告訴每一張還在等的票：你現在排第幾（決定四）。"""
        if not self._waiting:
            return
        ordered = order_tickets([t.snapshot() for t in self._waiting], self._served)
        total = len(ordered)
        for idx, row in enumerate(ordered):
            ticket = row["ticket"]
            if ticket.on_wait is None:
                continue
            try:
                ticket.on_wait(idx, total)
            except Exception as e:
                # 通知畫面失敗不該影響排隊本身
                logger.warning(f"車道位置通知失敗: {e}")

    def _release(self, room: str):
        self._running_count = max(0, self._running_count - 1)
        left = self._running.get(room, 0) - 1
        if left > 0:
            self._running[room] = left
        else:
            self._running.pop(room, None)
        self._pump()
        self._notify()

    # --- 對外 ---

    def snapshot(self) -> Dict[str, Any]:
        """現在誰在跑、誰在等。櫃檯總覽與 /api/rooms/overview 用得到。"""
        ordered = order_tickets([t.snapshot() for t in self._waiting], self._served)
        return {
            "running": sorted(self._running.keys()),
            "running_count": self._running_count,
            "capacity": self._max,
            "waiting": len(self._waiting),
            "waiting_rooms": [row["room"] for row in ordered],
        }

    def waiting_for(self, room: str) -> int:
        return sum(1 for t in self._waiting if t.room == room)

    async def run(self, room: str, work: Callable[[], Any], *,
                  klass: int = CLASS_ROOM,
                  urgent: Optional[Callable[[], bool]] = None,
                  on_wait: Optional[Callable[[int, int], None]] = None) -> Any:
        """
        排隊，輪到了就跑 `work()`，跑完（含丟例外）一定讓出位置。

        `on_wait(ahead, total)` 在還要等的時候會被呼叫至少一次，之後每當
        車道有人進出就再呼叫一次 —— 那一行字要一直是對的，不然它比沒有更糟。
        """
        ticket = _Ticket(str(room or ""), self._next_seq(), int(klass), urgent, on_wait)
        self._waiting.append(ticket)
        self._pump()
        if not ticket.admitted.is_set():
            self._notify()
            await ticket.admitted.wait()
        try:
            return await work()
        finally:
            self._release(ticket.room)
