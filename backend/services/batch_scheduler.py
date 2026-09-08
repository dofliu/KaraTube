"""
排程預處理 (Scheduled Batch Pre-processing)

商用點歌機的曲庫是「已經備好」的，客人點下去就開始播；KaraTube 是隨選處理，
第一次點一首新歌要等下載＋分離＋對字幕好幾分鐘。這一頁把等待挪到沒人唱的時候：
晚上把整張播放清單丟進來，半夜自己跑完，隔天所有歌都是「⚡ 快取秒播」。

設計上有三條紅線，順序就是優先度：

1. **有人在唱歌就不准動。** 流水線吃滿 GPU 與 CPU，跟現場演唱搶資源會讓
   舞台端掉幀、字幕跳針。所以每處理完「一首」就重新問一次「現在可以跑嗎」——
   不是每批問一次，讓客人隨時進包廂都能立刻搶回機器。
2. **時段是預設，不是牢籠。** 預設只在 02:00–06:00 開工，但建立任務時可以按
   「立即開始」（force_run），也可以把時段設成一樣的起訖時間代表全天候。
3. **失敗不影響後面的歌。** 一首歌下載失敗（影片被下架、網路斷線）就標成 ERROR
   繼續下一首，整批不會卡死；事後在 UI 上按重試把失敗的挑出來重跑。

任務存在 `cache/batch_jobs.json`，重開機續跑（RUNNING 的項目會退回 PENDING，
因為那首歌八成處理到一半就被中斷了）。
"""
import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("KaraTube.BatchScheduler")

# 保留多少筆歷史任務。跑完的任務還留著是為了「昨天那批到底有沒有跑完」，
# 但不需要無限長 —— 每筆任務可能有上百首歌，檔案會胖得很快。
MAX_JOBS = 30
# 一批最多幾首。貼進來一張 500 首的播放清單多半是誤操作，
# 而且整晚也跑不完（一首歌平均要好幾分鐘）。
MAX_ITEMS_PER_JOB = 200
# 排程迴圈多久醒來看一次。醒來只做幾個判斷，成本可以忽略；
# 20 秒的解析度對「等一首歌唱完」這種等待來說綽綽有餘。
TICK_SECONDS = 20

YOUTUBE_ID_RE = re.compile(r"^[0-9A-Za-z_-]{11}$")
YOUTUBE_URL_ID_RE = re.compile(r"(?:v=|/shorts/|/embed/|youtu\.be/)([0-9A-Za-z_-]{11})")

# 項目狀態
PENDING, RUNNING, DONE, ERROR, SKIPPED = "PENDING", "RUNNING", "DONE", "ERROR", "SKIPPED"
# 任務狀態
SCHEDULED, JOB_RUNNING, FINISHED, CANCELLED = "SCHEDULED", "RUNNING", "FINISHED", "CANCELLED"


def extract_video_id(text: str) -> Optional[str]:
    """從一行文字裡取出 YouTube 影片 ID。取不到（關鍵字、播放清單網址）回傳 None。"""
    line = (text or "").strip()
    if not line:
        return None
    if YOUTUBE_ID_RE.match(line):
        return line
    match = YOUTUBE_URL_ID_RE.search(line)
    return match.group(1) if match else None


def split_source_lines(text: str) -> List[str]:
    """
    把使用者貼進來的一坨文字切成一行一個來源。

    換行、逗號、分號都當分隔（手機上貼過來的清單常常是逗號串起來的），
    但**不切空白** —— 「周杰倫 稻香」是一個關鍵字，不是兩個。
    重複的來源只留第一個：同一首歌在播放清單裡出現兩次很常見，沒必要跑兩遍。
    """
    if not isinstance(text, str):
        return []
    raw = re.split(r"[\n\r,;]+", text)
    seen, lines = set(), []
    for part in raw:
        line = part.strip()
        if not line or line in seen:
            continue
        seen.add(line)
        lines.append(line)
    return lines


