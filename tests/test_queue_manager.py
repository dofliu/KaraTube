"""點歌佇列（QueueManager）單元測試。

用假的 SongProcessor 取代真正的下載/分離流水線，
專測佇列邏輯：點歌、插播、切歌、排序、控制參數夾限。
"""
import asyncio
import json

import pytest

from backend.services import song_quota
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


def test_locally_imported_song_never_gets_a_youtube_url(tmp_path):
    """
    本機匯入的歌不在 YouTube 上。佇列項目給它一個假的 youtube 網址的話，
    「重新處理」與「快取被清掉之後再點一次」都會去下載一支不存在的影片 ——
    而那兩次失敗離匯入很遠，沒有人會把它們聯想在一起。

    縮圖同理：`img.youtube.com/vi/loc_xxx` 對本機歌永遠是 404。
    """
    async def scenario():
        manager, _ = make_manager(tmp_path)
        item = await manager.add_song("loc_abc123def456")
        assert item["url"] == "local:loc_abc123def456"
        assert "youtube" not in item["thumbnail"]
        assert item["thumbnail"].startswith("/media/songs/loc_abc123def456/")
        await asyncio.sleep(0.05)

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


class FakeSettings:
    """SystemSettings 的最小替身：升降 Key 的預設值要能被讀到。"""

    def __init__(self, **values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)

    def control_defaults(self):
        return {}


def test_key_shift_resets_when_a_new_song_takes_the_stage(tmp_path):
    """
    升降 Key 跟著唱歌的人走：換歌回到預設。

    這是包廂裡最常被按的一顆鍵，也是最容易留給下一位客人的一個坑 ——
    上一位降了 4 個 Key，下一位上台那首歌會低得唱不下去，而他不知道要去哪裡改。
    """
    async def scenario():
        manager, _ = make_manager(tmp_path, cached_ids=["song0000001", "song0000002"])
        await manager.add_song("song0000001")
        await manager.add_song("song0000002")
        await manager.update_controls({"pitch_shift": -4})
        assert manager.pitch_shift == -4

        # 重唱＝同一個人同一首歌：他剛調好的 Key 要留著
        await manager.restart_current()
        assert manager.pitch_shift == -4

        # 切歌＝換人上台：回到預設（沒有設定物件時就是原調）
        await manager.skip_current()
        assert manager.current_song["song_id"] == "song0000002"
        assert manager.pitch_shift == 0

    asyncio.run(scenario())


def test_key_shift_resets_to_configured_default_not_zero(tmp_path):
    """
    回到的是設定頁的預設，不是硬編的 0。

    包廂裡固定一群男生唱，櫃檯把預設設成 -2 是合理的 —— 那才是這台機器的原調。
    """
    async def scenario():
        manager, _ = make_manager(tmp_path, cached_ids=["song0000001", "song0000002"])
        manager.settings = FakeSettings(default_pitch_shift=-2)
        await manager.add_song("song0000001")
        await manager.add_song("song0000002")
        await manager.update_controls({"pitch_shift": 5})
        await manager.skip_current()
        assert manager.pitch_shift == -2

    asyncio.run(scenario())


def test_key_shift_reset_survives_a_broken_settings_value(tmp_path):
    """設定檔被手動改壞（字串、超出範圍）時退回原調，不能讓切歌整個炸掉。"""
    async def scenario():
        manager, _ = make_manager(tmp_path, cached_ids=["song0000001", "song0000002"])
        manager.settings = FakeSettings(default_pitch_shift="很低")
        await manager.add_song("song0000001")
        await manager.add_song("song0000002")
        await manager.update_controls({"pitch_shift": 5})
        await manager.skip_current()
        assert manager.pitch_shift == 0

        manager.settings = FakeSettings(default_pitch_shift=99)
        await manager.add_song("song0000001")
        await manager.update_controls({"pitch_shift": 0})
        await manager.skip_current()
        assert manager.pitch_shift == 6

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


