"""包廂計時（歡唱時間）的單元測試。

這個功能真正要守住的不是「倒數會不會減」，是**時間到那一刻的行為**：
正在唱的那一首要唱完、佇列一首都不能少、續時要接得回去。
所以下面的測試大半在測那一分鐘，而不是測減法。

時間一律用 `now=` 傳進去，測試不必真的等三小時。
"""
import asyncio
import json
from datetime import datetime, timedelta

import pytest

from backend.services import room_timer
from backend.services.play_stats import PlayStats
from backend.services.queue_manager import QueueManager
from backend.services.room_timer import RoomTimer, RoomTimeUp
from backend.services.song_history import SongHistory
from backend.services.storage import SongStorage

T0 = datetime(2026, 9, 16, 20, 0, 0)


def at(minutes: float) -> datetime:
    """開場後第幾分鐘。"""
    return T0 + timedelta(minutes=minutes)


# --- 收斂與門檻 ---

def test_coerce_minutes_clamps_and_falls_back():
    assert room_timer.coerce_minutes(120) == 120
    assert room_timer.coerce_minutes(1) == room_timer.MIN_SESSION_MINUTES
    assert room_timer.coerce_minutes(99999) == room_timer.MAX_SESSION_MINUTES
    # 看不懂的值退回預設值，而不是拋錯：手機端送來怪東西時正確的行為是
    # 「照預設開一場」，不是讓整個計時掛掉
    assert room_timer.coerce_minutes("") == room_timer.DEFAULT_SESSION_MINUTES
    assert room_timer.coerce_minutes(None) == room_timer.DEFAULT_SESSION_MINUTES
    assert room_timer.coerce_minutes("三小時") == room_timer.DEFAULT_SESSION_MINUTES


def test_milestones_are_sorted_deduped_and_zero_means_off():
    assert room_timer.default_milestones(10, 3) == [("warn", 600), ("last_call", 180)]
    # 0 = 不提醒那一次
    assert room_timer.default_milestones(0, 3) == [("last_call", 180)]
    assert room_timer.default_milestones(0, 0) == []
    # 兩個門檻撞在一起只留一個（同一秒鐘跳兩次意思一樣的提醒是鬼叫）
    assert room_timer.default_milestones(5, 5) == [("warn", 300)]


# --- 倒數、暫停、續時 ---

def test_start_counts_down():
    timer = RoomTimer()
    timer.start(180, now=T0)
    snap = timer.snapshot(at(30))
    assert snap["state"] == room_timer.STATE_RUNNING
    assert snap["remaining_seconds"] == 150 * 60
    assert snap["elapsed_seconds"] == 30 * 60
    assert snap["expired"] is False
    # 幾點結束講得出來（「我們唱到幾點」是包廂最常問的一句）
    assert snap["ends_at"].startswith("2026-09-16T23:00")


def test_pause_stops_the_clock_and_resume_continues():
    timer = RoomTimer()
    timer.start(180, now=T0)
    timer.pause(now=at(30))
    # 停錶期間過了半小時，用掉的還是 30 分鐘
    paused = timer.snapshot(at(60))
    assert paused["state"] == room_timer.STATE_PAUSED
    assert paused["elapsed_seconds"] == 30 * 60
    assert paused["remaining_seconds"] == 150 * 60
    # 停錶中不講「幾點結束」：那個時間會隨著休息一直往後飄，看起來像機器在亂跳
    assert paused["ends_at"] is None

    timer.resume(now=at(60))
    assert timer.snapshot(at(70))["elapsed_seconds"] == 40 * 60


def test_clock_going_backwards_never_adds_time():
    """NTP 校時、有人改系統時間：少算一點時間對客人有利，倒扣回去則是爭執。"""
    timer = RoomTimer()
    timer.start(180, now=at(30))
    snap = timer.snapshot(T0)  # 時間倒退了半小時
    assert snap["elapsed_seconds"] == 0
    assert snap["remaining_seconds"] == 180 * 60


def test_expiry_reports_overtime_not_negative_remaining():
    timer = RoomTimer()
    timer.start(60, now=T0)
    snap = timer.snapshot(at(64))
    assert snap["state"] == room_timer.STATE_EXPIRED
    assert snap["expired"] is True
    # 畫面要的是「還剩多久」，「-00:04:00」只會讓人愣一下才看懂
    assert snap["remaining_seconds"] == 0
    assert snap["overtime_seconds"] == 4 * 60


def test_extend_adds_time_and_keeps_the_same_session():
    timer = RoomTimer()
    timer.start(60, now=T0)
    timer.extend(30, now=at(55))
    snap = timer.snapshot(at(55))
    assert snap["total_seconds"] == 90 * 60
    # 續的是同一場：已經用掉的 55 分鐘不會被抹掉
    assert snap["elapsed_seconds"] == 55 * 60
    assert snap["remaining_seconds"] == 35 * 60


