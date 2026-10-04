import asyncio
import functools
import io
import json
import shutil
import socket
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from urllib.parse import quote

from fastapi import (FastAPI, WebSocket, WebSocketDisconnect, Query, Body, Header,
                     HTTPException, Request)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import Response, FileResponse, StreamingResponse, JSONResponse
import qrcode

from backend.config import (FRONTEND_DIR, SONGS_DIR, CACHE_DIR, IMPORT_DIR, RECORDINGS_DIR,
                            PUBLIC_HOST, PUBLIC_PORT, DEVICE)
from backend.pipeline.chorus_detector import analyze_song_structure
from backend.pipeline.loudness import analyze_audio_file, gain_db_for_target
from backend.pipeline.song_processor import SongProcessor
from backend.services import backup
from backend.services.batch_scheduler import BatchScheduler, split_source_lines
from backend.services.storage import SongStorage
from backend.services.search_service import YouTubeSearchService
from backend.services.queue_manager import QueueManager
from backend.services.play_stats import PlayStats
from backend.services.favorites import Favorites
from backend.services.library import LANGUAGE_SPEC, NEW_SONG_DAYS, LibraryIndex
from backend.services.song_index import SongFinder
from backend.services.song_numbers import SongNumberBook, parse_number
from backend.services.lyric_offsets import (MAX_OFFSET_MS, MAX_RATE, MIN_RATE,
                                            LyricOffsets)
from backend.services.artist_index import ArtistFinder
from backend.services.local_import import LocalImportLibrary
from backend.services.song_history import SongHistory
from backend.services import (access_policy, contest, marquee, process_lane, room_timer,
                              rooms, service_calls, song_quota)
from backend.services.staff_lock import StaffLock
from backend.services.score_history import ScoreHistory
from backend.services.vocal_range import VocalRange
from backend.services.night_export import (DEFAULT_GAP_HOURS, ExportGate, filter_by_singer,
                                           find_session, group_sessions, iter_session_zip,
                                           zip_filename)
from backend.services.recordings import (HARD_MAX_UPLOAD_BYTES, RecordingLibrary,
                                         mp3_cache_limit, suffix_for_mime)
from backend.services.share_links import ShareLinkStore, clamp_max_downloads, clamp_ttl_hours
from backend.services.transcoder import (TranscodeGate, clamp_bitrate, probe_ffmpeg,
                                         reset_probe_cache, transcode_to_mp3)
from backend.services.settings import SETTINGS_SPEC, SystemSettings, default_settings
from backend.services.i18n import locale_info, negotiate_locale
from backend.version import __version__, version_info

# Logging setup
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("KaraTube.Server")

# 包廂計時的心跳。5 秒一次就夠：提醒的門檻是「剩 10 分鐘」這種尺度，
# 差五秒沒有人看得出來，而每秒醒來一次是拿 CPU 換一個沒有人要的精確度
# （畫面上那個每秒跳一次的倒數是前端自己跑的，不靠這個迴圈）。
ROOM_TICK_SECONDS = 5


async def stage_heartbeat_loop():
    """
    舞台的心跳：推進包廂計時、丟掉過期的舞台訊息，並在沒有人點歌時自己接一首。

    兩件事共用同一個迴圈，是因為它們要的是同一件事 —— 「時間自己過去了」
    這件事必須有人發現。提醒不能等到「下一次有人操作」才發現：包廂最安靜的
    時候正是快唱完的時候，而那正是最需要聽到「剩十分鐘」的時候。
    過期的訊息同理：沒有人會為了讓一則舊訊息消失而去按什麼。

    多包廂之後這一圈走的是**每一間**。一間出事不能拖垮其他間，所以
    try 包在每一間身上而不是整圈外面 —— 包在外面的話，202 包廂的服務鈴
    寫檔失敗會讓 101 的倒數整晚停在同一個數字，而沒有人看得出那兩件事有關。
    """
    while True:
        try:
            await asyncio.sleep(ROOM_TICK_SECONDS)
        except asyncio.CancelledError:
            raise
        for bundle in room_registry.items():
            try:
                await tick_one_room(bundle)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # 這個迴圈死掉的話倒數就永遠停在那裡，而且沒有人看得出來，
                # 所以任何一次失敗都只記錄、不中斷。
                logger.warning(f"舞台心跳失敗（{bundle.id}）: {e}")
        try:
            # 櫃檯管理鎖是整台機器一份的（機器層級的動作不分包廂），
            # 所以它的自動上鎖在迴圈外面走一次就好。不主動廣播的話，
            # 那顆鎖頭會在每支手機上一直亮著「已解鎖」，直到有人按下一個
            # 受保護的動作才發現自己早就被鎖在外面。
            if staff_lock.tick():
                await ws_manager.broadcast({"type": "STAFF_LOCK", "data": staff_lock.state()})
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"櫃檯管理鎖心跳失敗: {e}")


async def tick_one_room(bundle: "RoomBundle"):
    """一間包廂的一次心跳。廣播一律只送到這一間。"""
    for alert in await bundle.queue.tick_room():
        await ws_manager.broadcast({
            "type": "ROOM_ALERT",
            "data": {**alert, "room_id": bundle.id,
                     "room_name": room_name_of(bundle.id),
                     "room": bundle.queue.room_state()},
        }, room=bundle.id)
    # 舞台自己也會濾掉過期的（心跳五秒一次，而「十分鐘後消失」差五秒就
    # 不叫十分鐘了），這裡是為了讓點歌台的訊息清單跟舞台看到的同一份。
    if bundle.marquee.prune():
        await ws_manager.broadcast({"type": "MARQUEE_UPDATE",
                                    "data": marquee_state(bundle)}, room=bundle.id)
    # 服務鈴：開太久的那張單自己標成過期。掛在心跳上的理由跟過期訊息一樣
    # —— 一張沒有人理的單，正好是沒有人會去查的那一張，而畫面上一直寫著
    # 「等待中 47 分鐘」看起來像系統還在處理（見 service_calls.py 決定七）。
    if bundle.service.expire_stale():
        await ws_manager.broadcast({"type": "SERVICE_UPDATE",
                                    "data": service_state(bundle)}, room=bundle.id)
    # 沒有人點歌時，機器自己接一首。掛在同一個心跳上的理由跟計時一樣：
    # 「空了二十秒」這件事必須有人發現，而包廂最安靜的時候正是
    # 沒有人會去按任何按鈕的時候。
    await bundle.queue.tick_autofill()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """
    背景工作的生老病死。

    排程預處理與舞台心跳的迴圈都要有 event loop 才能建立，所以不能在模組層級
    start()；關機時要記得 cancel，否則 uvicorn --reload 每存一次檔就多一個迴圈在跑。
    """
    batch_scheduler.start()
    room_task = asyncio.create_task(stage_heartbeat_loop())
    try:
        yield
    finally:
        room_task.cancel()
        try:
            await room_task
        except asyncio.CancelledError:
            pass
        await batch_scheduler.stop()


app = FastAPI(title="KaraTube KTV Server", version=__version__, lifespan=lifespan)


