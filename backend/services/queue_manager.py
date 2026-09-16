import asyncio
import uuid
import logging
from typing import List, Dict, Any, Optional, Callable
from backend.pipeline.song_processor import SongProcessor
from backend.services.storage import SongStorage
from backend.services.play_stats import PlayStats
from backend.services.song_history import SongHistory
from backend.services import rotation as rotation_rules
from backend.services import room_timer as room_rules
from backend.services import song_quota
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
                 song_history: Optional[SongHistory] = None, settings: Optional[Any] = None,
                 room: Optional[Any] = None):
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
        # 公平輪唱（排麥輪序）：新點的歌照「這是誰的第幾首」插進佇列，
        # 讓一個人連點五首時其他人不必等完那五首。預設關著 ——
        # 開著會讓點的歌排到自己預期以外的位置，那是要先講好的規則，
        # 不是機器替包廂決定的事（見 backend/services/rotation.py 的設計說明）。
        self.rotation_enabled: bool = False
        self.rotation = rotation_rules.RotationTracker()
        # 每人待唱上限（點歌額度）：一個人同時最多能有幾首歌在等，0 = 不限。
        # 輪唱管順序、額度管量，是獨立的兩條規則（先到先唱的包廂也可能只想要
        # 「佇列不要被一個人塞滿」）。預設不限，見 backend/services/song_quota.py。
        self.pending_limit: int = song_quota.DEFAULT_PENDING_LIMIT
        # 包廂計時（歡唱時間）。計時本身在 room_timer，這裡只負責「時間到之後
        # 不再播下一首」那一個動作 —— 它是唯一一件計時管不到、但非它不可的事
        # （見 backend/services/room_timer.py 決定一）。
        # 沒給就開一個只活在記憶體裡的（測試與舊呼叫端）。
        self.room = room if room is not None else room_rules.RoomTimer()
        # 有人按過「結束計時」。自動開錶（設定頁的 room_timer_autostart）只在
        # 開機後的第一首歌生效一次，按過結束之後就不再自動把錶打開 ——
        # 見 _autostart_room_session。
        self._room_autostart_off = False

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
            "loop_end": self.loop_end,
            "rotation_enabled": self.rotation_enabled,
            # 輪次是算出來的（見 rotation.compute_rounds），所以每次廣播都重算一次，
            # 而不是寫在 queue item 上 —— 有人被刪、有人唱完，剩下的輪次全都要跟著變。
            "rotation": rotation_rules.rotation_summary(
                self.queue, self.rotation.counts(), self.rotation.names()),
            "pending_limit": self.pending_limit,
            # 額度用量跟輪次一樣是算出來的：有人被刪、有人上台，剩下的全都要跟著變。
            "quota": song_quota.quota_summary(self.queue, self.pending_limit),
            # 包廂計時。跟著每一次狀態廣播一起送，畫面才不必自己去輪詢一支
            # 「還剩多久」的端點 —— 而且倒數在點歌台與舞台上要是同一個數字。
            # 秒數只會在狀態變動時更新，每一秒的倒數由畫面自己跑（見 room-view.js）：
            # 為了一個倒數而每秒廣播一次整份狀態，是拿包廂的網路換一個時鐘。
            "room": self.room_state(),
        }

    # --- 包廂計時 ---

    def room_policy(self) -> Dict[str, Any]:
        """設定頁定下的計時規則。沒有設定物件（測試、舊呼叫端）就是「沒開」。"""
        if self.settings and hasattr(self.settings, "room_policy"):
            return self.settings.room_policy()
        return {"enabled": False, "minutes": room_rules.DEFAULT_SESSION_MINUTES,
                "autostart": False, "warn_minutes": room_rules.DEFAULT_WARN_MINUTES,
                "last_call_minutes": room_rules.DEFAULT_LAST_CALL_MINUTES,
                "expire_action": room_rules.DEFAULT_EXPIRE_ACTION,
                "extend_minutes": room_rules.DEFAULT_EXTEND_MINUTES}

    def room_state(self) -> Dict[str, Any]:
        """
        廣播給所有裝置的計時狀態 = 計時器的快照 + 現在生效的規則。

        規則跟著快照一起送，是因為畫面上那句話需要它們：「時間到會讓你唱完
        這一首」與「只是提醒、不會停」是兩句完全不同的話，而決定是哪一句的
        是設定，不是計時器。
        """
        policy = self.room_policy()
        snap = self.room.snapshot()
        return {
            **snap,
            "enabled": policy["enabled"],
            "expire_action": policy["expire_action"],
            "extend_minutes": policy["extend_minutes"],
            "default_minutes": policy["minutes"],
            "warn_minutes": policy["warn_minutes"],
            "last_call_minutes": policy["last_call_minutes"],
        }

    def room_stops_playback(self) -> bool:
        """
        時間到了、而且這台機器的規則是「到點就收」。

        只有這一個條件成立時，唱完的那一首才是今晚的最後一首。
        計時關著、或規則是「只提醒」時，這裡永遠回 False —— 一個沒有人打開的
        功能不該有任何機會去停掉別人的歌。
        """
        policy = self.room_policy()
        if not policy["enabled"] or policy["expire_action"] != "finish_song":
            return False
        return bool(self.room.snapshot()["expired"])

    async def _halt_for_room_time(self):
        """
        時間到，停在這裡。

        佇列一首都不刪（見 room_timer 決定二）：停下來的是播放，不是資料，
        續時之後接著唱的就是本來排好的那幾首。
        """
        if self.current_song:
            self.history.append(self.current_song)
        self.current_song = None
        self.is_playing = False
        self.room.mark_halted(True)
        await self.broadcast_state()

    def room_milestones(self):
        policy = self.room_policy()
        return room_rules.default_milestones(policy["warn_minutes"],
                                             policy["last_call_minutes"])

    async def tick_room(self) -> List[Dict[str, Any]]:
        """
        時鐘又走了一格（由伺服器的背景迴圈定期呼叫）。

        回傳這一刻剛跨過的提醒，讓呼叫端廣播出去。順便處理「時間到的時候
        剛好沒有歌在唱」這一種情形 —— 那時候沒有「唱完這一首」可以等，
        機器直接停在散場畫面。
        """
        policy = self.room_policy()
        if not policy["enabled"]:
            return []
        alerts = self.room.tick(self.room_milestones())
        # 歌與歌之間到點：沒有正在唱的那一首可以讓它唱完，直接收場
        if (self.current_song is None and self.room_stops_playback()
                and not self.room.snapshot()["halted"]):
            self.room.mark_halted(True)
            await self.broadcast_state()
        elif alerts:
            await self.broadcast_state()
        return alerts

    async def start_room_session(self, minutes: Any = None) -> Dict[str, Any]:
        """開一場（歸零重算）。沒指定長度就用設定頁的預設值。"""
        policy = self.room_policy()
        self.room.start(policy["minutes"] if minutes is None else minutes)
        self._room_autostart_off = False
        await self.broadcast_state()
        return self.room_state()

    async def extend_room_session(self, minutes: Any = None) -> Dict[str, Any]:
        """
        續時。停播中的話順便接回去唱下一首 ——
        按了「續時」還得再按一次播放的話，那顆續時鍵看起來就沒有反應。
        """
        policy = self.room_policy()
        self.room.extend(policy["extend_minutes"] if minutes is None else minutes)
        resumed = None
        if self.current_song is None and self.queue:
            resumed = await self.play_next()
        if resumed is None:
            await self.broadcast_state()
        return self.room_state()

    async def pause_room_session(self) -> Dict[str, Any]:
        """停錶（中場休息）。刻意不順便暫停播放：停錶是「不要算我的時間」，
        跟「把歌停下來」是兩件事，而且中場休息時多半還放著音樂。"""
        self.room.pause()
        await self.broadcast_state()
        return self.room_state()

    async def resume_room_session(self) -> Dict[str, Any]:
        self.room.resume()
        await self.broadcast_state()
        return self.room_state()

    async def stop_room_session(self) -> Dict[str, Any]:
        """
        結束計時。停播旗標一起解除 —— 這顆鍵的意思是「不要再管時間了」，
        結果卻讓機器停在散場畫面不肯播的話，沒有人找得到怎麼救回來。
        """
        self.room.stop()
        self._room_autostart_off = True
        resumed = None
        if self.current_song is None and self.queue:
            resumed = await self.play_next()
        if resumed is None:
            await self.broadcast_state()
        return self.room_state()

    async def add_song(self, url_or_id: str, title: str = "", artist: str = "", thumbnail: str = "",
                       priority: bool = False, requested_by: str = "") -> Dict[str, Any]:
        """
        Add song to queue or insert at top (插播).

        點歌額度滿了就丟 song_quota.QuotaExceeded —— 在解析 song_id、查快取、
        建 queue item **之前**先擋。擋在後面的話會先去 yt-dlp 抓一次 metadata，
        等於為了一首不會收的歌打一次網路（而且那一秒鐘伺服器正在放歌）。

        歡唱時間到了則丟 room_timer.RoomTimeUp。這一道擋在額度前面，因為
        「今晚結束了」蓋過「你排太多首」—— 兩個理由同時成立時，講後者
        會讓人以為刪掉一首就能再點（然後他刪了，再點，再被擋一次）。
        """
        # 時間到之後點的歌永遠不會播（除非續時），收下來只是讓佇列多一首
        # 沒有人會唱到的歌。插播（⚡）也一樣擋 —— 它繞得過額度那種「包廂內部的
        # 公平規則」，但繞不過「今晚已經結束」這件事實。
        if self.room_stops_playback():
            raise room_rules.RoomTimeUp(self.room_state())
        # 多人包廂：這首是誰點的。長度截 24 字，防手機端惡搞塞爆佇列版面。
        # 額度與輪序都認這個收斂過的值，不是原始字串。
        requested_by = str(requested_by or "").strip()[:24]
        verdict = song_quota.check(self.queue, requested_by, self.pending_limit,
                                   priority=priority)
        if not verdict["allowed"]:
            raise song_quota.QuotaExceeded(verdict)

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
            "requested_by": requested_by,
            # 插播的歌。輪唱要認得它才不會插到它前面（現場按下去的決定
            # 不該被機器的規則推翻），拖曳排序之後也還認得出來。
            "priority": bool(priority),
        }

        if priority:
            self.queue.insert(0, queue_item)
        elif self.rotation_enabled:
            # 公平輪唱：照「這是他的第幾首」找位置，而不是一律排到最後
            index, _round = rotation_rules.plan_insert_index(
                self.queue, self.rotation.counts(), queue_item["requested_by"])
            self.queue.insert(index, queue_item)
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
        # 歡唱時間到了。剛唱完的那一首就是今晚的最後一首 —— 停的是下一首，
        # 不是正在唱的那一首（見 backend/services/room_timer.py 決定一）。
        if self.room_stops_playback():
            await self._halt_for_room_time()
            return None

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
            # 第一首歌開始播＝這一場開始了。自動開錶是刻意的：要靠人記得按
            # 「開始計時」的話，最常見的結局是三小時後才有人想起來沒按 ——
            # 那時候這個功能等於沒開，而且已經補不回來了。
            self._autostart_room_session()
            # 上一首圈起來的練唱區間對這一首沒有意義，換人上台就歸零
            self.clear_loop()
            self.is_playing = True
            # 輪序也是「真的上台」才算一首 —— 排進佇列又被刪掉的不該佔掉他的輪次。
            # 被切歌的算（麥克風確實輪到他手上了），這一點跟點唱排行一致。
            self.rotation.record_play(next_item, gap_hours=self.session_gap_hours())
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

    def _autostart_room_session(self):
        """
        設定頁開了計時、也開了自動開錶，而且還沒有一場在跑 —— 那就從這一首開始算。

        按過「結束計時」之後就不再自動起錶（`_room_autostart_off`）。那顆鍵的
        意思是「這桌不要計時了」，如果下一首歌又把錶打開、倒數重新出現在舞台上，
        那顆鍵看起來就像沒有反應 —— 而使用者的下一個動作是再按一次。
        """
        policy = self.room_policy()
        if not policy["enabled"] or not policy["autostart"] or self._room_autostart_off:
            return
        if self.room.snapshot()["active"]:
            return
        self.room.start(policy["minutes"])

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

    def session_gap_hours(self) -> float:
        """
        相隔幾小時算換了一場。與整晚打包共用同一個設定欄位 ——
        系統裡「一場」只能有一個定義，兩個各自可調的話會出現
        「打包說這是同一場、輪序說換了一場」這種自己打自己臉的畫面。
        """
        if not self.settings:
            return rotation_rules.DEFAULT_SESSION_GAP_HOURS
        try:
            return float(self.settings.get("recording_session_gap_hours",
                                           rotation_rules.DEFAULT_SESSION_GAP_HOURS))
        except (TypeError, ValueError):
            return rotation_rules.DEFAULT_SESSION_GAP_HOURS

    def placement_of(self, queue_id: str) -> Dict[str, Any]:
        """
        剛點的那一首排在哪、是第幾輪。點歌台拿它講出「排在第 3 位（第 2 輪）」。

        刻意是「事後從現在的佇列讀出來」而不是點歌當下回報的固定值：
        兩支手機同時點歌時，先算好的位置在回應送出去之前就已經不對了。
        """
        rounds = rotation_rules.compute_rounds(self.queue, self.rotation.counts())
        for index, item in enumerate(self.queue):
            if item.get("queue_id") == queue_id:
                return {
                    "enabled": self.rotation_enabled,
                    "index": index,
                    "position": index + 1,
                    "round": rounds[index],
                    # 插到幾首歌前面。0 = 排在最後面（等於沒開輪唱的行為）
                    "ahead_of": len(self.queue) - 1 - index,
                    "queue_length": len(self.queue),
                }
        # 找不到＝已經上台了（快取歌會在 add_song 裡直接開播）
        return {"enabled": self.rotation_enabled, "index": None, "position": None,
                "round": None, "ahead_of": 0, "queue_length": len(self.queue)}

    def quota_of(self, requested_by: str) -> Dict[str, Any]:
        """
        這個人現在的額度狀況。點完歌之後問一次，好在同一句話裡講出「還可以再排幾首」。

        跟 placement_of 一樣是**事後**從當下的佇列讀出來的：兩支手機同時點歌時，
        點歌那一刻算好的剩餘量在回應送出去之前就已經不對了。
        """
        return song_quota.state(self.queue, requested_by, self.pending_limit)

    async def reset_rotation(self) -> Dict[str, Any]:
        """把今晚的輪序歸零（換一批客人、或是大家講好重新排）。"""
        self.rotation.reset()
        await self.broadcast_state()
        return rotation_rules.rotation_summary(
            self.queue, self.rotation.counts(), self.rotation.names())

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
        # 公平輪唱的開關是共享狀態：一支手機打開，包廂裡每一台都看得到規則變了
        # （只有「大家都知道」的排序規則才不會變成吵架的來源）。
        if "rotation_enabled" in params:
            self.rotation_enabled = bool(params["rotation_enabled"])
        # 每人待唱上限也是共享狀態：這條規則要「大家都知道」才不會變成吵架的來源，
        # 而且被擋下來的那支手機必須看得到現在的上限是多少。
        if "pending_limit" in params:
            self.pending_limit = song_quota.coerce_limit(params["pending_limit"])
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
