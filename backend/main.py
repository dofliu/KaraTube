import asyncio
import io
import json
import socket
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Dict, Any, Tuple
from urllib.parse import quote

from fastapi import (FastAPI, WebSocket, WebSocketDisconnect, Query, Body, HTTPException,
                     Request)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import Response, FileResponse, StreamingResponse
import qrcode

from backend.config import (FRONTEND_DIR, SONGS_DIR, CACHE_DIR, RECORDINGS_DIR, PUBLIC_HOST,
                            PUBLIC_PORT, DEVICE)
from backend.pipeline.chorus_detector import analyze_song_structure
from backend.pipeline.loudness import analyze_audio_file, gain_db_for_target
from backend.pipeline.song_processor import SongProcessor
from backend.services.batch_scheduler import BatchScheduler, split_source_lines
from backend.services.storage import SongStorage
from backend.services.search_service import YouTubeSearchService
from backend.services.queue_manager import QueueManager
from backend.services.play_stats import PlayStats
from backend.services.favorites import Favorites
from backend.services.library import LANGUAGE_SPEC, NEW_SONG_DAYS, LibraryIndex
from backend.services.song_history import SongHistory
from backend.services import room_timer, song_quota
from backend.services.score_history import ScoreHistory
from backend.services.night_export import (DEFAULT_GAP_HOURS, ExportGate, filter_by_singer,
                                           find_session, group_sessions, iter_session_zip,
                                           zip_filename)
from backend.services.recordings import (HARD_MAX_UPLOAD_BYTES, RecordingLibrary,
                                         mp3_cache_limit, suffix_for_mime)
from backend.services.share_links import ShareLinkStore, clamp_max_downloads, clamp_ttl_hours
from backend.services.transcoder import (TranscodeGate, clamp_bitrate, probe_ffmpeg,
                                         reset_probe_cache, transcode_to_mp3)
from backend.services.settings import SETTINGS_SPEC, SystemSettings, default_settings
from backend.version import __version__, version_info

# Logging setup
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("KaraTube.Server")

# 包廂計時的心跳。5 秒一次就夠：提醒的門檻是「剩 10 分鐘」這種尺度，
# 差五秒沒有人看得出來，而每秒醒來一次是拿 CPU 換一個沒有人要的精確度
# （畫面上那個每秒跳一次的倒數是前端自己跑的，不靠這個迴圈）。
ROOM_TICK_SECONDS = 5


async def room_timer_loop():
    """
    定期推進包廂計時，把剛跨過的提醒廣播出去。

    提醒不能等到「下一次有人操作」才發現：包廂最安靜的時候正是快唱完的時候，
    而那正是最需要聽到「剩十分鐘」的時候。
    """
    while True:
        try:
            await asyncio.sleep(ROOM_TICK_SECONDS)
            for alert in await queue_manager.tick_room():
                await ws_manager.broadcast({
                    "type": "ROOM_ALERT",
                    "data": {**alert, "room": queue_manager.room_state()},
                })
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # 這個迴圈死掉的話倒數就永遠停在那裡，而且沒有人看得出來，
            # 所以任何一次失敗都只記錄、不中斷。
            logger.warning(f"包廂計時心跳失敗: {e}")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """
    背景工作的生老病死。

    排程預處理與包廂計時的迴圈都要有 event loop 才能建立，所以不能在模組層級
    start()；關機時要記得 cancel，否則 uvicorn --reload 每存一次檔就多一個迴圈在跑。
    """
    batch_scheduler.start()
    room_task = asyncio.create_task(room_timer_loop())
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

# Enable CORS for local network and mobile devices
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Services
# 設定要先讀起來 —— 模型選擇與響度目標在建構流水線時就要用到
settings = SystemSettings(CACHE_DIR / "settings.json")
storage = SongStorage(SONGS_DIR)
search_service = YouTubeSearchService()
song_processor = SongProcessor(
    whisper_model=settings.get("whisper_model"),
    demucs_model=settings.get("demucs_model"),
    loudness_target_lufs=settings.get("loudness_target_lufs", -14.0),
)
play_stats = PlayStats(CACHE_DIR / "play_stats.json")
favorites = Favorites(CACHE_DIR / "favorites.json")
song_history = SongHistory(CACHE_DIR / "song_history.json")
score_history = ScoreHistory(CACHE_DIR / "score_history.json")
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
# 曲庫分類瀏覽（語言/歌手）、新歌榜與推薦歌單，全部從快取資料夾即算即回
library = LibraryIndex(storage, play_stats=play_stats, song_history=song_history)

