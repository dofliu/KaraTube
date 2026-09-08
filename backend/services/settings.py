"""
系統設定 (System Settings)

商用點歌機都有一頁「機台設定」：開機預設的效果參數、快取上限、要用哪個模型。
沒有這一頁的話，包廂每次開機都要重調一輪殘響與回音，
而且使用者完全沒有辦法在不改程式碼的情況下換掉 Whisper / Demucs 模型。

設計原則：
  * 每個欄位都有型別與範圍，越界一律夾回合法值而不是丟例外 ——
    設定頁是給人用的，手機上滑錯一格不該讓伺服器回 500。
  * 未知欄位直接忽略，舊版前端送新版沒有的欄位也不會壞。
  * 存成 cache/settings.json，重開機沿用。檔案壞掉就退回預設值，不阻擋開機。
"""
import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("KaraTube.Settings")

WHISPER_MODEL_CHOICES = ("tiny", "base", "small", "medium", "large-v2", "large-v3")
DEMUCS_MODEL_CHOICES = ("htdemucs", "htdemucs_ft", "htdemucs_6s", "mdx_extra", "mdx_extra_q")
SING_MODE_CHOICES = ("solo", "party")

# 每個設定欄位：型別、預設值、範圍或選項。
# UI 也是照這張表長出來的，加一個欄位不用同時改三個地方。
SETTINGS_SPEC: Dict[str, Dict[str, Any]] = {
    # --- 開機預設的調音參數 ---
    "default_music_volume": {"type": "float", "default": 1.0, "min": 0.0, "max": 1.0},
    "default_mic_volume": {"type": "float", "default": 1.0, "min": 0.0, "max": 2.0},
    "default_vocal_volume": {"type": "float", "default": 0.0, "min": 0.0, "max": 1.0},
    "default_pitch_shift": {"type": "int", "default": 0, "min": -6, "max": 6},
    "default_mic_reverb": {"type": "float", "default": 0.25, "min": 0.0, "max": 1.0},
    "default_mic_echo": {"type": "float", "default": 0.15, "min": 0.0, "max": 1.0},
    "default_mic_echo_repeat": {"type": "float", "default": 0.40, "min": 0.0, "max": 1.0},
    "default_mic_echo_time_ms": {"type": "int", "default": 280, "min": 50, "max": 800},
    "default_mic_tone": {"type": "float", "default": 0.40, "min": 0.0, "max": 1.0},
    "default_sing_mode": {"type": "choice", "default": "solo", "choices": SING_MODE_CHOICES},
    "default_show_pitch": {"type": "bool", "default": True},

    # --- 自動音量平衡 (EBU R128) ---
    "loudness_normalize": {"type": "bool", "default": True},
    # -14 LUFS 是串流平台的通用目標，也是 KTV 包廂裡不刺耳又夠有力的音量
    "loudness_target_lufs": {"type": "float", "default": -14.0, "min": -30.0, "max": -5.0},

    # --- 導唱音量自動 ducking ---
    # 唱穩了導唱人聲自動退到背景，唱不下去它自己回來。深度 = 最多壓多少：
    # 0.6 表示最低降到原本的 40%，留一點在背景當安全網比整個消音好用。
    "guide_duck_enabled": {"type": "bool", "default": True},
    "guide_duck_depth": {"type": "float", "default": 0.6, "min": 0.0, "max": 0.95},

    # --- 麥克風自動增益 (AGC) ---
    # 換人唱不用重調麥克風音量：機器把每個人的收音電平拉到同一個目標。
    # -18 dBFS 是「大聲但離削峰還有餘裕」的工作點（唱歌的動態比說話大得多）。
    # 目標值調高（往 -6 靠）會更大聲也更容易在多人模式回授，往 -30 靠則偏保守。
    "mic_agc_enabled": {"type": "bool", "default": True},
    "mic_agc_target_db": {"type": "float", "default": -18.0, "min": -30.0, "max": -6.0},

    # --- 快取 ---
    # 0 = 不限制。超過上限時從最舊、且不在佇列裡的歌開始刪。
    "cache_limit_gb": {"type": "float", "default": 0.0, "min": 0.0, "max": 2000.0},
    "cache_auto_cleanup": {"type": "bool", "default": False},

    # --- 排程預處理 ---
    # 半夜把整張播放清單先跑成伴奏＋字幕，隔天客人點下去就是秒播。
    # 起訖時間相同代表「全天候」；跨午夜（23 → 6）也成立。
    "batch_enabled": {"type": "bool", "default": True},
    "batch_start_hour": {"type": "int", "default": 2, "min": 0, "max": 23},
    "batch_end_hour": {"type": "int", "default": 6, "min": 0, "max": 23},
    # 有人在唱歌時暫停批次處理。流水線吃滿 GPU，跟現場演唱搶資源會讓舞台掉幀，
    # 所以預設開著；伺服器夠力（或根本沒接舞台）才建議關掉。
    "batch_pause_while_singing": {"type": "bool", "default": True},

    # --- AI 模型（改完要重開伺服器才會生效）---
    "whisper_model": {"type": "choice", "default": "small", "choices": WHISPER_MODEL_CHOICES},
    "demucs_model": {"type": "choice", "default": "htdemucs", "choices": DEMUCS_MODEL_CHOICES},

    # --- 舞台演出 ---
    "intro_card_enabled": {"type": "bool", "default": True},
    "intro_card_seconds": {"type": "float", "default": 8.0, "min": 2.0, "max": 20.0},
    "settlement_enabled": {"type": "bool", "default": True},
    "settlement_seconds": {"type": "float", "default": 9.0, "min": 3.0, "max": 30.0},
}

