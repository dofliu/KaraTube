"""
錄唱回放 (Performance Recordings)

商用點歌機與線上 K 歌 App（全民K歌、唱吧）共同的一塊：把剛剛唱的那一次
**留下來**。分數說的是「唱得好不好」，錄音說的是「唱起來是什麼樣子」——
後者才是隔天還會被打開來聽的東西。

這支負責錄音檔的落地與生命週期，不碰錄音本身（錄音在舞台端的瀏覽器做，
見 frontend/js/take-recorder.js）：

  * 檔案存 `cache/recordings/`，索引存 `cache/recordings/index.json`。
    索引與檔案分開的原因是錄音檔動輒幾 MB，索引要能單獨快速讀出來給清單用。
  * **配額是這支的重點**。一首歌 3 分鐘的 Opus 大約 2~3 MB，一個晚上
    唱三十首就是 100 MB 上下，沒有上限的話幾個月後磁碟會無聲無息被吃光 ——
    而且吃光的是同一顆磁碟上的歌曲快取，下場是「歌曲下載到一半失敗」，
    使用者完全不會聯想到錄音。所以超過上限就從最舊的開始刪。
  * 但「最舊的先刪」會刪掉唱得最好的那一次。所以每一筆可以**標記保留**
    （pinned），保留的錄音不會被自動清掉；全部都保留而配額滿了就
    不再存新的，並明講原因 —— 悄悄不存是最糟的行為。

  * 另外管一份 **MP3 轉檔快取**（`cache/recordings/mp3/`）。錄音本身是瀏覽器
    錄出來的 webm/mp4，車機與舊播放器不一定打得開，所以可以轉一份 MP3 出來
    （轉檔本身在 transcoder.py）。這份 MP3 是**可丟的衍生資料**：
    有自己的上限、滿了從最久沒用的開始刪、刪掉了下次再轉就有。
    它刻意**不算進錄音配額**，因為「按一下下載就擠掉最舊的那一次演唱」
    是使用者無法理解也無法挽回的事 —— 演唱刪掉就沒有了，MP3 沒有。

安全性：錄音 id 由伺服器產生並用嚴格的格式檢查，不接受外部給的檔名。
`/api/recordings/{id}/audio` 直接把檔案送出去，id 若能塞進 `../` 就等於
把整台機器的檔案系統開放出去。
"""
import json
import logging
import re
import threading
from datetime import datetime
from pathlib import Path
from secrets import token_hex
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("KaraTube.Recordings")

# 伺服器自己產生的 id 格式：20260913-213045-a1b2c3
# 只認這個形狀，其他一律拒絕（路徑穿越、奇怪的副檔名都在這一關擋掉）。
RECORDING_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")

# MediaRecorder 在不同瀏覽器吐出來的容器不同：Chrome/Firefox 是 webm(Opus)，
# Safari 是 mp4(AAC)。副檔名照 mime 給，不然作業系統的播放器打不開。
MIME_SUFFIX = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/wave": ".wav",
    "audio/x-wav": ".wav",
}
DEFAULT_MIME = "audio/webm"

# 配額預設值。實際值由設定頁決定（main.py 每次上傳時把當下的設定傳進來），
# 這裡的常數是「呼叫端沒講」時的保守值。
DEFAULT_MAX_COUNT = 50
DEFAULT_MAX_BYTES = 512 * 1024 * 1024   # 512 MB

# 單一檔案的硬上限：一首歌再長也不會是 100 MB，超過通常代表送錯東西
# （或有人拿這支端點當檔案上傳空間用）。
HARD_MAX_UPLOAD_BYTES = 128 * 1024 * 1024

# MP3 轉檔快取放在錄音資料夾底下的子資料夾。
# 放子資料夾而不是跟錄音混在一起有個具體理由：`_reconcile()` 會把
# 「資料夾裡不在索引上的檔案」當孤兒檔刪掉，MP3 混在一起就得靠檔名去猜。
MP3_DIR_NAME = "mp3"

