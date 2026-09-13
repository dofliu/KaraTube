"""
整晚打包下載 (Night Export)

唱完之後真正會發生的事有兩種：**一個人**想把自己那幾首帶走（分享連結 + QR
已經處理掉了），以及**收場的時候**有人說「今天晚上的通通給我一份」——
後者用一首一首按下載是二十三次另存新檔，而且存出來會散在資料夾裡分不出
誰是誰、哪一首在前面。這一支做的就是把一整場包成一個 zip。

## 一場是怎麼算出來的

**不是照日曆日期切。** 包廂的一場是「晚上九點唱到凌晨兩點半」——
照日期切的話這一場會被切成兩半，而且後半（真正唱到嗨的那一段）
會被標成「隔天」。使用者心裡的那一場是連續的，所以這裡照**空檔**切：
相鄰兩次演唱之間超過 `gap_hours`（預設 6 小時）就算換了一場。

空檔法在另一個方向也對：同一個下午兩點與晚上九點各唱一輪，日期法會把
它們算成同一場（實際上是兩桌不同的客人），空檔法分得開。

## 為什麼是 ZIP_STORED（不壓縮）

錄音是 Opus / AAC / MP3 —— 全部都已經是壓縮過的資料。再 deflate 一次
省下來的空間是零點幾個百分點，換來的是把一整晚的資料重壓一遍的 CPU，
而那顆 CPU 同時在放歌、算音準、跑下一首的人聲分離。**唱歌永遠優先**，
所以音檔一律用 stored（純複製），只有最後那份曲目清單用 deflate
（純文字，壓下去省 70%，而它只有幾 KB）。

## 為什麼是邊包邊送，不是先包成檔案再送

先在磁碟上生一份 zip 的話，一場 200 MB 就要另外佔 200 MB ——
而錄音功能本身有配額，存在的理由正是「這顆磁碟會被塞爆」。
打包下載不該是那個把磁碟塞爆的人。所以 zip 是**串流**出去的：
一邊讀錄音檔一邊往連線寫，伺服器這邊永遠只有幾百 KB 在記憶體裡。

代價是沒有 `Content-Length`（還沒讀完之前算不出精確長度），瀏覽器的
進度條會是「未知大小」。這個代價是刻意收的：先算一次總長度再串流，
中間只要有一筆被配額擠掉，實際送出的位元組就對不上宣告的長度，
瀏覽器會把整包當成「下載失敗」—— 那比沒有進度條糟得多。
（清單端點會先報一個預估大小，畫面上寫「約 186 MB」就夠使用者判斷了。）

## 檔名

zip 裡的檔名是給**別的作業系統**看的：使用者解開之後多半是丟進車上的
USB、傳給朋友、或在 Windows 上開。所以：

  * 開頭補上序號與時間（`01 21-05 …`）—— 車機與大部分播放器是照檔名排序的，
    沒有序號的話一整晚的順序會變成照歌名筆劃排；
  * Windows 的非法字元（`\\ / : * ? " < > |`）與控制字元換成 `_`；
  * 結尾的點與空白砍掉（Windows 會自己吃掉，變成解壓縮後檔名對不上）；
  * 長度按 **UTF-8 位元組**裁（中文一個字 3 bytes，檔名上限是位元組數，
    照字數裁的話中文歌名會裁出一個存不進去的名字）。
"""
import logging
import re
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

logger = logging.getLogger("KaraTube.NightExport")

# 相隔多久算是不同場次。6 小時的理由：一場包廂再久也就五、六個小時，
# 而下一桌客人跟上一桌之間一定有清場、休息的空檔。
DEFAULT_GAP_HOURS = 6

# 串流的區塊大小。太小會讓 yield 的次數變多（每一次都要過一層 threadpool），
# 太大會讓記憶體多住一份；256 KB 是一般網路上跑得順的位置。
CHUNK_BYTES = 256 * 1024

# zip 裡的檔名長度上限（UTF-8 位元組）。多數檔案系統的單一檔名上限是 255 bytes，
# 留一段給序號、時間與副檔名，歌名本身給到 120 bytes（中文 40 字）。
MAX_NAME_BYTES = 120

MANIFEST_NAME = "曲目.txt"

ILLEGAL_CHARS = '\\/:*?"<>|'


