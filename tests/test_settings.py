"""系統設定服務的單元測試。

重點在「設定頁是給人用的」：手機上滑錯一格、舊版前端送了不存在的欄位、
設定檔被編輯壞掉，都不該讓伺服器掛掉或讓機台變成不能唱歌的狀態。
"""
import json

import pytest

from backend.services.settings import (
    SETTINGS_SPEC,
    SystemSettings,
    coerce_value,
    default_settings,
)


@pytest.fixture()
def settings(tmp_path):
    return SystemSettings(tmp_path / "settings.json")


def test_defaults_cover_every_spec_field():
    defaults = default_settings()
    assert set(defaults) == set(SETTINGS_SPEC)
    assert defaults["loudness_target_lufs"] == -14.0
    assert defaults["whisper_model"] == "small"
    assert defaults["cache_limit_gb"] == 0.0  # 0 = 不限制


def test_update_and_persist(tmp_path):
    path = tmp_path / "settings.json"
    first = SystemSettings(path)
    first.update({"loudness_target_lufs": -18.0, "default_mic_reverb": 0.5})

    reloaded = SystemSettings(path)
    assert reloaded.get("loudness_target_lufs") == -18.0
    assert reloaded.get("default_mic_reverb") == 0.5
    # 沒動到的欄位保持預設
    assert reloaded.get("default_mic_echo") == SETTINGS_SPEC["default_mic_echo"]["default"]


def test_values_are_clamped_not_rejected(settings):
    """越界一律夾回合法值 —— 設定頁不該因為滑桿滑過頭就回錯誤。"""
    result = settings.update({"default_mic_reverb": 5.0, "default_pitch_shift": -99,
                              "loudness_target_lufs": 0.0})
    assert result["default_mic_reverb"] == 1.0
    assert result["default_pitch_shift"] == -6
    assert result["loudness_target_lufs"] == -5.0


def test_unknown_keys_and_bad_values_are_ignored(settings):
    before = settings.all()
    result = settings.update({
        "not_a_setting": 123,               # 舊版/新版前端的多餘欄位
        "default_mic_echo": "很大聲",        # 文字塞進數字欄位
        "whisper_model": "gpt-9",           # 不在選項裡的模型
        "default_music_volume": float("nan"),
    })
    assert result["default_mic_echo"] == before["default_mic_echo"]
    assert result["whisper_model"] == before["whisper_model"]
    assert result["default_music_volume"] == before["default_music_volume"]
    assert "not_a_setting" not in result


def test_bool_accepts_checkbox_strings():
    assert coerce_value("loudness_normalize", "false") is False
    assert coerce_value("loudness_normalize", "on") is True
    assert coerce_value("loudness_normalize", 0) is False


def test_int_field_rounds_floats():
    assert coerce_value("default_mic_echo_time_ms", 280.6) == 281
    assert coerce_value("default_pitch_shift", 2.4) == 2


def test_reset_restores_factory_values(settings):
    settings.update({"default_mic_reverb": 0.9, "cache_limit_gb": 50})
    assert settings.reset() == default_settings()
    assert settings.get("default_mic_reverb") == 0.25


def test_corrupt_settings_file_falls_back_to_defaults(tmp_path):
    """設定檔壞掉時要照樣開得起來，不然整台機器就唱不了歌了。"""
    path = tmp_path / "settings.json"
    path.write_text("{ 這不是 JSON", encoding="utf-8")
    assert SystemSettings(path).all() == default_settings()


def test_settings_file_from_older_version_keeps_known_fields(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"settings": {"default_mic_echo": 0.8, "removed_field": 1}}),
                    encoding="utf-8")
    loaded = SystemSettings(path)
    assert loaded.get("default_mic_echo") == 0.8
    assert "removed_field" not in loaded.all()


def test_control_defaults_map_to_queue_manager_fields(settings):
    settings.update({"default_mic_reverb": 0.6, "default_sing_mode": "party"})
    defaults = settings.control_defaults()
    assert defaults["mic_reverb"] == 0.6
    assert defaults["sing_mode"] == "party"
    assert "default_mic_reverb" not in defaults  # 已改寫成控制欄位名稱


def test_cache_limit_bytes(settings):
    assert settings.cache_limit_bytes() == 0  # 預設不限制
    settings.update({"cache_limit_gb": 2})
    assert settings.cache_limit_bytes() == 2 * 1024 ** 3


def test_guide_duck_defaults_on_with_a_safety_net(settings):
    """導唱自動淡出預設開啟，但不會把導唱整個消音（永遠留一點在背景）。"""
    assert settings.get("guide_duck_enabled") is True
    depth = settings.get("guide_duck_depth")
    assert 0.0 < depth < 1.0
    # 深度上限刻意不到 1.0：忘詞時導唱完全消失就不是安全網了
    assert settings.update({"guide_duck_depth": 1.5})["guide_duck_depth"] == 0.95
    assert settings.update({"guide_duck_depth": -1})["guide_duck_depth"] == 0.0


def test_stage_options_are_milliseconds(settings):
    settings.update({"intro_card_seconds": 5.5, "settlement_enabled": False})
    options = settings.stage_options()
    assert options["intro_card_ms"] == 5500
    assert options["settlement_enabled"] is False
