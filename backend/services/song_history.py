"""
已唱歷史（演唱紀錄）

商用點歌機的「已點歌曲 / 已唱歷史」分頁：今天唱過什麼、什麼時候唱的，
一鍵就能再點一次。與點唱排行不同 —— 排行是「累計次數」的聚合，
歷史是「一次一筆」的時間序列，同一首唱三次就有三筆。
存成 cache/song_history.json，重開機不會消失。
"""
import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("KaraTube.SongHistory")

# 只保留最近 N 筆，避免長期使用後檔案無限膨脹
MAX_ENTRIES = 500


class SongHistory:
    def __init__(self, history_file: Path):
        self.history_file = Path(history_file)
        # WebSocket 事件迴圈與 REST 兩邊都可能進來，讀寫要上鎖
        self._lock = threading.Lock()
        self._entries: List[Dict[str, Any]] = []
        self._load()

    def _load(self):
        if not self.history_file.exists():
            return
        try:
            raw = json.loads(self.history_file.read_text(encoding="utf-8"))
            entries = raw.get("entries", []) if isinstance(raw, dict) else []
            self._entries = [e for e in entries if isinstance(e, dict)][-MAX_ENTRIES:]
        except Exception as e:
            logger.warning(f"已唱歷史讀取失敗，重新開始: {e}")
            self._entries = []

    def _save(self):
        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {"updated_at": datetime.now().isoformat(timespec="seconds"),
                       "entries": self._entries}
            self.history_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"已唱歷史寫入失敗: {e}")

    def record(self, song: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """歌曲真正上台播放時記一筆。排進佇列又被刪掉的不算。"""
        song_id = song.get("song_id") or song.get("id")
        if not song_id:
            return None
        entry = {
            "song_id": song_id,
            "title": song.get("title", ""),
            "artist": song.get("artist", ""),
            "thumbnail": song.get("thumbnail", ""),
            "sung_at": datetime.now().isoformat(timespec="seconds"),
        }
        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > MAX_ENTRIES:
                self._entries = self._entries[-MAX_ENTRIES:]
            self._save()
        return dict(entry)

    def recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        """最近唱過的歌，最新的排最前面。"""
        with self._lock:
            return [dict(e) for e in reversed(self._entries[-limit:])]

    def today_count(self) -> int:
        """今天（本機日期）一共唱了幾首。"""
        today = datetime.now().date().isoformat()
        with self._lock:
            return sum(1 for e in self._entries
                       if str(e.get("sung_at", "")).startswith(today))

    def total_count(self) -> int:
        with self._lock:
            return len(self._entries)

    def clear(self):
        with self._lock:
            self._entries = []
            self._save()
        logger.info("已唱歷史已清空")
