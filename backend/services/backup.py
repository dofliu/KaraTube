"""
備份與還原：把這台機器**認得的人事物**打包成一個檔案

前面二十九版補的都是「包廂裡的那一晚」，這一版補的是**那一晚之後**：
硬碟會壞、機器會換、店會從單機換成一台伺服器帶十間包廂。而在這一版之前，
這台機器上所有累積出來的東西（歌號簿、最愛、成績、音域、字幕校正、設定）
全部只存在 `cache/` 底下那十幾個 JSON 檔裡，沒有任何一條路可以**帶走**它們
—— 要搬只能叫店家自己 scp 一個資料夾，而那個資料夾裡同時還有幾十 GB 的歌、
一份櫃檯密碼，以及三個「現在正在發生的事」的狀態檔。

所以這個模組不是 `tar cache/`。它是一張**清單**，以及清單上每一條為什麼在
（或不在）上面的理由。

---

## 一、備份什麼：三類，以及一條明確的排除表

**(1) 不可重建的承諾** —— 只有一份：`song_numbers.json`。

這是整份備份裡唯一**沒有第二個來源**的東西。曲庫可以重下載（來源網址記在每一首
的 metadata 裡），最愛與成績重唱就會再長出來，設定按幾下就調回去 —— 但歌號是一個
承諾：「100237 永遠是稻香，或者什麼都不是」。換機器時號碼簿沒跟過去，新機器會照
自己的入庫順序重新發號，於是 100237 變成別首歌 —— 而包廂牆上那本歌本、客人記了
三年的那六個數字，全部指到錯的地方，**而且沒有任何人會收到通知**。
（見 `song_numbers.py` 第 1 點：回收號碼省下的是幾個數字，賠掉的是「記號碼」
這件事本身。備份漏掉號碼簿，賠掉的是同一個東西。）

**(2) 人累積出來的** —— 最愛、已唱歷史、成績與個人最佳、音域檔案、點唱排行、
每首歌的字幕校正。這些重唱一晚會長回來一點，但**長不回原來那一份**：
「我的個人最佳」不是重唱就有的東西，而字幕校正是有人戴著耳機一句一句調出來的。

**(3) 這台機器的設定** —— 系統設定、包廂名冊、本機匯入帳本。

**排除表（`EXCLUDED`）跟清單一樣重要**，每一條都寫了理由，因為三個月後想
「為什麼我的錄音沒有被備份」的人會來讀這個檔案：

* `songs/`（曲庫本體）—— 幾十 GB。一份沒有人下載得動的備份等於沒有備份，
  而它是這份清單上唯一**可以重建**的東西。取而代之，備份裡放一張唯讀的
  `library.json`（歌號 → 歌名 → 來源網址），還原之後機器才講得出
  「號碼簿上有 312 首，這台機器上只有 40 首，其餘 272 首要重新處理」——
  不然那 272 個號碼會無聲地變成墓碑，而使用者的結論是「備份沒還原成功」。
* `recordings/`（錄唱回放）—— 量大，而且那是客人的聲音。備份檔會被 email、
  丟進雲端硬碟、留在隨身碟上，而沒有任何一位客人同意過那件事。
* `staff_lock.json`（櫃檯密碼）—— 同上，但更嚴重：那是這台機器唯一的安全邊界。
  備份檔跑到哪裡，密碼就跟到哪裡。**還原不會動櫃檯鎖**，新機器還原完仍然是
  「還沒設定密碼」的狀態，由人重新設一次。
* `room_timer.json` / `service_calls.json` / `contest.json` / `batch_jobs.json`
  —— 「現在正在發生的事」。搬機器的那一刻沒有人在唱歌，把別台機器的計時器、
  半張未結案的服務單、昨晚的擂台倒進來，只會讓櫃檯看到一份不存在的現況。
  （所以備份裡一條巢狀路徑都沒有：`rooms/<id>/` 底下的三個檔全在這一類，
  跟著走的只有名冊 `rooms.json` 本身。）
* `recording_shares.json`（分享連結）—— 指向不會一起搬過去的錄音。
* `temp/`、`import/` —— 前者是垃圾，後者是使用者自己的原始檔案（匯入從來
  沒有承諾要把它們搬走，見 `local_import.py`）。

## 二、還原：為什麼是「重新啟動後生效」

這一件事是寫到一半才看清楚的。每一個服務都在 `__init__` 就把自己那份 JSON
**讀進記憶體**了（`Favorites`、`ScoreHistory`、`SongNumberBook`… 全部都是），
之後只在寫入時整份覆寫回去。所以「還原 = 把檔案覆蓋掉」在跑著的機器上是**錯的**，
而且錯得非常安靜：還原完看起來完全成功（檔案真的換了、報告也說換了 N 個），
接著客人按一下收藏，`Favorites` 把記憶體裡那份**舊的**寫回去 —— 整份還原
就這樣一個檔一個檔地消失，沒有任何錯誤訊息。

解法有兩條。一條是給十幾個服務各加一支 `reload()`，然後祈禱三個月後新加的
服務記得也加一支（那正是「沒有任何測試看得到」的那種漏）。另一條是把還原
**搬到任何服務被建立之前** —— 也就是下一次啟動。這裡選第二條：

1. 上傳的備份檔先驗過（格式、版本、逐檔 SHA-256），**驗不過就在這一步擋下來**，
   一個位元組都不會寫到 `cache/` 裡。
2. 驗過的內容攤平放進 `cache/restore_pending/`，回一句「重新啟動後生效」。
   這個階段隨時可以取消（`cancel_restore`），因為還沒有動到任何東西。
3. 下次啟動時 `apply_pending_restore()` 在 `main.py` 建立任何服務之前跑完，
   結果寫成 `cache/restore_report.json`，設定頁看得到。

代價是要重開一次機器，換來的是「還原之後看到的就是還原的結果」。

## 三、還原之前，先把現況打包起來

還原是這台機器上唯一一個會**一次抹掉所有東西**的動作，而按下它的人通常
正在慌（硬碟剛換、資料剛不見、備份是三個月前的）。慌的時候最容易做的錯事
是「還原錯一份備份」，而那一下會把原本還在的東西也蓋掉。

所以套用之前一定先把現況打包成 `cache/backups/pre-restore-<時間>.zip`
（留最近 `SAFETY_KEEP` 份）。這一步失敗就**整個還原不做** —— 沒有退路的還原
不值得做。

## 四、只還原、不合併

還原是整份取代，沒有「合併」這個選項，理由在號碼簿上：兩台機器各自從 100001
開始發號，合併的結果是同一個號碼對到兩首歌 —— 那正是這個功能唯一不能出的錯。
其他幾份合併起來也都有各自的壞法（成績會重複計、設定沒有辦法「合併」），
但號碼簿那一條就足以定案。
"""
import hashlib
import io
import json
import logging
import re
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger("KaraTube.Backup")

