"""
曲庫查歌：注音首字、歌名字數、文字比對

商用點歌機（錢櫃、好樂迪、金嗓、音圓）的實體鍵盤上有三條查歌的路：注音首字、
歌名字數、歌手。KaraTube 原本只有一條「打字搜尋 YouTube」——那是線上點歌，
查到的歌要等 AI 跑完才唱得到。這支補的是另一條：**只在已經備好的曲庫裡查**，
查到的每一首都是快取秒播，點下去直接上台。

難的不是鍵盤，是索引要建在哪一串字上：

1. **YouTube 標題不是歌名。**「周杰倫 Jay Chou - 稻香 Rice Fields【Official MV】4K」
   照整串字算，「稻香」兩個字會數成二十幾個（字數查詢整個廢掉），注音首字查
   「ㄉㄒ」也永遠落空（開頭是「ㄓ」）。所以索引建立前要先萃出歌名本體。

2. **多音字不能只留一個讀音。**「重」的首碼是 ㄓ 也是 ㄔ。索引只存其中一個的話，
   用另一個讀音查的人就永遠查不到那首歌 —— 而且他不會知道為什麼，
   他會以為曲庫裡沒有這首。所以每個字存的是**所有讀音的首碼集合**，
   比對時只要交集非空就算命中。

3. **比對是連續子序列，不是只看開頭。** 商用機多半要求從第一個字開始按，
   但使用者記得的常常是歌名中間那幾個字（「甲你攬牢牢」記得的是「攬牢牢」）。
   這裡兩種都收，只是排序上讓開頭命中的排前面。

索引結果寫回每首歌的 `metadata.json`（`find_index`），跟語言/歌手分類同一個做法：
算一次就好，刪快取就跟著消失，不留對不上的孤兒索引。
"""
import logging
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence

from backend.services.library import UNKNOWN_ARTIST, looks_like_channel

logger = logging.getLogger("KaraTube.SongIndex")

# 索引格式版本。改了萃取或注音規則就 +1，讓舊索引自動重算
# （不改版本的話，使用者會拿著新鍵盤去查用舊規則建的索引，查不到還找不出原因）。
INDEX_VERSION = 1

# --- 注音 ---

try:  # pypinyin 是純 Python、幾百 KB；沒裝的話注音查歌關閉，文字與字數查詢照常
    from pypinyin import Style, pinyin as _pinyin
    BOPOMOFO_AVAILABLE = True
except Exception:  # pragma: no cover - 只有沒裝套件的環境會走到
    Style = None
    _pinyin = None
    BOPOMOFO_AVAILABLE = False

# 標準注音鍵盤的四排：聲母 21、介音 3、韻母 13（含 ㄦ）。
# 順序照實體點歌機/注音鍵盤的排法，讓用慣的人手指找得到。
BOPOMOFO_ROWS: List[List[str]] = [
    ["ㄅ", "ㄆ", "ㄇ", "ㄈ", "ㄉ", "ㄊ", "ㄋ", "ㄌ"],
    ["ㄍ", "ㄎ", "ㄏ", "ㄐ", "ㄑ", "ㄒ"],
    ["ㄓ", "ㄔ", "ㄕ", "ㄖ", "ㄗ", "ㄘ", "ㄙ"],
    ["ㄧ", "ㄨ", "ㄩ", "ㄚ", "ㄛ", "ㄜ", "ㄝ", "ㄞ", "ㄟ", "ㄠ", "ㄡ",
     "ㄢ", "ㄣ", "ㄤ", "ㄥ", "ㄦ"],
]
BOPOMOFO_KEYS = frozenset(k for row in BOPOMOFO_ROWS for k in row)

_BOPOMOFO_RE = re.compile(r"[ㄅ-ㄯ]")
_HAN_RE = re.compile(r"[一-鿿㐀-䶿]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z0-9']+")

# --- 歌名本體萃取 ---

# 括號類。中文標題常用《》「」標出歌名本身，跟【】()[] 這種「附註」不同，
# 所以前者是「取裡面」，後者是「丟掉裡面」。
_TITLE_QUOTED = re.compile(r"[《〈「『]([^》〉」』]{1,30})[》〉」』]")
_BRACKET_NOISE = re.compile(r"[\[\(【（][^\]\)】）]*[\]\)】）]")

