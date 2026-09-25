"""
本機檔案的前處理 (Local Media Preparation)

YouTube 那條路上，`downloader.py` 交給流水線的是兩個檔案：`original_video.mp4`
與 `original_audio.mp3`。本機匯入要交出**一模一樣的兩個檔案**，後面的分離、
對詞、音高、響度才能原封不動地重用 —— 這個模組就是那一段轉換，僅此而已。

## 三個決定

**1. 影像失敗不算整首失敗。**
音訊是歌，影像只是背景。使用者的 mkv 裡可能包著一條瀏覽器播不動的視訊
（VP9 以外的怪編碼、10-bit HEVC、壞掉的索引），而那不該讓一首歌整個匯入失敗。
所以影像走「先試串流複製 → 不行就轉檔 → 再不行就放棄」，放棄之後那首歌
變成「沒有 MV」，情境背景會自動接手（跟純音檔匯入完全同一條路）。

**2. 影像優先串流複製，不重新編碼。**
`-c:v copy` 是搬 bytes，一首歌不到一秒；重新編碼一首 4 分鐘的 1080p 在 CPU 上
要好幾分鐘，而這台機器同時還要跑 Demucs。所以複製是預設，轉檔是退路，
而且轉檔時直接降到 720p —— 舞台是背景畫面，不是放映廳。

**3. 影像那一份不留音軌（`-an`）。**
舞台播的聲音永遠來自 `instrumental.mp3` 與 `vocals.mp3`，背景影片一律靜音。
把音軌一起複製進去只是讓那個檔案大上三成，而且多一條讓瀏覽器選錯軌的路。

`ffprobe` 讀出來的內嵌標題／演出者會回報給呼叫端 —— 從 CD 抓軌或從音樂庫
匯出的檔案裡，那兩個欄位比檔名可靠得多。
"""
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("KaraTube.LocalMedia")

# 逾時。本機檔案不用等網路，但 ffmpeg 讀到壞檔時會卡住不動，
# 而每一個卡住的 ffmpeg 都佔著一條執行緒。
PROBE_TIMEOUT_S = 30
AUDIO_TIMEOUT_S = 600
VIDEO_COPY_TIMEOUT_S = 600
VIDEO_ENCODE_TIMEOUT_S = 3600
THUMB_TIMEOUT_S = 60

# 重新編碼時的高度上限。舞台上那是背景，不是放映廳。
REENCODE_HEIGHT = 720


def ffmpeg_binary() -> str:
    """跟 transcoder.py 共用同一個環境變數，測試也靠它換成假的。"""
    return os.getenv("KARATUBE_FFMPEG", "").strip() or "ffmpeg"


def ffprobe_binary() -> str:
    return os.getenv("KARATUBE_FFPROBE", "").strip() or "ffprobe"


def build_probe_command(src: Path, binary: str = "") -> List[str]:
    """ffprobe 指令：問這個檔案有哪些軌、多長、內嵌標籤寫了什麼。"""
    return [
        binary or ffprobe_binary(), "-v", "error",
        "-print_format", "json",
        "-show_format", "-show_streams",
        str(src),
    ]