def test_has_video_flag_travels_with_the_song(tmp_path):
    """
    舞台端要在載 MV 之前就知道這首有沒有影片（沒有的話直接上情境背景）。

    只靠前端等 <video> 404 的話，那一秒的黑畫面剛好落在第一句歌詞上。
    """
    async def scenario():
        storage = SongStorage(tmp_path / "songs")
        for song_id, video_path in (("withvideo01", "/cache/withvideo01/original_video.mp4"),
                                    ("audioonly01", None)):
            song_dir = tmp_path / "songs" / song_id
            song_dir.mkdir(parents=True)
            (song_dir / "metadata.json").write_text(
                json.dumps({"id": song_id, "title": song_id, "video_path": video_path}),
                encoding="utf-8")
        manager = QueueManager(FakeProcessor(), storage,
                               play_stats=PlayStats(tmp_path / "play_stats.json"),
                               song_history=SongHistory(tmp_path / "song_history.json"))

        assert (await manager.add_song("withvideo01"))["has_video"] is True
        assert (await manager.add_song("audioonly01"))["has_video"] is False

        # 還沒處理過的歌是「不知道」，不是「沒有」——
        # 當成沒有的話，每首有 MV 的歌都會先閃一下情境背景。
        pending = await manager.add_song("notcached01")
        assert pending["has_video"] is None
        await asyncio.sleep(0.05)
        # 流水線跑完才知道答案（假流水線沒回 video_path = 只有音訊）
        assert pending["has_video"] is False

    asyncio.run(scenario())


# --- 公平輪唱（排麥輪序）---

def rotation_manager(tmp_path, count=8):
    """準備一批已快取的歌，讓每一次點歌都是秒進佇列（不必等假流水線）。"""
    ids = [f"rot{n:08d}" for n in range(count)]
    manager, _ = make_manager(tmp_path, cached_ids=ids)
    return manager, ids


def queue_singers(manager):
    return [item["requested_by"] for item in manager.queue]


def test_rotation_is_off_by_default(tmp_path):
    """預設是先到先唱 —— 沒講好就改動排序規則，使用者只會覺得佇列自己亂跳。"""
    async def scenario():
        manager, ids = rotation_manager(tmp_path)
        assert manager.get_full_state()["rotation_enabled"] is False
        await manager.add_song(ids[0], requested_by="小明")   # 直接上台
        await manager.add_song(ids[1], requested_by="小明")
        await manager.add_song(ids[2], requested_by="小明")
        await manager.add_song(ids[3], requested_by="小美")
        assert queue_singers(manager) == ["小明", "小明", "小美"]

    asyncio.run(scenario())


def test_rotation_puts_new_singer_before_second_round(tmp_path):
    async def scenario():
        manager, ids = rotation_manager(tmp_path)
        await manager.update_controls({"rotation_enabled": True})
        await manager.add_song(ids[0], requested_by="小明")   # 直接上台（第 1 輪）
        await manager.add_song(ids[1], requested_by="小明")   # 他的第 2 輪
        await manager.add_song(ids[2], requested_by="小明")   # 他的第 3 輪
        await manager.add_song(ids[3], requested_by="小美")   # 她的第 1 輪 → 插到最前面
        await manager.add_song(ids[4], requested_by="阿華")   # 他的第 1 輪 → 小美後面
        assert queue_singers(manager) == ["小美", "阿華", "小明", "小明"]

    asyncio.run(scenario())


def test_rotation_counts_songs_already_sung(tmp_path):
    """唱完一首再點，不能排到還沒唱過的人前面。"""
    async def scenario():
        manager, ids = rotation_manager(tmp_path)
        await manager.update_controls({"rotation_enabled": True})
        await manager.add_song(ids[0], requested_by="小明")   # 上台並唱完
        await manager.add_song(ids[1], requested_by="小美")
        await manager.add_song(ids[2], requested_by="小明")   # 他的第 2 輪
        assert queue_singers(manager) == ["小美", "小明"]

    asyncio.run(scenario())


def test_rotation_never_jumps_ahead_of_priority(tmp_path):
    async def scenario():
        manager, ids = rotation_manager(tmp_path)
        await manager.update_controls({"rotation_enabled": True})
        await manager.add_song(ids[0], requested_by="小明")               # 上台
        await manager.add_song(ids[1], requested_by="小明")
        await manager.add_song(ids[2], requested_by="小明", priority=True)  # 插播
        await manager.add_song(ids[3], requested_by="小美")
        # 插播那首仍然是下一首；小美排在它後面、小明的第 2 輪前面
        assert manager.queue[0]["song_id"] == ids[2]
        assert manager.queue[0]["priority"] is True
        assert queue_singers(manager) == ["小明", "小美", "小明"]

    asyncio.run(scenario())


