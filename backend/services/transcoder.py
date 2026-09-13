"""
錄音轉檔成 MP3 (MP3 Transcoder)

錄音是瀏覽器的 MediaRecorder 錄的，容器由瀏覽器決定：Chrome/Firefox 給
webm(Opus)、Safari 給 mp4(AAC)。這兩種在包廂裡（用同一台瀏覽器播）完全沒問題，
但錄音真正的去處是**別的地方**：

  * 車機的 USB 播放清單 —— 大部分只吃 MP3；
  * 長輩手上的舊手機、平板、傳統 MP3 隨身聽；
  * LINE/微信傳過去之後對方直接點開（部分版本不認 webm）。

分享連結解決了「怎麼傳給我」，這一支解決的是傳過去之後**打不打得開**。
MP3 不是最好的格式，但它是唯一「在任何東西上都能播」的格式。

## 幾個刻意的選擇

**轉檔是用到才做，不是錄完就做。**
一個晚上錄三十首，實際會被帶走的大概三首。錄完就轉等於拿 CPU 去換
沒人要的檔案 —— 而那顆 CPU 同時在跑 Demucs 與下一首歌的下載。
所以轉檔在「有人按下 MP3」的那一刻才發生，轉完留著（下一次是秒回）。

**轉出來的 MP3 是可丟的快取，永遠不會擠掉任何一次演唱。**
它有自己的上限、自己的 LRU（見 recordings.py），刪掉了下次再轉一次就有。
反過來做（讓 MP3 佔錄音配額）的後果是：有人按了一下下載，
包廂裡最舊的那一次演唱就被擠掉了 —— 那一次是刪掉就沒有的東西。

**ffmpeg 不在就把功能收起來，不要讓使用者按下去才失敗。**
ffmpeg 是本專案的硬需求（下載與分離都要），但「硬需求」不等於「一定在」：
有人是手動裝的、有人的 ffmpeg 是精簡版沒有 libmp3lame（沒有 MP3 編碼器的
ffmpeg 是真的存在的，很多發行版為了授權把它拆出去）。兩種情況都要在
**畫面上**就講清楚，而不是轉一半吐一個 500。

**寫檔一定是先寫 .part 再改名。**
轉到一半被逾時砍掉的話，資料夾裡會留下一個「看起來是 MP3、播到一半斷掉」
的檔案。使用者的解讀是「這次的錄音壞了」，而不是「轉檔失敗」——
前者會讓他去刪掉那一次原始錄音。
"""
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("KaraTube.Transcoder")

# 允許用環境變數指定 ffmpeg（Windows 手動安裝、或容器裡放在非標準路徑）。
# 測試也靠這一點：塞一支假的 ffmpeg 進來就能在沒有 ffmpeg 的機器上跑完整流程。
def ffmpeg_binary() -> str:
    return os.getenv("KARATUBE_FFMPEG", "").strip() or "ffmpeg"


DEFAULT_BITRATE_KBPS = 192
MIN_BITRATE_KBPS = 96
MAX_BITRATE_KBPS = 320

# 轉檔逾時。一首 5 分鐘的歌在最慢的機器上也是十幾秒的事，
# 到 120 秒還沒完通常代表 ffmpeg 卡住了（讀到壞檔、等一個永遠不來的輸入）。
# 逾時要有上限的理由很實際：每一個轉檔都佔著一條執行緒。
TRANSCODE_TIMEOUT_S = 120

# 探測 ffmpeg 的逾時。探測只是叫它印一份編碼器清單，不該花到 10 秒。
PROBE_TIMEOUT_S = 10

# 探測結果的快取：成功就一直記著（ffmpeg 不會自己消失）。
# 失敗只記 60 秒 —— 管理員照著畫面上那句話把 ffmpeg 裝好之後，
# 不該還要重開伺服器才看得到按鈕。
PROBE_FAIL_TTL_S = 60

UNAVAILABLE_MESSAGE = {
    "not_installed": "這台機器上找不到 ffmpeg，沒辦法轉成 MP3（安裝方式見部署說明）",
    "no_lame": "這台機器的 ffmpeg 沒有內建 MP3 編碼器（libmp3lame），請改裝完整版",
    "probe_failed": "ffmpeg 叫不動，沒辦法轉成 MP3（請確認它能正常執行）",
}

_probe_lock = threading.Lock()
_probe_cache: Optional[Dict[str, Any]] = None
_probe_at = 0.0


def clamp_bitrate(value: Any) -> int:
    """位元率夾回合法範圍。看不懂就回預設值 —— 設定頁是給人用的。"""
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        return DEFAULT_BITRATE_KBPS
    return max(MIN_BITRATE_KBPS, min(MAX_BITRATE_KBPS, v))


def _clean_tag(value: Any) -> str:
    """
    ID3 標籤值。換行與控制字元砍掉 ——
    歌名是從 YouTube 抓回來的字串，裡面什麼都可能有，
    而 ffmpeg 的 metadata 參數是照 `key=value` 解析的。
    """
    text = "".join(ch for ch in str(value or "") if ch.isprintable())
    return text.replace("=", " ").strip()[:120]


