import logging
from typing import List, Dict, Any
import yt_dlp
from backend.config import SONGS_DIR

logger = logging.getLogger("KaraTube.SearchService")

class YouTubeSearchService:
    def __init__(self):
        pass

    def search(self, query: str, max_results: int = 15) -> List[Dict[str, Any]]:
        """Search YouTube for songs by keyword or direct URL."""
        query = query.strip()
        if not query:
            return []

        # Check if direct YouTube URL
        is_url = "youtube.com" in query or "youtu.be" in query
        search_target = query if is_url else f"ytsearch{max_results}:{query}"

        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': True,
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

        results = []
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(search_target, download=False)
                entries = info.get('entries', []) if 'entries' in info else [info]

                for item in entries:
                    if not item:
                        continue
                    video_id = item.get('id')
                    if not video_id:
                        continue

                    duration_sec = item.get('duration') or 0
                    mins, secs = divmod(int(duration_sec), 60)
                    duration_str = f"{mins:02d}:{secs:02d}"

                    # Check if already processed in local cache
                    is_cached = (SONGS_DIR / video_id / "metadata.json").exists()

                    thumbnail = item.get('thumbnail')
                    if not thumbnail or "http" not in thumbnail:
                        thumbnail = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"

                    results.append({
                        "id": video_id,
                        "title": item.get('title', 'Unknown Title'),
                        "uploader": item.get('uploader') or item.get('channel', 'Unknown Artist'),
                        "duration": duration_sec,
                        "duration_string": duration_str,
                        "thumbnail": thumbnail,
                        "url": f"https://www.youtube.com/watch?v={video_id}",
                        "is_cached": is_cached
                    })
        except Exception as e:
            logger.error(f"Search failed for query '{query}': {e}")

        return results
