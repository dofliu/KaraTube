"""
評分歷史與個人最佳紀錄

唱畢結算畫面的後端：每唱完一首，舞台端把總分送進來，這裡負責
1. 記一筆演唱成績（時間序列，同一首唱三次就有三筆）
2. 維護「每首歌的個人最佳」（含這次的最佳／待加強段落）
3. 算出這次的成績擊敗了過往多少比例的演唱（商用機的「擊敗全國 XX%」在
   單機系統上的對應物：擊敗這台機器上 XX% 的歷史演唱）
4. 留下每一場的段落命中率，讓 `section_trends` 算得出跨場次的趨勢
   （「這首歌的副歌 2 你一向掉 9 個百分點」）

存成 cache/score_history.json，重開機不會消失。
"""
import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.services import section_trends

logger = logging.getLogger("KaraTube.ScoreHistory")

# 只保留最近 N 筆成績，個人最佳另外存所以不會因裁切而遺失
MAX_ENTRIES = 1000

# 一筆成績最多存幾段的段落命中率。歷史檔是整份讀進記憶體再整份寫回的，
# 每筆多存一串段落就是每筆都變大；十六段已經涵蓋任何正常長度的歌，
# 超出的部分對「哪一段是弱點」也沒有幫助（沒有人記得住第十七段）。
MAX_SECTIONS_PER_ENTRY = 16

# 段落要有這麼多幀導唱音符才存得進歷史。
# 與 frontend/js/section-scorer.js 的 MIN_SECTION_NOTE_FRAMES 同值：
# 舞台端已經濾過一次，這裡再濾一次是因為 API 是公開的 ——
# 兩幀的段落命中率是 0% 或 100% 全憑運氣，混進趨勢統計就是雜訊。
MIN_SECTION_NOTE_FRAMES = 30

# 對唱模式的平手門檻（分數差佔較高分的比例）。
# 必須與舞台端 frontend/js/duet-scorer.js 的 TIE_RATIO 一致 ——
# 兩邊各判一次勝負的話，畫面與手機通知會講出不同的結果。
DUET_TIE_RATIO = 0.03


