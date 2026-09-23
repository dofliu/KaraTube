"""
每首歌的字幕校正 (Per-song Lyric Calibration：偏移 + 速度)

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

這個模組負責第 2 種：**把每一首歌的校正綁在 song_id 上、存在伺服器**。

而「這首歌的 LRC 偏差」本身又有兩種形狀，v1.22 只處理得了前一種：

* **整首差一樣多**（片頭被剪掉、LRC 是另一個混音版本）—— 一個 `offset_ms`
  就修得好，唱到哪裡都一樣準。
* **越唱越歪**（v1.23 補的這一種）—— 抓到的歌詞是**速度不同**的版本：
  為了避開版權比對，上傳者把整首調快 1~6% 是常見做法。這時候第一句對得剛剛好、
  副歌開始慢半拍、片尾差到兩三秒。這種歪**加一個常數永遠修不好**：把片尾對準
  就換片頭錯，使用者會一路按方向鍵按到整首歌都對不上，然後以為機器壞了。

所以紀錄裡有兩個數字，構成一個仿射變換（跟 `lyrics_aligner` 對 LRC 候選做的
是同一件事，只是這一次的量測來自人耳）：

    音訊時間 = rate × 歌詞時間 + offset

`rate` 是「這個上傳版本比 LRC 的版本慢幾倍」（1.02 ＝ 慢 2%，也就是 LRC 那一份
被調快了 2%）。播放端走的是反函數（見 `frontend/js/lyric-sync.js` 的 `syncTimes`）：

    lyricTime = (scoreTime − offset) ÷ rate

rate ＝ 1 時整條式子退化成 v1.22 的行為，所以舊資料不必轉檔。

幾個刻意的決定：

* **`rate` 只由「兩點校正」寫，沒有滑桿。** 速度是解出來的，不是拉出來的：
  1.03 跟 1.04 在片頭聽起來完全一樣（差 0.3 秒要唱到第三分鐘才聽得出來），
  給一根滑桿等於要使用者用一個看不見回饋的動作去猜一個四位數的值。
  人能做的量測只有「這一句現在開始」，而兩個這種量測就唯一決定一條直線。

* **速度錯的代價比偏移錯大得多，所以夾限很緊（±15%）。** 偏移錯 300ms 就是
  錯 300ms；rate 錯 3% 在四分鐘的歌尾端是 7 秒 —— 而且畫面上不會有任何東西
  說「這個數字是猜的」。夾不住的值一律當「那兩點有一點對到別的句子了」而拒絕，
  詳見 `frontend/js/lyric-sync.js` 的 `solveTwoPoint`。

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
  **但 `rate` 一律不動**：裝置延遲是一個常數（喇叭不會讓音樂變快），
  速度差是那份 LRC 與那個上傳版本之間的事，跟這台機器接了什麼喇叭無關。
  減完偏移變 0、但 rate 不是 1 的歌因此**不能刪掉紀錄**（刪了等於把那首歌
  好不容易解出來的速度丟掉，而且症狀是「唱到一半又開始越唱越歪」）。
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

# 速度校正的上下限。±15% 已經涵蓋所有「為了避開版權比對而變速」的上傳
# （實務上是 1~6%），再外面的值一律是「兩點裡有一點對到別的句子了」——
# 那種情況繼續算下去會得到一條在片尾差十幾秒的直線，而畫面上看不出異狀。
MIN_RATE = 0.85
MAX_RATE = 1.15

# 比這個還小的速度差直接當 1.0：0.2% 在五分鐘的歌尾端是 0.6 秒，剛好在
# 「聽得出來」的邊界上，而人按鍵的反應時間本來就有 ±100ms 的雜訊。
# 存一個 1.0008 進去只會讓「這首校正過速度」這句話失真。
RATE_EPSILON = 0.002

# 檔案格式版本。v1 只有 offset_ms，v2 多一個 rate（缺了就是 1.0，舊檔照讀）。
OFFSETS_VERSION = 2


def clamp_offset_ms(value: Any) -> int:
    """把任何輸入夾成合法的偏移毫秒數。認不得的一律當 0（不是丟例外）。"""
    if isinstance(value, bool):  # bool 是 int 的子類，先擋掉
        return 0
    try:
        ms = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    return max(-MAX_OFFSET_MS, min(MAX_OFFSET_MS, ms))


def clamp_rate(value: Any) -> float:
    """
    把任何輸入夾成合法的速度倍率。認不得的一律當 1.0（＝沒有速度校正）。

    「認不得就當 1.0」跟偏移的「認不得就當 0」是同一條規則，但這裡更重要：
    rate 進到分母裡（`lyricTime = (scoreTime − offset) ÷ rate`），一個 0 或
    NaN 溜進去，整首歌的字幕會直接消失或停在第一句 —— 那不是「校正沒生效」，
    是舞台白掉。
    """
    if isinstance(value, bool):        # bool 是 int 的子類，先擋掉
        return 1.0
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return 1.0
    if rate != rate or rate in (float("inf"), float("-inf")):   # NaN / inf
        return 1.0
    # 0 與負數**不是「很慢的歌」，是壞值**（時間停住或倒著走）。夾成 MIN_RATE
    # 會把一個明顯的錯誤變成一個看起來合理的 0.85 —— 那首歌的字幕會歪得莫名其妙，
    # 而檔案裡的數字看起來像是有人校正過的。
    if rate <= 0:
        return 1.0
    rate = max(MIN_RATE, min(MAX_RATE, rate))
    if abs(rate - 1.0) < RATE_EPSILON:
        return 1.0
    # 存六位小數就夠了：1e-6 的速度差在十分鐘的歌上是 0.6 毫秒。
    # 不 round 的話 JSON 裡會出現 1.0240000000000002 這種字串。
    return round(rate, 6)


class LyricOffsets:
    """
    song_id -> 字幕校正（偏移毫秒，正值＝字幕延後；速度倍率，1.0＝不校正）。

    執行緒安全（FastAPI 的 threadpool 與心跳迴圈都可能碰到它）。
    偏移 0 **且**速度 1.0 的歌**不留紀錄** —— 「沒有校正過」與「校正結果剛好是
    沒有校正」對使用者是同一件事，留著只會讓檔案愈長愈大，也讓「校正過幾首」
    這個數字失真。反過來說，只要其中一個不是預設值就要留（只解出速度、
    偏移剛好是 0 的歌是很正常的結果）。
    """

    def __init__(self, offsets_file: Path):
        self.offsets_file = Path(offsets_file)
        self._lock = threading.Lock()
        # song_id -> {"offset_ms": int, "rate": float, "updated_at": str}
        self._records: Dict[str, Dict[str, Any]] = {}
        self._load()

    @staticmethod
    def _is_default(offset_ms: int, rate: float) -> bool:
        """這一筆等於「沒有校正過」嗎？是的話就不該留在檔案裡。"""
        return offset_ms == 0 and rate == 1.0

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
                # 舊檔（v1）沒有 rate 這個欄位 —— 缺了就是 1.0，不必轉檔。
                rate = clamp_rate(rec.get("rate", 1.0))
                if self._is_default(ms, rate):
                    continue
                self._records[str(song_id)] = {
                    "offset_ms": ms,
                    "rate": rate,
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

    def rate(self, song_id: Any) -> float:
        """這首歌的速度倍率。沒解過就是 1.0（絕不回 0 —— 它在分母裡）。"""
        key = str(song_id or "")
        if not key:
            return 1.0
        with self._lock:
            rec = self._records.get(key)
            return float(rec.get("rate", 1.0)) if rec else 1.0

    def calibration(self, song_id: Any) -> Dict[str, Any]:
        """
        兩個數字一起拿。廣播與播放端都該走這一支 —— 分兩次查會出現
        「新的偏移配上舊的速度」那一幀，而那一幀正好是字幕跳一下的樣子。
        """
        key = str(song_id or "")
        if not key:
            return {"offset_ms": 0, "rate": 1.0}
        with self._lock:
            rec = self._records.get(key)
            if not rec:
                return {"offset_ms": 0, "rate": 1.0}
            return {"offset_ms": int(rec["offset_ms"]), "rate": float(rec.get("rate", 1.0))}

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

    def all_calibrations(self) -> Dict[str, Dict[str, Any]]:
        """song_id -> {offset_ms, rate}。快取管理頁的徽章要兩個數字都畫得出來。"""
        with self._lock:
            return {song_id: {"offset_ms": int(rec["offset_ms"]),
                              "rate": float(rec.get("rate", 1.0))}
                    for song_id, rec in self._records.items()}

    def count(self) -> int:
        """校正過幾首（偏移或速度，兩者任一）。"""
        with self._lock:
            return len(self._records)

    def offset_count(self) -> int:
        """
        **偏移**不是 0 的有幾首 —— 升級成本機基準的確認框要用這一個。

        不能用 `count()`：只解出速度、偏移剛好是 0 的歌不會被 rebase 動到，
        算進去等於在確認框上對使用者多報了幾首。
        """
        with self._lock:
            return sum(1 for rec in self._records.values() if rec["offset_ms"] != 0)

    # --- 寫 ---

    def set(self, song_id: Any, offset_ms: Any) -> int:
        """
        只設偏移（速度保持原樣），回傳夾限之後真正生效的值。

        「速度保持原樣」是這一支最重要的性質：點歌台的滑桿與舞台的 ← → 只送
        得出偏移，如果沒給就當成「重設成 1.0」，那麼兩點校正解出來的速度會在
        使用者下一次微調偏移時**無聲消失** —— 而症狀（唱到後段又開始歪）
        跟「他剛剛那一下按錯方向」長得一模一樣。
        """
        return self.set_calibration(song_id, offset_ms=offset_ms)["offset_ms"]

    def set_calibration(self, song_id: Any, offset_ms: Any = None,
                        rate: Any = None) -> Dict[str, Any]:
        """
        設定這首歌的校正，回傳夾限之後真正生效的 `{"offset_ms", "rate"}`。

        `None` ＝ **這個數字不要動**（不是「設成預設值」）：兩點校正一次送兩個，
        滑桿只送偏移，而「重算歌詞」之後的清空走 `clear()`。
        兩個都回到預設值時那筆紀錄整個刪掉（見類別說明）。
        """
        key = str(song_id or "")
        if not key:
            return {"offset_ms": 0, "rate": 1.0}
        with self._lock:
            rec = self._records.get(key)
            ms = (clamp_offset_ms(offset_ms) if offset_ms is not None
                  else (int(rec["offset_ms"]) if rec else 0))
            new_rate = (clamp_rate(rate) if rate is not None
                        else (float(rec.get("rate", 1.0)) if rec else 1.0))
            if self._is_default(ms, new_rate):
                self._records.pop(key, None)
            else:
                self._records[key] = {
                    "offset_ms": ms,
                    "rate": new_rate,
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                }
            self._save()
        return {"offset_ms": ms, "rate": new_rate}

    def clear(self, song_id: Any) -> bool:
        """
        清掉這首歌的校正（偏移**與**速度都回到預設）。有清到才回 True。

        兩個一起清是刻意的：舞台上那顆歸零鍵（`0`）與點歌台的「歸零」是同一個
        動作 ——「這首歌的字幕又不對了，全部重來」。留著速度不清的話，使用者
        會看到一首「已經歸零過、卻還是越唱越歪」的歌，然後沒有任何鍵救得回來。
        """
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
        減完變成 0 的直接刪掉（那首歌的偏差正好就是裝置延遲）—— 但**解過速度的
        那幾首不能刪**：rate 不是裝置的屬性（喇叭不會讓音樂變快），少了它那首歌
        會從「對得好好的」變回「唱到後段越來越歪」，而使用者剛剛按的是一顆
        名叫「設成本機基準」的鍵，不會想到那是原因。
        """
        delta = clamp_offset_ms(delta_ms)
        if delta == 0:
            return {"changed": 0, "cleared": 0, "delta_ms": 0}
        changed, cleared = 0, 0
        stamp = datetime.now().isoformat(timespec="seconds")
        with self._lock:
            for song_id in list(self._records):
                rec = self._records[song_id]
                rate = float(rec.get("rate", 1.0))
                if rec["offset_ms"] == 0:
                    continue          # 只解過速度的歌：rebase 不該碰它
                new_ms = clamp_offset_ms(rec["offset_ms"] - delta)
                if self._is_default(new_ms, rate):
                    self._records.pop(song_id)
                    cleared += 1
                else:
                    self._records[song_id] = {"offset_ms": new_ms, "rate": rate,
                                              "updated_at": stamp}
                    changed += 1
            self._save()
        return {"changed": changed, "cleared": cleared, "delta_ms": delta}