def test_rotation_treats_everyone_unnamed_as_plain_fifo(tmp_path):
    """沒人取暱稱時，開著輪唱的行為與沒開一模一樣（不會有人覺得佇列在亂跳）。"""
    async def scenario():
        manager, ids = rotation_manager(tmp_path)
        await manager.update_controls({"rotation_enabled": True})
        for song_id in ids[:4]:
            await manager.add_song(song_id)
        assert [item["song_id"] for item in manager.queue] == ids[1:4]

    asyncio.run(scenario())


def test_rotation_state_reports_rounds_and_people(tmp_path):
    async def scenario():
        manager, ids = rotation_manager(tmp_path)
        await manager.update_controls({"rotation_enabled": True})
        await manager.add_song(ids[0], requested_by="小明")   # 上台（已唱 1 首）
        await manager.add_song(ids[1], requested_by="小明")
        await manager.add_song(ids[2], requested_by="小美")
        state = manager.get_full_state()
        rounds = state["rotation"]["rounds"]
        assert rounds[manager.queue[0]["queue_id"]] == 1      # 小美的第 1 輪
        assert rounds[manager.queue[1]["queue_id"]] == 2      # 小明的第 2 輪
        people = {s["name"]: s for s in state["rotation"]["singers"]}
        assert people["小明"]["sung"] == 1 and people["小明"]["pending"] == 1
        assert state["rotation"]["named_count"] == 2

    asyncio.run(scenario())


def test_placement_tells_the_requester_where_the_song_landed(tmp_path):
    async def scenario():
        manager, ids = rotation_manager(tmp_path)
        await manager.update_controls({"rotation_enabled": True})
        await manager.add_song(ids[0], requested_by="小明")   # 上台
        await manager.add_song(ids[1], requested_by="小明")
        item = await manager.add_song(ids[2], requested_by="小美")
        placement = manager.placement_of(item["queue_id"])
        assert placement["enabled"] is True
        assert placement["position"] == 1 and placement["round"] == 1
        assert placement["ahead_of"] == 1                     # 插到一首歌前面

        # 已經上台的歌不在佇列裡，回報 None 而不是硬湊一個位置
        gone = manager.placement_of("no-such-queue-id")
        assert gone["position"] is None and gone["ahead_of"] == 0

    asyncio.run(scenario())


def test_reset_rotation_clears_counts_but_not_the_queue(tmp_path):
    """
    歸零的是「誰唱過幾首」，不是佇列 —— 已經排好的順序是大家看著排出來的。
    """
    async def scenario():
        manager, ids = rotation_manager(tmp_path)
        await manager.update_controls({"rotation_enabled": True})
        await manager.add_song(ids[0], requested_by="小明")   # 上台
        await manager.add_song(ids[1], requested_by="小美")
        before = [item["queue_id"] for item in manager.queue]
        summary = await manager.reset_rotation()
        assert manager.rotation.counts() == {}
        assert [item["queue_id"] for item in manager.queue] == before
        assert all(s["sung"] == 0 for s in summary["singers"])

    asyncio.run(scenario())


def test_session_gap_comes_from_settings(tmp_path):
    """輪序與整晚打包共用同一個「一場」的定義。"""
    class FakeSettings:
        def __init__(self, value):
            self.value = value

        def get(self, key, fallback=None):
            return self.value if key == "recording_session_gap_hours" else fallback

    async def scenario():
        manager, _ = make_manager(tmp_path)
        assert manager.session_gap_hours() == 6.0     # 沒有設定物件時的預設
        manager.settings = FakeSettings(9)
        assert manager.session_gap_hours() == 9.0
        manager.settings = FakeSettings("壞掉的值")
        assert manager.session_gap_hours() == 6.0

    asyncio.run(scenario())


# --- 每人待唱上限（點歌額度）---