# MP3 快取的上限 = 錄音配額的這個比例。跟著配額走而不是寫死一個數字：
# 使用者把錄音上限調到 64 MB（小機器）時，快取不該還佔 256 MB。
# 它是**額外**佔的空間（不吃錄音配額），所以這個數字等於在說
# 「磁碟總量最多是錄音配額的 1.25 倍」，設定頁上要照實寫出來。
MP3_CACHE_RATIO = 0.25
MP3_CACHE_MIN_BYTES = 32 * 1024 * 1024


def mp3_cache_limit(max_bytes: int) -> int:
    """MP3 快取上限（bytes）。錄音配額設成不限（0）時給一個保守的固定值。"""
    if max_bytes and max_bytes > 0:
        return max(MP3_CACHE_MIN_BYTES, int(max_bytes * MP3_CACHE_RATIO))
    return 256 * 1024 * 1024


def normalize_mime(mime: str) -> str:
    """
    `audio/webm;codecs=opus` → `audio/webm`。

    瀏覽器送來的 mime 一定帶 codecs 參數，直接拿去查副檔名會查不到，
    存成 `.bin` 的錄音檔使用者下載回去就打不開了。
    """
    base = str(mime or "").split(";")[0].strip().lower()
    return base if base in MIME_SUFFIX else DEFAULT_MIME


def suffix_for_mime(mime: str) -> str:
    return MIME_SUFFIX.get(normalize_mime(mime), ".webm")


def _clean_number(value: Any, lo: float, hi: float) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v:  # NaN
        return None
    return max(lo, min(hi, v))


