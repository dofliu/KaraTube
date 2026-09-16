"""
包廂計時（歡唱時間與「剩下多久」）

商用點歌機都有這一條：包廂買了三小時，機器要倒數，時間快到要提醒，時間到要
收場。難的從來不是倒數 —— 倒數是減法。難的是**時間到之後怎麼辦**。

商用機的做法是直接停掉正在唱的那一首。那是這整件事裡最糟的一種收法：被停掉
的那個人正站在包廂中間、麥克風在他手上、他剛唱到副歌，然後畫面黑掉。他不會
覺得「時間到了」，他會覺得「機器在我唱到一半的時候把我關掉」。同一分鐘之內
本來可以好好收場的一個晚上，變成一個難堪的畫面。

所以這一支的設計是從「最後一分鐘長什麼樣子」倒推回來的：

決定一：時間到不停歌，停的是「下一首」
    時間到的時候，正在唱的那一首**唱完**。唱完之後機器就停在那裡，不自動
    播下一首。超時的上限因此是一首歌的長度（三到五分鐘），是可預期的；
    相對的，停在副歌那一句是不可預期的難堪。用三分鐘換掉那個畫面很划算。

    這件事要在提醒的時候就先講明白（「時間到會讓你唱完這一首」），
    不然最後一首的點歌者會以為自己被偷走一首歌。

決定二：佇列一首都不刪
    時間到之後佇列原封不動留在畫面上。刪掉的話，續時之後大家得重點一輪
    （而且沒有人記得剛剛排了什麼）；更糟的是「時間到」與「佇列被清空」
    在畫面上是同一秒發生的，看起來就像機器當掉了。
    停下來的是播放，不是資料。

決定三：續時是加時間，不是重開一場
    續時 30 分鐘就是 total += 30，已經唱過的、輪序、額度全都不動 ——
    續的是同一場，不是新的一場。而且續時之後如果機器是停著的，
    它要自己接回去播下一首：按了「續時」還得再按一次「播放」的話，
    那顆續時鍵看起來就沒有反應。

決定四：提醒要早、要少、而且要講「時間到會發生什麼事」
    預設提醒兩次（剩 10 分鐘、剩 3 分鐘）加上時間到那一次。每一個門檻只講
    一次 —— 每 30 秒跳一次的提醒會被當成雜訊直接無視，然後真正重要的
    那一次也一起被無視。續時之後重新變得「還沒到」的門檻會重新武裝，
    因為續了 30 分鐘之後那個 10 分鐘提醒又變成有意義的資訊了。

決定五：暫停就是真的停錶
    餐點來了、中場休息、有人要接電話。暫停期間不計時，恢復之後接著算。
    這是包廂會用到的功能，不是後台的除錯開關。

決定六：計時存檔，重開機接得回來
    佇列不持久化（重開機就是空的），但計時不一樣：它對應的是「客人買了多久」，
    這件事不該因為伺服器重開而重算或歸零。存檔擺太久（超過 STALE_HOURS）
    才丟掉 —— 那代表機器關過一整晚，昨天那一場早就散了。

這一支只管時間，不碰佇列也不碰播放：要不要停播是 QueueManager 的決定
（見 `QueueManager.play_next`），這裡只回答「現在幾點了、還剩多久」。
"""
import json
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("KaraTube.RoomTimer")

# 一場的預設長度（分鐘）。3 小時是包廂最常見的一個單位。
DEFAULT_SESSION_MINUTES = 180
# 一場的上下限。10 分鐘以下不是一場（是按錯），24 小時以上等於沒有計時。
MIN_SESSION_MINUTES = 10
MAX_SESSION_MINUTES = 1440

# 「續時」一次加多久的預設值與上下限
DEFAULT_EXTEND_MINUTES = 30
MIN_EXTEND_MINUTES = 5
MAX_EXTEND_MINUTES = 240

# 提醒門檻的預設值（分鐘）。0 = 不提醒。
DEFAULT_WARN_MINUTES = 10
DEFAULT_LAST_CALL_MINUTES = 3

# 時間到之後怎麼辦：
#   finish_song —— 讓正在唱的那一首唱完，然後停住不播下一首（見決定一，預設）
#   notify_only —— 只提醒，什麼都不停（自己算時間的家用場景）
# 刻意**沒有**「立刻停掉正在唱的那一首」這個選項：那正是這個功能要消滅的畫面，
# 把它做成一個選項等於把它留在那裡等人選到。
EXPIRE_ACTION_CHOICES = ("finish_song", "notify_only")
DEFAULT_EXPIRE_ACTION = "finish_song"

