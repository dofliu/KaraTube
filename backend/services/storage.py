import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from backend.config import SONGS_DIR

logger = logging.getLogger("KaraTube.Storage")

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
        import shutil
        song_folder = self.storage_dir / song_id
        if song_folder.exists():
            shutil.rmtree(song_folder, ignore_errors=True)
            return True
        return False
