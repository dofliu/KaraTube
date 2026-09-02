import sys
import socket
import webbrowser
import uvicorn
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def print_banner(ip: str, port: int):
    banner = f"""
===================================================================
   🎤  KaraTube - YouTube 隨選即唱 KTV 伴唱系統  🎤
===================================================================

  [1] 點歌控制台 (Console / Controller):
      👉  http://localhost:{port}
      👉  http://{ip}:{port}

  [2] KTV 舞台演唱大螢幕 (Stage Screen / TV Output):
      👉  http://localhost:{port}/player.html
      👉  http://{ip}:{port}/player.html

  [3] 手機點歌 (LAN Mobile Remote):
      👉  在點歌台點擊「📱 手機點歌 QR」直接掃碼連線！

  ⚡ 支援特性:
     - YouTube / YouTube Music 即時點歌與背景分離
     - Demucs AI 人聲與純伴奏分離
     - WhisperX 毫秒級雙行變色歌詞
     - JOYSOUND / DAM 風格即時音準線與麥克風評分
     - 原唱/伴奏無段調節、升降 Key、空間殘響與歡呼音效

===================================================================
    """
    print(banner)

def main():
    port = 8080
    ip = get_local_ip()
    print_banner(ip, port)

    # Open console and player in browser
    try:
        webbrowser.open(f"http://localhost:{port}")
    except Exception:
        pass

    # Start FastAPI server via uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=port, log_level="info")

if __name__ == "__main__":
    main()
