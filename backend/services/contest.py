"""
今晚擂台（包廂歌王榜）

商用點歌機都有的那一張榜：一個晚上唱完，誰是今晚的歌王。這一支就是它。

榜是**分房**的（見 rooms.py 的判準）—— 擂台是「這一組客人現在正在做的事」，
跟點唱排行（這台機器最多人唱的歌，是店的問題）不同邊。A 包廂的歌王跟
B 包廂沒有關係，混在一起的話兩間都看不到自己那一場。

---

**決定一：排名用的是命中率，不是結算畫面上那個分數。**

這是整個功能唯一不能弄錯的一件事。`pitch-engine.js` 的 `score` 是一個
**累加器**（每命中一幀 +15），所以它同時量到兩件事：唱得準不準，以及
**這首歌有多長、人聲有多密**。五分鐘的抒情歌唱到 70% 命中，分數會遠高於
兩分半的快歌唱到 95% —— 拿它排名，選出來的歌王是「挑最長的歌的人」，
而那個榜第二天就沒有人要看了（而且它會反過來教大家專挑長歌）。

命中率（命中幀 ÷ 有導唱音符的幀）把長度除掉了，剩下的才是唱得準不準。
畫面上叫它「擂台分」（0~100），刻意跟結算畫面那個四位數的分數分開講 ——
兩個都叫「分數」的話，使用者會問為什麼我唱了 3,480 分榜上寫 78。

**決定二：代表分＝最好的三首的平均，而且唱不滿三首不排名。**

想過的另外三種，以及它們會壞在哪：

  * **總分加總** —— 唱最多的贏。而包廂裡唱最多的那一位，正是公平輪唱
    (rotation.py) 整支模組在約束的那個人；榜不該反過來獎勵他。
  * **單曲最高分** —— 一首運氣好就封王（副歌都在舒適音域的那一首）。
  * **全部演唱平均** —— **懲罰參與**：想守住名次的人唱滿門檻之後就不敢再唱，
    因為下一首唱壞會把自己的平均拉下來。一個會讓人停止唱歌的榜是壞的榜。

「最好的三首的平均」三件事都躲開了：要三首（不是運氣）、多唱只可能變好
（不懲罰參與）、而且不是加總（唱多不等於贏）。取幾首與門檻是**同一個數字**
（`RANK_SONGS`）—— 門檻比取的首數低的話，只唱兩首的人是「兩首的平均」，
而少一首可以少犯一次錯，那反而是優勢。

**決定三：同一首歌只算最好的那一次。**

不這樣做的話，最好的三首會全部是同一首歌唱三次 —— 而那是把「今晚誰唱得
最好」變成「誰最會刷同一首」。三首指的是三首**不同的歌**。

**決定四：沒取暱稱的人不進榜。**

公平輪唱把沒取暱稱的人全算同一個人（那讓輪唱在沒有人取暱稱時等於沒開），
但同一招搬到榜上就錯了：那一位「無名氏歌王」其實是一群人，而榜的全部意義
就是指出**一個人**。所以未具名的演唱只進「今晚還有幾首沒掛名字」那一行，
並且說出下一步（取個暱稱就上得了榜）—— 跟輪唱那句提示是同一個形狀。

**決定五：一場的邊界照空檔切，跟輪唱與整晚打包同一個定義。**

系統裡「一場」只能有一個定義（見 settings.py 的 `recording_session_gap_hours`）。
相鄰兩次演唱隔超過 `gap_hours`（預設 6 小時）就是下一桌客人，榜自動翻新。
照日曆日切會把「九點唱到凌晨兩點半」的那一場切成兩半，而後半正是唱到最嗨
的那一段；綁包廂計時的開始鍵則會漏掉沒在計時的機器（家裡那一台）。

**決定六：榜要存檔。**

公平輪唱刻意不存檔（佇列本來就不持久化，輪序沒有理由活得比佇列久），
但榜不一樣：它是**已經發生的成績**，而伺服器在一場裡重開（更新、當掉、
插頭被踢到）並不會讓那三首歌沒有唱過。掉了的話，現場沒有任何辦法算回來。

**決定七：唱到一半被切掉的那一次不進榜 —— 而這件事不必在這裡寫。**

舞台端只在 `ended` 事件出結算（見 player.js），被切歌的那一次根本不會送出
成績。所以「唱 30 秒副歌就切歌」刷不到榜，這裡一行程式都不用寫。
這裡擋的是另一件事：**沒有導唱音符的歌**（沒抓到人聲音高的那一種）命中率
的分母是 0 —— 那是「沒得算」不是「0 分」，不擋的話它會變成一首拉低平均的歌，
而唱的人完全不知道發生什麼事。
"""
import json
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.services import rotation