# WebSocket Connection Manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket client connected. Total clients: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"WebSocket client disconnected. Total clients: {len(self.active_connections)}")

    async def broadcast(self, message: Dict[str, Any]):
        message_json = json.dumps(message)
        dead_connections = []
        for connection in self.active_connections:
            try:
                await connection.send_text(message_json)
            except Exception:
                dead_connections.append(connection)
        for dead in dead_connections:
            self.disconnect(dead)

ws_manager = ConnectionManager()
# 包廂計時。存檔放在 cache/ —— 計時對應的是「客人買了多久」，不該因為
# 伺服器重開（或 --reload 存了一次檔）就重算或歸零。
room = room_timer.RoomTimer(CACHE_DIR / "room_timer.json")
queue_manager = QueueManager(song_processor, storage, broadcast_cb=ws_manager.broadcast,
                             play_stats=play_stats, song_history=song_history,
                             settings=settings, room=room)
# 開機就把設定頁的「預設調音參數」套進共享狀態，第一台連上來的裝置看到的就是設定值
queue_manager.apply_control_defaults()


def stage_is_busy() -> bool:
    """
    舞台正在忙嗎？排程預處理靠這個決定要不要讓開。

    「有人在唱歌」只是其中一種忙：佇列裡還有歌在跑流水線時也算 ——
    現場點的那首當然比半夜的批次任務優先，讓它獨佔 GPU 才會早點唱到。
    """
    if queue_manager.current_song is not None:
        return True
    return any(item.get("status") in ("PENDING", "PROCESSING")
               for item in queue_manager.queue)


batch_scheduler = BatchScheduler(
    song_processor, storage, CACHE_DIR / "batch_jobs.json",
    settings=settings, broadcast_cb=ws_manager.broadcast, busy_cb=stage_is_busy,
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


@app.get("/api/health")
async def health_check():
    """容器健康檢查用。只回報「服務起得來」，不碰模型也不碰磁碟。"""
    return {"status": "ok", "version": __version__}


@app.get("/api/info")
async def get_server_info():
    ip = PUBLIC_HOST or get_local_ip()
    base = public_base_url()
    return {
        "status": "online",
        "ip": ip,
        "port": PUBLIC_PORT,
        "device": DEVICE,
        "version": __version__,
        "web_url": base,
        "player_url": f"{base}/player.html"
    }

@app.get("/api/qrcode")
async def get_qrcode_image():
    target_url = public_base_url()
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

@app.get("/api/cache")
async def get_cache_overview():
    """快取管理總覽：每首歌的磁碟用量與完整性 + 磁碟剩餘空間。"""
    return {"songs": storage.list_cache_entries(), **storage.cache_stats()}


@app.delete("/api/cache/{song_id}")
async def delete_cached_song(song_id: str):
    """刪除一首快取歌曲（含壞資料夾）。演唱中或還在佇列裡的不能刪。"""
    if queue_manager.is_song_in_use(song_id):
        raise HTTPException(status_code=409, detail="歌曲演唱中或在佇列裡，不能刪除快取")
    if not storage.delete_song(song_id):
        raise HTTPException(status_code=404, detail="快取中沒有這首歌")
    return {"status": "success", "song_id": song_id}


@app.post("/api/cache/{song_id}/reprocess")
async def reprocess_cached_song(song_id: str):
    """砍掉快取重新跑整條流水線：處理壞掉的歌（字幕全歪、檔案缺漏）用。"""
    if queue_manager.is_song_in_use(song_id):
        raise HTTPException(status_code=409, detail="歌曲演唱中或在佇列裡，不能重新處理")
    song_dir = SONGS_DIR / song_id
    if not song_dir.exists():
        raise HTTPException(status_code=404, detail="快取中沒有這首歌")
    # 先留住舊 metadata 的顯示資訊，刪掉快取後排入佇列重新處理
    meta = storage.get_song_metadata(song_id) or {}
    storage.delete_song(song_id)
    item = await queue_manager.add_song(
        song_id,
        title=meta.get("title", ""),
        artist=meta.get("artist", ""),
        thumbnail=meta.get("thumbnail", ""),
    )
    return {"status": "success", "item": item}


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


@app.get("/api/queue")
async def get_queue():
    return queue_manager.get_full_state()

@app.post("/api/queue/add")
async def add_to_queue(payload: Dict[str, Any] = Body(...)):
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
            "room": queue_manager.room_state()}

@app.delete("/api/queue/{queue_id}")
async def remove_queue_item(queue_id: str):
    await queue_manager.remove_from_queue(queue_id)
    return {"status": "success"}

