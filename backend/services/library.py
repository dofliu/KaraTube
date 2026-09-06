"""
曲庫瀏覽：語言 / 歌手分類、新歌榜與推薦歌單

商用點歌機（錢櫃、好樂迪、金嗓、音圓）的主畫面除了搜尋，就是「分類點歌」——
語言別（國語 / 台語 / 粵語 / 日語 / 韓語 / 英語）、歌手、以及新歌榜。
KaraTube 的曲庫是使用者點過的歌自然長出來的，YouTube 又不會給我們語言標籤，
所以這裡自己從「歌名 + 頻道名 + 已對齊的歌詞」判語言、從頻道名還原歌手。

判定結果會寫回 `metadata.json`（`language` / `artist_name`），
所以每首歌只算一次，之後瀏覽曲庫都是純讀檔。
"""
import logging
import re
import time
from collections import Counter
from typing import Any, Dict, List, Optional

logger = logging.getLogger("KaraTube.Library")

# 分類瀏覽的語言別。順序就是點歌台上的顯示順序（跟商用點歌機一致：國語打頭）。
LANGUAGE_SPEC: List[Dict[str, str]] = [
    {"key": "mandarin", "label": "國語"},
    {"key": "taiwanese", "label": "台語"},
    {"key": "cantonese", "label": "粵語"},
    {"key": "japanese", "label": "日語"},
    {"key": "korean", "label": "韓語"},
    {"key": "english", "label": "英語"},
    {"key": "other", "label": "其他"},
]
LANGUAGE_KEYS = tuple(spec["key"] for spec in LANGUAGE_SPEC)
_LANGUAGE_LABELS = {spec["key"]: spec["label"] for spec in LANGUAGE_SPEC}

UNKNOWN_ARTIST = "未知歌手"

# 字元類別。中日韓三種文字混用，只能靠比例判斷，不能看到一個假名就當日文歌。
_HANGUL = re.compile(r"[가-힣ᄀ-ᇿ]")
_KANA = re.compile(r"[぀-ゟ゠-ヿ]")
_HAN = re.compile(r"[一-鿿㐀-䶿]")
_LATIN = re.compile(r"[A-Za-z]")

# 歌名 / 頻道名裡直接寫明語言別的，最準，優先採用
_LANGUAGE_HINTS = (
    ("taiwanese", ("台語", "臺語", "閩南語", "河洛話")),
    ("cantonese", ("粵語", "廣東話", "粵語歌")),
    ("japanese", ("日文歌", "日語歌")),
    ("korean", ("韓文歌", "韓語歌")),
    ("english", ("英文歌", "西洋歌", "西洋老歌")),
)

# 粵語專用字。取「幾乎只在粵語書面語出現」的，避免誤判國語歌
# （所以不收 係、仲、嗰陣 這類在國語文本也會出現的字）。
_CANTONESE_MARKERS = ("唔", "嘅", "咁", "冇", "喺", "佢", "睇", "諗",
                      "乜嘢", "啲", "咗", "咩", "梗係", "點解")

# 台語（閩南語）書面用字。同樣只收辨識度高的。
_TAIWANESE_MARKERS = ("袂", "毋", "佇", "啥物", "拍拚", "嘸", "甲意", "厝",
                      "逐家", "阿母", "歹勢", "一世人", "阮兜", "咱兜", "轉來")

# 判語言只需要開頭一小段歌詞，整首讀進來對結果沒有幫助
_LYRICS_SAMPLE_CHARS = 600

# 「新歌」的定義：加入曲庫幾天內算新
NEW_SONG_DAYS = 14


def language_label(key: str) -> str:
    """語言代碼 → 中文顯示名。認不得的一律當「其他」。"""
    return _LANGUAGE_LABELS.get(key, _LANGUAGE_LABELS["other"])


def _marker_hits(text: str, markers) -> int:
    """文本裡出現了幾種（不是幾次）指定的方言用字。"""
    return sum(1 for m in markers if m in text)


