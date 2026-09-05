"""
副歌偵測與段落切分 (Chorus & Section Detection)

商用 KTV 點歌機都有「副歌重播 / A-B 循環練唱」：按一下就把最想練的那一段
圈起來反覆播。難點不在播放，而在「副歌在哪裡」—— 沒有人會替每首歌手動標記。

作法：純文字重複結構分析，不碰音訊。
歌詞時間軸（lyrics.json）在流水線裡已經對齊好了，而副歌的定義本來就是
「整段歌詞重複出現的區塊」。拿逐行文字找最長、重複最多次的連續區塊，
比做音訊自相似矩陣便宜好幾個數量級，而且找到的行可以直接對回它的時間。

段落切分則靠「唱句之間的空白」：前奏、間奏、尾奏就是沒有歌詞的長靜默，
主歌與副歌之間也會有換氣的短停頓。切出來的段落讓使用者一鍵跳到任一段練唱。

全部是純函數：不讀檔、不碰網路、不依賴第三方套件，所以單元測試跑得動。
"""
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 比對前先只留下有意義的字元。標點、空白、全形符號在不同來源的 LRC 裡寫法不一，
# 留著會讓「同一句」被判成兩句不同的歌詞。CJK 在 \w 裡算文字，不會被吃掉。
_STRIP_PATTERN = re.compile(r"[\s\W_]+", re.UNICODE)

MIN_CHORUS_LINES = 2        # 單行的重複多半是口白或語助詞（喔喔喔），不算副歌
MIN_CHORUS_REPEATS = 2
MIN_CHORUS_SECONDS = 5.0    # 太短的循環聽起來像跳針
MAX_CHORUS_SECONDS = 100.0  # 超過這個長度多半是抓到「整段主歌+副歌」而不是副歌
FALLBACK_MIN_REPEATS = 3    # 找不到多行區塊時，重複 3 次以上的單行才當副歌鉤子
FALLBACK_MIN_SECONDS = 2.5  # 單行鉤子本來就短，沿用 5 秒門檻等於整條退路都作廢

SECTION_GAP_SECONDS = 6.0   # 唱句之間空這麼久就算換段（間奏）
INTRO_MIN_SECONDS = 5.0     # 第一句之前要空這麼久才值得標成「前奏」
OUTRO_MIN_SECONDS = 5.0
PREVIEW_CHARS = 18


# ----------------------------------------------------------------------
# 小工具
# ----------------------------------------------------------------------

def normalize_line(text: Any) -> str:
    """把一行歌詞正規化成比對用的字串。空字串代表這行沒有可比對的內容。"""
    return _STRIP_PATTERN.sub("", str(text or "")).lower()


def _line_time(line: Dict[str, Any], key: str, fallback: float = 0.0) -> float:
    try:
        return float(line.get(key, fallback))
    except (TypeError, ValueError):
        return fallback