def test_pending_limit_is_off_by_default(tmp_path):
    """預設不限 —— 跟輪唱一樣，會改變「我點不點得了歌」的規則要講好才開。"""
    async def scenario():
        manager, ids = rotation_manager(tmp_path)
        assert manager.get_full_state()["pending_limit"] == 0
        for song_id in ids:
            await manager.add_song(song_id, requested_by="小明")
        assert len(manager.queue) == len(ids) - 1   # 第一首直接上台

    asyncio.run(scenario())


def test_pending_limit_blocks_the_next_song(tmp_path):
    async def scenario():
        manager, ids = rotation_manager(tmp_path)
        await manager.update_controls({"pending_limit": 2})
        await manager.add_song(ids[0], requested_by="小明")   # 上台，不佔額度
        await manager.add_song(ids[1], requested_by="小明")
        await manager.add_song(ids[2], requested_by="小明")
        with pytest.raises(song_quota.QuotaExceeded) as excinfo:
            await manager.add_song(ids[3], requested_by="小明")
        assert excinfo.value.verdict["pending"] == 2
        assert excinfo.value.verdict["limit"] == 2
        # 擋下來的那一首完全沒有進到佇列裡
        assert len(manager.queue) == 2

    asyncio.run(scenario())


def test_singing_a_song_frees_a_slot(tmp_path):
    """
    決定一那句承諾要真的成立：排滿了等一首唱完就又能點。

    這是這個功能與「今晚最多幾首」最大的差別 —— 後者沒有這一條，
    被擋下來的人今晚剩下的三個小時都不能點。
    """
    async def scenario():
        manager, ids = rotation_manager(tmp_path)
        await manager.update_controls({"pending_limit": 1})
        await manager.add_song(ids[0], requested_by="小明")   # 上台
        await manager.add_song(ids[1], requested_by="小明")   # 待唱 1/1
        with pytest.raises(song_quota.QuotaExceeded):
            await manager.add_song(ids[2], requested_by="小明")
        await manager.play_next()                             # 那一首上台了
        item = await manager.add_song(ids[2], requested_by="小明")
        assert item["song_id"] == ids[2]

    asyncio.run(scenario())


def test_each_named_singer_has_their_own_allowance(tmp_path):
    async def scenario():
        manager, ids = rotation_manager(tmp_path)
        await manager.update_controls({"pending_limit": 1})
        await manager.add_song(ids[0], requested_by="小明")   # 上台
        await manager.add_song(ids[1], requested_by="小明")   # 小明 1/1
        await manager.add_song(ids[2], requested_by="小美")   # 小美 1/1
        await manager.add_song(ids[3], requested_by="阿華")   # 阿華 1/1
        assert queue_singers(manager) == ["小明", "小美", "阿華"]
        with pytest.raises(song_quota.QuotaExceeded):
            await manager.add_song(ids[4], requested_by="小美")

    asyncio.run(scenario())


def test_unnamed_singers_share_one_allowance(tmp_path):
    """
    決定二：沒取暱稱的所有人共用一份額度，取暱稱才拿得到自己的。

    反過來寫（沒取名的不受限）等於公告「把暱稱刪掉就無限點」。
    """
    async def scenario():
        manager, ids = rotation_manager(tmp_path)
        await manager.update_controls({"pending_limit": 2})
        await manager.add_song(ids[0])                        # 上台
        await manager.add_song(ids[1])
        await manager.add_song(ids[2])                        # 這一桶滿了
        with pytest.raises(song_quota.QuotaExceeded):
            await manager.add_song(ids[3])
        # 取個暱稱就有屬於自己的額度
        item = await manager.add_song(ids[3], requested_by="小明")
        assert item["requested_by"] == "小明"

    asyncio.run(scenario())