# 櫃檯管理鎖的守門（見 backend/services/access_policy.py）。
#
# 刻意寫在 CORS 前面：Starlette 的中介層是**後加的包在外面**，所以 CORS 要晚一步
# 註冊才會把標頭補到這裡回出去的 403 上 —— 順序反過來的話，外掛的櫃檯看板
# 收到的會是一個沒有 CORS 標頭的錯誤（瀏覽器連狀態碼都不給它看）。
#
# 做成中介層而不是每條路由掛 Depends，是為了讓「哪些要鎖」只有一份清單：
# 掛在路由上的話，新加的端點忘記掛就是默默沒保護，而那件事沒有任何測試看得到。
@app.middleware("http")
async def staff_lock_guard(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and access_policy.requires_unlock(request.method, path):
        if not staff_lock.authorize(request.headers.get("X-Staff-Token", "")):
            info = access_policy.describe(request.method, path)
            return JSONResponse(status_code=403,
                                content={**info, "lock": staff_lock.state()})
    return await call_next(request)


# Enable CORS for local network and mobile devices
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 還原：在任何服務被建立之前 ---
#
# 這一行的位置是這個功能最重要的一件事，而不是一個實作細節。
# 下面每一個服務都在 `__init__` 就把自己那份 JSON **讀進記憶體**，之後只在
# 寫入時整份蓋回去 —— 所以「把檔案換掉」在服務建立**之後**做是錯的，而且是
# 無聲地錯：還原看起來成功（檔案真的換了），接著客人按一下收藏，Favorites
# 把記憶體裡那份舊的寫回去，整份還原就一個檔一個檔地消失。
# 所以還原不是即時動作，是「下次開機時做的事」（見 backend/services/backup.py）。
_restore_report = backup.apply_pending_restore(CACHE_DIR, app_version=__version__)
if _restore_report:
    logger.info("開機時套用了一份還原：%s（報告見 cache/%s）",
                _restore_report.get("status"), backup.REPORT_NAME)

# Initialize Services
# 櫃檯管理鎖。放在最前面是因為上面那道中介層每一個請求都會問它 ——
# 存在 cache/ 而不是 settings.json 裡：系統設定本身正是被它保護的東西
# （見 staff_lock.set_auto_lock_minutes）。
staff_lock = StaffLock(CACHE_DIR / "staff_lock.json")
# 設定要先讀起來 —— 模型選擇與響度目標在建構流水線時就要用到
settings = SystemSettings(CACHE_DIR / "settings.json")
storage = SongStorage(SONGS_DIR)
search_service = YouTubeSearchService()
# 本機曲庫匯入：`cache/import/` 裡的檔案 → 跟 YouTube 歌完全一樣的曲庫歌曲。
# 帳本放在 cache/ 而不是各首歌的資料夾裡（理由同歌號簿）：「重新處理」會
# rmtree 整個歌資料夾，而那樣就再也找不到原始檔案在哪了。
local_imports = LocalImportLibrary(IMPORT_DIR, CACHE_DIR / "local_imports.json",
                                   storage=storage)
song_processor = SongProcessor(
    whisper_model=settings.get("whisper_model"),
    demucs_model=settings.get("demucs_model"),
    loudness_target_lufs=settings.get("loudness_target_lufs", -14.0),
    local_library=local_imports,
)
play_stats = PlayStats(CACHE_DIR / "play_stats.json")
favorites = Favorites(CACHE_DIR / "favorites.json")
song_history = SongHistory(CACHE_DIR / "song_history.json")
score_history = ScoreHistory(CACHE_DIR / "score_history.json")
# 每個人的音域檔案。跟評分歷史同一邊（整台機器一份、跟著暱稱走），
# 不分包廂 —— 音域是這個人的聲音，不是這一間包廂的性質。
vocal_range = VocalRange(CACHE_DIR / "vocal_range.json")
recordings = RecordingLibrary(RECORDINGS_DIR)
# 分享連結的索引刻意放在 cache/ 而不是錄音資料夾裡：RecordingLibrary 會把
# 錄音資料夾裡「不在索引上」的檔案當成孤兒檔刪掉，放進去會在下次開機時消失。
share_links = ShareLinkStore(CACHE_DIR / "recording_shares.json")
# MP3 轉檔的兩道關卡：同一筆同時只轉一次、整台機器同時只轉一個。
# 「只轉一個」是刻意的 —— 那顆 CPU 正在放歌、算音準、跑下一首的人聲分離，
# 一桌人同時掃 QR 下載也不該讓舞台卡住。
mp3_gate = TranscodeGate(max_concurrent=1)
# 整晚打包（一個 zip 帶走一整場）同時只做一份：一包是幾百 MB，而那條網路
# 正是舞台端串影片與 WebSocket 在走的。第二個人等一下就好，舞台卡住不行。
night_gate = ExportGate()
# 歌號簿（六位數點歌）。存在 cache/ 而不是各首歌的資料夾裡是刻意的：
# 刪掉一首歌的資料夾不該把「那個號碼曾經是誰的」一起刪掉 ——
# 號碼絕不回收，正是這個功能唯一的價值來源（見 song_numbers.py）。
song_numbers = SongNumberBook(CACHE_DIR / "song_numbers.json")
# 每首歌的字幕偏移（人用耳朵校出來的那個數字）。放 cache/ 的理由跟歌號一樣：
# 「重新處理」會 rmtree 整個歌資料夾、快取自動清理也會無聲刪掉它，
# 而人花時間校出來的偏移不該被流水線洗掉（見 lyric_offsets.py）。
lyric_offsets = LyricOffsets(CACHE_DIR / "lyric_offsets.json")
# 重算歌詞的關卡：同一首同時只重算一次、整台機器同時只跑一個。
# 「只跑一個」跟 MP3 轉檔同一個理由 —— 那顆 CPU 正在放歌、算音準，
# 而重算可能會掉進 Whisper 轉錄（幾分鐘、吃滿核心）。
lyrics_gate = TranscodeGate(max_concurrent=1)
# 曲庫分類瀏覽（語言/歌手）、新歌榜與推薦歌單，全部從快取資料夾即算即回。
# 歌號掛在這一層，所以每一份曲庫清單（分類、查歌、歌星、自動接歌）都帶著號碼
# —— 要有人記得住號碼，前提是它到處都印得出來。
library = LibraryIndex(storage, play_stats=play_stats, song_history=song_history,
                       song_numbers=song_numbers)
# 曲庫查歌（注音首字／歌名字數）。索引建在 library 給的同一份清單上，
# 才不會出現「分類瀏覽看得到、查歌查不到」這種兩套清單對不起來的狀況。
song_finder = SongFinder(storage, library)
# 歌星查歌（注音首字查歌手）。接在 song_finder 後面而不是自己掃一次曲庫：
# 歌手清單與歌單用的都是同一份索引，歌名也跟著是萃出來的本體而非整串標題。
artist_finder = ArtistFinder(song_finder)

# WebSocket Connection Manager
#
# 多包廂之後每一條連線都屬於**一間**包廂（?room=）。分流的規則只有兩條：
#
#   1. 帶房號的訊息只送給那一間。送錯的代價是 A 包廂的螢幕跳出 B 包廂的歌詞，
#      那比不同步還糟 —— 不同步看得出來是壞的，唱到別人的歌看起來像自己按錯。
#   2. 不帶房號的訊息（設定、櫃檯管理鎖、曲庫與排程）送給全部：
#      那些東西整台機器只有一份，改了就是所有人都改了。
#
# 櫃檯那一頁另外掛 `desk=1`，它會**額外**收到每一間的服務鈴與計時提醒
# —— 但收不到 STATE_UPDATE / TIME_UPDATE / SCORE_EVENT。十間包廂每秒各推
# 一次播放時間，櫃檯那一頁會被自己的訊息淹掉，而它要的「誰在唱什麼」
# 一秒一次太頻繁了，那份資料在 /api/rooms/overview 裡。
DESK_FORWARD_TYPES = frozenset({"SERVICE_UPDATE", "ROOM_ALERT", "MARQUEE_UPDATE"})


class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        # 連線 → 房號。用 dict 而不是在 WebSocket 上掛屬性，是為了讓
        # disconnect 一定清得乾淨（漏掉的話那條死連線會一直收到廣播、
        # 一直丟例外，而廣播迴圈每一則訊息都要為它多繞一次）。
        self.rooms: Dict[WebSocket, str] = {}
        self.desks: List[WebSocket] = []

    async def connect(self, websocket: WebSocket, room_id: str, desk: bool = False):
        await websocket.accept()
        self.active_connections.append(websocket)
        self.rooms[websocket] = room_id
        if desk:
            self.desks.append(websocket)
        logger.info(f"WebSocket 連線加入包廂 {room_id}"
                    f"{'（櫃檯）' if desk else ''}，目前 {len(self.active_connections)} 條")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        self.rooms.pop(websocket, None)
        if websocket in self.desks:
            self.desks.remove(websocket)

    def room_counts(self) -> Dict[str, int]:
        """每一間現在有幾台裝置連著。櫃檯總覽用它答「那間開著沒有」。"""
        out: Dict[str, int] = {}
        for rid in self.rooms.values():
            out[rid] = out.get(rid, 0) + 1
        return out

    def _targets(self, message: Dict[str, Any], room: Optional[str]) -> List[WebSocket]:
        if room is None:
            return list(self.active_connections)
        targets = [ws for ws in self.active_connections if self.rooms.get(ws) == room]
        if str(message.get("type")) in DESK_FORWARD_TYPES:
            for ws in self.desks:
                if self.rooms.get(ws) != room:
                    targets.append(ws)
        return targets

    async def broadcast(self, message: Dict[str, Any], room: Optional[str] = None):
        """
        送出去。`room` 給了就只有那一間（外加櫃檯的那幾種）。

        房號會**寫進訊息本身**：斷線重連之後有可能連到別間去（換 Wi-Fi、
        手機從睡眠醒來、網址被人改過），而每一則訊息自己報房號的話，
        對不上的那一頁可以整頁不畫 —— 靜靜地畫錯是這個功能最糟的失敗。
        """
        if room is not None:
            message = {**message, "room": room}
        message_json = json.dumps(message)
        dead_connections = []
        for connection in self._targets(message, room):
            try:
                await connection.send_text(message_json)
            except Exception:
                dead_connections.append(connection)
        for dead in dead_connections:
            self.disconnect(dead)


ws_manager = ConnectionManager()

# 整台機器唯一的流水線車道（見 backend/services/process_lane.py）。
# 十間包廂同時點新歌時，這條車道是「每一間都等更久」與「輪流、一首一首來」
# 之間的差別。
pipeline_lane = process_lane.ProcessLane(max_concurrent=1)

# 包廂名冊（cache/rooms.json）。升級的機器讀不到這個檔，會自動長出一間
# `default` —— 而 default 的狀態檔就是原本那幾個（見 rooms.py 決定二），
# 所以升級之後看到的跟升級之前一模一樣。
room_roster = rooms.RoomRoster(CACHE_DIR / "rooms.json")


class RoomBundle:
    """
    一間包廂的那一組服務：佇列、計時、舞台訊息、服務鈴、今晚擂台。

    做成一個物件而不是五張 dict，是為了讓「拿到這間包廂」只有一個動作 ——
    五張表的話，新加的每一支端點都要記得去對五次房號，而漏掉的那一次
    會變成「切歌切到別間」。
    """

    __slots__ = ("id", "queue", "timer", "marquee", "service", "contest")

    def __init__(self, room_id: str, queue, timer, board, desk, arena):
        self.id = room_id
        self.queue = queue
        self.timer = timer
        self.marquee = board
        self.service = desk
        self.contest = arena


def _make_room_bundle(room_id: str) -> RoomBundle:
    """開一間包廂要準備的東西。RoomRegistry 在開機與新增房間時各叫一次。"""
    timer = room_timer.RoomTimer(rooms.state_path(CACHE_DIR, room_id, "room_timer.json"))
    # 舞台訊息刻意不落地：伺服器重開之後最可能的狀況是「那件事早就處理完了」，
    # 而一則沒有人記得的舊訊息自己跳到螢幕上，看起來就像機器壞了。
    # 服務鈴相反，它**要存檔** —— 一張還開著的單對應的是現實世界裡一件還沒做完
    # 的事，伺服器重開不會讓那杯冰塊自己送到（見 service_calls.py 決定七）。
    board = marquee.MarqueeBoard()
    desk = service_calls.ServiceDesk(
        rooms.state_path(CACHE_DIR, room_id, "service_calls.json"),
        stale_minutes=settings.service_call_policy()["stale_minutes"],
    )
    # 今晚擂台要存檔（contest.py 決定六）：伺服器在一場裡重開不會讓那三首歌
    # 沒有唱過，而掉了的話現場沒有任何辦法算回來。
    arena = contest.ContestBoard(
        rooms.state_path(CACHE_DIR, room_id, "contest.json"),
        gap_hours=float(settings.get("recording_session_gap_hours", DEFAULT_GAP_HOURS) or
                        DEFAULT_GAP_HOURS),
    )
    qm = QueueManager(song_processor, storage,
                      # 廣播綁死在這一間上：QueueManager 自己不認識房號分流，
                      # 它只知道「把狀態送出去」，而送到哪由這一層決定。
                      broadcast_cb=functools.partial(ws_manager.broadcast, room=room_id),
                      play_stats=play_stats, song_history=song_history,
                      settings=settings, room=timer,
                      library=library, favorites=favorites,
                      number_of=song_numbers.ensure,
                      lyric_calibration_of=lyric_offsets.calibration,
                      room_id=room_id, lane=pipeline_lane)
    # 開機就把設定頁的「預設調音參數」套進共享狀態，第一台連上來的裝置看到的就是設定值
    qm.apply_control_defaults()
    return RoomBundle(room_id, qm, timer, board, desk, arena)


room_registry = rooms.RoomRegistry(room_roster, _make_room_bundle)

# 預設包廂那一組服務的捷徑。給**不分房**的呼叫端用：測試、維護腳本，以及
# 那些在多包廂之前就寫死「這台機器只有一組佇列」的外部工具。
# 新的程式碼一律走 `bundle_for(room)` —— 這三個名字只保證「default 那一間」，
# 把它們當成「這台機器的佇列」用，就是 202 包廂被切歌的那個 bug。
default_room = room_registry.get(rooms.DEFAULT_ROOM_ID)
queue_manager = default_room.queue
marquee_board = default_room.marquee
service_desk = default_room.service


def bundle_for(room: Any) -> RoomBundle:
    """
    房號 → 那一間。認不得就 404（rooms.py 決定三：絕不靜靜退回 default）。

    每一支跟包廂有關的端點的第一行都是它，而不是各自去查 registry ——
    少查一次的那一支會變成「所有包廂共用一份佇列」，而那件事在單包廂的
    機器上完全看不出來，直到店裡裝了第二間。
    """
    found = room_registry.get(room)
    if found is None:
        raise HTTPException(status_code=404,
                            detail={"error": "no_such_room", "room": str(room or ""),
                                    "rooms": room_registry.rows()})
    return found


def song_in_use_anywhere(song_id: str) -> bool:
    """
    **任何一間**包廂正在唱這首、或佇列裡還排著它嗎？

    刪快取／重新處理／重算歌詞的守門一律問這一支而不是某一間的佇列 ——
    只問 default 的話，櫃檯在管理頁按下「重新處理」會把 202 包廂正在唱的
    那一首的檔案砍掉，而那一刻舞台上的人只看到畫面停住。
    """
    return any(bundle.queue.is_song_in_use(song_id) for bundle in room_registry.items())


async def broadcast_all_rooms():
    """整台機器的狀態變了（曲庫、字幕校正、預設調音），每一間都要重畫。"""
    for bundle in room_registry.items():
        await bundle.queue.broadcast_state()



def stage_is_busy() -> bool:
    """
    舞台正在忙嗎？排程預處理靠這個決定要不要讓開。

    「有人在唱歌」只是其中一種忙：佇列裡還有歌在跑流水線時也算 ——
    現場點的那首當然比半夜的批次任務優先，讓它獨佔 GPU 才會早點唱到。

    多包廂之後是**任何一間**在忙就算忙。只看 default 的話，半夜三點
    202 包廂那一桌點的歌會跟批次任務搶同一顆 CPU，而批次任務不知道
    自己該讓開 —— 那一桌等的是一首現在就要唱的歌。
    """
    for bundle in room_registry.items():
        qm = bundle.queue
        if qm.current_song is not None:
            return True
        if any(item.get("status") in ("PENDING", "PROCESSING") for item in qm.queue):
            return True
    return False


batch_scheduler = BatchScheduler(
    song_processor, storage, CACHE_DIR / "batch_jobs.json",
    settings=settings, broadcast_cb=ws_manager.broadcast, busy_cb=stage_is_busy,
    # 批次任務也要排進同一條車道，而且永遠排在所有包廂後面
    # （見 process_lane.py 決定三）：`busy_cb` 只擋得住「還沒開始的那一首」，
    # 已經跑到一半的 Demucs 不會因為有人點歌就停下來。
    lane=pipeline_lane,
)

# Helper: Get Local Network IP
def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def public_base_url() -> str:
    """
    手機要連進來用的網址。

    容器裡自動偵測到的是 bridge 網段的位址（172.17.x.x），手機連不進去；
    反向代理後面對外的 port 也不是容器監聽的那個。
    所以 `KARATUBE_PUBLIC_HOST` / `KARATUBE_PUBLIC_PORT` 一旦設了就以它們為準，
    沒設才退回「自動偵測本機 IP + 監聽 port」的原行為。
    """
    host = PUBLIC_HOST or get_local_ip()
    # 80 / 443 不寫進網址，QR code 掃出來才是乾淨的 http://karatube.local
    if PUBLIC_PORT in (80, 443):
        scheme = "https" if PUBLIC_PORT == 443 else "http"
        return f"{scheme}://{host}"
    return f"http://{host}:{PUBLIC_PORT}"

# --- REST Endpoints ---

@app.get("/api/version")
async def get_version():
    """執行中的版本。包廂那台機器跑的是哪一版，看這裡而不是猜。"""
    return version_info()


@app.get("/api/i18n")
async def get_i18n(request: Request):
    """
    介面語言：有哪些、舞台現在是哪一種、這個瀏覽器自己偏好哪一種。

    刻意**不**回傳翻譯字典 —— 字典跟著 `/js/i18n-catalog.js` 走瀏覽器快取，
    不佔每一次開頁的 API 往返，而且舞台離線的時候照樣有字可用。

    `suggested` 是從這個請求的 `Accept-Language` 算出來的：第一次開這一頁、
    裝置上還沒存過偏好的時候用它。一個日文系統的客人掃了 QR 進來應該直接
    看到日文，而不是先看到一頁中文、自己去找語言鍵 —— 找得到的前提是
    他看得懂那顆鍵上的字，而那正是他沒有的東西。
    """
    info = locale_info(settings.get("stage_locale"))
    info["suggested"] = negotiate_locale(request.headers.get("accept-language"))
    return info


@app.get("/api/health")
async def health_check():
    """容器健康檢查用。只回報「服務起得來」，不碰模型也不碰磁碟。"""
    return {"status": "ok", "version": __version__}


@app.get("/api/info")
async def get_server_info(room: str = Query(default=rooms.DEFAULT_ROOM_ID)):
    ip = PUBLIC_HOST or get_local_ip()
    base = public_base_url()
    # 房號要掛進手機與舞台的網址裡。預設那一間**不掛** ——
    # 只有一間包廂的店，網址上多一段 `?room=default` 只會讓人以為
    # 自己開錯頁面（而且舊的書籤與印好的 QR 都要重做）。
    suffix = "" if room == rooms.DEFAULT_ROOM_ID else f"?room={quote(room)}"
    return {
        "status": "online",
        "ip": ip,
        "port": PUBLIC_PORT,
        "device": DEVICE,
        "version": __version__,
        "room_id": room,
        "room_name": room_name_of(room),
        "web_url": f"{base}/{suffix}" if suffix else base,
        "player_url": f"{base}/player.html{suffix}"
    }

@app.get("/api/qrcode")
async def get_qrcode_image(room: str = Query(default=rooms.DEFAULT_ROOM_ID)):
    """
    手機點歌的 QR。帶 `?room=` 就是**那一間門口貼的那一張** ——
    掃進來的手機直接落在那一間的點歌台，不必再選一次房號
    （選錯房號是這個功能最糟的失敗，見 rooms.py 決定三）。
    """
    bundle_for(room)
    target_url = public_base_url()
    if room != rooms.DEFAULT_ROOM_ID:
        target_url = f"{target_url}/?room={quote(room)}"
    qr = qrcode.QRCode(
        version=1,
        box_size=10,
        border=2
    )
    qr.add_data(target_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")

@app.get("/api/search")
async def search_songs(q: str = Query(..., description="Search query or YouTube URL")):
    results = search_service.search(q)
    return {"query": q, "results": results}

@app.get("/api/cached-songs")
async def get_cached_songs():
    songs = storage.list_cached_songs()
    return {"songs": songs}

def _cache_overview() -> Dict[str, Any]:
    """
    快取管理總覽的實際工作（會掃整個 songs/ 目錄，所以是同步函式、丟到執行緒跑）。

    刻意只掃一次：`cache_stats()` 自己也會呼叫 `list_cache_entries()`，
    不把結果傳進去的話整個目錄會被 rglob 兩遍，而每一首歌現在還多讀一份
    alignment.json —— 曲庫幾百首時那是舞台 WebSocket 卡一下的量。

    每一筆再補上「這首歌的字幕偏移」：快取管理頁要能看出哪幾首被人工校正過
    （校正過的歌在重算歌詞之後最可能變錯，見 rebuild-lyrics 端點）。
    """
    entries = storage.list_cache_entries()
    calibrations = lyric_offsets.all_calibrations()
    for entry in entries:
        cal = calibrations.get(entry["song_id"]) or {}
        entry["lyric_offset_ms"] = int(cal.get("offset_ms", 0) or 0)
        entry["lyric_rate"] = float(cal.get("rate", 1.0) or 1.0)
    return {"songs": entries, **storage.cache_stats(entries),
            "tuned_count": sum(1 for e in entries
                               if e["lyric_offset_ms"] or e["lyric_rate"] != 1.0)}


@app.get("/api/cache")
async def get_cache_overview():
    """快取管理總覽：每首歌的磁碟用量、完整性、對齊品質與字幕偏移 + 磁碟剩餘空間。"""
    return await asyncio.to_thread(_cache_overview)


@app.delete("/api/cache/{song_id}")
async def delete_cached_song(song_id: str):
    """刪除一首快取歌曲（含壞資料夾）。演唱中或還在佇列裡的不能刪。"""
    if song_in_use_anywhere(song_id):
        raise HTTPException(status_code=409, detail="歌曲演唱中或在佇列裡，不能刪除快取")
    if not storage.delete_song(song_id):
        raise HTTPException(status_code=404, detail="快取中沒有這首歌")
    return {"status": "success", "song_id": song_id}


@app.post("/api/cache/{song_id}/reprocess")
async def reprocess_cached_song(song_id: str,
                                room: str = Query(default=rooms.DEFAULT_ROOM_ID)):
    """
    砍掉快取重新跑整條流水線：處理壞掉的歌（字幕全歪、檔案缺漏）用。

    重跑要有人等它跑完，所以它會被排進**一間**包廂的佇列（`?room=`，
    不給就是預設那一間）—— 櫃檯多半是在自己那一台管理頁按的，
    而跑完那首歌就在那一間的佇列裡等著被試唱。
    """
    if song_in_use_anywhere(song_id):
        raise HTTPException(status_code=409, detail="歌曲演唱中或在佇列裡，不能重新處理")
    song_dir = SONGS_DIR / song_id
    if not song_dir.exists():
        raise HTTPException(status_code=404, detail="快取中沒有這首歌")
    # 先留住舊 metadata 的顯示資訊，刪掉快取後排入佇列重新處理
    meta = storage.get_song_metadata(song_id) or {}
    storage.delete_song(song_id)
    item = await bundle_for(room).queue.add_song(
        song_id,
        title=meta.get("title", ""),
        artist=meta.get("artist", ""),
        thumbnail=meta.get("thumbnail", ""),
    )
    return {"status": "success", "item": item}


# --- 重算歌詞（便宜的那一條路）---
#
# 「字幕整首都歪」有兩種修法，成本差一個數量級：
#   * 重新處理：砍掉整個資料夾，重新下載 + Demucs + Whisper + 音準 + 響度，
#     有 GPU 也要幾分鐘，而且那首歌會從曲庫上消失再長回來。
#   * 重算歌詞：只用已經存在的 vocals.mp3 重跑一次對齊（抓 LRC → 仿射校正 →
#     起唱點吸附），不下載、不跑分離模型。走 LRC 的話十幾秒就好。
#
# 以前只有 CLI（rebuild_lyrics.py）做得到後者，所以畫面上唯一的選擇是那顆貴的。


def _rebuild_song_lyrics(song_id: str) -> Dict[str, Any]:
    """
    只重算一首歌的歌詞時間軸。**會阻塞**（網路抓 LRC、可能載入 Whisper），
    所以呼叫端一律走 `asyncio.to_thread`。

    重用 `song_processor.lyrics_aligner`（跟流水線同一個實例）是為了共用已經
    載入的模型；也因為如此，它跟正在跑的流水線會搶同一個 aligner ——
    `lyrics_gate` 的「整台機器同時只跑一個」擋的就是自己人打自己人。
    """
    song_dir = SONGS_DIR / song_id
    voc_file = song_dir / "vocals.mp3"
    lyrics_file = song_dir / "lyrics.json"
    if not voc_file.exists():
        return {"status": "missing_vocals",
                "message": "這首歌缺人聲軌，只能用「重新處理」重跑整條流水線"}

    meta = storage.get_song_metadata(song_id) or {}

    def work() -> Dict[str, Any]:
        before = storage.get_song_alignment(song_id)
        # 端點那一道 409 是**幾十秒前**問的：抓 LRC 要等網路、掉進 Whisper 更久，
        # 這段時間裡那首歌完全可能被點播並開始唱（排隊中的那一位剛好點到它）。
        # 就地覆寫 lyrics.json 會讓台上的人下一次載入時換成另一份詞，
        # 所以真正動手之前再問一次 —— 而且是在 align 跑完、**寫檔之前**還來不及問，
        # 因為寫檔在 align 裡面。這裡能做的是把窗口縮到最小：對齊前再驗一次，
        # 對齊後（寫檔已經發生）如果發現已經有人在唱，就明說一句讓畫面講得出來。
        if song_in_use_anywhere(song_id):
            return {"status": "in_use",
                    "message": "這首歌剛剛被點播了，重算已取消（重算會換掉正在唱的那份歌詞）"}
        # 本機匯入的歌：重算照樣讀檔案旁邊那一份 .lrc。不讀的話，按下重算會把
        # 使用者自備的歌詞換成線上抓的（或聽寫的）—— 那正是他放那個檔案要避免的事。
        # 檔案不在了（隨身碟拔掉）就回 None，自動退回一般流程。
        local_lrc = (local_imports.sidecar_lrc_text(song_id)
                     if meta.get("source") == "local" else None)
        try:
            lines = song_processor.lyrics_aligner.align(
                voc_file, meta.get("title", ""), meta.get("artist", ""), lyrics_file,
                local_lrc)
        except Exception as e:
            logger.exception(f"[{song_id}] 重算歌詞失敗: {e}")
            return {"status": "failed", "message": f"重算歌詞失敗：{e}"}
        return {"status": "ready", "lines": len(lines),
                "before": before, "after": storage.get_song_alignment(song_id),
                # 跑的過程中被點播了：歌詞已經換掉，正在唱的那一位手上是舊的那份。
                # 不是錯誤（新歌詞是好的），但要讓畫面說得出「這一首要下次才生效」。
                "started_singing": song_in_use_anywhere(song_id)}

    return lyrics_gate.run(song_id, work)


@app.post("/api/cache/{song_id}/rebuild-lyrics")
async def rebuild_song_lyrics(song_id: str):
    """
    只重算這首歌的歌詞時間軸（不重新下載、不跑人聲分離）。

    守門跟刪除／重新處理一致：演唱中或還在佇列裡就 409 —— 重算會就地覆寫
    `lyrics.json`，而那首歌可能正被唱著（更糟的是佇列裡那首的流水線稍後會
    再寫一次，把重算結果蓋掉）。

    **順手清掉這首歌的人工字幕偏移**：那個數字是為「舊的那份歌詞」調出來的，
    歌詞換了之後它最有可能變成錯的。回應裡會講清楚清掉了多少，
    前端的確認框也會先講一次（不靜默清，也不靜默留）。
    """
    if song_in_use_anywhere(song_id):
        raise HTTPException(status_code=409, detail="歌曲演唱中或在佇列裡，不能重算歌詞")
    if not (SONGS_DIR / song_id).exists():
        raise HTTPException(status_code=404, detail="快取中沒有這首歌")

    result = await asyncio.to_thread(_rebuild_song_lyrics, song_id)
    status = result.get("status")
    if status == "missing_vocals":
        raise HTTPException(status_code=409, detail=result["message"])
    if status == "busy":
        raise HTTPException(status_code=503,
                            detail="另一首歌正在重算歌詞，請稍後再試（同時只跑一首）")
    if status == "in_use":
        raise HTTPException(status_code=409, detail=result["message"])
    if status != "ready":
        # 重算不出來幾乎都是暫時性的（網路抓不到 LRC、記憶體不足），
        # 同一個網址等一下再按多半就成功，所以是 503 不是 500。
        raise HTTPException(status_code=503, detail=result.get("message", "重算歌詞失敗"))

    # 歌詞整份換掉了，人工校正的**兩個數字都失效**（速度尤其：新的時間軸是
    # 重新對出來的，舊的 1.024× 套上去只會把一份剛對好的歌詞再弄歪一次）。
    # 所以看的是「有沒有紀錄」，不是「偏移是不是 0」—— 只解過速度的歌偏移正好
    # 是 0，用 get() 判斷會讓那個 rate 留下來。
    cleared = lyric_offsets.calibration(song_id)
    cleared_offset = cleared["offset_ms"]
    cleared_rate = cleared["rate"]
    if cleared_offset or cleared_rate != 1.0:
        lyric_offsets.clear(song_id)
        # 清掉了就要讓每一台裝置知道 —— 不廣播的話，點歌台的滑桿與舞台的
        # songOffsetMs 還停在那個已經不存在的值上（而且下一次有人動滑桿時
        # 又會把它寫回去）。
        await broadcast_all_rooms()
    # 歌詞換了，段落曲式跟著換：點歌台的段落快取與舞台的歌詞都要重讀。
    await ws_manager.broadcast({
        "type": "LYRICS_REBUILT",
        "data": {"song_id": song_id, "alignment": result.get("after"),
                 "cleared_offset_ms": cleared_offset,
                 "cleared_rate": cleared_rate},
    })
    return {"status": "success", "song_id": song_id, "lines": result.get("lines", 0),
            "before": result.get("before"), "alignment": result.get("after"),
            "cleared_offset_ms": cleared_offset, "cleared_rate": cleared_rate,
            "started_singing": bool(result.get("started_singing"))}


# --- 本機曲庫匯入 ---
#
# 曲庫的第二個入口：把 `cache/import/` 裡的影音檔跑成曲庫歌曲。
# 匯入**不自己跑流水線**，而是建一筆排程任務丟給 batch_scheduler ——
# 理由是那兩件事要爭的是同一顆 CPU：一顆隨身碟可能有兩百首歌，而包廂裡
# 隨時會有人推門進來點第一首。排程器已經會「處理完一首就重新問一次
# 現在可不可以跑」，把匯入接上去就自動繼承了那條讓路規則。


@app.get("/api/import")
async def get_local_imports():
    """掃描匯入資料夾：有哪些檔案、哪些已經在曲庫裡、哪些上次失敗了。"""
    return await asyncio.to_thread(local_imports.scan)


@app.post("/api/import")
async def create_import_job(payload: Dict[str, Any] = Body(...)):
    """
    把勾選的本機檔案排進處理佇列。

    `items` 是 `[{path, title?, artist?}]`：`path` 相對於匯入資料夾，
    歌名與歌手可以在畫面上改過再送（那是**唯一一次便宜的機會** ——
    處理完之後歌號、注音索引、歌星併名全部建在那串字上）。
    """
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=400, detail="請先勾選要匯入的檔案")

    prepared = await asyncio.to_thread(local_imports.prepare, items)
    sources = prepared["sources"]
    if not sources:
        detail = "這些檔案都讀不到（被移走了？）" if prepared["failed"] else "沒有可匯入的檔案"
        raise HTTPException(status_code=400, detail=detail)

    job = batch_scheduler.create_job(
        sources,
        name=payload.get("name") or f"本機匯入 {len(sources)} 首",
        start_now=bool(payload.get("start_now", True)),
        requested_by=payload.get("requested_by", ""),
    )
    await ws_manager.broadcast({"type": "BATCH_UPDATE", "data": batch_scheduler.state()})
    return {"status": "success", "job": job, "failed": prepared["failed"],
            "accepted": len(sources), "state": batch_scheduler.state()}


# --- 排程預處理 ---


@app.get("/api/batch")
async def get_batch_state():
    """排程預處理總覽：時段、現在能不能跑（附理由）、每筆任務的進度。"""
    return batch_scheduler.state()


@app.post("/api/batch")
async def create_batch_job(payload: Dict[str, Any] = Body(...)):
    """
    建立一批排程預處理任務。

    `sources` 收使用者原封不動貼進來的文字：播放清單網址、單曲網址、關鍵字
    混在一起都可以，一行一個（逗號分隔也吃）。展開要連 YouTube，
    所以丟到 executor 去做，不擋住 event loop 上其他人的點歌。
    """
    text = payload.get("sources") or payload.get("text") or ""
    lines = split_source_lines(text)
    if not lines:
        raise HTTPException(status_code=400, detail="請貼上播放清單網址、歌曲網址或關鍵字")

    loop = asyncio.get_event_loop()
    expanded = await loop.run_in_executor(None, search_service.expand_sources, lines)
    songs = expanded.get("songs", [])
    if not songs:
        raise HTTPException(status_code=404, detail="這些來源都找不到歌曲，請確認網址或關鍵字")

    job = batch_scheduler.create_job(
        songs,
        name=payload.get("name", ""),
        start_now=bool(payload.get("start_now")),
        requested_by=payload.get("requested_by", ""),
    )
    await ws_manager.broadcast({"type": "BATCH_UPDATE", "data": batch_scheduler.state()})
    return {"status": "success", "job": job, "failed": expanded.get("failed", []),
            "state": batch_scheduler.state()}


@app.post("/api/batch/force")
async def set_batch_force(payload: Dict[str, Any] = Body(...)):
    """立即開始 / 回到照表操課。「現在就跑」是最常按的按鈕，值得一支獨立端點。"""
    force = batch_scheduler.set_force_run(bool(payload.get("force", True)))
    await ws_manager.broadcast({"type": "BATCH_UPDATE", "data": batch_scheduler.state()})
    return {"status": "success", "force_run": force, "state": batch_scheduler.state()}


@app.post("/api/batch/{job_id}/retry")
async def retry_batch_job(job_id: str):
    """把這批裡失敗的歌重新排隊。"""
    job = batch_scheduler.retry_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="找不到這筆排程任務")
    await ws_manager.broadcast({"type": "BATCH_UPDATE", "data": batch_scheduler.state()})
    return {"status": "success", "job": job}