def parse_probe_output(text: str) -> Dict[str, Any]:
    """
    ffprobe 的 JSON → 我們要的幾個欄位。抽成純函式才測得到 ——
    這裡判錯的症狀是「明明有聲音卻說沒有音訊軌」，而那要真的有檔案才看得到。

    `duration` 優先取 format 那一層（容器層的長度），沒有才退回音訊軌自己的
    —— 有些 mkv 的視訊軌長度是壞的，而歌的長度要跟音訊一致。
    """
    out: Dict[str, Any] = {"duration": 0.0, "has_audio": False, "has_video": False,
                           "width": 0, "height": 0, "title": "", "artist": ""}
    try:
        data = json.loads(text or "{}")
    except (ValueError, TypeError):
        return out
    if not isinstance(data, dict):
        return out

    fmt = data.get("format") if isinstance(data.get("format"), dict) else {}
    try:
        out["duration"] = max(0.0, float(fmt.get("duration", 0) or 0))
    except (TypeError, ValueError):
        out["duration"] = 0.0
    tags = fmt.get("tags") if isinstance(fmt.get("tags"), dict) else {}
    for key, field in (("title", "title"), ("artist", "artist"), ("album_artist", "artist")):
        value = str(tags.get(key) or tags.get(key.upper()) or "").strip()
        if value and not out[field]:
            out[field] = value[:120]

    audio_duration = 0.0
    for stream in data.get("streams", []) or []:
        if not isinstance(stream, dict):
            continue
        codec_type = str(stream.get("codec_type") or "")
        if codec_type == "audio":
            out["has_audio"] = True
            try:
                audio_duration = max(audio_duration, float(stream.get("duration", 0) or 0))
            except (TypeError, ValueError):
                pass
        elif codec_type == "video":
            # 封面圖（專輯封面）會以「視訊軌」的樣子出現在 mp3／flac 裡，
            # 當成 MV 的話舞台會播一張不動的圖 —— 那正是情境背景要取代的東西。
            disposition = stream.get("disposition") or {}
            if isinstance(disposition, dict) and disposition.get("attached_pic"):
                continue
            out["has_video"] = True
            try:
                out["width"] = max(int(out["width"]), int(stream.get("width", 0) or 0))
                out["height"] = max(int(out["height"]), int(stream.get("height", 0) or 0))
            except (TypeError, ValueError):
                pass
    if out["duration"] <= 0:
        out["duration"] = audio_duration
    return out


def build_audio_command(src: Path, dst: Path, binary: str = "") -> List[str]:
    """
    抽音訊成 MP3。參數的理由跟 transcoder.build_command 同源：

      * `-vn` / `-map 0:a:0`：只要第一條音軌，不要封面圖也不要第二語言軌。
      * `-ar 44100`：下游的 Demucs 與 librosa 都會重採樣，這裡先統一，
        省得每一段各自猜。
      * `-f mp3`：輸出寫的是 `.part`，副檔名認不出格式（見 transcoder.py）。
      * `-nostdin`：不加的話 ffmpeg 會去搶 stdin，用 systemd 跑時會停在那裡。
    """
    return [
        binary or ffmpeg_binary(), "-y", "-nostdin", "-i", str(src),
        "-vn", "-map", "0:a:0", "-map_metadata", "-1",
        "-acodec", "libmp3lame", "-q:a", "2", "-ar", "44100",
        "-f", "mp3", str(dst),
    ]


def build_video_copy_command(src: Path, dst: Path, binary: str = "") -> List[str]:
    """串流複製成 mp4（不重新編碼）。`+faststart` 讓瀏覽器不必下載完才能播。"""
    return [
        binary or ffmpeg_binary(), "-y", "-nostdin", "-i", str(src),
        "-map", "0:v:0", "-an", "-sn", "-dn",
        "-c:v", "copy", "-movflags", "+faststart",
        "-f", "mp4", str(dst),
    ]


def build_video_encode_command(src: Path, dst: Path, binary: str = "",
                               height: int = REENCODE_HEIGHT) -> List[str]:
    """複製不成時的退路：轉成 H.264。降到 720p 並用 veryfast ——
    這台機器同時還要跑人聲分離。"""
    return [
        binary or ffmpeg_binary(), "-y", "-nostdin", "-i", str(src),
        "-map", "0:v:0", "-an", "-sn", "-dn",
        "-vf", f"scale=-2:min({int(height)}\\,ih)",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-f", "mp4", str(dst),
    ]


def build_thumbnail_command(src: Path, dst: Path, at_seconds: float = 10.0,
                            binary: str = "") -> List[str]:
    """
    抽一張畫面當歌卡縮圖。兩個參數位置是有意義的：

      * `-ss` 放在 `-i` **前面** —— 放後面會從頭解碼到那一秒，長片要等很久。
      * `-c:v mjpeg` 明講編碼器 —— 輸出檔名是 `.jpg.part`（原子換檔用），
        image2 muxer 從副檔名猜不出要編成 JPEG（見 transcoder.py 的同一個坑）。
    """
    return [
        binary or ffmpeg_binary(), "-y", "-nostdin",
        "-ss", f"{max(0.0, float(at_seconds)):.2f}", "-i", str(src),
        "-map", "0:v:0", "-frames:v", "1", "-vf", "scale=-2:360",
        "-c:v", "mjpeg", "-f", "image2", str(dst),
    ]


