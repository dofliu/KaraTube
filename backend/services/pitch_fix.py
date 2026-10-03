"""
智能修音的共享常數與夾限 (Pitch correction limits)

新一代點歌機與手機 K 歌 App 都有的那顆鍵（金嗓的「美聲」、全民K歌與唱吧的
「智能修音」、DAM 的ピッチ補正）：唱出來的音高差一點點，機器在送進喇叭之前
把它推回導唱音符上。

實際的判斷與移調都在瀏覽器端（`frontend/js/pitch-fix.js` +
`frontend/js/audio-effects.js` 的 `harmony-shifter` worklet）—— 伺服器不處理音訊，
它只負責**把開關與強度當成共享狀態**，讓點歌台、手機與舞台看到的是同一組。

這個檔只放「合法的值」與「怎麼夾」，而且刻意獨立成一支，理由跟 `tone_eq.py` 一樣：

  * `queue_manager`（即時控制參數）與 `settings`（開機預設值）要用同一組選項。
    各寫一份的話，設定頁存得進去的強度會跟即時控制認得的不一樣，
    而症狀是「開機時修音是輕的，推一下就跳成中的」。
  * 強度表必須跟前端的 `PITCH_FIX_STRENGTHS` 對得起來，所以要有一個
    import 得到的來源讓測試把兩邊釘在一起（見 tests/test_pitch_fix.py）。

**為什麼「關閉」不是一種強度**：開關與強度是兩個欄位。做成四選一（off/輕/中/強）
的話，設定檔裡存著 `off` 的機器下次有人打開修音時，打開的是一個沒有作用的功能 ——
而他會去調強度、發現三段都一樣沒聲音，然後結論是「這台的修音壞了」。
"""
from typing import Any

# 強度三段。這張表是**前端那張表的副本**，而且只複製伺服器真的需要知道的欄位
# （選項名、比例、滑行時間常數）。測試會比對兩邊的數字，改了一邊沒改另一邊會被擋下來。
#
# ratio    修正比例（1.0 = 完全修到導唱音符上）
# glide_ms 修正量滑過去的時間常數：這個數字同時決定「抖音活不活得下來」
#          —— 滑得比抖音（5~7Hz）慢，抖音就穿得過去。
PITCH_FIX_STRENGTHS = {
    "light": {"ratio": 0.35, "glide_ms": 150},
    "medium": {"ratio": 0.65, "glide_ms": 90},
    "strong": {"ratio": 1.0, "glide_ms": 45},
}

PITCH_FIX_STRENGTH_CHOICES = ("light", "medium", "strong")
DEFAULT_PITCH_FIX_STRENGTH = "medium"

# 捕捉範圍（cent）。超出這個距離就不是「唱不準」而是「沒在唱這一句」
# （唱錯行、整段低八度、在講話），硬修的結果是把聲音整個搬走一個半音以上。
CAPTURE_CENTS = 150.0

# 修正量的硬上限（半音）。超過兩個半音之後聲音就不像本人了。
MAX_SHIFT_SEMITONES = 2.0


def coerce_strength(value: Any) -> str:
    """
    把強度字串正規化。

    認不得一律回預設值而不是丟例外：這個值會從手機、舊版前端、還原回來的
    設定檔三個地方進來，其中任何一個送了 "off"（上一版的四選一）或一個空字串，
    都不該讓整個點歌台收到 500。回預設值的失敗模式是「修音比預期的強一點」，
    使用者看得見也改得掉。
    """
    if not isinstance(value, str):
        return DEFAULT_PITCH_FIX_STRENGTH
    key = value.strip()
    return key if key in PITCH_FIX_STRENGTHS else DEFAULT_PITCH_FIX_STRENGTH