# 備份格式版本。欄位只增不減，所以**舊備份永遠讀得進來**；
# 比這個數字新的備份一律拒絕 —— 舊機器不認得新格式，硬倒進去只會壞得很難查
# （而「壞得很難查」正是還原最不能出的事）。
BACKUP_FORMAT = 1

# 認出「這是不是 KaraTube 的備份檔」用的標記。使用者的下載資料夾裡有幾十個 zip，
# 而選錯一個檔案的代價在這個功能上特別高。
BACKUP_KIND = "karatube-backup"

MANIFEST_NAME = "karatube-backup.json"
DATA_PREFIX = "data/"
LIBRARY_NAME = "library.json"
READ_ME_NAME = "README.txt"

# 待套用的還原攤在這裡（見模組說明第二節）
PENDING_DIRNAME = "restore_pending"
# 還原前的現況備份放這裡
SAFETY_DIRNAME = "backups"
# 上一次還原的報告。設定頁讀這一份，因為還原是在使用者看不到的開機那一刻做的。
REPORT_NAME = "restore_report.json"
# 現況備份留幾份。留太多是拿磁碟換一個沒有人會翻到第六份的安心感。
SAFETY_KEEP = 5

# 備份檔上限。超過就拒絕 —— 這條路是「上傳一個檔案然後覆蓋整台機器的資料」，
# 沒有理由讓它接受一個 200MB 的東西（正常一份是幾百 KB 到幾 MB）。
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
# 解開之後的總量上限（zip bomb：50KB 的 zip 可以解出 50GB）。
MAX_EXTRACTED_BYTES = 256 * 1024 * 1024


class BackupError(Exception):
    """備份檔不能用。訊息是要直接給人看的那一句（含下一步）。"""


# --- 清單 ---------------------------------------------------------------
# (檔名, 分類, 這是什麼, 不見了會怎樣)
#
# 第四欄不是註解，是會上畫面的東西：按下「還原」之前，使用者看到的是這一欄，
# 而不是十三個檔名。

GROUP_LABELS: Dict[str, str] = {
    "promise": "不可重建的承諾",
    "people": "人累積出來的",
    "machine": "這台機器的設定",
}

