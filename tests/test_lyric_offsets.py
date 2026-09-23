"""
每首歌字幕偏移的單元測試。

守的是四件事：
  * **校正綁在歌上、活過重開機**（這是整個功能的目的）。
  * **預設值等於沒校正過**（偏移 0 且速度 1.0 不留紀錄，否則「校正過幾首」
    這個數字會失真）。
  * **rebase 是一次交易**：裝置基準 +X 時，所有已校正的歌各 −X ——
    少做這一步就會冒出一批「昨天好好的、今天突然歪了」的歌。而 rebase
    **不准碰速度**：喇叭不會讓音樂變快。
  * **速度不會被偏移的寫入洗掉**：滑桿與方向鍵只送得出 offset_ms，
    如果把「沒帶到」當成「重設為 1.0」，兩點校正的成果會在下一次微調時無聲消失，
    而症狀跟「剛剛按錯方向」長得一模一樣。
"""
import json

import pytest

from backend.services.lyric_offsets import (MAX_OFFSET_MS, MAX_RATE, MIN_RATE,
                                            LyricOffsets, clamp_offset_ms, clamp_rate)


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
    assert raw["version"] == 2
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


# --- 速度校正（兩點校正解出來的那個數字）---

def test_clamp_rate():
    assert clamp_rate(1.024) == 1.024
    assert clamp_rate(0.97) == 0.97
    assert clamp_rate("1.05") == 1.05              # 前端送字串進來也要吃得下
    assert clamp_rate(9.0) == MAX_RATE             # 夾回來，不是丟例外
    assert clamp_rate(0.1) == MIN_RATE
    assert clamp_rate(1.0005) == 1.0               # 比 epsilon 小就是「沒有速度問題」
    # 以下每一個進到分母都會讓整台舞台的字幕消失，所以一律退回 1.0
    assert clamp_rate(0) == 1.0
    assert clamp_rate(None) == 1.0
    assert clamp_rate("abc") == 1.0
    assert clamp_rate(True) == 1.0
    assert clamp_rate(float("nan")) == 1.0
    assert clamp_rate(float("inf")) == 1.0


def test_rate_defaults_to_one(offsets):
    assert offsets.rate("never-tuned") == 1.0
    assert offsets.calibration("never-tuned") == {"offset_ms": 0, "rate": 1.0}


def test_set_calibration_stores_both(offsets):
    applied = offsets.set_calibration("song1", offset_ms=-150, rate=1.024)
    assert applied == {"offset_ms": -150, "rate": 1.024}
    assert offsets.calibration("song1") == {"offset_ms": -150, "rate": 1.024}
    assert offsets.entry("song1")["updated_at"]


def test_rate_alone_is_a_real_calibration(offsets):
    """偏移剛好是 0、只解出速度是很正常的結果 —— 那筆紀錄必須留著。"""
    offsets.set_calibration("song1", offset_ms=0, rate=1.03)
    assert offsets.count() == 1
    assert offsets.rate("song1") == 1.03
    assert offsets.get("song1") == 0
    # 但它不該被算進「升級成本機基準會動到幾首」
    assert offsets.offset_count() == 0


def test_setting_offset_does_not_wipe_rate(offsets):
    """
    這一條是整個 v1.23 最容易無聲壞掉的地方：滑桿與方向鍵只送得出偏移，
    如果沒帶到的欄位被當成「重設」，兩點校正的成果會在下一次微調時消失，
    而使用者看到的症狀（後段又開始越唱越歪）跟「剛剛按錯方向」一模一樣。
    """
    offsets.set_calibration("song1", offset_ms=-150, rate=1.024)
    offsets.set("song1", -100)                     # ← 方向鍵那條路
    assert offsets.calibration("song1") == {"offset_ms": -100, "rate": 1.024}


def test_setting_rate_does_not_wipe_offset(offsets):
    offsets.set("song1", 300)
    offsets.set_calibration("song1", rate=0.98)
    assert offsets.calibration("song1") == {"offset_ms": 300, "rate": 0.98}


def test_both_defaults_means_never_tuned(offsets):
    offsets.set_calibration("song1", offset_ms=200, rate=1.02)
    offsets.set_calibration("song1", offset_ms=0, rate=1.0)
    assert offsets.entry("song1") is None
    assert offsets.count() == 0