@app.post("/api/batch/{job_id}/cancel")
async def cancel_batch_job(job_id: str):
    """取消還沒跑的部分。正在處理的那一首讓它跑完，中途砍掉只會留下半成品。"""
    job = batch_scheduler.cancel_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="找不到這筆排程任務")
    await ws_manager.broadcast({"type": "BATCH_UPDATE", "data": batch_scheduler.state()})
    return {"status": "success", "job": job}


@app.delete("/api/batch/{job_id}")
async def delete_batch_job(job_id: str):
    """刪掉任務紀錄。已經處理好的歌留在曲庫裡，不受影響。"""
    if not batch_scheduler.delete_job(job_id):
        raise HTTPException(status_code=404, detail="找不到這筆排程任務")
    await ws_manager.broadcast({"type": "BATCH_UPDATE", "data": batch_scheduler.state()})
    return {"status": "success"}


@app.delete("/api/batch")
async def clear_finished_batch_jobs():
    """清掉跑完／取消的紀錄，還有待處理項目的任務不動。"""
    removed = batch_scheduler.clear_finished()
    await ws_manager.broadcast({"type": "BATCH_UPDATE", "data": batch_scheduler.state()})
    return {"status": "success", "removed": removed}


@app.get("/api/library")
async def get_library_facets():
    """分類瀏覽的分類軸：每個語言別幾首、每位歌手幾首。"""
    return {**library.facets(), "language_spec": LANGUAGE_SPEC}


@app.get("/api/library/songs")
async def browse_library(
    language: str = Query("", description="語言別代碼，空值或 all 代表不篩選"),
    artist: str = Query("", description="歌手名，空值或 all 代表不篩選"),
    sort: str = Query("recent", pattern="^(recent|plays|title|artist)$"),
    limit: int = Query(120, ge=1, le=500),
):
    """依語言 / 歌手瀏覽曲庫。分類是從歌名、頻道名與歌詞判定並快取在 metadata。"""
    songs = library.browse(language=language, artist=artist, sort=sort, limit=limit)
    return {"songs": songs, "count": len(songs),
            "language": language, "artist": artist, "sort": sort}


@app.get("/api/library/find/keys")
async def get_find_keys():
    """查歌鍵盤要的資料：注音鍵位、歌名字數桶、曲庫有幾首。"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, song_finder.facets)


@app.get("/api/library/find")
async def find_in_library(
    q: str = Query("", description="注音首碼、英文首字母或歌名片段"),
    chars: int = Query(0, ge=0, le=30, description="歌名字數，0 代表不篩選"),
    limit: int = Query(60, ge=1, le=300),
):
    """
    只在已備好的曲庫裡查歌（查到的每一首都是快取秒播）。

    第一次查會替沒有索引的歌算注音首碼並寫回 metadata，所以整包丟到 executor
    去做 —— 曲庫幾百首時那是幾百次讀檔，不能佔住 event loop 讓別人點不了歌。
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: song_finder.search(query=q, chars=chars or None, limit=limit))


def _number_keypad(prefix: str = "", limit: int = 40) -> Dict[str, Any]:
    """歌號鍵盤的一次完整狀態（同步版；呼叫端丟到 executor 去跑）。

    候選只列現在唱得到的歌：列一首點不下去的歌，使用者按下去會以為系統壞了。
    已下架的號碼要等他打滿整組、明確地問「這一首去哪了」，才由查號那支回答。
    """
    # 用 song_finder 而不是 library 拿清單：歌卡上要印的是**歌名本體**
    # （「稻香」），不是整串 YouTube 標題 —— 跟注音查歌、歌星查歌同一份索引，
    # 三條查歌路列出來的同一首歌才會長得一樣。
    entries = song_finder.entries()
    by_id = {e["song_id"]: e for e in entries}
    picked = song_numbers.candidates(prefix, ready_ids=by_id.keys(), limit=limit)
    songs = [by_id[sid] for sid in picked["song_ids"] if sid in by_id]
    return {
        "prefix": picked["prefix"],
        "songs": songs,
        "total": picked["total"],
        "next_digits": picked["next_digits"],
        "library_total": len(entries),
        "book": song_numbers.stats(by_id.keys()),
    }


def _number_lookup(raw: str) -> Dict[str, Any]:
    """打滿一組號碼之後的答案（同步版；呼叫端丟到 executor 去跑）。

    四種結果分得很開，因為使用者的下一步完全不同：
    `ready` 點下去就唱、`gone` 這首已經不在曲庫（去搜尋框重新下載一份就會回到
    同一個號碼）、`unknown` 號碼打錯了（去查別的號碼）、`unavailable` 是機器
    的問題（號碼簿讀不出來，叫店員來）。全部揉成「查無此歌號」的話，
    第二種會被當成第三種 —— 使用者於是反覆確認自己沒記錯，而號碼一直是對的。
    """
    book = song_numbers.stats()
    if not book.get("available", True):
        return {"input": str(raw or ""), "status": "unavailable",
                "number": None, "song": None, "book": book}
    number = parse_number(raw)
    if number is None:
        return {"input": str(raw or ""), "status": "invalid",
                "number": None, "song": None, "book": book}
    record = song_numbers.lookup(number)
    if record is None:
        return {"input": str(raw or ""), "status": "unknown",
                "number": number, "song": None, "book": book}
    entries = song_finder.entries()
    song = next((e for e in entries if e["song_id"] == record["song_id"]), None)
    return {
        "input": str(raw or ""),
        "status": "ready" if song else "gone",
        "number": number,
        "song": song,
        # 下架的那一首也要講得出「原本是哪一首」—— 使用者要的是這個答案，
        # 不是「查無此歌號」。號碼簿留著墓碑就是為了這一句話。
        "record": record,
        "book": book,
    }


@app.get("/api/library/numbers")
async def get_song_numbers(
    prefix: str = Query("", description="已經按下去的那幾碼（只取數字）"),
    limit: int = Query(40, ge=1, le=200),
):
    """歌號鍵盤：目前這幾碼的候選歌曲，以及「下一個數字鍵按哪些還有歌」。"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _number_keypad(prefix, limit))


@app.get("/api/library/number/{number}")
async def get_song_by_number(number: str):
    """查一組歌號。沒發過、已下架、號碼簿壞掉三種情況分開回答（見 _number_lookup）。"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _number_lookup(number))


@app.get("/api/library/songbook")
async def get_songbook(limit: int = Query(500, ge=1, le=5000)):
    """整本號碼簿，照號碼排 —— 包廂桌上那本紙歌本的資料來源。

    含已經不在曲庫裡的號碼（標 in_library=false）：紙本歌本印出去之後改不了，
    而那一頁上的號碼永遠不會變成別首歌，所以印著也不會騙人。
    """
    loop = asyncio.get_event_loop()

    def build():
        ready = {e["song_id"] for e in library.entries()}
        rows = song_numbers.records()[:limit]
        for row in rows:
            row["in_library"] = row["song_id"] in ready
        return {"songs": rows, "count": len(rows),
                "book": song_numbers.stats(ready)}

    return await loop.run_in_executor(None, build)


@app.get("/api/library/artists/keys")
async def get_artist_find_keys():
    """歌星鍵盤要的資料：注音鍵位、曲庫有幾位歌手、幾首認不出歌手。"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, artist_finder.facets)


@app.get("/api/library/artists/find")
async def find_artists(
    q: str = Query("", description="歌手名的注音首碼、英文首字母，或直接打名字"),
    artist: str = Query("", description="選定的歌手（歌星清單給的 id）"),
    limit: int = Query(60, ge=1, le=300),
):
    """
    歌星查歌：查到歌手，同一包就把他的歌單帶回來。

    同一位歌手在曲庫裡的多種寫法（周杰倫 / Jay Chou / 周杰倫 Jay Chou）已經併成
    一位，所以歌單是完整的那一份。跟歌名查歌一樣丟到 executor：第一次查要替整個
    曲庫建索引，那是幾百次讀檔，不能佔住 event loop 讓別人點不了歌。
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: artist_finder.search(query=q, artist_id=artist, limit=limit))


@app.get("/api/library/new")
async def get_new_songs(limit: int = Query(24, ge=1, le=100)):
    """新歌榜：最近加入曲庫的歌。"""
    songs = library.new_songs(limit)
    return {"songs": songs, "count": len(songs), "new_days": NEW_SONG_DAYS}


@app.get("/api/library/recommend")
async def get_recommendations(limit: int = Query(12, ge=1, le=50)):
    """推薦歌單：依點唱紀錄推薦下一首，每首都附推薦理由。"""
    songs = library.recommendations(limit)
    return {"songs": songs, "count": len(songs)}


# --- 包廂（多包廂：一台伺服器帶多組舞台與佇列）---
#
# 下面每一支端點都吃 `?room=`，不給就是 `default` —— 只有一間包廂的店
# 一個字都不用改，這個功能對他們等於不存在（見 rooms.py 決定二）。


def room_name_of(room_id: str) -> str:
    row = room_roster.get(room_id)
    return row["name"] if row else str(room_id)


def room_overview_row(bundle: "RoomBundle", counts: Dict[str, int]) -> Dict[str, Any]:
    """
    櫃檯總覽裡的一列：這間現在在唱什麼、排了幾首、剩多久、有沒有人在叫櫃檯。

    刻意**不**包含整份佇列與整份訊息清單：十間包廂各一份佇列會讓這支端點
    回好幾百 KB，而櫃檯要的是「哪一間需要我過去」。要看細節就點進那一間
    （那時候才拿 /api/queue?room=）。
    """
    qm = bundle.queue
    current = qm.current_song or {}
    open_call = bundle.service.snapshot().get("call")
    return {
        "id": bundle.id,
        "name": room_name_of(bundle.id),
        "devices": counts.get(bundle.id, 0),
        "is_playing": bool(qm.is_playing and qm.current_song),
        "now_playing": ({"song_id": current.get("song_id"), "title": current.get("title"),
                         "artist": current.get("artist"),
                         "requested_by": current.get("requested_by", "")}
                        if qm.current_song else None),
        "queue_length": len(qm.queue),
        "processing": sum(1 for i in qm.queue if i.get("status") in ("PENDING", "PROCESSING")),
        "room": qm.room_state(),
        "service_open": open_call,
        "marquee_count": len(bundle.marquee.snapshot().get("messages", [])),
        # 今晚擂台只給一句話（誰領先），整張榜要點進那一間才拿 ——
        # 十間包廂各一張榜會讓這支端點回好幾十 KB，而櫃檯要的是
        # 「哪一間需要我過去」，不是「202 的第四名是誰」。
        "contest_leader": bundle.contest.leader(),
    }


@app.get("/api/rooms")
async def list_rooms():
    """
    這台機器有哪幾間包廂。點歌台開機時問一次，才畫得出切換清單。
    """
    counts = ws_manager.room_counts()
    return {
        "rooms": [{**row, "devices": counts.get(row["id"], 0)} for row in room_registry.rows()],
        "default": rooms.DEFAULT_ROOM_ID,
        "limit": rooms.MAX_ROOMS,
    }