def test_priority_bypasses_the_limit_but_still_consumes_it(tmp_path):
    """
    插播是現場按下去的決定，機器的規則不該推翻它；但它照樣算進待唱數，
    否則它就變成「繞過額度」的那顆按鈕（而那顆按鈕在每一張歌卡上）。
    """
    async def scenario():
        manager, ids = rotation_manager(tmp_path)
        await manager.update_controls({"pending_limit": 1})
        await manager.add_song(ids[0], requested_by="小明")   # 上台
        await manager.add_song(ids[1], requested_by="小明")   # 待唱 1/1
        inserted = await manager.add_song(ids[2], requested_by="小明", priority=True)
        assert manager.queue[0]["queue_id"] == inserted["queue_id"]
        # 插播進去之後他有 2 首待唱，普通點歌照樣被擋（而且是被擋得更早）
        assert manager.quota_of("小明")["pending"] == 2
        with pytest.raises(song_quota.QuotaExceeded):
            await manager.add_song(ids[3], requested_by="小明")

    asyncio.run(scenario())


def test_lowering_the_limit_never_removes_queued_songs(tmp_path):
    """
    決定四：調低上限（或中途才打開）不動任何已經排好的歌。

    「超過的從後面砍掉」會是這個系統最嚴重的一次背叛：有人動了一個設定，
    別人排好的歌就消失了，而且畫面上看不出是誰、為什麼。
    """
    async def scenario():
        manager, ids = rotation_manager(tmp_path)
        await manager.add_song(ids[0], requested_by="小明")   # 上台
        for song_id in ids[1:5]:
            await manager.add_song(song_id, requested_by="小明")
        before = [item["queue_id"] for item in manager.queue]
        await manager.update_controls({"pending_limit": 1})
        assert [item["queue_id"] for item in manager.queue] == before
        # 只是在降回上限以下之前點不了新的
        with pytest.raises(song_quota.QuotaExceeded):
            await manager.add_song(ids[5], requested_by="小明")

    asyncio.run(scenario())


def test_pending_limit_is_clamped_like_other_controls(tmp_path):
    async def scenario():
        manager, _ = rotation_manager(tmp_path)
        await manager.update_controls({"pending_limit": -3})
        assert manager.pending_limit == 0
        await manager.update_controls({"pending_limit": 999})
        assert manager.pending_limit == song_quota.MAX_PENDING_LIMIT
        await manager.update_controls({"pending_limit": "壞掉的值"})
        assert manager.pending_limit == 0

    asyncio.run(scenario())


def test_state_carries_quota_usage(tmp_path):
    """畫面上那一行「小明 2/2 滿」的資料來源。"""
    async def scenario():
        manager, ids = rotation_manager(tmp_path)
        await manager.update_controls({"pending_limit": 2})
        await manager.add_song(ids[0], requested_by="小明")   # 上台
        await manager.add_song(ids[1], requested_by="小明")
        await manager.add_song(ids[2], requested_by="小明")
        await manager.add_song(ids[3], requested_by="小美")
        quota = manager.get_full_state()["quota"]
        assert quota["limit"] == 2
        assert [(s["name"], s["pending"], s["full"]) for s in quota["singers"]] == [
            ("小明", 2, True), ("小美", 1, False)]

    asyncio.run(scenario())


def test_quota_of_reports_standing_after_the_add(tmp_path):
    """
    quota_of 回報的是「現在站在哪」，所以點完第一首（上限 3）剩 2，不是 1 ——
    使用者會拿這句話跟畫面上那一行「1/3」對照，對不上就不會再相信任何一邊。
    """
    async def scenario():
        manager, ids = rotation_manager(tmp_path)
        await manager.update_controls({"pending_limit": 3})
        await manager.add_song(ids[0], requested_by="小明")   # 上台，不佔額度
        assert manager.quota_of("小明") == {
            "limit": 3, "pending": 0, "remaining": 3, "full": False,
            "name": "小明", "anonymous": False}
        await manager.add_song(ids[1], requested_by="小明")
        assert manager.quota_of("小明")["remaining"] == 2

    asyncio.run(scenario())


# --- 自動接歌（沒有人點歌時，機器自己接一首）---

class FakeLibrary:
    """假曲庫索引：直接回一份固定的「已經備好」清單。"""

    def __init__(self, entries):
        self._entries = list(entries)

    def entries(self):
        return [dict(e) for e in self._entries]


class FakeFavorites:
    def __init__(self, ids=()):
        self._ids = list(ids)

    def ids(self):
        return list(self._ids)


