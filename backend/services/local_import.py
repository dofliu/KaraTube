"""
本機曲庫匯入 (Local Media Import)

在這一版之前，KaraTube 的曲庫只有一個入口：YouTube。那對家裡那台機器完全
夠用，但它讓這個系統**放不進店裡**，也讓一整類使用者進不來：

  * 店家買的、或自己拍的伴唱影片就躺在一顆硬碟裡，從來沒有上過 YouTube；
  * 早年的專輯、母帶、朋友的創作，網路上根本沒有；
  * 包廂那台機器不一定連得到外網（很多店的點歌機是刻意不接網際網路的）。

商用點歌機的曲庫從來都是**本機檔案**。所以這一頁補的不是一個方便功能，
而是「這台機器的歌可以從哪裡來」的第二條路：把檔案丟進 `cache/import/`，
機器掃得到、認得出、跑同一條流水線（分離 → 對詞 → 音高 → 響度），
跑完之後它跟 YouTube 來的歌**沒有任何差別** —— 一樣有歌號、一樣進注音索引、
一樣算分、一樣錄得起來。

## 五個決定

**1. 身分證是檔案內容，不是檔名。**
匯入的歌要有一個 song_id（那是資料夾名稱、歌號的綁定對象、排行與個人最佳的
鍵）。用檔名的話，把「周杰倫-稻香.mp4」改名成「稻香.mp4」就會變成**第二首歌**：
再跑一次十幾分鐘的流水線，而且歌號、最愛、個人最佳全部跟原來那首分家。
所以用檔案內容算指紋（大小 + 頭尾各 1MB 的 SHA-1）—— 改名、搬資料夾、
換一顆隨身碟，都還是同一首歌。

不讀整個檔案是因為掃描要能**互動**：一顆裝了 300 首 MV 的隨身碟是好幾十 GB，
全部讀完再回答「你有哪些歌」要好幾分鐘。頭尾各 1MB 對「同一個檔案」永遠一致，
對「不同的歌」要撞號得先撞大小再撞兩段內容，這個曲庫規模下可以忽略。
而且指紋連同 (大小, mtime) 一起記在 registry 裡，第二次掃描連那 2MB 都不用讀。

**2. 分隔符要有空白。**
檔名解析成「歌手 - 歌名」是必要的（沒有歌手就進不了歌星查歌），但 `A-Lin`、
`Jay-Z`、`G.E.M.` 裡面的連字號不是分隔符。規則因此是：半形 `-` 兩邊要有空白
才算分隔，全形的 `－ – —` 才可以貼著字。切錯的代價是那首歌從此掛在一位
叫「A」的歌星底下 —— 而使用者要到歌星查歌那一頁才會發現。

**3. 該丟掉的只有技術雜訊。**
`[1080p]`、`(官方MV)`、`【4K】` 這種整段都是規格的括號拿掉，歌名才乾淨
（歌名字數查歌是照字數算的，多算兩個字就查不到）。但 `(Live)`、`(演唱會版)`、
`(粵語版)` 一律留著 —— 那是**另一個錄音**，時間軸跟原版對不上，把它跟原版
混為一談只會讓人以為「字幕歪了」。

**4. 旁邊那份 .lrc 是主角，不是備援。**
本機檔案多半是冷門歌、自製歌、早年的專輯 —— 網路上抓不到 LRC 的機率遠高於
YouTube 熱門歌。所以同名的 `.lrc` 放在旁邊就直接採用，而且**不因為分數低就
退回 Whisper**：使用者親手放的歌詞被機器默默換成一份聽打的亂碼，他既看不到
原因也想不到要去哪裡改。對齊分數照樣記進 `alignment.json`，畫面上的品質徽章
會講出來 —— 看得到的壞比看不到的好。

**5. 原始檔案一個都不動。**
匯入是複製，不是搬移：`cache/import/` 裡的檔案在匯入之後原封不動留著
（狀態變成「已在曲庫」）。刪掉曲庫裡那首歌也不會碰到它。理由很簡單 ——
那顆隨身碟可能是使用者唯一的一份，而「匯入」這兩個字沒有向任何人承諾
會把他的檔案搬走。

registry 存在 `cache/local_imports.json`：哪個 song_id 來自哪個檔案、
使用者有沒有自己改過歌名、上一次處理是成功還失敗。它跟歌號簿一樣**不放在
歌的資料夾裡** —— 「重新處理」會 rmtree 整個資料夾，而那樣就再也找不到
原始檔案在哪了。
"""
import hashlib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger("KaraTube.LocalImport")