@app.get("/api/rooms/overview")
async def rooms_overview():
    """
    櫃檯總覽：每一間現在的樣子。

    做成一支 REST 端點（而不是靠 WebSocket 推）是刻意的：櫃檯那一頁
    要的是「現在全店長怎樣」，而那份資料每秒變十次 —— 推的話那一頁會被
    自己的訊息淹掉（見 DESK_FORWARD_TYPES 的說明）。需要即時的那兩件
    （服務鈴、計時提醒）才用推的。
    """
    counts = ws_manager.room_counts()
    return {
        "rooms": [room_overview_row(b, counts) for b in room_registry.items()],
        "lane": pipeline_lane.snapshot(),
    }


@app.post("/api/rooms")
async def create_room(payload: Dict[str, Any] = Body(default={})):
    """
    開一間包廂。`id` 不給就從名字生一個（「101 包廂」→ `101`）。

    房號重複一律拒絕而不是自動改名：櫃檯打的那個房號是他要貼在門口的
    那一個（見 rooms.py `add`）。
    """
    data = payload or {}
    try:
        row = room_registry.create(data.get("name", ""), data.get("id"))
    except rooms.RoomError as exc:
        raise HTTPException(status_code=409,
                            detail={"error": exc.reason, **exc.detail}) from exc
    await ws_manager.broadcast({"type": "ROOMS_UPDATE", "data": room_registry.rows()})
    return {"status": "success", "room": row, "rooms": room_registry.rows()}


@app.post("/api/rooms/{room_id}/rename")
async def rename_room(room_id: str, payload: Dict[str, Any] = Body(default={})):
    """改名（「101」→「VIP 大包」）。房號不會跟著變 —— 門口那張 QR 不必重印。"""
    bundle_for(room_id)
    try:
        row = room_registry.rename(room_id, (payload or {}).get("name", ""))
    except rooms.RoomError as exc:
        raise HTTPException(status_code=409,
                            detail={"error": exc.reason, **exc.detail}) from exc
    await ws_manager.broadcast({"type": "ROOMS_UPDATE", "data": room_registry.rows()})
    return {"status": "success", "room": row, "rooms": room_registry.rows()}


@app.delete("/api/rooms/{room_id}")
async def delete_room(room_id: str, force: bool = Query(default=False)):
    """
    關掉一間。

    **裡面還有人在唱歌就先擋下來**（409，要 `force=true` 才過）。
    刪掉正在播的那一間，舞台那台電視會在唱到一半的時候整頁失效，
    而按下去的人多半只是想清掉一間「今天沒開」的空房 —— 那個錯一秒鐘
    就發生了，卻要等到包廂裡的人跑出來問才有人知道。
    """
    bundle = bundle_for(room_id)
    qm = bundle.queue
    if not force and (qm.current_song is not None or qm.queue):
        raise HTTPException(status_code=409, detail={
            "error": "room_in_use", "id": bundle.id, "name": room_name_of(bundle.id),
            "queue_length": len(qm.queue),
            "now_playing": (qm.current_song or {}).get("title", ""),
        })
    try:
        row = room_registry.remove(room_id)
    except rooms.RoomError as exc:
        raise HTTPException(status_code=409,
                            detail={"error": exc.reason, **exc.detail}) from exc
    # 這一間自己的狀態檔（計時、服務鈴）跟著走。留著的話，半年後有人用同一個
    # 房號再開一間，那間會**繼承一場沒有人記得的計時** —— 開機第一分鐘就跳出
    # 「歡唱時間已結束」，而櫃檯找不到任何理由。
    # 只砍 `cache/rooms/<id>/` 這一層（default 沒有這個資料夾，所以砍不到
    # 原本那幾個檔案），曲庫、排行、錄音一個都不碰 —— 那些是整台機器的東西。
    folder = rooms.room_dir(CACHE_DIR, row["id"])
    if folder is not None and folder.is_dir():
        try:
            shutil.rmtree(folder)
        except Exception as e:
            logger.warning(f"刪除包廂資料夾失敗 {folder}: {e}")
    await ws_manager.broadcast({"type": "ROOMS_UPDATE", "data": room_registry.rows()})
    # 那一間裡面還連著的裝置要知道自己的房間沒了，否則它會一直對著一個
    # 404 的房號重試，畫面上什麼都不會說。
    await ws_manager.broadcast({"type": "ROOM_CLOSED", "data": row}, room=row["id"])
    return {"status": "success", "room": row, "rooms": room_registry.rows()}


@app.get("/api/queue")
async def get_queue(room: str = Query(default=rooms.DEFAULT_ROOM_ID)):
    return bundle_for(room).queue.get_full_state()

@app.post("/api/queue/add")
async def add_to_queue(payload: Dict[str, Any] = Body(...),
                       room: str = Query(default=rooms.DEFAULT_ROOM_ID)):
    queue_manager = bundle_for(room).queue
    url_or_id = payload.get("url") or payload.get("id")
    if not url_or_id:
        raise HTTPException(status_code=400, detail="Missing url or id parameter")

    title = payload.get("title", "")
    artist = payload.get("artist", "")
    thumbnail = payload.get("thumbnail", "")
    priority = payload.get("priority", False)
    requested_by = payload.get("requested_by", "")

    try:
        item = await queue_manager.add_song(url_or_id, title, artist, thumbnail, priority,
                                            requested_by=requested_by)
    except room_timer.RoomTimeUp as exc:
        # 同樣是 409（不是 403）：這一首沒被收下的理由是「現在的狀態不收」，
        # 而不是「你沒有權限」。時間到跟額度滿在畫面上是同一種語氣 ——
        # 說明規則，並且講出下一步（續時就接著唱）。
        raise HTTPException(status_code=409, detail={"error": "room_time_up",
                                                     "room": exc.snapshot}) from exc
    except song_quota.QuotaExceeded as exc:
        # 409 而不是 429：擋下來的理由不是「按太快」，是「佇列現在的狀態不收這一首」。
        # 整份結論原封不動送出去，畫面才講得出「誰、現在幾首、什麼時候可以再點」——
        # 少講最後一件，使用者的下一個動作就是再按一次。
        raise HTTPException(status_code=409, detail={"error": "pending_limit_reached",
                                                    "quota": exc.verdict}) from exc
    # 公平輪唱開著時，點歌的人要知道自己被排到哪（「排在第 3 位，你的第 2 輪」）——
    # 不講的話使用者看到的是「我點的歌沒有出現在最後面」，那看起來像壞掉。
    # 計時開著時把剩餘時間一起帶回去：剩 4 分鐘還點了一首 5 分鐘的歌，
    # 畫面才講得出「這首可能唱不完」—— 那句話講在點歌的當下有用，
    # 講在歌被停下來的那一刻就只是事後諸葛。
    return {"status": "success", "item": item,
            "placement": queue_manager.placement_of(item["queue_id"]),
            "quota": queue_manager.quota_of(item["requested_by"]),
            "room_id": room,
            "room": queue_manager.room_state()}

@app.delete("/api/queue/{queue_id}")
async def remove_queue_item(queue_id: str, room: str = Query(default=rooms.DEFAULT_ROOM_ID)):
    await bundle_for(room).queue.remove_from_queue(queue_id)
    return {"status": "success"}

@app.post("/api/queue/{queue_id}/retry")
async def retry_queue_item(queue_id: str, room: str = Query(default=rooms.DEFAULT_ROOM_ID)):
    """重新處理佇列裡狀態為 ERROR 的歌。"""
    item = await bundle_for(room).queue.retry_item(queue_id)
    if item is None:
        raise HTTPException(status_code=404, detail="佇列裡沒有這首失敗的歌")
    return {"status": "success", "item": item}

@app.post("/api/queue/reorder")
async def reorder_queue(payload: Dict[str, int] = Body(...),
                        room: str = Query(default=rooms.DEFAULT_ROOM_ID)):
    from_idx = payload.get("from_idx", 0)
    to_idx = payload.get("to_idx", 0)
    await bundle_for(room).queue.reorder_queue(from_idx, to_idx)
    return {"status": "success"}

@app.get("/api/rotation")
async def get_rotation(room: str = Query(default=rooms.DEFAULT_ROOM_ID)):
    """目前的輪序：每一首的輪次、每個人唱了幾首／還有幾首。"""
    state = bundle_for(room).queue.get_full_state()
    return {"enabled": state["rotation_enabled"], **state["rotation"]}

@app.get("/api/quota")
async def get_quota(room: str = Query(default=rooms.DEFAULT_ROOM_ID)):
    """
    目前的點歌額度：上限是多少、誰排了幾首、誰滿了。

    跟 /api/rotation 分開，因為它們是獨立的兩條規則：輪唱管**順序**
    （一個人連點五首時其他人不必等完那五首），額度管**量**
    （那五首本來就不該同時排在佇列裡）。先到先唱的包廂也可能只想要後面那一條。
    """
    return bundle_for(room).queue.get_full_state()["quota"]


@app.get("/api/autofill")
async def get_autofill(room: str = Query(default=rooms.DEFAULT_ROOM_ID)):
    """
    自動接歌：開了沒有、照什麼挑、空多久才接、連著接了幾首、剛才那首為什麼被挑中。

    每一次 STATE_UPDATE 也帶著同一份資料（`state["autofill"]`），這支端點是給
    不想開 WebSocket 的呼叫端用的。
    """
    return bundle_for(room).queue.autofill_state()


@app.get("/api/autofill/preview")
async def preview_autofill(room: str = Query(default=rooms.DEFAULT_ROOM_ID)):
    """
    「現在接的話會接哪一首」。設定頁按下去可以先看一眼，不必真的等它接。

    只挑不播：挑歌本身沒有副作用（不記進「最近接過」，也不碰佇列），
    所以按幾次都不會影響等一下真正接歌的結果。
    """
    plan = await bundle_for(room).queue.plan_autofill()
    if plan is None:
        raise HTTPException(status_code=404, detail="曲庫裡還沒有可以接的歌")
    return {"song": plan["song"], "reason": plan["reason"], "source": plan["source"],
            "source_fallback": plan["source_fallback"], "relaxed": plan["relaxed"],
            "pool": plan["pool"]}


@app.post("/api/autofill/random")
async def random_pick_song(payload: Dict[str, Any] = Body(default={}),
                           room: str = Query(default=rooms.DEFAULT_ROOM_ID)):
    """
    🎲 來一首：從已經備好的曲庫隨機點一首。

    「想唱歌但想不到要唱什麼」是包廂裡最常見的一種卡住，而它跟搜尋是兩件事 ——
    搜尋要先想得出關鍵字。這顆鍵用的是自動接歌那一副挑歌規則（設定頁說
    「只挑我的最愛」時它也照辦），但挑出來的歌**算人點的**：計入排行與歷史、
    佔輪序與額度，跟手動點一首完全一樣 —— 有人按了這顆鍵，就是有人做了決定。
    """
    queue_manager = bundle_for(room).queue
    requested_by = (payload or {}).get("requested_by", "")
    try:
        result = await queue_manager.random_pick(requested_by=requested_by)
    except room_timer.RoomTimeUp as exc:
        raise HTTPException(status_code=409, detail={"error": "room_time_up",
                                                     "room": exc.snapshot}) from exc
    except song_quota.QuotaExceeded as exc:
        raise HTTPException(status_code=409, detail={"error": "pending_limit_reached",
                                                     "quota": exc.verdict}) from exc
    if result is None:
        # 404 而不是 500：曲庫還是空的不是錯誤，是「這台機器還沒有歌可以挑」，
        # 而畫面要講的下一步是「先去搜尋點一首，處理好就會留在曲庫裡」。
        raise HTTPException(status_code=404, detail="曲庫裡還沒有備好的歌可以挑")
    item = result["item"]
    return {"status": "success", "item": item, "reason": result["reason"],
            "placement": queue_manager.placement_of(item["queue_id"]),
            "quota": queue_manager.quota_of(item["requested_by"])}


@app.post("/api/rotation/reset")
async def reset_rotation(room: str = Query(default=rooms.DEFAULT_ROOM_ID)):
    """
    輪序歸零。換一批客人（但機器沒關）、或是大家講好重新排時按的。

    刻意不動佇列：已經排好的順序是大家看著排出來的，歸零的是「誰已經唱過幾首」
    這份統計，下一首新點的歌才照新的輪次排。
    """
    queue_manager = bundle_for(room).queue
    summary = await queue_manager.reset_rotation()
    return {"status": "success", "enabled": queue_manager.rotation_enabled, **summary}

@app.get("/api/room")
async def get_room_timer(room: str = Query(default=rooms.DEFAULT_ROOM_ID)):
    """
    包廂計時：這一場買了多久、用掉多久、還剩多久、時間到會怎麼處理。

    每一次 STATE_UPDATE 也帶著同一份資料（`state["room"]`），這支端點是給
    不想開 WebSocket 的呼叫端（外掛的櫃檯看板、腳本）用的。
    """
    return bundle_for(room).queue.room_state()


@app.post("/api/room/start")
async def start_room_timer(payload: Dict[str, Any] = Body(default={}),
                           room: str = Query(default=rooms.DEFAULT_ROOM_ID)):
    """
    開始計時（歸零重算）。`minutes` 不給就用設定頁的預設長度。

    已經在計時的時候按它是**重開一場**，不是續時 —— 續時請用 /api/room/extend。
    兩件事做成同一顆鍵的話，中途按錯就會把已經用掉的兩小時抹掉，
    而那兩小時是拿不回來的（沒有人記得剛剛是幾點開始的）。
    """
    return {"status": "success",
            "room": await bundle_for(room).queue.start_room_session(payload.get("minutes"))}


@app.post("/api/room/extend")
async def extend_room_timer(payload: Dict[str, Any] = Body(default={}),
                            room: str = Query(default=rooms.DEFAULT_ROOM_ID)):
    """
    續時（加時間，不是重開一場）。停在「時間到」畫面時按它會自己接回去播下一首。
    """
    return {"status": "success",
            "room": await bundle_for(room).queue.extend_room_session(payload.get("minutes"))}


@app.post("/api/room/pause")
async def pause_room_timer(room: str = Query(default=rooms.DEFAULT_ROOM_ID)):
    """停錶（中場休息、餐點來了）。播放不受影響。"""
    return {"status": "success", "room": await bundle_for(room).queue.pause_room_session()}


@app.post("/api/room/resume")
async def resume_room_timer(room: str = Query(default=rooms.DEFAULT_ROOM_ID)):
    """繼續倒數。"""
    return {"status": "success", "room": await bundle_for(room).queue.resume_room_session()}


@app.post("/api/room/stop")
async def stop_room_timer(room: str = Query(default=rooms.DEFAULT_ROOM_ID)):
    """
    結束計時（這桌不要再被計時了）。

    刻意連「時間到停播」的旗標一起解除並接回去播：這顆鍵的意思是
    「不要再管時間了」，按完卻還停在散場畫面不肯播的話，沒有人找得到怎麼救回來。
    """
    return {"status": "success", "room": await bundle_for(room).queue.stop_room_session()}


def marquee_state(bundle: "RoomBundle") -> Dict[str, Any]:
    """
    廣播給這一間所有裝置的舞台訊息狀態 = 訊息清單 + 現在生效的規則。

    規則跟著清單一起送，是因為畫面需要它們：舞台要知道「沒在播歌時要不要用
    大字卡」，點歌台要知道送出時預設幾秒、幾分鐘後消失。分兩支端點拿的話，
    兩邊會有一邊拿到的是舊的。

    規則本身是整台機器一份（設定頁），只有**訊息清單**分包廂 ——
    「跑馬燈幾秒消失」是這台機器的調校，不是這一桌客人的偏好。
    """
    policy = settings.marquee_policy()
    return {
        **bundle.marquee.snapshot(),
        "room_id": bundle.id,
        "enabled": policy["enabled"],
        "default_seconds": policy["seconds"],
        "default_ttl_minutes": policy["ttl_minutes"],
        "card_when_idle": policy["card_when_idle"],
    }


@app.get("/api/marquee")
async def get_marquee(room: str = Query(default=rooms.DEFAULT_ROOM_ID)):
    """
    舞台訊息：現在有哪些字要出現在包廂螢幕上。

    舞台端開機時先問這一支（WebSocket 只推「有變動」的那一刻，
    中途才打開的舞台不問一次就會是空的），之後靠 MARQUEE_UPDATE 更新。
    """
    return marquee_state(bundle_for(room))


@app.post("/api/marquee")
async def post_marquee(payload: Dict[str, Any] = Body(...),
                       room: str = Query(default=rooms.DEFAULT_ROOM_ID),
                       broadcast_all: bool = Query(default=False)):
    """
    送一則到舞台上（櫃檯的「您的餐點到了」、生日祝福）。

    `broadcast_all=true` 是櫃檯的**全店廣播**（「本店 23:30 打烊」）——
    一次送進每一間包廂。做成一個旗標而不是讓櫃檯按十次，是因為按十次的
    那個人一定會漏掉一間，而漏掉的那一間正是還沒聽到要打烊的那一間。

    擋下來的時候回 409 而不是 400：跟點歌額度、歡唱時間同一種語氣 ——
    這不是「你送錯了」，是「現在的狀態不收這一則」，而每一句「不行」
    後面都要有下一步（等一則播完、或先撤掉一則）。
    """
    policy = settings.marquee_policy()
    targets = room_registry.items() if broadcast_all else [bundle_for(room)]
    if not policy["enabled"]:
        raise HTTPException(status_code=409,
                            detail={"error": "disabled",
                                    "marquee": marquee_state(targets[0])})
    sent = []
    for bundle in targets:
        try:
            message = bundle.marquee.post(
                payload.get("text"),
                sender=payload.get("sender", "") or "",
                urgent=bool(payload.get("urgent")),
                pinned=bool(payload.get("pinned")),
                # 沒指定就用設定頁的值。每一則都可以自己帶（緊急的想久一點、
                # 生日祝福想釘住），但包廂不該為了送一句話而先進設定頁。
                ttl_minutes=payload.get("ttl_minutes", policy["ttl_minutes"]),
                seconds=payload.get("seconds", policy["seconds"]),
            )
        except marquee.MarqueeRejected as exc:
            # 全店廣播時有一間滿了**不算整批失敗**：其餘九間該收到的還是要收到，
            # 而擋下來的那一間會出現在 rejected 裡讓櫃檯自己決定要不要清一清。
            if not broadcast_all:
                raise HTTPException(status_code=409,
                                    detail={"error": exc.reason, **exc.detail,
                                            "marquee": marquee_state(bundle)}) from exc
            sent.append({"room": bundle.id, "rejected": exc.reason})
            continue
        state = marquee_state(bundle)
        await ws_manager.broadcast({"type": "MARQUEE_UPDATE", "data": state}, room=bundle.id)
        sent.append({"room": bundle.id, "message": message})
    return {"status": "success", "sent": sent,
            "message": next((r.get("message") for r in sent if r.get("message")), None),
            "marquee": marquee_state(targets[0])}