def in_window(now: datetime, start_hour: int, end_hour: int) -> bool:
    """
    現在是不是在排程時段內。

    起訖相同代表「全天候」（使用者刻意把時段關掉的講法），
    跨午夜（23 → 6）也要成立 —— 那正好是最常設的時段。
    """
    start = int(start_hour) % 24
    end = int(end_hour) % 24
    if start == end:
        return True
    hour = now.hour
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def minutes_until_window(now: datetime, start_hour: int, end_hour: int) -> int:
    """離下一次開工還有幾分鐘。已經在時段內回傳 0。"""
    if in_window(now, start_hour, end_hour):
        return 0
    start = int(start_hour) % 24
    target = now.replace(hour=start, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return max(0, int((target - now).total_seconds() // 60))


def make_item(source: Dict[str, Any]) -> Dict[str, Any]:
    """把一筆來源資料轉成任務項目。song_id 缺失時用原始文字當識別。"""
    song_id = str(source.get("song_id") or source.get("id") or "").strip()
    title = str(source.get("title") or "").strip()
    return {
        "song_id": song_id,
        "title": title or song_id or "（未知歌曲）",
        "artist": str(source.get("artist") or source.get("uploader") or "").strip(),
        "thumbnail": str(source.get("thumbnail") or "").strip(),
        "url": str(source.get("url") or (f"https://www.youtube.com/watch?v={song_id}" if song_id else "")),
        "status": PENDING,
        "status_text": "等待處理",
        "progress": 0,
        "error": "",
    }


def job_progress(job: Dict[str, Any]) -> Dict[str, Any]:
    """一筆任務的統計：各狀態幾首、完成百分比。UI 的進度條直接吃這個。"""
    items = job.get("items", [])
    total = len(items)
    counts = {PENDING: 0, RUNNING: 0, DONE: 0, ERROR: 0, SKIPPED: 0}
    for item in items:
        counts[item.get("status", PENDING)] = counts.get(item.get("status", PENDING), 0) + 1
    settled = counts[DONE] + counts[ERROR] + counts[SKIPPED]
    return {
        "total": total,
        "pending": counts[PENDING],
        "running": counts[RUNNING],
        "done": counts[DONE],
        "error": counts[ERROR],
        "skipped": counts[SKIPPED],
        "settled": settled,
        "percent": round(settled / total * 100) if total else 0,
    }


class BatchScheduler:
    def __init__(self, processor: Any, storage: Any, jobs_file: Path,
                 settings: Optional[Any] = None,
                 broadcast_cb: Optional[Callable] = None,
                 busy_cb: Optional[Callable[[], bool]] = None,
                 clock: Optional[Callable[[], datetime]] = None):
        self.processor = processor
        self.storage = storage
        self.jobs_file = Path(jobs_file)
        self.settings = settings
        self.broadcast_cb = broadcast_cb
        # 「現在有人在唱歌嗎」。沒給就當作永遠不忙（測試與純批次伺服器用）。
        self.busy_cb = busy_cb or (lambda: False)
        self._now = clock or datetime.now

        self.jobs: List[Dict[str, Any]] = []
        # 「立即開始」：忽略時段限制直到沒有待處理的歌為止。
        # 要持久化 —— 半夜按了立即開始、伺服器重啟，使用者的意圖不該被吃掉。
        self.force_run = False
        self._working = False          # run_once 重入防護
        self._task: Optional[asyncio.Task] = None
        self._last_reason = ""
        self._load()

    # --- 設定 ---

    def _setting(self, key: str, fallback: Any) -> Any:
        if not self.settings:
            return fallback
        value = self.settings.get(key, fallback)
        return fallback if value is None else value

    @property
    def enabled(self) -> bool:
        return bool(self._setting("batch_enabled", True))

    @property
    def start_hour(self) -> int:
        return int(self._setting("batch_start_hour", 2))

    @property
    def end_hour(self) -> int:
        return int(self._setting("batch_end_hour", 6))

    @property
    def pause_while_singing(self) -> bool:
        return bool(self._setting("batch_pause_while_singing", True))

    # --- 持久化 ---

    def _load(self):
        if not self.jobs_file.exists():
            return
        try:
            raw = json.loads(self.jobs_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"排程任務檔讀取失敗，從空的開始: {e}")
            return
        if not isinstance(raw, dict):
            return
        jobs = raw.get("jobs")
        if isinstance(jobs, list):
            self.jobs = [j for j in jobs if isinstance(j, dict) and j.get("items") is not None]
        self.force_run = bool(raw.get("force_run", False))
        # 上次關機時正在處理的那首歌八成沒跑完，退回 PENDING 重跑。
        # 流水線本來就會重用已下載的檔案，重跑的代價比留下半成品小得多。
        for job in self.jobs:
            for item in job.get("items", []):
                if item.get("status") == RUNNING:
                    item["status"] = PENDING
                    item["status_text"] = "等待處理（上次中斷）"
                    item["progress"] = 0
            if job.get("status") == JOB_RUNNING:
                job["status"] = SCHEDULED

    def _save(self):
        try:
            self.jobs_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "updated_at": self._now().isoformat(timespec="seconds"),
                "force_run": self.force_run,
                "jobs": self.jobs[-MAX_JOBS:],
            }
            self.jobs_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as e:
            logger.warning(f"排程任務檔寫入失敗: {e}")

    # --- 任務管理 ---

    def create_job(self, sources: List[Dict[str, Any]], name: str = "",
                   start_now: bool = False, requested_by: str = "") -> Dict[str, Any]:
        """
        建立一筆排程任務。sources 是已經展開好的歌曲清單（展開要連網，所以在外面做）。

        同一批裡重複的 song_id 只留一筆；已經在曲庫裡而且檔案齊全的直接標成
        SKIPPED —— 讓使用者一眼看出「這張清單其實只有 3 首要跑」。
        """
        items, seen = [], set()
        for source in sources[:MAX_ITEMS_PER_JOB]:
            item = make_item(source)
            key = item["song_id"] or item["title"]
            if key in seen:
                continue
            seen.add(key)
            if item["song_id"] and self._already_cached(item["song_id"]):
                item["status"] = SKIPPED
                item["status_text"] = "已在曲庫中"
                item["progress"] = 100
            items.append(item)

        now = self._now()
        job = {
            "job_id": str(uuid.uuid4()),
            "name": (name or f"排程 {now.strftime('%m/%d %H:%M')}").strip()[:40],
            "created_at": now.isoformat(timespec="seconds"),
            "started_at": None,
            "finished_at": None,
            "status": SCHEDULED,
            "requested_by": str(requested_by or "").strip()[:24],
            "items": items,
        }
        # 整批都已經在曲庫裡就不用等排程時段了，直接標完成
        if not any(i["status"] == PENDING for i in items):
            job["status"] = FINISHED
            job["finished_at"] = now.isoformat(timespec="seconds")
        self.jobs.append(job)
        del self.jobs[:-MAX_JOBS]
        if start_now and job["status"] != FINISHED:
            self.force_run = True
        self._save()
        return job

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        return next((j for j in self.jobs if j.get("job_id") == job_id), None)

    def cancel_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """取消任務：還沒跑的項目標成取消，已經跑完的保留紀錄。正在跑的那首讓它跑完。"""
        job = self.get_job(job_id)
        if job is None:
            return None
        for item in job["items"]:
            if item["status"] == PENDING:
                item["status"] = SKIPPED
                item["status_text"] = "已取消"
        job["status"] = CANCELLED
        job["finished_at"] = self._now().isoformat(timespec="seconds")
        self._sync_force_flag()
        self._save()
        return job

    def delete_job(self, job_id: str) -> bool:
        before = len(self.jobs)
        self.jobs = [j for j in self.jobs if j.get("job_id") != job_id]
        if len(self.jobs) == before:
            return False
        self._sync_force_flag()
        self._save()
        return True

    def retry_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """把失敗的項目重新排進去。整批重跑不是重點，重點是「昨晚壞掉的那 3 首」。"""
        job = self.get_job(job_id)
        if job is None:
            return None
        retried = 0
        for item in job["items"]:
            if item["status"] == ERROR:
                item.update({"status": PENDING, "status_text": "等待重試",
                             "progress": 0, "error": ""})
                retried += 1
        if retried:
            job["status"] = SCHEDULED
            job["finished_at"] = None
        self._save()
        return job

    def clear_finished(self) -> int:
        """清掉已完成／已取消的任務紀錄。還有待處理項目的一律不動。"""
        keep, removed = [], 0
        for job in self.jobs:
            stats = job_progress(job)
            if stats["pending"] or stats["running"]:
                keep.append(job)
            else:
                removed += 1
        self.jobs = keep
        self._save()
        return removed

    def set_force_run(self, value: bool) -> bool:
        """立即開始 / 回到照表操課。"""
        self.force_run = bool(value) and self.pending_count() > 0
        self._save()
        return self.force_run

    # --- 排程判斷 ---

    def pending_count(self) -> int:
        return sum(job_progress(job)["pending"] for job in self.jobs)

    def _already_cached(self, song_id: str) -> bool:
        try:
            return bool(self.storage.is_song_complete(song_id))
        except AttributeError:      # 舊版 storage 沒有這支，退回只看 metadata
            return self.storage.get_song_metadata(song_id) is not None
        except Exception:
            return False

    def run_decision(self, now: Optional[datetime] = None) -> Tuple[bool, str]:
        """
        現在能不能處理下一首。回傳 (可以嗎, 給人看的理由)。

        理由字串會直接顯示在 UI 上 —— 「為什麼半夜兩點了還沒開始跑」
        是這個功能最容易被問的問題，所以答案要一直擺在畫面上。
        """
        now = now or self._now()
        if self.pending_count() == 0:
            return False, "沒有待處理的歌曲"
        if not self.enabled:
            return False, "排程預處理已關閉（可在系統設定開啟）"
        if self.pause_while_singing and self.busy_cb():
            return False, "現在有人在唱歌，等舞台空下來再繼續"
        if self.force_run:
            return True, "立即開始（不等排程時段）"
        if in_window(now, self.start_hour, self.end_hour):
            return True, f"排程時段內（{self.start_hour:02d}:00–{self.end_hour:02d}:00）"
        mins = minutes_until_window(now, self.start_hour, self.end_hour)
        hours, rem = divmod(mins, 60)
        wait = f"{hours} 小時 {rem} 分" if hours else f"{rem} 分"
        return False, f"等待排程時段 {self.start_hour:02d}:00 開始（還有 {wait}）"

    def _next_pending(self) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
        """先進先出：先建立的任務先跑完，同一批照貼上的順序。"""
        for job in self.jobs:
            if job.get("status") == CANCELLED:
                continue
            for item in job.get("items", []):
                if item.get("status") == PENDING:
                    return job, item
        return None

    def _sync_force_flag(self):
        """沒有待處理的歌就把「立即開始」收回來，下一批照樣照表操課。"""
        if self.force_run and self.pending_count() == 0:
            self.force_run = False

    # --- 執行 ---

    async def _broadcast(self):
        if self.broadcast_cb:
            try:
                await self.broadcast_cb({"type": "BATCH_UPDATE", "data": self.state()})
            except Exception as e:
                logger.warning(f"排程狀態廣播失敗: {e}")

    async def run_once(self, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
        """
        處理**一首**歌就回來。回傳處理過的項目，什麼都沒做時回 None。

        刻意一次只跑一首：跑完一首就重新問「現在可以跑嗎」，
        客人推門進包廂點第一首歌時，機器最多再忙一首歌的時間就會讓開。
        """
        if self._working:
            return None
        ok, reason = self.run_decision(now)
        self._last_reason = reason
        if not ok:
            return None
        found = self._next_pending()
        if found is None:
            return None

        job, item = found
        self._working = True
        item["status"] = RUNNING
        item["status_text"] = "處理中…"
        item["progress"] = 0
        if job["status"] != JOB_RUNNING:
            job["status"] = JOB_RUNNING
            job["started_at"] = job.get("started_at") or self._now().isoformat(timespec="seconds")
        self._save()
        await self._broadcast()

        def on_progress(_sid, msg, pct):
            item["status_text"] = msg
            item["progress"] = max(0, min(100, int(pct))) if pct >= 0 else item["progress"]

        try:
            target = item["url"] or item["song_id"]
            meta = await self.processor.process_song(target, progress_callback=on_progress)
            item["title"] = meta.get("title", item["title"]) or item["title"]
            item["artist"] = meta.get("artist", item["artist"]) or item["artist"]
            item["thumbnail"] = meta.get("thumbnail", item["thumbnail"]) or item["thumbnail"]
            item["status"] = DONE
            item["status_text"] = "已完成"
            item["progress"] = 100
            logger.info(f"排程預處理完成: {item['title']}")
        except Exception as e:
            # 一首失敗不能拖垮整批：標記後繼續下一首，事後可以只重試失敗的
            item["status"] = ERROR
            item["status_text"] = "處理失敗"
            item["error"] = str(e)[:200]
            item["progress"] = 0
            logger.warning(f"排程預處理失敗 {item['song_id']}: {e}")
        finally:
            self._working = False
            self._finalize_job(job)
            self._sync_force_flag()
            self._save()
            await self._broadcast()

        return item

    def _finalize_job(self, job: Dict[str, Any]):
        stats = job_progress(job)
        if stats["pending"] == 0 and stats["running"] == 0 and job["status"] != CANCELLED:
            job["status"] = FINISHED
            job["finished_at"] = self._now().isoformat(timespec="seconds")

    async def _loop(self):
        logger.info("排程預處理迴圈已啟動")
        while True:
            try:
                await asyncio.sleep(TICK_SECONDS)
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:      # 迴圈永遠不能死，死了就再也不會有人處理
                logger.error(f"排程迴圈發生未預期錯誤: {e}")

    def start(self):
        """啟動背景迴圈。重複呼叫安全（已經在跑就不動）。"""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())
        return self._task

    async def stop(self):
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    # --- 對外狀態 ---

    def state(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        now = now or self._now()
        can_run, reason = self.run_decision(now)
        return {
            "enabled": self.enabled,
            "window": {
                "start_hour": self.start_hour,
                "end_hour": self.end_hour,
                "all_day": self.start_hour % 24 == self.end_hour % 24,
                "in_window": in_window(now, self.start_hour, self.end_hour),
                "minutes_until": minutes_until_window(now, self.start_hour, self.end_hour),
            },
            "force_run": self.force_run,
            "pause_while_singing": self.pause_while_singing,
            "stage_busy": bool(self.busy_cb()),
            "can_run": can_run,
            "reason": reason,
            "working": self._working,
            "pending_total": self.pending_count(),
            "jobs": [{**job, "progress": job_progress(job)} for job in reversed(self.jobs)],
        }