def build_command(src: Path, dst: Path, bitrate_kbps: int = DEFAULT_BITRATE_KBPS,
                  tags: Optional[Dict[str, Any]] = None,
                  binary: str = "") -> List[str]:
    """
    轉檔指令。抽成純函式是為了能單獨測 —— 這串參數錯一個字，
    症狀是「下載回去的 MP3 打不開」，而那要真的下載才看得到。

    幾個參數的理由：
      * `-vn`：錄音裡不會有影像，但 mp4 容器可能帶封面圖，
        不擋掉的話 ffmpeg 會試著把它編成一軌，然後失敗。
      * `-map 0:a:0`：只取第一條音軌。對唱模式若來源有兩軌，
        不指定的話 ffmpeg 的預設選法會挑「最好的那一條」，換機器可能換答案。
      * `-ar 44100`：CD 取樣率。48kHz 的 MP3 是合法的，但老車機挑食。
      * `-nostdin`：不加的話 ffmpeg 會去搶 stdin，在某些 service 啟動方式下
        會讓它停在那裡等一個永遠不會有的輸入。
      * **`-f mp3`**：ffmpeg 是看**副檔名**決定輸出格式的，而我們寫的是
        `xxx.mp3.part`（原子換檔用的暫存檔），它認不出 `.part` 是什麼，
        直接以「Error initializing the muxer ... Invalid argument」收場。
        明講格式之後，輸出檔叫什麼名字都不影響結果。
      * ID3 標籤：車機螢幕上顯示的就是這個。沒有標籤的話一整排都是檔名，
        而檔名在很多車機上只顯示得下前 8 個字。
    """
    meta = tags or {}
    cmd = [
        binary or ffmpeg_binary(),
        "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-i", str(src),
        "-vn", "-map", "0:a:0",
        "-c:a", "libmp3lame",
        "-b:a", f"{clamp_bitrate(bitrate_kbps)}k",
        "-ar", "44100",
    ]
    for key, value in (("title", meta.get("title")), ("artist", meta.get("artist")),
                       ("album", meta.get("album")), ("date", meta.get("date"))):
        cleaned = _clean_tag(value)
        if cleaned:
            cmd += ["-metadata", f"{key}={cleaned}"]
    cmd += ["-f", "mp3", str(dst)]
    return cmd


def probe_ffmpeg(binary: str = "", force: bool = False) -> Dict[str, Any]:
    """
    這台機器能不能轉 MP3。回傳 `{"available": bool, "reason": str, "message": str}`。

    分兩件事問：ffmpeg 在不在（叫得動嗎），以及它有沒有 libmp3lame。
    第二件事很常被漏掉 —— 精簡版 ffmpeg 是存在的，而它失敗的樣子是
    「轉檔跑了兩秒然後回一個看不懂的錯」。先問清楚，畫面上才講得出人話。
    """
    global _probe_cache, _probe_at
    with _probe_lock:
        cached = _probe_cache
        if cached is not None and not force:
            fresh = cached.get("available") or (time.time() - _probe_at) < PROBE_FAIL_TTL_S
            if fresh:
                return dict(cached)

    result = _run_probe(binary or ffmpeg_binary())
    with _probe_lock:
        _probe_cache = result
        _probe_at = time.time()
    return dict(result)


def _run_probe(binary: str) -> Dict[str, Any]:
    try:
        proc = subprocess.run([binary, "-hide_banner", "-encoders"],
                              capture_output=True, timeout=PROBE_TIMEOUT_S)
    except FileNotFoundError:
        return {"available": False, "reason": "not_installed",
                "message": UNAVAILABLE_MESSAGE["not_installed"]}
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(f"ffmpeg 探測失敗: {e}")
        return {"available": False, "reason": "probe_failed",
                "message": UNAVAILABLE_MESSAGE["probe_failed"]}

    if proc.returncode != 0:
        return {"available": False, "reason": "probe_failed",
                "message": UNAVAILABLE_MESSAGE["probe_failed"]}
    listing = (proc.stdout or b"").decode("utf-8", "replace")
    if "libmp3lame" not in listing:
        return {"available": False, "reason": "no_lame",
                "message": UNAVAILABLE_MESSAGE["no_lame"]}
    return {"available": True, "reason": "", "message": ""}


def reset_probe_cache():
    """把探測結果忘掉（測試用，以及設定頁的「重新偵測」）。"""
    global _probe_cache, _probe_at
    with _probe_lock:
        _probe_cache = None
        _probe_at = 0.0


