"""
每首歌字幕偏移的單元測試。

守的是三件事：
  * **偏移綁在歌上、活過重開機**（這是整個功能的目的）。
  * **0 等於沒校正過**（不留紀錄，否則「校正過幾首」這個數字會失真）。
  * **rebase 是一次交易**：裝置基準 +X 時，所有已校正的歌各 −X ——
    少做這一步就會冒出一批「昨天好好的、今天突然歪了」的歌。
"""
import json

import pytest

from backend.services.lyric_offsets import (MAX_OFFSET_MS, LyricOffsets, clamp_offset_ms)


@pytest.fixture
def offsets(tmp_path):
    return LyricOffsets(tmp_path / "lyric_offsets.json")


# --- 夾限 ---

def test_clamp_offset_ms():
    assert clamp_offset_ms(300) == 300
    assert clamp_offset_ms(-300) == -300
    assert clamp_offset_ms(99999) == MAX_OFFSET_MS
    assert clamp_offset_ms(-99999) == -MAX_OFFSET_MS
    assert clamp_offset_ms("150") == 150       # 前端送字串進來也要吃得下
    assert clamp_offset_ms(150.6) == 151       # 四捨五入，不是無聲截斷
    assert clamp_offset_ms(None) == 0
    assert clamp_offset_ms("abc") == 0
    assert clamp_offset_ms(True) == 0          # bool 是 int 的子類


# --- 基本讀寫 ---

def test_unknown_song_is_zero(offsets):
    assert offsets.get("never-tuned") == 0
    assert offsets.entry("never-tuned") is None
    assert offsets.count() == 0


def test_set_and_get(offsets):
    assert offsets.set("song1", 300) == 300
    assert offsets.get("song1") == 300
    assert offsets.count() == 1
    entry = offsets.entry("song1")
    assert entry["song_id"] == "song1" and entry["offset_ms"] == 300
    assert entry["updated_at"]                       # 校正時間要記，交接的人才看得出新舊


def test_set_clamps(offsets):
    assert offsets.set("song1", 99999) == MAX_OFFSET_MS
    assert offsets.get("song1") == MAX_OFFSET_MS


def test_zero_means_never_tuned(offsets):
    offsets.set("song1", 300)
    assert offsets.set("song1", 0) == 0
    # 「沒有校正過」與「校正結果剛好是 0」對使用者是同一件事
    assert offsets.entry("song1") is None
    assert offsets.count() == 0


def test_clear(offsets):
    offsets.set("song1", 300)
    assert offsets.clear("song1") is True
    assert offsets.get("song1") == 0
    assert offsets.clear("song1") is False      # 清第二次不該出錯


def test_all_and_count(offsets):
    offsets.set("a", 100)
    offsets.set("b", -200)
    offsets.set("c", 0)
    assert offsets.all() == {"a": 100, "b": -200}
    assert offsets.count() == 2


def test_empty_song_id_is_ignored(offsets):
    assert offsets.set("", 300) == 0
    assert offsets.count() == 0
    assert offsets.get("") == 0


# --- 持久化 ---

def test_survives_restart(tmp_path):
    path = tmp_path / "lyric_offsets.json"
    first = LyricOffsets(path)
    first.set("song1", 250)
    first.set("song2", -120)

    again = LyricOffsets(path)                  # 重開伺服器
    assert again.get("song1") == 250
    assert again.get("song2") == -120
    assert again.count() == 2


def test_saved_file_shape(tmp_path):
    path = tmp_path / "lyric_offsets.json"
    LyricOffsets(path).set("song1", 250)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == 1
    assert raw["songs"]["song1"]["offset_ms"] == 250
    assert raw["updated_at"]


def test_corrupt_file_backs_up_and_starts_over(tmp_path):
    path = tmp_path / "lyric_offsets.json"
    path.write_text("{ 這不是 JSON", encoding="utf-8")
    store = LyricOffsets(path)
    # 壞檔不該讓功能整個停掉（最糟只是重調一次），但原檔要留著
    assert store.count() == 0
    assert (tmp_path / "lyric_offsets.json.bad").read_text(encoding="utf-8") == "{ 這不是 JSON"
    assert store.set("song1", 120) == 120
    assert LyricOffsets(path).get("song1") == 120


def test_garbage_entries_are_skipped(tmp_path):
    path = tmp_path / "lyric_offsets.json"
    path.write_text(json.dumps({
        "version": 1,
        "songs": {
            "good": {"offset_ms": 300},
            "bad_type": "nope",
            "zero": {"offset_ms": 0},
            "over": {"offset_ms": 999999},
        },
    }), encoding="utf-8")
    store = LyricOffsets(path)
    assert store.get("good") == 300
    assert store.get("bad_type") == 0
    assert store.entry("zero") is None          # 0 不留紀錄
    assert store.get("over") == MAX_OFFSET_MS   # 手改過頭的值夾回來，不是整筆丟掉


# --- rebase（升級成本機基準的另一半）---

def test_rebase_shifts_every_tuned_song(offsets):
    offsets.set("a", 300)
    offsets.set("b", 500)
    offsets.set("c", -100)
    result = offsets.rebase(200)
    assert result["delta_ms"] == 200
    assert offsets.get("a") == 100
    assert offsets.get("b") == 300
    assert offsets.get("c") == -300
    assert result["changed"] == 3 and result["cleared"] == 0


def test_rebase_clears_songs_that_land_on_zero(offsets):
    offsets.set("a", 200)
    offsets.set("b", 500)
    result = offsets.rebase(200)
    # a 的偏差正好就是裝置延遲：升級之後它不再需要自己的校正
    assert offsets.entry("a") is None
    assert offsets.get("b") == 300
    assert result["cleared"] == 1 and result["changed"] == 1
    assert offsets.count() == 1


def test_rebase_zero_is_a_noop(offsets):
    offsets.set("a", 300)
    assert offsets.rebase(0) == {"changed": 0, "cleared": 0, "delta_ms": 0}
    assert offsets.get("a") == 300


def test_rebase_is_reversible(offsets):
    """反向 rebase 回得去 —— 這是使用者唯一的後悔路徑，必須成立。"""
    offsets.set("a", 300)
    offsets.set("b", -150)
    offsets.rebase(200)
    offsets.rebase(-200)
    assert offsets.get("a") == 300
    assert offsets.get("b") == -150


def test_rebase_persists(tmp_path):
    path = tmp_path / "lyric_offsets.json"
    first = LyricOffsets(path)
    first.set("a", 300)
    first.rebase(100)
    assert LyricOffsets(path).get("a") == 200


# --- 併發 ---

def test_concurrent_writes_are_safe(offsets):
    import threading

    def hammer(n):
        for i in range(30):
            offsets.set(f"song{n}", 100 + i)

    threads = [threading.Thread(target=hammer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert offsets.count() == 4
    assert all(v == 129 for v in offsets.all().values())
