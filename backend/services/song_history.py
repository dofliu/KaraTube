"""
已唱歷史（演唱紀錄）

商用點歌機的「已點歌曲 / 已唱歷史」分頁：今天唱過什麼、什麼時候唱的，
一鍵就能再點一次。與點唱排行不同 —— 排行是「累計次數」的聚合，
歷史是「一次一筆」的時間序列，同一首唱三次就有三筆。
存成 cache/song_history.json，重開機不會消失。

**多包廂：一個檔案，每一筆帶房號。**
分成十個檔案的話，「曲庫推薦」與「最近唱過」要去掃十個檔案才湊得出一份
全店的清單；而這一份清單本來就是全店的（同一首歌在哪一間唱都算唱過）。
所以檔案只有一份，讀的時候才照房號濾 —— `room=None` 是全店，
給了房號就只有那一間。沒有 `room` 欄位的舊資料算 default 那一間
（升級之前的世界本來就只有一間）。
"""
import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.services.rooms import DEFAULT_ROOM_ID as DEFAULT_ROOM

logger = logging.getLogger("KaraTube.SongHistory")

# 只保留最近 N 筆，避免長期使用後檔案無限膨脹。
# 多包廂之後這個數字是**整台機器共用**的：十間包廂一個晚上唱掉幾百首，
# 500 筆會讓早場那一批在客人還沒走的時候就被擠掉，而「今天唱過的歌」
# 正是他們會回頭按的那一份清單。
MAX_ENTRIES = 2000


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

    def record(self, song: Dict[str, Any],
               room: str = DEFAULT_ROOM) -> Optional[Dict[str, Any]]:
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
            "room": str(room or DEFAULT_ROOM),
        }
        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > MAX_ENTRIES:
                self._entries = self._entries[-MAX_ENTRIES:]
            self._save()
        return dict(entry)

    def _rows(self, room: Optional[str]) -> List[Dict[str, Any]]:
        """照房號濾過的那一份（`room=None` 是全店）。呼叫端已經拿著鎖。"""
        if room is None:
            return self._entries
        want = str(room)
        return [e for e in self._entries if str(e.get("room") or DEFAULT_ROOM) == want]

    def recent(self, limit: int = 50, room: Optional[str] = None) -> List[Dict[str, Any]]:
        """最近唱過的歌，最新的排最前面。`room` 給了就只看那一間。"""
        with self._lock:
            return [dict(e) for e in reversed(self._rows(room)[-limit:])]

    def today_count(self, room: Optional[str] = None) -> int:
        """今天（本機日期）一共唱了幾首。"""
        today = datetime.now().date().isoformat()
        with self._lock:
            return sum(1 for e in self._rows(room)
                       if str(e.get("sung_at", "")).startswith(today))

    def total_count(self, room: Optional[str] = None) -> int:
        with self._lock:
            return len(self._rows(room))

    def clear(self, room: Optional[str] = None):
        """
        清空。`room` 給了就只清那一間 —— 換一桌客人清的是那一間的紀錄，
        不該把別間包廂正在唱的那一場一起抹掉。
        """
        with self._lock:
            if room is None:
                self._entries = []
            else:
                want = str(room)
                self._entries = [e for e in self._entries
                                 if str(e.get("room") or DEFAULT_ROOM) != want]
            self._save()
        logger.info(f"已唱歷史已清空（{room or '全部包廂'}）")