GROUP_ORDER: Tuple[str, ...] = ("promise", "people", "machine")

BACKUP_FILES: Tuple[Tuple[str, str, str, str], ...] = (
    ("song_numbers.json", "promise", "歌號簿",
     "換機器之後重新發號，客人記住的六位數會指到別首歌"),
    ("lyric_offsets.json", "people", "每首歌的字幕校正",
     "有人戴著耳機一句一句調出來的偏移，重唱不會長回來"),
    ("favorites.json", "people", "我的最愛", "收藏的歌要重新找一次"),
    ("score_history.json", "people", "成績與個人最佳",
     "個人最佳紀錄歸零 —— 那是唱出來的，不是設定出來的"),
    ("vocal_range.json", "people", "每個人的音域檔案",
     "機器要重新認識每一個人的聲音（再唱幾首才給得出建議 Key）"),
    ("song_history.json", "people", "已唱歷史", "「今天唱過什麼」從頭開始"),
    ("play_stats.json", "people", "點唱排行", "熱門排行回到空的"),
    ("settings.json", "machine", "系統設定",
     "預設調音、快取上限、AI 模型選擇全部回原廠值"),
    ("rooms.json", "machine", "包廂名冊",
     "包廂要重開，而房號印在門口的 QR code 上"),
    ("local_imports.json", "machine", "本機匯入帳本",
     "匯入過的檔案認不回來，同一顆隨身碟會被重跑一次流水線"),
)

# 明確**不**備份的。列出來是為了讓「為什麼沒有我的錄音」有地方可以讀。
EXCLUDED: Tuple[Tuple[str, str], ...] = (
    ("songs/（曲庫本體）",
     "幾十 GB，而且是這份清單上唯一可以重建的東西（來源網址記在 metadata 裡）。"
     "備份裡改放一張唯讀的曲庫清單，還原後機器才講得出哪幾首要重新處理。"),
    ("recordings/（錄唱回放）",
     "量大，而且那是客人的聲音 —— 備份檔會被 email、丟進雲端硬碟，"
     "而沒有任何一位客人同意過那件事。"),
    ("staff_lock.json（櫃檯密碼）",
     "備份檔跑到哪裡密碼就跟到哪裡。還原不會動櫃檯鎖，新機器由人重設一次。"),
    ("room_timer.json / service_calls.json / contest.json（現在正在發生的事）",
     "搬機器的那一刻沒有人在唱歌。倒進來只會讓櫃檯看到一份不存在的現況。"),
    ("batch_jobs.json（排程處理）",
     "半夜那批工作屬於原來那台機器的曲庫與 CPU。"),
    ("recording_shares.json（錄音分享連結）", "指向不會一起搬過去的錄音。"),
    ("temp/、import/",
     "前者是垃圾；後者是使用者自己的原始檔案，匯入從來沒有承諾要把它們搬走。"),
)

_FILE_INDEX: Dict[str, Tuple[str, str, str, str]] = {row[0]: row for row in BACKUP_FILES}

# 哪一份 JSON 的「幾筆」要去哪裡數。按下還原之前看到的是「最愛 48 首、
# 成績 1,204 筆」，而不是「favorites.json 12KB」—— 後者沒有人判斷得出來
# 自己選對檔案了沒有。
_COUNT_SPEC: Dict[str, Tuple[str, str]] = {
    "song_numbers.json": ("songs", "首有號碼"),
    "favorites.json": ("songs", "首收藏"),
    "song_history.json": ("history", "筆"),
    "score_history.json": ("records", "筆成績"),
    "vocal_range.json": ("singers", "個人"),
    "play_stats.json": ("songs", "首有紀錄"),
    "lyric_offsets.json": ("songs", "首校正過"),
    "rooms.json": ("rooms", "間包廂"),
    "local_imports.json": ("files", "個檔案"),
}

_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


def _now_text(now: Optional[datetime] = None) -> str:
    return (now or datetime.now()).isoformat(timespec="seconds")


def _stamp(now: Optional[datetime] = None) -> str:
    return (now or datetime.now()).strftime("%Y%m%d-%H%M%S")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def count_entries(name: str, payload: bytes) -> Optional[int]:
    """
    這一份檔案裡有幾筆。數不出來回 `None` 而不是 0 —— 「0 筆」與「不知道」
    在還原前的確認畫面上是兩件完全不同的事（前者會讓人以為備份是空的）。
    """
    spec = _COUNT_SPEC.get(name)
    if not spec:
        return None
    try:
        raw = json.loads(payload.decode("utf-8"))
    except Exception:
        return None
    if not isinstance(raw, dict):
        return len(raw) if isinstance(raw, list) else None
    bucket = raw.get(spec[0])
    if isinstance(bucket, (dict, list)):
        return len(bucket)
    return None


