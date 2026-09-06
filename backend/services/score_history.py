"""
評分歷史與個人最佳紀錄

唱畢結算畫面的後端：每唱完一首，舞台端把總分送進來，這裡負責
1. 記一筆演唱成績（時間序列，同一首唱三次就有三筆）
2. 維護「每首歌的個人最佳」（含這次的最佳／待加強段落）
3. 算出這次的成績擊敗了過往多少比例的演唱（商用機的「擊敗全國 XX%」在
   單機系統上的對應物：擊敗這台機器上 XX% 的歷史演唱）

存成 cache/score_history.json，重開機不會消失。
"""
import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("KaraTube.ScoreHistory")

# 只保留最近 N 筆成績，個人最佳另外存所以不會因裁切而遺失
MAX_ENTRIES = 1000


class ScoreHistory:
    def __init__(self, score_file: Path):
        self.score_file = Path(score_file)
        self._lock = threading.Lock()
        self._entries: List[Dict[str, Any]] = []
        # song_id -> 該曲個人最佳的那一筆成績
        self._bests: Dict[str, Dict[str, Any]] = {}
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
        except Exception as e:
            logger.warning(f"評分歷史讀取失敗，重新開始: {e}")
            self._entries = []
            self._bests = {}

    def _save(self):
        try:
            self.score_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {"updated_at": datetime.now().isoformat(timespec="seconds"),
                       "entries": self._entries,
                       "bests": self._bests}
            self.score_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"評分歷史寫入失敗: {e}")

    def record(self, result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """記一筆唱畢成績，回傳含個人最佳與擊敗比例的結算資料。"""
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
            "score": score,
            "accuracy": _clamp_float(result.get("accuracy"), 0.0, 1.0),
            "max_combo": _clamp_int(result.get("max_combo"), 0, 10 ** 6),
            "grade": str(result.get("grade", ""))[:8],
            # 段落評分的結論（「副歌 2」這種標籤）。整段長條圖是結算畫面的現場資訊，
            # 不入庫；這兩個標籤才是回頭看歷史時有用的東西：這首歌我老是哪一段唱壞。
            "best_section": str(result.get("best_section") or "")[:24],
            "worst_section": str(result.get("worst_section") or "")[:24],
            "sung_at": datetime.now().isoformat(timespec="seconds"),
        }

        with self._lock:
            # 擊敗比例：跟「這一筆之前」的所有歷史成績比
            previous = [int(e.get("score", 0)) for e in self._entries]
            beat_percent = (
                round(100 * sum(1 for s in previous if s < score) / len(previous))
                if previous else None
            )

            prev_best = self._bests.get(song_id)
            prev_best_score = int(prev_best.get("score", -1)) if prev_best else -1
            is_new_best = score > prev_best_score
            if is_new_best:
                self._bests[song_id] = dict(entry)

            self._entries.append(entry)
            if len(self._entries) > MAX_ENTRIES:
                self._entries = self._entries[-MAX_ENTRIES:]
            self._save()

        summary = dict(entry)
        summary["is_new_best"] = is_new_best
        summary["best_score"] = max(score, prev_best_score)
        summary["previous_best"] = prev_best_score if prev_best_score >= 0 else None
        summary["beat_percent"] = beat_percent
        logger.info(f"唱畢結算: {entry.get('title') or song_id} {score} 分"
                    f"（{'刷新個人最佳' if is_new_best else '個人最佳 ' + str(summary['best_score'])}）")
        return summary

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

    def total_count(self) -> int:
        with self._lock:
            return len(self._entries)

    def clear(self):
        with self._lock:
            self._entries = []
            self._bests = {}
            self._save()
        logger.info("評分歷史已清空")


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
