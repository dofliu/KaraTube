import os
import re
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional
import yt_dlp

logger = logging.getLogger("KaraTube.Downloader")

class YouTubeDownloader:
    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def extract_info(self, url_or_query: str) -> Dict[str, Any]:
        """Extract metadata from YouTube URL or search query without downloading."""
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': 'in_playlist' if 'playlist' in url_or_query else False,
            'skip_download': True,
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'ios', 'web']
                }
            },
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'
            }
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url_or_query, download=False)
            if 'entries' in info and info['entries']:
                info = info['entries'][0]
            return {
                'id': info.get('id'),
                'title': info.get('title', 'Unknown Title'),
                'uploader': info.get('uploader') or info.get('channel', 'Unknown Artist'),
                'duration': info.get('duration', 0),
                'thumbnail': info.get('thumbnail', ''),
                'webpage_url': info.get('webpage_url', f"https://www.youtube.com/watch?v={info.get('id')}"),
            }

    def download(self, url: str, song_id: str, progress_hook=None) -> Dict[str, Any]:
        """Download both best video and best audio for the song."""
        song_dir = self.output_dir / song_id
        song_dir.mkdir(parents=True, exist_ok=True)

        audio_output = song_dir / "original_audio.mp3"
        video_output = song_dir / "original_video.mp4"
        meta_output = song_dir / "metadata.json"

        # Download Video & Audio with Android/iOS player client spoofing to prevent 403 Forbidden
        ydl_opts = {
            'format': 'bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4]/bestaudio/best',
            'outtmpl': {
                'default': str(song_dir / 'download.%(ext)s'),
            },
            'merge_output_format': 'mp4',
            'quiet': False,
            'no_warnings': True,
            'overwrites': True,
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'ios', 'web']
                }
            },
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'
            }
        }

        if progress_hook:
            ydl_opts['progress_hooks'] = [progress_hook]

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if 'entries' in info and info['entries']:
                info = info['entries'][0]

            title = info.get('title', 'Unknown Title')
            uploader = info.get('uploader') or info.get('channel', 'Unknown Artist')
            duration = info.get('duration', 0)
            thumbnail = info.get('thumbnail', '')

        # Locate downloaded merged file
        downloaded_mp4 = song_dir / "download.mp4"
        if downloaded_mp4.exists():
            downloaded_mp4.replace(video_output)
        else:
            # Check for any other extension downloaded
            for f in song_dir.glob("download.*"):
                if f.is_file() and f != meta_output:
                    f.replace(video_output)
                    break

        # Extract audio using ffmpeg from video file to mp3 if needed
        if video_output.exists() and not audio_output.exists():
            import subprocess
            subprocess.run([
                'ffmpeg', '-y', '-i', str(video_output),
                '-vn', '-acodec', 'libmp3lame', '-q:a', '2',
                str(audio_output)
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        metadata = {
            'id': song_id,
            'title': title,
            'artist': uploader,
            'duration': duration,
            'thumbnail': thumbnail,
            'url': url,
            'video_path': str(video_output) if video_output.exists() else None,
            'audio_path': str(audio_output) if audio_output.exists() else None,
        }

        with open(meta_output, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

        return metadata
