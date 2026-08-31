"""已唱歷史：一次一筆的演唱時間序列。"""
import json
from datetime import datetime

from backend.services.song_history import SongHistory, MAX_ENTRIES


def make_history(tmp_path):
    return SongHistory(tmp_path / "song_history.json")


def test_record_and_recent_order(tmp_path):
    h = make_history(tmp_path)
    h.record({"song_id": "a", "title": "第一首"})
    h.record({"song_id": "b", "title": "第二首"})
    h.record({"song_id": "a", "title": "第一首"})  # 同一首唱兩次 = 兩筆

    recent = h.recent()
    assert len(recent) == 3
    assert [e["song_id"] for e in recent] == ["a", "b", "a"]  # 最新在前
    assert recent[0]["sung_at"]


def test_record_requires_song_id(tmp_path):
    h = make_history(tmp_path)
    assert h.record({"title": "沒有 id"}) is None
    assert h.total_count() == 0


def test_recent_limit(tmp_path):
    h = make_history(tmp_path)
    for i in range(10):
        h.record({"song_id": f"s{i}"})
    assert len(h.recent(limit=3)) == 3
    assert h.recent(limit=3)[0]["song_id"] == "s9"


def test_today_count(tmp_path):
    h = make_history(tmp_path)
    h.record({"song_id": "today1"})
    h.record({"song_id": "today2"})
    # 手動塞一筆昨天的，不該被算進今天
    h._entries.insert(0, {"song_id": "old", "sung_at": "2000-01-01T12:00:00"})
    assert h.today_count() == 2
    assert h.total_count() == 3


def test_persistence(tmp_path):
    path = tmp_path / "song_history.json"
    h1 = SongHistory(path)
    h1.record({"song_id": "x", "title": "留下來"})

    h2 = SongHistory(path)
    assert h2.total_count() == 1
    assert h2.recent()[0]["title"] == "留下來"


def test_corrupt_file_recovers(tmp_path):
    path = tmp_path / "song_history.json"
    path.write_text("not json at all", encoding="utf-8")
    h = SongHistory(path)
    assert h.total_count() == 0
    assert h.record({"song_id": "ok"}) is not None


def test_entries_capped(tmp_path):
    h = make_history(tmp_path)
    h._entries = [{"song_id": f"s{i}", "sung_at": datetime.now().isoformat()}
                  for i in range(MAX_ENTRIES)]
    h.record({"song_id": "newest"})
    assert h.total_count() == MAX_ENTRIES
    assert h.recent(limit=1)[0]["song_id"] == "newest"


def test_clear(tmp_path):
    h = make_history(tmp_path)
    h.record({"song_id": "gone"})
    h.clear()
    assert h.total_count() == 0
    # 清空要落盤，重新載入也要是空的
    data = json.loads((tmp_path / "song_history.json").read_text(encoding="utf-8"))
    assert data["entries"] == []
