"""
三段音色等化器的範圍與夾限 (Tone EQ limits)

商用 KTV 擴大機面板上一定有的高音／中音／低音，麥克風一套、音樂一套。
實際的濾波器與補償增益在瀏覽器端（`frontend/js/tone-eq.js` +
`frontend/js/audio-effects.js`）—— 伺服器不處理音訊，它只負責
**把那六個數字當成共享狀態**，讓點歌台、手機與舞台看到的是同一組。

這個檔只放「合法範圍」與「怎麼夾」，而且刻意獨立成一支：

  * `queue_manager`（即時控制參數）與 `settings`（開機預設值）都要用同一組上限。
    各寫一份的話，設定頁存得進去的值會比即時控制夾得還寬，
    而症狀是「開機時音色正常，推一下滑桿就跳掉」。
  * 上限必須跟前端的 `EQ_TARGETS` 對得起來，所以要有一個 import 得到的來源
    讓測試把兩邊釘在一起（見 tests/test_tone_eq.py）。

為什麼兩套的範圍不一樣：
  * 麥克風 ±12 dB —— 麥克風的音色差異本來就大（領夾、動圈、手機內建），
    而且它是生料，調壞了下一首就調回來。
  * 音樂 ±8 dB —— 伴奏是已經混好、而且已經被自動音量平衡（EBU R128）
    對到 −14 LUFS 的成品。給它跟麥克風一樣的空間，等於允許一個人
    把那個「每一首都一樣大聲」的保證整個推翻掉。
"""
from typing import Any

MIC_EQ_LIMIT_DB = 12.0
MUSIC_EQ_LIMIT_DB = 8.0

# 共享狀態與設定頁共用的欄位名。照這張表長出夾限與對應，
# 加一段（真的有人要五段的話）不用同時改四個地方。
MIC_EQ_FIELDS = ("mic_eq_bass", "mic_eq_mid", "mic_eq_treble")
MUSIC_EQ_FIELDS = ("music_eq_bass", "music_eq_mid", "music_eq_treble")


def coerce_eq_db(value: Any, limit_db: float) -> float:
    """
    把一格的 dB 值夾進合法範圍。

    讀不出數字一律回 0.0（＝沒調）而不是丟例外：這個值會從手機的滑桿、
    舊版前端、還原回來的設定檔三個地方進來，其中任何一個送了一個字串
    都不該讓整個點歌台收到 500。回 0 的失敗模式是「等化器沒作用」，
    使用者看得見也改得掉；丟例外的失敗模式是整頁不動。
    """
    try:
        db = float(value)
    except (TypeError, ValueError):
        return 0.0
    if db != db or db in (float("inf"), float("-inf")):   # NaN / inf
        return 0.0
    limit = abs(float(limit_db))
    # 半格（0.5 dB）是人耳在包廂裡分得出來的最小差異。吸附在這裡做一次，
    # 前端與後端才不會因為浮點尾數而一直互相覆寫對方的值。
    db = round(db * 2) / 2
    return max(-limit, min(limit, db))