class AutofillSettings:
    """只提供自動接歌（必要時加上包廂計時）規則的假設定物件。"""

    def __init__(self, enabled=True, source="mixed", idle_seconds=20, stop_after=3,
                 room=None):
        self._policy = {"enabled": enabled, "source": source,
                        "idle_seconds": idle_seconds, "stop_after": stop_after}
        self._room = room

    def autofill_policy(self):
        return dict(self._policy)

    def room_policy(self):
        # 沒特別指定就是「計時沒開」—— 自動接歌的測試多半不關心計時
        return dict(self._room or {
            "enabled": False, "minutes": 180, "autostart": False,
            "warn_minutes": 10, "last_call_minutes": 3,
            "expire_action": "finish_song", "extend_minutes": 30})

    def get(self, key, fallback=None):
        return fallback


def autofill_manager(tmp_path, library_ids=("lib00000001", "lib00000002", "lib00000003"),
                     favorites=(), **policy):
    """一台開著自動接歌、曲庫裡有幾首備好歌的機器，外加一個可以撥的時鐘。"""
    manager, stats = make_manager(tmp_path, cached_ids=library_ids)
    entries = [{"song_id": sid, "title": f"曲庫歌 {sid}", "artist": "歌手",
                "thumbnail": "", "plays": 0} for sid in library_ids]
    manager.library = FakeLibrary(entries)
    manager.favorites = FakeFavorites(favorites)
    manager.settings = AutofillSettings(**policy)
    clock = {"t": 1000.0}
    manager._now = lambda: clock["t"]
    return manager, stats, clock


async def idle_until_autofill(manager, clock, seconds=20.0):
    """撥過空閒門檻，讓心跳接一首。回傳接到的那一首（沒接就是 None）。"""
    await manager.tick_autofill()      # 第一次只是開始計算空閒
    clock["t"] += seconds + 1
    return await manager.tick_autofill()


def test_autofill_is_off_by_default(tmp_path):
    """預設關著（決定八）：會讓機器自己出聲的功能，包廂要先講好才開。"""
    async def scenario():
        manager, _ = make_manager(tmp_path, cached_ids=["lib00000001"])
        state = manager.get_full_state()["autofill"]
        assert state["enabled"] is False
        assert await manager.tick_autofill() is None
        assert manager.current_song is None

    asyncio.run(scenario())


def test_autofill_waits_out_the_idle_window(tmp_path):
    """
    空了要先等一下下才接（決定四）。

    門檻沒到就接的話，會跟正在找下一首的人搶 —— 而使用者的下一個動作是按切歌。
    """
    async def scenario():
        manager, _, clock = autofill_manager(tmp_path, idle_seconds=20)
        assert await manager.tick_autofill() is None     # 開始算空閒
        clock["t"] += 10
        assert await manager.tick_autofill() is None     # 還不到 20 秒
        assert manager.current_song is None
        clock["t"] += 11
        played = await manager.tick_autofill()
        assert played is not None
        assert manager.current_song["auto"] is True
        assert manager.current_song["auto_reason"]
        assert manager.is_playing is True

    asyncio.run(scenario())


def test_auto_song_is_nobody_s_song(tmp_path):
    """
    機器接的歌不算任何人的一首（決定二）：不進排行、不進已唱歷史、不佔輪序。

    排行那一條是硬性的 —— 自動接歌照排行挑歌，播出來又計進排行的話，
    那條排行三個晚上之後就只剩機器自己的回音。
    """
    async def scenario():
        manager, stats, clock = autofill_manager(tmp_path)
        assert await idle_until_autofill(manager, clock) is not None
        assert stats.total_plays() == 0
        assert manager.song_history.total_count() == 0
        assert manager.rotation.counts() == {}
        assert manager.current_song["requested_by"] == ""

    asyncio.run(scenario())


def test_autofill_does_not_start_the_room_clock(tmp_path):
    """
    自動開錶認的是「這一場開始了」，而機器自己接的那一首不算（決定六）。

    不擋的話，最糟的情形是一間沒有人的包廂自己把三小時的錶按下去。
    """
    async def scenario():
        manager, _, clock = autofill_manager(tmp_path)
        manager.settings = AutofillSettings(
            room={"enabled": True, "minutes": 60, "autostart": True,
                  "warn_minutes": 10, "last_call_minutes": 3,
                  "expire_action": "finish_song", "extend_minutes": 30})
        assert await idle_until_autofill(manager, clock) is not None
        assert manager.room.snapshot()["active"] is False
        # 但人點的歌照樣會把錶打開
        await manager.add_song("lib00000002", requested_by="小明")
        assert manager.room.snapshot()["active"] is True

    asyncio.run(scenario())


