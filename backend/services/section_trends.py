"""
跨場次段落趨勢（這個人在這首歌一向強在哪一段）

單次演唱的段落評分回答「這一次哪一段唱壞了」，但那一句話每次都不一樣 ——
今天副歌掉了可能只是這一次沒接上氣。真正能拿來練歌的是跨場次的那一句：
**「這首歌的副歌 2 你一向掉 9 個百分點」**。唱三次以上才有資格講「一向」。

商用機的對應物是 JOYSOUND 的「分析採点」與全民K歌的個人歷史曲線；
這裡做的是同一件事的單機版，資料來源就是 `score_history.json` 裡的每一筆成績。

## 為什麼不是「把每次的段落命中率平均起來就好」

那樣算出來的是「這個人的嗓子好不好」，不是「哪一段是弱點」：

* **嗓音狀況會整體平移**。感冒那天每一段都掉 15 個百分點，平均下來
  每一段看起來都是弱點；狀況好的那天每一段都是強項。所以每一場先減去
  **那一場自己的平均**，比較的是一場演唱的「形狀」而不是「高度」。
* **一次不算趨勢**。平均值不分「三次都掉 9 分」與「兩次正常、一次掉 27 分」，
  但這兩件事該講的話完全不同（練這一段 vs 那天狀況不好）。所以除了平均偏差
  還要求**方向一致性**：至少三分之二的場次同一個方向。
* **曲式會變**。歌曲重新處理過（換模型、歌詞來源變了）之後 `chorus_detector`
  切出來的段落可能不一樣，「副歌 2」在新舊兩版指的不是同一段。
  所以只比對**段落組成相同**的場次，曲式一變就從那一場重新累積。

純資料邏輯：不碰檔案、不碰網路、沒有狀態，所以能直接單元測試。
`ScoreHistory` 只負責把某首歌某個人的歷史成績挑出來餵進來。
"""
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 少於這麼多場不談「一向」。三場是「重複」的最小證據量 ——
# 兩場只能說「上次和這次」，那是比較，不是趨勢。
MIN_PERFORMANCES = 3

# 某一段至少要在這麼多場裡被評到分才敢點名。
# 段落評分本身有「音符不足 0.5 秒不評分」的守門，所以同一首歌的不同場次
# 可評分的段落集合可能略有出入（有一次前奏太長吃掉了主歌 1 的開頭）。
MIN_SECTION_APPEARANCES = 3

# 平均偏差要超過這個值才點名。與單場段落評分的 MIN_SECTION_SPREAD 同值
# （frontend/js/section-scorer.js），「值得講出來的差距」在兩邊是同一個手感。
MIN_TREND_MARGIN = 0.05

# 方向一致性門檻：偏差與平均同號的場次比例。2/3 = 三場裡至少兩場同方向。
MIN_CONSISTENCY = 2.0 / 3.0

# 只看最近這麼多場。唱了五十次的歌，半年前的唱法不能拿來說「一向」——
# 而且人真的會進步，舊資料會把進步稀釋掉。
MAX_TRACKED_PERFORMANCES = 20

# 一場至少要有這麼多可評分的段落才進得了趨勢分析。
# 只有一段可比的話「減去自己的平均」恆等於 0，那一場不帶任何形狀資訊。
MIN_GRADED_SECTIONS = 2

# 判斷進步／退步需要的最少場次與最小幅度。
# 三場的斜率太容易被單一場次帶著走，所以比點名弱點多要求一場。
MIN_TREND_DIRECTION_PERFORMANCES = 4
MIN_DIRECTION_DELTA = 0.05


def _round3(value: float) -> float:
    return round(value * 1000) / 1000


