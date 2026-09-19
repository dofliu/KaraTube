"""
歌星查歌：注音首字查歌手，選一位就翻開他的歌單

商用點歌機（錢櫃、好樂迪、金嗓、音圓）的實體鍵盤上有三條路：歌名注音、歌名字數、
**歌星**。前兩條已經在 `song_index.py`，這支補最後一條 —— 而且它是包廂裡最常用的
那一條：唱到一半想不起歌名，但一定記得「我要唱張學友」。

難的不是鍵盤（鍵盤跟歌名查歌共用同一套），難的是**同一個人在曲庫裡有好幾種寫法**：

1. **一個人不該在歌手清單裡出現三次。** 曲庫的歌手名是從頻道名與標題還原的
   （`library.normalize_artist`），同一位歌手因此會長出「周杰倫」「Jay Chou」
   「周杰倫 Jay Chou」三種字串。照原字串建索引，歌手清單就會出現三個周杰倫，
   每一個手上只有他一部分的歌 —— 使用者按 ㄓㄐㄌ 進去看到兩首，
   會以為曲庫裡就只有這兩首，然後回頭去 YouTube 重下載一份已經有的歌。
   所以先把同一個人的多種寫法併成一位（`_cluster`），漢字寫法與英文寫法
   都進索引：按 ㄓㄐㄌ 或按 J C 都會找到同一位，而且是同一份完整歌單。

2. **合併要保守，寧可漏併也不要錯併。** 只在「整段漢字完全相同」或
   「整段英文完全相同」時併，不做子字串比對 ——「張惠妹」與「張惠」不是同一個人，
   而錯併的代價是歌單裡混進別人的歌（使用者會當成系統壞了），
   比漏併（清單裡多一筆）嚴重得多。而且**漢字不同就是不同人**：
   兩位歌手的英文名剛好一樣時，漢字那一關會擋下來。

3. **「未知歌手」不進鍵盤，但也不能消失。** 它不是名字，按注音永遠不該撈到它；
   可是那些歌是真的存在的，所以它照樣列出來（排最後、標成未知），
   使用者看得到「有 12 首歌認不出歌手」，而不是納悶曲庫數字為什麼對不起來。

4. **按到只剩一位，就直接把歌單翻開。** 商用機上那一下多餘的點擊沒有任何資訊量：
   畫面上只剩一位歌手，使用者要的一定是他。
"""
import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend.services.library import UNKNOWN_ARTIST
from backend.services.song_index import (
    BOPOMOFO_AVAILABLE,
    BOPOMOFO_ROWS,
    is_key_query,
    match_keys,
    normalize_query,
    title_keys,
)

logger = logging.getLogger("KaraTube.ArtistIndex")

_HAN_RE = re.compile(r"[一-鿿㐀-䶿]")
_BOPOMOFO_RE = re.compile(r"[ㄅ-ㄯ]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z0-9']+")

# 合併用的比對片段至少要這麼長。一個字母（「A」）誰都可能撞到，
# 拿它當「同一個人」的證據會把不相干的歌手併在一起。
_MIN_TOKEN = 2


def name_parts(name: str) -> Tuple[str, str]:
    """
    歌手名拆成（漢字段, 英文段）。「周杰倫 Jay Chou」→（"周杰倫", "Jay Chou"）。

    拆開才能各自建索引：漢字那段走注音（ㄓㄐㄌ），英文那段走首字母（J C）。
    不拆的話按 ㄓㄐㄌ 會在第四格撞到英文單字，鍵盤整片變灰。
    """
    text = name or ""
    han = "".join(ch for ch in text if _HAN_RE.match(ch))
    latin = " ".join(_LATIN_WORD_RE.findall(text))
    return han, latin


