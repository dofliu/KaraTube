"""
舞台訊息（跑馬燈）

商用點歌機都有這一條：櫃檯把字打到包廂的螢幕上 ——「您的餐點到了」、
「A 桌張先生生日快樂」、「您的歡唱時間剩 20 分鐘」。功能本身三行就寫得完
（存一段字、推出去、顯示），難的是那段字**會蓋掉畫面上的什麼**。

舞台那一面螢幕上已經沒有空地了：上緣是歌名與分數，中間是音準線，
下半整片是歌詞。一則訊息不管放哪裡都在搶某個東西的位置，而唯一不能搶的
就是歌詞 —— 台上那個人正在看它唱歌。所以這一支的設計是從「這則訊息要
蓋掉誰」倒推回來的：

決定一：訊息只走畫面最上緣，而且是「推開」不是「蓋住」
    訊息帶固定在最上緣，出現的時候整排 HUD（歌名、倒數、分數）往下讓位，
    不是絕對定位疊上去。差別在於：疊上去遲早會壓到某個字，而且那件事只有
    在包廂裡、只有在訊息跳出來的那幾秒才看得到（開發時不會遇到）。
    推開則是版面自己算出來的，永遠不會疊到任何東西。
    歌詞與音準線那半邊完全不動 —— 那是這個功能唯一不准碰的區域。

決定二：大字卡只在沒有人唱歌的時候出現
    櫃檯要的是「一定看得到」。但「一定看得到」在副歌那一句等於「一定擋到」。
    所以同一則訊息有兩種身體：沒有在播歌時是置中的大字卡（看一眼就知道），
    正在播歌時自動降級成上緣那一條。緊急訊息也一樣降級 —— 它換到的優待是
    插到隊伍最前面、而且亮得比較久，不是「可以蓋住歌詞」。

決定三：訊息會過期，而且預設就會
    「您的餐點到了」在四十分鐘之後才跳出來是**錯的資訊**，比沒有訊息更糟。
    每一則預設十分鐘後自己消失，要整晚都在的（生日祝福）必須明講「釘住」，
    而釘住的也有上限（PINNED_MAX_HOURS）—— 沒有一則訊息值得在螢幕上待到
    下一桌客人進來。

決定四：一次只講一件事
    三則訊息同時進來時排隊輪播，不是三條一起掛上去。兩行以上的跑馬燈沒有
    人讀得完，而讀不完等於全都沒讀到。

決定五：長度上限是硬的（MAX_TEXT_CHARS）
    舞台那一條只有一行。一段一百字的訊息在上面會變成一條跑很久的字幕，
    唱歌的人會一直用餘光追它。與其讓它跑完，不如在送出的時候就講明白
    「這裡只能寫一句話」。

決定六：不持久化
    計時要存檔（客人買了多久不該因為重開機而重算），訊息剛好相反：伺服器
    重開之後最可能的狀況是「那件事早就處理完了」，而一則沒有人記得的舊訊息
    自己跳到螢幕上，看起來就像機器壞了。釘住的祝福重打一次只要五秒。

這一支只管「有哪些訊息、哪些還有效」。要怎麼顯示（輪播到第幾則、是字幕帶
還是大字卡）是舞台的事，寫在 `frontend/js/marquee-view.js`。
"""
import logging
import threading
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger("KaraTube.Marquee")

# 一則訊息最多幾個字（見決定五）。40 字大約是一行中文標題的長度，
# 在 1080p 的舞台上一眼讀得完。
MAX_TEXT_CHARS = 40

# 同時最多幾則待播。超過就不收 —— 排到第九則的訊息輪到它時，
# 那件事早就過去了（而且前八則已經把螢幕上緣佔滿一整輪）。
MAX_MESSAGES = 8

# 每一則在螢幕上停留幾秒（輪播的一輪）。
DEFAULT_SHOW_SECONDS = 8.0
MIN_SHOW_SECONDS = 3.0
MAX_SHOW_SECONDS = 30.0

# 多久之後自己消失（見決定三）。
DEFAULT_TTL_MINUTES = 10
MIN_TTL_MINUTES = 1
MAX_TTL_MINUTES = 240

# 釘住的訊息最多活多久。沒有「永不過期」這個選項：散場之後還掛在螢幕上的
# 生日祝福，是下一桌客人看到的第一個東西。
PINNED_MAX_HOURS = 4


def clean_text(text: Any) -> str:
    """
    把送進來的字收成「一行」。

    換行在舞台那一條上沒有意義（它只有一行高），但貼上來的文字常常帶著換行；
    直接原樣存起來的話，顯示時會變成中間多一個看不出來的空白。
    """
    raw = "" if text is None else str(text)
    # 換行、tab 一律收成空白，再把連續空白壓成一個
    flattened = " ".join(raw.replace("\r", " ").replace("\n", " ").split())
    return flattened[:MAX_TEXT_CHARS]


def coerce_seconds(value: Any, default: float = DEFAULT_SHOW_SECONDS) -> float:
    """顯示秒數。看不懂的值退回預設值，不是拋錯（手機端送怪東西不該讓訊息發不出去）。"""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return float(default)
    if seconds != seconds:  # NaN
        return float(default)
    return float(min(MAX_SHOW_SECONDS, max(MIN_SHOW_SECONDS, seconds)))


def coerce_ttl_minutes(value: Any, default: int = DEFAULT_TTL_MINUTES) -> int:
    """存活時間（分鐘）。同樣夾限，同樣看不懂就退回預設值。"""
    try:
        minutes = int(round(float(value)))
    except (TypeError, ValueError):
        return int(default)
    return int(min(MAX_TTL_MINUTES, max(MIN_TTL_MINUTES, minutes)))


