import os
from pathlib import Path

# Base Paths
BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = BASE_DIR / "cache"
SONGS_DIR = CACHE_DIR / "songs"
TEMP_DIR = CACHE_DIR / "temp"
FRONTEND_DIR = BASE_DIR / "frontend"

# Ensure runtime directories exist
SONGS_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR.mkdir(parents=True, exist_ok=True)

# Server Config
HOST = "0.0.0.0"
PORT = 8080

# AI Models & Processing Config
WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL", "small")
DEMUCS_MODEL = os.getenv("DEMUCS_MODEL", "htdemucs")

try:
    import torch
    DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
except ImportError:
    DEVICE = "cpu"

COMPUTE_TYPE = "float16" if DEVICE == "cuda" else "int8"
