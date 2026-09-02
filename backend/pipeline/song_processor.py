import asyncio
import logging
from pathlib import Path
from typing import Dict, Any, Callable, Optional

from backend.config import SONGS_DIR, DEMUCS_MODEL, WHISPER_MODEL_SIZE, DEVICE, COMPUTE_TYPE
from backend.pipeline.downloader import YouTubeDownloader
from backend.pipeline.separator import VocalSeparator
from backend.pipeline.lyrics_aligner import LyricsAligner
from backend.pipeline.pitch_extractor import PitchExtractor

logger = logging.getLogger("KaraTube.SongProcessor")

class SongProcessor:
    def __init__(self):
        self.downloader = YouTubeDownloader(output_dir=SONGS_DIR)
        self.separator = VocalSeparator(model_name=DEMUCS_MODEL, device=DEVICE)
        self.lyrics_aligner = LyricsAligner(model_size=WHISPER_MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE)
        self.pitch_extractor = PitchExtractor()

    def is_song_ready(self, song_id: str) -> bool:
        """Check if all assets for this song already exist in cache."""
        song_dir = SONGS_DIR / song_id
        if not song_dir.exists():
            return False

        has_audio = (song_dir / "instrumental.mp3").exists() and (song_dir / "vocals.mp3").exists()
        has_lyrics = (song_dir / "lyrics.json").exists()
        has_meta = (song_dir / "metadata.json").exists()
        return has_audio and has_lyrics and has_meta

    async def process_song(
        self,
        url_or_id: str,
        progress_callback: Optional[Callable[[str, str, int], None]] = None
    ) -> Dict[str, Any]:
        """
        Execute the full pipeline asynchronously.
        progress_callback(song_id, status_message, percentage)
        """
        # Resolve song_id / url
        if "youtube.com" in url_or_id or "youtu.be" in url_or_id:
            url = url_or_id
            # Extract video id
            import re
            match = re.search(r'(?:v=|\/)([0-9A-Za-z_-]{11})', url)
            song_id = match.group(1) if match else "temp_" + str(abs(hash(url)) % 1000000)
        else:
            song_id = url_or_id
            url = f"https://www.youtube.com/watch?v={song_id}"

        song_dir = SONGS_DIR / song_id
        song_dir.mkdir(parents=True, exist_ok=True)

        meta_file = song_dir / "metadata.json"
        inst_file = song_dir / "instrumental.mp3"
        voc_file = song_dir / "vocals.mp3"
        lyrics_file = song_dir / "lyrics.json"
        pitch_file = song_dir / "pitch.json"

        # Check cache
        if self.is_song_ready(song_id):
            logger.info(f"Song {song_id} already fully processed and cached.")
            if progress_callback:
                progress_callback(song_id, "Ready (Cached)", 100)
            import json
            with open(meta_file, 'r', encoding='utf-8') as f:
                return json.load(f)

        try:
            # 1. Download
            if progress_callback:
                progress_callback(song_id, "Downloading YouTube Video & Audio...", 15)
            logger.info(f"[{song_id}] Downloading...")
            loop = asyncio.get_event_loop()
            meta = await loop.run_in_executor(None, self.downloader.download, url, song_id)

            audio_path = Path(meta["audio_path"])
            title = meta.get("title", "Unknown Title")
            artist = meta.get("artist", "")

            # 2. Vocal Separation
            if progress_callback:
                progress_callback(song_id, "AI Separating Instrumental & Vocals (Demucs)...", 45)
            logger.info(f"[{song_id}] Separating stems...")
            inst_path, voc_path = await loop.run_in_executor(
                None, self.separator.separate, audio_path, song_dir
            )

            # 3. Lyrics Alignment
            if progress_callback:
                progress_callback(song_id, "Fetching & Aligning Karaoke Lyrics (Word-Level)...", 75)
            logger.info(f"[{song_id}] Aligning lyrics...")
            await loop.run_in_executor(
                None, self.lyrics_aligner.align, voc_path, title, artist, lyrics_file
            )

            # 4. Pitch Extraction
            if progress_callback:
                progress_callback(song_id, "Extracting Pitch Curve & Guide Notes...", 90)
            logger.info(f"[{song_id}] Extracting pitch...")
            await loop.run_in_executor(
                None, self.pitch_extractor.extract_pitch, voc_path, pitch_file
            )

            # Update Metadata
            meta["instrumental_path"] = str(inst_file)
            meta["vocals_path"] = str(voc_file)
            meta["lyrics_path"] = str(lyrics_file)
            meta["pitch_path"] = str(pitch_file)
            meta["status"] = "READY"

            import json
            with open(meta_file, 'w', encoding='utf-8') as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)

            if progress_callback:
                progress_callback(song_id, "Ready to Sing!", 100)
            logger.info(f"[{song_id}] Processing pipeline completed successfully.")
            return meta

        except Exception as e:
            logger.exception(f"Pipeline error on song {song_id}: {e}")
            if progress_callback:
                progress_callback(song_id, f"Error: {str(e)}", -1)
            raise e