def _run(cmd: List[str], timeout: int) -> bool:
    """跑一個 ffmpeg 指令。成功回 True；失敗只記 log，由呼叫端決定要不要放棄。"""
    try:
        proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                              timeout=timeout, check=False)
    except FileNotFoundError:
        logger.error("找不到 ffmpeg/ffprobe，本機匯入需要它（安裝方式見部署說明）")
        return False
    except subprocess.TimeoutExpired:
        logger.warning(f"逾時（{timeout}s）: {' '.join(cmd[:3])}…")
        return False
    if proc.returncode != 0:
        tail = (proc.stderr or b"").decode("utf-8", "ignore").strip().splitlines()[-3:]
        logger.warning(f"ffmpeg 失敗（{proc.returncode}）: {' / '.join(tail)}")
        return False
    return True


def probe_media(src: Path, timeout: int = PROBE_TIMEOUT_S) -> Optional[Dict[str, Any]]:
    """
    讀檔案資訊。檔案壞掉、逾時回 None。

    **ffprobe 不存在是丟例外而不是回 None**：那不是「這個檔案有問題」，是
    「這台機器少裝了東西」，而兩者的下一步完全不同。回 None 的話畫面會對著
    每一個檔案說「讀不懂這個檔案」，使用者會去怪他的影片（精簡安裝的 ffmpeg
    沒有 ffprobe 是真的存在的，見 transcoder.py 的 libmp3lame 同一件事）。
    """
    try:
        proc = subprocess.run(build_probe_command(src), capture_output=True,
                              timeout=timeout, check=False)
    except FileNotFoundError as e:
        raise RuntimeError(
            "這台機器上找不到 ffprobe，沒辦法判讀本機影音檔"
            "（它通常跟 ffmpeg 裝在一起，安裝方式見部署說明）") from e
    except subprocess.TimeoutExpired:
        logger.warning(f"ffprobe 逾時: {src}")
        return None
    if proc.returncode != 0:
        return None
    return parse_probe_output((proc.stdout or b"").decode("utf-8", "ignore"))


def _atomic(dst: Path, work) -> bool:
    """
    先寫 `.part` 再改名。中途失敗留下的半份檔案，對下游來說是「檔案存在」——
    而 `is_song_complete()` 只看存不存在，那首歌會變成「完整但播不出聲音」。
    """
    dst = Path(dst)
    tmp = dst.with_suffix(dst.suffix + ".part")
    try:
        tmp.unlink()
    except OSError:
        pass
    ok = False
    try:
        ok = bool(work(tmp)) and tmp.exists() and tmp.stat().st_size > 0
        if ok:
            tmp.replace(dst)
        return ok
    finally:
        if not ok:
            try:
                tmp.unlink()
            except OSError:
                pass


def extract_audio(src: Path, dst: Path) -> bool:
    """抽出音訊成 MP3。這一步失敗 = 這個檔案沒有歌，整首匯入就該失敗。"""
    return _atomic(dst, lambda tmp: _run(build_audio_command(src, tmp), AUDIO_TIMEOUT_S))


def prepare_video(src: Path, dst: Path) -> bool:
    """
    準備背景影片。先串流複製，不行才轉檔，兩條都不行就回 False
    （那首歌變成沒有 MV，舞台改用情境背景 —— 不是錯誤）。
    """
    if _atomic(dst, lambda tmp: _run(build_video_copy_command(src, tmp), VIDEO_COPY_TIMEOUT_S)):
        return True
    logger.info(f"串流複製不成，改用重新編碼: {Path(src).name}")
    return _atomic(dst, lambda tmp: _run(build_video_encode_command(src, tmp),
                                         VIDEO_ENCODE_TIMEOUT_S))


def grab_thumbnail(src: Path, dst: Path, duration: float = 0.0) -> bool:
    """
    抽一張縮圖。抽不到不是錯誤（純音檔本來就沒有畫面）。

    取樣點放在整首的 1/4 處而不是固定第 10 秒：很多伴唱影片開頭是黑畫面或
    製作單位的卡片，而一張全黑的縮圖跟沒有縮圖一樣沒用。
    """
    at = max(1.0, float(duration) * 0.25) if duration and duration > 8 else 5.0
    return _atomic(dst, lambda tmp: _run(build_thumbnail_command(src, tmp, at),
                                         THUMB_TIMEOUT_S))