def count_unit(name: str) -> str:
    spec = _COUNT_SPEC.get(name)
    return spec[1] if spec else "筆"


def describe_plan(cache_dir: Path) -> Dict[str, Any]:
    """
    「這份備份會裝什麼」—— 不用真的做一份就答得出來。

    做成一支唯讀的查詢是刻意的：使用者在按下載之前想知道的是內容，
    而不是先下載一個幾 MB 的 zip 再自己解開來看。
    """
    cache_dir = Path(cache_dir)
    groups: List[Dict[str, Any]] = []
    total_bytes = 0
    for key in GROUP_ORDER:
        rows: List[Dict[str, Any]] = []
        for name, group, label, risk in BACKUP_FILES:
            if group != key:
                continue
            path = cache_dir / name
            exists = path.is_file()
            payload = b""
            if exists:
                try:
                    payload = path.read_bytes()
                except OSError:
                    exists = False
            size = len(payload)
            total_bytes += size
            rows.append({
                "name": name,
                "label": label,
                "risk": risk,
                "present": exists,
                "bytes": size,
                "entries": count_entries(name, payload) if exists else None,
                "unit": count_unit(name),
            })
        groups.append({"group": key, "label": GROUP_LABELS[key], "files": rows})
    return {
        "format": BACKUP_FORMAT,
        "groups": groups,
        "total_bytes": total_bytes,
        "excluded": [{"what": what, "why": why} for what, why in EXCLUDED],
    }