def transcode_to_mp3(src: Path, dst: Path, bitrate_kbps: int = DEFAULT_BITRATE_KBPS,
                     tags: Optional[Dict[str, Any]] = None,
                     binary: str = "",
                     timeout: int = TRANSCODE_TIMEOUT_S) -> Dict[str, Any]:
    """
    把一個錄音檔轉成 MP3。回傳 `{"status": "ok"|"failed", ...}`，不丟例外。

    先寫 `<檔名>.part` 再 `os.replace` 換上去：`os.replace` 在同一個檔案系統上
    是原子的，所以任何一個看得到 `xxx.mp3` 的人拿到的一定是完整的檔案。
    轉到一半失敗或逾時留下的 .part 當場刪掉。
    """
    src = Path(src)
    dst = Path(dst)
    if not src.is_file():
        return {"status": "failed", "reason": "missing_source", "message": "找不到原始錄音檔"}

    part = dst.with_name(dst.name + ".part")
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning(f"MP3 資料夾建立失敗: {e}")
        return {"status": "failed", "reason": "io_error", "message": "MP3 轉檔資料夾建不起來"}

    cmd = build_command(src, part, bitrate_kbps, tags, binary)
    started = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except FileNotFoundError:
        _discard(part)
        return {"status": "failed", "reason": "not_installed",
                "message": UNAVAILABLE_MESSAGE["not_installed"]}
    except subprocess.TimeoutExpired:
        # subprocess.run 逾時時會先砍掉子行程再丟例外，所以這裡不會留孤兒 ffmpeg
        _discard(part)
        logger.warning(f"MP3 轉檔逾時（{timeout}s）: {src.name}")
        return {"status": "failed", "reason": "timeout", "message": "MP3 轉檔逾時"}
    except (OSError, subprocess.SubprocessError) as e:
        _discard(part)
        logger.warning(f"MP3 轉檔啟動失敗: {e}")
        return {"status": "failed", "reason": "io_error", "message": "MP3 轉檔執行失敗"}

    if proc.returncode != 0:
        tail = (proc.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-3:]
        logger.warning(f"MP3 轉檔失敗（rc={proc.returncode}）: {' / '.join(tail)}")
        _discard(part)
        return {"status": "failed", "reason": "ffmpeg_error", "message": "MP3 轉檔失敗"}

    # rc=0 但沒有輸出是可能的（來源沒有音軌時某些版本會這樣收場）。
    # 不擋的話會換上一個 0 bytes 的 mp3，使用者下載到一個點不開的檔案。
    if not part.is_file() or part.stat().st_size <= 0:
        _discard(part)
        return {"status": "failed", "reason": "empty_output", "message": "MP3 轉檔沒有產生內容"}

    try:
        os.replace(part, dst)
    except OSError as e:
        _discard(part)
        logger.warning(f"MP3 換檔失敗: {e}")
        return {"status": "failed", "reason": "io_error", "message": "MP3 存檔失敗"}

    size = dst.stat().st_size
    logger.info(f"MP3 轉檔完成: {dst.name}（{size // 1024} KB, {time.time() - started:.1f}s）")
    return {"status": "ok", "bytes": size, "seconds": round(time.time() - started, 2)}


def _discard(path: Path):
    try:
        if path.is_file():
            path.unlink()
    except OSError as e:
        logger.warning(f"暫存檔刪不掉: {path.name} ({e})")


class TranscodeGate:
    """
    「同一筆同時只轉一次、整台機器同時只轉一個」。

    兩道關卡各自解決一個真實的問題：

      * **同一筆**：使用者按了 MP3 沒反應（其實在轉），於是又按一次。
        沒有這道關卡的話同一個目標檔會有兩個 ffmpeg 同時在寫。
      * **整台機器**：一桌人同時掃 QR 下載，五個 ffmpeg 一起跑會把 CPU 吃光 ——
        而那顆 CPU 正在放歌、算音準、跑下一首的分離。唱歌永遠比轉檔重要，
        所以寧可讓第五個人多等十秒。

    等太久要有結論（`wait_timeout`）：一個轉不完的請求掛在那裡，
    使用者看到的是轉圈圈的瀏覽器，那比一句「現在忙，等一下再按」更糟。
    """

    def __init__(self, max_concurrent: int = 1, wait_timeout: float = 180.0):
        self._slots = threading.BoundedSemaphore(max(1, int(max_concurrent)))
        self._wait_timeout = float(wait_timeout)
        self._keys_lock = threading.Lock()
        self._keys: Dict[str, threading.Lock] = {}

    def _lock_for(self, key: str) -> threading.Lock:
        with self._keys_lock:
            lock = self._keys.get(key)
            if lock is None:
                lock = threading.Lock()
                self._keys[key] = lock
            return lock

    def run(self, key: str, worker: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
        """
        在兩道鎖底下跑 `worker()`。

        `worker` **必須**自己先確認「是不是已經有人轉好了」——
        排在後面的那一個進來時，檔案通常已經在了，這時候該做的是直接回傳，
        而不是再轉一次蓋掉。
        """
        per_key = self._lock_for(key)
        if not per_key.acquire(timeout=self._wait_timeout):
            return {"status": "busy", "reason": "busy", "message": "這一筆正在轉檔中，請稍候再試"}
        try:
            if not self._slots.acquire(timeout=self._wait_timeout):
                return {"status": "busy", "reason": "busy",
                        "message": "轉檔排隊中（唱歌優先），請稍候再試"}
            try:
                return worker()
            finally:
                self._slots.release()
        finally:
            per_key.release()