def test_extend_resumes_a_paused_or_halted_session():
    """按了「續時」還得再按一次播放的話，那顆續時鍵看起來就沒有反應。"""
    timer = RoomTimer()
    timer.start(60, now=T0)
    timer.pause(now=at(60))
    timer.mark_halted(True)
    timer.extend(30, now=at(70))
    snap = timer.snapshot(at(70))
    assert snap["state"] == room_timer.STATE_RUNNING
    assert snap["halted"] is False


def test_extend_without_a_session_starts_one():
    """「還沒開始計時就按續時」最可能的意思是「開始計時」。"""
    timer = RoomTimer()
    snap = timer.extend(45, now=T0)
    assert snap["active"] is True
    assert snap["total_seconds"] == 45 * 60


def test_stop_clears_everything():
    timer = RoomTimer()
    timer.start(60, now=T0)
    timer.mark_halted(True)
    snap = timer.stop()
    assert snap["active"] is False
    assert snap["state"] == room_timer.STATE_OFF
    assert snap["halted"] is False


# --- 提醒 ---

MILESTONES = [("warn", 600), ("last_call", 180)]


def test_each_milestone_fires_once():
    timer = RoomTimer()
    timer.start(60, now=T0)
    assert timer.tick(MILESTONES, now=at(30)) == []
    fired = timer.tick(MILESTONES, now=at(51))
    assert [a["kind"] for a in fired] == ["warn"]
    # 再敲十次也不會再講一次（每 30 秒跳一次的提醒會被當成雜訊直接無視）
    assert timer.tick(MILESTONES, now=at(52)) == []
    assert [a["kind"] for a in timer.tick(MILESTONES, now=at(58))] == ["last_call"]
    assert [a["kind"] for a in timer.tick(MILESTONES, now=at(61))] == ["expired"]
    assert timer.tick(MILESTONES, now=at(70)) == []


def test_crossing_several_thresholds_at_once_only_says_the_most_urgent():
    """
    伺服器剛重開、心跳被卡住、或是有人開了一場比提醒門檻還短的。

    三句話在同一秒鐘一起跳出來會互相蓋掉，而真正該被看到的是最後那一句。
    """
    timer = RoomTimer()
    timer.start(60, now=T0)
    alerts = timer.tick(MILESTONES, now=at(61))
    assert [a["kind"] for a in alerts] == ["expired"]
    # 被跳過的門檻算講過了，不會事後補跳出來
    assert timer.tick(MILESTONES, now=at(62)) == []


def test_expiry_alert_needs_no_configuration():
    """沒有人會想關掉「時間到了」這個提醒，所以它不是設定項。"""
    timer = RoomTimer()
    timer.start(10, now=T0)
    assert [a["kind"] for a in timer.tick([], now=at(11))] == ["expired"]


def test_paused_timer_never_fires():
    timer = RoomTimer()
    timer.start(60, now=T0)
    timer.pause(now=at(10))
    assert timer.tick(MILESTONES, now=at(600)) == []


def test_extend_rearms_milestones_that_became_future_again():
    """續了 30 分鐘之後，那個「剩 10 分鐘」提醒又變成有意義的資訊了。"""
    timer = RoomTimer()
    timer.start(60, now=T0)
    assert [a["kind"] for a in timer.tick(MILESTONES, now=at(55))] == ["warn"]
    timer.extend(30, now=at(55))
    assert timer.tick(MILESTONES, now=at(56)) == []          # 還剩 34 分鐘，不吵
    assert [a["kind"] for a in timer.tick(MILESTONES, now=at(81))] == ["warn"]


# --- 存檔（重開機接得回來）---

def test_state_survives_a_restart(tmp_path):
    path = tmp_path / "room_timer.json"
    RoomTimer(path).start(180)
    again = RoomTimer(path)
    snap = again.snapshot()
    assert snap["active"] is True
    assert snap["total_seconds"] == 180 * 60
    # 重開機不會憑空多出時間，也不會歸零
    assert snap["remaining_seconds"] <= 180 * 60


def test_stale_state_is_not_resumed(tmp_path):
    """機器關過一整晚：昨天那一場不管當時剩幾分鐘，今天開機都不該接回來。"""
    path = tmp_path / "room_timer.json"
    stale = datetime.now() - timedelta(hours=room_timer.STALE_HOURS + 1)
    path.write_text(json.dumps({
        "saved_at": stale.isoformat(timespec="seconds"),
        "active": True, "total_seconds": 10800, "banked_seconds": 0,
        "started_at": stale.isoformat(), "halted": False, "fired": [],
    }), encoding="utf-8")
    assert RoomTimer(path).snapshot()["active"] is False