# 分隔「歌手 - 歌名」的符號。全形與半形都吃
_SPLIT_RE = re.compile(r"\s+[-–—]\s+|\s*[｜|｀]\s*|\s*／\s*")

# 標題裡剝乾淨括號後仍會剩下的宣傳字樣
_NOISE_WORDS = (
    "official music video", "official mv", "official video", "official audio",
    "music video", "lyric video", "lyrics video", "audio only",
    "official", "mv", "hd", "hq", "4k", "8k", "1080p", "720p",
    "動態歌詞", "歌詞版", "歌詞", "字幕版", "高音質", "無損音質", "完整版",
    "現場版", "live版", "純音樂", "伴奏版", "官方版", "官方", "正式版",
    "華語", "新歌", "首播", "中文字幕", "中英字幕", "字幕", "中文翻譯", "翻譯",
)
_NOISE_RE = re.compile(
    r"(?:^|\s)(?:" + "|".join(re.escape(w) for w in _NOISE_WORDS) + r")(?=\s|$)",
    re.I,
)

# 歌名本體最多留這麼長。超過的多半是萃取失敗（整串標題留下來了），
# 讓它進索引只會讓字數查詢出現一堆 30 個字的「歌名」。
_MAX_CORE = 40


def _strip_edges(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip(" \t-–—｜|·、,，.。:：/\\")


def _same_name(a: str, b: str) -> bool:
    """兩個名字算不算同一個（比對歌手時忽略大小寫與空白）。"""
    norm = lambda s: re.sub(r"\s+", "", (s or "")).lower()  # noqa: E731
    a2, b2 = norm(a), norm(b)
    if not a2 or not b2:
        return False
    return a2 == b2 or a2 in b2 or b2 in a2


def title_core(title: str, artist_name: str = "") -> str:
    """
    從 YouTube 標題萃出「歌名本體」。

    順序：《歌名》這種明示的先認 → 丟掉【】()[] 附註 → 用 - ｜ 切開歌手與歌名 →
    刷掉 Official MV / 4K / 歌詞版這類宣傳字樣。

    切開之後哪一半是歌名？知道歌手是誰（分類瀏覽已經判過）就留**不是歌手**的那半；
    兩半都不像歌手時留後半 —— 華語 YouTube 的慣例是「歌手 - 歌名」，
    這個猜法跟 library.artist_from_title 的假設是同一個，兩邊才不會打架。

    萃不出東西時回傳整理過的原標題，絕不回空字串：查不到歌已經夠糟，
    曲庫裡出現一首沒有名字的歌更糟。
    """
    raw = (title or "").strip()
    if not raw:
        return ""

    quoted = _TITLE_QUOTED.search(raw)
    if quoted:
        inner = _trim_latin_tail(_strip_edges(quoted.group(1)))
        if inner:
            return inner[:_MAX_CORE]

    # 括號裡的東西通常是附註（Official MV、4K），但「五月天【入陣曲】」這種
    # 沒有分隔線的標題，括號裡放的才是歌名。所以先留著當備胎。
    bracketed = [_strip_edges(m.group(0)[1:-1]) for m in _BRACKET_NOISE.finditer(raw)]
    text = _BRACKET_NOISE.sub(" ", raw)

    parts = [p for p in (_strip_edges(p) for p in _SPLIT_RE.split(text)) if p]
    if len(parts) >= 2:
        if artist_name and artist_name != UNKNOWN_ARTIST:
            keep = [p for p in parts if not _same_name(p, artist_name)]
            if keep:
                parts = keep
        elif looks_like_channel(parts[0]):
            parts = parts[1:] or parts
        else:
            parts = parts[1:]
        text = parts[0]
    else:
        text = parts[0] if parts else text

    text = _strip_edges(_NOISE_RE.sub(" ", text))
    text = _drop_artist_token(text, artist_name)
    text = _trim_latin_tail(text)

    # 剝完只剩歌手名（或什麼都不剩）＝ 歌名根本不在這串字上，改用括號裡的。
    # 「五月天 Mayday【入陣曲】」也算這一類：中文歌手的英文名還是歌手，
    # 所以中文標題剝到一個漢字都不剩時，歌名多半被關在括號裡。
    lost_han = bool(_HAN_RE.search(raw)) and not _HAN_RE.search(text)
    if not text or lost_han or _same_name(text, artist_name):
        for cand in bracketed:
            cand = _strip_edges(_NOISE_RE.sub(" ", cand))
            cand = _drop_artist_token(cand, artist_name)
            if not cand or _same_name(cand, artist_name):
                continue
            # 補救中文歌名時只認真的有中文的候選，不然會把「Official Audio」
            # 這類剝不乾淨的附註當成歌名
            if lost_han and len(_HAN_RE.findall(cand)) < 2:
                continue
            if _has_word(cand):
                return _trim_latin_tail(cand)[:_MAX_CORE]
    if not text:
        text = _strip_edges(_BRACKET_NOISE.sub(" ", raw)) or raw.strip()
    return text[:_MAX_CORE]


def _trim_latin_tail(text: str) -> str:
    """中文歌名後面掛的英譯（「稻香 Rice Fields」）不算歌名的一部分，剝掉。

    留著的話兩件事會怪：字數（「稻香」是 2 個字，不是 2 個字加兩個英文單字），
    還有注音鍵盤 —— 按完「ㄉㄒ」之後下一格是英文單字，整個注音鍵盤會一起變灰，
    看起來就像壞掉。整串都是英文的歌名不動（那才是它真正的名字）。
    """
    text = _strip_edges(text)
    if not text or not _HAN_RE.search(text):
        return text
    while True:
        trimmed = re.sub(r"[\s,.:;\-–—]*[A-Za-z][A-Za-z0-9\'\.]*$", "", text).rstrip()
        if trimmed == text or not trimmed or not _HAN_RE.search(trimmed):
            return _strip_edges(text)
        text = trimmed


def _has_word(text: str) -> bool:
    return bool(_HAN_RE.search(text or "") or _LATIN_WORD_RE.search(text or ""))


def _drop_artist_token(text: str, artist_name: str) -> str:
    """「江蕙 甲你攬牢牢」這種只用空白隔開歌手的標題，把開頭/結尾的歌手名拿掉。

    只認完全相同的那一段，不做模糊比對 —— 歌手名剛好是歌名的一部分
    （周華健有一首就叫〈朋友〉）的時候，寧可少剝也不要把歌名剝掉。
    """
    name = _strip_edges(artist_name)
    if not name or name == UNKNOWN_ARTIST or not text:
        return text
    for pattern in (rf"^{re.escape(name)}\s+", rf"\s+{re.escape(name)}$"):
        stripped = _strip_edges(re.sub(pattern, " ", text, flags=re.I))
        if stripped and stripped != text:
            return stripped
    return text


def han_char_count(text: str) -> int:
    """歌名裡的漢字數。英文歌回 0（字數查詢本來就只適用中文歌名）。"""
    return len(_HAN_RE.findall(text or ""))


# --- 首碼索引 ---

def char_initials(ch: str) -> str:
    """
    單一個字的首碼候選，串成一個字串（"重" → "ㄓㄔㄊ"）。

    多音字保留全部讀音：使用者按的是他心裡那個讀音，不是字典排第一的那個。
    非漢字回空字串（呼叫端自己處理英數）。
    """
    if not BOPOMOFO_AVAILABLE or not ch or not _HAN_RE.match(ch):
        return ""
    try:
        readings = _pinyin(ch, style=Style.BOPOMOFO_FIRST, heteronym=True,
                           errors=lambda x: [])
    except Exception:  # pragma: no cover - pypinyin 對怪字不該讓查歌掛掉
        return ""
    out = []
    for group in readings:
        for sym in group:
            head = (sym or "")[:1]
            if head in BOPOMOFO_KEYS and head not in out:
                out.append(head)
    return "".join(out)


def title_keys(core: str) -> List[str]:
    """
    歌名本體 → 逐字的首碼候選清單。

    一個中文字一格、一個英數單字一格（"Yesterday Once More" → ["Y","O","M"]），
    標點與空白不佔格 —— 商用機上使用者按的是「字」，不是「字元」。
    """
    keys: List[str] = []
    text = core or ""
    i = 0
    while i < len(text):
        ch = text[i]
        if _HAN_RE.match(ch):
            cand = char_initials(ch)
            keys.append(cand)
            i += 1
            continue
        word = _LATIN_WORD_RE.match(text, i)
        if word:
            keys.append(word.group(0)[:1].upper())
            i = word.end()
            continue
        i += 1
    return keys


def normalize_query(query: str) -> List[str]:
    """使用者按出來的那一串 → 逐鍵清單。空白與標點忽略，英文一律轉大寫。"""
    out: List[str] = []
    for ch in (query or ""):
        if ch in BOPOMOFO_KEYS:
            out.append(ch)
        elif ch.isalnum():
            out.append(ch.upper())
    return out


def is_key_query(query: str) -> bool:
    """這串查詢是「按鍵盤按出來的首碼」還是「打出來的字」？"""
    text = (query or "").strip()
    if not text:
        return False
    if _BOPOMOFO_RE.search(text):
        return True
    # 純英數而且沒有漢字：兩種讀法都可能（"Yesterday" 是字，"YOM" 是首碼），
    # 所以呼叫端兩種都比，這裡只回答「可以當首碼讀嗎」。
    return bool(re.fullmatch(r"[A-Za-z0-9 ]+", text))


def match_keys(query_keys: Sequence[str], keys: Sequence[str]) -> Optional[int]:
    """
    首碼比對：query 是不是 keys 的連續子序列？是的話回傳起始位置，不是回 None。

    每一格用「候選集合有沒有包含」來判，多音字因此兩個讀音都查得到。
    """
    n, m = len(query_keys), len(keys)
    if n == 0 or n > m:
        return None
    for start in range(m - n + 1):
        for offset in range(n):
            cand = keys[start + offset] or ""
            if query_keys[offset] not in cand:
                break
        else:
            return start
    return None


# --- 索引與查詢 ---

class SongFinder:
    """
    曲庫查歌。索引建在 LibraryIndex 給出的「可以馬上唱」的歌上。

    自己不掃資料夾、不存檔：索引寫回 metadata.json，讀取一律經過 LibraryIndex，
    才不會出現「曲庫瀏覽看得到、查歌查不到」這種兩套清單對不起來的狀況。
    """

    def __init__(self, storage, library):
        self.storage = storage
        self.library = library

    # --- 索引 ---

    def index_for(self, song_id: str, title: str, artist_name: str = "",
                  meta: Optional[Dict[str, Any]] = None,
                  refresh: bool = False) -> Dict[str, Any]:
        """取得（必要時計算並寫回）一首歌的查歌索引。"""
        meta = meta if meta is not None else (self.storage.get_song_metadata(song_id) or {})
        cached = meta.get("find_index")
        if (not refresh and isinstance(cached, dict)
                and cached.get("v") == INDEX_VERSION
                and isinstance(cached.get("keys"), list)
                # 之前在沒有 pypinyin 的環境建的索引沒有注音，裝好之後要補建
                and (cached.get("bopomofo") or not BOPOMOFO_AVAILABLE)):
            return cached

        core = title_core(title, artist_name)
        entry = {
            "v": INDEX_VERSION,
            "core": core,
            "chars": han_char_count(core),
            "keys": title_keys(core),
            "bopomofo": BOPOMOFO_AVAILABLE,
        }
        try:
            self.storage.update_song_metadata(song_id, {"find_index": entry})
        except Exception as e:  # 寫不進去就每次重算，不該讓查歌失敗
            logger.warning(f"查歌索引寫回失敗 {song_id}: {e}")
        return entry

    def entries(self, refresh: bool = False) -> List[Dict[str, Any]]:
        """曲庫每一首歌 + 查歌索引。"""
        out: List[Dict[str, Any]] = []
        for entry in self.library.entries():
            idx = self.index_for(entry["song_id"], entry.get("title", ""),
                                 entry.get("artist_name", ""), refresh=refresh)
            out.append({**entry,
                        "core_title": idx.get("core", ""),
                        "char_count": int(idx.get("chars") or 0),
                        "keys": list(idx.get("keys") or [])})
        return out

    # --- 查詢 ---

    def facets(self) -> Dict[str, Any]:
        """查歌鍵盤要的資料：字數桶、曲庫總數、注音查詢能不能用。"""
        entries = self.entries()
        counts = Counter(e["char_count"] for e in entries if e["char_count"] > 0)
        buckets = [{"chars": n, "count": c} for n, c in sorted(counts.items())]
        latin = sum(1 for e in entries if e["char_count"] == 0)
        return {
            "bopomofo_available": BOPOMOFO_AVAILABLE,
            "rows": BOPOMOFO_ROWS,
            "char_buckets": buckets,
            "latin_count": latin,
            "total": len(entries),
        }

    @staticmethod
    def _next_keys(matched: List[Dict[str, Any]], keys_query: Sequence[str],
                   raw_query: str) -> List[str]:
        """
        目前這一串後面「再按哪些鍵仍然有歌」。

        商用點歌機會把按下去必定落空的鍵變灰，使用者才不會在鍵盤上亂試。
        只有首碼查詢（含還沒按任何鍵）算得出來；使用者是直接打字的時候回空清單，
        呼叫端把整個鍵盤當成可按 —— 寧可按下去查不到，也不要把查得到的鍵鎖起來。
        """
        if raw_query and not keys_query:
            return []
        at = len(keys_query)
        out: List[str] = []
        for e in matched:
            if keys_query and e.get("match_kind") != "keys":
                continue
            pos = e.get("match_pos", 0) if keys_query else 0
            slot = pos + at
            keys = e.get("keys") or []
            if slot >= len(keys):
                continue
            for sym in keys[slot]:
                if sym not in out:
                    out.append(sym)
        return sorted(out)

    def search(self, query: str = "", chars: Optional[int] = None,
               limit: int = 60) -> Dict[str, Any]:
        """
        在曲庫裡查歌。

        * `query` 有注音符號 → 只走首碼比對。
        * `query` 是純英數 → 首碼比對與文字比對都試，取比較好的那個
          （"LOVE" 既可能是四個字的首碼，也可能是歌名裡的字）。
        * `query` 含漢字 → 文字比對（使用者已經打得出字了，不需要再猜首碼）。
        * `chars` 是歌名字數篩選，可以單獨用，也可以跟 query 疊。

        兩者都沒給就回整個曲庫（照歌名排）——「按了字數又想看全部」時
        使用者的動作是把條件清掉，那一下不該得到空畫面。
        """
        all_entries = self.entries()
        wanted = None
        if chars is not None:
            try:
                wanted = int(chars)
            except (TypeError, ValueError):
                wanted = None
        entries = ([e for e in all_entries if e["char_count"] == wanted]
                   if wanted is not None and wanted > 0 else all_entries)

        text = (query or "").strip()
        keys_query = normalize_query(text) if is_key_query(text) else []
        text_query = text.lower() if text and not _BOPOMOFO_RE.search(text) else ""

        matched: List[Dict[str, Any]] = []
        for e in entries:
            pos = match_keys(keys_query, e["keys"]) if keys_query else None
            kind = "keys" if pos is not None else ""
            if pos is None and text_query:
                core = (e.get("core_title") or "").lower()
                where = core.find(text_query)
                if where < 0:
                    haystack = f"{e.get('title', '')} {e.get('artist_name', '')}".lower()
                    where = 0 if text_query in haystack else -1
                    if where == 0:
                        # 命中在原標題或歌手名上，比命中歌名本體弱，排到後面去
                        pos, kind = len(e["keys"]) + 1, "text"
                else:
                    pos, kind = where, "text"
            if text and pos is None:
                continue
            matched.append({**e, "match_kind": kind or "all",
                            "match_pos": pos if pos is not None else 0})

        # 開頭命中排前面（使用者多半從第一個字按起），再來是短歌名、常點唱的。
        matched.sort(key=lambda e: (e["match_pos"], e["char_count"] or 99,
                                    -e["plays"], e["core_title"]))
        limit = max(0, min(int(limit or 0), 300))
        return {
            "next_keys": self._next_keys(matched, keys_query, text),
            "songs": matched[:limit],
            "total": len(matched),
            "query": text,
            "keys": keys_query,
            "chars": wanted if wanted and wanted > 0 else None,
            "bopomofo_available": BOPOMOFO_AVAILABLE,
            "library_total": len(all_entries),
        }