def test_autofill_stops_after_the_configured_streak(tmp_path):
    """
    連著接幾首沒有人接手就停（決定五）：沒有人點歌通常代表沒有人在了。

    有人點了一首，計數歸零，機器重新願意接。
    """
    async def scenario():
        manager, _, clock = autofill_manager(tmp_path, stop_after=2)
        assert await idle_until_autofill(manager, clock) is not None
        await manager.skip_current()                      # 唱完，佇列空了
        assert await idle_until_autofill(manager, clock) is not None
        assert manager.autofill_streak == 2
        await manager.skip_current()
        assert await idle_until_autofill(manager, clock) is None   # 接滿了，安靜下來
        assert manager.get_full_state()["autofill"]["stopped"] is True

        # 有人點歌 → 計數歸零 → 機器又願意接
        await manager.add_song("lib00000001", requested_by="小明")
        assert manager.autofill_streak == 0
        await manager.skip_current()
        assert await idle_until_autofill(manager, clock) is not None

    asyncio.run(scenario())


def test_autofill_keeps_quiet_while_the_queue_has_songs(tmp_path):
    """佇列裡還有歌（就算還在跑流水線）就輪不到機器 —— 那首跑完會自己接上。"""
    async def scenario():
        manager, _, clock = autofill_manager(tmp_path)
        manager.queue.append({"queue_id": "q1", "song_id": "pending01",
                              "status": "PENDING", "requested_by": "小明"})
        assert await manager.tick_autofill() is None
        clock["t"] += 600
        assert await manager.tick_autofill() is None
        assert manager.current_song is None

    asyncio.run(scenario())


def test_auto_song_yields_immediately_when_someone_orders(tmp_path):
    """
    機器接的歌開播 45 秒內有人點歌 → 立刻讓位（決定三）。

    切掉的是一首沒有人點的歌，不是把誰的演唱打斷。
    """
    async def scenario():
        manager, stats, clock = autofill_manager(tmp_path)
        assert await idle_until_autofill(manager, clock) is not None
        auto_id = manager.current_song["song_id"]
        clock["t"] += 10                                  # 還在前奏
        await manager.add_song("lib00000002", requested_by="小明")
        assert manager.current_song["song_id"] == "lib00000002"
        assert manager.current_song.get("auto") is not True
        assert manager.current_song["requested_by"] == "小明"
        # 人點的那一首照樣計入排行（讓位不影響它是誰點的）
        assert stats.total_plays() == 1
        assert manager.history[-1]["song_id"] == auto_id

    asyncio.run(scenario())


def test_auto_song_finishes_when_someone_is_already_singing_it(tmp_path):
    """
    超過 45 秒就讓它唱完再換：已經唱到一半的人被切掉，比點歌的人多等兩分鐘難堪。

    這跟包廂計時的決定一是同一個原則 —— 機器可以決定下一首播什麼，
    但不該去停一個正在唱歌的人。
    """
    async def scenario():
        manager, _, clock = autofill_manager(tmp_path)
        assert await idle_until_autofill(manager, clock) is not None
        auto_id = manager.current_song["song_id"]
        clock["t"] += 90                                   # 已經唱進去了
        await manager.add_song("lib00000002", requested_by="小明")
        assert manager.current_song["song_id"] == auto_id  # 沒被切掉
        assert [item["song_id"] for item in manager.queue] == ["lib00000002"]
        # 唱完就換上人點的那一首
        await manager.skip_current()
        assert manager.current_song["song_id"] == "lib00000002"

    asyncio.run(scenario())