def _norm_latin(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def merge_tokens(name: str) -> List[str]:
    """
    這個寫法拿哪幾段字去跟別的寫法比對「是不是同一個人」。

    漢字段與英文段各算一段，而且比的是**整段**，不是子字串 ——
    「張惠妹」與「張惠」的漢字段不相等，所以不會被併成同一位。
    """
    han, latin = name_parts(name)
    tokens: List[str] = []
    if len(han) >= _MIN_TOKEN:
        tokens.append("han:" + han)
    lat = _norm_latin(latin)
    if len(lat) >= _MIN_TOKEN:
        tokens.append("lat:" + lat)
    return tokens


def name_keys(name: str) -> List[List[str]]:
    """一個歌手寫法 → 可以拿來比對的首碼序列（漢字一組、英文一組）。"""
    han, latin = name_parts(name)
    variants: List[List[str]] = []
    for part in (han, latin):
        keys = title_keys(part) if part else []
        if keys and keys not in variants:
            variants.append(keys)
    return variants


class _Clusters:
    """把「同一個人的不同寫法」併起來的 union-find。

    併之前先看漢字：合併後的群裡出現兩種不同漢字名，就是兩個人（英文名撞名），
    這一併不做。
    """

    def __init__(self) -> None:
        self.parent: Dict[str, str] = {}
        self.hans: Dict[str, set] = {}

    def add(self, name: str) -> None:
        if name in self.parent:
            return
        self.parent[name] = name
        han, _ = name_parts(name)
        self.hans[name] = {han} if len(han) >= _MIN_TOKEN else set()

    def find(self, name: str) -> str:
        root = name
        while self.parent[root] != root:
            self.parent[root] = self.parent[self.parent[root]]
            root = self.parent[root]
        return root

    def union(self, a: str, b: str) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return True
        merged = self.hans[ra] | self.hans[rb]
        if len(merged) > 1:   # 漢字名不同 ＝ 不同人，英文名撞名不算數
            return False
        self.parent[rb] = ra
        self.hans[ra] = merged
        return True


def _display_name(names: Sequence[str], counts: Dict[str, int]) -> str:
    """一群寫法裡挑一個當顯示名。

    介面是中文的，所以有漢字的優先；同樣有漢字就挑短的（「周杰倫」勝過
    「周杰倫 Jay Chou」）。全都是英文時挑歌最多的那個寫法。
    """
    han_names = [n for n in names if _HAN_RE.search(n)]
    pool = han_names or list(names)
    return sorted(pool, key=lambda n: (len(n), -counts.get(n, 0), n))[0]


class ArtistFinder:
    """
    歌星查歌。歌手清單從 `SongFinder.entries()` 長出來 ——

    共用同一份曲庫與同一份歌名索引，「曲庫瀏覽看得到、歌星查歌查不到」
    這種兩套清單對不起來的狀況才不會發生；歌單裡的歌名也跟著是萃出來的
    歌名本體，不是整串 YouTube 標題。
    """

    def __init__(self, finder):
        self.finder = finder

    # --- 歌手清單 ---

    def groups(self) -> List[Dict[str, Any]]:
        """曲庫裡的歌手（同一個人的不同寫法已經併好）。"""
        entries = self.finder.entries()

        order: List[str] = []
        counts: Dict[str, int] = {}
        thumbs: Dict[str, str] = {}
        songs_by_name: Dict[str, List[Dict[str, Any]]] = {}
        for e in entries:
            raw = e.get("artist_name") or UNKNOWN_ARTIST
            if raw not in counts:
                order.append(raw)
                counts[raw] = 0
                songs_by_name[raw] = []
            counts[raw] += 1
            songs_by_name[raw].append(e)
            if e.get("thumbnail") and raw not in thumbs:
                thumbs[raw] = e["thumbnail"]

        clusters = _Clusters()
        token_map: Dict[str, List[str]] = {}
        for raw in order:
            clusters.add(raw)
            if raw == UNKNOWN_ARTIST:
                continue   # 「未知歌手」不是名字，不參與合併
            for token in merge_tokens(raw):
                token_map.setdefault(token, []).append(raw)

        # 先用漢字段併（同名同人，最安全），再用英文段併（會被漢字那關擋）
        for prefix in ("han:", "lat:"):
            for token, names in token_map.items():
                if not token.startswith(prefix):
                    continue
                for other in names[1:]:
                    clusters.union(names[0], other)

        grouped: Dict[str, List[str]] = {}
        for raw in order:
            grouped.setdefault(clusters.find(raw), []).append(raw)

        out: List[Dict[str, Any]] = []
        for members in grouped.values():
            name = _display_name(members, counts)
            aliases = [m for m in members if m != name]
            unknown = name == UNKNOWN_ARTIST
            variants: List[List[str]] = []
            if not unknown:
                for member in members:
                    for keys in name_keys(member):
                        if keys not in variants:
                            variants.append(keys)
            songs: List[Dict[str, Any]] = []
            for member in members:
                songs.extend(songs_by_name.get(member, []))
            songs.sort(key=lambda s: (-int(s.get("plays") or 0),
                                      s.get("core_title") or s.get("title") or ""))
            out.append({
                "id": name,
                "name": name,
                "aliases": aliases,
                "count": len(songs),
                "thumbnail": next((thumbs[m] for m in members if m in thumbs), ""),
                "unknown": unknown,
                "keys": variants,
                "songs": songs,
            })

        # 歌多的排前面，同樣多首照名字排（順序才穩定）；未知歌手一律墊底
        out.sort(key=lambda g: (g["unknown"], -g["count"], g["name"]))
        return out

    # --- 查詢 ---

    def facets(self) -> Dict[str, Any]:
        """歌星鍵盤要的資料：注音鍵位、幾位歌手、幾首歌。"""
        groups = self.groups()
        known = [g for g in groups if not g["unknown"]]
        return {
            "bopomofo_available": BOPOMOFO_AVAILABLE,
            "rows": BOPOMOFO_ROWS,
            "artist_count": len(known),
            "unknown_count": sum(g["count"] for g in groups if g["unknown"]),
            "total": sum(g["count"] for g in groups),
        }

    @staticmethod
    def _match(group: Dict[str, Any], keys_query: Sequence[str],
               text_query: str) -> Optional[Tuple[int, str]]:
        """這位歌手有沒有被查到？回（命中位置, 命中方式），沒中回 None。"""
        if keys_query:
            hits = [pos for keys in group["keys"]
                    if (pos := match_keys(keys_query, keys)) is not None]
            if hits:
                return min(hits), "keys"
        if text_query:
            for cand in [group["name"]] + list(group["aliases"]):
                where = cand.lower().find(text_query)
                if where >= 0:
                    return where, "text"
        return None

    @staticmethod
    def _next_keys(matched: List[Dict[str, Any]],
                   keys_query: Sequence[str], raw_query: str) -> List[str]:
        """這一串後面再按哪些鍵還有歌手。直接打字時回空清單（算不出來）。"""
        if raw_query and not keys_query:
            return []
        at = len(keys_query)
        out: List[str] = []
        for group in matched:
            if group.get("unknown"):
                continue
            for keys in group["keys"]:
                starts = ([match_keys(keys_query, keys)] if keys_query else [0])
                for start in starts:
                    if start is None or start + at >= len(keys):
                        continue
                    for sym in keys[start + at]:
                        if sym not in out:
                            out.append(sym)
        return sorted(out)

    def search(self, query: str = "", artist_id: str = "",
               limit: int = 60) -> Dict[str, Any]:
        """
        查歌手，並把（選中的那位／查到的這些）歌手的歌一起回傳。

        查詢字串跟歌名查歌同一套規則：注音符號走首碼、純英數兩種都試、
        含漢字就直接比字。歌手清單與歌單一起回，是因為使用者要的是歌不是名字 ——
        **按到只剩一位歌手時自動選起來**，那一下點擊沒有任何資訊量。
        """
        groups = self.groups()
        text = (query or "").strip()
        keys_query = normalize_query(text) if is_key_query(text) else []
        text_query = text.lower() if text and not _BOPOMOFO_RE.search(text) else ""

        matched: List[Dict[str, Any]] = []
        for group in groups:
            if not text:
                matched.append({**group, "match_pos": 0, "match_kind": "all"})
                continue
            hit = self._match(group, keys_query, text_query)
            if hit is None:
                continue
            matched.append({**group, "match_pos": hit[0], "match_kind": hit[1]})
        matched.sort(key=lambda g: (g["match_pos"], g["unknown"],
                                    -g["count"], g["name"]))

        wanted = (artist_id or "").strip()
        selected = next((g for g in matched if g["id"] == wanted), None)
        if selected is None and wanted:
            # 選了一位歌手之後又多按一個注音，那位歌手可能已經不在結果裡了。
            # 照樣把他找回來：使用者看得到自己選的是誰，才知道要退一格。
            selected = next((g for g in groups if g["id"] == wanted), None)
            if selected is not None:
                selected = {**selected, "match_pos": 0, "match_kind": "picked"}
        auto = False
        if selected is None and text and len(matched) == 1 and not matched[0]["unknown"]:
            selected, auto = matched[0], True

        songs: List[Dict[str, Any]] = []
        if selected is not None:
            songs = list(selected["songs"])
        else:
            for group in matched:
                songs.extend(group["songs"])
        limit = max(0, min(int(limit or 0), 300))

        def strip(group: Dict[str, Any]) -> Dict[str, Any]:
            # 歌單另外回傳，清單裡就不用再夾一份（幾百首歌會讓這包 JSON 大得離譜）
            return {k: v for k, v in group.items() if k not in ("songs", "keys")}

        return {
            "artists": [strip(g) for g in matched],
            "artist_total": len(matched),
            "selected": strip(selected) if selected is not None else None,
            "auto_selected": auto,
            "songs": songs[:limit],
            "song_total": len(songs),
            "next_keys": self._next_keys(matched, keys_query, text),
            "query": text,
            "keys": keys_query,
            "bopomofo_available": BOPOMOFO_AVAILABLE,
            "library_artists": sum(1 for g in groups if not g["unknown"]),
            "library_total": sum(g["count"] for g in groups),
        }