class MarqueeRejected(Exception):
    """
    這則訊息不收。

    跟額度、計時那兩支同一個寫法：帶著原因與現況丟出來，API 層才講得出
    「最多同時八則，等一則播完或刪掉一則再送」—— 只丟一句字串的話，
    那句話就得在服務層裡拼，而那是畫面的工作。
    """

    def __init__(self, reason: str, detail: Dict[str, Any] | None = None):
        self.reason = reason
        self.detail = detail or {}
        super().__init__(reason)


class MarqueeBoard:
    """
    現在有哪些訊息要播。

    刻意不落地（見決定六），所以沒有 state_file 參數 —— 重開機就是空的。
    時間一律從外面傳進來（`now`），測試不必真的等十分鐘。
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._messages: List[Dict[str, Any]] = []

    # --- 查詢 ---

    def _alive(self, msg: Dict[str, Any], now: datetime) -> bool:
        try:
            expires = datetime.fromisoformat(str(msg.get("expires_at")))
        except (TypeError, ValueError):
            # 過期時間壞掉的訊息當成過期：寧可少播一則，也不要留下一則
            # 永遠不會消失、而且沒有人知道怎麼刪的字。
            return False
        return now < expires

    def messages(self, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """
        還有效的訊息，照播放順序排。

        緊急的插到最前面（那正是「緊急」的全部意思），其餘照送出的先後；
        釘住的排在最後 —— 它會待上好幾個小時，每一輪都讓剛送出來的先講。
        """
        now = now or datetime.now()
        with self._lock:
            alive = [dict(msg) for msg in self._messages if self._alive(msg, now)]
        alive.sort(key=lambda m: (bool(m.get("pinned")), not m.get("urgent"),
                                  str(m.get("created_at"))))
        return alive

    def snapshot(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """畫面與 API 看的都是這一份。"""
        now = now or datetime.now()
        alive = self.messages(now)
        return {
            "messages": alive,
            "count": len(alive),
            "max_messages": MAX_MESSAGES,
            "max_chars": MAX_TEXT_CHARS,
            "updated_at": now.isoformat(timespec="seconds"),
        }

    # --- 操作 ---

    def post(self, text: Any, sender: str = "", urgent: bool = False, pinned: bool = False,
             ttl_minutes: Any = None, seconds: Any = None,
             now: Optional[datetime] = None) -> Dict[str, Any]:
        """
        送一則到舞台上。

        空白訊息與滿了都丟 `MarqueeRejected` —— 靜靜地不收是最糟的回應：
        送出的人會以為螢幕上已經有字了，然後對著客人說「您看一下螢幕」。
        """
        now = now or datetime.now()
        body = clean_text(text)
        if not body:
            raise MarqueeRejected("empty", {"max_chars": MAX_TEXT_CHARS})

        show_seconds = coerce_seconds(seconds)
        minutes = coerce_ttl_minutes(ttl_minutes)
        if pinned:
            # 釘住的用同一個上限天花板（見決定三）：釘住是「久一點」，不是「永遠」。
            expires = now + timedelta(hours=PINNED_MAX_HOURS)
        else:
            expires = now + timedelta(minutes=minutes)

        message = {
            "id": uuid.uuid4().hex[:12],
            "text": body,
            # 誰送的只給點歌台看（清單上要分得出是誰打的），舞台上不顯示：
            # 台下的人要看的是那句話，不是誰打的。
            "sender": clean_text(sender)[:20],
            "urgent": bool(urgent),
            "pinned": bool(pinned),
            "seconds": show_seconds,
            "created_at": now.isoformat(timespec="seconds"),
            "expires_at": expires.isoformat(timespec="seconds"),
        }

        with self._lock:
            # 先清掉過期的再數：滿了的那一刻常常有一半是早就該消失的訊息，
            # 為了它們擋下一則新的會讓人以為這個功能壞了。
            self._messages = [m for m in self._messages if self._alive(m, now)]
            if len(self._messages) >= MAX_MESSAGES:
                raise MarqueeRejected("full", {"count": len(self._messages),
                                               "max_messages": MAX_MESSAGES})
            self._messages.append(message)
        logger.info("舞台訊息：%s（%s 秒一輪，%s）", body, show_seconds,
                    "釘住" if pinned else f"{minutes} 分鐘後消失")
        return dict(message)

    def remove(self, message_id: str, now: Optional[datetime] = None) -> bool:
        """撤掉一則（打錯字、那件事已經處理完了）。回傳有沒有真的撤到。"""
        now = now or datetime.now()
        with self._lock:
            before = len(self._messages)
            self._messages = [m for m in self._messages if m.get("id") != str(message_id)]
            return len(self._messages) != before

    def clear(self, include_pinned: bool = True, now: Optional[datetime] = None) -> int:
        """
        全部撤掉，回傳撤了幾則。

        `include_pinned=False` 留下釘住的那些：「把剛剛那幾則清掉」跟
        「連生日祝福也拿掉」是兩個不同的意思，而後者通常不是按下去的人要的。
        """
        now = now or datetime.now()
        with self._lock:
            keep = [m for m in self._messages
                    if (not include_pinned and m.get("pinned") and self._alive(m, now))]
            removed = len(self._messages) - len(keep)
            self._messages = keep
            return removed

    def prune(self, now: Optional[datetime] = None) -> int:
        """
        丟掉過期的，回傳丟了幾則。

        畫面自己也會濾掉過期的（伺服器五秒才跑一次心跳，而「十分鐘後消失」
        差五秒就不叫十分鐘了），這裡是為了不讓清單無限長下去 ——
        以及讓點歌台的訊息清單跟舞台上看到的是同一份。
        """
        now = now or datetime.now()
        with self._lock:
            before = len(self._messages)
            self._messages = [m for m in self._messages if self._alive(m, now)]
            return before - len(self._messages)
