import asyncio
import uuid
import logging
from typing import List, Dict, Any, Optional, Callable
from backend.pipeline.song_processor import SongProcessor
from backend.services.storage import SongStorage
from backend.services.play_stats import PlayStats

logger = logging.getLogger("KaraTube.QueueManager")

class QueueManager:
    def __init__(self, song_processor: SongProcessor, storage: SongStorage,
                 broadcast_cb: Optional[Callable] = None, play_stats: Optional[PlayStats] = None):
        self.processor = song_processor
        self.storage = storage
        self.broadcast_cb = broadcast_cb
        self.play_stats = play_stats

        self.current_song: Optional[Dict[str, Any]] = None
        self.queue: List[Dict[str, Any]] = []
        self.history: List[Dict[str, Any]] = []
        self.is_playing: bool = False

        # Audio and Control state
        self.vocal_volume: float = 0.0 # 0.0 = pure instrumental, 1.0 = full vocal
        self.pitch_shift: int = 0      # -6 to +6 semitones
        self.music_volume: float = 1.0
        self.mic_volume: float = 1.0
        # KTV 效果。殘響與回音是兩條獨立的路徑，回音的「音量」與「重複次數」也分開，
        # 這樣「小聲但多次」跟「大聲但一次」才調得出來。
        self.mic_reverb: float = 0.25
        self.mic_echo: float = 0.15
        self.mic_echo_repeat: float = 0.4
        self.mic_echo_time_ms: int = 280
        # 高頻柔化量。齒音與回授自激都集中在 5.5kHz 以上。
        self.mic_tone: float = 0.4
        # 演唱模式：solo = 人聲不進喇叭（筆電內建麥克風唯一安全的用法）
        #           party = 人聲外放，需要外接喇叭
        self.sing_mode: str = "solo"
        # 字幕微調（毫秒，正值 = 字幕延後）。喇叭／藍牙輸出延遲因場地而異，
        # 演唱中要能即時修，所以放在共享狀態裡讓點歌台與舞台端都能改。
        self.lyric_offset_ms: int = 0
        # 舞台是否顯示音準導唱線。關掉可以讓 MV 畫面完整露出來。
        self.show_pitch: bool = True

    def set_broadcast_callback(self, cb: Callable):
        self.broadcast_cb = cb

    async def broadcast_state(self):
        if self.broadcast_cb:
            payload = {
                "type": "STATE_UPDATE",
                "data": self.get_full_state()
            }
            await self.broadcast_cb(payload)

    def get_full_state(self) -> Dict[str, Any]:
        return {
            "current_song": self.current_song,
            "queue": self.queue,
            "history": self.history[-10:],
            "is_playing": self.is_playing,
            "vocal_volume": self.vocal_volume,
            "pitch_shift": self.pitch_shift,
            "music_volume": self.music_volume,
            "mic_volume": self.mic_volume,
            "mic_reverb": self.mic_reverb,
            "mic_echo": self.mic_echo,
            "mic_echo_repeat": self.mic_echo_repeat,
            "mic_echo_time_ms": self.mic_echo_time_ms,
            "mic_tone": self.mic_tone,
            "sing_mode": self.sing_mode,
            "lyric_offset_ms": self.lyric_offset_ms,
            "show_pitch": self.show_pitch
        }

    async def add_song(self, url_or_id: str, title: str = "", artist: str = "", thumbnail: str = "", priority: bool = False) -> Dict[str, Any]:
        """Add song to queue or insert at top (插播)."""
        # Resolve song_id
        import re
        if "youtube.com" in url_or_id or "youtu.be" in url_or_id:
            match = re.search(r'(?:v=|\/)([0-9A-Za-z_-]{11})', url_or_id)
            song_id = match.group(1) if match else "temp_" + str(abs(hash(url_or_id)) % 1000000)
        else:
            song_id = url_or_id

        # Check existing metadata if cached
        cached_meta = self.storage.get_song_metadata(song_id)
        if cached_meta:
            title = cached_meta.get("title", title)
            artist = cached_meta.get("artist", artist)
            thumbnail = cached_meta.get("thumbnail", thumbnail)
            status = "READY"
            progress = 100
        else:
            status = "PENDING"
            progress = 0

        queue_item = {
            "queue_id": str(uuid.uuid4()),
            "song_id": song_id,
            "url": f"https://www.youtube.com/watch?v={song_id}",
            "title": title or "Loading...",
            "artist": artist or "",
            "thumbnail": thumbnail or f"https://img.youtube.com/vi/{song_id}/hqdefault.jpg",
            "status": status,
            "progress": progress,
            "status_text": "Queued" if status == "PENDING" else "Ready (Cached)"
        }

        if priority:
            self.queue.insert(0, queue_item)
        else:
            self.queue.append(queue_item)

        await self.broadcast_state()

        # Trigger background processing if not ready
        if status != "READY":
            asyncio.create_task(self._process_queue_item(queue_item))
        else:
            # If nothing currently playing, play immediately
            if self.current_song is None:
                await self.play_next()

        return queue_item

    async def _process_queue_item(self, item: Dict[str, Any]):
        song_id = item["song_id"]

        def on_progress(sid, msg, pct):
            item["status_text"] = msg
            item["progress"] = pct
            if pct == 100:
                item["status"] = "READY"
            elif pct < 0:
                item["status"] = "ERROR"
            asyncio.create_task(self.broadcast_state())

        try:
            meta = await self.processor.process_song(item["url"], progress_callback=on_progress)
            item["title"] = meta.get("title", item["title"])
            item["artist"] = meta.get("artist", item["artist"])
            item["thumbnail"] = meta.get("thumbnail", item["thumbnail"])
            item["status"] = "READY"
            item["progress"] = 100
            item["status_text"] = "Ready"

            # Auto-play if nothing is currently playing and this is the head of queue
            if self.current_song is None and self.queue and self.queue[0]["queue_id"] == item["queue_id"]:
                await self.play_next()
            else:
                await self.broadcast_state()

        except Exception as e:
            logger.error(f"Failed processing item {song_id}: {e}")
            item["status"] = "ERROR"
            item["status_text"] = f"Failed: {str(e)}"
            await self.broadcast_state()

    async def play_next(self) -> Optional[Dict[str, Any]]:
        """Advance queue and play the next ready song."""
        if not self.queue:
            self.current_song = None
            self.is_playing = False
            await self.broadcast_state()
            return None

        # Find first ready song in queue
        next_item = None
        for i, item in enumerate(self.queue):
            if item["status"] == "READY":
                next_item = self.queue.pop(i)
                break

        if next_item:
            if self.current_song:
                self.history.append(self.current_song)
            self.current_song = next_item
            self.is_playing = True
            # 真正上台才計入點唱排行，排進佇列又被移除的不算
            if self.play_stats:
                self.play_stats.record_play(next_item)
            await self.broadcast_state()
            return next_item
        else:
            logger.info("Next song in queue is not ready yet.")
            return None

    async def skip_current(self):
        """Cut / Skip currently playing song."""
        logger.info("Skipping current song...")
        return await self.play_next()

    async def restart_current(self):
        """Restart the current song from beginning."""
        if self.current_song:
            if self.broadcast_cb:
                await self.broadcast_cb({
                    "type": "CONTROL_COMMAND",
                    "command": "RESTART"
                })

    async def remove_from_queue(self, queue_id: str):
        self.queue = [item for item in self.queue if item["queue_id"] != queue_id]
        await self.broadcast_state()

    async def reorder_queue(self, from_idx: int, to_idx: int):
        if 0 <= from_idx < len(self.queue) and 0 <= to_idx < len(self.queue):
            item = self.queue.pop(from_idx)
            self.queue.insert(to_idx, item)
            await self.broadcast_state()

    async def update_controls(self, params: Dict[str, Any]):
        if "vocal_volume" in params:
            self.vocal_volume = float(params["vocal_volume"])
        if "pitch_shift" in params:
            self.pitch_shift = int(params["pitch_shift"])
        if "music_volume" in params:
            self.music_volume = float(params["music_volume"])
        if "mic_volume" in params:
            self.mic_volume = float(params["mic_volume"])
        if "mic_reverb" in params:
            self.mic_reverb = max(0.0, min(1.0, float(params["mic_reverb"])))
        if "mic_echo" in params:
            self.mic_echo = max(0.0, min(1.0, float(params["mic_echo"])))
        if "mic_echo_repeat" in params:
            self.mic_echo_repeat = max(0.0, min(1.0, float(params["mic_echo_repeat"])))
        if "mic_echo_time_ms" in params:
            self.mic_echo_time_ms = int(max(50, min(800, int(params["mic_echo_time_ms"]))))
        if "mic_tone" in params:
            self.mic_tone = max(0.0, min(1.0, float(params["mic_tone"])))
        if "sing_mode" in params:
            self.sing_mode = "party" if params["sing_mode"] == "party" else "solo"
        if "lyric_offset_ms" in params:
            self.lyric_offset_ms = int(max(-2000, min(2000, int(params["lyric_offset_ms"]))))
        if "show_pitch" in params:
            self.show_pitch = bool(params["show_pitch"])
        if "is_playing" in params:
            self.is_playing = bool(params["is_playing"])

        await self.broadcast_state()