# 本機匯入歌曲的 song_id 前綴。看到它就知道「這首歌不在 YouTube 上」——
# 佇列要用 `local:` 網址而不是 youtube 網址去處理它（見 queue_manager.add_song）。
LOCAL_ID_PREFIX = "loc_"
# 流水線用的來源字串。`process_song("local:loc_xxx")` 會走本機那條路。
LOCAL_URL_PREFIX = "local:"

VIDEO_EXTS = frozenset({
    ".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".mpg", ".mpeg", ".ts", ".wmv", ".flv",
})
AUDIO_EXTS = frozenset({
    ".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus", ".wma", ".aif", ".aiff",
})
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS

# 一次掃描最多列幾個檔案。一顆隨身碟可能有上千個檔案，而畫面上一次也看不完；
# 超過就截斷並在畫面上說「還有更多，分批匯入」——
# 靜悄悄只列前 400 個會讓使用者以為剩下的檔案機器讀不到。
MAX_SCAN_FILES = 400
# 指紋取樣大小：頭尾各讀這麼多。
FINGERPRINT_CHUNK = 1024 * 1024
# 小於這個大小的檔案不當成歌（多半是佔位檔、封面圖的殘骸、下載到一半的檔案）
MIN_MEDIA_BYTES = 64 * 1024

# 匯入資料夾裡的說明檔。資料夾必須自己講得出用法 —— 部署文件在另一台電腦上。
READ_ME_NAME = "讀我-如何匯入歌曲.txt"
READ_ME_TEXT = """KaraTube 本機曲庫匯入
=====================

把你自己的伴唱影片或音檔複製到這個資料夾，然後到點歌台的
「📁 本機匯入」分頁按「匯入」，機器就會跑跟 YouTube 歌曲一樣的流水線
（AI 分離伴奏 → 歌詞對齊 → 音高導唱線 → 響度量測）。

支援的格式
  影片：mp4 mkv mov avi webm m4v mpg ts wmv flv
  音檔：mp3 m4a aac flac wav ogg opus wma aiff
  （純音檔沒有 MV，舞台會自動改用情境背景，一樣可以唱。）

檔名怎麼取
  「歌手 - 歌名.mp4」    ← 建議這樣，半形減號兩邊要有空白
  「歌手－歌名.mp4」      ← 全形減號可以貼著字
  「歌名.mp4」           ← 也可以，歌手留空，之後在畫面上補打

  括號裡整段都是規格的會自動拿掉：[1080p]、(官方MV)、【4K】。
  版本資訊則會保留：(Live)、(粵語版) —— 那是另一個錄音，不該跟原版混在一起。

歌詞
  把同名的 .lrc 放在旁邊（稻香.mp4 → 稻香.lrc），機器會直接用它，
  並且自動校正時間軸。沒有 .lrc 就上網找；找不到就聽人聲軌聽打。

你的檔案不會被搬走
  匯入是「複製一份進曲庫」，這個資料夾裡的原始檔案原封不動留著。
  匯入完成後它會顯示成「已在曲庫」。你可以自己刪，也可以留著當備份。
"""

# --- 檔名清理 ----------------------------------------------------------------

