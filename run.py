import os
import sys
import socket
import webbrowser
import uvicorn
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from backend.config import HOST, PORT, PUBLIC_HOST, PUBLIC_PORT  # noqa: E402
from backend.version import VERSION_CODENAME, __version__, build_id  # noqa: E402


def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def in_container() -> bool:
    """
    是不是跑在容器裡。

    容器裡沒有瀏覽器也沒有桌面，`webbrowser.open()` 會噴一堆錯誤訊息
    （或更糟：卡在某個 xdg-open 的逾時上），開機日誌看起來像壞了。
    """
    if os.getenv("KARATUBE_IN_CONTAINER", "").strip() == "1":
        return True
    return Path("/.dockerenv").exists()


def should_open_browser() -> bool:
    """`KARATUBE_OPEN_BROWSER=0/1` 可強制指定；沒指定就是「非容器才開」。"""
    flag = os.getenv("KARATUBE_OPEN_BROWSER", "").strip()
    if flag in ("0", "false", "no"):
        return False
    if flag in ("1", "true", "yes"):
        return True
    return not in_container()


def print_banner(ip: str, port: int):
    build = build_id()
    version_line = f"v{__version__}「{VERSION_CODENAME}」" + (f"  build {build}" if build else "")
    banner = f"""
===================================================================
   🎤  KaraTube - YouTube 隨選即唱 KTV 伴唱系統  🎤
   {version_line}
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
     - 導唱人聲隨演唱穩定度自動淡出（唱穩了退場，走音立刻回來）
     - 排程預處理：半夜整批把播放清單跑成伴奏＋字幕（有人唱歌就讓開）

===================================================================
    """
    print(banner)


def main():
    # 對外公告的位址優先（容器 / 反向代理情境），沒設才用自動偵測的內網 IP
    ip = PUBLIC_HOST or get_local_ip()
    print_banner(ip, PUBLIC_PORT)

    # Open console and player in browser
    if should_open_browser():
        try:
            webbrowser.open(f"http://localhost:{PUBLIC_PORT}")
        except Exception:
            pass

    # Start FastAPI server via uvicorn
    uvicorn.run("backend.main:app", host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
