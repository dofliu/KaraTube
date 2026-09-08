"""排程預處理的單元測試。

重點在三件事，因為它們是這個功能唯一會傷到人的地方：
  1. 時段判斷（含跨午夜）—— 算錯就變成尖峰時段搶 GPU。
  2. 有人在唱歌時絕不開工 —— 這條破功，客人唱到一半舞台就會掉幀。
  3. 一首失敗不拖垮整批 —— 40 首裡壞 1 首不該讓另外 39 首都跑不到。
"""
import asyncio
import json
from datetime import datetime

import pytest

from backend.services.batch_scheduler import (
    DONE,
    ERROR,
    FINISHED,
    PENDING,
    SKIPPED,
    BatchScheduler,
    extract_video_id,
    in_window,
    job_progress,
    minutes_until_window,
    split_source_lines,
)


# --- 來源解析 ---

def test_extract_video_id_handles_common_url_shapes():
    assert extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_video_id("https://youtu.be/dQw4w9WgXcQ?t=42") == "dQw4w9WgXcQ"
    assert extract_video_id("https://music.youtube.com/watch?v=dQw4w9WgXcQ&list=RD") == "dQw4w9WgXcQ"
    assert extract_video_id("dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    # 關鍵字與空字串不是 ID，要回 None 讓呼叫端改走搜尋
    assert extract_video_id("周杰倫 稻香") is None
    assert extract_video_id("") is None


def test_split_source_lines_splits_on_newline_and_comma_but_not_space():
    text = "周杰倫 稻香\nhttps://youtu.be/dQw4w9WgXcQ, 五月天 溫柔\n\n周杰倫 稻香"
    lines = split_source_lines(text)
    # 空白不切（「周杰倫 稻香」是一個關鍵字）、重複的只留一筆、空行丟掉
    assert lines == ["周杰倫 稻香", "https://youtu.be/dQw4w9WgXcQ", "五月天 溫柔"]
    assert split_source_lines("") == []
    assert split_source_lines(None) == []


# --- 時段判斷 ---

def at(hour, minute=0):
    return datetime(2026, 9, 8, hour, minute)


def test_in_window_normal_range():
    assert in_window(at(3), 2, 6) is True
    assert in_window(at(2), 2, 6) is True      # 起點含在內
    assert in_window(at(6), 2, 6) is False     # 終點不含
    assert in_window(at(21), 2, 6) is False


def test_in_window_wraps_midnight():
    """23:00–06:00 是最常設的時段，跨午夜一定要成立。"""
    for hour in (23, 0, 3, 5):
        assert in_window(at(hour), 23, 6) is True, hour
    for hour in (6, 12, 22):
        assert in_window(at(hour), 23, 6) is False, hour


def test_in_window_same_hour_means_all_day():
    assert in_window(at(13), 4, 4) is True


def test_minutes_until_window():
    assert minutes_until_window(at(3), 2, 6) == 0          # 已經在時段內
    assert minutes_until_window(at(21, 30), 2, 6) == 270    # 21:30 → 隔天 02:00
    assert minutes_until_window(at(1, 15), 2, 6) == 45


# --- 測試替身 ---

class FakeProcessor:
    """假的流水線：記下處理過哪些歌，指定的歌一律拋例外。"""

    def __init__(self, fail_ids=()):
        self.processed = []
        self.fail_ids = set(fail_ids)

    async def process_song(self, url_or_id, progress_callback=None):
        song_id = url_or_id.rsplit("=", 1)[-1]
        self.processed.append(song_id)
        if progress_callback:
            progress_callback(song_id, "下載中", 30)
        if song_id in self.fail_ids:
            raise RuntimeError("影片已下架")
        return {"title": f"歌 {song_id}", "artist": "測試歌手", "thumbnail": ""}


class FakeStorage:
    def __init__(self, complete_ids=()):
        self.complete_ids = set(complete_ids)

    def is_song_complete(self, song_id):
        return song_id in self.complete_ids

    def get_song_metadata(self, song_id):
        return {"id": song_id} if song_id in self.complete_ids else None


def make_scheduler(tmp_path, *, processor=None, storage=None, settings=None,
                   busy=False, now=at(3)):
    return BatchScheduler(
        processor or FakeProcessor(),
        storage or FakeStorage(),
        tmp_path / "batch_jobs.json",
        settings=settings or DictSettings(),
        busy_cb=lambda: busy,
        clock=lambda: now,
    )


class DictSettings:
    """SystemSettings 的最小替身：只要有 get()。"""

    def __init__(self, **overrides):
        self._data = {
            "batch_enabled": True,
            "batch_start_hour": 2,
            "batch_end_hour": 6,
            "batch_pause_while_singing": True,
        }
        self._data.update(overrides)

    def get(self, key, fallback=None):
        return self._data.get(key, fallback)


def sources(*ids):
    return [{"song_id": sid, "title": f"歌 {sid}"} for sid in ids]


# --- 任務建立 ---

def test_create_job_skips_songs_already_in_library(tmp_path):
    sched = make_scheduler(tmp_path, storage=FakeStorage(complete_ids={"aaa"}))
    job = sched.create_job(sources("aaa", "bbb"))
    statuses = {i["song_id"]: i["status"] for i in job["items"]}
    assert statuses == {"aaa": SKIPPED, "bbb": PENDING}
    assert job_progress(job)["pending"] == 1


def test_create_job_deduplicates_and_finishes_when_nothing_to_do(tmp_path):
    sched = make_scheduler(tmp_path, storage=FakeStorage(complete_ids={"aaa"}))
    job = sched.create_job(sources("aaa", "aaa"))
    assert len(job["items"]) == 1
    # 整批都已經在曲庫裡，不必等排程時段，直接標完成
    assert job["status"] == FINISHED
    assert sched.pending_count() == 0


# --- 排程判斷 ---

def test_does_not_run_while_someone_is_singing(tmp_path):
    sched = make_scheduler(tmp_path, busy=True)
    sched.create_job(sources("aaa"))
    can_run, reason = sched.run_decision()
    assert can_run is False
    assert "唱歌" in reason


def test_runs_while_singing_when_the_guard_is_switched_off(tmp_path):
    sched = make_scheduler(tmp_path, busy=True,
                           settings=DictSettings(batch_pause_while_singing=False))
    sched.create_job(sources("aaa"))
    assert sched.run_decision()[0] is True


def test_waits_outside_the_window_and_reports_how_long(tmp_path):
    sched = make_scheduler(tmp_path, now=at(21, 30))
    sched.create_job(sources("aaa"))
    can_run, reason = sched.run_decision()
    assert can_run is False
    assert "02:00" in reason and "4 小時 30 分" in reason


def test_start_now_overrides_the_window(tmp_path):
    sched = make_scheduler(tmp_path, now=at(21))
    sched.create_job(sources("aaa"), start_now=True)
    assert sched.force_run is True
    assert sched.run_decision()[0] is True


def test_disabled_scheduler_never_runs(tmp_path):
    sched = make_scheduler(tmp_path, settings=DictSettings(batch_enabled=False))
    sched.create_job(sources("aaa"), start_now=True)
    assert sched.run_decision()[0] is False


# --- 執行 ---

def test_run_once_processes_one_song_then_yields(tmp_path):
    proc = FakeProcessor()
    sched = make_scheduler(tmp_path, processor=proc)
    sched.create_job(sources("aaa", "bbb"))

    item = asyncio.run(sched.run_once())
    assert item["song_id"] == "aaa"
    assert item["status"] == DONE
    assert item["title"] == "歌 aaa"
    # 一次只跑一首：第二首還在等，讓排程迴圈有機會重新判斷現場狀況
    assert proc.processed == ["aaa"]
    assert sched.pending_count() == 1


def test_failed_song_does_not_block_the_rest_of_the_batch(tmp_path):
    proc = FakeProcessor(fail_ids={"bbb"})
    sched = make_scheduler(tmp_path, processor=proc)
    job = sched.create_job(sources("aaa", "bbb", "ccc"))

    for _ in range(3):
        asyncio.run(sched.run_once())

    statuses = {i["song_id"]: i["status"] for i in job["items"]}
    assert statuses == {"aaa": DONE, "bbb": ERROR, "ccc": DONE}
    assert proc.processed == ["aaa", "bbb", "ccc"]
    assert job["status"] == FINISHED
    error_item = next(i for i in job["items"] if i["song_id"] == "bbb")
    assert "下架" in error_item["error"]


def test_retry_only_reschedules_failed_items(tmp_path):
    proc = FakeProcessor(fail_ids={"bbb"})
    sched = make_scheduler(tmp_path, processor=proc)
    job = sched.create_job(sources("aaa", "bbb"))
    for _ in range(2):
        asyncio.run(sched.run_once())

    proc.fail_ids.clear()
    sched.retry_job(job["job_id"])
    assert sched.pending_count() == 1        # 只有失敗的那首回到佇列

    asyncio.run(sched.run_once())
    assert proc.processed == ["aaa", "bbb", "bbb"]
    assert job_progress(job)["done"] == 2


def test_force_flag_clears_itself_when_the_queue_empties(tmp_path):
    sched = make_scheduler(tmp_path, now=at(21))
    sched.create_job(sources("aaa"), start_now=True)
    asyncio.run(sched.run_once())
    # 跑完就收回「立即開始」，下一批照樣照表操課
    assert sched.force_run is False


def test_cancel_marks_pending_items_and_stops_the_batch(tmp_path):
    proc = FakeProcessor()
    sched = make_scheduler(tmp_path, processor=proc)
    job = sched.create_job(sources("aaa", "bbb"))
    sched.cancel_job(job["job_id"])

    assert sched.pending_count() == 0
    assert asyncio.run(sched.run_once()) is None
    assert proc.processed == []


def test_delete_and_clear_finished(tmp_path):
    sched = make_scheduler(tmp_path)
    done_job = sched.create_job(sources("aaa"))
    asyncio.run(sched.run_once())
    pending_job = sched.create_job(sources("bbb"))

    assert sched.clear_finished() == 1              # 跑完的清掉，還有待處理的留著
    assert [j["job_id"] for j in sched.jobs] == [pending_job["job_id"]]
    assert sched.delete_job(pending_job["job_id"]) is True
    assert sched.delete_job(done_job["job_id"]) is False


# --- 持久化 ---

def test_jobs_survive_restart_and_interrupted_song_goes_back_to_pending(tmp_path):
    jobs_file = tmp_path / "batch_jobs.json"
    sched = make_scheduler(tmp_path)
    job = sched.create_job(sources("aaa", "bbb"), start_now=True)
    # 模擬「處理到一半斷電」：檔案裡留下 RUNNING 的項目
    raw = json.loads(jobs_file.read_text(encoding="utf-8"))
    raw["jobs"][0]["items"][0]["status"] = "RUNNING"
    raw["jobs"][0]["status"] = "RUNNING"
    jobs_file.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    revived = make_scheduler(tmp_path)
    assert [j["job_id"] for j in revived.jobs] == [job["job_id"]]
    assert revived.jobs[0]["items"][0]["status"] == PENDING
    assert revived.pending_count() == 2
    assert revived.force_run is True     # 使用者按過「立即開始」，重開機不該吃掉這個意圖


def test_corrupt_jobs_file_does_not_block_startup(tmp_path):
    (tmp_path / "batch_jobs.json").write_text("{ 這不是 JSON", encoding="utf-8")
    sched = make_scheduler(tmp_path)
    assert sched.jobs == []
    assert sched.state()["pending_total"] == 0


# --- 對外狀態 ---

def test_state_shape_for_the_ui(tmp_path):
    sched = make_scheduler(tmp_path, now=at(21))
    sched.create_job(sources("aaa"), name="週末歌單")
    state = sched.state()
    assert state["window"]["start_hour"] == 2
    assert state["window"]["in_window"] is False
    assert state["window"]["minutes_until"] == 300
    assert state["pending_total"] == 1
    assert state["can_run"] is False and state["reason"]
    assert state["jobs"][0]["name"] == "週末歌單"
    assert state["jobs"][0]["progress"]["total"] == 1


def test_state_lists_newest_job_first(tmp_path):
    sched = make_scheduler(tmp_path)
    sched.create_job(sources("aaa"), name="舊的")
    sched.create_job(sources("bbb"), name="新的")
    assert [j["name"] for j in sched.state()["jobs"]] == ["新的", "舊的"]


@pytest.mark.parametrize("hour,expected", [(2, True), (5, True), (6, False), (14, False)])
def test_window_boundaries_are_inclusive_start_exclusive_end(tmp_path, hour, expected):
    sched = make_scheduler(tmp_path, now=at(hour))
    sched.create_job(sources("aaa"))
    assert sched.run_decision()[0] is expected