# 括號整組：[...] (...) 【...】 （...） 〔...〕
_BRACKET_RE = re.compile(r"[\[\(【（〔][^\[\]\(\)【】（）〔〕]*[\]\)】）〕]")
# 開頭的曲序：01. / 03 - / 1_ （專輯抓軌出來的檔名幾乎都有）
_TRACK_NO_RE = re.compile(r"^\s*\d{1,3}\s*[\.\-_、)]\s*")
# 分隔符：半形減號要有空白（A-Lin 不能被切開），全形的可以貼著字
_SPLIT_RE = re.compile(r"\s+[-~]\s+|\s*[－–—]\s*|\s+[｜|]\s+")

# 括號裡整段都是這些東西才算雜訊。刻意**不含** live / 演唱會 / 粵語 / 國語 /
# acoustic 這類「版本」字眼：那是另一個錄音，丟掉等於讓兩個版本在曲庫裡撞成一首。
_NOISE_TOKENS = frozenset({
    "official", "officialvideo", "officialmv", "officialmusicvideo", "officialaudio",
    "mv", "m/v", "pv", "video", "audio", "lyrics", "lyric", "lyricvideo", "lyricsvideo",
    "hd", "fhd", "uhd", "hq", "sd", "4k", "8k", "2k", "1080p", "720p", "480p", "1080",
    "720", "60fps", "30fps", "x264", "h264", "x265", "h265", "hevc", "avc", "aac", "flac",
    "mp3", "mp4", "wav", "remastered", "remaster", "hires", "hi-res",
    "官方", "官方版", "官方mv", "官方完整版", "官方音樂錄影帶", "音樂錄影帶",
    "高清", "超清", "藍光", "修復版", "完整版", "無損", "中文字幕", "動態歌詞",
    "歌詞版", "歌詞", "字幕版", "完整", "新歌",
})


def _noise_key(text: str) -> str:
    """把括號裡的內容壓成比對用的鍵：去掉空白與標點、英文轉小寫。"""
    return re.sub(r"[\s\.\-_,，、·]+", "", str(text or "")).lower()


def _is_noise_bracket(inner: str) -> bool:
    """這一組括號裡整段都是規格／來源雜訊嗎？"""
    key = _noise_key(inner)
    if not key:
        return True
    if key in _NOISE_TOKENS:
        return True
    # 「1080p60」「4khdr」這類黏在一起的寫法
    if re.fullmatch(r"(\d{3,4}p\d{0,3}|\d[kK]|hdr|hdr10|dolbyvision)", key):
        return True
    return False


def strip_noise(name: str) -> str:
    """
    拿掉檔名裡的技術雜訊，留下歌名該有的字。

    只動「整組括號都是雜訊」的部分 —— 半組半組地砍會把 `(Live 2019)` 砍成
    `(2019)`，那比不砍更難懂。
    """
    text = str(name or "")

    def replace(match: "re.Match[str]") -> str:
        inner = match.group(0)[1:-1]
        return " " if _is_noise_bracket(inner) else match.group(0)

    # 巢狀括號很少見，跑兩輪就夠（第二輪處理 `((HD))` 這種）
    for _ in range(2):
        new_text = _BRACKET_RE.sub(replace, text)
        if new_text == text:
            break
        text = new_text
    text = _TRACK_NO_RE.sub("", text)
    # 收尾的孤立雜訊（`稻香 1080p`、`稻香 HD`）
    parts = text.split()
    while parts and _noise_key(parts[-1]) in _NOISE_TOKENS:
        parts.pop()
    text = " ".join(parts) if parts else text.strip()
    return re.sub(r"\s{2,}", " ", text).strip(" -_·、")


