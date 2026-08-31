import os
import io
import json
import socket
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Body, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, Response, FileResponse
import qrcode
from PIL import Image

from backend.config import FRONTEND_DIR, SONGS_DIR, CACHE_DIR, HOST, PORT, DEVICE
from backend.pipeline.song_processor import SongProcessor
from backend.services.storage import SongStorage
from backend.services.search_service import YouTubeSearchService
from backend.services.queue_manager import QueueManager
from backend.services.play_stats import PlayStats
from backend.services.favorites import Favorites
from backend.services.song_history import SongHistory
from backend.services.score_history import ScoreHistory

# Logging setup
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("KaraTube.Server")

app = FastAPI(title="KaraTube KTV Server", version="1.0.0")

# Enable CORS for local network and mobile devices
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Services
storage = SongStorage(SONGS_DIR)
search_service = YouTubeSearchService()
song_processor = SongProcessor()
play_stats = PlayStats(CACHE_DIR / "play_stats.json")
favorites = Favorites(CACHE_DIR / "favorites.json")
song_history = SongHistory(CACHE_DIR / "song_history.json")
score_history = ScoreHistory(CACHE_DIR / "score_history.json")

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
                             play_stats=play_stats, song_history=song_history)

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

# --- REST Endpoints ---

@app.get("/api/info")
async def get_server_info():
    ip = get_local_ip()
    return {
        "status": "online",
        "ip": ip,
        "port": PORT,
        "device": DEVICE,
        "web_url": f"http://{ip}:{PORT}",
        "player_url": f"http://{ip}:{PORT}/player.html"
    }

@app.get("/api/qrcode")
async def get_qrcode_image():
    ip = get_local_ip()
    target_url = f"http://{ip}:{PORT}"
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

    item = await queue_manager.add_song(url_or_id, title, artist, thumbnail, priority)
    return {"status": "success", "item": item}

@app.delete("/api/queue/{queue_id}")
async def remove_queue_item(queue_id: str):
    await queue_manager.remove_from_queue(queue_id)
    return {"status": "success"}

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


@app.get("/api/songs/{song_id}/lyrics")
async def get_lyrics(song_id: str):
    lyrics = storage.get_song_lyrics(song_id)
    return {"song_id": song_id, "lyrics": lyrics}

@app.get("/api/songs/{song_id}/pitch")
async def get_pitch(song_id: str):
    pitch = storage.get_song_pitch(song_id)
    return {"song_id": song_id, "pitch": pitch}

# --- WebSocket Hub ---

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    # Send initial state immediately
    await websocket.send_text(json.dumps({
        "type": "STATE_UPDATE",
        "data": queue_manager.get_full_state()
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