# 存檔擺著超過這麼久就不接續（見決定六）。12 小時的意思是「機器關過一個晚上」——
# 昨天那一場不管當時剩幾分鐘，今天開機都不該把它接回來。
STALE_HOURS = 12.0

# 狀態。畫面上要說的話完全不同，所以分成四種而不是一個 bool：
#   off     沒有在計時（預設）
#   running 倒數中
#   paused  停錶中（中場休息）
#   expired 時間到了（正在唱的那一首還是會唱完）
STATE_OFF = "off"
STATE_RUNNING = "running"
STATE_PAUSED = "paused"
STATE_EXPIRED = "expired"


def coerce_minutes(value: Any, default: int = DEFAULT_SESSION_MINUTES,
                   low: int = MIN_SESSION_MINUTES, high: int = MAX_SESSION_MINUTES) -> int:
    """
    把「幾分鐘」收斂成合法值。

    看不懂（空字串、文字、None）退回預設值而不是拋錯：這個數字會從手機端、
    設定檔、舊版前端三個地方送進來，其中任何一個送了怪東西時，正確的行為是
    「照預設開一場」，不是讓整個計時功能掛掉。
    """
    try:
        minutes = int(round(float(value)))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, minutes))


def default_milestones(warn_minutes: Any = DEFAULT_WARN_MINUTES,
                       last_call_minutes: Any = DEFAULT_LAST_CALL_MINUTES
                       ) -> List[Tuple[str, int]]:
    """
    提醒門檻（(種類, 剩餘秒數) 由遠到近）。0 或看不懂 = 不提醒那一次。

    兩個門檻撞在一起（都設 5 分鐘）時只留一個：同一秒鐘跳兩個意思一樣的提醒
    只會讓使用者覺得機器在鬼叫。
    """
    out: List[Tuple[str, int]] = []
    seen = set()
    for kind, raw in (("warn", warn_minutes), ("last_call", last_call_minutes)):
        try:
            minutes = int(round(float(raw)))
        except (TypeError, ValueError):
            continue
        if minutes <= 0:
            continue
        seconds = minutes * 60
        if seconds in seen:
            continue
        seen.add(seconds)
        out.append((kind, seconds))
    out.sort(key=lambda pair: -pair[1])
    return out


class RoomTimeUp(Exception):
    """
    歡唱時間已經到了，這一首不收。

    跟額度那一支同樣的寫法：帶著整份快照丟出來，API 層才講得出「時間到了、
    續時 30 分鐘就接著唱」—— 只丟一句字串的話，那句話就得在 QueueManager 裡拼，
    而那是畫面的工作（點歌台與手機端的說法可能不一樣）。
    """

    def __init__(self, snapshot: Dict[str, Any]):
        self.snapshot = snapshot
        super().__init__("歡唱時間已結束")