def library_snapshot(rows: Optional[Iterable[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """
    備份裡那張唯讀的曲庫清單。**它永遠不會被還原成任何一個檔案** ——
    它存在的唯一理由是讓還原之後的機器講得出「號碼簿上有 312 首，
    這台機器上只有 40 首」。

    只留四個欄位：識別（song_id）、給人看的（title / artist）、
    重建用的（source）、以及號碼。縮圖與響度那些重跑流水線就會回來。
    """
    out: List[Dict[str, Any]] = []
    for row in rows or ():
        if not isinstance(row, dict):
            continue
        song_id = str(row.get("song_id") or row.get("id") or "").strip()
        if not song_id:
            continue
        entry: Dict[str, Any] = {"song_id": song_id}
        for key in ("title", "artist", "source", "url", "number"):
            value = row.get(key)
            if value not in (None, ""):
                entry[key] = value
        out.append(entry)
    out.sort(key=lambda r: str(r.get("song_id")))
    return out


def create_archive(cache_dir: Path, *, app_version: str = "",
                   library: Optional[Iterable[Dict[str, Any]]] = None,
                   now: Optional[datetime] = None) -> bytes:
    """
    打一份備份出來（回傳 zip 的位元組，不落地）。

    不落地是刻意的：備份的用途是**帶走**，而寫進 `cache/` 的備份會跟著
    原本那顆硬碟一起壞掉 —— 那正是它要防的事。
    （還原前的現況備份是另一回事，它的用途是「馬上退回去」，見
    `_write_safety_copy`。）
    """
    cache_dir = Path(cache_dir)
    files: List[Dict[str, Any]] = []
    buf = io.BytesIO()
    snapshot = library_snapshot(library)
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, group, label, _risk in BACKUP_FILES:
            path = cache_dir / name
            if not path.is_file():
                # 沒有這個檔案是正常的（還沒有人收藏過任何一首歌）。
                # 記成 present=False 而不是塞一個空的 {} 進去 —— 還原的時候
                # 「原本就沒有」跟「有，但是空的」要分得開。
                continue
            try:
                payload = path.read_bytes()
            except OSError as exc:
                logger.warning("備份讀不到 %s: %s", name, exc)
                continue
            zf.writestr(DATA_PREFIX + name, payload)
            files.append({
                "name": name,
                "group": group,
                "label": label,
                "bytes": len(payload),
                "sha256": _sha256(payload),
                "entries": count_entries(name, payload),
            })
        manifest = {
            "kind": BACKUP_KIND,
            "format": BACKUP_FORMAT,
            "created_at": _now_text(now),
            "app_version": str(app_version or ""),
            "files": files,
            "library_songs": len(snapshot),
            "excluded": [{"what": what, "why": why} for what, why in EXCLUDED],
        }
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2))
        zf.writestr(LIBRARY_NAME, json.dumps(
            {"songs": snapshot}, ensure_ascii=False, indent=2))
        zf.writestr(READ_ME_NAME, _readme_text(manifest))
    return buf.getvalue()


def _readme_text(manifest: Dict[str, Any]) -> str:
    """
    解開 zip 的人第一眼看到的那一段。

    寫這一份的理由跟錯誤訊息一樣：拿到這個檔案的人不一定記得它是什麼，
    而三個月後在雲端硬碟裡翻到 `karatube-backup-20260930.zip` 的那個人，
    最需要知道的是「裡面**沒有**歌」。
    """
    lines = [
        "KaraTube 備份檔",
        "",
        f"建立時間：{manifest.get('created_at', '')}",
        f"機器版本：{manifest.get('app_version', '') or '未知'}",
        f"備份格式：{manifest.get('format', '')}",
        "",
        "這份備份裝的是「這台機器認得的人事物」：",
    ]
    for row in manifest.get("files", []):
        entries = row.get("entries")
        tail = f"（{entries} {count_unit(row.get('name', ''))}）" if entries is not None else ""
        lines.append(f"  - {row.get('label', '')}{tail}")
    lines += [
        "",
        "這份備份**沒有**裝：",
    ]
    for what, why in EXCLUDED:
        lines.append(f"  - {what}：{why}")
    lines += [
        "",
        "還原方式：系統設定 → 備份與還原 → 選擇這個檔案 → 確認還原，",
        "然後重新啟動 KaraTube（還原在開機時套用，見 backend/services/backup.py）。",
        "",
        f"曲庫清單（{manifest.get('library_songs', 0)} 首）在 {LIBRARY_NAME}：",
        "歌本身不在備份裡，但那張清單帶著來源網址，重新處理就會回來。",
    ]
    return "\n".join(lines) + "\n"


def backup_filename(app_version: str = "", now: Optional[datetime] = None) -> str:
    tag = f"-v{app_version}" if app_version else ""
    return f"karatube-backup{tag}-{_stamp(now)}.zip"


# --- 讀一份備份 ---------------------------------------------------------


def _open_archive(data: bytes) -> zipfile.ZipFile:
    if not data:
        raise BackupError("檔案是空的。請重新選擇一份 KaraTube 備份檔。")
    if len(data) > MAX_ARCHIVE_BYTES:
        raise BackupError(
            f"檔案太大（{len(data) // (1024 * 1024)}MB）。"
            "KaraTube 的備份通常只有幾 MB —— 這可能不是備份檔。")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise BackupError("這不是一個 zip 檔。請選擇 KaraTube 產生的備份檔（.zip）。") from exc
    total = sum(max(0, info.file_size) for info in zf.infolist())
    if total > MAX_EXTRACTED_BYTES:
        raise BackupError("這個檔案解開之後太大了，不像是 KaraTube 的備份檔。")
    return zf


def read_manifest(data: bytes) -> Dict[str, Any]:
    """
    把清單讀出來，並且**在還原前**就判定這份檔案能不能用。

    認不得的一律在這裡擋掉，而不是倒到一半才發現 —— 還原是覆寫，
    倒到一半的失敗比一開始就拒絕糟糕得多。
    """
    zf = _open_archive(data)
    try:
        raw = zf.read(MANIFEST_NAME)
    except KeyError as exc:
        raise BackupError(
            "這個 zip 裡沒有 KaraTube 的備份清單。"
            "請選擇「系統設定 → 備份與還原 → 下載備份」產生的那個檔案。") from exc
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise BackupError(
            "備份清單讀不出來（檔案可能在傳輸中損壞）。請重新取得一份備份。") from exc
    if not isinstance(manifest, dict) or manifest.get("kind") != BACKUP_KIND:
        raise BackupError("這不是 KaraTube 的備份檔。")
    try:
        fmt = int(manifest.get("format", 0))
    except (TypeError, ValueError):
        fmt = 0
    if fmt <= 0:
        raise BackupError("備份清單缺少格式版本，無法判斷能不能還原。")
    if fmt > BACKUP_FORMAT:
        raise BackupError(
            f"這份備份是較新的格式（v{fmt}），這台機器只認得到 v{BACKUP_FORMAT}。"
            "請先把 KaraTube 升級到產生這份備份的版本再還原。")
    return manifest


def inspect_archive(data: bytes, cache_dir: Path,
                    current_songs: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """
    「按下去會發生什麼事」的那張表。**不寫任何東西。**

    這一步存在的理由是還原沒有 undo：現況備份救得回來，但前提是那個人
    知道自己按錯了。所以確認畫面上要有三種東西 —— 這份備份是什麼時候的、
    每一項會從幾筆變成幾筆、以及還原之後有幾首歌不在這台機器上。
    """
    manifest = read_manifest(data)
    zf = _open_archive(data)
    cache_dir = Path(cache_dir)

    names_in_zip = set(zf.namelist())
    rows: List[Dict[str, Any]] = []
    missing: List[Dict[str, str]] = []
    problems: List[str] = []
    listed = {str(row.get("name", "")): row
              for row in manifest.get("files", []) if isinstance(row, dict)}

    for name, group, label, risk in BACKUP_FILES:
        arc = DATA_PREFIX + name
        current = cache_dir / name
        current_entries = None
        if current.is_file():
            try:
                current_entries = count_entries(name, current.read_bytes())
            except OSError:
                current_entries = None
        if arc not in names_in_zip:
            # 備份裡沒有這一份，通常是產生備份的那台機器上本來就沒有
            # （沒有人收藏過任何一首歌）。這不是錯誤，但**要講出來**：
            # 還原是整份取代，所以這台機器上現有的那一份會被清掉。
            missing.append({"name": name, "label": label, "group": group,
                            "current_entries": current_entries})
            continue
        payload = zf.read(arc)
        want = str(listed.get(name, {}).get("sha256") or "")
        if want and want != _sha256(payload):
            problems.append(f"{label}（{name}）的內容與清單對不起來，檔案可能損壞。")
        try:
            json.loads(payload.decode("utf-8"))
        except Exception:
            problems.append(f"{label}（{name}）不是有效的 JSON，這份備份不完整。")
        rows.append({
            "name": name, "group": group, "label": label, "risk": risk,
            "bytes": len(payload),
            "entries": count_entries(name, payload),
            "current_entries": current_entries,
            "unit": count_unit(name),
        })

    unknown = sorted(
        n[len(DATA_PREFIX):] for n in names_in_zip
        if n.startswith(DATA_PREFIX) and n[len(DATA_PREFIX):] not in _FILE_INDEX
        and n != DATA_PREFIX)

    songs = _library_coverage(zf, current_songs)

    report: Dict[str, Any] = {
        "ok": not problems and bool(rows),
        "created_at": manifest.get("created_at", ""),
        "app_version": manifest.get("app_version", ""),
        "format": manifest.get("format", 0),
        "files": rows,
        "missing": missing,
        "unknown": unknown,
        "problems": problems,
        "songs": songs,
        "excluded": [{"what": what, "why": why} for what, why in EXCLUDED],
    }
    if not rows and not problems:
        # 一份什麼都沒有的備份倒下去會把這台機器清空，而使用者以為自己在還原。
        report["problems"] = ["這份備份裡一個資料檔都沒有 —— 還原它等於把這台機器清空。"]
        report["ok"] = False
    return report


def _library_coverage(zf: zipfile.ZipFile,
                      current_songs: Optional[Iterable[str]]) -> Dict[str, Any]:
    """
    還原之後有幾首歌不在這台機器上。

    這個數字是「曲庫不進備份」這個決定的**配套**：不講的話，還原完那幾百個
    歌號會無聲地變成墓碑，而打進去得到的是「這首歌已經不在曲庫了」——
    使用者會以為還原失敗了，而其實還原是成功的，缺的是歌本身。
    """
    have = {str(s) for s in (current_songs or ()) if s}
    try:
        raw = json.loads(zf.read(LIBRARY_NAME).decode("utf-8"))
        rows = raw.get("songs", []) if isinstance(raw, dict) else []
    except Exception:
        # 舊格式或壞掉的清單：講「不知道」，不要編一個數字出來。
        return {"known": False, "in_backup": 0, "on_machine": len(have),
                "matched": 0, "missing": 0, "examples": []}
    ids = [str(r.get("song_id")) for r in rows
           if isinstance(r, dict) and r.get("song_id")]
    matched = [i for i in ids if i in have]
    gone = [r for r in rows
            if isinstance(r, dict) and str(r.get("song_id")) not in have]
    return {
        "known": True,
        "in_backup": len(ids),
        "on_machine": len(have),
        "matched": len(matched),
        "missing": len(gone),
        # 只給幾個例子：一張 272 首的清單在確認畫面上沒有人讀得完，
        # 而「有哪些」的完整答案在備份檔的 library.json 裡。
        "examples": [
            {"song_id": str(r.get("song_id")), "title": r.get("title", ""),
             "number": r.get("number"), "source": r.get("source") or r.get("url") or ""}
            for r in gone[:8]
        ],
    }


def library_rows(data: bytes) -> List[Dict[str, Any]]:
    """備份裡那張曲庫清單（讓人可以拿去重新處理）。讀不到就回空的。"""
    try:
        zf = _open_archive(data)
        raw = json.loads(zf.read(LIBRARY_NAME).decode("utf-8"))
    except Exception:
        return []
    rows = raw.get("songs", []) if isinstance(raw, dict) else []
    return [r for r in rows if isinstance(r, dict) and r.get("song_id")]


# --- 待套用的還原 -------------------------------------------------------


def pending_dir(cache_dir: Path) -> Path:
    return Path(cache_dir) / PENDING_DIRNAME


def stage_restore(data: bytes, cache_dir: Path,
                  current_songs: Optional[Iterable[str]] = None,
                  now: Optional[datetime] = None) -> Dict[str, Any]:
    """
    驗過之後攤進 `cache/restore_pending/`，等下次開機套用。

    **這一步不動 `cache/` 裡任何一份現有資料**，所以隨時可以取消。
    寫進去的是驗過的內容而不是原始 zip：開機那一刻再解一次 zip 的話，
    解不開就變成「開機失敗」，而那是最不該在無人看管時發生的事。
    """
    report = inspect_archive(data, cache_dir, current_songs)
    if not report.get("ok"):
        raise BackupError(report.get("problems", ["這份備份不能用。"])[0])

    zf = _open_archive(data)
    folder = pending_dir(cache_dir)
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True, exist_ok=True)

    written: List[str] = []
    for row in report["files"]:
        name = row["name"]
        if not _SAFE_NAME.match(name) or name not in _FILE_INDEX:
            # 走不到（清單是白名單），但還原這條路上寧可多擋一次：
            # 這裡是唯一一個「外面來的名字會變成寫入路徑」的地方。
            continue
        (folder / name).write_bytes(zf.read(DATA_PREFIX + name))
        written.append(name)

    plan = {
        "staged_at": _now_text(now),
        "created_at": report.get("created_at", ""),
        "app_version": report.get("app_version", ""),
        "format": report.get("format", 0),
        "files": [dict(row) for row in report["files"]],
        "clears": [dict(row) for row in report["missing"]],
        "songs": report.get("songs", {}),
        "written": written,
    }
    (folder / MANIFEST_NAME).write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return plan


def pending_restore(cache_dir: Path) -> Optional[Dict[str, Any]]:
    """有沒有一份還原在等著開機。沒有回 None。"""
    path = pending_dir(cache_dir) / MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("待套用的還原讀不出來: %s", exc)
        return None
    return raw if isinstance(raw, dict) else None


def cancel_restore(cache_dir: Path) -> bool:
    """取消還沒套用的還原。取消得掉正是「延後到開機」換來的東西。"""
    folder = pending_dir(cache_dir)
    if not folder.exists():
        return False
    shutil.rmtree(folder, ignore_errors=True)
    return True


# --- 開機時套用 ---------------------------------------------------------


def _write_safety_copy(cache_dir: Path, app_version: str,
                       now: Optional[datetime] = None) -> Optional[str]:
    """還原之前把現況打包起來（見模組說明第三節）。失敗回 None。"""
    folder = Path(cache_dir) / SAFETY_DIRNAME
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"pre-restore-{_stamp(now)}.zip"
    try:
        target.write_bytes(create_archive(cache_dir, app_version=app_version, now=now))
    except Exception as exc:
        logger.error("還原前的現況備份寫不出來: %s", exc)
        return None
    _prune_safety(folder)
    return target.name


def _prune_safety(folder: Path):
    try:
        zips = sorted((p for p in folder.glob("pre-restore-*.zip") if p.is_file()),
                      key=lambda p: p.name)
    except OSError:
        return
    for stale in zips[:-SAFETY_KEEP]:
        try:
            stale.unlink()
        except OSError:
            pass


def apply_pending_restore(cache_dir: Path, app_version: str = "",
                          now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """
    開機時套用還原。**一定要在任何服務被建立之前呼叫**（見模組說明第二節）。

    順序是不能換的：先打包現況，再覆寫。現況打包失敗就整個不做 ——
    一個沒有退路的還原不值得做，而「今天先不還原」是使用者按一下就能重來的事，
    「原本的資料也沒了」不是。
    """
    plan = pending_restore(cache_dir)
    if plan is None:
        return None
    cache_dir = Path(cache_dir)
    folder = pending_dir(cache_dir)

    safety = _write_safety_copy(cache_dir, app_version, now)
    if safety is None:
        report = {
            "status": "aborted",
            "applied_at": _now_text(now),
            "reason": "還原前的現況備份寫不出來（磁碟可能滿了或沒有寫入權限）。"
                      "為了不讓現有資料回不去，這次還原沒有進行 —— "
                      "清出空間之後重新啟動就會再試一次。",
            "restored": [], "cleared": [], "failed": [],
            "source_created_at": plan.get("created_at", ""),
        }
        _write_report(cache_dir, report)
        # 刻意**不**刪掉 pending：磁碟清乾淨之後重開機應該自動接著做，
        # 而不是要使用者記得再上傳一次那個檔案。
        return report

    restored: List[Dict[str, Any]] = []
    cleared: List[Dict[str, Any]] = []
    failed: List[Dict[str, str]] = []

    for row in plan.get("files", []):
        name = str(row.get("name", ""))
        if name not in _FILE_INDEX or not _SAFE_NAME.match(name):
            continue
        src = folder / name
        if not src.is_file():
            failed.append({"name": name, "label": row.get("label", ""),
                           "error": "待套用的檔案不見了"})
            continue
        try:
            shutil.copyfile(src, cache_dir / name)
            restored.append({"name": name, "label": row.get("label", ""),
                             "entries": row.get("entries")})
        except OSError as exc:
            failed.append({"name": name, "label": row.get("label", ""), "error": str(exc)})

    # 備份裡沒有的那幾份要清掉 —— 還原是整份取代，留著等於把兩台機器的資料
    # 混在一起，而那正是「只還原、不合併」要避免的事（見模組說明第四節）。
    for row in plan.get("clears", []):
        name = str(row.get("name", ""))
        if name not in _FILE_INDEX or not _SAFE_NAME.match(name):
            continue
        target = cache_dir / name
        if not target.is_file():
            continue
        try:
            target.unlink()
            cleared.append({"name": name, "label": row.get("label", "")})
        except OSError as exc:
            failed.append({"name": name, "label": row.get("label", ""), "error": str(exc)})

    shutil.rmtree(folder, ignore_errors=True)

    report = {
        "status": "done" if not failed else "partial",
        "applied_at": _now_text(now),
        "source_created_at": plan.get("created_at", ""),
        "source_version": plan.get("app_version", ""),
        "safety_copy": safety,
        "restored": restored,
        "cleared": cleared,
        "failed": failed,
        "songs": plan.get("songs", {}),
    }
    _write_report(cache_dir, report)
    logger.info("已套用還原：%d 份還原、%d 份清除、%d 份失敗（現況備份 %s）",
                len(restored), len(cleared), len(failed), safety)
    return report


def _write_report(cache_dir: Path, report: Dict[str, Any]):
    try:
        (Path(cache_dir) / REPORT_NAME).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("還原報告寫不出來: %s", exc)


def last_report(cache_dir: Path) -> Optional[Dict[str, Any]]:
    """
    上一次還原的結果。

    要留下來是因為還原發生在**沒有人看著的那一刻**（開機）：按下確認的人
    看到的是「重新啟動後生效」，而真正的結果要等他回來才看得到。
    """
    path = Path(cache_dir) / REPORT_NAME
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return raw if isinstance(raw, dict) else None


def safety_copies(cache_dir: Path) -> List[Dict[str, Any]]:
    """還原前留下來的那幾份現況備份（最新的在前面）。"""
    folder = Path(cache_dir) / SAFETY_DIRNAME
    if not folder.is_dir():
        return []
    rows: List[Dict[str, Any]] = []
    for path in folder.glob("pre-restore-*.zip"):
        try:
            stat = path.stat()
        except OSError:
            continue
        rows.append({
            "name": path.name,
            "bytes": stat.st_size,
            "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        })
    rows.sort(key=lambda r: r["name"], reverse=True)
    return rows


def safety_path(cache_dir: Path, name: str) -> Optional[Path]:
    """
    現況備份的檔案位置。名字是外面傳進來的，所以只認 `pre-restore-*.zip`
    這個形狀 —— 這條路是「給一個名字就把檔案讀出去」，路徑穿越要在這裡擋掉。
    """
    safe = str(name or "")
    if not _SAFE_NAME.match(safe) or not safe.startswith("pre-restore-") \
            or not safe.endswith(".zip"):
        return None
    path = Path(cache_dir) / SAFETY_DIRNAME / safe
    return path if path.is_file() else None
