"""三段音色等化器的單元測試。

伺服器不處理音訊 —— 它只是那六個數字的共享狀態。所以這裡守的是兩件事：

  1. **夾限**。這六個值會一路走到舞台端的濾波器與限幅器前面，
     一個 +40 進去的症狀是整首歌都在破音，而沒有人會聯想到等化器。
  2. **前後端的上限是同一組**。前端照 `tone-eq.js` 的 `EQ_TARGETS` 畫滑桿、
     後端照 `tone_eq.py` 夾值，兩邊分岔的症狀是「推到底之後放手會跳回去」——
     使用者只會覺得滑桿壞了。
"""
import re
from pathlib import Path

import pytest

from backend.services.play_stats import PlayStats
from backend.services.queue_manager import QueueManager
from backend.services.settings import SETTINGS_SPEC, SystemSettings, default_settings
from backend.services.song_history import SongHistory
from backend.services.storage import SongStorage
from backend.services.tone_eq import (
    MIC_EQ_FIELDS,
    MIC_EQ_LIMIT_DB,
    MUSIC_EQ_FIELDS,
    MUSIC_EQ_LIMIT_DB,
    coerce_eq_db,
)

FRONTEND_TONE_EQ = Path(__file__).resolve().parents[1] / "frontend" / "js" / "tone-eq.js"


class _FakeProcessor:
    """這一支完全不碰流水線 —— 佇列只是那六個數字的容器。"""

    async def process_song(self, url, progress_callback=None):   # pragma: no cover
        raise AssertionError("音色等化器的測試不該碰到流水線")


@pytest.fixture()
def manager(tmp_path):
    return QueueManager(
        _FakeProcessor(),
        SongStorage(tmp_path / "songs"),
        play_stats=PlayStats(tmp_path / "play_stats.json"),
        song_history=SongHistory(tmp_path / "song_history.json"),
    )


# --- 夾限 ---

def test_within_range_values_pass_through():
    assert coerce_eq_db(0, MIC_EQ_LIMIT_DB) == 0.0
    assert coerce_eq_db(6, MIC_EQ_LIMIT_DB) == 6.0
    assert coerce_eq_db(-6.5, MIC_EQ_LIMIT_DB) == -6.5


def test_out_of_range_is_clamped_not_rejected():
    # 夾回去而不是丟例外：設定頁與滑桿是給人用的，越界不該讓伺服器回 500
    assert coerce_eq_db(99, MIC_EQ_LIMIT_DB) == MIC_EQ_LIMIT_DB
    assert coerce_eq_db(-99, MIC_EQ_LIMIT_DB) == -MIC_EQ_LIMIT_DB
    assert coerce_eq_db(99, MUSIC_EQ_LIMIT_DB) == MUSIC_EQ_LIMIT_DB


def test_unreadable_values_fall_back_to_flat():
    # 回 0（等化器沒作用）的失敗模式看得見也改得掉；丟例外的失敗模式是整頁不動
    for bad in (None, "", "abc", [], {}, float("nan"), float("inf")):
        assert coerce_eq_db(bad, MIC_EQ_LIMIT_DB) == 0.0


def test_strings_are_accepted():
    # 手機的表單送上來的都是字串
    assert coerce_eq_db("4", MIC_EQ_LIMIT_DB) == 4.0
    assert coerce_eq_db("-20", MIC_EQ_LIMIT_DB) == -MIC_EQ_LIMIT_DB


def test_snapped_to_half_a_decibel():
    # 前後端都吸附在同一格，才不會因為浮點尾數互相覆寫對方的值
    assert coerce_eq_db(3.26, MIC_EQ_LIMIT_DB) == 3.5
    assert coerce_eq_db(3.1, MIC_EQ_LIMIT_DB) == 3.0


def test_music_range_is_tighter_than_mic():
    # 伴奏是已經被自動音量平衡對到 −14 LUFS 的成品，動它的空間要比麥克風小
    assert MUSIC_EQ_LIMIT_DB < MIC_EQ_LIMIT_DB


# --- 共享控制狀態 ---