class ScoreHistory:
    def __init__(self, score_file: Path):
        self.score_file = Path(score_file)
        self._lock = threading.Lock()
        self._entries: List[Dict[str, Any]] = []
        # song_id -> 該曲個人最佳的那一筆成績
        self._bests: Dict[str, Dict[str, Any]] = {}
        # 對唱模式的「這位演唱者在這首歌的最佳」。
        # 刻意跟 _bests 分開存而不是塞進同一個 dict：
        # _bests 的鍵是 song_id，混進「song_id + 名字」的複合鍵之後，
        # 任何照 song_id 查表的地方都會多出查不到歌的鍵，那種 bug 很難找。
        self._singer_bests: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self):
        if not self.score_file.exists():
            return
        try:
            raw = json.loads(self.score_file.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._entries = [e for e in raw.get("entries", [])
                                 if isinstance(e, dict)][-MAX_ENTRIES:]
                bests = raw.get("bests", {})
                self._bests = {k: v for k, v in bests.items()
                               if isinstance(v, dict)} if isinstance(bests, dict) else {}
                # 舊版檔案沒有這一區（對唱模式之前的紀錄），當成空的就好
                singer = raw.get("singer_bests", {})
                self._singer_bests = {k: v for k, v in singer.items()
                                      if isinstance(v, dict)} if isinstance(singer, dict) else {}
        except Exception as e:
            logger.warning(f"評分歷史讀取失敗，重新開始: {e}")
            self._entries = []
            self._bests = {}
            self._singer_bests = {}

    def _save(self):
        try:
            self.score_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {"updated_at": datetime.now().isoformat(timespec="seconds"),
                       "entries": self._entries,
                       "bests": self._bests,
                       "singer_bests": self._singer_bests}
            self.score_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"評分歷史寫入失敗: {e}")

    @staticmethod
    def _singer_key(song_id: str, singer: str) -> str:
        # \x00 不可能出現在 song_id 或名字裡，所以不會有「歌名剛好含分隔符」的碰撞
        return f"{song_id}\x00{singer}"

    def _build_entry(self, result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """把前端送來的成績整理成一筆紀錄。看不懂（沒 song_id、分數是文字）回 None。"""
        song_id = result.get("song_id") or result.get("id")
        try:
            score = int(result.get("score", 0))
        except (TypeError, ValueError):
            return None
        if not song_id or score < 0:
            return None

        entry = {
            "song_id": song_id,
            "title": result.get("title", ""),
            "artist": result.get("artist", ""),
            "thumbnail": result.get("thumbnail", ""),
            # 對唱模式才有值：這一筆是哪一位唱的。
            # 空字串代表單人演唱（絕大多數的歷史紀錄），
            # 這樣舊資料不用轉換，新舊兩種紀錄也能排在同一條時間軸上。
            "singer": str(result.get("singer") or "")[:12],
            "duet": bool(result.get("duet")),
            "score": score,
            "accuracy": _clamp_float(result.get("accuracy"), 0.0, 1.0),
            "max_combo": _clamp_int(result.get("max_combo"), 0, 10 ** 6),
            "grade": str(result.get("grade", ""))[:8],
            # 段落評分的結論（「副歌 2」這種標籤）。整段長條圖是結算畫面的現場資訊，
            # 不入庫；這兩個標籤才是回頭看歷史時有用的東西：這首歌我老是哪一段唱壞。
            "best_section": str(result.get("best_section") or "")[:24],
            "worst_section": str(result.get("worst_section") or "")[:24],
            # 對唱的段落對決結論：這一位領先最多的那一段（「主場段落」）。
            # 單人演唱一律空字串 —— 一個人唱沒有對手，談不上主場。
            "duel_section": str(result.get("duel_section") or "")[:24],
            # 每一段的命中率。單場的長條圖是現場資訊（結算畫面已經畫過了），
            # 但跨場次的趨勢只能從這裡長出來 —— 存的是標籤與命中率兩個數字，
            # 不是整張成績單。1.7.0 之前的紀錄沒有這一欄，趨勢分析當成沒資料。
            "sections": _compact_sections(result.get("sections")),
            "sung_at": datetime.now().isoformat(timespec="seconds"),
        }
        return entry

    def _record_locked(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """把一筆整理好的紀錄寫進歷史（呼叫前必須先拿到鎖，且由呼叫端負責存檔）。"""
        song_id = entry["song_id"]
        score = entry["score"]
        singer = entry.get("singer") or ""

        # 擊敗比例：跟「這一筆之前」的所有歷史成績比
        previous = [int(e.get("score", 0)) for e in self._entries]
        beat_percent = (
            round(100 * sum(1 for s in previous if s < score) / len(previous))
            if previous else None
        )

        # 這台機器在這首歌的最高分（不分是誰唱的）
        prev_best = self._bests.get(song_id)
        prev_best_score = int(prev_best.get("score", -1)) if prev_best else -1
        if score > prev_best_score:
            self._bests[song_id] = dict(entry)

        # 對唱模式再多維護一份「這位演唱者在這首歌的最佳」。
        # 「刷新紀錄」對有名字的人來說指的是刷新自己的紀錄 ——
        # 拿全機器的最高分去比，等於每次跟包廂裡唱得最好的那個人比，沒有意義。
        if singer:
            key = self._singer_key(song_id, singer)
            prev_singer = self._singer_bests.get(key)
            prev_singer_score = int(prev_singer.get("score", -1)) if prev_singer else -1
            reference_score = prev_singer_score
            if score > prev_singer_score:
                self._singer_bests[key] = dict(entry)
        else:
            reference_score = prev_best_score

        is_new_best = score > reference_score

        self._entries.append(entry)
        if len(self._entries) > MAX_ENTRIES:
            self._entries = self._entries[-MAX_ENTRIES:]

        summary = dict(entry)
        # 趨勢在附加這一筆之後才算：結算畫面要講的是「含今天這次的你一向如何」，
        # 而不是「在今天之前你一向如何」。唱完當下看到的那句話必須把剛才唱的算進去，
        # 否則今天明明唱好了副歌，畫面還在說副歌是你的弱點。
        summary["trend"] = self._trend_locked(song_id, singer)
        summary["is_new_best"] = is_new_best
        summary["best_score"] = max(score, reference_score)
        summary["previous_best"] = reference_score if reference_score >= 0 else None
        summary["beat_percent"] = beat_percent
        who = f"{singer} " if singer else ""
        logger.info(f"唱畢結算: {who}{entry.get('title') or song_id} {score} 分"
                    f"（{'刷新個人最佳' if is_new_best else '個人最佳 ' + str(summary['best_score'])}）")
        return summary

    def record(self, result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """記一筆唱畢成績，回傳含個人最佳與擊敗比例的結算資料。"""
        entry = self._build_entry(result)
        if entry is None:
            return None
        with self._lock:
            summary = self._record_locked(entry)
            self._save()
        return summary

    def record_duet(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        對唱模式的唱畢結算：兩位演唱者的成績一起記。

        `payload` 形狀是 `{song_id, title, artist, thumbnail, a: {...}, b: {...}}`，
        兩位共用歌曲資訊，各自帶 singer / score / accuracy / max_combo / grade。

        刻意做成一支方法而不是「呼叫 record 兩次」：兩筆要嘛都進去、要嘛都不進去，
        而且只存檔一次。分兩次寫的話，中間出事就會在歷史裡留下
        「一場只有一個人的對唱」—— 那筆資料之後永遠說不清是誰的問題。
        """
        if not isinstance(payload, dict):
            return None
        shared = {k: payload.get(k, "") for k in ("song_id", "title", "artist", "thumbnail")}
        sides = {}
        for which in ("a", "b"):
            side = payload.get(which)
            if not isinstance(side, dict):
                return None
            entry = self._build_entry({**shared, **side, "duet": True})
            if entry is None:
                return None
            # 名字是對唱紀錄的重點（沒有名字就分不出兩筆是誰的），沒給就用麥克風代號
            if not entry["singer"]:
                entry["singer"] = "A 麥" if which == "a" else "B 麥"
            sides[which] = entry

        with self._lock:
            result = {which: self._record_locked(entry) for which, entry in sides.items()}
            self._save()

        score_a = result["a"]["score"]
        score_b = result["b"]["score"]
        margin = abs(score_a - score_b)
        top = max(score_a, score_b)
        # 平手門檻與舞台端同一個比例（frontend/js/duet-scorer.js 的 TIE_RATIO）：
        # 兩邊算出不同的勝負，畫面說平手、手機通知說 A 贏，是最糟的那種不一致。
        tie = top == 0 or margin / top <= DUET_TIE_RATIO
        result["winner"] = "tie" if tie else ("a" if score_a > score_b else "b")
        result["margin"] = margin
        # 廣播給點歌台的通知只需要一行字的材料，所以這裡就把它整理好
        result["song_id"] = shared["song_id"]
        result["title"] = shared["title"]
        result["duet"] = True
        return result

    def best_for(self, song_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            best = self._bests.get(song_id)
            return dict(best) if best else None

    def recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        """最近的演唱成績，最新的排最前面。"""
        with self._lock:
            return [dict(e) for e in reversed(self._entries[-limit:])]

    def bests(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {k: dict(v) for k, v in self._bests.items()}

    def singer_best_for(self, song_id: str, singer: str) -> Optional[Dict[str, Any]]:
        """對唱模式：這位演唱者在這首歌的個人最佳。沒唱過回 None。"""
        with self._lock:
            best = self._singer_bests.get(self._singer_key(song_id, singer or ""))
            return dict(best) if best else None

    def singer_bests(self) -> List[Dict[str, Any]]:
        """所有「某人在某首歌的最佳」。複合鍵不好直接吐給前端，所以攤平成清單。"""
        with self._lock:
            return [dict(v) for v in self._singer_bests.values()]

    def _trend_locked(self, song_id: str, singer: str = "") -> Dict[str, Any]:
        """趨勢計算本體（呼叫前必須先拿到鎖）。`threading.Lock` 不可重入，
        所以記錄流程不能回頭呼叫 `trend_for` —— 那會當場鎖死自己。"""
        mine = [dict(e) for e in self._entries
                if e.get("song_id") == song_id
                and (e.get("singer") or "") == (singer or "")]
        return section_trends.compute_trend(mine)

    def trend_for(self, song_id: str, singer: str = "") -> Dict[str, Any]:
        """
        跨場次段落趨勢：這位演唱者在這首歌一向強在哪一段、弱在哪一段。

        `singer` 空字串代表單人演唱的紀錄（絕大多數）。刻意不做成「不分是誰唱的
        就全部混在一起」：段落弱點是**這個人**的弱點，把包廂裡三個人的
        副歌混成一筆平均，算出來的是這首歌的副歌難不難唱。
        """
        with self._lock:
            return self._trend_locked(song_id, singer)

    def trends(self, limit: int = 20) -> List[Dict[str, Any]]:
        """所有「唱到有話可說」的（歌曲, 演唱者）組合，最近唱過的排前面。"""
        with self._lock:
            grouped: Dict[str, List[Dict[str, Any]]] = {}
            # 排序鍵用「最後一筆在歷史裡的位置」而不是 sung_at：
            # 時間戳只到秒，同一秒進來的兩首（測試、批次匯入）排出來的順序
            # 由字典序決定，也就是隨機的。位置是真正的先後。
            last_index: Dict[str, int] = {}
            for i, entry in enumerate(self._entries):
                song_id = entry.get("song_id")
                if not song_id:
                    continue
                key = self._singer_key(song_id, entry.get("singer") or "")
                grouped.setdefault(key, []).append(dict(entry))
                last_index[key] = i

        out = []
        for key, group in grouped.items():
            trend = section_trends.compute_trend(group)
            if trend.get("status") != "ok":
                continue
            last = group[-1]
            trend["thumbnail"] = last.get("thumbnail", "")
            trend["artist"] = last.get("artist", "")
            trend["best_score"] = max(int(e.get("score", 0)) for e in group)
            out.append((last_index[key], trend))
        # 最近唱過的排前面：這份清單是「回頭看看剛才那幾首」，不是排行榜
        out.sort(key=lambda pair: pair[0], reverse=True)
        return [trend for _, trend in out[:limit]]

    def total_count(self) -> int:
        with self._lock:
            return len(self._entries)

    def clear(self):
        with self._lock:
            self._entries = []
            self._bests = {}
            self._singer_bests = {}
            self._save()
        logger.info("評分歷史已清空")


def _compact_sections(raw: Any) -> List[Dict[str, Any]]:
    """
    把成績單上的段落列表壓成「標籤 + 命中率」兩個欄位存進歷史。

    丟掉的是 start/end/grade/perfect_frames 這些**單場**才有意義的欄位：
    時間點在下一次重新處理之後就不一定對得上，等級可以從命中率再算出來。
    音符幀數留著當守門用（太短的段落命中率是運氣不是實力），不入庫。
    """
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        label = str(row.get("label") or "").strip()[:24]
        if not label:
            continue
        frames = row.get("note_frames")
        if frames is not None and _clamp_int(frames, 0, 10 ** 9) < MIN_SECTION_NOTE_FRAMES:
            continue
        # graded 是舞台端算好的同一道守門；明確為 False 就尊重它
        if row.get("graded") is False:
            continue
        out.append({"label": label,
                    "accuracy": round(_clamp_float(row.get("accuracy"), 0.0, 1.0), 3)})
        if len(out) >= MAX_SECTIONS_PER_ENTRY:
            break
    return out


def _clamp_float(value: Any, lo: float, hi: float) -> float:
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return lo


def _clamp_int(value: Any, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return lo