def detect_language(title: str, artist: str = "", lyrics_text: str = "") -> str:
    """
    判斷一首歌的語言別。

    順序：歌名/頻道名的明示標記 → 韓文 → 日文 → 漢字（再細分粵語/台語/國語）→ 英文。
    粵語與台語靠方言專用字判定，要出現兩種以上才算，單一個字不足以定案
    （「厝」也可能只是國語歌詞裡的一個字）。判不出來就回 "other"，
    不會丟例外 —— 分類錯了頂多是分頁擺錯，不該讓曲庫掛掉。
    """
    header = f"{title or ''} {artist or ''}"
    for key, hints in _LANGUAGE_HINTS:
        if any(hint in header for hint in hints):
            return key

    text = f"{lyrics_text or ''} {header}"
    hangul = len(_HANGUL.findall(text))
    kana = len(_KANA.findall(text))
    han = len(_HAN.findall(text))
    latin = len(_LATIN.findall(text))

    # 韓文歌就算夾雜漢字，諺文也一定佔多數
    if hangul >= 3 or (hangul >= 1 and hangul * 2 >= han):
        return "korean"
    # 日文歌一定有假名，而且假名不會少於漢字太多；
    # 反過來說國語歌名裡的一個「の」不該把整首歌判成日文，所以要看比例。
    if kana >= 3 or (kana >= 1 and kana * 2 >= han):
        return "japanese"
    if han >= 2:
        cantonese = _marker_hits(text, _CANTONESE_MARKERS)
        taiwanese = _marker_hits(text, _TAIWANESE_MARKERS)
        if cantonese >= 2 and cantonese >= taiwanese:
            return "cantonese"
        if taiwanese >= 2:
            return "taiwanese"
        return "mandarin"
    if latin >= 3:
        return "english"
    return "other"


# YouTube 自動產生的歌手頻道叫「周杰倫 - Topic」，官方頻道則常帶 Official / 官方
_TOPIC_SUFFIX = re.compile(r"\s*[-－]\s*Topic\s*$", re.I)
_VEVO_SUFFIX = re.compile(r"\s*VEVO\s*$", re.I)
_OFFICIAL_SUFFIX = re.compile(
    r"\s*(官方(?:專屬)?(?:頻道|音樂頻道)?|Official(?:\s+(?:Channel|Artist\s+Channel|Music|Video))?)\s*$",
    re.I,
)

# 有這些字樣的多半是唱片公司/音樂頻道，不是歌手本人的名字
_CHANNEL_WORDS = ("records", "record", "entertainment", "music", "musik", "channel",
                  "studio", "media", "tv", "official", "karaoke", "lyrics",
                  "唱片", "娛樂", "音樂", "頻道", "工作室", "傳媒", "官方",
                  "伴唱", "卡拉", "歌詞", "點歌", "ktv", "mv")

# 「歌手 - 歌名」「歌手｜歌名」是華語 YouTube 最常見的命名法
_TITLE_SPLIT = re.compile(r"\s+[-–—]\s+|\s*[｜|]\s*")
_BRACKETED = re.compile(r"[\[\(【《〈].*?[\]\)】》〉]")


