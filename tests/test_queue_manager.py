"""點歌佇列（QueueManager）單元測試。

用假的 SongProcessor 取代真正的下載/分離流水線，
專測佇列邏輯：點歌、插播、切歌、排序、控制參數夾限。
"""
import asyncio
import json

from backend.services.play_stats import PlayStats
from backend.services.queue_manager import QueueManager
from backend.services.song_history import SongHistory
from backend.services.storage import SongStorage


class FakeProcessor:
    """假流水線：立刻回報完成。"""

    async def process_song(self, url, progress_callback=None):
        song_id = url.rsplit("v=", 1)[-1]
        if progress_callback:
            progress_callback(song_id, "Ready", 100)
        return {"title": f"處理完成 {song_id}", "artist": "測試", "thumbnail": ""}


def make_manager(tmp_path, cached_ids=()):
    storage = SongStorage(tmp_path / "songs")
    for song_id in cached_ids:
        song_dir = tmp_path / "songs" / song_id
        song_dir.mkdir(parents=True)
        (song_dir / "metadata.json").write_text(
            json.dumps({"id": song_id, "title": f"快取歌 {song_id}", "artist": "歌手"}),
            encoding="utf-8")
    stats = PlayStats(tmp_path / "play_stats.json")
    history = SongHistory(tmp_path / "song_history.json")
    return QueueManager(FakeProcessor(), storage, play_stats=stats,
                        song_history=history), stats


def test_cached_song_plays_immediately(tmp_path):
    async def scenario():
        manager, stats = make_manager(tmp_path, cached_ids=["cached00001"])
        item = await manager.add_song("cached00001")
        assert item["status"] == "READY"
        assert item["title"] == "快取歌 cached00001"
        # 沒有歌在播 → 直接上台，並計入點唱統計與已唱歷史
        assert manager.current_song["song_id"] == "cached00001"
        assert manager.is_playing is True
        assert stats.total_plays() == 1
        assert manager.song_history.total_count() == 1
        assert manager.song_history.recent()[0]["song_id"] == "cached00001"
        assert manager.queue == []

    asyncio.run(scenario())