def parse_ts(text: Any) -> Optional[datetime]:
    """索引裡的 `created_at`（ISO 字串）轉成 datetime。壞掉的回 None。"""
    try:
        return datetime.fromisoformat(str(text or ""))
    except (TypeError, ValueError):
        return None


def group_sessions(entries: Iterable[Dict[str, Any]],
                   gap_hours: float = DEFAULT_GAP_HOURS) -> List[Dict[str, Any]]:
    """
    把錄音切成一場一場的。回傳**最近的一場排最前面**。

    切法是空檔而不是日期（理由見檔頭）。判斷用的是清單本身的順序 ——
    索引是照存入順序長出來的，時間戳只負責回答「這裡是不是斷點」。
    時間戳壞掉（或沒有）的那一筆**不製造斷點**：它比較可能是一筆舊資料，
    而不是一場新的演唱，切下去只會多出一場只有一首的鬼場次。
    """
    gap_seconds = max(0.0, float(gap_hours)) * 3600.0
    sessions: List[List[Dict[str, Any]]] = []
    prev_ts: Optional[datetime] = None

    for entry in entries:
        ts = parse_ts(entry.get("created_at"))
        if not sessions:
            sessions.append([entry])
        elif ts is not None and prev_ts is not None and \
                (ts - prev_ts).total_seconds() > gap_seconds:
            sessions.append([entry])
        else:
            sessions[-1].append(entry)
        if ts is not None:
            prev_ts = ts

    views = [_session_view(group) for group in sessions]
    return list(reversed(views))


def _session_view(group: List[Dict[str, Any]]) -> Dict[str, Any]:
    """一場的摘要。`entries` 留著給打包用，端點回應時要記得拿掉。"""
    started = str(group[0].get("created_at", ""))
    ended = str(group[-1].get("created_at", ""))
    singers: List[str] = []
    for entry in group:
        name = str(entry.get("singer") or "").strip()
        if name and name not in singers:
            singers.append(name)
    return {
        "key": session_key(started, group[0].get("id", "")),
        "started_at": started,
        "ended_at": ended,
        "count": len(group),
        "bytes": sum(int(e.get("bytes") or 0) for e in group),
        "songs": len({str(e.get("song_id") or "") for e in group}),
        "singers": singers,
        "pinned_count": sum(1 for e in group if e.get("pinned")),
        "entries": group,
    }


def session_key(started_at: str, fallback_id: Any = "") -> str:
    """
    一場的識別字串：`20260913-2105`（開始時間到分鐘）。

    用開始時間而不是流水號：流水號會因為前面的場次被配額清掉而整批位移，
    使用者剛剛複製的那個網址隔天就指到別場去了。時間戳壞掉時退回
    第一筆的錄音 id（那個 id 本身已經是被正則釘死的形狀）。
    """
    ts = parse_ts(started_at)
    if ts is not None:
        return f"{ts:%Y%m%d-%H%M}"
    return re.sub(r"[^0-9A-Za-z-]", "", str(fallback_id or ""))[:32] or "unknown"


def find_session(sessions: List[Dict[str, Any]], key: str) -> Optional[Dict[str, Any]]:
    return next((s for s in sessions if s["key"] == str(key or "")), None)


def filter_by_singer(session: Dict[str, Any], singer: str) -> List[Dict[str, Any]]:
    """
    只留某一個人的那幾首。`singer` 是空字串就整場都要。

    「我的那幾首」是收場時最常見的要求（一桌八個人，不是每個人都想要
    另外七個人的版本），而它幾乎不花成本：同一場的清單過濾一次而已。
    """
    want = str(singer or "").strip()
    if not want:
        return list(session.get("entries", []))
    return [e for e in session.get("entries", [])
            if str(e.get("singer") or "").strip() == want]


# --- 檔名 ---

def truncate_bytes(text: str, limit: int) -> str:
    """照 UTF-8 位元組裁，不切斷任何一個字。"""
    data = text.encode("utf-8")
    if len(data) <= limit:
        return text
    return data[:limit].decode("utf-8", "ignore")