def _clean_channel(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return ""
    name = _TOPIC_SUFFIX.sub("", name)
    name = _VEVO_SUFFIX.sub("", name)
    name = _OFFICIAL_SUFFIX.sub("", name)
    return re.sub(r"\s+", " ", name).strip(" -–—｜|")


def looks_like_channel(name: str) -> bool:
    """這個名字看起來像頻道而不像歌手嗎？"""
    lowered = (name or "").lower()
    return any(word in lowered for word in _CHANNEL_WORDS)


def artist_from_title(title: str) -> str:
    """從「歌手 - 歌名」這類標題裡取出歌手。取不到就回空字串。"""
    title = (title or "").strip()
    if not title:
        return ""
    head = _TITLE_SPLIT.split(title, maxsplit=1)[0]
    head = _BRACKETED.sub("", head).strip()
    # 沒切到（整串都是 head）或切出來太長，就當作標題裡沒有歌手資訊
    if not head or head == title or len(head) > 24:
        return ""
    return head


def normalize_artist(title: str, uploader: str) -> str:
    """
    把 YouTube 頻道名整理成比較像「歌手」的字串，給分類瀏覽當歌手鍵用。

    頻道名本身像歌手（「周杰倫 - Topic」→「周杰倫」）就用它；
    看起來是唱片公司或音樂頻道，才退而求其次從歌名前半段猜。
    兩邊都失敗回「未知歌手」，讓這些歌至少聚在一起而不是各自成群。
    """
    name = _clean_channel(uploader)
    if not name or looks_like_channel(name):
        guessed = artist_from_title(title)
        if guessed:
            return guessed
    return name or UNKNOWN_ARTIST


def lyrics_sample(lyrics: Optional[List[Dict[str, Any]]],
                  max_chars: int = _LYRICS_SAMPLE_CHARS) -> str:
    """把 lyrics.json 攤成純文字，只取前面一小段用來判語言。"""
    if not lyrics:
        return ""
    parts: List[str] = []
    total = 0
    for line in lyrics:
        if not isinstance(line, dict):
            continue
        text = str(line.get("text") or "")
        if not text:
            continue
        parts.append(text)
        total += len(text)
        if total >= max_chars:
            break
    return " ".join(parts)[:max_chars]


class LibraryIndex:
    """
    曲庫索引：把快取資料夾長成可以「分類點歌」的清單。

    不自己存檔 —— 分類結果寫回每首歌的 metadata.json，
    刪快取就跟著消失，不會留下對不上的孤兒索引。
    """

    def __init__(self, storage, play_stats=None, song_history=None):
        self.storage = storage
        self.play_stats = play_stats
        self.song_history = song_history

    # --- 分類 ---

    def classify(self, song_id: str, meta: Optional[Dict[str, Any]] = None,
                 refresh: bool = False) -> Dict[str, str]:
        """取得（必要時計算並寫回）一首歌的語言與歌手。"""
        meta = meta if meta is not None else (self.storage.get_song_metadata(song_id) or {})
        language = meta.get("language")
        artist_name = meta.get("artist_name")
        if not refresh and language in LANGUAGE_KEYS and artist_name:
            return {"language": language, "artist_name": artist_name}

        title = meta.get("title", "") or ""
        artist = meta.get("artist", "") or ""
        try:
            sample = lyrics_sample(self.storage.get_song_lyrics(song_id))
        except Exception:  # 歌詞壞掉不該讓整個曲庫頁掛掉
            sample = ""
        language = detect_language(title, artist, sample)
        artist_name = normalize_artist(title, artist)
        self.storage.update_song_metadata(
            song_id, {"language": language, "artist_name": artist_name})
        return {"language": language, "artist_name": artist_name}

    # --- 清單 ---

    def _play_map(self) -> Dict[str, Dict[str, Any]]:
        if self.play_stats is None:
            return {}
        try:
            return self.play_stats.snapshot()
        except Exception:
            return {}

    def entries(self, refresh: bool = False) -> List[Dict[str, Any]]:
        """曲庫裡「可以馬上唱」的歌（檔案齊全的），附語言、歌手與點唱次數。"""
        plays = self._play_map()
        result: List[Dict[str, Any]] = []
        for entry in self.storage.list_cache_entries():
            if not entry.get("complete"):
                continue  # 檔案不齊的歌唱不了，不該出現在點歌清單
            song_id = entry["song_id"]
            meta = self.storage.get_song_metadata(song_id) or {}
            tags = self.classify(song_id, meta, refresh=refresh)
            stat = plays.get(song_id, {})
            result.append({
                "song_id": song_id,
                "id": song_id,
                "title": meta.get("title") or entry.get("title") or song_id,
                "artist": meta.get("artist") or entry.get("artist") or "",
                "artist_name": tags["artist_name"],
                "language": tags["language"],
                "language_label": language_label(tags["language"]),
                "thumbnail": (entry.get("thumbnail") or meta.get("thumbnail")
                              or f"https://i.ytimg.com/vi/{song_id}/mqdefault.jpg"),
                "duration": meta.get("duration") or 0,
                "cached_at": entry.get("cached_at", 0),
                "plays": int(stat.get("plays", 0)),
                "last_played": stat.get("last_played"),
                "is_cached": True,
            })
        return result

    def facets(self) -> Dict[str, Any]:
        """分類瀏覽的側欄資料：每個語言別幾首、每位歌手幾首。"""
        entries = self.entries()
        lang_counts = Counter(e["language"] for e in entries)
        languages = [{"key": spec["key"], "label": spec["label"],
                      "count": lang_counts.get(spec["key"], 0)}
                     for spec in LANGUAGE_SPEC]

        artist_counts = Counter(e["artist_name"] for e in entries)
        thumbs: Dict[str, str] = {}
        for e in entries:
            thumbs.setdefault(e["artist_name"], e["thumbnail"])
        # 歌多的歌手排前面，同樣多首則照名字排，順序才穩定
        artists = [{"name": name, "count": count, "thumbnail": thumbs.get(name, "")}
                   for name, count in sorted(artist_counts.items(),
                                             key=lambda kv: (-kv[1], kv[0]))]
        return {
            "languages": languages,
            "artists": artists,
            "total": len(entries),
            "artist_count": len(artists),
        }

    def browse(self, language: Optional[str] = None, artist: Optional[str] = None,
               sort: str = "recent", limit: int = 200) -> List[Dict[str, Any]]:
        """依語言 / 歌手篩選曲庫。認不得的語言或歌手回空清單，不報錯。"""
        entries = self.entries()
        if language and language != "all":
            entries = [e for e in entries if e["language"] == language]
        if artist and artist != "all":
            entries = [e for e in entries if e["artist_name"] == artist]

        if sort == "plays":
            entries.sort(key=lambda e: e["cached_at"], reverse=True)
            entries.sort(key=lambda e: e["plays"], reverse=True)
        elif sort == "title":
            entries.sort(key=lambda e: e["title"])
        elif sort == "artist":
            entries.sort(key=lambda e: (e["artist_name"], e["title"]))
        else:  # recent
            entries.sort(key=lambda e: e["cached_at"], reverse=True)
        return entries[:max(0, limit)]

    def new_songs(self, limit: int = 24, days: int = NEW_SONG_DAYS) -> List[Dict[str, Any]]:
        """新歌榜：最近加入曲庫的歌，最新排最前面。"""
        cutoff = time.time() - days * 86400
        entries = self.entries()
        entries.sort(key=lambda e: e["cached_at"], reverse=True)
        picked = entries[:max(0, limit)]
        for e in picked:
            e["is_new"] = e["cached_at"] >= cutoff
        return picked

    def recommendations(self, limit: int = 12) -> List[Dict[str, Any]]:
        """
        推薦歌單：從「你唱過什麼」推回「接下來唱什麼」。

        四種來源依序取，每首都附上推薦理由（點歌機的推薦要講得出道理，
        不然使用者只會覺得是亂排的清單）：
        1. 好久沒唱的老朋友 —— 唱過兩次以上，但最近沒再點。
        2. 同一位歌手的其他歌 —— 最近唱過的歌手，曲庫裡還沒唱過的歌。
        3. 剛加入曲庫還沒唱過的新歌。
        4. 補位：最新加入的歌。
        """
        entries = self.entries()
        if not entries:
            return []

        picks: List[Dict[str, Any]] = []
        seen = set()

        def add(entry: Dict[str, Any], reason: str, tag: str) -> None:
            if entry["song_id"] in seen or len(picks) >= limit:
                return
            seen.add(entry["song_id"])
            picks.append({**entry, "reason": reason, "reason_tag": tag})

        # 1. 好久沒唱的老朋友
        old_friends = [e for e in entries if e["plays"] >= 2]
        old_friends.sort(key=lambda e: e["last_played"] or "")
        for e in old_friends[:max(1, limit // 3)]:
            add(e, "好久沒唱了，再來一次？", "old_friend")

        # 2. 最近唱過的歌手的其他歌
        recent_artists: List[str] = []
        if self.song_history is not None:
            try:
                sung_ids = {h.get("song_id") for h in self.song_history.recent(30)}
            except Exception:
                sung_ids = set()
            for e in entries:
                if e["song_id"] in sung_ids and e["artist_name"] not in recent_artists:
                    recent_artists.append(e["artist_name"])
        for artist in recent_artists:
            if artist == UNKNOWN_ARTIST:
                continue
            same = [e for e in entries
                    if e["artist_name"] == artist and e["plays"] == 0]
            same.sort(key=lambda e: e["cached_at"], reverse=True)
            for e in same[:2]:
                add(e, f"你唱過 {artist}，這首還沒唱過", "same_artist")

        # 3. 還沒唱過的新歌
        never = [e for e in entries if e["plays"] == 0]
        never.sort(key=lambda e: e["cached_at"], reverse=True)
        for e in never:
            add(e, "剛加入曲庫，還沒開嗓過", "never_played")

        # 4. 補位
        newest = sorted(entries, key=lambda e: e["cached_at"], reverse=True)
        for e in newest:
            add(e, "曲庫新歌", "newest")

        return picks[:limit]
