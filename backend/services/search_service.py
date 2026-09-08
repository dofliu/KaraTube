import logging
from typing import List, Dict, Any
import yt_dlp
from backend.config import SONGS_DIR

logger = logging.getLogger("KaraTube.SearchService")

# 播放清單一次最多展開幾首。整晚也跑不完更多，而且 extract_flat 抓 500 筆要等很久。
MAX_PLAYLIST_ITEMS = 200


def is_playlist_url(text: str) -> bool:
    """判斷這行是不是播放清單網址。

    `watch?v=xxx&list=yyy` 這種「清單裡的某一首」刻意**不**算播放清單：
    使用者從清單中複製某首歌的網址時，想點的是那一首，不是整張清單。
    要整張清單的話網址長 `playlist?list=`（YouTube 的「分享整個播放清單」給的就是這個）。
    """
    line = (text or "").strip().lower()
    if "youtube.com" not in line and "youtu.be" not in line:
        return False
    return "playlist?list=" in line or "/playlist" in line


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

    def expand_sources(self, lines: List[str], limit: int = MAX_PLAYLIST_ITEMS) -> Dict[str, Any]:
        """
        把使用者貼進來的每一行展開成歌曲清單（排程預處理用）。

        一行可以是三種東西，混在一起貼也吃得下：
          * 播放清單網址 → 展開成整張清單
          * 單曲網址或 11 碼影片 ID → 就那一首
          * 關鍵字 → 搜尋結果的第一首（跟使用者在搜尋框按 Enter 後點第一張卡片一樣）

        展開不到的行不會讓整批失敗，而是收進 `failed` 回報給使用者 ——
        貼了 40 行結果第 7 行打錯字，該做的是跑剩下的 39 首並告訴他哪一行有問題。
        """
        from backend.services.batch_scheduler import extract_video_id

        songs: List[Dict[str, Any]] = []
        failed: List[str] = []
        seen = set()

        def take(entries: List[Dict[str, Any]], source_line: str):
            if not entries:
                failed.append(source_line)
                return
            for entry in entries:
                if len(songs) >= limit:
                    return
                song_id = entry.get("id")
                if not song_id or song_id in seen:
                    continue
                seen.add(song_id)
                songs.append({
                    "song_id": song_id,
                    "title": entry.get("title", ""),
                    "artist": entry.get("uploader", ""),
                    "thumbnail": entry.get("thumbnail", ""),
                    "url": entry.get("url") or f"https://www.youtube.com/watch?v={song_id}",
                })

        for line in lines:
            if len(songs) >= limit:
                break
            try:
                if is_playlist_url(line):
                    take(self.search(line), line)
                    continue
                video_id = extract_video_id(line)
                if video_id:
                    take(self.search(f"https://www.youtube.com/watch?v={video_id}"), line)
                    continue
                take(self.search(line, max_results=1)[:1], line)
            except Exception as e:
                logger.warning(f"展開來源失敗「{line}」: {e}")
                failed.append(line)

        return {"songs": songs, "failed": failed}
