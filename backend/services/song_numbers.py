"""
歌號：每一首備好的歌一組六位數字

商用點歌機（金嗓、音圓、錢櫃的桌上機）上最快的一條點歌路不是搜尋，是**歌號**：
常客記得「135792 是甲你攬牢牢」，坐下來按六個鍵、確認，歌就進佇列了 ——
不必想關鍵字、不必挑注音、不必在十五個搜尋結果裡找哪一個才是原版。
包廂裡也只有這一條路可以用喊的（「幫我點 100237」），而其他每一條都得走過去看螢幕。

功能本身很小（給每首歌一個號碼），難的全部在**號碼的承諾**：

1. **號碼絕不回收。** 這是整個功能唯一的價值來源。使用者把 100237 記成「稻香」
   之後，那六個數字就必須永遠是稻香、或者什麼都不是 —— 絕不可以變成別首歌。
   所以刪快取**不**釋放號碼：本子上留一筆「這個號碼是稻香，現在不在曲庫裡」，
   打進去得到的是「這首歌已經不在曲庫了」，而不是別人的歌。
   回收號碼省下的是幾個數字，賠掉的是「記號碼」這件事本身。

2. **同一首歌回來要拿回原號。** 砍掉重跑（重新處理）與「刪掉之後又點一次」
   在包廂裡都很常見。號碼綁在 `song_id`（YouTube 影片 ID，重跑不會變）上，
   所以歌回來就自動復位。這也是為什麼墓碑不能只記「這個號碼死了」，
   而要記「這個號碼是誰的」。

3. **只發給已經備好的歌。** YouTube 有無限首歌，給每個搜尋結果發號等於一晚燒光
   整個號碼池，而且那個號碼隔天對不到任何東西（那首歌根本沒留下來）。
   發號的時機是「這首歌完整地出現在曲庫清單裡」，所以列出來的每一個號碼
   都是快取秒播。

4. **發號順序要可重建。** 第一次升級上來時曲庫可能已經有幾百首，要一口氣補發。
   照資料夾的掃描順序發的話，同一份曲庫在兩台機器上會長出兩套號碼；
   照「入庫時間，同秒再照 song_id」排序發，任何人重建都得到同一份號碼簿。

號碼簿存在 `cache/song_numbers.json`，跟歌的快取資料夾分開 ——
刪掉一首歌的資料夾不該把「那個號碼曾經是誰」一起刪掉（見第 1 點）。
"""
import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

logger = logging.getLogger("KaraTube.SongNumbers")

# 第一組號碼。從 100001 起跳而不是 1：
# 六位數是商用點歌機的慣例（長度固定，按滿就知道打完了），
# 而且沒有前導零 —— 「001234」在任何一個文字欄位裡都會被吃成 1234。
NUMBER_START = 100001

# 號碼簿格式版本。改了發號規則就 +1（目前只用來認舊檔）。
BOOK_VERSION = 1

# 一次查詢最多回幾個候選。鍵盤上打兩碼會命中幾百首，
# 而捲三頁找歌比重打一次號碼慢得多。
DEFAULT_LIMIT = 40


def parse_number(text: Any) -> Optional[int]:
    """
    把使用者打的那一串變成號碼。認不得（有非數字、太短、超出範圍）回 None。

    刻意不接受 `" 100237 "` 以外的花樣（沒有 #、沒有連字號）：
    這個欄位只有數字鍵按得出來，寬鬆解析只會讓「打錯」看起來像「查不到」。
    """
    if isinstance(text, bool):  # bool 是 int 的子類，先擋掉
        return None
    if isinstance(text, int):
        value = text
    else:
        raw = str(text or "").strip()
        if not raw or not raw.isdigit():
            return None
        value = int(raw)
    if value < NUMBER_START or value > 10 ** 9:
        return None
    return value


def format_number(number: Any) -> str:
    """號碼的顯示字串。號碼本身就是六位數，所以不必補零。"""
    n = parse_number(number)
    return str(n) if n is not None else ""