def test_clear_removes_rate_too(offsets):
    """歸零是「這首歌的字幕全部重來」。留著速度會生出一首『歸零過卻還是歪』的歌。"""
    offsets.set_calibration("song1", offset_ms=200, rate=1.02)
    assert offsets.clear("song1") is True
    assert offsets.calibration("song1") == {"offset_ms": 0, "rate": 1.0}


def test_rate_survives_restart(tmp_path):
    path = tmp_path / "lyric_offsets.json"
    LyricOffsets(path).set_calibration("song1", offset_ms=-150, rate=1.024)
    again = LyricOffsets(path)
    assert again.calibration("song1") == {"offset_ms": -150, "rate": 1.024}


def test_v1_file_reads_as_rate_one(tmp_path):
    """舊檔（v1，只有 offset_ms）不必轉檔：缺了 rate 就是 1.0。"""
    path = tmp_path / "lyric_offsets.json"
    path.write_text(json.dumps({"version": 1, "songs": {"old": {"offset_ms": 250}}}),
                    encoding="utf-8")
    store = LyricOffsets(path)
    assert store.calibration("old") == {"offset_ms": 250, "rate": 1.0}


def test_garbage_rate_is_skipped_not_fatal(tmp_path):
    path = tmp_path / "lyric_offsets.json"
    path.write_text(json.dumps({
        "version": 2,
        "songs": {
            "bad_rate": {"offset_ms": 100, "rate": "nope"},
            "zero_rate": {"offset_ms": 100, "rate": 0},
            "over_rate": {"offset_ms": 0, "rate": 9.0},
            "only_rate": {"offset_ms": 0, "rate": 1.03},
        },
    }), encoding="utf-8")
    store = LyricOffsets(path)
    assert store.calibration("bad_rate") == {"offset_ms": 100, "rate": 1.0}
    assert store.rate("zero_rate") == 1.0          # 0 在分母裡＝舞台白掉
    assert store.rate("over_rate") == MAX_RATE     # 夾回來，不是整筆丟掉
    assert store.rate("only_rate") == 1.03


def test_all_calibrations(offsets):
    offsets.set_calibration("song1", offset_ms=100, rate=1.02)
    offsets.set("song2", -50)
    assert offsets.all_calibrations() == {
        "song1": {"offset_ms": 100, "rate": 1.02},
        "song2": {"offset_ms": -50, "rate": 1.0},
    }


# --- rebase 與速度 ---

def test_rebase_leaves_rate_alone(offsets):
    """喇叭不會讓音樂變快：裝置基準的加減跟速度沒有關係。"""
    offsets.set_calibration("song1", offset_ms=300, rate=1.024)
    offsets.rebase(100)
    assert offsets.calibration("song1") == {"offset_ms": 200, "rate": 1.024}


def test_rebase_keeps_rate_only_songs(offsets):
    """只解過速度的歌不該被 rebase 碰到（它的偏移本來就是 0）。"""
    offsets.set_calibration("song1", offset_ms=0, rate=1.03)
    result = offsets.rebase(200)
    assert result["changed"] == 0 and result["cleared"] == 0
    assert offsets.rate("song1") == 1.03


def test_rebase_does_not_delete_a_song_that_still_has_a_rate(offsets):
    """
    偏移減到 0、但速度還在的歌**不能刪紀錄**：刪了那首歌會從「對得好好的」
    變回「唱到後段越來越歪」，而使用者剛剛按的是一顆叫「設成本機基準」的鍵。
    """
    offsets.set_calibration("song1", offset_ms=200, rate=1.024)
    result = offsets.rebase(200)
    assert result["cleared"] == 0 and result["changed"] == 1
    assert offsets.calibration("song1") == {"offset_ms": 0, "rate": 1.024}
    assert offsets.count() == 1


def test_offset_count_only_counts_offsets(offsets):
    offsets.set_calibration("song1", offset_ms=0, rate=1.03)
    offsets.set_calibration("song2", offset_ms=200, rate=1.0)
    offsets.set_calibration("song3", offset_ms=-50, rate=0.98)
    assert offsets.count() == 3
    assert offsets.offset_count() == 2
