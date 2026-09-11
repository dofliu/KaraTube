import asyncio
import uuid
import logging
from typing import List, Dict, Any, Optional, Callable
from backend.pipeline.song_processor import SongProcessor
from backend.services.storage import SongStorage
from backend.services.play_stats import PlayStats
from backend.services.song_history import SongHistory
# 和聲風格的選項只有一份（設定頁與控制參數共用），避免兩邊各列一次而漂走
from backend.services.settings import HARMONY_STYLE_CHOICES

logger = logging.getLogger("KaraTube.QueueManager")

# 練唱循環的最短長度。比這更短的 A-B 區間只會變成跳針，多半是使用者連按兩下設錯的。
MIN_LOOP_SECONDS = 1.0


def coerce_position(value: Any) -> Optional[float]:
    """把前端送來的秒數轉成合法的播放位置。看不懂或是 NaN 就回 None（代表沒設）。"""
    if value is None:
        return None
    try:
        pos = float(value)
    except (TypeError, ValueError):
        return None
    if pos != pos:  # NaN
        return None
    return round(max(0.0, pos), 3)


class QueueManager:
    def __init__(self, song_processor: SongProcessor, storage: SongStorage,
                 broadcast_cb: Optional[Callable] = None, play_stats: Optional[PlayStats] = None,
                 song_history: Optional[SongHistory] = None, settings: Optional[Any] = None):
        self.processor = song_processor
        self.storage = storage
        self.broadcast_cb = broadcast_cb
        self.play_stats = play_stats
        self.song_history = song_history
        # 系統設定（可為 None：測試與舊呼叫端不必提供）
        self.settings = settings

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
        # 和聲（雙聲部）。風格是「音階上的度數」而不是固定半音數，
        # 實際移調量由舞台端依這首歌的調性決定（frontend/js/harmony-planner.js）。
        self.harmony_enabled: bool = False
        self.harmony_style: str = "third"
        self.harmony_level: float = 0.5
        # 對唱模式：兩支麥克風分別評分。
        # 開關與兩位演唱者的暱稱是共享狀態（點歌台按下去所有裝置同步），
        # 但「第二支麥克風接在哪」是那台機器的硬體接法，記在舞台端的 localStorage。
        self.duet_enabled: bool = False
        self.duet_name_a: str = ""
        self.duet_name_b: str = ""
        # 演唱模式：solo = 人聲不進喇叭（筆電內建麥克風唯一安全的用法）
        #           party = 人聲外放，需要外接喇叭
        self.sing_mode: str = "solo"
        # 字幕微調（毫秒，正值 = 字幕延後）。喇叭／藍牙輸出延遲因場地而異，
        # 演唱中要能即時修，所以放在共享狀態裡讓點歌台與舞台端都能改。
        self.lyric_offset_ms: int = 0
        # 舞台是否顯示音準導唱線。關掉可以讓 MV 畫面完整露出來。
        self.show_pitch: bool = True
        # 練唱模式：A-B 區段循環（商用點歌機的「副歌重播」）。
        # 三個欄位放在共享狀態裡，點歌台設好的區間，舞台端與其他手機看到的是同一組。
        # A-B 點屬於「這一首歌」，換歌時要清掉，否則下一首會在莫名其妙的地方跳回去。
        self.loop_enabled: bool = False
        self.loop_start: Optional[float] = None
        self.loop_end: Optional[float] = None

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
            "harmony_enabled": self.harmony_enabled,
            "harmony_style": self.harmony_style,
            "harmony_level": self.harmony_level,
            "duet_enabled": self.duet_enabled,
            "duet_name_a": self.duet_name_a,
            "duet_name_b": self.duet_name_b,
            "sing_mode": self.sing_mode,
            "lyric_offset_ms": self.lyric_offset_ms,
            "show_pitch": self.show_pitch,
            "loop_enabled": self.loop_enabled,
            "loop_start": self.loop_start,
            "loop_end": self.loop_end
        }

    async def add_song(self, url_or_id: str, title: str = "", artist: str = "", thumbnail: str = "",
                       priority: bool = False, requested_by: str = "") -> Dict[str, Any]:
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
        # 這首有沒有 MV。舞台端靠它決定要不要直接上情境背景 ——
        # 不給的話舞台只能先試著載 original_video.mp4、等 404 回來才知道，
        # 那一秒的黑畫面剛好落在片頭的第一句。還沒處理的歌回 None（不知道），
        # 舞台會先當成有、載不起來再改口（見 ambient-visuals.js startSong）。
        has_video = None
        if cached_meta:
            title = cached_meta.get("title", title)
            artist = cached_meta.get("artist", artist)
            thumbnail = cached_meta.get("thumbnail", thumbnail)
            has_video = bool(cached_meta.get("video_path"))
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
            "has_video": has_video,
            "status": status,
            "progress": progress,
            "status_text": "Queued" if status == "PENDING" else "Ready (Cached)",
            # 多人包廂：這首是誰點的。長度截 24 字，防手機端惡搞塞爆佇列版面。
            "requested_by": str(requested_by or "").strip()[:24],
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
            # 流水線跑完才知道 MV 到底有沒有下到（很多歌只有音訊）
            item["has_video"] = bool(meta.get("video_path"))
            item["status"] = "READY"
            item["progress"] = 100
            item["status_text"] = "Ready"

            # 剛多了一首歌的磁碟用量，這時候檢查上限最準
            self._cleanup_cache_if_needed()

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
            # 上一首圈起來的練唱區間對這一首沒有意義，換人上台就歸零
            self.clear_loop()
            self.is_playing = True
            # 真正上台才計入點唱排行與已唱歷史，排進佇列又被移除的不算
            if self.play_stats:
                self.play_stats.record_play(next_item)
            if self.song_history:
                self.song_history.record(next_item)
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

    async def seek_to(self, position: Any) -> float:
        """
        要舞台端跳到指定秒數（進度條拖曳、段落跳轉、回到 A 點都走這條）。

        跳轉是舞台端的媒體操作，伺服器不持有播放位置，所以只送指令不改狀態。
        回傳夾限後真正送出去的位置，讓 API 可以回報給呼叫端。
        """
        pos = coerce_position(position) or 0.0
        if self.broadcast_cb:
            await self.broadcast_cb({
                "type": "CONTROL_COMMAND",
                "command": "SEEK",
                "position": pos
            })
        return pos

    def clear_loop(self):
        """關掉 A-B 循環並清掉區間。"""
        self.loop_enabled = False
        self.loop_start = None
        self.loop_end = None

    def _normalize_loop(self):
        """
        把 A-B 區間收斂成合法狀態。

        先按 B 再按 A 是很自然的操作（聽到一半才想圈這段），所以順序反了就對調，
        而不是報錯。區間缺一角或短到會跳針時，循環一律關掉 ——
        寧可不循環，也不要讓舞台在同一秒鐘上瘋狂 seek。
        """
        if (self.loop_start is not None and self.loop_end is not None
                and self.loop_end < self.loop_start):
            self.loop_start, self.loop_end = self.loop_end, self.loop_start
        if (self.loop_start is None or self.loop_end is None
                or self.loop_end - self.loop_start < MIN_LOOP_SECONDS):
            self.loop_enabled = False

    def is_song_in_use(self, song_id: str) -> bool:
        """歌曲正在演唱或還在佇列裡。使用中的快取不能刪，刪了舞台會直接斷片。"""
        if self.current_song and self.current_song.get("song_id") == song_id:
            return True
        return any(item.get("song_id") == song_id for item in self.queue)

    async def retry_item(self, queue_id: str) -> Optional[Dict[str, Any]]:
        """重新處理佇列裡失敗的歌（下載被斷線、模型爆掉之類的暫時性錯誤）。"""
        for item in self.queue:
            if item["queue_id"] == queue_id and item["status"] == "ERROR":
                item["status"] = "PENDING"
                item["progress"] = 0
                item["status_text"] = "Retrying..."
                await self.broadcast_state()
                asyncio.create_task(self._process_queue_item(item))
                return item
        return None

    async def remove_from_queue(self, queue_id: str):
        self.queue = [item for item in self.queue if item["queue_id"] != queue_id]
        await self.broadcast_state()

    async def reorder_queue(self, from_idx: int, to_idx: int):
        if 0 <= from_idx < len(self.queue) and 0 <= to_idx < len(self.queue):
            item = self.queue.pop(from_idx)
            self.queue.insert(to_idx, item)
            await self.broadcast_state()

    def apply_control_defaults(self) -> Dict[str, Any]:
        """
        把系統設定裡的「開機預設」套進目前的控制狀態。

        開機時呼叫一次（此時還沒有人連線，不需要廣播），
        設定頁按下「套用到現在」時也會走這條，只是那邊會再廣播一次。
        """
        if not self.settings:
            return {}
        defaults = self.settings.control_defaults()
        self._apply_controls(defaults)
        return defaults

    def _cleanup_cache_if_needed(self):
        """快取自動清理：超過設定的上限就從最舊的歌開始刪，演唱中／佇列裡的不動。"""
        if not self.settings or not self.settings.get("cache_auto_cleanup"):
            return
        limit = self.settings.cache_limit_bytes()
        if limit <= 0:
            return
        protected = {item["song_id"] for item in self.queue}
        if self.current_song:
            protected.add(self.current_song["song_id"])
        try:
            removed = self.storage.enforce_cache_limit(limit, protected)
        except Exception as e:
            logger.warning(f"快取自動清理失敗: {e}")
            return
        if removed:
            logger.info(f"快取自動清理：釋放 {len(removed)} 首歌的空間")

    def _apply_controls(self, params: Dict[str, Any]):
        """把控制參數夾進合法範圍後寫入狀態。不廣播 —— 廣播由呼叫端決定。"""
        if "vocal_volume" in params:
            self.vocal_volume = float(params["vocal_volume"])
        if "pitch_shift" in params:
            self.pitch_shift = int(max(-6, min(6, int(params["pitch_shift"]))))
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
        if "harmony_enabled" in params:
            self.harmony_enabled = bool(params["harmony_enabled"])
        if "harmony_style" in params:
            # 認不得的風格保留原值：舊版舞台端送過來的字串不該讓和聲變成隨機風格
            style = str(params["harmony_style"])
            if style in HARMONY_STYLE_CHOICES:
                self.harmony_style = style
        if "harmony_level" in params:
            self.harmony_level = max(0.0, min(1.0, float(params["harmony_level"])))
        if "duet_enabled" in params:
            self.duet_enabled = bool(params["duet_enabled"])
        # 暱稱長度截 12 字：對唱計分板一行要塞兩個名字，手機端塞爆版面的話
        # 舞台上就看不到分數了（跟 requested_by 同一個理由，只是空間更小）
        if "duet_name_a" in params:
            self.duet_name_a = str(params["duet_name_a"] or "").strip()[:12]
        if "duet_name_b" in params:
            self.duet_name_b = str(params["duet_name_b"] or "").strip()[:12]
        if "sing_mode" in params:
            self.sing_mode = "party" if params["sing_mode"] == "party" else "solo"
        if "lyric_offset_ms" in params:
            self.lyric_offset_ms = int(max(-2000, min(2000, int(params["lyric_offset_ms"]))))
        if "show_pitch" in params:
            self.show_pitch = bool(params["show_pitch"])
        if "is_playing" in params:
            self.is_playing = bool(params["is_playing"])
        # 練唱 A-B 循環：三個欄位可以分開送（先設 A、再設 B、最後才開循環）
        if "loop_start" in params:
            self.loop_start = coerce_position(params["loop_start"])
        if "loop_end" in params:
            self.loop_end = coerce_position(params["loop_end"])
        if "loop_enabled" in params:
            self.loop_enabled = bool(params["loop_enabled"])
        self._normalize_loop()

    async def update_controls(self, params: Dict[str, Any]):
        self._apply_controls(params)
        await self.broadcast_state()
