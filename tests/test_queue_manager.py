"""點歌佇列（QueueManager）單元測試。

用假的 SongProcessor 取代真正的下載/分離流水線，
專測佇列邏輯：點歌、插播、切歌、排序、控制參數夾限。
"""
import asyncio
import json

from backend.services.play_stats import PlayStats
from backend.services.queue_manager import QueueManager
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
    return QueueManager(FakeProcessor(), storage, play_stats=stats), stats


def test_cached_song_plays_immediately(tmp_path):
    async def scenario():
        manager, stats = make_manager(tmp_path, cached_ids=["cached00001"])
        item = await manager.add_song("cached00001")
        assert item["status"] == "READY"
        assert item["title"] == "快取歌 cached00001"
        # 沒有歌在播 → 直接上台，並計入點唱統計
        assert manager.current_song["song_id"] == "cached00001"
        assert manager.is_playing is True
        assert stats.total_plays() == 1
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

    asyncio.run(scenario())


def test_full_state_contains_control_fields(tmp_path):
    manager, _ = make_manager(tmp_path)
    state = manager.get_full_state()
    for key in ("current_song", "queue", "is_playing", "vocal_volume",
                "pitch_shift", "music_volume", "mic_volume", "lyric_offset_ms",
                "show_pitch", "sing_mode"):
        assert key in state