@app.post("/api/queue/{queue_id}/retry")
async def retry_queue_item(queue_id: str):
    """重新處理佇列裡狀態為 ERROR 的歌。"""
    item = await queue_manager.retry_item(queue_id)
    if item is None:
        raise HTTPException(status_code=404, detail="佇列裡沒有這首失敗的歌")
    return {"status": "success", "item": item}

@app.post("/api/queue/reorder")
async def reorder_queue(payload: Dict[str, int] = Body(...)):
    from_idx = payload.get("from_idx", 0)
    to_idx = payload.get("to_idx", 0)
    await queue_manager.reorder_queue(from_idx, to_idx)
    return {"status": "success"}

@app.get("/api/rotation")
async def get_rotation():
    """目前的輪序：每一首的輪次、每個人唱了幾首／還有幾首。"""
    state = queue_manager.get_full_state()
    return {"enabled": state["rotation_enabled"], **state["rotation"]}

@app.get("/api/quota")
async def get_quota():
    """
    目前的點歌額度：上限是多少、誰排了幾首、誰滿了。

    跟 /api/rotation 分開，因為它們是獨立的兩條規則：輪唱管**順序**
    （一個人連點五首時其他人不必等完那五首），額度管**量**
    （那五首本來就不該同時排在佇列裡）。先到先唱的包廂也可能只想要後面那一條。
    """
    state = queue_manager.get_full_state()
    return state["quota"]


@app.post("/api/rotation/reset")
async def reset_rotation():
    """
    輪序歸零。換一批客人（但機器沒關）、或是大家講好重新排時按的。

    刻意不動佇列：已經排好的順序是大家看著排出來的，歸零的是「誰已經唱過幾首」
    這份統計，下一首新點的歌才照新的輪次排。
    """
    summary = await queue_manager.reset_rotation()
    return {"status": "success", "enabled": queue_manager.rotation_enabled, **summary}

@app.get("/api/room")
async def get_room_timer():
    """
    包廂計時：這一場買了多久、用掉多久、還剩多久、時間到會怎麼處理。

    每一次 STATE_UPDATE 也帶著同一份資料（`state["room"]`），這支端點是給
    不想開 WebSocket 的呼叫端（外掛的櫃檯看板、腳本）用的。
    """
    return queue_manager.room_state()


@app.post("/api/room/start")
async def start_room_timer(payload: Dict[str, Any] = Body(default={})):
    """
    開始計時（歸零重算）。`minutes` 不給就用設定頁的預設長度。

    已經在計時的時候按它是**重開一場**，不是續時 —— 續時請用 /api/room/extend。
    兩件事做成同一顆鍵的話，中途按錯就會把已經用掉的兩小時抹掉，
    而那兩小時是拿不回來的（沒有人記得剛剛是幾點開始的）。
    """
    return {"status": "success",
            "room": await queue_manager.start_room_session(payload.get("minutes"))}


@app.post("/api/room/extend")
async def extend_room_timer(payload: Dict[str, Any] = Body(default={})):
    """
    續時（加時間，不是重開一場）。停在「時間到」畫面時按它會自己接回去播下一首。
    """
    return {"status": "success",
            "room": await queue_manager.extend_room_session(payload.get("minutes"))}


@app.post("/api/room/pause")
async def pause_room_timer():
    """停錶（中場休息、餐點來了）。播放不受影響。"""
    return {"status": "success", "room": await queue_manager.pause_room_session()}


@app.post("/api/room/resume")
async def resume_room_timer():
    """繼續倒數。"""
    return {"status": "success", "room": await queue_manager.resume_room_session()}


@app.post("/api/room/stop")
async def stop_room_timer():
    """
    結束計時（這桌不要再被計時了）。

    刻意連「時間到停播」的旗標一起解除並接回去播：這顆鍵的意思是
    「不要再管時間了」，按完卻還停在散場畫面不肯播的話，沒有人找得到怎麼救回來。
    """
    return {"status": "success", "room": await queue_manager.stop_room_session()}


@app.post("/api/queue/skip")
async def skip_song():
    next_song = await queue_manager.skip_current()
    return {"status": "success", "next_song": next_song}

@app.post("/api/queue/restart")
async def restart_song():
    await queue_manager.restart_current()
    return {"status": "success"}

@app.post("/api/seek")
async def seek_playback(payload: Dict[str, Any] = Body(...)):
    """跳到指定秒數：進度條拖曳、段落跳轉、回到 A 點練唱都用這支。"""
    position = await queue_manager.seek_to(payload.get("position", 0))
    return {"status": "success", "position": position}

@app.post("/api/control")
async def control_playback(payload: Dict[str, Any] = Body(...)):
    await queue_manager.update_controls(payload)
    return {"status": "success", "state": queue_manager.get_full_state()}