def test_autofill_does_not_replay_what_it_just_played(tmp_path):
    """同一晚聽到第二次同一首，包廂就會去按切歌。"""
    async def scenario():
        manager, _, clock = autofill_manager(
            tmp_path, library_ids=("lib00000001", "lib00000002"))
        assert await idle_until_autofill(manager, clock) is not None
        first = manager.current_song["song_id"]
        await manager.skip_current()
        assert await idle_until_autofill(manager, clock) is not None
        assert manager.current_song["song_id"] != first
        assert manager.autofill_recent == [first, manager.current_song["song_id"]]

    asyncio.run(scenario())


def test_autofill_never_steals_a_song_from_the_queue(tmp_path):
    """
    曲庫只有兩首、其中一首已經排在佇列裡：機器只能接另外那一首。

    偷跑掉別人排好的歌，那個人待會會看到自己點的歌「已經唱過了」。
    """
    async def scenario():
        manager, _, clock = autofill_manager(
            tmp_path, library_ids=("lib00000001", "lib00000002"))
        # 先讓一首人點的歌上台，另一首排在佇列裡
        await manager.add_song("lib00000001", requested_by="小明")
        await manager.add_song("lib00000002", requested_by="小美")
        await manager.skip_current()                       # 小美那首上台，佇列空了
        assert manager.current_song["song_id"] == "lib00000002"
        assert await idle_until_autofill(manager, clock) is None  # 兩首都在用，不接

    asyncio.run(scenario())


def test_autofill_stays_silent_after_the_room_time_is_up(tmp_path):
    """散場畫面之後自己放歌，是這個功能最難堪的壞法（決定七）。"""
    async def scenario():
        manager, _, clock = autofill_manager(tmp_path)
        manager.settings = AutofillSettings(
            room={"enabled": True, "minutes": 60, "autostart": False,
                  "warn_minutes": 10, "last_call_minutes": 3,
                  "expire_action": "finish_song", "extend_minutes": 30})
        manager.room.start(60)
        manager.room.mark_halted(True)
        assert await idle_until_autofill(manager, clock) is None
        assert manager.current_song is None

    asyncio.run(scenario())


def test_autofill_state_travels_with_every_broadcast(tmp_path):
    """點歌台要能標出「現在這首是機器接的」，而那個標記必須跟佇列同一份狀態。"""
    async def scenario():
        manager, _, clock = autofill_manager(tmp_path, source="fresh", stop_after=5)
        state = manager.get_full_state()["autofill"]
        assert state["enabled"] is True
        assert state["source"] == "fresh"
        assert state["playing"] is False
        assert state["streak"] == 0
        await idle_until_autofill(manager, clock)
        state = manager.get_full_state()["autofill"]
        assert state["playing"] is True
        assert state["streak"] == 1
        assert state["last"]["reason"]
        assert state["last"]["title"].startswith("曲庫歌 ")

    asyncio.run(scenario())


# --- 🎲 來一首（隨機點歌）---

def test_random_pick_counts_as_a_human_request(tmp_path):
    """
    🎲 用的是同一副挑歌規則，但算人點的：有人按了那顆鍵，就是有人做了決定。
    """
    async def scenario():
        manager, stats, _ = autofill_manager(tmp_path)
        result = await manager.random_pick(requested_by="小明")
        assert result is not None
        assert result["reason"]
        assert manager.current_song["requested_by"] == "小明"
        assert manager.current_song.get("auto") is not True
        assert stats.total_plays() == 1
        assert manager.song_history.total_count() == 1

    asyncio.run(scenario())


def test_random_pick_returns_none_when_the_library_is_empty(tmp_path):
    """曲庫空的不是錯誤，是「這台機器還沒有歌可以挑」。"""
    async def scenario():
        manager, _, _ = autofill_manager(tmp_path, library_ids=())
        assert await manager.random_pick(requested_by="小明") is None

    asyncio.run(scenario())


def test_random_pick_still_obeys_the_pending_limit(tmp_path):
    """這顆鍵是點歌的捷徑，不是繞過規則的後門。"""
    async def scenario():
        manager, _, _ = autofill_manager(tmp_path)
        await manager.update_controls({"pending_limit": 1})
        await manager.random_pick(requested_by="小明")     # 上台，不佔額度
        await manager.random_pick(requested_by="小明")     # 排一首，額度滿
        with pytest.raises(song_quota.QuotaExceeded):
            await manager.random_pick(requested_by="小明")

    asyncio.run(scenario())
