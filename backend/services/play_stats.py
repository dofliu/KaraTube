"""
點唱統計

記錄每首歌實際上台演唱的次數，供點歌台的「熱門點唱排行」使用。
在歌曲真正開始播放時才計數，而不是加入佇列時 ——
排進去又被移除的歌不該佔排行。
"""
import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("KaraTube.PlayStats")


class PlayStats:
    def __init__(self, stats_file: Path):
        self.stats_file = Path(stats_file)
        # 統計會從 WebSocket 事件迴圈與 REST 兩邊進來，讀寫要上鎖
        self._lock = threading.Lock()
        self._data: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self):
        if not self.stats_file.exists():
            return
        try:
            raw = json.loads(self.stats_file.read_text(encoding="utf-8"))
            self._data = raw.get("songs", {}) if isinstance(raw, dict) else {}
        except Exception as e:
            logger.warning(f"點唱統計讀取失敗，重新開始計數: {e}")
            self._data = {}

    def _save(self):
        try:
            self.stats_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {"updated_at": datetime.now().isoformat(timespec="seconds"),
                       "songs": self._data}
            self.stats_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"點唱統計寫入失敗: {e}")

    def record_play(self, song: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        song_id = song.get("song_id") or song.get("id")
        if not song_id:
            return None

        with self._lock:
            entry = self._data.get(song_id) or {
                "song_id": song_id,
                "plays": 0,
                "first_played": None,
            }
            entry["plays"] = int(entry.get("plays", 0)) + 1
            entry["last_played"] = datetime.now().isoformat(timespec="seconds")
            if not entry.get("first_played"):
                entry["first_played"] = entry["last_played"]
            # 標題與縮圖可能因為重新抓取而更新，每次覆寫成最新的
            for key, src in (("title", "title"), ("artist", "artist"), ("thumbnail", "thumbnail")):
                if song.get(src):
                    entry[key] = song[src]
            self._data[song_id] = entry
            self._save()
            logger.info(f"點唱統計: {entry.get('title', song_id)} 累計 {entry['plays']} 次")
            return dict(entry)

    def top(self, limit: int = 20) -> List[Dict[str, Any]]:
        # 回傳副本 —— 名次是查詢當下算出來的，不該被寫回統計檔
        with self._lock:
            items = [dict(v) for v in self._data.values()]
        # 穩定排序：先排次要鍵（最近唱過的優先），再排主要鍵（次數）
        items.sort(key=lambda x: x.get("last_played") or "", reverse=True)
        items.sort(key=lambda x: int(x.get("plays", 0)), reverse=True)
        items = items[:limit]
        for i, item in enumerate(items):
            item["rank"] = i + 1
        return items

    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        """整份統計的副本（song_id → 紀錄）。曲庫瀏覽要一次查很多首，不適合逐首查。"""
        with self._lock:
            return {k: dict(v) for k, v in self._data.items()}

    def total_plays(self) -> int:
        with self._lock:
            return sum(int(v.get("plays", 0)) for v in self._data.values())

    def reset(self):
        with self._lock:
            self._data = {}
            self._save()
        logger.info("點唱統計已清空")
