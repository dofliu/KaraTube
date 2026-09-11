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


def test_mic_agc_defaults_leave_headroom(settings):
    """麥克風自動增益預設開啟，目標電平必須留削峰餘裕。"""
    assert settings.get("mic_agc_enabled") is True
    target = settings.get("mic_agc_target_db")
    # 唱歌的動態比說話大，目標值太靠近 0 dBFS 副歌那一下就會削峰
    assert -30.0 <= target <= -6.0
    # 越界夾回範圍而不是報錯：這個滑桿在手機上很容易滑到底
    assert settings.update({"mic_agc_target_db": 0.0})["mic_agc_target_db"] == -6.0
    assert settings.update({"mic_agc_target_db": -99})["mic_agc_target_db"] == -30.0


def test_stage_options_are_milliseconds(settings):
    settings.update({"intro_card_seconds": 5.5, "settlement_enabled": False})
    options = settings.stage_options()
    assert options["intro_card_ms"] == 5500
    assert options["settlement_enabled"] is False


def test_harmony_defaults_off_and_never_louder_than_lead(settings):
    """和聲預設關著；打開後的音量也不該蓋過主唱。"""
    assert settings.get("default_harmony_enabled") is False
    assert settings.get("default_harmony_style") == "third"
    assert 0.0 < settings.get("default_harmony_level") < 1.0
    # 認不得的聲部不套用（choice 欄位回 None，呼叫端保留原值）
    assert settings.update({"default_harmony_style": "亂送的"})["default_harmony_style"] == "third"
    assert settings.update({"default_harmony_style": "duet"})["default_harmony_style"] == "duet"
    # 和聲的三個欄位都要能推進 QueueManager 的控制參數
    defaults = settings.control_defaults()
    assert defaults["harmony_style"] == "duet"
    assert "harmony_enabled" in defaults
    assert "harmony_level" in defaults


def test_duet_defaults_and_crosstalk_margin(settings):
    """對唱模式預設關著；串音判定門檻要能調，而且越界要夾回合法範圍。"""
    assert settings.get("default_duet_enabled") is False
    assert settings.get("duet_crosstalk_margin_db") == 9.0

    # 房間小、喇叭大聲時要調高；滑到底也不能變成 0 dB（那等於沒有判定）
    assert settings.update({"duet_crosstalk_margin_db": 15})["duet_crosstalk_margin_db"] == 15.0
    assert settings.update({"duet_crosstalk_margin_db": 0})["duet_crosstalk_margin_db"] == 3.0
    assert settings.update({"duet_crosstalk_margin_db": 999})["duet_crosstalk_margin_db"] == 24.0

    # 開機預設要推得進 QueueManager 的控制狀態
    assert "duet_enabled" in settings.control_defaults()


def test_ambient_background_defaults(settings):
    """情境背景預設是「只在沒有 MV 時出場」，亮度上限要壓在不搶字幕的位置。"""
    assert settings.get("ambient_bg_mode") == "auto"
    assert settings.get("ambient_bg_theme") == "auto"
    assert settings.get("ambient_bg_brightness") == 0.85

    # 認不得的模式／主題不套用（choice 欄位回 None，呼叫端保留原值）
    assert settings.update({"ambient_bg_mode": "隨便"})["ambient_bg_mode"] == "auto"
    assert settings.update({"ambient_bg_theme": "銀河"})["ambient_bg_theme"] == "auto"
    assert settings.update({"ambient_bg_mode": "off"})["ambient_bg_mode"] == "off"
    assert settings.update({"ambient_bg_theme": "ocean"})["ambient_bg_theme"] == "ocean"

    # 亮度不能滑到 0：那等於背景被關掉，但模式還顯示開著，畫面說的跟看到的不一樣
    assert settings.update({"ambient_bg_brightness": 0.0})["ambient_bg_brightness"] == 0.3
    assert settings.update({"ambient_bg_brightness": 9})["ambient_bg_brightness"] == 1.0


def test_ambient_settings_reach_the_stage(settings):
    """舞台端要拿得到情境背景的三個參數（改完不重開就要生效）。"""
    settings.update({"ambient_bg_mode": "always", "ambient_bg_theme": "neon",
                     "ambient_bg_brightness": 0.4})
    options = settings.stage_options()
    assert options["ambient_bg_mode"] == "always"
    assert options["ambient_bg_theme"] == "neon"
    assert options["ambient_bg_brightness"] == 0.4


def test_ambient_theme_choices_match_the_frontend():
    """
    設定頁的主題選項與 frontend/js/ambient-visuals.js 的 THEMES 必須一致。

    對不上的話設定頁會列出一個舞台端根本畫不出來的主題（選了之後背景不會變，
    而且完全沒有錯誤訊息）。註解會被忽略，測試不會 —— 所以直接讀那支 JS 來比。
    """
    import re
    from pathlib import Path

    from backend.services.settings import AMBIENT_THEME_CHOICES

    source = (Path(__file__).resolve().parents[1] /
              "frontend" / "js" / "ambient-visuals.js").read_text(encoding="utf-8")
    block = re.search(r"^const THEMES = \{(.*?)^\};", source, re.S | re.M)
    assert block, "找不到 ambient-visuals.js 的 THEMES 宣告"
    # 每個主題都以 `  id: {` 開頭（縮排兩格），巢狀欄位縮排更深，不會被撈到
    js_themes = set(re.findall(r"^  (\w+): \{", block.group(1), re.M))

    assert js_themes == set(AMBIENT_THEME_CHOICES) - {"auto"}