logger = logging.getLogger("KaraTube.Contest")

# 代表分取最好的幾首，也是上榜門檻（決定二：兩者必須是同一個數字）
RANK_SONGS = 3

# 一首歌至少要有這麼多幀導唱音符才算「唱過一首」。
# 幀率約 60fps，所以 600 幀 ≈ 10 秒的導唱旋律 —— 任何一首真的歌都遠超過它。
# 這個數字擋的是「沒有音高資料的歌」（命中率分母是 0，那是沒得算不是 0 分），
# 不是短歌。
MIN_NOTE_FRAMES = 600

# 榜上最多幾位。上限的作用是擋住「每首歌換一個暱稱」把記憶體與存檔撐大，
# 不是業務規則 —— 一間包廂坐不下 64 個人。滿了之後擠掉的是**最沒有機會上榜
# 而且最久沒唱**的那一位（首數最少、last_at 最舊），不是最新進來的那一位：
# 擠掉新來的人會讓「我唱了為什麼榜上沒有我」永遠無解。
MAX_SINGERS = 64

# 一位演唱者最多記幾首不同的歌。代表分只看最好的三首，再多的歌對排名沒有影響，
# 留著只是為了「今晚唱了幾首」那個數字 —— 所以計數照算，明細只留最好的這些。
MAX_SONGS_PER_SINGER = 60


def _clamp_unit(value: Any) -> float:
    """命中率夾在 0..1。看不懂的值當 0（呼叫端已經先擋過 note_frames）。"""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    if f != f:  # NaN
        return 0.0
    return max(0.0, min(1.0, f))


def _points(accuracy: float) -> int:
    """命中率 → 擂台分（0~100 的整數）。畫面上到處都用這一個換算。"""
    return int(round(_clamp_unit(accuracy) * 100))


def session_id_from(started_at: str) -> str:
    """
    一場的識別字串：`20260927-2105`（開始時間到分鐘）。

    與整晚打包（night_export.py）同一種寫法 —— 櫃檯看到的兩個地方
    講的是同一場，那兩串字就該長得一樣。
    """
    try:
        return datetime.fromisoformat(started_at).strftime("%Y%m%d-%H%M")
    except (TypeError, ValueError):
        return ""