def _valid_lines(lyrics: Optional[Sequence[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """濾掉不是 dict、或時間軸壞掉（end <= start）的行，並依時間排序。"""
    lines = []
    for item in lyrics or []:
        if not isinstance(item, dict):
            continue
        start = _line_time(item, "start", -1.0)
        end = _line_time(item, "end", -1.0)
        if start < 0 or end <= start:
            continue
        lines.append(item)
    return sorted(lines, key=lambda ln: _line_time(ln, "start"))


def _preview(text: Any) -> str:
    s = str(text or "").strip()
    return s if len(s) <= PREVIEW_CHARS else s[:PREVIEW_CHARS] + "…"


# ----------------------------------------------------------------------
# 副歌偵測
# ----------------------------------------------------------------------

def _candidate_signatures(norms: Sequence[str], min_lines: int) -> List[Tuple[str, ...]]:
    """
    列出所有「在歌詞裡出現過至少兩次」的連續區塊。

    對每組相同起頭的行 (i, j) 往下延伸到不再相同為止，得到這一對的最長共同區塊；
    再把它的每個前綴也收進來 —— 四行副歌可能只有後兩行在尾聲又多唱了一次，
    那個兩行版本的重複次數比較高，值得一起參加評分。
    """
    n = len(norms)
    sigs = set()
    for i in range(n):
        if not norms[i]:
            continue
        for j in range(i + 1, n):
            if norms[j] != norms[i]:
                continue
            length = 0
            # i + length < j 保證兩次出現不重疊
            while (i + length < j and j + length < n
                   and norms[i + length] and norms[i + length] == norms[j + length]):
                length += 1
            for size in range(min_lines, length + 1):
                sigs.add(tuple(norms[i:i + size]))
    return sorted(sigs, key=len, reverse=True)


def _occurrences(norms: Sequence[str], signature: Tuple[str, ...]) -> List[int]:
    """不重疊地掃出這個區塊出現的所有起始行號。"""
    n, m = len(norms), len(signature)
    found: List[int] = []
    i = 0
    while i + m <= n:
        if tuple(norms[i:i + m]) == signature:
            found.append(i)
            i += m
        else:
            i += 1
    return found


def _confidence(lines: int, repeats: int) -> float:
    """
    0~1 的把握程度，給 UI 決定要不要主動推薦「一鍵副歌」。

    重複次數比區塊長度更能證明「這是副歌」，所以權重給 0.6；
    四行以上的區塊已經是完整一段，再長也不加分。
    """
    repeat_score = min(1.0, (repeats - 1) / 3.0)
    length_score = min(1.0, lines / 4.0)
    return round(0.6 * repeat_score + 0.4 * length_score, 3)


def _build_chorus(lines: List[Dict[str, Any]], starts: List[int],
                  size: int, min_seconds: float) -> Optional[Dict[str, Any]]:
    """把「第幾行開始、共幾行」的重複區塊換算成帶時間的副歌資料。"""
    spans = []
    for p in starts:
        start = _line_time(lines[p], "start")
        end = _line_time(lines[p + size - 1], "end")
        if end <= start:
            continue
        spans.append({"start": round(start, 3), "end": round(end, 3)})
    if not spans:
        return None

    primary = spans[0]
    span_seconds = primary["end"] - primary["start"]
    if not (min_seconds <= span_seconds <= MAX_CHORUS_SECONDS):
        return None

    head = starts[0]
    return {
        "start": primary["start"],
        "end": primary["end"],
        "lines": size,
        "repeats": len(spans),
        "text": "\n".join(str(lines[head + k].get("text", "")).strip() for k in range(size)),
        "occurrences": [dict(idx=i, **span) for i, span in enumerate(spans)],
        "confidence": _confidence(size, len(spans)),
    }


def detect_chorus(lyrics: Optional[Sequence[Dict[str, Any]]],
                  min_lines: int = MIN_CHORUS_LINES,
                  min_repeats: int = MIN_CHORUS_REPEATS,
                  min_seconds: float = MIN_CHORUS_SECONDS) -> Optional[Dict[str, Any]]:
    """
    從對齊好的歌詞找出副歌。找不到就回傳 None（UI 退回手動設 A-B 點）。

    排序原則：先看區塊涵蓋的總行數（重複次數 × 每次行數），
    同分時偏好「一次比較長」的區塊 —— 完整四行的副歌比兩行的鉤子更像使用者要練的那段。
    """
    lines = _valid_lines(lyrics)
    if len(lines) < 2:
        return None
    norms = [normalize_line(ln.get("text")) for ln in lines]

    ranked = []
    for sig in _candidate_signatures(norms, max(1, min_lines)):
        starts = _occurrences(norms, sig)
        if len(starts) < min_repeats:
            continue
        size = len(sig)
        ranked.append((len(starts) * size, size, len(starts), -starts[0], sig, starts))
    ranked.sort(reverse=True)

    for _, size, _, _, _, starts in ranked:
        chorus = _build_chorus(lines, starts, size, min_seconds)
        if chorus:
            return chorus

    # 多行區塊找不到（口水歌、副歌單句反覆）時，退而求其次找重複很多次的單行鉤子。
    # 單行本來就只有兩三秒，時間門檻要跟著放寬，否則這條退路等於沒有。
    if min_lines > 1:
        return detect_chorus(lyrics, min_lines=1,
                             min_repeats=max(min_repeats, FALLBACK_MIN_REPEATS),
                             min_seconds=FALLBACK_MIN_SECONDS)
    return None


# ----------------------------------------------------------------------
# 段落切分
# ----------------------------------------------------------------------

def _group_blocks(lines: List[Dict[str, Any]], gap_seconds: float) -> List[Tuple[int, int]]:
    """依唱句之間的靜默把歌詞行切成數段，回傳 (起始行, 結束行) 的閉區間。"""
    blocks: List[Tuple[int, int]] = []
    head = 0
    for i in range(1, len(lines)):
        if _line_time(lines[i], "start") - _line_time(lines[i - 1], "end") > gap_seconds:
            blocks.append((head, i - 1))
            head = i
    blocks.append((head, len(lines) - 1))
    return blocks


def _is_chorus_block(block_start: float, block_end: float,
                     chorus: Optional[Dict[str, Any]]) -> bool:
    """副歌的某一次出現整段落在這個區塊裡，這個區塊就是副歌段。"""
    if not chorus:
        return False
    for occ in chorus.get("occurrences", []):
        if occ["start"] >= block_start - 0.01 and occ["end"] <= block_end + 0.01:
            return True
    return False


def detect_sections(lyrics: Optional[Sequence[Dict[str, Any]]],
                    duration: Optional[float] = None,
                    gap_seconds: float = SECTION_GAP_SECONDS,
                    chorus: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """
    把整首歌切成可以一鍵跳轉的段落：前奏 / 主歌 / 副歌 / 間奏 / 尾奏。

    `chorus` 傳 `detect_chorus()` 的結果就會標出副歌段；不傳就全部當主歌。
    `duration` 是歌曲總長度（秒），有給才標得出尾奏。
    """
    lines = _valid_lines(lyrics)
    if not lines:
        return []

    blocks = _group_blocks(lines, gap_seconds)
    sections: List[Dict[str, Any]] = []
    verse_no = chorus_no = interlude_no = 0

    first_start = _line_time(lines[0], "start")
    if first_start >= INTRO_MIN_SECONDS:
        sections.append({"kind": "intro", "label": "前奏",
                         "start": 0.0, "end": round(first_start, 3), "preview": ""})

    for bi, (head, tail) in enumerate(blocks):
        block_start = _line_time(lines[head], "start")
        block_end = _line_time(lines[tail], "end")

        if bi > 0:
            prev_end = _line_time(lines[blocks[bi - 1][1]], "end")
            interlude_no += 1
            sections.append({"kind": "interlude", "label": f"間奏 {interlude_no}",
                             "start": round(prev_end, 3), "end": round(block_start, 3),
                             "preview": ""})

        if _is_chorus_block(block_start, block_end, chorus):
            chorus_no += 1
            kind, label = "chorus", f"副歌 {chorus_no}"
        else:
            verse_no += 1
            kind, label = "verse", f"主歌 {verse_no}"

        sections.append({
            "kind": kind, "label": label,
            "start": round(block_start, 3), "end": round(block_end, 3),
            "preview": _preview(lines[head].get("text")),
        })

    last_end = _line_time(lines[-1], "end")
    if duration and float(duration) - last_end >= OUTRO_MIN_SECONDS:
        sections.append({"kind": "outro", "label": "尾奏",
                         "start": round(last_end, 3), "end": round(float(duration), 3),
                         "preview": ""})

    for idx, section in enumerate(sections):
        section["index"] = idx
    return sections


def analyze_song_structure(lyrics: Optional[Sequence[Dict[str, Any]]],
                           duration: Optional[float] = None) -> Dict[str, Any]:
    """一次算好副歌與段落，給 `/api/songs/{song_id}/sections` 直接回傳。"""
    chorus = detect_chorus(lyrics)
    return {
        "chorus": chorus,
        "sections": detect_sections(lyrics, duration=duration, chorus=chorus),
    }