@app.delete("/api/marquee/{message_id}")
async def delete_marquee(message_id: str, room: str = Query(default=rooms.DEFAULT_ROOM_ID)):
    """撤掉一則（打錯字、那件事已經處理完了）。撤不到也回 success：
    畫面要的結果是「它不在了」，而它確實不在了。"""
    bundle = bundle_for(room)
    removed = bundle.marquee.remove(message_id)
    state = marquee_state(bundle)
    if removed:
        await ws_manager.broadcast({"type": "MARQUEE_UPDATE", "data": state}, room=bundle.id)
    return {"status": "success", "removed": removed, "marquee": state}


@app.delete("/api/marquee")
async def clear_marquee(include_pinned: bool = Query(default=True),
                        room: str = Query(default=rooms.DEFAULT_ROOM_ID)):
    """
    全部撤掉。`include_pinned=false` 會留下釘住的那幾則 ——
    「把剛剛那幾則清掉」跟「連生日祝福也拿掉」是兩個不同的意思。
    """
    bundle = bundle_for(room)
    removed = bundle.marquee.clear(include_pinned=include_pinned)
    state = marquee_state(bundle)
    await ws_manager.broadcast({"type": "MARQUEE_UPDATE", "data": state}, room=bundle.id)
    return {"status": "success", "removed": removed, "marquee": state}


def service_state(bundle: "RoomBundle") -> Dict[str, Any]:
    """
    廣播給這一間所有裝置的服務鈴狀態 = 現在那張單 + 紀錄 + 現在生效的規則。

    規則跟著送的理由跟舞台訊息一樣：客人那支手機要知道「按幾分鐘後會過期」，
    櫃檯那一端要知道要不要響一聲。分兩支端點拿的話，兩邊會有一邊拿到的是舊的。

    每一次都先把設定頁的「開多久算過期」套進去 —— 設定改完不必重開伺服器，
    而且**只影響還開著的那一張**（已經結案的不會自己亮回來，
    見 service_calls.py `set_stale_minutes`）。

    房號與房名一起送：櫃檯那一頁收到的是十間包廂的單混在一起，
    沒有房名的話那一列只寫著「加冰塊」，而櫃檯不知道要端去哪裡。
    """
    policy = settings.service_call_policy()
    bundle.service.set_stale_minutes(policy["stale_minutes"])
    return {
        **bundle.service.snapshot(),
        "room_id": bundle.id,
        "room_name": room_name_of(bundle.id),
        "enabled": policy["enabled"],
        "chime": policy["chime"],
    }


@app.get("/api/service")
async def get_service_calls(room: str = Query(default=rooms.DEFAULT_ROOM_ID),
                            all_rooms: bool = Query(default=False)):
    """
    服務鈴：現在有沒有人在叫櫃檯。

    `all_rooms=true` 是櫃檯那一頁要的那一份 —— 全店還開著的單，
    照「等最久的排最前面」排。櫃檯要的順序是等待時間，不是房號
    （見 service_calls.py 決定三：併單不重設等待時間）。

    跟舞台訊息同一個道理：中途才打開的點歌台要先問一次（WebSocket 只推
    「有變動」的那一刻），之後靠 SERVICE_UPDATE 更新。
    """
    if not all_rooms:
        return service_state(bundle_for(room))
    rows = [service_state(b) for b in room_registry.items()]
    open_calls = [{**r["call"], "room_id": r["room_id"], "room_name": r["room_name"]}
                  for r in rows if r.get("call")]
    open_calls.sort(key=lambda c: str(c.get("created_at") or ""))
    return {"rooms": rows, "open_calls": open_calls,
            "waiting_count": len(open_calls)}


@app.post("/api/service")
async def post_service_call(payload: Dict[str, Any] = Body(...),
                            room: str = Query(default=rooms.DEFAULT_ROOM_ID)):
    """
    按下服務鈴。

    已經有一張開著就併進去（見 service_calls.py 決定一），回傳的 `merged`
    讓畫面講得出「已經併進剛剛那一張」而不是再講一次「已送出」——
    第二次按下去得到跟第一次一模一樣的回應，那個人會以為第一次沒送出去。
    """
    bundle = bundle_for(room)
    policy = settings.service_call_policy()
    if not policy["enabled"]:
        raise HTTPException(status_code=409, detail={"error": "disabled",
                                                     "service": service_state(bundle)})
    try:
        call = bundle.service.ring(payload.get("items"),
                                   note=payload.get("note", "") or "",
                                   by=payload.get("by", "") or "")
    except service_calls.ServiceCallRejected as exc:
        raise HTTPException(status_code=409,
                            detail={"error": exc.reason, **exc.detail,
                                    "service": service_state(bundle)}) from exc
    state = service_state(bundle)
    await ws_manager.broadcast({"type": "SERVICE_UPDATE", "data": state,
                                # 併單不再響一次：櫃檯已經看到那一列了，
                                # 而同一張單響三聲只會讓人把音量關掉。
                                "chime": bool(policy["chime"]) and not call.get("merged")},
                               room=bundle.id)
    return {"status": "success", "call": call, "merged": bool(call.get("merged")),
            "service": state}


@app.post("/api/service/ack")
async def ack_service_call(payload: Dict[str, Any] = Body(default=None),
                           room: str = Query(default=rooms.DEFAULT_ROOM_ID)):
    """
    櫃檯收到了。不改變現實世界，卻是整個功能最重要的一步 ——
    它把「等待中」變成「有人看到了」，而那正是客人還會不會再按一次的分水嶺。
    """
    data = payload or {}
    bundle = bundle_for(data.get("room") or room)
    try:
        call = bundle.service.ack(call_id=data.get("id"), by=data.get("by", "") or "")
    except service_calls.ServiceCallRejected as exc:
        raise HTTPException(status_code=409,
                            detail={"error": exc.reason, **exc.detail,
                                    "service": service_state(bundle)}) from exc
    state = service_state(bundle)
    await ws_manager.broadcast({"type": "SERVICE_UPDATE", "data": state}, room=bundle.id)
    return {"status": "success", "call": call, "service": state}


@app.post("/api/service/resolve")
async def resolve_service_call(payload: Dict[str, Any] = Body(default=None),
                               room: str = Query(default=rooms.DEFAULT_ROOM_ID)):
    """櫃檯處理完了。`reply` 是回給包廂的那一句（「餐點五分鐘後到」）。"""
    data = payload or {}
    bundle = bundle_for(data.get("room") or room)
    try:
        call = bundle.service.resolve(call_id=data.get("id"),
                                      reply=data.get("reply", "") or "",
                                      by=data.get("by", "") or "")
    except service_calls.ServiceCallRejected as exc:
        raise HTTPException(status_code=409,
                            detail={"error": exc.reason, **exc.detail,
                                    "service": service_state(bundle)}) from exc
    state = service_state(bundle)
    await ws_manager.broadcast({"type": "SERVICE_UPDATE", "data": state}, room=bundle.id)
    return {"status": "success", "call": call, "service": state}


@app.post("/api/service/cancel")
async def cancel_service_call(payload: Dict[str, Any] = Body(default=None),
                              room: str = Query(default=rooms.DEFAULT_ROOM_ID)):
    """
    包廂自己取消（「不用了，我們自己去拿」）。

    跟「完成」分成兩個狀態是刻意的：按不掉的鈴大家就不敢按，而兩者在紀錄上
    長得一樣的話，「今晚有幾單沒服務到」就永遠問不出來。
    """
    data = payload or {}
    bundle = bundle_for(room)
    try:
        call = bundle.service.cancel(call_id=data.get("id"), by=data.get("by", "") or "")
    except service_calls.ServiceCallRejected as exc:
        raise HTTPException(status_code=409,
                            detail={"error": exc.reason, **exc.detail,
                                    "service": service_state(bundle)}) from exc
    state = service_state(bundle)
    await ws_manager.broadcast({"type": "SERVICE_UPDATE", "data": state}, room=bundle.id)
    return {"status": "success", "call": call, "service": state}


@app.delete("/api/service/history")
async def clear_service_history(room: str = Query(default=rooms.DEFAULT_ROOM_ID)):
    """
    清掉紀錄（換一桌客人）。**開著的那一張不動** ——
    它對應的是現實世界裡還沒做完的事，不會因為按了「清除紀錄」就做完了。
    """
    bundle = bundle_for(room)
    removed = bundle.service.clear_history()
    state = service_state(bundle)
    await ws_manager.broadcast({"type": "SERVICE_UPDATE", "data": state}, room=bundle.id)
    return {"status": "success", "removed": removed, "service": state}


@app.post("/api/queue/skip")
async def skip_song(room: str = Query(default=rooms.DEFAULT_ROOM_ID)):
    next_song = await bundle_for(room).queue.skip_current()
    return {"status": "success", "next_song": next_song}

@app.post("/api/queue/restart")
async def restart_song(room: str = Query(default=rooms.DEFAULT_ROOM_ID)):
    await bundle_for(room).queue.restart_current()
    return {"status": "success"}

@app.post("/api/seek")
async def seek_playback(payload: Dict[str, Any] = Body(...),
                        room: str = Query(default=rooms.DEFAULT_ROOM_ID)):
    """跳到指定秒數：進度條拖曳、段落跳轉、回到 A 點練唱都用這支。"""
    position = await bundle_for(room).queue.seek_to(payload.get("position", 0))
    return {"status": "success", "position": position}

@app.post("/api/skip-interlude")
async def skip_interlude(room: str = Query(default=rooms.DEFAULT_ROOM_ID)):
    """跳過正在倒數的前奏／間奏。

    這支刻意**不帶目標秒數**（不是另一條 `/api/seek`）：該跳到哪裡只有舞台端
    算得出來 —— 空檔表活在歌詞時間軸上，而 `audio.currentTime` 活在音訊時間軸上，
    中間差著這首歌的 LRC 偏移與兩點校正解出來的速度，兩者都只存在舞台那一端。

    點歌台自己算一份的話，手機上按下去的位置會跟電視上那個倒數差幾秒，
    而「調過字幕的歌跳得特別不準」這種關聯，在包廂裡沒有任何人查得出來。
    所以這裡只把「有人按了」廣播出去，判斷與落點都留在唯一有資料的那一端；
    現在沒有可跳的空檔時，舞台端什麼都不做。
    """
    bundle = bundle_for(room)
    await ws_manager.broadcast({
        "type": "CONTROL_COMMAND",
        "command": "SKIP_INTERLUDE",
    }, room=bundle.id)
    return {"status": "success"}

@app.post("/api/control")
async def control_playback(payload: Dict[str, Any] = Body(...),
                           room: str = Query(default=rooms.DEFAULT_ROOM_ID)):
    queue_manager = bundle_for(room).queue
    await queue_manager.update_controls(payload)
    return {"status": "success", "state": queue_manager.get_full_state()}

@app.post("/api/sound-effect")
async def trigger_sound_effect(payload: Dict[str, str] = Body(...),
                               room: str = Query(default=rooms.DEFAULT_ROOM_ID)):
    bundle = bundle_for(room)
    effect_name = payload.get("effect", "cheer")
    await ws_manager.broadcast({
        "type": "SOUND_EFFECT",
        "effect": effect_name
    }, room=bundle.id)
    return {"status": "success", "effect": effect_name}

@app.get("/api/rankings")
async def get_rankings(limit: int = Query(20, ge=1, le=100)):
    """熱門點唱排行：依實際上台演唱次數排序。"""
    return {
        "rankings": play_stats.top(limit),
        "total_plays": play_stats.total_plays()
    }


@app.delete("/api/rankings")
async def reset_rankings():
    play_stats.reset()
    return {"status": "success"}


@app.get("/api/favorites")
async def get_favorites():
    """我的最愛清單：最新收藏排最前面。"""
    return {"favorites": favorites.list_all(), "ids": favorites.ids()}


@app.post("/api/favorites/toggle")
async def toggle_favorite(payload: Dict[str, Any] = Body(...)):
    """收藏 ↔ 取消收藏，一顆按鈕搞定。"""
    if not (payload.get("song_id") or payload.get("id")):
        raise HTTPException(status_code=400, detail="Missing song_id parameter")
    result = favorites.toggle(payload)
    return {"status": "success", **result}


