"""
每首歌的字幕偏移 (Per-song Lyric Offset)

「字幕對不上」有兩種完全不同的原因，而 v1.21 為止它們被塞在**同一個數字**裡
（`frontend/js/player.js` 的 `lyricOffsetMs`）：

1. **這台裝置的延遲** —— 藍牙喇叭、HDMI 電視、外接混音器。對**每一首歌都一樣**，
   而且瀏覽器量得到一部分（`AudioContext.outputLatency`），人手動補的只是殘差。
   它屬於「這台機器怎麼接的」，所以留在那台裝置上（localStorage + 音訊設定面板）。
2. **這首歌的 LRC 偏差** —— 抓到的歌詞是別的版本、片頭被剪過。只有**這一首**歌
   對不上，沒有任何量測值可用，只有人耳知道。它屬於**那首歌**。

混在一起的後果很具體：為了某一首爛 LRC 調了 +300ms，下一首歌就反過來多錯
300ms，而且那個錯誤無聲、無界、跟著機器走到明天晚上 —— 下一個唱的人不知道
前一個人按過方向鍵，畫面上也沒有任何東西說「上一首的修正還套著」。

這個模組負責第 2 種：**把每一首歌的偏移綁在 song_id 上、存在伺服器**。

幾個刻意的決定：

* **存在 `cache/lyric_offsets.json`，不放進歌的資料夾。** 「重新處理」會
  `shutil.rmtree` 整個 `cache/songs/<id>/`（main.py 的 reprocess），快取自動清理
  也會無聲刪掉整個資料夾。人花時間用耳朵校出來的數字不該被流水線洗掉 ——
  這跟歌號（`song_numbers.py`）放在 cache/ 的理由是同一個。

* **綁 `song_id`（YouTube 影片 ID，重跑不會變）。** 所以刪掉快取之後再點同一首，
  校正過的偏移還在；同一首歌在歌單裡出現兩次也共用同一個值。曲庫會愈唱愈準。

* **只影響字幕，不影響評分。** 這個模組只是存一個數字，但它的用途必須寫在這裡：
  `pitch.json` 的導唱音符是從 `vocals.mp3` 抽的，活在**音訊時間軸**上；
  `lyrics.json` 才活在 LRC 的時間軸上。把 LRC 偏差也減進評分時間，等於把計分
  視窗整段搬離真實人聲 —— 那首歌的音準率與 Combo 會無聲下降，而且畫面上
  看不出任何異狀。舞台端因此分成 `scoreTime`（只扣裝置延遲）與
  `lyricTime`（再扣這首歌的偏移），見 `frontend/js/player.js` 的 renderLoop。

* **壞檔就備份成 `.bad` 再從空的開始。** 跟歌號簿不同：歌號壞掉寧可整個功能
  停掉（重發等於把大家記住的號碼改成別首歌），偏移壞掉最糟只是要重調一次，
  硬撐著不寫反而讓使用者連「重新調一次」都做不到。但原檔要留著（那是人工
  校正的成果），所以先複製成 `lyric_offsets.json.bad` 再重新開始。

* **`rebase()` 是「升級成本機基準」那個動作的另一半。** 把某一首的 +200ms 升級
  成整台機器的延遲補償時，如果不把已經校正過的每一首都減掉 200ms，它們會
  同時偏掉 200ms —— 一批「本來好好的、今天突然歪了」的歌是最難查的故障。
  所以升級基準必須是一次交易：裝置 +X、所有單曲 −X，歸零的直接刪掉。
"""
import json
import logging
import os
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("KaraTube.LyricOffsets")

# 偏移的上下限（毫秒）。跟 queue_manager._apply_controls 的裝置延遲同一個範圍：
# 兩秒已經是「整段副歌都對不上」的等級，再大多半是抓到了完全不同的一首歌，
# 那該按「重算歌詞」而不是繼續拉滑桿。
MAX_OFFSET_MS = 2000

# 檔案格式版本。改了結構就 +1。
OFFSETS_VERSION = 1


def clamp_offset_ms(value: Any) -> int:
    """把任何輸入夾成合法的偏移毫秒數。認不得的一律當 0（不是丟例外）。"""
    if isinstance(value, bool):  # bool 是 int 的子類，先擋掉
        return 0
    try:
        ms = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    return max(-MAX_OFFSET_MS, min(MAX_OFFSET_MS, ms))