@app.post("/api/sound-effect")
async def trigger_sound_effect(payload: Dict[str, str] = Body(...)):
    effect_name = payload.get("effect", "cheer")
    await ws_manager.broadcast({
        "type": "SOUND_EFFECT",
        "effect": effect_name
    })
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
async def get_history(limit: int = Query(50, ge=1, le=200)):
    """已唱歷史：最近實際上台演唱過的歌，最新的排最前面。"""
    return {
        "history": song_history.recent(limit),
        "today_count": song_history.today_count(),
        "total_count": song_history.total_count(),
    }


@app.delete("/api/history")
async def clear_history():
    song_history.clear()
    return {"status": "success"}


@app.post("/api/scores")
async def submit_score(payload: Dict[str, Any] = Body(...)):
    """唱畢結算：舞台端送來總分，回傳含個人最佳與擊敗比例的結算資料。"""
    result = score_history.record(payload)
    if result is None:
        raise HTTPException(status_code=400, detail="Missing song_id or invalid score")
    # 廣播給所有端（點歌台 / 手機）同步顯示結算結果
    await ws_manager.broadcast({"type": "SCORE_FINAL", "data": result})
    return {"status": "success", "result": result}


@app.post("/api/scores/duet")
async def submit_duet_score(payload: Dict[str, Any] = Body(...)):
    """
    對唱模式的唱畢結算：兩位演唱者的成績一起送、一起記、廣播一則通知。

    形狀是 `{song_id, title, artist, thumbnail, a: {...}, b: {...}}`。
    分兩次呼叫 `/api/scores` 也能記到分，但中間斷線就會留下一場
    只有一個人的對唱，而且包廂裡每支手機會跳兩則結算通知。
    """
    result = score_history.record_duet(payload)
    if result is None:
        raise HTTPException(status_code=400, detail="Missing song_id / a / b or invalid score")
    await ws_manager.broadcast({"type": "SCORE_FINAL", "data": result})
    return {"status": "success", "result": result}


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


@app.post("/api/settings")
async def update_settings(payload: Dict[str, Any] = Body(...)):
    """更新設定。認不得的欄位與越界的值會被忽略／夾回範圍，不會回 500。"""
    updated = settings.update(payload)
    # 響度目標改了，之後新處理的歌要照新目標量測
    song_processor.loudness_target_lufs = updated.get("loudness_target_lufs", -14.0)
    await _broadcast_settings()
    return {"status": "success", "settings": updated}


@app.delete("/api/settings")
async def reset_settings():
    """一鍵恢復原廠設定。"""
    updated = settings.reset()
    song_processor.loudness_target_lufs = updated.get("loudness_target_lufs", -14.0)
    await _broadcast_settings()
    return {"status": "success", "settings": updated}


@app.post("/api/settings/apply-defaults")
async def apply_default_controls():
    """把設定頁的預設調音參數立刻套到現在的演唱狀態（不用重開伺服器）。"""
    applied = queue_manager.apply_control_defaults()
    await queue_manager.broadcast_state()
    return {"status": "success", "applied": applied}


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
    lyrics = storage.get_song_lyrics(song_id)
    return {"song_id": song_id, "lyrics": lyrics}

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
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    # Send initial state immediately
    await websocket.send_text(json.dumps({
        "type": "STATE_UPDATE",
        "data": queue_manager.get_full_state()
    }))
    # 舞台端的片頭卡秒數、結算畫面開關都在設定裡，一連上就要拿到
    await websocket.send_text(json.dumps({
        "type": "SETTINGS_UPDATE",
        "data": settings.all()
    }))

    try:
        while True:
            data_text = await websocket.receive_text()
            try:
                msg = json.loads(data_text)
                msg_type = msg.get("type")

                if msg_type == "TIME_UPDATE":
                    # Stage screen syncs its current playback time (seconds) to remote clients
                    await ws_manager.broadcast(msg)
                elif msg_type == "CONTROL":
                    await queue_manager.update_controls(msg.get("data", {}))
                elif msg_type == "SOUND_EFFECT":
                    await ws_manager.broadcast(msg)
                elif msg_type == "SONG_ENDED":
                    logger.info("Song ended notification received from stage player.")
                    await queue_manager.skip_current()
                elif msg_type == "SCORE_EVENT":
                    await ws_manager.broadcast(msg)
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)

# --- Media & Static Files ---

# Mount cached songs directory for audio/video streaming
app.mount("/media/songs", StaticFiles(directory=str(SONGS_DIR)), name="media_songs")

# Mount frontend files
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
