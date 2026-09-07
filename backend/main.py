import asyncio
import io
import json
import socket
import logging
from typing import List, Dict, Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Body, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import Response
import qrcode

from backend.config import (FRONTEND_DIR, SONGS_DIR, CACHE_DIR, PUBLIC_HOST,
                            PUBLIC_PORT, DEVICE)
from backend.pipeline.chorus_detector import analyze_song_structure
from backend.pipeline.loudness import analyze_audio_file, gain_db_for_target
from backend.pipeline.song_processor import SongProcessor
from backend.services.storage import SongStorage
from backend.services.search_service import YouTubeSearchService
from backend.services.queue_manager import QueueManager
from backend.services.play_stats import PlayStats
from backend.services.favorites import Favorites
from backend.services.library import LANGUAGE_SPEC, NEW_SONG_DAYS, LibraryIndex
from backend.services.song_history import SongHistory
from backend.services.score_history import ScoreHistory
from backend.services.settings import SETTINGS_SPEC, SystemSettings, default_settings
from backend.version import __version__, version_info

# Logging setup
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("KaraTube.Server")

app = FastAPI(title="KaraTube KTV Server", version=__version__)

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
queue_manager = QueueManager(song_processor, storage, broadcast_cb=ws_manager.broadcast,
                             play_stats=play_stats, song_history=song_history,
                             settings=settings)
# 開機就把設定頁的「預設調音參數」套進共享狀態，第一台連上來的裝置看到的就是設定值
queue_manager.apply_control_defaults()

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

    item = await queue_manager.add_song(url_or_id, title, artist, thumbnail, priority,
                                        requested_by=requested_by)
    return {"status": "success", "item": item}

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


@app.get("/api/scores")
async def get_scores(limit: int = Query(50, ge=1, le=200)):
    """評分歷史：最近的演唱成績與每首歌的個人最佳。"""
    return {
        "scores": score_history.recent(limit),
        "bests": score_history.bests(),
        "total_count": score_history.total_count(),
    }


@app.get("/api/scores/{song_id}/best")
async def get_best_score(song_id: str):
    """單曲個人最佳。還沒唱過回傳 null，讓前端自己決定顯示。"""
    return {"song_id": song_id, "best": score_history.best_for(song_id)}


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