def test_broken_state_file_does_not_block_startup(tmp_path):
    path = tmp_path / "room_timer.json"
    path.write_text("{ 這不是 JSON", encoding="utf-8")
    assert RoomTimer(path).snapshot()["state"] == room_timer.STATE_OFF


def test_unwritable_state_file_does_not_stop_the_room(tmp_path):
    """寫不進去只代表「重開機接不回來」，不該讓包廂的計時當場停擺。"""
    blocker = tmp_path / "blocked"
    blocker.write_text("我是一個檔案，不是目錄", encoding="utf-8")
    timer = RoomTimer(blocker / "room_timer.json")  # 父層是檔案，建目錄一定失敗
    snap = timer.start(60, now=T0)
    assert snap["active"] is True
    assert snap["remaining_seconds"] == 60 * 60


# --- 與佇列的整合：時間到那一分鐘 ---

class FakeProcessor:
    async def process_song(self, url, progress_callback=None):
        song_id = url.rsplit("v=", 1)[-1]
        if progress_callback:
            progress_callback(song_id, "Ready", 100)
        return {"title": f"處理完成 {song_id}", "artist": "測試", "thumbnail": ""}


class FakeSettings:
    """只回答計時規則的假設定物件。"""

    def __init__(self, **overrides):
        self.policy = {
            "enabled": True, "minutes": 180, "autostart": False,
            "warn_minutes": 10, "last_call_minutes": 3,
            "expire_action": "finish_song", "extend_minutes": 30,
        }
        self.policy.update(overrides)

    def room_policy(self):
        return dict(self.policy)

    def get(self, key, fallback=None):
        return fallback


def make_manager(tmp_path, cached_ids=(), **policy):
    storage = SongStorage(tmp_path / "songs")
    for song_id in cached_ids:
        song_dir = tmp_path / "songs" / song_id
        song_dir.mkdir(parents=True)
        (song_dir / "metadata.json").write_text(
            json.dumps({"id": song_id, "title": f"快取歌 {song_id}", "artist": "歌手"}),
            encoding="utf-8")
    return QueueManager(FakeProcessor(), storage,
                        play_stats=PlayStats(tmp_path / "play_stats.json"),
                        song_history=SongHistory(tmp_path / "song_history.json"),
                        settings=FakeSettings(**policy))


def run_out_of_time(manager, minutes=60):
    """
    讓這一場「已經到點」。

    刻意用**真實時鐘往回推**，而不是像上面那些純邏輯測試一樣傳一個固定的假時間：
    QueueManager 這一側（play_next、add_song、tick_room）問的是 datetime.now()，
    混用的話這幾條測試會變成「幾點跑、在哪個時區跑」決定成敗 ——
    CI 跑在 UTC，固定的假時間可能還在未來，於是「時間到」整組測試在本機全綠、
    在 CI 上全紅（而且看起來像功能壞了）。
    """
    manager.room.start(minutes, now=datetime.now() - timedelta(minutes=minutes + 1))


def test_time_up_stops_the_next_song_not_the_current_one(tmp_path):
    """
    這是整個功能的重點測試。

    時間到的時候，正在唱的那一首**不被動到**；停的是下一首。
    而且佇列一首都不刪 —— 停下來的是播放，不是資料。
    """
    async def scenario():
        manager = make_manager(tmp_path, cached_ids=["aaaaaaaaaa1", "aaaaaaaaaa2"])
        await manager.add_song("aaaaaaaaaa1")       # 直接上台
        await manager.add_song("aaaaaaaaaa2")       # 排隊
        run_out_of_time(manager)

        # 正在唱的那一首還在唱
        assert manager.current_song["song_id"] == "aaaaaaaaaa1"
        assert manager.is_playing is True

        # 這一首唱完（舞台端送 SONG_ENDED → skip_current）
        assert await manager.skip_current() is None
        assert manager.current_song is None
        assert manager.is_playing is False
        assert manager.room_state()["halted"] is True
        # 佇列原封不動
        assert [i["song_id"] for i in manager.queue] == ["aaaaaaaaaa2"]

    asyncio.run(scenario())


def test_notify_only_never_stops_anything(tmp_path):
    async def scenario():
        manager = make_manager(tmp_path, cached_ids=["bbbbbbbbbb1", "bbbbbbbbbb2"],
                               expire_action="notify_only")
        await manager.add_song("bbbbbbbbbb1")
        await manager.add_song("bbbbbbbbbb2")
        run_out_of_time(manager)
        nxt = await manager.skip_current()
        assert nxt["song_id"] == "bbbbbbbbbb2"

    asyncio.run(scenario())