def test_youtube_url_resolves_video_id(tmp_path):
    async def scenario():
        manager, _ = make_manager(tmp_path)
        item = await manager.add_song("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert item["song_id"] == "dQw4w9WgXcQ"
        await asyncio.sleep(0.05)  # 讓背景處理跑完

    asyncio.run(scenario())


def test_priority_song_jumps_queue(tmp_path):
    async def scenario():
        manager, _ = make_manager(tmp_path, cached_ids=["song0000001", "song0000002", "song0000003"])
        await manager.add_song("song0000001")           # 直接上台
        await manager.add_song("song0000002")           # 排第一
        item = await manager.add_song("song0000003", priority=True)  # 插播
        assert manager.queue[0]["queue_id"] == item["queue_id"]

    asyncio.run(scenario())


def test_skip_advances_and_records_history(tmp_path):
    async def scenario():
        manager, stats = make_manager(tmp_path, cached_ids=["song0000001", "song0000002"])
        await manager.add_song("song0000001")
        await manager.add_song("song0000002")
        next_song = await manager.skip_current()
        assert next_song["song_id"] == "song0000002"
        assert [h["song_id"] for h in manager.history] == ["song0000001"]
        assert stats.total_plays() == 2
        # 佇列唱完 → 停止播放
        assert await manager.skip_current() is None
        assert manager.is_playing is False

    asyncio.run(scenario())


def test_remove_from_queue(tmp_path):
    async def scenario():
        manager, stats = make_manager(tmp_path, cached_ids=["song0000001", "song0000002"])
        await manager.add_song("song0000001")
        item = await manager.add_song("song0000002")
        await manager.remove_from_queue(item["queue_id"])
        assert manager.queue == []
        # 排進去又被移除的不算點唱次數
        assert stats.total_plays() == 1

    asyncio.run(scenario())


def test_reorder_queue(tmp_path):
    async def scenario():
        manager, _ = make_manager(tmp_path, cached_ids=["song0000001", "song0000002", "song0000003"])
        await manager.add_song("song0000001")
        a = await manager.add_song("song0000002")
        b = await manager.add_song("song0000003")
        await manager.reorder_queue(1, 0)
        assert [i["queue_id"] for i in manager.queue] == [b["queue_id"], a["queue_id"]]
        # 超出範圍的索引不該爆炸也不該改變佇列
        await manager.reorder_queue(5, 0)
        assert [i["queue_id"] for i in manager.queue] == [b["queue_id"], a["queue_id"]]

    asyncio.run(scenario())


class FlakyProcessor:
    """假流水線：第一次爆炸，第二次成功。專門測「重試」。"""

    def __init__(self):
        self.calls = 0

    async def process_song(self, url, progress_callback=None):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("network died")
        song_id = url.rsplit("v=", 1)[-1]
        return {"title": f"重試成功 {song_id}", "artist": "測試", "thumbnail": ""}


def test_retry_failed_item(tmp_path):
    async def scenario():
        storage = SongStorage(tmp_path / "songs")
        manager = QueueManager(FlakyProcessor(), storage)
        item = await manager.add_song("failsong0001")
        await asyncio.sleep(0.05)  # 第一次處理失敗
        assert manager.queue[0]["status"] == "ERROR"

        retried = await manager.retry_item(item["queue_id"])
        assert retried is not None
        await asyncio.sleep(0.05)  # 第二次處理成功，且沒歌在播 → 直接上台
        assert manager.current_song["song_id"] == "failsong0001"
        assert manager.current_song["title"] == "重試成功 failsong0001"

    asyncio.run(scenario())


def test_retry_only_applies_to_error_items(tmp_path):
    async def scenario():
        manager, _ = make_manager(tmp_path, cached_ids=["song0000001", "song0000002"])
        await manager.add_song("song0000001")           # 直接上台
        ready = await manager.add_song("song0000002")   # 排隊中，狀態 READY
        assert await manager.retry_item(ready["queue_id"]) is None
        assert await manager.retry_item("no-such-queue-id") is None

    asyncio.run(scenario())


def test_is_song_in_use(tmp_path):
    async def scenario():
        manager, _ = make_manager(tmp_path, cached_ids=["song0000001", "song0000002"])
        await manager.add_song("song0000001")   # 演唱中
        await manager.add_song("song0000002")   # 佇列裡
        assert manager.is_song_in_use("song0000001") is True
        assert manager.is_song_in_use("song0000002") is True
        assert manager.is_song_in_use("song0000003") is False

    asyncio.run(scenario())


def test_update_controls_clamps_values(tmp_path):
    async def scenario():
        manager, _ = make_manager(tmp_path)
        await manager.update_controls({
            "mic_reverb": 5.0,
            "mic_echo": -1.0,
            "mic_echo_time_ms": 10000,
            "lyric_offset_ms": -99999,
            "sing_mode": "hacker",
            "pitch_shift": 3,
        })
        assert manager.mic_reverb == 1.0
        assert manager.mic_echo == 0.0
        assert manager.mic_echo_time_ms == 800
        assert manager.lyric_offset_ms == -2000
        assert manager.sing_mode == "solo"
        assert manager.pitch_shift == 3
        # 升降 Key 夾在 ±6 半音，超出範圍的值不能讓變調器爆掉
        await manager.update_controls({"pitch_shift": 99})
        assert manager.pitch_shift == 6
        await manager.update_controls({"pitch_shift": -99})
        assert manager.pitch_shift == -6

    asyncio.run(scenario())


def test_requested_by_recorded_and_sanitized(tmp_path):
    """多人包廂：點歌時記下是誰點的，前後空白修掉、過長截斷。"""
    async def scenario():
        manager, _ = make_manager(tmp_path, cached_ids=["song0000001", "song0000002", "song0000003"])
        await manager.add_song("song0000001")  # 直接上台，佔住舞台
        item = await manager.add_song("song0000002", requested_by="  小明  ")
        assert item["requested_by"] == "小明"
        # 沒給暱稱 → 空字串，前端不顯示標籤
        anon = await manager.add_song("song0000003")
        assert anon["requested_by"] == ""
        # 上台後 current_song 也帶著點歌人，舞台片頭卡才有得顯示
        await manager.skip_current()
        assert manager.current_song["requested_by"] == "小明"

    asyncio.run(scenario())


def test_requested_by_truncated_to_24_chars(tmp_path):
    async def scenario():
        manager, _ = make_manager(tmp_path, cached_ids=["song0000001"])
        item = await manager.add_song("song0000001", requested_by="甲" * 50)
        assert item["requested_by"] == "甲" * 24

    asyncio.run(scenario())


def test_full_state_contains_control_fields(tmp_path):
    manager, _ = make_manager(tmp_path)
    state = manager.get_full_state()
    for key in ("current_song", "queue", "is_playing", "vocal_volume",
                "pitch_shift", "music_volume", "mic_volume", "lyric_offset_ms",
                "show_pitch", "sing_mode", "harmony_enabled", "harmony_style",
                "harmony_level", "duet_enabled", "duet_name_a", "duet_name_b"):
        assert key in state


# --- 和聲（雙聲部）---

def test_harmony_controls_are_shared_state(tmp_path):
    """和聲的開關／聲部／音量是共享狀態：點歌台按下去，舞台端與其他手機都看得到。"""
    async def scenario():
        manager, _ = make_manager(tmp_path)
        # 預設關著：和聲是加了才有的效果，不該開機就自己出聲
        assert manager.harmony_enabled is False
        assert manager.harmony_style == "third"

        await manager.update_controls({
            "harmony_enabled": True,
            "harmony_style": "duet",
            "harmony_level": 0.7,
        })
        state = manager.get_full_state()
        assert state["harmony_enabled"] is True
        assert state["harmony_style"] == "duet"
        assert state["harmony_level"] == 0.7

    asyncio.run(scenario())


def test_unknown_harmony_style_keeps_previous(tmp_path):
    """認不得的聲部名稱保留原值 —— 舊版舞台端不該讓和聲變成隨機聲部。"""
    async def scenario():
        manager, _ = make_manager(tmp_path)
        await manager.update_controls({"harmony_style": "octave"})
        assert manager.harmony_style == "octave"
        await manager.update_controls({"harmony_style": "第七度加減七"})
        assert manager.harmony_style == "octave"

    asyncio.run(scenario())


def test_harmony_level_clamped(tmp_path):
    async def scenario():
        manager, _ = make_manager(tmp_path)
        await manager.update_controls({"harmony_level": 9.0})
        assert manager.harmony_level == 1.0
        await manager.update_controls({"harmony_level": -3})
        assert manager.harmony_level == 0.0

    asyncio.run(scenario())


# --- 練唱模式：A-B 區段循環 ---

def test_loop_range_stored_and_broadcast(tmp_path):
    """A-B 點是共享狀態：設好之後每台裝置拿到的完整狀態裡都要有。"""
    async def scenario():
        manager, _ = make_manager(tmp_path)
        await manager.update_controls({"loop_start": 61.5, "loop_end": 92.25,
                                       "loop_enabled": True})
        state = manager.get_full_state()
        assert state["loop_start"] == 61.5
        assert state["loop_end"] == 92.25
        assert state["loop_enabled"] is True

    asyncio.run(scenario())


def test_loop_points_can_be_set_one_at_a_time(tmp_path):
    """先按 A、再按 B 是實際的操作順序，中間那個半成品狀態不能壞。"""
    async def scenario():
        manager, _ = make_manager(tmp_path)
        await manager.update_controls({"loop_start": 30.0})
        assert manager.loop_start == 30.0 and manager.loop_end is None
        # 只有 A 點時循環開不起來，否則舞台不知道要跳回哪裡
        await manager.update_controls({"loop_enabled": True})
        assert manager.loop_enabled is False
        await manager.update_controls({"loop_end": 45.0, "loop_enabled": True})
        assert manager.loop_enabled is True

    asyncio.run(scenario())


def test_reversed_loop_points_are_swapped(tmp_path):
    """聽到一半才想圈這段，先按 B 再按 A 很自然 —— 順序反了要自動對調而不是報錯。"""
    async def scenario():
        manager, _ = make_manager(tmp_path)
        await manager.update_controls({"loop_start": 90.0, "loop_end": 60.0,
                                       "loop_enabled": True})
        assert manager.loop_start == 60.0
        assert manager.loop_end == 90.0
        assert manager.loop_enabled is True

    asyncio.run(scenario())


def test_too_short_loop_is_rejected(tmp_path):
    """連按兩下設出來的零長度區間會讓舞台在同一秒瘋狂 seek，一律不准開循環。"""
    async def scenario():
        manager, _ = make_manager(tmp_path)
        await manager.update_controls({"loop_start": 60.0, "loop_end": 60.2,
                                       "loop_enabled": True})
        assert manager.loop_enabled is False
        # 區間本身保留，使用者只要把 B 點往後挪就能用
        assert manager.loop_start == 60.0 and manager.loop_end == 60.2

    asyncio.run(scenario())


def test_loop_positions_are_sanitized(tmp_path):
    async def scenario():
        manager, _ = make_manager(tmp_path)
        await manager.update_controls({"loop_start": -10, "loop_end": "abc"})
        assert manager.loop_start == 0.0
        assert manager.loop_end is None
        await manager.update_controls({"loop_end": float("nan")})
        assert manager.loop_end is None
        # null 代表「清掉這個點」
        await manager.update_controls({"loop_start": 12.0, "loop_end": 30.0})
        await manager.update_controls({"loop_start": None})
        assert manager.loop_start is None

    asyncio.run(scenario())


def test_loop_cleared_when_next_song_takes_stage(tmp_path):
    """A-B 點屬於某一首歌。換人上台還留著，下一首會在莫名其妙的地方跳回去。"""
    async def scenario():
        manager, _ = make_manager(tmp_path, cached_ids=["song0000001", "song0000002"])
        await manager.add_song("song0000001")
        await manager.add_song("song0000002")
        await manager.update_controls({"loop_start": 20.0, "loop_end": 40.0,
                                       "loop_enabled": True})
        assert manager.loop_enabled is True

        await manager.skip_current()
        assert manager.current_song["song_id"] == "song0000002"
        assert manager.loop_enabled is False
        assert manager.loop_start is None and manager.loop_end is None

    asyncio.run(scenario())


def test_seek_broadcasts_clamped_position(tmp_path):
    """跳轉是舞台端的媒體操作，伺服器只送指令、不改自己的狀態。"""
    sent = []

    async def scenario():
        manager, _ = make_manager(tmp_path)
        manager.set_broadcast_callback(lambda msg: _record(msg))
        assert await manager.seek_to(75.256) == 75.256
        assert await manager.seek_to(-5) == 0.0
        assert await manager.seek_to("not a number") == 0.0

    async def _record(msg):
        sent.append(msg)

    asyncio.run(scenario())
    assert [m["command"] for m in sent] == ["SEEK", "SEEK", "SEEK"]
    assert [m["position"] for m in sent] == [75.256, 0.0, 0.0]


def test_full_state_contains_loop_fields(tmp_path):
    manager, _ = make_manager(tmp_path)
    state = manager.get_full_state()
    for key in ("loop_enabled", "loop_start", "loop_end"):
        assert key in state


# --- 對唱模式（兩支麥克風分別評分）---

def test_duet_controls_are_shared_state(tmp_path):
    """對唱開關與兩位演唱者的暱稱是共享狀態：點歌台改，舞台端與其他手機同步。"""
    async def scenario():
        manager, _ = make_manager(tmp_path)
        # 預設關著：第二支麥克風不是每台機器都有
        assert manager.duet_enabled is False
        assert manager.duet_name_a == ""

        await manager.update_controls({
            "duet_enabled": True,
            "duet_name_a": "小明",
            "duet_name_b": "小美",
        })
        state = manager.get_full_state()
        assert state["duet_enabled"] is True
        assert state["duet_name_a"] == "小明"
        assert state["duet_name_b"] == "小美"

    asyncio.run(scenario())


def test_duet_names_are_trimmed_and_capped(tmp_path):
    """暱稱截 12 字：對唱計分板一行要塞兩個名字，塞爆就看不到分數了。"""
    async def scenario():
        manager, _ = make_manager(tmp_path)
        await manager.update_controls({
            "duet_name_a": "  小明  ",
            "duet_name_b": "美" * 40,
        })
        assert manager.duet_name_a == "小明"
        assert manager.duet_name_b == "美" * 12

    asyncio.run(scenario())


def test_duet_can_be_turned_off_by_the_stage(tmp_path):
    """舞台端第二支麥克風開不起來時會把開關改回 False，狀態要真的跟著關。"""
    async def scenario():
        manager, _ = make_manager(tmp_path)
        await manager.update_controls({"duet_enabled": True})
        await manager.update_controls({"duet_enabled": False})
        assert manager.get_full_state()["duet_enabled"] is False

    asyncio.run(scenario())