def safe_stem(text: str, limit: int = MAX_NAME_BYTES) -> str:
    """
    把任意字串變成一個在 Windows / macOS / Linux 都存得下去的檔名主體。

    來源是 YouTube 的歌名與使用者自己打的暱稱 —— 兩者都可能有斜線、冒號、
    換行，甚至是一整串空白。全部收乾淨，收完是空的就回空字串讓呼叫端決定。
    """
    cleaned = "".join(
        "_" if (ch in ILLEGAL_CHARS or not ch.isprintable()) else ch
        for ch in str(text or "")
    )
    cleaned = truncate_bytes(cleaned.strip(), limit)
    # Windows 會把結尾的點與空白吃掉：存進去叫 `歌名.`，解開之後變成 `歌名`，
    # 於是曲目清單上寫的檔名跟資料夾裡的對不起來。
    return cleaned.rstrip(". ").strip()


def member_name(entry: Dict[str, Any], index: int, used: Optional[set] = None) -> str:
    """
    zip 裡的一個檔名：`01 21-05 月亮代表我的心 - 阿明.webm`。

    序號在最前面是為了**排序**：車機、隨身碟播放器、Windows 檔案總管都是
    照檔名排的，沒有序號的話「今晚唱的順序」在解開之後就永遠找不回來了。
    時間用 `21-05` 而不是 `21:05` —— 冒號在 Windows 是非法字元。

    `used` 是已經用掉的名字（大小寫不敏感：Windows 與 macOS 的檔案系統
    預設不分大小寫，同一場裡兩首同名的歌會在解壓縮時互相覆蓋）。

    這裡不必處理 Windows 的保留裝置名稱（`CON`、`NUL`、`COM1`…）：那些名字
    只有在**整個主檔名就是它**的時候才是保留的，而每個名字前面一定掛著
    序號與時間，所以一首叫《AUX》的歌出來是 `03 22-10 AUX.webm`，不是 `AUX.webm`。
    """
    ts = parse_ts(entry.get("created_at"))
    stamp = f"{ts:%H-%M}" if ts else "00-00"
    parts = [p for p in (safe_stem(entry.get("title", "")),
                         safe_stem(entry.get("singer", ""), 40)) if p]
    stem = " - ".join(parts) or str(entry.get("id", "take"))
    stem = truncate_bytes(stem, MAX_NAME_BYTES).rstrip(". ")
    suffix = Path(str(entry.get("file", ""))).suffix or ".webm"

    base = f"{index:02d} {stamp} {stem}"
    name = f"{base}{suffix}"
    if used is None:
        return name
    # 同名要讓開。用 (2)(3) 而不是直接覆蓋 —— 覆蓋掉的是一次真的演唱。
    counter = 2
    while name.lower() in used:
        name = f"{base} ({counter}){suffix}"
        counter += 1
    used.add(name.lower())
    return name


# --- 曲目清單 ---