class RecordingLibrary:
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.mp3_dir = self.base_dir / MP3_DIR_NAME
        self.index_file = self.base_dir / "index.json"
        self._lock = threading.Lock()
        self._entries: List[Dict[str, Any]] = []
        self._mp3_seq = 0
        self._load()
        # 重開機之後序號要接在最大的那個之後，不然新轉好的那一份會是
        # 「最久沒用的」，下一次快取滿了第一個被刪的就是它。
        self._mp3_seq = max([int(e.get("mp3_seq") or 0) for e in self._entries] or [0])

    # --- 持久化 ---

    def _load(self):
        if not self.index_file.exists():
            return
        try:
            raw = json.loads(self.index_file.read_text(encoding="utf-8"))
            entries = raw.get("recordings", []) if isinstance(raw, dict) else []
        except Exception as e:
            logger.warning(f"錄音索引讀取失敗，改從資料夾重建: {e}")
            entries = []
        self._entries = [e for e in entries if isinstance(e, dict) and e.get("id")]
        self._reconcile()

    def _reconcile(self):
        """
        索引與資料夾對帳。兩個方向都會發生：

          * 索引有、檔案不在 —— 有人手動刪檔或磁碟滿了寫到一半失敗。
            留著只會讓清單上出現點下去沒聲音的項目。
          * 檔案在、索引沒有 —— 寫檔成功但索引還沒存就斷電。
            這種孤兒檔不刪的話永遠佔著空間，而且配額算不到它。
        """
        kept = []
        known_files = set()
        known_mp3 = set()
        for entry in self._entries:
            path = self.base_dir / str(entry.get("file", ""))
            if entry.get("file") and path.is_file():
                entry["bytes"] = path.stat().st_size
                self._reconcile_mp3(entry, known_mp3)
                kept.append(entry)
                known_files.add(path.name)
            else:
                logger.info(f"錄音檔不見了，從索引移除: {entry.get('id')}")
        self._entries = kept

        if not self.base_dir.is_dir():
            return
        for path in self.base_dir.iterdir():
            if path.name == self.index_file.name or not path.is_file():
                continue
            if path.name not in known_files:
                try:
                    path.unlink()
                    logger.info(f"清掉沒有索引的孤兒錄音檔: {path.name}")
                except OSError as e:
                    logger.warning(f"孤兒錄音檔刪不掉: {path.name} ({e})")

        # MP3 快取也要對帳，而且兩個方向都會發生：
        # 索引說有、檔案被人手動刪掉（那就標成沒有，下次重轉）；
        # 檔案在、沒有任何一筆索引指向它（上一輪轉到一半斷電、或那筆錄音已經被刪）。
        # 後者不收的話會一直佔著空間，而且 MP3 快取的用量會算不準。
        if self.mp3_dir.is_dir():
            for path in self.mp3_dir.iterdir():
                if not path.is_file() or path.name in known_mp3:
                    continue
                try:
                    path.unlink()
                    logger.info(f"清掉沒人認領的 MP3 快取: {path.name}")
                except OSError as e:
                    logger.warning(f"MP3 快取刪不掉: {path.name} ({e})")

    def _reconcile_mp3(self, entry: Dict[str, Any], known_mp3: set):
        """索引上的 MP3 還在不在。不在就把欄位清掉（下次有人要就重轉一份）。"""
        name = str(entry.get("mp3_file") or "")
        if not name:
            return
        path = self.mp3_dir / name
        if path.is_file():
            entry["mp3_bytes"] = path.stat().st_size
            known_mp3.add(path.name)
        else:
            for key in ("mp3_file", "mp3_bytes", "mp3_at", "mp3_used_at", "mp3_seq"):
                entry.pop(key, None)

    def _save(self):
        try:
            self.base_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "recordings": self._entries,
            }
            self.index_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"錄音索引寫入失敗: {e}")

    # --- 寫入 ---

    def save(self, audio: bytes, meta: Dict[str, Any],
             max_count: int = DEFAULT_MAX_COUNT,
             max_bytes: int = DEFAULT_MAX_BYTES) -> Dict[str, Any]:
        """
        存一次演唱。回傳 `{"status": "saved"|"rejected", ...}`。

        刻意不丟例外：錄音是「順手留下來」的功能，任何失敗都不該影響
        舞台的下一首歌，呼叫端拿到 rejected 就照原因顯示一行字即可。
        """
        if not audio:
            return {"status": "rejected", "reason": "empty", "message": "錄音內容是空的"}
        size = len(audio)
        if size > HARD_MAX_UPLOAD_BYTES:
            return {"status": "rejected", "reason": "too_large",
                    "message": f"單一錄音超過上限（{HARD_MAX_UPLOAD_BYTES // (1024 * 1024)} MB）"}
        if max_bytes > 0 and size > max_bytes:
            # 比整個配額還大：先刪光別人再存也還是超過，不如當場拒絕
            return {"status": "rejected", "reason": "over_quota",
                    "message": "這一次的錄音比錄音配額本身還大，請到設定頁調高上限"}

        song_id = str(meta.get("song_id") or "").strip()
        if not song_id:
            return {"status": "rejected", "reason": "missing_song", "message": "缺少 song_id"}

        mime = normalize_mime(meta.get("mime"))
        now = datetime.now()
        rec_id = f"{now:%Y%m%d-%H%M%S}-{token_hex(3)}"
        filename = f"{rec_id}{suffix_for_mime(mime)}"

        entry = {
            "id": rec_id,
            "file": filename,
            "mime": mime,
            "bytes": size,
            "song_id": song_id,
            "title": str(meta.get("title") or ""),
            "artist": str(meta.get("artist") or ""),
            "thumbnail": str(meta.get("thumbnail") or ""),
            "singer": str(meta.get("singer") or ""),
            "mode": "duet" if str(meta.get("mode")) == "duet" else "solo",
            "duration_ms": int(_clean_number(meta.get("duration_ms"), 0, 6 * 3600 * 1000) or 0),
            "score": int(_clean_number(meta.get("score"), 0, 1_000_000) or 0),
            "grade": str(meta.get("grade") or ""),
            "accuracy": round(_clean_number(meta.get("accuracy"), 0.0, 1.0) or 0.0, 4),
            "pinned": False,
            "created_at": now.isoformat(timespec="seconds"),
        }

        with self._lock:
            self.base_dir.mkdir(parents=True, exist_ok=True)
            path = self.base_dir / filename
            try:
                path.write_bytes(audio)
            except OSError as e:
                logger.warning(f"錄音寫檔失敗: {e}")
                return {"status": "rejected", "reason": "io_error", "message": "錄音存檔失敗"}
            self._entries.append(entry)
            evicted, blocked = self._enforce_quota(max_count, max_bytes, protect_id=rec_id)
            if blocked:
                # 舊的全都標記保留、配額又滿了：把剛寫進去的收回來。
                # 存下來再讓下一次把它擠掉是最糟的組合 —— 使用者看到「已儲存」，
                # 回頭卻找不到那一次。
                self._remove_entry(entry)
                self._save()
                return {"status": "rejected", "reason": "quota_pinned",
                        "message": "保留中的錄音已佔滿配額，請取消幾筆保留或調高上限"}
            self._save()

        return {"status": "saved", "recording": dict(entry), "evicted": evicted}

    def _enforce_quota(self, max_count: int, max_bytes: int,
                       protect_id: str = "") -> Tuple[int, bool]:
        """
        超過配額就從最舊的開始刪，標記保留的跳過。回傳 (刪了幾筆, 是否仍超標)。

        `protect_id` 是剛存進來的那一筆。不排除它的話，當舊的全被標記保留時
        「最舊的可刪項目」就是它自己 —— 存進去再馬上刪掉，而且回報存檔成功。

        呼叫端必須已經持有鎖。
        """
        evicted = 0
        while True:
            over_count = max_count > 0 and len(self._entries) > max_count
            over_bytes = max_bytes > 0 and self._total_bytes() > max_bytes
            if not (over_count or over_bytes):
                return evicted, False
            victim = next((e for e in self._entries
                           if not e.get("pinned") and e.get("id") != protect_id), None)
            if victim is None:
                return evicted, True   # 刪不動了，照實說
            self._remove_entry(victim)
            evicted += 1

    def _remove_entry(self, entry: Dict[str, Any]):
        path = self.base_dir / str(entry.get("file", ""))
        try:
            if path.is_file():
                path.unlink()
        except OSError as e:
            logger.warning(f"錄音檔刪除失敗: {path.name} ({e})")
        # 轉好的 MP3 跟著走。留著就是一個沒有主人的檔案：
        # 清單上看不到、配額算不到，只有磁碟知道它在。
        self._drop_mp3(entry)
        self._entries = [e for e in self._entries if e.get("id") != entry.get("id")]

    def _drop_mp3(self, entry: Dict[str, Any]) -> int:
        """刪掉一筆的 MP3 快取，回傳釋出的 bytes。呼叫端必須已經持有鎖。"""
        name = str(entry.get("mp3_file") or "")
        freed = int(entry.get("mp3_bytes") or 0)
        if name:
            path = self.mp3_dir / name
            try:
                if path.is_file():
                    freed = path.stat().st_size
                    path.unlink()
            except OSError as e:
                logger.warning(f"MP3 快取刪除失敗: {name} ({e})")
        for key in ("mp3_file", "mp3_bytes", "mp3_at", "mp3_used_at", "mp3_seq"):
            entry.pop(key, None)
        return freed if name else 0

    def _total_bytes(self) -> int:
        return sum(int(e.get("bytes") or 0) for e in self._entries)

    # --- 讀取 ---

    def list_all(self, limit: int = 100, song_id: str = "",
                 singer: str = "") -> List[Dict[str, Any]]:
        """最近錄的排最前面。可依歌曲或演唱者篩選。"""
        with self._lock:
            items = list(self._entries)
        if song_id:
            items = [e for e in items if e.get("song_id") == song_id]
        if singer:
            items = [e for e in items if e.get("singer") == singer]
        return [dict(e) for e in reversed(items[-limit:])] if limit > 0 else \
               [dict(e) for e in reversed(items)]

    def get(self, rec_id: str) -> Optional[Dict[str, Any]]:
        if not RECORDING_ID_RE.match(str(rec_id or "")):
            return None
        with self._lock:
            for entry in self._entries:
                if entry.get("id") == rec_id:
                    return dict(entry)
        return None

    def path_for(self, rec_id: str) -> Optional[Path]:
        """
        錄音檔的實際路徑。找不到（或 id 形狀不對）回傳 None。

        路徑一律用索引裡的檔名組出來，而不是把 id 拼進路徑：
        檔名是伺服器寫進去的，id 是外面送進來的。
        """
        entry = self.get(rec_id)
        if entry is None:
            return None
        path = self.base_dir / str(entry.get("file", ""))
        # 最後一道：組出來的路徑一定要還在錄音資料夾底下
        try:
            path.resolve().relative_to(self.base_dir.resolve())
        except ValueError:
            logger.warning(f"錄音路徑超出錄音資料夾，拒絕提供: {rec_id}")
            return None
        return path if path.is_file() else None

    # --- MP3 轉檔快取 ---
    #
    # 這一段只管檔案的帳（在哪裡、多大、誰最久沒用到），真正的轉檔在
    # transcoder.py。分開的理由是轉檔要開子行程、要逾時、要跑在執行緒裡，
    # 而這支是一個「拿著鎖改索引」的資料結構 —— 兩種東西混在一起會很難測。

    def mp3_path_for(self, rec_id: str) -> Optional[Path]:
        """已經轉好的 MP3。還沒轉（或檔案不見了）回傳 None。"""
        entry = self.get(rec_id)
        if entry is None or not entry.get("mp3_file"):
            return None
        path = self.mp3_dir / str(entry["mp3_file"])
        return path if path.is_file() else None

    def mp3_target_for(self, rec_id: str) -> Optional[Path]:
        """
        MP3 該被寫到哪裡。id 不合法（或查無此筆）回傳 None。

        檔名直接用 id：id 的形狀已經被 `RECORDING_ID_RE` 釘死成
        `20260913-213045-a1b2c3`，沒有斜線也沒有點，拼進路徑是安全的。
        """
        if self.get(rec_id) is None:
            return None
        return self.mp3_dir / f"{rec_id}.mp3"

    def register_mp3(self, rec_id: str, max_bytes: int = 0) -> Optional[Dict[str, Any]]:
        """
        轉檔完成後把這一份 MP3 記進索引，並執行快取上限。

        `max_bytes` 是 MP3 快取自己的上限（不是錄音配額）。超過就從
        **最久沒被用到**的那一份開始刪 —— 用 LRU 而不是「最舊的錄音」，
        因為一首三個月前的老歌如果每週都有人下載，它才是該留著的那一份。
        """
        target = self.mp3_target_for(rec_id)
        if target is None or not target.is_file():
            return None
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock:
            entry = next((e for e in self._entries if e.get("id") == rec_id), None)
            if entry is None:
                return None
            entry["mp3_file"] = target.name
            entry["mp3_bytes"] = target.stat().st_size
            entry["mp3_at"] = now
            entry["mp3_used_at"] = now
            entry["mp3_seq"] = self._next_mp3_seq()
            self._enforce_mp3_cap(max_bytes, protect_id=rec_id)
            self._save()
            return dict(entry)

    def touch_mp3(self, rec_id: str):
        """記下「這一份剛剛被用到」。LRU 要靠它才知道誰該留。"""
        with self._lock:
            for entry in self._entries:
                if entry.get("id") == rec_id and entry.get("mp3_file"):
                    entry["mp3_used_at"] = datetime.now().isoformat(timespec="seconds")
                    entry["mp3_seq"] = self._next_mp3_seq()
                    self._save()
                    return

    def _next_mp3_seq(self) -> int:
        """
        LRU 的排序鍵。刻意用遞增的序號而不是時間戳：

          * 同一秒內連按兩次（在真的使用情境下很常見）時間戳分不出先後；
          * 時間會倒退 —— NTP 校時、使用者改系統時間，剛用過的那一份
            會突然變成「最久沒碰的」而被刪掉。序號沒有這兩個問題。

        呼叫端必須已經持有鎖。
        """
        self._mp3_seq += 1
        return self._mp3_seq

    def _enforce_mp3_cap(self, max_bytes: int, protect_id: str = "") -> int:
        """
        MP3 快取超過上限就從最久沒用的開始丟。回傳丟了幾份。

        `protect_id` 是剛轉好的那一份：不保護它的話，當它自己就比上限還大時
        會被當場刪掉，使用者按了下載卻拿到 404。留著它（單一份超標）是
        刻意的取捨 —— 下一次有人轉別的，它就會變成最久沒用的那一個。

        呼叫端必須已經持有鎖。
        """
        if not max_bytes or max_bytes <= 0:
            return 0
        dropped = 0
        while self._mp3_bytes() > max_bytes:
            victims = [e for e in self._entries
                       if e.get("mp3_file") and e.get("id") != protect_id]
            if not victims:
                return dropped
            victim = min(victims, key=lambda e: int(e.get("mp3_seq") or 0))
            self._drop_mp3(victim)
            dropped += 1
        return dropped

    def _mp3_bytes(self) -> int:
        return sum(int(e.get("mp3_bytes") or 0) for e in self._entries
                   if e.get("mp3_file"))

    def clear_mp3_cache(self) -> Dict[str, int]:
        """把轉好的 MP3 全部丟掉。錄音一個都不會動 —— 丟的是可以重算的東西。"""
        with self._lock:
            freed = 0
            dropped = 0
            for entry in self._entries:
                if entry.get("mp3_file"):
                    freed += self._drop_mp3(entry)
                    dropped += 1
            self._save()
            return {"removed": dropped, "freed_bytes": freed}

    def stats(self, max_count: int = DEFAULT_MAX_COUNT,
              max_bytes: int = DEFAULT_MAX_BYTES) -> Dict[str, Any]:
        with self._lock:
            total = self._total_bytes()
            count = len(self._entries)
            pinned = sum(1 for e in self._entries if e.get("pinned"))
            mp3_count = sum(1 for e in self._entries if e.get("mp3_file"))
            mp3_bytes = self._mp3_bytes()
        return {
            "count": count,
            "pinned_count": pinned,
            "total_bytes": total,
            # MP3 快取另外報一份。混進 total_bytes 的話設定頁上的
            # 「已用 / 配額」會突然變大，而使用者根本沒有多錄任何一首。
            "mp3_count": mp3_count,
            "mp3_bytes": mp3_bytes,
            "mp3_max_bytes": mp3_cache_limit(max_bytes),
            "max_count": max_count,
            "max_bytes": max_bytes,
            # 「剩多少」比「用了多少」好懂：使用者要判斷的是還能不能再唱
            "remaining_count": max(0, max_count - count) if max_count > 0 else None,
            "remaining_bytes": max(0, max_bytes - total) if max_bytes > 0 else None,
        }

    # --- 修改與刪除 ---

    def set_pinned(self, rec_id: str, pinned: bool) -> Optional[Dict[str, Any]]:
        if not RECORDING_ID_RE.match(str(rec_id or "")):
            return None
        with self._lock:
            for entry in self._entries:
                if entry.get("id") == rec_id:
                    entry["pinned"] = bool(pinned)
                    self._save()
                    return dict(entry)
        return None

    def delete(self, rec_id: str) -> bool:
        if not RECORDING_ID_RE.match(str(rec_id or "")):
            return False
        with self._lock:
            entry = next((e for e in self._entries if e.get("id") == rec_id), None)
            if entry is None:
                return False
            self._remove_entry(entry)
            self._save()
            return True

    def clear(self, keep_pinned: bool = True) -> int:
        """
        清空錄音。預設**保留**標記保留的那幾筆 —— 「清空」按下去把特地留起來的
        那一次也刪掉，是使用者永遠不會原諒的那種刪除。要全刪得明講。
        """
        with self._lock:
            victims = [e for e in self._entries
                       if not (keep_pinned and e.get("pinned"))]
            for entry in victims:
                self._remove_entry(entry)
            self._save()
            return len(victims)