def test_disabled_timer_never_stops_anything(tmp_path):
    """一個沒有人打開的功能不該有任何機會去停掉別人的歌。"""
    async def scenario():
        manager = make_manager(tmp_path, cached_ids=["cccccccccc1", "cccccccccc2"],
                               enabled=False)
        await manager.add_song("cccccccccc1")
        await manager.add_song("cccccccccc2")
        run_out_of_time(manager)
        assert manager.room_stops_playback() is False
        nxt = await manager.skip_current()
        assert nxt["song_id"] == "cccccccccc2"

    asyncio.run(scenario())


def test_adding_after_time_up_is_refused_with_the_whole_snapshot(tmp_path):
    async def scenario():
        manager = make_manager(tmp_path, cached_ids=["dddddddddd1"])
        run_out_of_time(manager)
        with pytest.raises(RoomTimeUp) as excinfo:
            await manager.add_song("dddddddddd1")
        snap = excinfo.value.snapshot
        assert snap["expired"] is True
        assert snap["extend_minutes"] == 30
        assert manager.queue == []

    asyncio.run(scenario())


def test_priority_does_not_bypass_time_up(tmp_path):
    """插播繞得過包廂內部的公平規則，繞不過「今晚已經結束」這件事實。"""
    async def scenario():
        manager = make_manager(tmp_path, cached_ids=["eeeeeeeeee1"])
        run_out_of_time(manager)
        with pytest.raises(RoomTimeUp):
            await manager.add_song("eeeeeeeeee1", priority=True)

    asyncio.run(scenario())


def test_extend_resumes_playback_from_the_queue(tmp_path):
    async def scenario():
        manager = make_manager(tmp_path, cached_ids=["ffffffffff1", "ffffffffff2"])
        await manager.add_song("ffffffffff1")
        await manager.add_song("ffffffffff2")
        run_out_of_time(manager)
        await manager.skip_current()
        assert manager.current_song is None

        await manager.extend_room_session(30)
        # 續時之後自己接回去唱，不必再按一次播放
        assert manager.current_song["song_id"] == "ffffffffff2"
        assert manager.room_state()["halted"] is False

    asyncio.run(scenario())


def test_stop_releases_the_halt(tmp_path):
    """「不要再管時間了」按下去卻還停在散場畫面的話，沒有人找得到怎麼救回來。"""
    async def scenario():
        manager = make_manager(tmp_path, cached_ids=["gggggggggg1", "gggggggggg2"])
        await manager.add_song("gggggggggg1")
        await manager.add_song("gggggggggg2")
        run_out_of_time(manager)
        await manager.skip_current()

        await manager.stop_room_session()
        assert manager.current_song["song_id"] == "gggggggggg2"
        assert manager.room_state()["active"] is False

    asyncio.run(scenario())


def test_autostart_begins_with_the_first_song(tmp_path):
    async def scenario():
        manager = make_manager(tmp_path, cached_ids=["hhhhhhhhhh1"], autostart=True,
                               minutes=120)
        assert manager.room_state()["active"] is False
        await manager.add_song("hhhhhhhhhh1")
        snap = manager.room_state()
        assert snap["active"] is True
        assert snap["total_seconds"] == 120 * 60

    asyncio.run(scenario())


def test_autostart_does_not_undo_an_explicit_stop(tmp_path):
    """按過「結束計時」之後，下一首歌不該又把錶打開（那顆鍵會看起來沒反應）。"""
    async def scenario():
        manager = make_manager(tmp_path, cached_ids=["iiiiiiiiii1", "iiiiiiiiii2"],
                               autostart=True)
        await manager.add_song("iiiiiiiiii1")
        await manager.stop_room_session()
        await manager.add_song("iiiiiiiiii2")
        await manager.skip_current()
        assert manager.room_state()["active"] is False

    asyncio.run(scenario())


def test_tick_halts_when_time_runs_out_between_songs(tmp_path):
    """歌與歌之間到點：沒有「唱完這一首」可以等，直接收場。"""
    async def scenario():
        manager = make_manager(tmp_path)
        # tick_room 走的是真實時鐘（它是伺服器的心跳），所以把開場時間放在過去
        manager.room.start(10, now=datetime.now() - timedelta(minutes=11))
        assert manager.current_song is None
        alerts = await manager.tick_room()
        assert [a["kind"] for a in alerts] == ["expired"]
        assert manager.room_state()["halted"] is True

    asyncio.run(scenario())


def test_room_state_carries_the_rules_the_screen_needs(tmp_path):
    """
    「時間到會讓你唱完這一首」與「只是提醒、不會停」是兩句完全不同的話，
    而決定是哪一句的是設定，不是計時器 —— 所以規則跟著快照一起送。
    """
    manager = make_manager(tmp_path, minutes=90, extend_minutes=20)
    state = manager.room_state()
    assert state["enabled"] is True
    assert state["expire_action"] == "finish_song"
    assert state["default_minutes"] == 90
    assert state["extend_minutes"] == 20
    assert state["warn_minutes"] == 10