@app.delete("/api/favorites/{song_id}")
async def remove_favorite(song_id: str):
    removed = favorites.remove(song_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Song not in favorites")
    return {"status": "success"}


@app.get("/api/history")
async def get_history(limit: int = Query(50, ge=1, le=200),
                      room: str = Query(default=rooms.DEFAULT_ROOM_ID),
                      all_rooms: bool = Query(default=False)):
    """
    已唱歷史：最近實際上台演唱過的歌，最新的排最前面。

    預設只看**這一間**唱過的（那份清單是給包廂裡的人按「再唱一次」的，
    混進別間的歌就不是他們唱過的東西了）。櫃檯要看全店的用 `all_rooms=true`。
    """
    if not all_rooms:
        bundle_for(room)
    target = None if all_rooms else room
    return {
        "history": song_history.recent(limit, room=target),
        "today_count": song_history.today_count(room=target),
        "total_count": song_history.total_count(room=target),
        "room_id": None if all_rooms else room,
    }


@app.delete("/api/history")
async def clear_history(room: str = Query(default=rooms.DEFAULT_ROOM_ID),
                        all_rooms: bool = Query(default=False)):
    """清空已唱歷史。預設只清這一間；`all_rooms=true` 才是整台機器。"""
    if not all_rooms:
        bundle_for(room)
    song_history.clear(room=None if all_rooms else room)
    return {"status": "success"}


@app.post("/api/scores")
async def submit_score(payload: Dict[str, Any] = Body(...),
                       room: str = Query(default=rooms.DEFAULT_ROOM_ID)):
    """
    唱畢結算：舞台端送來總分，回傳含個人最佳與擊敗比例的結算資料。

    評分歷史是**整台機器**的（跟著人走，不跟著房間走），今晚擂台是
    **這一間**的 —— 同一張成績單同時餵兩邊，分界見 rooms.py 與 contest.py。
    """
    bundle = bundle_for(room)
    result = score_history.record(payload)
    if result is None:
        raise HTTPException(status_code=400, detail="Missing song_id or invalid score")
    result["contest"] = bundle.contest.record(contest_take(payload, payload))
    # 廣播給所有端（點歌台 / 手機）同步顯示結算結果
    await ws_manager.broadcast({"type": "SCORE_FINAL", "data": result}, room=bundle.id)
    await broadcast_contest(bundle)
    return {"status": "success", "result": result}


@app.post("/api/scores/duet")
async def submit_duet_score(payload: Dict[str, Any] = Body(...),
                            room: str = Query(default=rooms.DEFAULT_ROOM_ID)):
    """
    對唱模式的唱畢結算：兩位演唱者的成績一起送、一起記、廣播一則通知。

    形狀是 `{song_id, title, artist, thumbnail, a: {...}, b: {...}}`。
    分兩次呼叫 `/api/scores` 也能記到分，但中間斷線就會留下一場
    只有一個人的對唱，而且包廂裡每支手機會跳兩則結算通知。
    """
    bundle = bundle_for(room)
    result = score_history.record_duet(payload)
    if result is None:
        raise HTTPException(status_code=400, detail="Missing song_id / a / b or invalid score")
    # 兩位各記一次。對唱那一側的名字用 payload 裡的原始值，**不是** score_history
    # 補過的「A 麥」「B 麥」—— 那兩個預設值是給成績單看的，但榜上不能有一位
    # 叫「A 麥」的歌王（它今晚是三個不同的人）。
    result["contest"] = {
        "a": bundle.contest.record(contest_take(payload, payload.get("a") or {})),
        "b": bundle.contest.record(contest_take(payload, payload.get("b") or {})),
    }
    await ws_manager.broadcast({"type": "SCORE_FINAL", "data": result}, room=bundle.id)
    await broadcast_contest(bundle)
    return {"status": "success", "result": result}


def contest_take(payload: Dict[str, Any], side: Dict[str, Any]) -> Dict[str, Any]:
    """
    成績單 → 擂台要的那幾個欄位。

    `side` 單人演唱時就是整份 payload，對唱時是 `a` / `b` 其中一側 ——
    曲目資訊在外層、唱的人與命中率在側邊，所以兩個都要收。

    `performer` 單人用 `performer`（舞台端填的是「誰點的」，那是包廂裡唯一
    知道的名字），對唱用那一側的 `singer`。
    """
    return {
        "song_id": payload.get("song_id") or payload.get("id"),
        "title": payload.get("title", ""),
        # `performer` 在就用它，**即使是空字串** —— 對唱那一側沒取名時
        # `singer` 是補過的「A 麥」，拿它當退路等於把決定四繞過去。
        # 退路只給更早版本的舞台端（沒有 performer 這個欄位）。
        "performer": side.get("performer", side.get("singer", "")),
        "accuracy": side.get("accuracy"),
        "score": side.get("score"),
        "grade": side.get("grade", ""),
        "note_frames": side.get("note_frames"),
    }


async def broadcast_contest(bundle: "RoomBundle"):
    """榜變了就推給這一間的每一台裝置（點歌台的擂台分頁不必自己輪詢）。"""
    await ws_manager.broadcast({"type": "CONTEST_UPDATE",
                                "data": bundle.contest.snapshot()}, room=bundle.id)


@app.get("/api/contest")
async def get_contest(room: str = Query(default=rooms.DEFAULT_ROOM_ID)):
    """今晚擂台：這一間這一場的歌王榜。"""
    return {"contest": bundle_for(room).contest.snapshot()}


@app.post("/api/contest/reset")
async def reset_contest(room: str = Query(default=rooms.DEFAULT_ROOM_ID)):
    """
    手動開新的一場（換一批客人）。

    跟輪唱的「重新排」是同一顆鍵的意思，所以也跟它一樣要櫃檯解鎖 ——
    按下去會抹掉還在包廂裡的那幾位今晚的成績，而那是拿不回來的。
    平常不必按：隔了 6 小時沒有人唱，榜自己就翻新了（contest.py 決定五）。
    """
    bundle = bundle_for(room)
    board = bundle.contest.reset()
    await broadcast_contest(bundle)
    return {"status": "success", "contest": board}


@app.get("/api/scores")
async def get_scores(limit: int = Query(50, ge=1, le=200)):
    """評分歷史：最近的演唱成績與每首歌的個人最佳（含對唱的個人最佳）。"""
    return {
        "scores": score_history.recent(limit),
        "bests": score_history.bests(),
        "singer_bests": score_history.singer_bests(),
        "total_count": score_history.total_count(),
    }


@app.get("/api/scores/trends")
async def get_score_trends(limit: int = Query(20, ge=1, le=100)):
    """
    跨場次段落趨勢總表：所有唱到「有話可說」（同一曲式至少三場）的歌。

    路由要排在 `/api/scores/{song_id}/best` 之前嗎？不必 —— 段數不同
    （這支三段、那支四段），FastAPI 不會把 `trends` 當成 song_id。
    """
    return {"trends": score_history.trends(limit)}


@app.get("/api/scores/{song_id}/best")
async def get_best_score(song_id: str):
    """單曲個人最佳。還沒唱過回傳 null，讓前端自己決定顯示。"""
    return {"song_id": song_id, "best": score_history.best_for(song_id)}


@app.get("/api/scores/{song_id}/trend")
async def get_song_trend(song_id: str, singer: str = Query("")):
    """
    單曲的跨場次段落趨勢（這位演唱者一向強在哪一段）。

    `singer` 空字串＝單人演唱的紀錄。場次不夠時照樣回 200 並附上
    `status: "insufficient"` 與還差幾場 —— 「再唱兩次就能看出你的弱點」
    是有用的畫面，404 不是。
    """
    return {
        "song_id": song_id,
        "singer": singer,
        "trend": score_history.trend_for(song_id, singer),
    }


# --- 音域檢測與建議 Key ---------------------------------------------------
#
# 五條路由全部不分包廂、全部不上鎖：音域是「這位客人的聲音」，
# 而唱歌本身的動作在 access_policy 裡一律不需要櫃檯密碼（見 staff_lock.py 的判準一）。


@app.post("/api/vocal-range")
async def submit_vocal_range(payload: Dict[str, Any] = Body(...)):
    """
    收一次演唱的音高直方圖（舞台端在唱畢時送，形狀 `{singer, bins}`）。

    刻意**不**併進 `/api/scores`：兩件事的失敗代價差很多。結算是使用者
    正在看的畫面，音域是背景累積 —— 併在一起的話，直方圖太大或格式有問題
    就會讓一張已經算好的成績單回 400，而使用者完全不知道為什麼沒有成績。
    分開之後，這一條掉了只是這一首沒累積到，下一首照樣接上。

    功能關掉時安靜地不記（回 `recorded: false`）—— 設定頁關掉的意思是
    「不要收集我的聲音」，那就一幀都不能收。
    """
    if not settings.get("vocal_range_enabled", True):
        return {"status": "success", "result": {"recorded": False, "reason": "disabled",
                                                "message": "音域檢測已在系統設定頁關閉。"}}
    return {"status": "success",
            "result": vocal_range.submit(payload.get("singer"), payload.get("bins"))}


@app.get("/api/vocal-range")
async def list_vocal_ranges():
    """櫃檯視角：這台機器上有幾份音域檔（設定頁的「已建檔的聲音」那一列）。"""
    return {"singers": vocal_range.list_singers(),
            "enabled": bool(settings.get("vocal_range_enabled", True))}


@app.get("/api/vocal-range/profile")
async def get_vocal_range(singer: str = Query("")):
    """
    某個人的音域檔案。

    暱稱走 query string 而不是路徑參數：包廂裡的暱稱是自由文字（會有
    斜線、`.`、emoji），塞進路徑要多一層編碼，而編漏一次的症狀是
    「我的音域頁面是空的」—— 那種錯在現場查不出來。
    """
    return {"profile": vocal_range.profile(singer),
            "enabled": bool(settings.get("vocal_range_enabled", True))}


@app.get("/api/vocal-range/advice")
async def get_vocal_range_advice(singer: str = Query(""), song_id: str = Query("")):
    """
    這個人 + 這首歌 → 建議移調幾個 Key。

    歌還沒處理完（沒有 pitch.json）時回的是 `no_demand` 而不是 404：
    這一行在畫面上是**可有可無**的一行，查不到就安靜地不出現，
    不該讓點歌台的整塊面板亮紅字。
    """
    notes = storage.get_song_pitch(song_id).get("notes", []) if song_id else []
    return {"song_id": song_id, "singer": singer,
            "advice": vocal_range.advice(singer, notes),
            "enabled": bool(settings.get("vocal_range_enabled", True))}


@app.delete("/api/vocal-range")
async def reset_vocal_range(singer: str = Query("")):
    """
    「重新認識我的聲音」：把某個人的檔案整份刪掉。

    這是唯一能瞬間交棒的方法（公用機器上同一個暱稱換了一個人），
    所以它不上櫃檯鎖 —— 要櫃檯拿鑰匙才能刪掉自己的聲音，
    等於這個功能在包廂裡不存在。
    """
    removed = vocal_range.reset(singer)
    return {"status": "success", "removed": removed}


def _recording_quota() -> Dict[str, int]:
    """目前的錄音配額。每次上傳都重讀設定 —— 使用者調完上限不必重開伺服器。"""
    return {
        "max_count": int(settings.get("recording_max_count", 50) or 0),
        "max_bytes": settings.recording_limit_bytes(),
    }


def _recording_download_name(entry: Dict[str, Any], suffix: str = "") -> str:
    """
    下載回去的檔名：`20260913-2130 歌名 - 演唱者.webm`。

    檔名裡的斜線與冒號在 Windows 是非法字元，歌名裡它們又很常見
    （「A/B」「Part 2: ...」），不換掉的話瀏覽器會拿到一個存不下去的檔名。
    """
    stamp = str(entry.get("created_at", ""))[:16].replace("-", "").replace(":", "").replace("T", "-")
    parts = [p for p in (entry.get("title", ""), entry.get("singer", "")) if p]
    stem = f"{stamp} {' - '.join(parts)}".strip() or str(entry.get("id", "recording"))
    safe = "".join("_" if c in '\\/:*?"<>|' else c for c in stem)[:120]
    return f"{safe}{suffix or suffix_for_mime(entry.get('mime', ''))}"


# --- 錄音轉 MP3 ---
#
# 錄音是瀏覽器的 MediaRecorder 錄的（webm/Opus 或 mp4/AAC），在包廂裡播沒問題，
# 但錄音真正的去處是車機的 USB、長輩的舊手機、傳過去給對方直接點開 ——
# 那些地方只認 MP3。分享連結解決「怎麼傳給我」，這一段解決「傳過去打不打得開」。

def _mp3_capability() -> Dict[str, Any]:
    """
    這台機器現在能不能轉 MP3。前端拿這個決定按鈕要不要出現。

    「能不能」有兩種不能：設定頁關掉了（使用者的決定），
    以及 ffmpeg 不在／沒有 libmp3lame（機器的狀況）。兩種要分開講，
    不然管理員會去裝一個他其實已經裝好的東西。
    """
    if not settings.get("recording_mp3_enabled", True):
        return {"available": False, "reason": "disabled",
                "message": "設定頁把「錄音轉 MP3」關掉了"}
    cap = probe_ffmpeg()
    return {"available": bool(cap.get("available")), "reason": cap.get("reason", ""),
            "message": cap.get("message", "")}


async def _mp3_capability_async() -> Dict[str, Any]:
    """
    給 async 端點用的版本。

    探測結果幾乎都是快取命中（微秒等級），但**第一次**是真的去開一個子行程，
    而且卡住的 ffmpeg 會讓它等到逾時 —— 那段時間整台伺服器的 WebSocket
    都會停住。清單與分享頁的資料都會經過這裡，所以一律丟到執行緒裡問。
    """
    return await asyncio.to_thread(_mp3_capability)


def _ensure_recording_mp3(rec_id: str) -> Dict[str, Any]:
    """
    確保這一筆有一份轉好的 MP3，回傳 `{"status": ..., "path": Path}`。

    **會阻塞**（裡面是 ffmpeg 子行程），所以呼叫端一律走 `asyncio.to_thread`：
    直接在 async 端點裡跑的話，轉檔那十幾秒整台伺服器的 WebSocket 都會停住 ——
    症狀是「有人按了下載，舞台上的歌詞就卡住了」。
    """
    entry = recordings.get(rec_id)
    if entry is None:
        return {"status": "missing", "message": "找不到這筆錄音"}

    ready = recordings.mp3_path_for(rec_id)
    if ready is not None:
        recordings.touch_mp3(rec_id)
        return {"status": "ready", "path": ready, "entry": entry}

    cap = _mp3_capability()
    if not cap["available"]:
        return {"status": "unavailable", **cap}

    def work() -> Dict[str, Any]:
        # 排在後面的那一個進來時通常已經有人轉好了，直接用 ——
        # 再轉一次不只白做工，還會蓋掉一個正在被下載的檔案。
        already = recordings.mp3_path_for(rec_id)
        if already is not None:
            recordings.touch_mp3(rec_id)
            return {"status": "ready", "path": already, "entry": entry}

        src = recordings.path_for(rec_id)
        dst = recordings.mp3_target_for(rec_id)
        if src is None or dst is None:
            return {"status": "missing", "message": "找不到這筆錄音"}

        result = transcode_to_mp3(
            src, dst,
            bitrate_kbps=clamp_bitrate(settings.get("recording_mp3_bitrate", 192)),
            tags={
                "title": entry.get("title", ""),
                # 車機螢幕上「演唱者」比原唱有意義 —— 這是他自己唱的那一次
                "artist": entry.get("singer") or entry.get("artist", ""),
                "album": "KaraTube",
                "date": str(entry.get("created_at", ""))[:10],
            },
        )
        if result.get("status") != "ok":
            return {"status": "failed", **result}

        recordings.register_mp3(rec_id, mp3_cache_limit(_recording_quota()["max_bytes"]))
        path = recordings.mp3_path_for(rec_id)
        if path is None:
            # 轉好了卻立刻被快取上限擠掉：只會發生在「單一份比整個快取還大」，
            # 這時候照實說，不要回一個指向空氣的路徑。
            return {"status": "failed", "message": "MP3 轉好了但放不進快取，請調高錄音配額"}
        return {"status": "ready", "path": path, "entry": entry}

    return mp3_gate.run(rec_id, work)


async def _mp3_path_or_error(rec_id: str) -> Path:
    """轉好的 MP3 路徑，拿不到就丟對應的 HTTP 錯誤。"""
    result = await asyncio.to_thread(_ensure_recording_mp3, rec_id)
    status = result.get("status")
    if status == "ready":
        return result["path"]
    if status == "missing":
        raise HTTPException(status_code=404, detail=result.get("message", "找不到這筆錄音"))
    # 轉不出來全部是 503（暫時性）而不是 500：ffmpeg 裝好、設定打開、
    # 或是等前面那一個轉完，同一個網址就會成功。原始錄音一直都還在。
    raise HTTPException(status_code=503, detail=result.get("message", "MP3 轉檔失敗"))


def _prune_share_links() -> int:
    """
    把「錄音已經不在了」的分享連結收掉。

    配額把一筆錄音擠掉是**無聲**發生的（沒有人按刪除），所以刪除時撤銷
    不夠用；清單與上傳這兩條會經過的路上順手做一次，索引就不會無限長大。
    """
    return share_links.prune(e["id"] for e in recordings.list_all(limit=0))


@app.get("/api/recordings")
async def list_recordings(limit: int = Query(100, ge=1, le=500),
                          song_id: str = Query(""),
                          singer: str = Query("")):
    """錄唱回放清單：最近錄的排最前面，附配額用量。"""
    quota = _recording_quota()
    _prune_share_links()
    return {
        "recordings": recordings.list_all(limit, song_id=song_id, singer=singer),
        "stats": recordings.stats(**quota),
        "enabled": bool(settings.get("recording_enabled", False)),
        "share_enabled": bool(settings.get("recording_share_enabled", True)),
        # 這台機器能不能轉 MP3。前端照這個決定按鈕出不出現 ——
        # 讓使用者按下去才知道機器上沒有 ffmpeg，是最差的講法。
        "mp3": await _mp3_capability_async(),
    }


@app.post("/api/recordings")
async def upload_recording(request: Request,
                           song_id: str = Query(...),
                           title: str = Query(""),
                           artist: str = Query(""),
                           thumbnail: str = Query(""),
                           singer: str = Query(""),
                           mode: str = Query("solo"),
                           duration_ms: int = Query(0, ge=0),
                           score: int = Query(0, ge=0),
                           grade: str = Query(""),
                           accuracy: float = Query(0.0, ge=0.0, le=1.0)):
    """
    舞台端唱完後把錄音上傳進來。音檔是 **raw body**，metadata 走 query string。

    刻意不用 multipart：那要多裝一個 `python-multipart`，而這裡要傳的只有
    「一個檔案 + 幾個欄位」，raw body 讓後端、測試與前端三邊都少一層解析。

    Content-Length 先擋一次再讀 body —— Starlette 的 `request.body()` 會把整包
    讀進記憶體，等讀完才發現太大就已經吃掉那些記憶體了。
    """
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > HARD_MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="錄音檔太大")

    audio = await request.body()
    result = recordings.save(
        audio,
        {
            "song_id": song_id, "title": title, "artist": artist, "thumbnail": thumbnail,
            "singer": singer, "mode": mode, "duration_ms": duration_ms,
            "score": score, "grade": grade, "accuracy": accuracy,
            "mime": request.headers.get("content-type", ""),
        },
        **_recording_quota(),
    )
    if result.get("status") != "saved":
        # 400 而不是 500：拒絕都是「送進來的東西不合規」，不是伺服器壞了
        raise HTTPException(status_code=400, detail=result.get("message", "錄音存檔失敗"))

    await ws_manager.broadcast({"type": "RECORDING_SAVED", "data": result["recording"]})
    return {"status": "success", **result, "stats": recordings.stats(**_recording_quota())}


@app.get("/api/recordings/{rec_id}/audio")
async def get_recording_audio(rec_id: str, download: int = Query(0, ge=0, le=1),
                              format: str = Query("")):
    """
    錄音檔本體。`download=1` 才給 Content-Disposition，否則就地播放。

    `format=mp3` 轉一份 MP3 再給（第一次會等幾秒，之後是秒回）——
    車機與舊播放器不吃 webm，而錄音的去處多半就是那些地方。
    """
    entry = recordings.get(rec_id)
    path = recordings.path_for(rec_id)
    if entry is None or path is None:
        raise HTTPException(status_code=404, detail="找不到這筆錄音")
    if str(format).lower() == "mp3":
        return FileResponse(
            await _mp3_path_or_error(rec_id),
            media_type="audio/mpeg",
            filename=_recording_download_name(entry, ".mp3") if download else None,
        )
    return FileResponse(
        path,
        media_type=entry.get("mime") or "application/octet-stream",
        filename=_recording_download_name(entry) if download else None,
    )


@app.delete("/api/recordings/mp3")
async def clear_mp3_cache():
    """
    把轉好的 MP3 全部丟掉。錄音一個都不會動 ——
    這裡刪的是「可以重算出來的東西」，所以這顆按鈕不需要二次確認。
    """
    result = recordings.clear_mp3_cache()
    return {"status": "success", **result,
            "stats": recordings.stats(**_recording_quota())}


@app.post("/api/recordings/mp3/recheck")
async def recheck_mp3_support():
    """
    重新偵測 ffmpeg。管理員照著畫面上那句話把 ffmpeg 裝好之後，
    按這裡就好，不必重開整台伺服器。
    """
    reset_probe_cache()
    return {"status": "success", "mp3": await _mp3_capability_async()}


# --- 整晚打包下載 ---
#
# 分享連結是「一個人帶走自己那一首」，這一段是收場時的另一句話：
# 「今天晚上的通通給我一份」。一首一首按下載是二十三次另存新檔，
# 而且存出來散在資料夾裡分不出誰是誰、哪一首在前面。
#
# 這一段只在**點歌台**（包廂內網）出得來，刻意不掛在分享 token 底下：
# 一個 zip 是整場所有人的聲音，那不是掃 QR 的人該拿得到的東西。

def _session_gap_hours() -> float:
    return float(settings.get("recording_session_gap_hours", DEFAULT_GAP_HOURS)
                 or DEFAULT_GAP_HOURS)


def _sessions() -> List[Dict[str, Any]]:
    """目前所有場次（最近的排最前面）。`entries` 還留著，端點回應前要拿掉。"""
    # limit=0 是「全部」：打包要的是整場，被 100 筆的預設值截掉會少歌。
    return group_sessions(reversed(recordings.list_all(limit=0)), _session_gap_hours())


def _session_public(session: Dict[str, Any]) -> Dict[str, Any]:
    view = {k: v for k, v in session.items() if k != "entries"}
    view["zip_url"] = f"/api/recordings/sessions/{session['key']}/zip"
    return view