def test_queue_manager_starts_flat(manager):
    state = manager.get_full_state()
    for key in MIC_EQ_FIELDS + MUSIC_EQ_FIELDS:
        assert state[key] == 0.0, f"{key} 開機就帶著曲線的話，第一個覺得聲音怪的人查不到原因"


def test_queue_manager_clamps_each_band(manager):
    manager._apply_controls({
        "mic_eq_bass": 99, "mic_eq_treble": -99,
        "music_eq_bass": 99, "music_eq_treble": -99,
    })
    state = manager.get_full_state()
    assert state["mic_eq_bass"] == MIC_EQ_LIMIT_DB
    assert state["mic_eq_treble"] == -MIC_EQ_LIMIT_DB
    assert state["music_eq_bass"] == MUSIC_EQ_LIMIT_DB
    assert state["music_eq_treble"] == -MUSIC_EQ_LIMIT_DB


def test_one_band_at_a_time_does_not_reset_the_others(manager):
    """滑桿一次只推一段 —— 送一格不該把另外兩格歸零。

    這是整個功能最容易出現、也最難查的錯：兩支手機同時調音時，
    一邊推低音會把另一邊剛調好的高音抹掉，而畫面上兩邊看起來都是對的。
    """
    manager._apply_controls({"mic_eq_bass": 4, "mic_eq_mid": -2, "mic_eq_treble": 3})
    manager._apply_controls({"mic_eq_bass": 1})
    state = manager.get_full_state()
    assert state["mic_eq_bass"] == 1
    assert state["mic_eq_mid"] == -2
    assert state["mic_eq_treble"] == 3


def test_mic_and_music_are_independent(manager):
    manager._apply_controls({"mic_eq_bass": 6})
    assert manager.get_full_state()["music_eq_bass"] == 0.0
    manager._apply_controls({"music_eq_treble": -5})
    assert manager.get_full_state()["mic_eq_bass"] == 6


def test_unreadable_band_does_not_raise(manager):
    manager._apply_controls({"mic_eq_mid": "nope"})
    assert manager.get_full_state()["mic_eq_mid"] == 0.0


# --- 開機預設值 ---

def test_settings_default_to_flat():
    defaults = default_settings()
    for field in MIC_EQ_FIELDS + MUSIC_EQ_FIELDS:
        assert defaults[f"default_{field}"] == 0.0


def test_settings_ranges_match_the_shared_state(tmp_path):
    """設定頁存得進去的值不能比即時控制夾得還寬。

    寬的那一邊會在開機時被套用，然後第一次推滑桿就被夾回去 ——
    症狀是「開機音色正常，碰一下就跳掉」。
    """
    settings = SystemSettings(tmp_path / "settings.json")
    result = settings.update({f"default_{f}": 99 for f in MIC_EQ_FIELDS + MUSIC_EQ_FIELDS})
    for field in MIC_EQ_FIELDS:
        assert result[f"default_{field}"] == MIC_EQ_LIMIT_DB
    for field in MUSIC_EQ_FIELDS:
        assert result[f"default_{field}"] == MUSIC_EQ_LIMIT_DB


def test_every_eq_field_has_a_boot_default_and_a_control_mapping():
    from backend.services.settings import CONTROL_DEFAULT_KEYS
    for field in MIC_EQ_FIELDS + MUSIC_EQ_FIELDS:
        key = f"default_{field}"
        assert key in SETTINGS_SPEC, f"{key} 少了設定欄位"
        assert CONTROL_DEFAULT_KEYS.get(key) == field, f"{key} 沒有對應到共享狀態"


# --- 前後端的上限釘在一起 ---

def test_frontend_and_backend_share_the_same_limits():
    """`tone-eq.js` 的 EQ_TARGETS 與這裡的上限必須一致。

    分岔的話，滑桿推得到的位置會比伺服器收得下的多，而放手之後值會跳回去 ——
    使用者的結論是「這根滑桿壞了」，不會想到是兩個檔案裡的兩個數字。
    """
    source = FRONTEND_TONE_EQ.read_text(encoding="utf-8")
    limits = [float(m) for m in re.findall(r"limitDb:\s*([0-9.]+)", source)]
    assert limits == [MIC_EQ_LIMIT_DB, MUSIC_EQ_LIMIT_DB]
