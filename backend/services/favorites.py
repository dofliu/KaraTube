"""
我的最愛（常唱歌曲）

KTV 包廂的基本配備：把常唱的歌收藏起來，下次進包廂不用重新搜尋。
與點唱統計不同 —— 收藏是使用者主動標記的，跟唱過幾次無關。
存成 cache/favorites.json，重開機不會消失。
"""
import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("KaraTube.Favorites")


class Favorites:
    def __init__(self, favorites_file: Path):
        self.favorites_file = Path(favorites_file)
        # REST 與 WebSocket 兩邊都可能同時進來，讀寫要上鎖
        self._lock = threading.Lock()
        self._data: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self):
        if not self.favorites_file.exists():
            return
        try:
            raw = json.loads(self.favorites_file.read_text(encoding="utf-8"))
            self._data = raw.get("songs", {}) if isinstance(raw, dict) else {}
        except Exception as e:
            logger.warning(f"收藏清單讀取失敗，重新開始: {e}")
            self._data = {}

    def _save(self):
        try:
            self.favorites_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {"updated_at": datetime.now().isoformat(timespec="seconds"),
                       "songs": self._data}
            self.favorites_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"收藏清單寫入失敗: {e}")

    def add(self, song: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        song_id = song.get("song_id") or song.get("id")
        if not song_id:
            return None
        with self._lock:
            entry = self._data.get(song_id) or {
                "song_id": song_id,
                "added_at": datetime.now().isoformat(timespec="seconds"),
            }
            # 標題與縮圖可能因為重新抓取而更新，每次覆寫成最新的
            for key in ("title", "artist", "thumbnail"):
                if song.get(key):
                    entry[key] = song[key]
            self._data[song_id] = entry
            self._save()
            return dict(entry)

    def remove(self, song_id: str) -> bool:
        with self._lock:
            if song_id in self._data:
                del self._data[song_id]
                self._save()
                return True
            return False

    def toggle(self, song: Dict[str, Any]) -> Dict[str, Any]:
        """收藏 ↔ 取消收藏，回傳最終狀態，讓前端一顆按鈕搞定。"""
        song_id = song.get("song_id") or song.get("id")
        if not song_id:
            return {"favorited": False}
        if self.contains(song_id):
            self.remove(song_id)
            return {"song_id": song_id, "favorited": False}
        entry = self.add(song)
        return {"song_id": song_id, "favorited": True, "entry": entry}

    def contains(self, song_id: str) -> bool:
        with self._lock:
            return song_id in self._data

    def list_all(self) -> List[Dict[str, Any]]:
        # 回傳副本，最新收藏排最前面
        with self._lock:
            items = [dict(v) for v in self._data.values()]
        items.sort(key=lambda x: x.get("added_at") or "", reverse=True)
        return items

    def ids(self) -> List[str]:
        with self._lock:
            return list(self._data.keys())
