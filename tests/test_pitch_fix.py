"""智能修音的後端單元測試。

伺服器不處理音訊 —— 它只是「修音開著嗎、修多強」這兩個值的共享狀態。
所以這裡守的是三件事：

  1. **夾限與退路**。強度字串會從手機、舊版前端、還原回來的設定檔三個地方進來，
     其中任何一個送了 "off" 或一個空字串，都不該讓整個點歌台收到 500。
  2. **前後端是同一張強度表**。前端照 `pitch-fix.js` 的 `PITCH_FIX_STRENGTHS`
     決定修多少、滑多快，後端照 `pitch_fix.py` 認選項；兩邊分岔的症狀是
     「設定頁存得進去的強度，點歌台按下去會跳成另一個」。
  3. **開關與強度是兩個欄位**。做成四選一（off/輕/中/強）的話，設定檔裡存著
     off 的機器下次有人打開修音時，打開的是一個沒有作用的功能。
"""
import re
from pathlib import Path

import pytest

from backend.services.pitch_fix import (
    CAPTURE_CENTS,
    DEFAULT_PITCH_FIX_STRENGTH,
    MAX_SHIFT_SEMITONES,
    PITCH_FIX_STRENGTH_CHOICES,
    PITCH_FIX_STRENGTHS,
    coerce_strength,
)
from backend.services.play_stats import PlayStats
from backend.services.queue_manager import QueueManager
from backend.services.settings import SETTINGS_SPEC, default_settings
from backend.services.song_history import SongHistory
from backend.services.storage import SongStorage

FRONTEND_PITCH_FIX = Path(__file__).resolve().parents[1] / "frontend" / "js" / "pitch-fix.js"


class _FakeProcessor:
    """這一支完全不碰流水線 —— 佇列只是那兩個值的容器。"""

    async def process_song(self, url, progress_callback=None):   # pragma: no cover
        raise AssertionError("修音的測試不該碰到流水線")


@pytest.fixture()
def manager(tmp_path):
    return QueueManager(
        _FakeProcessor(),
        SongStorage(tmp_path / "songs"),
        play_stats=PlayStats(tmp_path / "play_stats.json"),
        song_history=SongHistory(tmp_path / "song_history.json"),
    )


# --- 強度的夾限 ---

def test_known_strengths_pass_through():
    for key in PITCH_FIX_STRENGTH_CHOICES:
        assert coerce_strength(key) == key


def test_unknown_strength_falls_back_instead_of_raising():
    # "off" 是最可能出現的錯值：開關如果曾經做成四選一，設定檔裡會留著它。
    # 回預設值的失敗模式是「修音比預期的強一點」，使用者看得見也改得掉；
    # 丟例外的失敗模式是整個點歌台收到 500。
    assert coerce_strength("off") == DEFAULT_PITCH_FIX_STRENGTH
    assert coerce_strength("") == DEFAULT_PITCH_FIX_STRENGTH
    assert coerce_strength("MEDIUM") == DEFAULT_PITCH_FIX_STRENGTH   # 大小寫不當成同一個
    assert coerce_strength(None) == DEFAULT_PITCH_FIX_STRENGTH
    assert coerce_strength(3) == DEFAULT_PITCH_FIX_STRENGTH
    assert coerce_strength(["light"]) == DEFAULT_PITCH_FIX_STRENGTH


def test_strength_is_trimmed():
    # 設定檔是人手改得到的，前後空白不該讓修音退回預設
    assert coerce_strength("  light  ") == "light"


def test_default_strength_is_the_middle_one():
    """預設「中」—— 第一次按下去的人該聽到自己的歌聲，不是修音的味道。"""
    assert DEFAULT_PITCH_FIX_STRENGTH == "medium"
    assert PITCH_FIX_STRENGTHS[DEFAULT_PITCH_FIX_STRENGTH]["ratio"] < 1.0


# --- 共享狀態 ---

def test_pitch_fix_defaults_to_off(manager):
    """預設關著。修音會改變**使用者自己的聲音**，而「我的聲音聽起來不一樣了」
    在包廂裡是會被當成故障的那一類問題 —— 開著出廠的話，第一個覺得怪的人
    要先知道有這個功能、才查得到原因。"""
    state = manager.get_full_state()
    assert state["pitch_fix_enabled"] is False
    assert state["pitch_fix_strength"] == DEFAULT_PITCH_FIX_STRENGTH


def test_controls_update_enabled_and_strength(manager):
    manager._apply_controls({"pitch_fix_enabled": True, "pitch_fix_strength": "strong"})
    state = manager.get_full_state()
    assert state["pitch_fix_enabled"] is True
    assert state["pitch_fix_strength"] == "strong"