def _content_disposition(filename: str) -> str:
    """
    中文檔名的 `Content-Disposition`。

    兩種寫法都給：`filename*=UTF-8''…`（RFC 5987，現代瀏覽器認這個）
    加上一個把非 ASCII 換成底線的退化版 —— 只給前者的話，少數舊瀏覽器
    會存成一個叫 `zip` 的無副檔名檔案。
    """
    ascii_name = "".join(c if 32 <= ord(c) < 127 and c != '"' else "_" for c in filename)
    return (f"attachment; filename=\"{ascii_name}\"; "
            f"filename*=UTF-8''{quote(filename, safe='')}")


@app.get("/api/recordings/sessions")
async def list_recording_sessions():
    """
    有哪幾場可以打包。一場 = 連續唱的那一段（相隔超過設定的小時數就算換一場）。

    刻意**不照日曆日期切**：包廂的一場是「九點唱到凌晨兩點半」，
    照日期切會把它切成兩半，而且唱到最嗨的後半會被標成「隔天」。
    """
    return {
        "sessions": [_session_public(s) for s in _sessions()],
        "gap_hours": _session_gap_hours(),
        # 打包中的話畫面上先講一聲，不要讓使用者按下去才看到 429
        "busy": night_gate.busy,
    }


@app.get("/api/recordings/sessions/{key}/zip")
async def download_session_zip(key: str, singer: str = Query("")):
    """
    一整場包成一個 zip，**邊包邊送**。

    沒有先在磁碟上生一份再送，理由很實際：一場 200 MB 的話那就要另外佔
    200 MB，而錄音配額存在的理由正是「這顆磁碟會被塞爆」——
    打包下載不該是那個把磁碟塞爆的人。

    也因此沒有 `Content-Length`：精確長度要讀完才知道，而先算一次再串流的話，
    中間只要有一筆被配額擠掉，送出的位元組就對不上宣告的長度，瀏覽器會把
    整包當成下載失敗。清單端點已經先報過預估大小了。
    """
    session = find_session(_sessions(), key)
    if session is None:
        raise HTTPException(status_code=404, detail="找不到這一場（可能已經被清掉了）")
    entries = filter_by_singer(session, singer)
    if not entries:
        raise HTTPException(status_code=404, detail="這一場裡沒有符合的錄音")

    token = night_gate.acquire()
    if not token:
        # 429 而不是 503：這是「現在有人在打包」，等一下再按同一個網址就會成功
        raise HTTPException(status_code=429, detail="正在打包另一份，請等它下載完再試")

    view = {**session, "count": len(entries)}

    def stream():
        try:
            # 資料夾跟著錄音庫走（而不是直接用 RECORDINGS_DIR 常數）：
            # 錄音庫是模組層級的單例，換掉它的測試也要能測到真正的打包路徑。
            yield from iter_session_zip(recordings.base_dir, entries, view, singer)
        finally:
            # 使用者中途取消（關分頁、按停止）時 generator 會被關掉，
            # 這裡一樣會跑到 —— 位子要還，不然下一個人會被擋到租約過期。
            night_gate.release(token)

    return StreamingResponse(
        stream(),
        media_type="application/zip",
        headers={
            "Content-Disposition": _content_disposition(zip_filename(view, singer)),
            # 這是一次性的打包結果，內容會隨著錄音被刪而變 —— 不要讓中間的
            # 代理或瀏覽器把它留著當快取再送一次。
            "Cache-Control": "no-store",
        },
    )


# --- 錄音分享（一次性連結 / QR）---
#
# 「傳給我」是唱完之後的下一句話。這一段讓當事人自己把那一次帶走：
# 產一個有時效的連結與 QR，掃了就能聽、能下載，時間到自動失效。
#
# 分享頁是**不需要任何身分**就能打開的（掃 QR 的人不會先去登入），
# 所以這一段的每一個端點都只認 token，而且只交出 token 指到的那一筆：
# 錄音 id、檔案路徑、其他錄音的存在與否，一概不從這裡外流。

# 連結失效的四種原因，每一種要講的話不一樣 ——
# 全部回「無效」的話，使用者不知道該不該叫人重發一個。
SHARE_DEAD_MESSAGE = {
    "not_found": "這個分享連結不存在（可能是網址少了幾個字）",
    "revoked": "這個分享連結已經被撤銷了",
    "expired": "這個分享連結已經過期了，請原點歌的人重新分享",
    "exhausted": "這個分享連結的下載次數已經用完了",
    "gone": "這一次的錄音已經不在包廂那台機器上了（被刪除或配額清掉）",
}


def _share_defaults() -> Dict[str, int]:
    return {
        "ttl_hours": clamp_ttl_hours(settings.get("recording_share_ttl_hours", 24)),
        "max_downloads": clamp_max_downloads(settings.get("recording_share_max_downloads", 0)),
    }


def _share_view(share: Dict[str, Any]) -> Dict[str, Any]:
    """一筆分享連結加上「要給人的網址」。網址在伺服器組，前端不猜主機位址。"""
    base = public_base_url()
    return {
        **share,
        "url": f"{base}/share/{share['token']}",
        "qr_url": f"/api/share/{share['token']}/qr.png",
    }


