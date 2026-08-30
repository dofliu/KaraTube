"""點唱統計（熱門排行）單元測試。"""
from backend.services.play_stats import PlayStats


def make_stats(tmp_path):
    return PlayStats(tmp_path / "play_stats.json")


def test_record_play_counts_and_persists(tmp_path):
    stats = make_stats(tmp_path)
    entry = stats.record_play({"song_id": "abc", "title": "歌一", "artist": "歌手A"})
    assert entry["plays"] == 1
    assert entry["title"] == "歌一"
    assert entry["first_played"] == entry["last_played"]

    stats.record_play({"song_id": "abc", "title": "歌一"})
    # 重新載入檔案，確認統計有寫進磁碟
    reloaded = make_stats(tmp_path)
    assert reloaded.top(10)[0]["plays"] == 2
    assert reloaded.total_plays() == 2


def test_record_play_without_id_is_ignored(tmp_path):
    stats = make_stats(tmp_path)
    assert stats.record_play({"title": "沒有 id"}) is None
    assert stats.total_plays() == 0


def test_top_orders_by_plays_then_recency(tmp_path):
    stats = make_stats(tmp_path)
    stats.record_play({"song_id": "twice", "title": "唱兩次"})
    stats.record_play({"song_id": "twice", "title": "唱兩次"})
    stats.record_play({"song_id": "once", "title": "唱一次"})

    top = stats.top(10)
    assert [t["song_id"] for t in top] == ["twice", "once"]
    assert [t["rank"] for t in top] == [1, 2]


def test_top_respects_limit(tmp_path):
    stats = make_stats(tmp_path)
    for i in range(5):
        stats.record_play({"song_id": f"song{i}", "title": f"歌{i}"})
    assert len(stats.top(3)) == 3


def test_reset_clears_everything(tmp_path):
    stats = make_stats(tmp_path)
    stats.record_play({"song_id": "abc", "title": "歌"})
    stats.reset()
    assert stats.total_plays() == 0
    assert stats.top(10) == []


def test_corrupt_file_starts_fresh(tmp_path):
    stats_file = tmp_path / "play_stats.json"
    stats_file.write_text("not json{{{", encoding="utf-8")
    stats = PlayStats(stats_file)
    assert stats.total_plays() == 0