def test_bad_strength_does_not_break_the_switch(manager):
    manager._apply_controls({"pitch_fix_enabled": True, "pitch_fix_strength": "off"})
    state = manager.get_full_state()
    # 開關照收（使用者真的按了），強度退回預設 —— 不是整包被丟掉
    assert state["pitch_fix_enabled"] is True
    assert state["pitch_fix_strength"] == DEFAULT_PITCH_FIX_STRENGTH


def test_partial_update_keeps_the_other_field(manager):
    """開關與強度分開送：一支手機改強度不該把另一支剛打開的開關關掉。"""
    manager._apply_controls({"pitch_fix_enabled": True, "pitch_fix_strength": "light"})
    manager._apply_controls({"pitch_fix_strength": "strong"})
    assert manager.get_full_state()["pitch_fix_enabled"] is True
    manager._apply_controls({"pitch_fix_enabled": False})
    assert manager.get_full_state()["pitch_fix_strength"] == "strong"


# --- 設定頁的開機預設 ---

def test_settings_expose_both_fields():
    data = default_settings()
    assert data["default_pitch_fix_enabled"] is False
    assert data["default_pitch_fix_strength"] == DEFAULT_PITCH_FIX_STRENGTH
    assert SETTINGS_SPEC["default_pitch_fix_strength"]["choices"] == PITCH_FIX_STRENGTH_CHOICES


def test_settings_map_onto_the_shared_state():
    from backend.services.settings import CONTROL_DEFAULT_KEYS
    assert CONTROL_DEFAULT_KEYS["default_pitch_fix_enabled"] == "pitch_fix_enabled"
    assert CONTROL_DEFAULT_KEYS["default_pitch_fix_strength"] == "pitch_fix_strength"


def test_settings_reject_unknown_strength():
    from backend.services.settings import coerce_value
    # choice 欄位認不得就當這個欄位沒送（保留原值），而不是寫進一個壞值
    assert coerce_value("default_pitch_fix_strength", "off") is None
    assert coerce_value("default_pitch_fix_strength", "light") == "light"


# --- 前後端釘在一起 ---

def test_frontend_and_backend_share_the_same_strength_table():
    """`pitch-fix.js` 的 PITCH_FIX_STRENGTHS 與這裡的那張表必須一致。

    分岔的話，設定頁存得進去的強度會跟點歌台按得到的不一樣，
    而症狀是「開機時修音是輕的，推一下就跳成中的」—— 沒有人會想到
    是兩個檔案裡的兩張表。
    """
    source = FRONTEND_PITCH_FIX.read_text(encoding="utf-8")
    found = {}
    for key in PITCH_FIX_STRENGTH_CHOICES:
        m = re.search(
            rf'{key}:\s*\{{\s*\n\s*id:\s*"{key}",\s*label:\s*"[^"]+",\s*'
            rf"ratio:\s*([0-9.]+),\s*glideMs:\s*([0-9.]+),",
            source,
        )
        assert m, f"frontend/js/pitch-fix.js 找不到 {key} 的強度定義"
        found[key] = {"ratio": float(m.group(1)), "glide_ms": float(m.group(2))}

    for key, spec in PITCH_FIX_STRENGTHS.items():
        assert found[key]["ratio"] == float(spec["ratio"]), f"{key} 的修正比例兩邊不一致"
        assert found[key]["glide_ms"] == float(spec["glide_ms"]), f"{key} 的滑行時間常數兩邊不一致"


def test_frontend_and_backend_share_the_capture_range():
    """捕捉範圍與修正量上限也要一致。

    這兩個數字決定「什麼時候不修」—— 後端雖然不算音訊，但它是
    說明文件與設定頁要印出來的那一份，印的跟做的不一樣比沒印還糟。
    """
    source = FRONTEND_PITCH_FIX.read_text(encoding="utf-8")
    capture = re.search(r"const CAPTURE_CENTS = ([0-9.]+);", source)
    max_shift = re.search(r"const MAX_SHIFT_SEMITONES = ([0-9.]+);", source)
    assert capture and max_shift
    assert float(capture.group(1)) == CAPTURE_CENTS
    assert float(max_shift.group(1)) == MAX_SHIFT_SEMITONES


def test_capture_range_stays_inside_the_hard_limit():
    """捕捉範圍不能超過修正量的硬上限，否則上限那一道夾限會變成
    「唱得越偏、修得越不完整」的隱形折線 —— 而那條折線聽得出來、查不出來。"""
    assert CAPTURE_CENTS / 100.0 <= MAX_SHIFT_SEMITONES
