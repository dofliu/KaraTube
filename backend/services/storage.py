import json
import logging
import shutil
from pathlib import Path
from typing import List, Dict, Any, Optional
from backend.config import SONGS_DIR

logger = logging.getLogger("KaraTube.Storage")

# 一首歌要能直接上台演唱，快取資料夾裡至少要有這四個檔案
REQUIRED_FILES = ("metadata.json", "instrumental.mp3", "vocals.mp3", "lyrics.json")

class SongStorage:
    def __init__(self, storage_dir: Path = SONGS_DIR):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def get_song_metadata(self, song_id: str) -> Optional[Dict[str, Any]]:
        meta_file = self.storage_dir / song_id / "metadata.json"
        if meta_file.exists():
            try:
                with open(meta_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error reading metadata for {song_id}: {e}")
        return None

    def get_song_lyrics(self, song_id: str) -> List[Dict[str, Any]]:
        lyrics_file = self.storage_dir / song_id / "lyrics.json"
        if lyrics_file.exists():
            try:
                with open(lyrics_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error reading lyrics for {song_id}: {e}")
        return []

    def get_song_pitch(self, song_id: str) -> Dict[str, Any]:
        pitch_file = self.storage_dir / song_id / "pitch.json"
        if pitch_file.exists():
            try:
                with open(pitch_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error reading pitch for {song_id}: {e}")
        return {"notes": [], "points": []}

    def list_cached_songs(self) -> List[Dict[str, Any]]:
        songs = []
        for song_folder in self.storage_dir.iterdir():
            if song_folder.is_dir():
                meta = self.get_song_metadata(song_folder.name)
                if meta:
                    songs.append(meta)
        # Sort by latest or title
        return songs

    def delete_song(self, song_id: str) -> bool:
        song_folder = self.storage_dir / song_id
        if song_folder.exists():
            shutil.rmtree(song_folder, ignore_errors=True)
            return True
        return False

    def get_song_size(self, song_id: str) -> int:
        """一首快取歌曲佔用的磁碟空間（bytes）。不存在回傳 0。"""
        song_folder = self.storage_dir / song_id
        if not song_folder.is_dir():
            return 0
        total = 0
        for f in song_folder.rglob("*"):
            try:
                if f.is_file():
                    total += f.stat().st_size
            except OSError:
                pass
        return total

    def list_cache_entries(self) -> List[Dict[str, Any]]:
        """快取管理用的完整清單：連沒有 metadata 的壞資料夾也列出來。

        `list_cached_songs()` 只給點歌用，壞資料夾會被藏起來；
        但壞資料夾照樣佔磁碟，管理介面必須看得到才刪得掉。
        每筆包含 complete 旗標（四個必要檔案都在才算完整）。
        """
        entries = []
        for song_folder in sorted(self.storage_dir.iterdir()):
            if not song_folder.is_dir():
                continue
            song_id = song_folder.name
            meta = self.get_song_metadata(song_id) or {}
            missing = [f for f in REQUIRED_FILES if not (song_folder / f).exists()]
            try:
                cached_at = song_folder.stat().st_mtime
            except OSError:
                cached_at = 0
            entries.append({
                "song_id": song_id,
                "title": meta.get("title", ""),
                "artist": meta.get("artist", ""),
                "thumbnail": meta.get("thumbnail", ""),
                "size_bytes": self.get_song_size(song_id),
                "complete": not missing,
                "missing_files": missing,
                "cached_at": cached_at,
            })
        # 最新快取排最前面，跟其他分頁一致
        entries.sort(key=lambda e: e["cached_at"], reverse=True)
        return entries

    def cache_stats(self) -> Dict[str, Any]:
        """快取總覽：歌曲數、佔用空間、磁碟剩餘空間。"""
        entries = self.list_cache_entries()
        total_bytes = sum(e["size_bytes"] for e in entries)
        try:
            usage = shutil.disk_usage(self.storage_dir)
            disk_free, disk_total = usage.free, usage.total
        except OSError:
            disk_free, disk_total = 0, 0
        return {
            "song_count": len(entries),
            "complete_count": sum(1 for e in entries if e["complete"]),
            "incomplete_count": sum(1 for e in entries if not e["complete"]),
            "total_bytes": total_bytes,
            "disk_free_bytes": disk_free,
            "disk_total_bytes": disk_total,
        }