def _format_size(num: int) -> str:
    n = max(0, int(num or 0))
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n // 1024} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def _format_duration(ms: Any) -> str:
    total = max(0, int((float(ms or 0)) // 1000))
    return f"{total // 60}:{total % 60:02d}"


def _short_time(text: Any) -> str:
    ts = parse_ts(text)
    return f"{ts:%H:%M}" if ts else "--:--"


def manifest_text(session: Dict[str, Any], packed: List[Tuple[str, Dict[str, Any]]],
                  missing: List[Dict[str, Any]], singer: str = "") -> str:
    """
    zip 裡的那份 `曲目.txt`。

    這份清單是**打包完才生出來的**，而且刻意放在整包的最後一個項目：
    唱過的某一首有可能在打包的當下剛好被配額擠掉，先寫清單的話清單會說謊
    （寫了一首資料夾裡沒有的歌，使用者會以為是解壓縮壞掉）。最後才寫，
    它講的就是實際包進去的東西 —— 連「哪幾首沒包成」都講得出來。
    """
    started = str(session.get("started_at", ""))[:16].replace("T", " ")
    ended = str(session.get("ended_at", ""))[:16].replace("T", " ")
    total = sum(int(e.get("bytes") or 0) for _, e in packed)
    lines = [
        "KaraTube 整晚打包",
        "=" * 46,
        f"場次：{started} ~ {ended}",
        f"內容：{len(packed)} 首・{_format_size(total)}" +
        (f"（只有 {singer} 唱的）" if singer else ""),
        f"匯出：{datetime.now():%Y-%m-%d %H:%M}",
        "",
    ]
    for name, entry in packed:
        bits = [
            _short_time(entry.get("created_at")),
            str(entry.get("singer") or "—"),
            str(entry.get("title") or entry.get("song_id") or ""),
            _format_duration(entry.get("duration_ms")),
        ]
        if int(entry.get("score") or 0) > 0:
            bits.append(f"{int(entry['score']):,} 分{' ' + entry['grade'] if entry.get('grade') else ''}")
        if str(entry.get("mode")) == "duet":
            bits.append("對唱")
        lines.append(f"{name}")
        lines.append(f"    {'・'.join(b for b in bits if b)}")
    if missing:
        lines += [
            "",
            "以下這幾首沒有包進來（打包前檔案已經不在了：被刪除，或被配額清掉）：",
        ]
        for entry in missing:
            lines.append(f"    {_short_time(entry.get('created_at'))} "
                         f"{entry.get('singer') or '—'}・{entry.get('title') or entry.get('song_id')}")
    lines += [
        "",
        "-" * 46,
        "這些是瀏覽器錄下來的原始格式（webm / mp4），電腦與手機都播得出來。",
        "要放進車機或舊播放器的話，到點歌台的「🎙️ 錄唱回放」按那一首的",
        "「🎧 MP3」轉一份 —— 整晚一次全部轉成 MP3 會佔住正在放歌的那顆 CPU，",
        "所以刻意不放在這個打包裡。",
    ]
    return "\n".join(lines) + "\n"


def zip_filename(session: Dict[str, Any], singer: str = "") -> str:
    """下載回去的檔名：`KaraTube 20260913 晚場 23首.zip`。"""
    ts = parse_ts(session.get("started_at"))
    stamp = f"{ts:%Y%m%d}" if ts else str(session.get("key", "session"))
    who = safe_stem(singer, 40)
    parts = [f"KaraTube {stamp}"]
    if who:
        parts.append(who)
    parts.append(f"{session.get('count', 0)}首")
    return " ".join(parts) + ".zip"


# --- 串流打包 ---

class _StreamSink:
    """
    zipfile 寫進來的位元組先落在這裡，generator 再一塊塊送出去。

    刻意**只有** `write()` 與 `tell()`，沒有 `seek()`：zipfile 發現回頭寫不了
    就會改用 data descriptor（把 CRC 與大小寫在檔案資料的後面），而不是
    先留白、事後回頭補。這正是串流要的行為 —— 已經送出去的位元組改不了。

    順帶解決另一件事：不預先宣告 `file_size` 的話，實際讀到多少就寫多少，
    檔案在讀的當下長度對不上索引（理論上不會，但磁碟的事說不準）也不會
    讓整包壞掉。
    """

    def __init__(self):
        self._buf = bytearray()
        self._pos = 0

    def write(self, data: bytes) -> int:
        self._buf.extend(data)
        self._pos += len(data)
        return len(data)

    def tell(self) -> int:
        return self._pos

    def flush(self):
        pass

    @property
    def pending(self) -> int:
        return len(self._buf)

    def drain(self) -> bytes:
        chunk = bytes(self._buf)
        self._buf.clear()
        return chunk


def _zip_time(entry: Dict[str, Any]) -> Tuple[int, int, int, int, int, int]:
    """
    zip 裡的檔案時間。zip 的時間欄位是 1980 年起算的 DOS 時間，
    傳一個 1980 之前的時間進去 zipfile 會丟例外 —— 而錄音的時間戳
    是有可能壞掉的（機器沒對時、索引被手改過）。壞掉就退回 1980-01-01。
    """
    ts = parse_ts(entry.get("created_at"))
    if ts is None or ts.year < 1980:
        return (1980, 1, 1, 0, 0, 0)
    return (ts.year, ts.month, ts.day, ts.hour, ts.minute, ts.second)


def iter_session_zip(base_dir: Path, entries: List[Dict[str, Any]],
                     session: Dict[str, Any], singer: str = "",
                     chunk_bytes: int = CHUNK_BYTES) -> Iterator[bytes]:
    """
    一場的 zip，一塊一塊吐出來。

    幾個關鍵：

      * **先開檔再讀**。開著的檔案描述子在 POSIX 上不會因為別人 unlink 就消失，
        所以打包途中配額清掉某一筆也不會讓那個檔案讀到一半斷掉。
      * **開不起來就跳過並記下來**，不是整包放棄。少一首的 zip 加上一份說明
        （曲目清單會寫出來），比一個下載到 80% 斷掉的檔案有用得多 ——
        後者使用者連哪幾首錄到了都不知道。
      * 音檔 stored、清單 deflate（理由見檔頭）。
    """
    base_dir = Path(base_dir)
    sink = _StreamSink()
    used: set = set()
    packed: List[Tuple[str, Dict[str, Any]]] = []
    missing: List[Dict[str, Any]] = []

    with zipfile.ZipFile(sink, "w", allowZip64=True) as zf:
        for index, entry in enumerate(entries, 1):
            src = base_dir / str(entry.get("file", ""))
            try:
                handle = open(src, "rb")
            except OSError:
                logger.info(f"打包時找不到錄音檔，跳過: {entry.get('id')}")
                missing.append(entry)
                continue
            name = member_name(entry, index, used)
            try:
                info = zipfile.ZipInfo(name, date_time=_zip_time(entry))
                info.compress_type = zipfile.ZIP_STORED
                # 一般檔案 rw-r--r--。不設的話部分工具解開來會是 000，
                # 使用者得先 chmod 才播得動。
                info.external_attr = 0o644 << 16
                with zf.open(info, "w") as dst:
                    while True:
                        chunk = handle.read(chunk_bytes)
                        if not chunk:
                            break
                        dst.write(chunk)
                        if sink.pending >= chunk_bytes:
                            yield sink.drain()
                packed.append((name, entry))
            except OSError as e:
                # 讀到一半壞掉：這一筆已經寫進 zip 了，不能撤回，但至少
                # 不要讓整包死在這裡 —— 後面那幾首跟清單還是送得出去。
                logger.warning(f"打包時讀取失敗: {entry.get('id')} ({e})")
                packed.append((name, entry))
            finally:
                handle.close()

        text = manifest_text(session, packed, missing, singer)
        manifest = zipfile.ZipInfo(MANIFEST_NAME, date_time=_zip_time(
            {"created_at": datetime.now().isoformat(timespec="seconds")}))
        manifest.compress_type = zipfile.ZIP_DEFLATED
        manifest.external_attr = 0o644 << 16
        with zf.open(manifest, "w") as dst:
            dst.write(text.encode("utf-8"))

    yield sink.drain()
    logger.info(f"整晚打包完成：{len(packed)} 首"
                f"{f'（{len(missing)} 首檔案已不在）' if missing else ''}")


class ExportGate:
    """
    同一時間只打一包。

    一包是一整晚的資料（可能兩、三百 MB），三個人同時按下去的話，
    伺服器要同時讀三份、網路要同時送三份 —— 而那條網路正是舞台端
    串影片與 WebSocket 在走的。第二個人等一下就好，舞台卡住不行。

    租約（lease）是這支的重點：拿了就要還，但「還」發生在 generator 結束時，
    而 generator 有可能**從來沒被跑過**（使用者按了下載又立刻關掉分頁，
    連線在第一個位元組之前就斷了）。那種情況沒有任何程式碼會執行到 finally，
    這個位子就永遠空不出來。所以鎖是有期限的：超過 `lease_seconds` 還沒還，
    下一個人可以直接把它拿走。
    """

    def __init__(self, lease_seconds: float = 900.0):
        self._lock = threading.Lock()
        self._lease_seconds = float(lease_seconds)
        self._token = 0
        self._holder = 0
        self._since = 0.0

    def acquire(self) -> int:
        """拿到位子回傳一個 > 0 的 token；有人正在用回傳 0。"""
        with self._lock:
            now = time.time()
            if self._holder and (now - self._since) < self._lease_seconds:
                return 0
            self._token += 1
            self._holder = self._token
            self._since = now
            return self._holder

    def release(self, token: int):
        """還位子。token 對不上就不動 —— 那代表租約已經過期被別人接手了。"""
        with self._lock:
            if token and token == self._holder:
                self._holder = 0
                self._since = 0.0

    @property
    def busy(self) -> bool:
        with self._lock:
            return bool(self._holder) and (time.time() - self._since) < self._lease_seconds
