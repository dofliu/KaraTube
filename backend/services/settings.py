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

# 待唱上限的天花板只有一份（設定頁與控制參數共用）。song_quota 是純邏輯、
# 不反過來 import 設定，所以這個方向不會有循環。
from backend.services.song_quota import MAX_PENDING_LIMIT
# 包廂計時的上下限與「時間到怎麼辦」的選項同理：規則寫在 room_timer，
# 設定頁只是把它們列出來讓人調。
from backend.services.room_timer import (DEFAULT_EXPIRE_ACTION, DEFAULT_EXTEND_MINUTES,
                                         DEFAULT_LAST_CALL_MINUTES, DEFAULT_SESSION_MINUTES,
                                         DEFAULT_WARN_MINUTES, EXPIRE_ACTION_CHOICES,
                                         MAX_EXTEND_MINUTES, MAX_SESSION_MINUTES,
                                         MIN_EXTEND_MINUTES, MIN_SESSION_MINUTES)
# 舞台訊息（跑馬燈）的秒數與存活時間上下限同理：規則寫在 marquee，
# 設定頁只是把它們列出來讓人調。
from backend.services.marquee import (DEFAULT_SHOW_SECONDS, DEFAULT_TTL_MINUTES,
                                      MAX_SHOW_SECONDS, MAX_TTL_MINUTES,
                                      MIN_SHOW_SECONDS, MIN_TTL_MINUTES)
# 自動接歌的挑歌來源與時間上下限同理：規則寫在 autofill，設定頁只是列出來。
from backend.services.autofill import (DEFAULT_IDLE_SECONDS, DEFAULT_SOURCE,
                                       DEFAULT_STOP_AFTER, MAX_IDLE_SECONDS, MAX_STOP_AFTER,
                                       MIN_IDLE_SECONDS, MIN_STOP_AFTER, SOURCE_CHOICES)

logger = logging.getLogger("KaraTube.Settings")

WHISPER_MODEL_CHOICES = ("tiny", "base", "small", "medium", "large-v2", "large-v3")
DEMUCS_MODEL_CHOICES = ("htdemucs", "htdemucs_ft", "htdemucs_6s", "mdx_extra", "mdx_extra_q")
SING_MODE_CHOICES = ("solo", "party")
# 和聲風格。都是「音階上的度數」而不是固定半音數（octave 例外，八度就是 12 個半音）。
# 實際移調量由舞台端依這首歌的調性算（frontend/js/harmony-planner.js）。
HARMONY_STYLE_CHOICES = ("third", "low_third", "fifth", "octave", "duet")