class RoomTimer:
    """
    這一場買了多久、用掉多久、還剩多久。

    `state_file` 給 None 就只活在記憶體裡（測試與沒有磁碟的呼叫端用）。
    時間一律從外面傳進來（`now`），這樣測試不必真的等三小時。
    """

    def __init__(self, state_file: Optional[Path] = None):
        self.state_file = Path(state_file) if state_file else None
        self._lock = threading.RLock()
        self._active = False
        self._total_seconds = 0
        # 已經用掉的秒數裡，「停錶之前累積的那些」。目前這一段（還在跑的）不算在內，
        # 由 _started_at 即時算出來 —— 兩邊都存的話會有一份資料是舊的。
        self._banked_seconds = 0.0
        self._started_at: Optional[datetime] = None
        self._halted = False
        self._fired: set = set()
        self._load()

    # --- 內部：時間計算 ---

    def _elapsed(self, now: datetime) -> float:
        """用掉的秒數。停錶中就是累積值，跑錶中再加上這一段。"""
        if self._started_at is None:
            return self._banked_seconds
        # 時間倒退（NTP 校時、有人改系統時間）時這一段算 0，而不是負的：
        # 少算一點時間對客人有利，倒扣回去則是憑空多出來的時間，
        # 而那會變成「我明明還有半小時」的爭執。
        delta = (now - self._started_at).total_seconds()
        return self._banked_seconds + max(0.0, delta)

    def _bank(self, now: datetime):
        """把跑錶中的那一段結算進累積值並停錶。"""
        self._banked_seconds = self._elapsed(now)
        self._started_at = None

    # --- 查詢 ---

    def snapshot(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """
        現在的計時狀況。畫面、API、播放閘門看的都是這一份。

        `remaining_seconds` 夾在 0 以上，超時的部分另外放在 `overtime_seconds`：
        畫面要的是「還剩多久」，而「-00:04:12」這種顯示只會讓人愣一下才看懂。
        """
        now = now or datetime.now()
        with self._lock:
            if not self._active:
                return {
                    "active": False, "state": STATE_OFF, "total_seconds": 0,
                    "elapsed_seconds": 0, "remaining_seconds": 0, "overtime_seconds": 0,
                    "running": False, "expired": False, "halted": False, "ends_at": None,
                }
            elapsed = self._elapsed(now)
            remaining = self._total_seconds - elapsed
            expired = remaining <= 0
            running = self._started_at is not None
            if expired:
                state = STATE_EXPIRED
            elif running:
                state = STATE_RUNNING
            else:
                state = STATE_PAUSED
            # 預計結束的時刻。只有跑錶中才算得出來（停錶中沒有「幾點結束」這件事，
            # 硬給一個時間的話它會隨著休息時間一直往後飄，看起來像機器在亂跳）。
            ends_at = None
            if running and not expired:
                ends_at = (now + timedelta(seconds=remaining)).isoformat(timespec="seconds")
            return {
                "active": True,
                "state": state,
                "total_seconds": int(self._total_seconds),
                "elapsed_seconds": int(elapsed),
                "remaining_seconds": int(max(0.0, remaining)),
                "overtime_seconds": int(max(0.0, -remaining)),
                "running": running,
                "expired": expired,
                "halted": self._halted,
                "ends_at": ends_at,
            }

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        return bool(self.snapshot(now)["expired"])

    # --- 操作 ---

    def start(self, minutes: Any = DEFAULT_SESSION_MINUTES,
              now: Optional[datetime] = None) -> Dict[str, Any]:
        """開一場。已經有一場在跑時就是**重開**（歸零重算），不是續時。"""
        now = now or datetime.now()
        with self._lock:
            self._active = True
            self._total_seconds = coerce_minutes(minutes) * 60
            self._banked_seconds = 0.0
            self._started_at = now
            self._halted = False
            self._fired = set()
            self._save()
        return self.snapshot(now)

    def pause(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """停錶（中場休息）。已經停著就什麼都不做。"""
        now = now or datetime.now()
        with self._lock:
            if self._active and self._started_at is not None:
                self._bank(now)
                self._save()
        return self.snapshot(now)

    def resume(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """繼續倒數。時間已經到了就不必恢復（恢復也是立刻又到）。"""
        now = now or datetime.now()
        with self._lock:
            if self._active and self._started_at is None:
                self._started_at = now
                self._save()
        return self.snapshot(now)

    def extend(self, minutes: Any = DEFAULT_EXTEND_MINUTES,
               now: Optional[datetime] = None) -> Dict[str, Any]:
        """
        續時（見決定三）。沒有在計時的時候按它，等於用這個長度開一場 ——
        「還沒開始計時就按續時」最可能的意思是「開始計時」，而不是什麼都不要發生。

        時間到之後續時會自動恢復倒數並解除停播旗標，接回去唱。
        """
        now = now or datetime.now()
        added = coerce_minutes(minutes, default=DEFAULT_EXTEND_MINUTES,
                               low=MIN_EXTEND_MINUTES, high=MAX_EXTEND_MINUTES)
        with self._lock:
            if not self._active:
                return self.start(added, now)
            self._total_seconds = min(MAX_SESSION_MINUTES * 60,
                                      self._total_seconds + added * 60)
            # 續了時間就從停錶狀態接回去跑：按了續時還要再按一次「繼續」的話，
            # 那顆續時鍵看起來就沒有反應。
            if self._started_at is None:
                self._started_at = now
            self._halted = False
            # 重新變得「還沒到」的門檻要重新武裝（見決定四）：續了 30 分鐘之後，
            # 那個 10 分鐘提醒又變成有意義的資訊了。
            remaining = self._total_seconds - self._elapsed(now)
            self._fired = {seconds for seconds in self._fired if remaining <= seconds}
            self._save()
        return self.snapshot(now)

    def stop(self) -> Dict[str, Any]:
        """結束計時（散場、或是這桌不想被計時了）。佇列與統計一概不動。"""
        with self._lock:
            self._active = False
            self._total_seconds = 0
            self._banked_seconds = 0.0
            self._started_at = None
            self._halted = False
            self._fired = set()
            self._save()
        return self.snapshot()

    def mark_halted(self, halted: bool = True):
        """
        機器已經因為時間到而停住（或又動起來了）。

        這件事是 QueueManager 決定的（它才知道有沒有歌在唱），但存放在這裡：
        舞台端要靠它決定是不是該亮出散場畫面，而舞台端拿到的是這一份快照。
        """
        with self._lock:
            if self._halted != bool(halted):
                self._halted = bool(halted)
                self._save()

    def tick(self, milestones: Sequence[Tuple[str, int]] = (),
             now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """
        時間又過去了一點。回傳這一刻**剛跨過**的提醒（通常是空的）。

        每個門檻只會回一次（見決定四）。時間到（0）永遠是其中一個門檻，
        不需要設定 —— 沒有人會想關掉「時間到了」這個提醒。

        一次跨過好幾個門檻時（伺服器剛重開、心跳被卡住、或是有人開了一場比
        提醒門檻還短的），只回**最急的那一個**，其餘的當作已經講過。
        「剩十分鐘」「剩三分鐘」「時間到」在同一秒鐘一起跳出來，三句話會互相
        蓋掉，而真正該被看到的是最後那一句。
        """
        now = now or datetime.now()
        with self._lock:
            if not self._active or self._started_at is None:
                # 停錶中不推進，也就不會有新的提醒（但時間到之後 _started_at
                # 還在跑，所以「時間到」那一次照樣會發出來）。
                return []
            remaining = self._total_seconds - self._elapsed(now)
            crossed: List[Dict[str, Any]] = []
            thresholds = list(milestones) + [("expired", 0)]
            for kind, seconds in thresholds:
                if remaining > seconds or seconds in self._fired:
                    continue
                self._fired.add(seconds)
                crossed.append({
                    "kind": kind,
                    "threshold_seconds": int(seconds),
                    "remaining_seconds": int(max(0.0, remaining)),
                })
            if not crossed:
                return []
            self._save()
            crossed.sort(key=lambda alert: alert["threshold_seconds"])
        return crossed[:1]

    # --- 持久化（見決定六）---

    def _payload(self) -> Dict[str, Any]:
        return {
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "active": self._active,
            "total_seconds": int(self._total_seconds),
            "banked_seconds": round(self._banked_seconds, 3),
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "halted": self._halted,
            "fired": sorted(self._fired),
        }

    def _save(self):
        if self.state_file is None:
            return
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(
                json.dumps(self._payload(), ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            # 寫不進去不該讓包廂停止運作：計時繼續在記憶體裡跑，
            # 只是這台機器重開之後接不回來。
            logger.warning(f"包廂計時存檔失敗: {e}")

    def _load(self):
        if self.state_file is None or not self.state_file.exists():
            return
        try:
            raw = json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"包廂計時存檔讀取失敗，這一場不接續: {e}")
            return
        if not isinstance(raw, dict) or not raw.get("active"):
            return
        try:
            saved_at = datetime.fromisoformat(str(raw.get("saved_at")))
        except (TypeError, ValueError):
            return
        # 擺太久＝機器關過一整晚，昨天那一場早就散了（見決定六）
        if datetime.now() - saved_at > timedelta(hours=STALE_HOURS):
            logger.info("包廂計時存檔已過期（超過 %s 小時），不接續", STALE_HOURS)
            return
        started_at = None
        if raw.get("started_at"):
            try:
                started_at = datetime.fromisoformat(str(raw["started_at"]))
            except (TypeError, ValueError):
                started_at = None
        self._active = True
        self._total_seconds = max(0, int(raw.get("total_seconds") or 0))
        try:
            self._banked_seconds = max(0.0, float(raw.get("banked_seconds") or 0.0))
        except (TypeError, ValueError):
            self._banked_seconds = 0.0
        self._started_at = started_at
        self._halted = bool(raw.get("halted"))
        self._fired = {int(s) for s in raw.get("fired", []) if isinstance(s, (int, float))}