def parse_source_name(stem: str) -> Tuple[str, str]:
    """
    從檔名（不含副檔名）解出 (歌名, 歌手)。解不出歌手就回空字串。

    解不出來**不是錯誤**：歌手留白的歌照樣點得到、唱得到，只是不會出現在
    歌星查歌裡；而使用者可以在匯入前的那一格自己補打。反過來「猜錯歌手」
    是回不去的 —— 一旦進了曲庫，那個名字會跟著歌號與排行一起留下。
    """
    cleaned = strip_noise(stem)
    if not cleaned:
        return (str(stem or "").strip(), "")
    pieces = _SPLIT_RE.split(cleaned, maxsplit=1)
    if len(pieces) == 2:
        artist, title = pieces[0].strip(), pieces[1].strip()
        # 兩邊都要有東西，而且歌手那一段不能長得像一整句歌名
        if artist and title and len(artist) <= 24:
            return (title, artist)
    return (cleaned, "")


def title_key(title: str) -> str:
    """比對「曲庫裡是不是已經有同名的歌」用的鍵。標點、空白、大小寫都不算數。"""
    return re.sub(r"[^0-9a-z一-鿿぀-ヿ가-힯]+", "",
                  str(title or "").lower())


def is_local_id(song_id: str) -> bool:
    """這個 song_id 是本機匯入來的嗎？"""
    return str(song_id or "").startswith(LOCAL_ID_PREFIX)


def local_url(song_id: str) -> str:
    """流水線認得的本機來源字串。"""
    return f"{LOCAL_URL_PREFIX}{song_id}"


def fingerprint_file(path: Path, chunk: int = FINGERPRINT_CHUNK) -> str:
    """
    檔案指紋 → song_id。

    取 (檔案大小, 開頭 chunk bytes, 結尾 chunk bytes) 的 SHA-1 前 12 個字。
    改名、搬家、換一顆碟都不影響；讀整個檔案則會讓掃描慢到沒辦法互動
    （見模組說明第 1 點）。
    """
    path = Path(path)
    size = path.stat().st_size
    h = hashlib.sha1()
    h.update(str(size).encode("ascii"))
    with open(path, "rb") as f:
        h.update(f.read(chunk))
        if size > chunk * 2:
            f.seek(-chunk, os.SEEK_END)
            h.update(f.read(chunk))
    return LOCAL_ID_PREFIX + h.hexdigest()[:12]


def sidecar_lrc(path: Path) -> Optional[Path]:
    """同名的 .lrc（稻香.mp4 → 稻香.lrc / 稻香.LRC）。沒有就回 None。"""
    path = Path(path)
    for suffix in (".lrc", ".LRC", ".Lrc"):
        candidate = path.with_suffix(suffix)
        if candidate.is_file():
            return candidate
    return None


def kind_of(suffix: str) -> str:
    """副檔名屬於哪一類：video / audio / ''（不支援）。"""
    ext = str(suffix or "").lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    return ""