class ContestBoard:
    """
    一間包廂的今晚擂台。執行緒安全，落地成一個 JSON（決定六）。

    存的是「每一位演唱者、每一首歌的最好那一次」，不是每一次演唱的流水帳：
    排名只看得到最好的三首，流水帳在 score_history 裡已經有一份了。
    """

    def __init__(self, state_file: Optional[Any] = None,
                 gap_hours: float = rotation.DEFAULT_SESSION_GAP_HOURS):
        self._file = Path(state_file) if state_file else None
        self._gap_hours = float(gap_hours)
        self._lock = threading.RLock()
        self._singers: Dict[str, Dict[str, Any]] = {}
        self._unnamed_takes = 0
        self._started_at = datetime.now()
        self._last_at: Optional[datetime] = None
        self._load()

    # --- 設定 ---

    def set_gap_hours(self, gap_hours: Any):
        """設定頁改了「一場的空檔」時跟著改（整個系統共用同一個定義）。"""
        try:
            value = float(gap_hours)
        except (TypeError, ValueError):
            return
        if value > 0:
            with self._lock:
                self._gap_hours = value

    # --- 持久化 ---

    def _load(self):
        if not self._file or not self._file.exists():
            return
        try:
            raw = json.loads(self._file.read_text(encoding="utf-8"))
        except Exception as e:
            # 壞檔不是致命傷：退回空榜（等於這一場還沒有人唱）。
            # 榜壞掉不該讓包廂不能唱歌。
            logger.warning(f"擂台榜讀取失敗，退回空榜: {e}")
            return
        if not isinstance(raw, dict):
            return
        singers = {}
        for row in raw.get("singers", []):
            if not isinstance(row, dict):
                continue
            key = rotation.singer_key(row.get("name"))
            if not key:
                continue
            songs = {}
            for sid, s in (row.get("songs") or {}).items():
                if isinstance(s, dict):
                    songs[str(sid)] = {
                        "song_id": str(sid),
                        "title": str(s.get("title") or "")[:80],
                        "accuracy": _clamp_unit(s.get("accuracy")),
                        "score": int(s.get("score") or 0),
                        "grade": str(s.get("grade") or "")[:8],
                        "sung_at": str(s.get("sung_at") or ""),
                    }
            singers[key] = {
                "key": key,
                "name": rotation.display_name(row.get("name")),
                "songs": songs,
                "takes": int(row.get("takes") or 0),
                "first_at": str(row.get("first_at") or ""),
                "last_at": str(row.get("last_at") or ""),
            }
        self._singers = singers
        self._unnamed_takes = int(raw.get("unnamed_takes") or 0)
        self._started_at = _parse_dt(raw.get("started_at")) or datetime.now()
        self._last_at = _parse_dt(raw.get("last_at"))

    def _save(self):
        if not self._file:
            return
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "started_at": self._started_at.isoformat(timespec="seconds"),
                "last_at": self._last_at.isoformat(timespec="seconds") if self._last_at else "",
                "unnamed_takes": self._unnamed_takes,
                "singers": list(self._singers.values()),
            }
            self._file.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        except Exception as e:
            logger.warning(f"擂台榜寫入失敗: {e}")

    # --- 一場的邊界（決定五）---

    def _roll_if_stale(self, now: datetime) -> bool:
        """
        離上一次演唱隔太久 → 這是下一桌客人，榜翻新。回傳有沒有翻。

        讀取（snapshot）也會觸發，不只寫入時：不然一早開機第一件事看到的是
        昨晚那一場的歌王，而那張榜上的人都已經回家了。
        時間**倒退**（NTP 校時）時差值是負的，不會超過門檻 —— 寧可少切一場，
        也不要把同一場切成兩半。
        """
        if self._last_at is None or self._gap_hours <= 0:
            return False
        if now - self._last_at <= timedelta(hours=self._gap_hours):
            return False
        self._singers.clear()
        self._unnamed_takes = 0
        self._started_at = now
        self._last_at = None
        logger.info("擂台榜換場（距離上一次演唱超過 "
                    f"{self._gap_hours:g} 小時）")
        return True

    # --- 記一次演唱 ---

    def record(self, take: Dict[str, Any],
               now: Optional[datetime] = None) -> Dict[str, Any]:
        """
        記一次演唱，回傳**給結算畫面用**的一句話所需的資料。

        `take` 要有 `performer`（暱稱）、`song_id`、`accuracy`、`note_frames`。
        進不了榜不是錯誤：回傳的 `accepted=False` 加一個 `reason`，
        舞台端照 reason 講出「取個暱稱就上得了榜」或者乾脆不顯示。
        """
        now = now or datetime.now()
        song_id = str(take.get("song_id") or "")
        name = rotation.display_name(take.get("performer"))
        key = rotation.singer_key(take.get("performer"))
        try:
            note_frames = int(take.get("note_frames") or 0)
        except (TypeError, ValueError):
            note_frames = 0

        with self._lock:
            rolled = self._roll_if_stale(now)

            if not song_id or note_frames < MIN_NOTE_FRAMES:
                # 沒有導唱音符的歌（決定七）：不是 0 分，是沒得算。
                # 不記、也不動 last_at —— 它連「這一場有人在唱」都證明不了。
                if rolled:
                    self._save()
                return self._verdict(None, None, reason="no_pitch_data")

            # `_last_at` 是「最近一次演唱」，而且**只往前走**。
            # 時間倒退（NTP 校時、有人改系統時間）時讓它跟著退的話，
            # 下一次**讀**這張榜就會看到一個十小時的空檔而把整場翻掉 ——
            # 而那一場的人還坐在包廂裡。
            if self._last_at is None or now > self._last_at:
                self._last_at = now
            if not key:
                # 未具名（決定四）：計數，但不進榜
                self._unnamed_takes += 1
                self._save()
                return self._verdict(None, None, reason="no_name")

            before = self._rank_of_locked(key)
            singer = self._singers.get(key)
            if singer is None:
                self._evict_if_full_locked()
                singer = {
                    "key": key, "name": name, "songs": {}, "takes": 0,
                    "first_at": now.isoformat(timespec="seconds"),
                    "last_at": "",
                }
                self._singers[key] = singer

            # 名字用最新的寫法（同一個人把「小明」改成「小明🎤」還是同一位，
            # 因為 key 是 casefold 過的 —— 但畫面上要跟著他現在叫自己什麼）
            singer["name"] = name or singer["name"]
            singer["takes"] += 1
            singer["last_at"] = now.isoformat(timespec="seconds")

            accuracy = _clamp_unit(take.get("accuracy"))
            entry = {
                "song_id": song_id,
                "title": str(take.get("title") or "")[:80],
                "accuracy": accuracy,
                "score": _safe_int(take.get("score")),
                "grade": str(take.get("grade") or "")[:8],
                "sung_at": now.isoformat(timespec="seconds"),
            }
            # 同一首只留最好的那一次（決定三）
            prev = singer["songs"].get(song_id)
            if prev is None or accuracy > prev["accuracy"]:
                singer["songs"][song_id] = entry
            self._trim_songs_locked(singer)

            self._save()
            after = self._rank_of_locked(key)
            return self._verdict(before, after, singer=singer)

    def _evict_if_full_locked(self):
        """榜滿了就擠掉最沒有機會上榜、而且最久沒唱的那一位。"""
        if len(self._singers) < MAX_SINGERS:
            return
        victim = min(self._singers.values(),
                     key=lambda s: (len(s["songs"]), s.get("last_at") or ""))
        self._singers.pop(victim["key"], None)

    @staticmethod
    def _trim_songs_locked(singer: Dict[str, Any]):
        """明細留最好的那幾首（`takes` 照算，所以「唱了幾首」不會因此變少）。"""
        songs = singer["songs"]
        if len(songs) <= MAX_SONGS_PER_SINGER:
            return
        keep = sorted(songs.values(), key=lambda s: -s["accuracy"])[:MAX_SONGS_PER_SINGER]
        singer["songs"] = {s["song_id"]: s for s in keep}

    # --- 排名 ---

    @staticmethod
    def _row_of(singer: Dict[str, Any]) -> Dict[str, Any]:
        """一位演唱者的榜上資料（還沒有名次）。"""
        songs = sorted(singer["songs"].values(), key=lambda s: -s["accuracy"])
        top = songs[:RANK_SONGS]
        qualified = len(songs) >= RANK_SONGS
        rank_accuracy = sum(s["accuracy"] for s in top) / len(top) if top else 0.0
        best = songs[0] if songs else None
        return {
            "key": singer["key"],
            "name": singer["name"],
            "qualified": qualified,
            # 沒上榜的人也算得出「目前這幾首的平均」，但那個數字不拿來排名 ——
            # 畫面上只用它回答「我現在大概在什麼位置」。
            "points": _points(rank_accuracy),
            "accuracy": round(rank_accuracy, 3),
            "songs": len(songs),
            "takes": singer["takes"],
            "need": max(0, RANK_SONGS - len(songs)),
            "best_points": _points(best["accuracy"]) if best else 0,
            "best_title": best["title"] if best else "",
            "top": [{"title": s["title"], "points": _points(s["accuracy"]),
                     "grade": s["grade"], "song_id": s["song_id"]} for s in top],
            "first_at": singer.get("first_at") or "",
            "last_at": singer.get("last_at") or "",
        }

    def _ranked_locked(self) -> List[Dict[str, Any]]:
        """
        上榜的那幾位，名次由高到低。

        同分怎麼排：先比單曲最高分（三首平均一樣時，有一首特別亮的排前面），
        再比唱了幾首不同的歌（唱得多而且維持住的人排前面），最後比誰先開始唱
        —— 這一層是為了讓順序**穩定**：三個數字都一樣時，榜每次重讀不該換位置。
        """
        rows = [self._row_of(s) for s in self._singers.values()]
        ranked = [r for r in rows if r["qualified"]]
        ranked.sort(key=lambda r: (-r["points"], -r["best_points"], -r["songs"],
                                   r["first_at"], r["key"]))
        for i, r in enumerate(ranked):
            r["rank"] = i + 1
        return ranked

    def _rank_of_locked(self, key: str) -> Optional[int]:
        for r in self._ranked_locked():
            if r["key"] == key:
                return r["rank"]
        return None

    def _verdict(self, before: Optional[int], after: Optional[int],
                 singer: Optional[Dict[str, Any]] = None,
                 reason: str = "") -> Dict[str, Any]:
        """結算畫面要的那一句話所需的資料。"""
        ranked = self._ranked_locked()
        leader = ranked[0] if ranked else None
        out: Dict[str, Any] = {
            "accepted": bool(singer) and reason == "",
            "reason": reason,
            "rank": after,
            "previous_rank": before,
            "rank_songs": RANK_SONGS,
            "leader": {"name": leader["name"], "points": leader["points"]} if leader else None,
            "board_size": len(ranked),
        }
        if singer is not None:
            row = self._row_of(singer)
            out.update({
                "name": row["name"], "points": row["points"],
                "songs": row["songs"], "need": row["need"],
                "qualified": row["qualified"],
            })
            # 「這一首讓他登頂」跟「他本來就是第一」要分得開：
            # 前者值得在舞台上亮一下，後者每首歌都亮就變成雜訊。
            out["is_leader"] = after == 1
            out["took_lead"] = after == 1 and before != 1
        return out

    # --- 查詢與重設 ---

    def snapshot(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """整張榜。點歌台的擂台分頁與櫃檯總覽都讀它。"""
        now = now or datetime.now()
        with self._lock:
            if self._roll_if_stale(now):
                self._save()
            ranked = self._ranked_locked()
            waiting = [r for r in (self._row_of(s) for s in self._singers.values())
                       if not r["qualified"]]
            # 候補照「差幾首」再照目前的平均排：差一首的人排前面，
            # 因為那一行要回答的是「誰快上榜了」。
            waiting.sort(key=lambda r: (r["need"], -r["points"], r["first_at"], r["key"]))
            started = self._started_at.isoformat(timespec="seconds")
            return {
                "session_id": session_id_from(started),
                "started_at": started,
                "last_at": self._last_at.isoformat(timespec="seconds") if self._last_at else "",
                "rank_songs": RANK_SONGS,
                "gap_hours": self._gap_hours,
                "ranked": ranked,
                "waiting": waiting,
                "unnamed_takes": self._unnamed_takes,
                "singers": len(self._singers),
                "total_takes": sum(s["takes"] for s in self._singers.values())
                               + self._unnamed_takes,
            }

    def leader(self, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
        """櫃檯總覽那一列只要一句話：這一間現在誰領先。沒有人上榜回 None。"""
        ranked = self.snapshot(now)["ranked"]
        if not ranked:
            return None
        top = ranked[0]
        return {"name": top["name"], "points": top["points"], "songs": top["songs"]}

    def reset(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """手動開新的一場（換一批客人）。跟輪唱的「重新排」是同一顆鍵的意思。"""
        now = now or datetime.now()
        with self._lock:
            self._singers.clear()
            self._unnamed_takes = 0
            self._started_at = now
            self._last_at = None
            self._save()
        return self.snapshot(now)


def _parse_dt(raw: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