def graded_sections(entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    """一筆成績裡可用於趨勢的段落。看不懂的資料一律略過而不是補 0。"""
    rows = entry.get("sections")
    if not isinstance(rows, list):
        return []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = str(row.get("label") or "").strip()
        if not label:
            continue
        try:
            accuracy = float(row.get("accuracy"))
        except (TypeError, ValueError):
            continue
        if not 0.0 <= accuracy <= 1.0:
            continue
        out.append({"label": label, "accuracy": accuracy})
    return out


def signature_of(sections: Sequence[Dict[str, Any]]) -> Tuple[str, ...]:
    """
    這一場的曲式指紋：可評分段落的標籤依演唱順序排成一列。

    只比對指紋相同的場次。段落標籤（「副歌 2」）是依曲式順序編號出來的，
    歌曲重新處理後多切出一段主歌，後面所有編號都會往後挪一位 ——
    那時候拿「副歌 2」跨版本相加，等於把兩段不同的歌混成一筆統計。
    """
    return tuple(row["label"] for row in sections)


def _shape_of(sections: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    """一場演唱的「形狀」：每段命中率減去這一場自己的平均。"""
    # 不用音符數加權：加權的話最長的那一段（通常是副歌）會主導基準線，
    # 等於拿副歌跟副歌自己比，永遠顯示不出副歌是弱點。
    mean = sum(row["accuracy"] for row in sections) / len(sections)
    return {row["label"]: row["accuracy"] - mean for row in sections}


def _linear_slope(values: Sequence[float]) -> float:
    """最小平方法的斜率（x = 0,1,2,…）。只用來看整體在進步還是在退步。"""
    n = len(values)
    mean_x = (n - 1) / 2.0
    mean_y = sum(values) / n
    denom = sum((i - mean_x) ** 2 for i in range(n))
    if denom == 0:
        return 0.0
    return sum((i - mean_x) * (v - mean_y) for i, v in enumerate(values)) / denom


def compute_trend(entries: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """
    把同一首歌、同一位演唱者的歷史成績（由舊到新）濃縮成一份趨勢。

    回傳的 `status`：
      * `none` —— 沒有任何一場帶著可用的段落資料（1.7.0 之前的舊紀錄）。
      * `insufficient` —— 有資料但場次不夠，附上「還差幾場」讓前端講得出來。
      * `ok` —— 可以下結論。`home` / `weak` 仍可能是 None（唱得很平均，
        或方向不一致），那代表「沒有值得點名的段落」而不是「沒資料」。
    """
    usable = [e for e in entries if isinstance(e, dict)]
    shaped: List[Dict[str, Any]] = []
    for entry in usable:
        sections = graded_sections(entry)
        if len(sections) < MIN_GRADED_SECTIONS:
            continue
        shaped.append({
            "entry": entry,
            "sections": sections,
            "signature": signature_of(sections),
        })

    if not shaped:
        return {"status": "none", "performances": 0,
                "needed": MIN_PERFORMANCES, "sections": [],
                "home": None, "weak": None, "direction": None}

    recent = shaped[-MAX_TRACKED_PERFORMANCES:]
    # 以最新一場的曲式為準：曲式變了就是從那一場開始重新累積，
    # 不是拿舊曲式的多數決把最新的那一場排除掉（那樣新資料永遠進不來）。
    signature = recent[-1]["signature"]
    comparable = [s for s in recent if s["signature"] == signature]
    skipped = len(recent) - len(comparable)

    performances = len(comparable)
    if performances < MIN_PERFORMANCES:
        return {
            "status": "insufficient",
            "performances": performances,
            "needed": MIN_PERFORMANCES - performances,
            "skipped_layout_changed": skipped,
            "sections": [],
            "home": None,
            "weak": None,
            "direction": None,
        }

    # 每段收集「每一場的偏差」與命中率
    samples: Dict[str, List[float]] = {}
    accuracies: Dict[str, List[float]] = {}
    for shot in comparable:
        shape = _shape_of(shot["sections"])
        for row in shot["sections"]:
            samples.setdefault(row["label"], []).append(shape[row["label"]])
            accuracies.setdefault(row["label"], []).append(row["accuracy"])

    rows: List[Dict[str, Any]] = []
    for label in signature:  # 依演唱順序，不是依強弱 —— 長條圖要照歌的順序讀
        deltas = samples.get(label, [])
        if not deltas:
            continue
        mean_delta = sum(deltas) / len(deltas)
        # 一致性：與平均同方向的場次比例。偏差恰好為 0 的場次兩邊都不算，
        # 它沒有表態（整場唱得完全平均時每一段都是 0）。
        agree = sum(1 for d in deltas if (d > 0) == (mean_delta > 0) and d != 0)
        consistency = agree / len(deltas)
        accs = accuracies.get(label, [])
        rows.append({
            "label": label,
            "appearances": len(deltas),
            "mean_accuracy": _round3(sum(accs) / len(accs)),
            "mean_delta": _round3(mean_delta),
            "consistency": _round3(consistency),
            # 夠不夠格被點名（場次數、幅度、方向一致性三道）
            "named": (
                len(deltas) >= MIN_SECTION_APPEARANCES
                and abs(_round3(mean_delta)) >= MIN_TREND_MARGIN
                and consistency >= MIN_CONSISTENCY
            ),
        })

    nameable = [r for r in rows if r["named"]]
    # 同分時偏好先唱到的那一段（rows 已依演唱順序），結論才穩定
    home = max(nameable, key=lambda r: r["mean_delta"], default=None)
    weak = min(nameable, key=lambda r: r["mean_delta"], default=None)
    if home is not None and home["mean_delta"] <= 0:
        home = None
    if weak is not None and weak["mean_delta"] >= 0:
        weak = None
    # 只有一段夠格時，它只能當強項或弱項的其中一邊（正負號決定），
    # 不能同時是「主場」與「待加強」—— 那句話自相矛盾。
    if home is not None and weak is not None and home["label"] == weak["label"]:
        weak = None

    direction, delta_accuracy = _direction(comparable)

    last = comparable[-1]["entry"]
    return {
        "status": "ok",
        "performances": performances,
        "skipped_layout_changed": skipped,
        "sections": rows,
        "home": home,
        "weak": weak,
        "direction": direction,
        "delta_accuracy": delta_accuracy,
        "last_sung_at": str(last.get("sung_at") or ""),
        "title": str(last.get("title") or ""),
        "song_id": str(last.get("song_id") or ""),
        "singer": str(last.get("singer") or ""),
    }


def _direction(comparable: Sequence[Dict[str, Any]]) -> Tuple[Optional[str], Optional[float]]:
    """整體是在進步還是在退步（看每場平均命中率的迴歸斜率）。"""
    if len(comparable) < MIN_TREND_DIRECTION_PERFORMANCES:
        return None, None
    means = [sum(r["accuracy"] for r in s["sections"]) / len(s["sections"])
             for s in comparable]
    # 斜率乘上跨越的場次數 = 這一段期間的總變化。用總變化而不是斜率本身下判斷，
    # 因為「每場進步 1 個百分點」在四場與二十場是完全不同的兩件事。
    total = _linear_slope(means) * (len(means) - 1)
    total = _round3(total)
    if total >= MIN_DIRECTION_DELTA:
        return "improving", total
    if total <= -MIN_DIRECTION_DELTA:
        return "slipping", total
    return "steady", total