class LocalImportLibrary:
    """匯入資料夾的掃描、指紋快取與「哪一首歌來自哪個檔案」的帳本。"""

    def __init__(self, root: Path, registry_file: Path, storage: Optional[Any] = None):
        self.root = Path(root)
        self.registry_file = Path(registry_file)
        self.storage = storage
        self._lock = threading.RLock()
        # song_id -> {path, title, artist, title_from_user, status, error, imported_at}
        self.entries: Dict[str, Dict[str, Any]] = {}
        # 相對路徑 -> {size, mtime, fp}。第二次掃描就不必再讀那 2MB。
        self.fingerprints: Dict[str, Dict[str, Any]] = {}
        self._load()
        self.ensure_root()

    # --- 資料夾 ---

    def ensure_root(self) -> bool:
        """建好匯入資料夾並放一份說明。建不起來（唯讀掛載）不是致命錯誤。"""
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            readme = self.root / READ_ME_NAME
            if not readme.exists():
                readme.write_text(READ_ME_TEXT, encoding="utf-8")
            return True
        except OSError as e:
            logger.warning(f"匯入資料夾建立失敗 {self.root}: {e}")
            return False

    # --- 持久化 ---

    def _load(self):
        if not self.registry_file.exists():
            return
        try:
            raw = json.loads(self.registry_file.read_text(encoding="utf-8"))
        except Exception as e:
            # 壞檔不該讓整台機器開不起來：從空的開始，最多就是重算一次指紋。
            logger.warning(f"本機匯入紀錄讀取失敗，從空的開始: {e}")
            return
        if not isinstance(raw, dict):
            return
        entries = raw.get("entries")
        if isinstance(entries, dict):
            self.entries = {str(k): v for k, v in entries.items() if isinstance(v, dict)}
        fps = raw.get("fingerprints")
        if isinstance(fps, dict):
            self.fingerprints = {str(k): v for k, v in fps.items() if isinstance(v, dict)}

    def _save(self):
        try:
            self.registry_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {"entries": self.entries, "fingerprints": self.fingerprints}
            self.registry_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as e:
            logger.warning(f"本機匯入紀錄寫入失敗: {e}")

    # --- 路徑 ---

    def resolve(self, rel_path: str) -> Path:
        """
        相對路徑 → 絕對路徑，並確認它真的在匯入資料夾裡面。

        路徑是從瀏覽器送進來的，所以 `../../etc/passwd` 一定要擋。用 resolve()
        之後比對根目錄，連「資料夾裡的符號連結指到外面」也一起擋掉
        —— resolve() 會把連結解開，解出來的路徑不在根目錄底下就不收。
        """
        base = self.root.resolve()
        text = str(rel_path or "").strip().replace("\\", "/").lstrip("/")
        if not text:
            raise ValueError("沒有指定檔案")
        target = (base / text).resolve()
        if target != base and base not in target.parents:
            raise ValueError("檔案不在匯入資料夾裡")
        return target

    def source_path(self, song_id: str) -> Path:
        """這個 song_id 的原始檔案在哪。找不到／已被移走就丟 FileNotFoundError。"""
        entry = self.entry(song_id)
        if not entry:
            raise FileNotFoundError(f"沒有這筆匯入紀錄: {song_id}")
        path = self.resolve(entry.get("path", ""))
        if not path.is_file():
            raise FileNotFoundError(
                f"匯入來源已經不在了：{entry.get('path', '')}"
                "（檔案被移走或隨身碟被拔掉了？放回 cache/import/ 再試一次）")
        return path

    def sidecar_lrc_text(self, song_id: str) -> Optional[str]:
        """這首歌旁邊那份 .lrc 的內容。沒有（或不是本機歌）回 None。"""
        try:
            lrc = sidecar_lrc(self.source_path(song_id))
        except (FileNotFoundError, ValueError):
            return None
        if lrc is None:
            return None
        try:
            return lrc.read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            logger.warning(f"讀不到 {lrc}: {e}")
            return None

    # --- 帳本 ---

    def entry(self, song_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            entry = self.entries.get(str(song_id or ""))
            return dict(entry) if entry else None

    def remember(self, song_id: str, rel_path: str, title: str, artist: str,
                 title_from_user: bool = False) -> Dict[str, Any]:
        with self._lock:
            entry = {
                "song_id": song_id,
                "path": rel_path,
                "title": title,
                "artist": artist,
                "title_from_user": bool(title_from_user),
                "status": "queued",
                "error": "",
                "requested_at": time.time(),
            }
            self.entries[song_id] = entry
            self._save()
            return dict(entry)

    def mark(self, song_id: str, status: str, error: str = ""):
        """處理結果回寫。掃描畫面靠它顯示「上次匯入失敗，原因是…」。"""
        with self._lock:
            entry = self.entries.get(str(song_id or ""))
            if not entry:
                return
            entry["status"] = status
            entry["error"] = str(error or "")[:200]
            if status == "done":
                entry["imported_at"] = time.time()
            self._save()

    def fingerprint_of(self, path: Path, rel: str) -> str:
        """指紋（會用 (大小, mtime) 當快取鍵）。檔案動過就重算。"""
        try:
            stat = path.stat()
        except OSError as e:
            raise FileNotFoundError(f"讀不到檔案: {rel}") from e
        cached = self.fingerprints.get(rel)
        if (cached and int(cached.get("size", -1)) == stat.st_size
                and abs(float(cached.get("mtime", -1)) - stat.st_mtime) < 1.0
                and str(cached.get("fp", "")).startswith(LOCAL_ID_PREFIX)):
            return str(cached["fp"])
        fp = fingerprint_file(path)
        with self._lock:
            self.fingerprints[rel] = {"size": stat.st_size, "mtime": stat.st_mtime, "fp": fp}
        return fp

    # --- 掃描 ---

    def _iter_media_files(self) -> Iterable[Path]:
        if not self.root.is_dir():
            return []
        found: List[Path] = []
        for path in sorted(self.root.rglob("*")):
            name = path.name
            # 隱藏檔、macOS 的 ._ 伴隨檔、Windows 的 desktop.ini 一律跳過
            if name.startswith(".") or name.startswith("._") or name.lower() == "desktop.ini":
                continue
            if not path.is_file():
                continue
            found.append(path)
            if len(found) >= MAX_SCAN_FILES * 3:
                break
        return found

    def _cached_titles(self) -> Dict[str, Dict[str, str]]:
        """曲庫裡已經有的歌名（給「你可能已經有這首了」的提示用）。"""
        if self.storage is None:
            return {}
        try:
            songs = self.storage.list_cached_songs()
        except Exception:
            return {}
        table: Dict[str, Dict[str, str]] = {}
        for meta in songs:
            if not isinstance(meta, dict):
                continue
            key = title_key(meta.get("title", ""))
            if key and key not in table:
                table[key] = {"song_id": str(meta.get("id") or ""),
                              "title": str(meta.get("title") or "")}
        return table

    def _state_of(self, song_id: str) -> Tuple[str, str]:
        """(狀態, 給人看的註解)。曲庫的實際檔案說了算，registry 只補充理由。"""
        complete = False
        if self.storage is not None:
            try:
                complete = bool(self.storage.is_song_complete(song_id))
            except Exception:
                complete = False
        if complete:
            return ("imported", "已在曲庫，可以直接點歌")
        entry = self.entries.get(song_id) or {}
        status = entry.get("status", "")
        if status == "error":
            return ("failed", entry.get("error") or "上次匯入失敗")
        if status == "queued":
            return ("queued", "已排入處理，等機器有空就跑")
        if status == "done":
            # registry 說跑完了，但曲庫裡的檔案不齊 —— 多半是被快取上限自動
            # 清掉了，或有人按了刪除。它現在是「可以再匯入一次」而不是「壞了」。
            return ("new", "曾經匯入過，但曲庫裡已經沒有了（可以再匯入一次）")
        return ("new", "")

    def scan(self) -> Dict[str, Any]:
        """
        掃描匯入資料夾。回傳每個檔案的狀態，**不會**動到任何檔案。

        會讀磁碟（指紋、`is_song_complete`），所以呼叫端一律丟到執行緒去跑。
        """
        self.ensure_root()
        files: List[Dict[str, Any]] = []
        skipped: List[Dict[str, str]] = []
        cached_titles = self._cached_titles()
        base = self.root.resolve()
        truncated = False

        for path in self._iter_media_files():
            try:
                rel = str(path.resolve().relative_to(base))
            except ValueError:
                continue
            suffix = path.suffix.lower()
            kind = kind_of(suffix)
            if not kind:
                # .lrc 是歌詞（不是歌）、說明檔是我們自己放的：兩者都不算「略過」
                if suffix not in (".lrc", ".txt", ".srt", ".json"):
                    shown = suffix or "沒有副檔名"
                    skipped.append({"path": rel, "reason": f"不支援的格式（{shown}）"})
                continue
            if len(files) >= MAX_SCAN_FILES:
                truncated = True
                break
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size < MIN_MEDIA_BYTES:
                skipped.append({"path": rel, "reason": "檔案太小，看起來不是一首歌"})
                continue
            try:
                song_id = self.fingerprint_of(path, rel)
            except (OSError, FileNotFoundError) as e:
                skipped.append({"path": rel, "reason": f"讀不到這個檔案（{e}）"})
                continue

            entry = self.entries.get(song_id) or {}
            parsed_title, parsed_artist = parse_source_name(path.stem)
            # 使用者改過的歌名要活過下一次掃描 —— 他打的時候是看著檔案打的，
            # 而檔名解析永遠只會得到同一個答案。
            if entry.get("title_from_user"):
                title = str(entry.get("title") or parsed_title)
                artist = str(entry.get("artist") or parsed_artist)
            else:
                title, artist = parsed_title, parsed_artist
            state, note = self._state_of(song_id)
            dup = cached_titles.get(title_key(title)) if state == "new" else None
            files.append({
                "path": rel,
                "song_id": song_id,
                "title": title,
                "artist": artist,
                "kind": kind,
                "ext": suffix,
                "size_bytes": size,
                "has_lrc": sidecar_lrc(path) is not None,
                "state": state,
                "note": note,
                # 「曲庫裡已經有同名的歌」只是提示，不擋：同名的歌真的存在，
                # 而使用者手上這個檔案可能正是他想換掉那一份的原因。
                "duplicate_of": (dup or {}).get("song_id", ""),
            })

        self._save()
        counts = {"total": len(files), "new": 0, "imported": 0, "queued": 0, "failed": 0}
        for f in files:
            counts[f["state"]] = counts.get(f["state"], 0) + 1
        files.sort(key=lambda f: ({"new": 0, "failed": 1, "queued": 2, "imported": 3}
                                  .get(f["state"], 4), f["path"]))
        return {
            "root": str(self.root),
            "exists": self.root.is_dir(),
            "files": files,
            "skipped": skipped[:40],
            "counts": counts,
            "truncated": truncated,
            "max_files": MAX_SCAN_FILES,
        }

    # --- 匯入 ---

    def prepare(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        把使用者勾選的檔案轉成排程任務要的 sources，並記進帳本。

        回傳 `{"sources": [...], "failed": [...]}`。一個檔案讀不到不該讓整批
        都收不下來 —— 一顆隨身碟裡有一兩個壞檔是常態。
        """
        sources: List[Dict[str, Any]] = []
        failed: List[Dict[str, str]] = []
        seen = set()
        for raw in items[:MAX_SCAN_FILES]:
            if not isinstance(raw, dict):
                continue
            rel = str(raw.get("path") or "").strip()
            try:
                path = self.resolve(rel)
                if not path.is_file():
                    raise FileNotFoundError("檔案不在了")
                if not kind_of(path.suffix):
                    raise ValueError("不支援的格式")
                rel_norm = str(path.resolve().relative_to(self.root.resolve()))
                song_id = self.fingerprint_of(path, rel_norm)
            except (OSError, ValueError, FileNotFoundError) as e:
                failed.append({"path": rel, "reason": str(e)})
                continue
            if song_id in seen:
                continue
            seen.add(song_id)
            parsed_title, parsed_artist = parse_source_name(path.stem)
            title = str(raw.get("title") or "").strip() or parsed_title
            artist = str(raw.get("artist") or "").strip() or parsed_artist
            from_user = (title != parsed_title) or (artist != parsed_artist)
            self.remember(song_id, rel_norm, title[:120], artist[:60], from_user)
            sources.append({
                "song_id": song_id,
                "title": title[:120],
                "artist": artist[:60],
                "url": local_url(song_id),
                "thumbnail": "",
            })
        return {"sources": sources, "failed": failed}