# 情境背景（沒抓到 MV 時的動態視覺）。
#   auto   只有在這首歌沒有可用的 MV 時才出場（抓不到影片，或抓到的其實是一張靜態圖）
#   always 一律用情境背景（有些包廂覺得 MV 會讓人分心，或版權畫面不想放）
#   off    關掉（沒有 MV 就是黑畫面，等於這個功能沒加之前的行為）
AMBIENT_BG_MODE_CHOICES = ("auto", "always", "off")
# 主題 id 必須與 frontend/js/ambient-visuals.js 的 THEMES 一致
# （tests/test_settings.py 有一條測試把兩邊釘在一起）。
# "auto" 不是主題，是「依歌曲穩定挑一個」—— 同一首歌永遠是同一個背景。
AMBIENT_THEME_CHOICES = ("auto", "aurora", "starfield", "neon", "ocean", "ember")

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
    # 公平輪唱（排麥輪序）。預設關著：這是一條會改變「我點的歌排在哪」的規則，
    # 開著而沒人講好的話，使用者只會覺得佇列自己亂跳。包廂講好了再開。
    "default_rotation_enabled": {"type": "bool", "default": False},
    # 每人待唱上限（點歌額度）。0 = 不限，也是預設值 —— 跟輪唱同一個理由：
    # 這是一條會改變「我點不點得了歌」的規則，包廂要先講好才開，
    # 預設開著的話第一個被擋下來的人只會覺得點歌壞了。
    # 上限算的是「同時有幾首在等」而不是「今晚總共唱幾首」，所以排滿了只要等
    # 其中一首唱完就又能點（見 backend/services/song_quota.py 決定一）。
    "default_pending_limit": {"type": "int", "default": 0,
                              "min": 0, "max": MAX_PENDING_LIMIT},

    # --- 和聲（雙聲部）---
    # 預設關著：和聲是「加了才有」的效果，而且它跟主唱一樣要外放才聽得到，
    # 開機就打開的話單人模式的使用者只會覺得「這顆按鈕沒反應」。
    # 音量 0.5 是和聲聽得清楚但仍明顯低於主唱的位置（上限 0.85，永遠不該蓋過主唱）。
    "default_harmony_enabled": {"type": "bool", "default": False},
    "default_harmony_style": {"type": "choice", "default": "third",
                              "choices": HARMONY_STYLE_CHOICES},
    "default_harmony_level": {"type": "float", "default": 0.5, "min": 0.0, "max": 1.0},

    # --- 對唱模式（兩支麥克風分別評分）---
    # 預設關著：第二支麥克風不是每台機器都有，開機就打開只會讓舞台端
    # 一直跳「第二支麥克風開不起來」。
    "default_duet_enabled": {"type": "bool", "default": False},
    # 串音判定門檻（dB）：兩支麥克風的電平差超過這個值，就只算大聲的那一位。
    # 房間越小、喇叭越大聲，串音越嚴重，門檻要調高；
    # 但調太高會把「唱得比較收的那一位」也一起判成串音（他就一直沒分數）。
    # 9 dB 是近距離收音的合理起點，現場照結算畫面的串音比例微調。
    "duet_crosstalk_margin_db": {"type": "float", "default": 9.0, "min": 3.0, "max": 24.0},

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

    # --- 情境背景 ---
    # 沒有 MV 的歌不該是一片黑（看起來像這首歌壞了）。
    # 亮度上限：背景再好看也不能跟字幕搶。下限刻意設 0.3 而不是 0 ——
    # 滑到 0 等於背景被關掉，但模式還顯示開著，畫面說的跟看到的就對不上了。
    "ambient_bg_mode": {"type": "choice", "default": "auto",
                        "choices": AMBIENT_BG_MODE_CHOICES},
    "ambient_bg_theme": {"type": "choice", "default": "auto",
                         "choices": AMBIENT_THEME_CHOICES},
    "ambient_bg_brightness": {"type": "float", "default": 0.85, "min": 0.3, "max": 1.0},

    # --- 錄唱回放 ---
    # 預設關著，而且是這份設定裡唯一「不開就不會錄」的功能：錄音錄到的是
    # 包廂裡所有人的聲音，預設打開等於替使用者決定要錄他們講的話。
    # 上限是磁碟保險絲：錄音跟歌曲快取共用同一顆磁碟，沒有上限的話
    # 最後失敗的會是「歌曲下載不下來」，沒有人會聯想到是錄音吃光的。
    "recording_enabled": {"type": "bool", "default": False},
    "recording_max_count": {"type": "int", "default": 50, "min": 1, "max": 500},
    "recording_max_mb": {"type": "int", "default": 512, "min": 16, "max": 20000},
    # 唱不到這麼久就不留。前奏放一半被切歌、麥克風擺著沒人唱的那種錄音
    # 一樣佔配額，而且會把真正想找的那一次擠掉。
    "recording_min_sing_seconds": {"type": "float", "default": 10.0, "min": 0.0, "max": 120.0},
    # --- 錄音轉 MP3 ---
    # 錄音是瀏覽器錄的 webm/mp4，車機與舊播放器不一定打得開。開著這個選項的話
    # 清單與分享頁會多一顆「MP3」按鈕，按下去才轉（不是錄完就轉 —— 那是拿
    # 正在放歌的 CPU 去換沒人要的檔案），轉好的留著當快取。
    # 192 kbps 是「聽不出差別」與「檔案不要太大」的交界；車機上再高也聽不出來。
    "recording_mp3_enabled": {"type": "bool", "default": True},
    "recording_mp3_bitrate": {"type": "int", "default": 192, "min": 96, "max": 320},

    # --- 整晚打包下載 ---
    # 一場 = 連續唱的那一段。相隔超過這個小時數就算換了一場 ——
    # 刻意不照日曆日期切：包廂的一場是「九點唱到凌晨兩點半」，照日期切會把
    # 它剖成兩半，而且唱到最嗨的後半會被標成「隔天」。
    # 6 小時的理由：一場再久也就五、六個小時，而下一桌跟上一桌之間一定有清場。
    # 公平輪唱的「今晚唱了幾首」也用同一個欄位判斷換場：系統裡「一場」只能有
    # 一個定義，兩個各自可調的話會出現「打包算同一場、輪序算換了一場」的矛盾。
    "recording_session_gap_hours": {"type": "int", "default": 6, "min": 1, "max": 24},

    # --- 錄音分享 ---
    # 分享連結不需要登入就打得開（掃 QR 的人不會先登入），所以時效是唯一的
    # 安全邊界：預設 24 小時，上限 30 天，沒有「永不過期」這個選項 ——
    # 錄到的是包廂裡所有人的聲音，一個永遠有效的公開連結事後收不回來。
    "recording_share_enabled": {"type": "bool", "default": True},
    "recording_share_ttl_hours": {"type": "int", "default": 24, "min": 1, "max": 720},
    # 下載幾次就失效。0 = 不限（時效還是在）。只算明確的下載，不算播放。
    "recording_share_max_downloads": {"type": "int", "default": 0, "min": 0, "max": 999},

    # --- 包廂計時（歡唱時間）---
    # 預設關著：家裡唱歌沒有人在算時間，開著只會多一條沒有人要的倒數。
    # 開了之後真正重要的是「時間到怎麼辦」—— 預設是讓正在唱的那一首唱完再停，
    # 而**沒有**「立刻停掉這一首」這個選項（見 backend/services/room_timer.py 決定一）。
    "room_timer_enabled": {"type": "bool", "default": False},
    "room_timer_minutes": {"type": "int", "default": DEFAULT_SESSION_MINUTES,
                           "min": MIN_SESSION_MINUTES, "max": MAX_SESSION_MINUTES},
    # 第一首歌開始播的時候自動開錶。不自動開的話，最常見的結局是三小時後
    # 才有人想起來沒按開始 —— 那時候這個功能等於沒開。
    "room_timer_autostart": {"type": "bool", "default": True},
    # 提醒門檻（分鐘，0 = 不提醒那一次）。兩次就夠：一次讓人來得及決定要不要
    # 續時，一次是最後召集。再多就是雜訊，而雜訊會把真正重要的那一次一起淹掉。
    "room_timer_warn_minutes": {"type": "int", "default": DEFAULT_WARN_MINUTES,
                                "min": 0, "max": 120},
    "room_timer_last_call_minutes": {"type": "int", "default": DEFAULT_LAST_CALL_MINUTES,
                                     "min": 0, "max": 60},
    "room_timer_expire_action": {"type": "choice", "default": DEFAULT_EXPIRE_ACTION,
                                 "choices": EXPIRE_ACTION_CHOICES},
    # 「續時」按一下加多久
    "room_timer_extend_minutes": {"type": "int", "default": DEFAULT_EXTEND_MINUTES,
                                  "min": MIN_EXTEND_MINUTES, "max": MAX_EXTEND_MINUTES},

    # --- 舞台訊息（跑馬燈）---
    # 櫃檯把字打到包廂螢幕上：「您的餐點到了」、生日祝福。預設開著 ——
    # 它不會自己跳出來（沒有人送訊息就什麼都不會發生），而關著的話，
    # 需要用它的那一刻（餐點送到門口）沒有人會想到要先去設定頁打開。
    "marquee_enabled": {"type": "bool", "default": True},
    # 每一則在螢幕上停留幾秒（多則訊息輪播的一輪）。
    "marquee_seconds": {"type": "float", "default": DEFAULT_SHOW_SECONDS,
                        "min": MIN_SHOW_SECONDS, "max": MAX_SHOW_SECONDS},
    # 多久之後自己消失。刻意沒有「不會消失」這個值：「您的餐點到了」在四十分鐘
    # 之後才在螢幕上，是比沒有訊息更糟的錯誤資訊。要留久一點的用「📌 釘住」送，
    # 而釘住的也有上限（見 marquee.py PINNED_MAX_HOURS）。
    "marquee_ttl_minutes": {"type": "int", "default": DEFAULT_TTL_MINUTES,
                            "min": MIN_TTL_MINUTES, "max": MAX_TTL_MINUTES},
    # 沒有在播歌時用置中的大字卡（看一眼就知道）。正在播歌時一律降級成上緣
    # 那一條，不受這個選項影響 —— 沒有任何訊息重要到可以蓋住正在唱的那個人。
    "marquee_card_when_idle": {"type": "bool", "default": True},

    # --- 自動接歌（沒有人點歌時，機器自己接一首）---
    # 預設關著：這是一條會讓機器自己發出聲音的規則，包廂要先講好才開
    # （見 backend/services/autofill.py 決定八）。
    # 開了之後真正重要的是那兩個數字：空了多久才接（別跟正在找歌的人搶），
    # 以及連續接幾首沒人接手就停（沒有人點歌通常代表沒有人在了）。
    "autofill_enabled": {"type": "bool", "default": False},
    "autofill_source": {"type": "choice", "default": DEFAULT_SOURCE,
                        "choices": SOURCE_CHOICES},
    "autofill_idle_seconds": {"type": "int", "default": DEFAULT_IDLE_SECONDS,
                              "min": MIN_IDLE_SECONDS, "max": MAX_IDLE_SECONDS},
    "autofill_stop_after": {"type": "int", "default": DEFAULT_STOP_AFTER,
                            "min": MIN_STOP_AFTER, "max": MAX_STOP_AFTER},

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
    "default_harmony_enabled": "harmony_enabled",
    "default_harmony_style": "harmony_style",
    "default_harmony_level": "harmony_level",
    "default_duet_enabled": "duet_enabled",
    "default_sing_mode": "sing_mode",
    "default_show_pitch": "show_pitch",
    "default_rotation_enabled": "rotation_enabled",
    "default_pending_limit": "pending_limit",
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

    def recording_limit_bytes(self) -> int:
        """錄音配額（bytes）。設定頁給的是 MB，這裡換算成 bytes。"""
        mb = int(self.get("recording_max_mb", 512) or 0)
        return mb * 1024 * 1024

    def room_policy(self) -> Dict[str, Any]:
        """
        包廂計時的規則（QueueManager 與 API 共用）。

        把「幾分鐘」在這裡換算成秒，兩邊才不會各自乘一次 60 而差一個數量級 ——
        這種錯在計時功能上特別難發現：三小時變成三分鐘看得出來，
        但十分鐘的提醒變成十秒鐘的提醒只會被當成「那個提醒好像壞了」。
        """
        data = self.all()
        return {
            "enabled": bool(data["room_timer_enabled"]),
            "minutes": int(data["room_timer_minutes"]),
            "autostart": bool(data["room_timer_autostart"]),
            "warn_minutes": int(data["room_timer_warn_minutes"]),
            "last_call_minutes": int(data["room_timer_last_call_minutes"]),
            "expire_action": data["room_timer_expire_action"],
            "extend_minutes": int(data["room_timer_extend_minutes"]),
        }

    def marquee_policy(self) -> Dict[str, Any]:
        """
        舞台訊息的規則（API 與舞台端共用）。

        `card_when_idle` 只影響「沒有在播歌」時的樣子。播歌中一律是上緣那一條，
        設定頁**沒有**可以改掉這件事的選項 —— 沒有任何訊息重要到可以蓋住
        正在唱的那個人（見 backend/services/marquee.py 決定二）。
        """
        data = self.all()
        return {
            "enabled": bool(data["marquee_enabled"]),
            "seconds": float(data["marquee_seconds"]),
            "ttl_minutes": int(data["marquee_ttl_minutes"]),
            "card_when_idle": bool(data["marquee_card_when_idle"]),
        }

    def autofill_policy(self) -> Dict[str, Any]:
        """
        自動接歌的規則（QueueManager 與 API 共用）。

        四個欄位一起送，是因為畫面上那句話需要它們全部：「20 秒沒人點歌就
        自己接一首、最多連著接 3 首」是一句看得懂的話，少講任何一半，
        使用者都無法預期機器下一步會做什麼 —— 而一台會自己出聲的機器，
        「可預期」是它唯一能讓人放心的地方。
        """
        data = self.all()
        return {
            "enabled": bool(data["autofill_enabled"]),
            "source": data["autofill_source"],
            "idle_seconds": int(data["autofill_idle_seconds"]),
            "stop_after": int(data["autofill_stop_after"]),
        }

    def stage_options(self) -> Dict[str, Any]:
        """舞台端要的演出設定（片頭卡、結算畫面）。"""
        data = self.all()
        return {
            "intro_card_enabled": data["intro_card_enabled"],
            "intro_card_ms": int(data["intro_card_seconds"] * 1000),
            "settlement_enabled": data["settlement_enabled"],
            "settlement_ms": int(data["settlement_seconds"] * 1000),
            "ambient_bg_mode": data["ambient_bg_mode"],
            "ambient_bg_theme": data["ambient_bg_theme"],
            "ambient_bg_brightness": data["ambient_bg_brightness"],
            "recording_enabled": data["recording_enabled"],
            # 舞台端算的是毫秒（錄音長度用 performance.now() 量），
            # 在這裡換算好，兩邊才不會各自乘一次 1000 而差一個數量級。
            "recording_min_sing_ms": int(data["recording_min_sing_seconds"] * 1000),
        }