class SongNumberBook:
    """
    號碼簿：song_id ↔ 歌號的雙向對照，外加已下架號碼的墓碑。

    自己不掃快取資料夾 —— 「現在有哪些歌」一律由 `LibraryIndex` 決定，
    這裡只負責「這首歌的號碼是幾號」。兩套曲庫清單對不起來是這個專案裡
    最難查的一類 bug（曲庫瀏覽看得到、查歌查不到），所以一套就好。
    """

    def __init__(self, book_file: Path):
        self.book_file = Path(book_file)
        self._lock = threading.Lock()
        # 號碼簿讀不出來時立起來的旗子。壞掉的本子不補發、不覆寫（見 _load）。
        self._broken = False
        # song_id -> {"number", "title", "artist", "assigned_at"}
        self._records: Dict[str, Dict[str, Any]] = {}
        # number -> song_id（反查用，永遠從 _records 重建，不獨立存檔）
        self._by_number: Dict[int, str] = {}
        self._next = NUMBER_START
        self._load()

    # --- 存取 ---

    def _load(self):
        if not self.book_file.exists():
            return
        try:
            raw = json.loads(self.book_file.read_text(encoding="utf-8"))
        except Exception as e:
            # 這裡刻意**不**重新發號，也不覆寫那個檔案。
            #
            # 其他持久化資料（點唱統計、評分歷史）壞掉時的做法都是「重新開始」，
            # 因為重算一次就回來了。號碼簿不行：重發一套新號碼，等於把包廂裡
            # 每一個人記住的號碼同時改成別首歌 —— 那正是這個功能承諾不會發生的事。
            # 所以壞掉的本子原地留著（讓人可以去修或還原備份），系統這一輪
            # 沒有歌號可用（前端會說明），但不會給出**錯的**歌號。
            logger.error(f"歌號簿讀取失敗，歌號暫停服務（檔案保留不覆寫）: {e}")
            self._records = {}
            self._by_number = {}
            self._next = NUMBER_START
            self._broken = True
            return
        records = raw.get("songs") if isinstance(raw, dict) else None
        if isinstance(records, dict):
            for song_id, rec in records.items():
                if not isinstance(rec, dict):
                    continue
                number = parse_number(rec.get("number"))
                if number is None or number in self._by_number:
                    continue  # 壞掉或重複的號碼直接跳過，不搶別人的
                self._records[str(song_id)] = {
                    "number": number,
                    "title": str(rec.get("title") or ""),
                    "artist": str(rec.get("artist") or ""),
                    "assigned_at": str(rec.get("assigned_at") or ""),
                }
                self._by_number[number] = str(song_id)
        # 下一個號碼取「檔案裡記的」與「現有最大號 + 1」的較大者：
        # 檔案被手動編輯過（刪掉幾筆）時，照最大號續發才不會發出已經有人記住的號碼。
        stored_next = parse_number(raw.get("next")) if isinstance(raw, dict) else None
        highest = max(self._by_number) + 1 if self._by_number else NUMBER_START
        self._next = max(stored_next or NUMBER_START, highest, NUMBER_START)

    def _save(self):
        """整份寫回。先寫暫存檔再 rename，中途斷電不會留下半份號碼簿。"""
        if self._broken:
            return  # 壞掉的本子不覆寫（見 _load）
        try:
            self.book_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": BOOK_VERSION,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "next": self._next,
                "songs": self._records,
            }
            tmp = self.book_file.with_suffix(self.book_file.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, self.book_file)
        except Exception as e:
            logger.warning(f"歌號簿寫入失敗: {e}")

    # --- 發號 ---

    def assign(self, entries: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        就地把 `number` 補進曲庫清單的每一筆，需要的話發新號。

        `entries` 是 `LibraryIndex.entries()` 的輸出（含 song_id / title /
        artist_name / cached_at）。回傳的就是同一份 list，方便串接。

        沒號碼的歌照「入庫時間，同秒再照 song_id」排序後依序發號 ——
        掃描順序在不同檔案系統上不一樣，照它發的話同一份曲庫會長出兩套號碼。
        """
        if not entries or self._broken:
            return list(entries or [])
        with self._lock:
            dirty = False
            pending = []
            for entry in entries:
                song_id = str(entry.get("song_id") or entry.get("id") or "")
                if not song_id:
                    continue
                rec = self._records.get(song_id)
                if rec is None:
                    pending.append(entry)
                    continue
                entry["number"] = rec["number"]
                # 歌名／歌手可能被重新判定過（分類、重新處理）。墓碑上寫的是
                # 這首歌「最後一次在曲庫裡的樣子」，所以跟著更新。
                title = str(entry.get("title") or "")
                artist = str(entry.get("artist_name") or entry.get("artist") or "")
                if title and title != rec["title"]:
                    rec["title"] = title
                    dirty = True
                if artist and artist != rec["artist"]:
                    rec["artist"] = artist
                    dirty = True

            for entry in sorted(pending, key=lambda e: (int(e.get("cached_at") or 0),
                                                        str(e.get("song_id") or ""))):
                song_id = str(entry.get("song_id") or entry.get("id") or "")
                number = self._take_number_locked()
                self._records[song_id] = {
                    "number": number,
                    "title": str(entry.get("title") or ""),
                    "artist": str(entry.get("artist_name") or entry.get("artist") or ""),
                    "assigned_at": datetime.now().isoformat(timespec="seconds"),
                }
                self._by_number[number] = song_id
                entry["number"] = number
                dirty = True

            if dirty:
                self._save()
        return list(entries)

    def _take_number_locked(self) -> int:
        """下一個沒有人用過的號碼（呼叫前必須先拿到鎖）。"""
        number = max(self._next, NUMBER_START)
        while number in self._by_number:
            number += 1
        self._next = number + 1
        return number

    def ensure(self, song_id: str, title: str = "", artist: str = "") -> Optional[int]:
        """
        單首發號：這首歌剛處理完、或剛被點進佇列時叫一次。已經有號碼就回原號。

        跟 `assign()` 的差別只在「一首 vs 一批」——批次那條要先排序才發號
        （見 assign 的說明），單首沒有順序問題：它就是現在這一刻入庫的那一首。
        歌剛備好就發號，是為了讓舞台的片頭卡當場印得出「下次直接打這組號碼」；
        等到有人去翻曲庫才發號的話，那張片頭卡上永遠是空的。
        """
        song_id = str(song_id or "")
        if not song_id or self._broken:
            return None
        with self._lock:
            rec = self._records.get(song_id)
            if rec is not None:
                return rec["number"]
            number = self._take_number_locked()
            self._records[song_id] = {
                "number": number,
                "title": str(title or ""),
                "artist": str(artist or ""),
                "assigned_at": datetime.now().isoformat(timespec="seconds"),
            }
            self._by_number[number] = song_id
            self._save()
        return number

    def number_of(self, song_id: str) -> Optional[int]:
        with self._lock:
            rec = self._records.get(str(song_id or ""))
            return rec["number"] if rec else None

    # --- 查號 ---

    def lookup(self, number: Any) -> Optional[Dict[str, Any]]:
        """
        號碼 → 那一筆紀錄（含 song_id、最後知道的歌名）。沒發過這個號碼回 None。

        「這首歌還在不在曲庫裡」不在這裡回答 —— 那是曲庫的事，
        而號碼簿記的是「這個號碼是誰的」，兩者的答案在歌被刪掉之後就會不一樣，
        剛好也就是這個功能存在的理由。
        """
        n = parse_number(number)
        if n is None:
            return None
        with self._lock:
            song_id = self._by_number.get(n)
            if not song_id:
                return None
            rec = self._records.get(song_id) or {}
            return {"number": n, "song_id": song_id,
                    "title": rec.get("title", ""), "artist": rec.get("artist", ""),
                    "assigned_at": rec.get("assigned_at", "")}

    def candidates(self, prefix: str = "", ready_ids: Optional[Iterable[str]] = None,
                   limit: int = DEFAULT_LIMIT) -> Dict[str, Any]:
        """
        打到一半時的候選清單與「下一個數字鍵按哪些還有歌」。

        商用點歌機要打滿六碼再按確認，這裡前綴就開始列 —— 包廂裡記得半組號碼
        （「一開頭那幾碼是 1002 什麼的」）比記得整組常見得多。

        `ready_ids` 是「現在真的唱得到的歌」。候選清單**只列這些**：
        列一首點不下去的歌只會讓人按下去然後發現沒反應。已下架的號碼
        要等使用者打滿整組、明確地問「這一首去哪了」，才由 lookup 回答。
        """
        digits = "".join(ch for ch in str(prefix or "") if ch.isdigit())
        ready: Optional[Set[str]] = set(ready_ids) if ready_ids is not None else None
        with self._lock:
            rows = []
            for song_id, rec in self._records.items():
                if ready is not None and song_id not in ready:
                    continue
                text = str(rec["number"])
                if digits and not text.startswith(digits):
                    continue
                rows.append((rec["number"], song_id, text))
        rows.sort()
        # 下一鍵：接著按哪些數字還有歌。跟注音鍵盤把落空的鍵變灰是同一個做法 ——
        # 使用者按了六下才發現沒有這首歌，那六下就是白按的。
        at = len(digits)
        next_digits = sorted({text[at] for _, _, text in rows if len(text) > at})
        return {
            "prefix": digits,
            "song_ids": [song_id for _, song_id, _ in rows[:max(1, int(limit))]],
            "total": len(rows),
            "next_digits": next_digits,
        }

    # --- 狀態 ---

    def stats(self, ready_ids: Optional[Iterable[str]] = None) -> Dict[str, Any]:
        ready: Optional[Set[str]] = set(ready_ids) if ready_ids is not None else None
        with self._lock:
            total = len(self._records)
            in_library = (sum(1 for sid in self._records if sid in ready)
                          if ready is not None else total)
            return {
                "assigned": total,
                "in_library": in_library,
                # 發過號但現在不在曲庫裡的（墓碑）。這個數字會一直長，
                # 那正是「號碼不回收」的成本，講出來比藏起來好。
                "retired": total - in_library,
                "next_number": self._next,
                "start": NUMBER_START,
                # 號碼簿壞掉時整個歌號功能要講得出自己為什麼不能用 ——
                # 不講的話畫面上只是「查無此歌號」，而那是騙人的。
                "available": not self._broken,
            }

    def records(self) -> List[Dict[str, Any]]:
        """整本號碼簿，照號碼排（列印歌本用）。"""
        with self._lock:
            rows = [{"number": rec["number"], "song_id": song_id,
                     "title": rec.get("title", ""), "artist": rec.get("artist", "")}
                    for song_id, rec in self._records.items()]
        rows.sort(key=lambda r: r["number"])
        return rows