# 設定裡的「開機預設值」對應到 QueueManager 的哪個控制欄位
CONTROL_DEFAULT_KEYS = {
    "default_music_volume": "music_volume",
    "default_mic_volume": "mic_volume",
    "default_vocal_volume": "vocal_volume",
    "default_pitch_shift": "pitch_shift",
    "default_mic_reverb": "mic_reverb",
    "default_mic_echo": "mic_echo",
    "default_mic_echo_repeat": "mic_echo_repeat",
    "default_mic_echo_time_ms": "mic_echo_time_ms",
    "default_mic_tone": "mic_tone",
    "default_sing_mode": "sing_mode",
    "default_show_pitch": "show_pitch",
}


def default_settings() -> Dict[str, Any]:
    return {key: spec["default"] for key, spec in SETTINGS_SPEC.items()}


def coerce_value(key: str, value: Any) -> Optional[Any]:
    """
    把單一欄位轉成合法值。無法解讀（文字塞進數字欄位之類）回傳 None，
    呼叫端就當這個欄位沒送過來，保留原值。
    """
    spec = SETTINGS_SPEC.get(key)
    if spec is None:
        return None
    kind = spec["type"]
    try:
        if kind == "bool":
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        if kind == "int":
            v = int(round(float(value)))
            return max(spec["min"], min(spec["max"], v))
        if kind == "float":
            v = float(value)
            if v != v:  # NaN
                return None
            return round(max(spec["min"], min(spec["max"], v)), 4)
        if kind == "choice":
            v = str(value)
            return v if v in spec["choices"] else None
    except (TypeError, ValueError):
        return None
    return None


class SystemSettings:
    def __init__(self, settings_file: Path):
        self.settings_file = Path(settings_file)
        self._lock = threading.Lock()
        self._data: Dict[str, Any] = default_settings()
        self._load()

    def _load(self):
        if not self.settings_file.exists():
            return
        try:
            raw = json.loads(self.settings_file.read_text(encoding="utf-8"))
            stored = raw.get("settings", {}) if isinstance(raw, dict) else {}
        except Exception as e:
            logger.warning(f"設定檔讀取失敗，改用預設值: {e}")
            return
        # 舊設定檔可能少欄位（升版）或多欄位（降版），都以 SPEC 為準
        for key, value in (stored or {}).items():
            coerced = coerce_value(key, value)
            if coerced is not None:
                self._data[key] = coerced

    def _save(self):
        try:
            self.settings_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "settings": self._data,
            }
            self.settings_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"設定檔寫入失敗: {e}")

    def all(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._data)

    def get(self, key: str, fallback: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, fallback)

    def update(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        """套用一批設定，回傳套用後的完整設定。認不得的欄位與值直接略過。"""
        if not isinstance(patch, dict):
            return self.all()
        with self._lock:
            changed = False
            for key, value in patch.items():
                coerced = coerce_value(key, value)
                if coerced is None or self._data.get(key) == coerced:
                    continue
                self._data[key] = coerced
                changed = True
            if changed:
                self._save()
            return dict(self._data)

    def reset(self) -> Dict[str, Any]:
        with self._lock:
            self._data = default_settings()
            self._save()
            return dict(self._data)

    # --- 衍生資訊 ---

    def control_defaults(self) -> Dict[str, Any]:
        """開機／「套用預設」時要推進 QueueManager 的控制參數。"""
        data = self.all()
        return {control_key: data[setting_key]
                for setting_key, control_key in CONTROL_DEFAULT_KEYS.items()
                if setting_key in data}

    def cache_limit_bytes(self) -> int:
        """快取上限（bytes）。0 代表不限制。"""
        gb = float(self.get("cache_limit_gb", 0.0) or 0.0)
        return int(gb * 1024 ** 3)

    def stage_options(self) -> Dict[str, Any]:
        """舞台端要的演出設定（片頭卡、結算畫面）。"""
        data = self.all()
        return {
            "intro_card_enabled": data["intro_card_enabled"],
            "intro_card_ms": int(data["intro_card_seconds"] * 1000),
            "settlement_enabled": data["settlement_enabled"],
            "settlement_ms": int(data["settlement_seconds"] * 1000),
        }
