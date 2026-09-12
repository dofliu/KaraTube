import os
from pathlib import Path

# Base Paths
BASE_DIR = Path(__file__).resolve().parent.parent
# 快取位置可用環境變數指定：容器裡要把它掛到 volume（`/data`），
# 不然重建映像就把整個曲庫洗掉了。相對路徑以專案根目錄為基準。
_cache_env = os.getenv("KARATUBE_CACHE_DIR", "").strip()
CACHE_DIR = (Path(_cache_env) if os.path.isabs(_cache_env)
             else BASE_DIR / (_cache_env or "cache"))
SONGS_DIR = CACHE_DIR / "songs"
TEMP_DIR = CACHE_DIR / "temp"
# 錄唱回放的音檔。跟歌曲快取放在同一個 CACHE_DIR 底下，
# 容器部署時掛同一個 volume 就一起保住了（見 docs/DEPLOYMENT.md）。
RECORDINGS_DIR = CACHE_DIR / "recordings"
FRONTEND_DIR = BASE_DIR / "frontend"

# Ensure runtime directories exist
SONGS_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR.mkdir(parents=True, exist_ok=True)


def _int_env(name: str, default: int) -> int:
    """環境變數轉整數。塞了怪東西就用預設值，不要讓機器開不起來。"""
    try:
        return int(str(os.getenv(name, "")).strip())
    except (TypeError, ValueError):
        return default


# Server Config
# 反向代理或 Docker port mapping 之後對外的 port 可能不是 8080，
# 而 QR code 與「手機點歌」網址是照這個值印出來的 —— 印錯手機就連不進來。
HOST = os.getenv("KARATUBE_HOST", "0.0.0.0")
PORT = _int_env("KARATUBE_PORT", 8080)
# 對外公告用的 port（走反向代理時填 80 / 443 那個）。沒設就跟監聽 port 相同。
PUBLIC_PORT = _int_env("KARATUBE_PUBLIC_PORT", PORT)
# 對外公告用的主機位址（有網域或固定內網 IP 時填）。沒設就自動偵測本機 IP。
PUBLIC_HOST = os.getenv("KARATUBE_PUBLIC_HOST", "").strip()

# AI Models & Processing Config
WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL", "small")
DEMUCS_MODEL = os.getenv("DEMUCS_MODEL", "htdemucs")

try:
    import torch
    DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
except ImportError:
    DEVICE = "cpu"

COMPUTE_TYPE = "float16" if DEVICE == "cuda" else "int8"