def _resolve_share(token: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    token → (分享連結, 錄音)。任何一關不過就丟 HTTPException。

    錄音存在與否要**每次重查**：配額把那一筆擠掉是無聲發生的（沒有人按刪除），
    只靠刪除時撤銷的話，舊連結會活到過期為止卻打不出任何東西。
    """
    share, reason = share_links.resolve(token)
    if share is None:
        # 404 只留給「這個 token 從來不存在」；曾經有效而現在不給看的，
        # 是 410 Gone —— 前端才分得出「網址打錯」與「時間到了」。
        status = 404 if reason == "not_found" else 410
        raise HTTPException(status_code=status,
                            detail=SHARE_DEAD_MESSAGE.get(reason, "這個分享連結無法使用"))
    entry = recordings.get(share["recording_id"])
    if entry is None:
        share_links.revoke(token, reason="gone")
        raise HTTPException(status_code=410, detail=SHARE_DEAD_MESSAGE["gone"])
    return share, entry


@app.post("/api/recordings/{rec_id}/share")
async def share_recording(rec_id: str, payload: Dict[str, Any] = Body(default={})):
    """
    給這一筆錄音一個分享連結。已經有還有效的連結就沿用同一個 ——
    每按一次分享就讓上一個 QR 失效，是掃過的人無法理解的行為。
    真的要換一個（發錯人了）送 `new: true`，舊的會一起撤銷。
    """
    if not settings.get("recording_share_enabled", True):
        raise HTTPException(status_code=403, detail="錄音分享在系統設定裡是關閉的")
    if recordings.get(rec_id) is None:
        raise HTTPException(status_code=404, detail="找不到這筆錄音")

    defaults = _share_defaults()
    fresh = bool(payload.get("new"))
    if fresh:
        share_links.revoke_for_recording(rec_id)
    share = share_links.create(
        rec_id,
        ttl_hours=payload.get("ttl_hours", defaults["ttl_hours"]),
        max_downloads=payload.get("max_downloads", defaults["max_downloads"]),
        reuse=not fresh,
    )
    return {"status": "success", "share": _share_view(share)}


@app.get("/api/recordings/{rec_id}/shares")
async def list_recording_shares(rec_id: str):
    """這筆錄音發出去過的連結（含已失效的，畫面要說得出為什麼打不開）。"""
    if recordings.get(rec_id) is None:
        raise HTTPException(status_code=404, detail="找不到這筆錄音")
    return {
        "shares": [_share_view(s) for s in share_links.list_for(rec_id)],
        "defaults": _share_defaults(),
        "enabled": bool(settings.get("recording_share_enabled", True)),
    }


@app.delete("/api/share/{token}")
async def revoke_share(token: str):
    """撤銷一個連結。送出去才後悔的那種，按下去要立刻打不開。"""
    if not share_links.revoke(token):
        raise HTTPException(status_code=404, detail="找不到這個分享連結")
    return {"status": "success"}


@app.get("/api/share/{token}")
async def get_shared_recording(token: str):
    """
    分享頁要的資料。只給這一筆看得到的欄位 ——
    song_id 之類的內部識別、檔案路徑、其他錄音的存在與否都不從這裡出去。
    """
    share, entry = _resolve_share(token)
    share_links.note_view(token)
    return {
        "status": "success",
        "recording": {
            "title": entry.get("title") or "這一次的演唱",
            "artist": entry.get("artist", ""),
            "singer": entry.get("singer", ""),
            "thumbnail": entry.get("thumbnail", ""),
            "mode": entry.get("mode", "solo"),
            "duration_ms": entry.get("duration_ms", 0),
            "score": entry.get("score", 0),
            "grade": entry.get("grade", ""),
            "accuracy": entry.get("accuracy", 0.0),
            "created_at": entry.get("created_at", ""),
            "mime": entry.get("mime", ""),
        },
        "share": {
            "expires_at": share.get("expires_at", ""),
            "expires_in_seconds": share.get("expires_in_seconds", 0),
            "downloads_left": share.get("downloads_left"),
        },
        "audio_url": f"/api/share/{token}/audio",
        "download_url": f"/api/share/{token}/audio?download=1",
        # 拿到連結的人多半是要把這一次放進車上的 USB 或傳給家人，
        # 所以 MP3 那顆按鈕在分享頁比在後台更重要。轉不了就給 null，
        # 讓那一頁乾脆不要長出一顆按下去會壞的按鈕。
        "mp3_url": (f"/api/share/{token}/audio?format=mp3&download=1"
                    if (await _mp3_capability_async())["available"] else None),
    }


@app.get("/api/share/{token}/audio")
async def get_shared_audio(token: str, download: int = Query(0, ge=0, le=1),
                           format: str = Query("")):
    """
    分享出去的那一段聲音。`format=mp3` 給轉好的 MP3。

    只有 `download=1` 才記次數：播放一次不是一個請求（拖進度條會發 Range、
    Safari 會為同一個檔案再要一次），照請求數扣的話使用者拖一下就沒了。

    次數是**在拿到檔案之後**才記的：轉檔失敗卻扣掉一次下載，
    等於這個連結被一個沒成功的動作燒掉了。
    """
    share, entry = _resolve_share(token)
    path = recordings.path_for(share["recording_id"])
    if path is None:
        raise HTTPException(status_code=410, detail=SHARE_DEAD_MESSAGE["gone"])
    suffix = ""
    if str(format).lower() == "mp3":
        path = await _mp3_path_or_error(share["recording_id"])
        suffix = ".mp3"
    if download:
        share_links.note_download(token)
    return FileResponse(
        path,
        media_type="audio/mpeg" if suffix else (entry.get("mime") or "application/octet-stream"),
        filename=_recording_download_name(entry, suffix) if download else None,
    )


@app.get("/api/share/{token}/qr.png")
async def get_share_qrcode(token: str):
    """分享連結的 QR。手機掃一下就帶走，不用在群組裡貼一串亂碼網址。"""
    _resolve_share(token)
    qr = qrcode.QRCode(version=None, box_size=8, border=2)
    qr.add_data(f"{public_base_url()}/share/{token}")
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


@app.get("/share/{token}")
async def share_page(token: str):
    """
    掃 QR 進來的那一頁。

    這裡回的是**靜態檔**，資料由頁面自己打 `/api/share/{token}` 拿 ——
    歌名與演唱者是從 YouTube 抓回來的字串，直接嵌進 HTML 就等於把
    別人取的標題當程式碼跑。交給前端用 textContent 塞，這個洞就不存在。
    """
    page = FRONTEND_DIR / "share.html"
    if not page.is_file():
        raise HTTPException(status_code=404, detail="分享頁不存在")
    return FileResponse(page, media_type="text/html")


@app.post("/api/recordings/{rec_id}/pin")
async def pin_recording(rec_id: str, payload: Dict[str, Any] = Body(default={})):
    """標記保留（配額滿了也不會被自動清掉）。沒給 pinned 就當成切換。"""
    entry = recordings.get(rec_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="找不到這筆錄音")
    pinned = payload.get("pinned")
    target = (not entry.get("pinned")) if pinned is None else bool(pinned)
    return {"status": "success", "recording": recordings.set_pinned(rec_id, target)}


@app.delete("/api/recordings/{rec_id}")
async def delete_recording(rec_id: str):
    if not recordings.delete(rec_id):
        raise HTTPException(status_code=404, detail="找不到這筆錄音")
    # 錄音刪了，已經發出去的連結要立刻打不開 —— 留著只會讓掃過 QR 的人
    # 在幾天後看到一個轉不動的播放器，而不是一句「這一次已經不在了」。
    share_links.revoke_for_recording(rec_id, reason="gone")
    return {"status": "success", "stats": recordings.stats(**_recording_quota())}


@app.delete("/api/recordings")
async def clear_recordings(include_pinned: int = Query(0, ge=0, le=1)):
    """清空錄音。預設保留「標記保留」的那幾筆，要全刪得明確送 include_pinned=1。"""
    removed = recordings.clear(keep_pinned=not include_pinned)
    _prune_share_links()
    return {"status": "success", "removed": removed,
            "stats": recordings.stats(**_recording_quota())}


# --- 櫃檯管理鎖 ---
#
# 這幾條路由自己**不受鎖保護**（access_policy 的 OPEN 表上列著）：
# 鎖上之後連敲門的那扇門都鎖住的話，就沒有人解得開了。
# 每一條各自驗自己的憑據 —— 解鎖要 PIN，改設定要 PIN 或已解鎖的 token，
# 上鎖什麼都不要。


async def _broadcast_staff_lock() -> Dict[str, Any]:
    """鎖的狀態變了要讓每一支手機都知道（鎖頭要跟著變色）。回傳那份狀態。"""
    state = staff_lock.state()
    await ws_manager.broadcast({"type": "STAFF_LOCK", "data": state})
    return state


@app.get("/api/staff-lock")
async def get_staff_lock():
    """
    鎖的目前狀態。**不含 PIN 也不含 token**。

    前端每一頁載入時都問一次，好決定那些按鈕要不要掛上鎖頭圖示 ——
    按下去才發現要密碼，跟按下去才發現沒有權限，是兩種不同的難堪。
    """
    return {"lock": staff_lock.state()}


@app.post("/api/staff-lock/unlock")
async def unlock_staff_lock(payload: Dict[str, Any] = Body(...)):
    """打櫃檯密碼解鎖。成功回一組 token，之後的受保護動作帶 `X-Staff-Token`。"""
    result = staff_lock.unlock(payload.get("pin"))
    status = result.get("status")
    if status == "cooldown":
        raise HTTPException(status_code=429, detail=result.get("message", "請稍後再試"),
                            headers={"Retry-After": str(result.get("retry_after", 5))})
    if status == "denied":
        raise HTTPException(status_code=403, detail=result.get("message", "密碼錯誤"))
    if status == "not_enabled":
        # 沒啟用不是錯誤：問「要不要解鎖」的答案是「這台機器沒有鎖」。
        return {"status": "not_enabled", "lock": staff_lock.state()}
    state = await _broadcast_staff_lock()
    return {"status": "success", "token": result["token"],
            "expires_in": result["expires_in"], "lock": state}


@app.post("/api/staff-lock/lock")
async def lock_staff_lock():
    """立刻上鎖。刻意不需要任何憑據 —— 關門不需要鑰匙（見 staff_lock.py 第 3 點）。"""
    staff_lock.lock()
    return {"status": "success", "lock": await _broadcast_staff_lock()}


@app.post("/api/staff-lock/pin")
async def set_staff_pin(payload: Dict[str, Any] = Body(...),
                        staff_token: str = Header("", alias="X-Staff-Token")):
    """
    設定或更換櫃檯密碼（第一次設定就等於啟用這把鎖）。設完立刻回到上鎖狀態。

    還沒啟用時不需要憑據（那時候還沒有東西可保護）；已啟用要現行密碼
    或已解鎖的 token。
    """
    result = staff_lock.set_pin(payload.get("pin"),
                                current_pin=payload.get("current_pin"),
                                token=staff_token)
    if result.get("status") == "invalid":
        raise HTTPException(status_code=400, detail=result.get("message", "密碼格式不對"))
    if result.get("status") == "denied":
        raise HTTPException(status_code=403, detail=result.get("message", "要先解鎖"))
    return {"status": "success", "lock": await _broadcast_staff_lock()}


@app.post("/api/staff-lock/disable")
async def disable_staff_lock(payload: Dict[str, Any] = Body(default={}),
                             staff_token: str = Header("", alias="X-Staff-Token")):
    """停用這把鎖（回到家用模式）。要現行密碼或已解鎖的 token。"""
    result = staff_lock.disable(current_pin=payload.get("current_pin"), token=staff_token)
    if result.get("status") == "denied":
        raise HTTPException(status_code=403, detail=result.get("message", "要先解鎖"))
    return {"status": "success", "lock": await _broadcast_staff_lock()}


@app.post("/api/staff-lock/auto-lock")
async def set_staff_auto_lock(payload: Dict[str, Any] = Body(...),
                              staff_token: str = Header("", alias="X-Staff-Token")):
    """調整「閒置多久自動上鎖」。越界的分鐘數夾回範圍，不回 500。"""
    result = staff_lock.set_auto_lock_minutes(payload.get("minutes"),
                                              current_pin=payload.get("current_pin"),
                                              token=staff_token)
    if result.get("status") == "denied":
        raise HTTPException(status_code=403, detail=result.get("message", "要先解鎖"))
    return {"status": "success", "auto_lock_minutes": result["auto_lock_minutes"],
            "lock": await _broadcast_staff_lock()}


@app.get("/api/settings")
async def get_settings():
    """系統設定：目前值、預設值，以及讓前端長出表單的欄位規格。"""
    return {
        "settings": settings.all(),
        "defaults": default_settings(),
        "spec": SETTINGS_SPEC,
        "runtime": {
            # 模型是在伺服器啟動時載入的，設定改了要重開才生效，UI 要能提示
            "active_whisper_model": song_processor.whisper_model,
            "active_demucs_model": song_processor.demucs_model,
            "device": DEVICE,
            "version": __version__,
        },
    }


async def _broadcast_settings():
    await ws_manager.broadcast({"type": "SETTINGS_UPDATE", "data": settings.all()})


def _apply_session_gap(updated: Dict[str, Any]):
    """
    「一場的空檔」改了，每一間的擂台榜跟著改。

    系統裡「一場」只能有一個定義（settings.py），所以這個欄位同時是整晚打包、
    公平輪唱與今晚擂台的換場門檻 —— 三個地方各讀各的話，使用者會看到
    「打包說這是同一場、榜說換了一場」這種自相矛盾的畫面。
    """
    gap = updated.get("recording_session_gap_hours", DEFAULT_GAP_HOURS)
    for bundle in room_registry.items():
        bundle.contest.set_gap_hours(gap)


@app.post("/api/settings")
async def update_settings(payload: Dict[str, Any] = Body(...)):
    """更新設定。認不得的欄位與越界的值會被忽略／夾回範圍，不會回 500。"""
    updated = settings.update(payload)
    # 響度目標改了，之後新處理的歌要照新目標量測
    song_processor.loudness_target_lufs = updated.get("loudness_target_lufs", -14.0)
    _apply_session_gap(updated)
    await _broadcast_settings()
    return {"status": "success", "settings": updated}


@app.delete("/api/settings")
async def reset_settings():
    """一鍵恢復原廠設定。"""
    updated = settings.reset()
    song_processor.loudness_target_lufs = updated.get("loudness_target_lufs", -14.0)
    _apply_session_gap(updated)
    await _broadcast_settings()
    return {"status": "success", "settings": updated}


@app.post("/api/settings/apply-defaults")
async def apply_default_controls():
    """
    把設定頁的預設調音參數立刻套到現在的演唱狀態（不用重開伺服器）。

    **每一間都套**：這是機台的預設值（櫃檯管理鎖也是這樣分類的），
    只套一間的話，櫃檯把殘響調小之後只有 101 變了，而其他九間要等到
    下一次重開伺服器 —— 那個落差沒有任何畫面講得出來。
    """
    applied = {}
    for bundle in room_registry.items():
        applied = bundle.queue.apply_control_defaults()
    await broadcast_all_rooms()
    return {"status": "success", "applied": applied}


# --- 備份與還原 ---
#
# 這一區補的是「那一晚之後」：硬碟會壞、機器會換。在這之前，這台機器上累積
# 出來的東西（歌號簿、最愛、成績、音域、字幕校正、設定）只存在 cache/ 底下
# 那十幾個 JSON 檔裡，沒有任何一條路可以帶走它們。
#
# 清單、排除表與每一條的理由都在 backend/services/backup.py。這裡只有三件事
# 值得在路由層講：
#
# 1. **下載是 POST 不是 GET**，即使它讀多於寫。因為櫃檯管理鎖刻意不鎖唯讀
#    （access_policy.py：「看得到不會弄壞任何東西」），而這一份的內容是
#    **全店所有人的資料**，它不屬於「看看曲庫有多大」那一類。
# 2. **檢查與套用是兩支端點**。還原沒有 undo，所以「會發生什麼事」要能在
#    不寫入任何東西的前提下先看一次。
# 3. **/api/restore/status 是 GET、不鎖**：開機套用完之後，最需要看到那份報告
#    的人不一定手上有密碼，而報告本身只講「換了幾份」。
MAX_RESTORE_UPLOAD = backup.MAX_ARCHIVE_BYTES


@app.get("/api/backup")
async def backup_plan():
    """這份備份會裝什麼、不會裝什麼 —— 不用真的做一份就答得出來。"""
    plan = backup.describe_plan(CACHE_DIR)
    plan["pending"] = backup.pending_restore(CACHE_DIR)
    plan["last_restore"] = backup.last_report(CACHE_DIR)
    plan["safety_copies"] = backup.safety_copies(CACHE_DIR)
    plan["version"] = __version__
    return plan


@app.post("/api/backup")
async def download_backup():
    """
    打一份備份給人帶走。

    不落地：備份的用途是離開這台機器，而寫進 cache/ 的備份會跟著同一顆硬碟
    一起壞掉 —— 那正是它要防的事。曲庫清單從 library 拿（跟分類瀏覽、查歌、
    歌號同一份清單，不另外掃一次資料夾）。
    """
    try:
        rows = library.entries()
    except Exception as e:  # 曲庫掃描出事不該讓備份做不出來
        logger.warning(f"備份時取曲庫清單失敗: {e}")
        rows = []
    payload = backup.create_archive(CACHE_DIR, app_version=__version__, library=rows)
    name = backup.backup_filename(__version__)
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}"',
                 "Content-Length": str(len(payload))},
    )


def _cached_song_ids() -> List[str]:
    try:
        return [str(e.get("song_id")) for e in library.entries() if e.get("song_id")]
    except Exception:
        return []


async def _read_archive(request: Request) -> bytes:
    """上傳的備份檔（raw body，理由同錄音上傳）。先看 Content-Length 再讀。"""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_RESTORE_UPLOAD:
        raise HTTPException(status_code=413, detail="備份檔太大，這不像是 KaraTube 的備份")
    return await request.body()


@app.post("/api/restore/inspect")
async def inspect_restore(request: Request):
    """
    「按下去會發生什麼事」。**不寫任何東西**，所以可以放心按。

    壞檔回 400 而不是 500：拒絕都是「送進來的東西不合規」，不是伺服器壞了，
    而訊息本身就是要給人看的那一句（含下一步）。
    """
    try:
        return backup.inspect_archive(await _read_archive(request), CACHE_DIR,
                                      _cached_song_ids())
    except backup.BackupError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/restore")
async def stage_restore(request: Request):
    """
    確認還原：驗過之後攤進 cache/restore_pending/，**下次啟動時套用**。

    為什麼不是現在做，見 backend/services/backup.py 第二節（每個服務都在
    __init__ 把 JSON 讀進記憶體了，現在覆寫會被下一次寫入無聲地蓋回去）。
    """
    try:
        plan = backup.stage_restore(await _read_archive(request), CACHE_DIR,
                                    _cached_song_ids())
    except backup.BackupError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"status": "pending", "plan": plan,
            "message": "已排定還原。重新啟動 KaraTube 之後生效，"
                       "套用前會自動把現在的資料備份一份。"}


@app.delete("/api/restore")
async def cancel_restore():
    """取消還沒套用的還原。取消得掉正是「延後到開機」換來的東西。"""
    removed = backup.cancel_restore(CACHE_DIR)
    return {"status": "success" if removed else "empty",
            "message": "已取消待套用的還原。" if removed else "目前沒有待套用的還原。"}


@app.get("/api/restore/status")
async def restore_status():
    """待套用的還原，以及上一次還原的結果（還原發生在沒有人看著的開機那一刻）。"""
    return {
        "pending": backup.pending_restore(CACHE_DIR),
        "last_restore": backup.last_report(CACHE_DIR),
        "safety_copies": backup.safety_copies(CACHE_DIR),
    }


@app.post("/api/backup/safety/{name}")
async def download_safety_copy(name: str):
    """
    還原前自動留下的那一份現況備份。

    存在的理由是「還原錯一份備份」—— 按下還原的人通常正在慌，而慌的時候
    最容易做的錯事就是選錯檔案。這條路是把現況那一份拿回來，重新還原它。
    """
    path = backup.safety_path(CACHE_DIR, name)
    if path is None:
        raise HTTPException(status_code=404, detail="找不到這一份現況備份")
    return FileResponse(path, media_type="application/zip", filename=path.name)


@app.get("/api/songs/{song_id}/loudness")
async def get_song_loudness(song_id: str):
    """
    這首歌該套多少增益（自動音量平衡）。

    metadata 沒有響度資料時（設定啟用前就快取好的舊歌）現場量一次並補寫回去，
    所以舊曲庫不用整批重跑流水線也能享受音量平衡。
    量不到就回 0 dB —— 寧可不動，也不要亂調。
    """
    target = float(settings.get("loudness_target_lufs", -14.0))
    enabled = bool(settings.get("loudness_normalize", True))
    meta = storage.get_song_metadata(song_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="快取中沒有這首歌")

    info = meta.get("loudness")
    if not info or info.get("lufs") is None:
        inst_path = SONGS_DIR / song_id / "instrumental.mp3"
        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(None, analyze_audio_file, inst_path, target)
        if info:
            storage.update_song_metadata(song_id, {"loudness": info})

    if not info or info.get("lufs") is None:
        return {"song_id": song_id, "enabled": enabled, "gain_db": 0.0,
                "lufs": None, "target_lufs": target, "measured": False}

    # 目標響度可能在量測之後被改過，所以增益一律照當下的設定重算
    gain_db = gain_db_for_target(info["lufs"], target, info.get("peak_dbfs"))
    return {
        "song_id": song_id,
        "enabled": enabled,
        "gain_db": gain_db if enabled else 0.0,
        "lufs": info["lufs"],
        "peak_dbfs": info.get("peak_dbfs"),
        "target_lufs": target,
        "measured": True,
    }


@app.get("/api/songs/{song_id}/lyrics")
async def get_lyrics(song_id: str):
    # 偏移跟著歌詞一起回：舞台換歌時本來就會抓這一支，多一個欄位就不必多打一次
    # 請求（不過真正沒有空窗的那條路是 STATE_UPDATE 上的 song_lyric_offset_ms ——
    # 那一份跟 current_song 同一則訊息抵達）。
    lyrics = storage.get_song_lyrics(song_id)
    return {"song_id": song_id, "lyrics": lyrics,
            **lyric_offsets.calibration(song_id)}


# --- 每首歌的字幕校正 ---
#
# 「這首歌的 LRC 偏差」與「這台裝置的延遲」是兩個數字（見 lyric_offsets.py）：
# 這一區只管前者，而前者本身又是兩個數字 —— 「這首歌對不上」有兩種形狀：
# 整首平移（`offset_ms`）與越唱越歪（`rate`，兩點校正解出來的）。
#
# 裝置延遲那一個留在舞台那台的 localStorage 與音訊設定面板，並以唯讀的姿態
# 出現在共享狀態的 `lyric_offset_ms` 上（讓點歌台印得出來）。
#
# 這幾條**不鎖**（access_policy 的 OPEN 表）：字幕對不上時，包廂裡的人必須
# 能當場修好那一首，而它只影響一首歌的顯示、隨時可以歸零。


@app.get("/api/songs/{song_id}/lyric-offset")
async def get_song_lyric_offset(song_id: str):
    """這首歌的字幕校正。沒校正過回 0／1.0，並附上 entry=null 讓前端分得出「沒調過」。"""
    return {"song_id": song_id, **lyric_offsets.calibration(song_id),
            "entry": lyric_offsets.entry(song_id), "max_offset_ms": MAX_OFFSET_MS,
            "min_rate": MIN_RATE, "max_rate": MAX_RATE}


@app.post("/api/songs/{song_id}/lyric-offset")
async def set_song_lyric_offset(song_id: str, payload: Dict[str, Any] = Body(...)):
    """
    校正這首歌的字幕：偏移（毫秒，正值＝字幕延後）與／或速度倍率。越界一律夾回，
    不回 500 —— 這是一個「唱到一半發現字幕歪了」的動作，不該用錯誤打斷演唱。

    **沒帶到的欄位不會被動到。** 滑桿與方向鍵只送得出 `offset_ms`，如果把缺席
    當成「速度重設為 1.0」，兩點校正解出來的速度會在使用者下一次微調偏移時
    無聲消失，而症狀（後段又開始越唱越歪）跟「剛剛按錯方向」長得一模一樣。

    寫完廣播一次完整狀態，包廂裡每一支手機與舞台同一刻換值 —— 五個人看著
    同一面螢幕，校正只有一個版本。
    """
    applied = lyric_offsets.set_calibration(
        song_id,
        offset_ms=payload.get("offset_ms"),
        rate=payload.get("rate"),
    )
    await broadcast_all_rooms()
    return {"status": "success", "song_id": song_id, **applied}


@app.delete("/api/songs/{song_id}/lyric-offset")
async def clear_song_lyric_offset(song_id: str):
    """把這首歌的字幕校正歸零（偏移與速度都回到「沒有校正過」）。"""
    lyric_offsets.clear(song_id)
    await broadcast_all_rooms()
    return {"status": "success", "song_id": song_id, "offset_ms": 0, "rate": 1.0}


@app.get("/api/lyric-offsets")
async def list_lyric_offsets():
    """校正過的歌一覽（快取管理頁與「升級成本機基準」的確認框要用）。"""
    return {"offsets": lyric_offsets.all(),
            "calibrations": lyric_offsets.all_calibrations(),
            "count": lyric_offsets.count(),
            # 升級成本機基準只會動到「偏移不是 0」的那幾首：只解過速度的歌
            # 不在其中，算進去等於在確認框上多報幾首。
            "offset_count": lyric_offsets.offset_count(),
            "max_offset_ms": MAX_OFFSET_MS,
            "min_rate": MIN_RATE, "max_rate": MAX_RATE}


@app.post("/api/lyric-offsets/rebase")
async def rebase_lyric_offsets(payload: Dict[str, Any] = Body(...)):
    """
    「把這首歌的偏移升級成本機基準」的另一半：所有已校正的歌各減掉 `delta_ms`。

    裝置延遲 +X 之後，每一首已經校正過的歌都會多出 X 的誤差；不一起減掉的話
    會冒出一批「昨天好好的、今天突然歪了」的歌，而那是最難查的故障。
    裝置那一半是舞台自己寫進 localStorage 的，伺服器只負責這一半。
    """
    result = lyric_offsets.rebase(payload.get("delta_ms"))
    await broadcast_all_rooms()
    return {"status": "success", **result, "count": lyric_offsets.count()}

@app.get("/api/songs/{song_id}/pitch")
async def get_pitch(song_id: str):
    pitch = storage.get_song_pitch(song_id)
    return {"song_id": song_id, "pitch": pitch}


@app.get("/api/songs/{song_id}/sections")
async def get_song_sections(song_id: str):
    """
    練唱模式用的曲式分析：副歌在哪裡、整首怎麼分段。

    從已對齊的歌詞算，不碰音訊也不用模型，所以是即算即回、不需要快取。
    沒有歌詞（還在處理中、或這首歌根本沒抓到詞）就回空的結構，
    前端退回手動設 A-B 點，不會壞。
    """
    lyrics = storage.get_song_lyrics(song_id)
    meta = storage.get_song_metadata(song_id) or {}
    structure = analyze_song_structure(lyrics, duration=meta.get("duration"))
    return {"song_id": song_id, "duration": meta.get("duration"), **structure}

# --- WebSocket Hub ---

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket,
                             room: str = Query(default=rooms.DEFAULT_ROOM_ID),
                             desk: bool = Query(default=False)):
    """
    一條連線＝一間包廂。

    房號認不得就**直接關掉連線**（4404），不是靜靜地退回 default：
    退回去的話，那支手機會收到別間包廂的狀態、把歌點進別間，而兩邊都
    不會發現（見 rooms.py 決定三）。關掉則是前端立刻看得到的錯誤，
    而且它有得救 —— 重掃一次門口那張 QR。

    `desk=1` 是櫃檯那一頁：它照樣屬於自己選的那一間（要能點歌、切歌），
    另外**多收**每一間的服務鈴與計時提醒（見 DESK_FORWARD_TYPES）。
    """
    bundle = room_registry.get(room)
    if bundle is None:
        # accept 之前不能送 close reason，所以先接起來再關 —— 不這樣做的話
        # 瀏覽器只看得到一個沒有理由的 1006，而那跟「Wi-Fi 斷了」長得一樣。
        await websocket.accept()
        await websocket.send_text(json.dumps({
            "type": "ROOM_UNKNOWN",
            "data": {"room": room, "rooms": room_registry.rows()},
        }))
        await websocket.close(code=4404)
        return

    await ws_manager.connect(websocket, bundle.id, desk=desk)
    # Send initial state immediately
    await websocket.send_text(json.dumps({
        "type": "STATE_UPDATE",
        "room": bundle.id,
        "data": bundle.queue.get_full_state()
    }))
    # 舞台端的片頭卡秒數、結算畫面開關都在設定裡，一連上就要拿到
    await websocket.send_text(json.dumps({
        "type": "SETTINGS_UPDATE",
        "data": settings.all()
    }))
    # 櫃檯管理鎖的狀態同理：一連上就要拿到，不然新開的那一頁會先畫一個
    # 「沒有鎖」的畫面，幾秒後才跳成上鎖 —— 而那幾秒裡按下去的動作全部會被擋。
    await websocket.send_text(json.dumps({
        "type": "STAFF_LOCK",
        "data": staff_lock.state()
    }))
    # 包廂清單：點歌台要畫得出「現在在哪一間、可以切到哪幾間」。
    await websocket.send_text(json.dumps({
        "type": "ROOMS_UPDATE",
        "data": room_registry.rows()
    }))

    try:
        while True:
            data_text = await websocket.receive_text()
            try:
                msg = json.loads(data_text)
                msg_type = msg.get("type")

                if msg_type == "TIME_UPDATE":
                    # Stage screen syncs its current playback time (seconds) to remote clients
                    await ws_manager.broadcast(msg, room=bundle.id)
                elif msg_type == "CONTROL":
                    await bundle.queue.update_controls(msg.get("data", {}))
                elif msg_type == "SOUND_EFFECT":
                    await ws_manager.broadcast(msg, room=bundle.id)
                elif msg_type == "SONG_ENDED":
                    logger.info(f"[{bundle.id}] Song ended notification received from stage.")
                    await bundle.queue.skip_current()
                elif msg_type == "SCORE_EVENT":
                    await ws_manager.broadcast(msg, room=bundle.id)
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


# --- Media & Static Files ---

# Mount cached songs directory for audio/video streaming
app.mount("/media/songs", StaticFiles(directory=str(SONGS_DIR)), name="media_songs")

# Mount frontend files
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