class LyricOffsets:
    """
    song_id -> 字幕偏移（毫秒，正值＝字幕延後）。

    執行緒安全（FastAPI 的 threadpool 與心跳迴圈都可能碰到它）。
    值是 0 的歌**不留紀錄** —— 「沒有校正過」與「校正結果剛好是 0」對使用者
    是同一件事，留著只會讓檔案愈長愈大，也讓「校正過幾首」這個數字失真。
    """

    def __init__(self, offsets_file: Path):
        self.offsets_file = Path(offsets_file)
        self._lock = threading.Lock()
        # song_id -> {"offset_ms": int, "updated_at": str}
        self._records: Dict[str, Dict[str, Any]] = {}
        self._load()

    # --- 持久化 ---

    def _load(self) -> None:
        if not self.offsets_file.exists():
            return
        try:
            raw = json.loads(self.offsets_file.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("lyric_offsets.json 不是物件")
        except Exception as e:
            # 壞檔：原檔備份成 .bad（那是人工校正的成果，不能就這樣蓋掉），
            # 記憶體從空的開始 —— 最糟的後果只是要重調一次，
            # 而硬撐著不寫會讓使用者連「重新調一次」都做不到。
            logger.error(f"字幕偏移讀取失敗，改從空的開始（原檔備份為 .bad）: {e}")
            try:
                shutil.copy2(self.offsets_file, self.offsets_file.with_suffix(".json.bad"))
            except Exception as copy_error:
                logger.warning(f"字幕偏移壞檔備份失敗: {copy_error}")
            self._records = {}
            return

        songs = raw.get("songs")
        if isinstance(songs, dict):
            for song_id, rec in songs.items():
                if not isinstance(rec, dict):
                    continue
                ms = clamp_offset_ms(rec.get("offset_ms"))
                if ms == 0:
                    continue
                self._records[str(song_id)] = {
                    "offset_ms": ms,
                    "updated_at": str(rec.get("updated_at") or ""),
                }

    def _save(self) -> None:
        """整份寫回。先寫暫存檔再 rename，中途斷電不會留下半份校正結果。"""
        try:
            self.offsets_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": OFFSETS_VERSION,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "songs": self._records,
            }
            tmp = self.offsets_file.with_suffix(self.offsets_file.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, self.offsets_file)
        except Exception as e:
            logger.warning(f"字幕偏移寫入失敗: {e}")

    # --- 讀 ---

    def get(self, song_id: Any) -> int:
        """這首歌的偏移（毫秒）。沒校正過就是 0 —— 這是最常被呼叫的一支。"""
        key = str(song_id or "")
        if not key:
            return 0
        with self._lock:
            rec = self._records.get(key)
            return int(rec["offset_ms"]) if rec else 0

    def entry(self, song_id: Any) -> Optional[Dict[str, Any]]:
        """完整紀錄（含校正時間）。沒有就 None，讓呼叫端分得出「沒調過」。"""
        key = str(song_id or "")
        with self._lock:
            rec = self._records.get(key)
            return {"song_id": key, **rec} if rec else None

    def all(self) -> Dict[str, int]:
        """song_id -> 偏移。給快取管理頁一次拿完，不必每一列各問一次。"""
        with self._lock:
            return {song_id: int(rec["offset_ms"]) for song_id, rec in self._records.items()}

    def count(self) -> int:
        """校正過幾首。升級基準的確認框要講得出「會同時調整 47 首」。"""
        with self._lock:
            return len(self._records)

    # --- 寫 ---

    def set(self, song_id: Any, offset_ms: Any) -> int:
        """
        設定這首歌的偏移，回傳夾限之後真正生效的值。

        設成 0 等於清掉那筆紀錄（見類別說明）。
        """
        key = str(song_id or "")
        if not key:
            return 0
        ms = clamp_offset_ms(offset_ms)
        with self._lock:
            if ms == 0:
                self._records.pop(key, None)
            else:
                self._records[key] = {
                    "offset_ms": ms,
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                }
            self._save()
        return ms

    def clear(self, song_id: Any) -> bool:
        """清掉這首歌的偏移（回到「沒有校正過」）。有清到才回 True。"""
        key = str(song_id or "")
        with self._lock:
            existed = self._records.pop(key, None) is not None
            if existed:
                self._save()
        return existed

    def rebase(self, delta_ms: Any) -> Dict[str, Any]:
        """
        所有已校正的歌各減掉 `delta_ms`，回傳 `{"changed": n, "cleared": m}`。

        這是「把這首歌的偏移升級成本機基準」的另一半：裝置延遲 +X 之後，
        每一首已經校正過的歌都會多出 X 的誤差，不一起減掉的話，會出現一批
        「昨天好好的、今天突然歪了」的歌 —— 那是最難查的故障。
        減完變成 0 的直接刪掉（那首歌的偏差正好就是裝置延遲）。
        """
        delta = clamp_offset_ms(delta_ms)
        if delta == 0:
            return {"changed": 0, "cleared": 0, "delta_ms": 0}
        changed, cleared = 0, 0
        stamp = datetime.now().isoformat(timespec="seconds")
        with self._lock:
            for song_id in list(self._records):
                new_ms = clamp_offset_ms(self._records[song_id]["offset_ms"] - delta)
                if new_ms == 0:
                    self._records.pop(song_id)
                    cleared += 1
                else:
                    self._records[song_id] = {"offset_ms": new_ms, "updated_at": stamp}
                    changed += 1
            self._save()
        return {"changed": changed, "cleared": cleared, "delta_ms": delta}
