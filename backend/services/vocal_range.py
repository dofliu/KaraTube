"""
音域檢測與建議 Key (Vocal Range & Key Advice)

商用點歌機上那一顆「男調／女調」是一個很粗的近似：它假設全世界的男生都比
原唱低 4 個半音、全世界的女生都高 4 個。JOYSOUND 的「音域チェック」與
DAM 的推薦 Key 做的是另一件事 —— **先認識這個人的聲音**，再對每一首歌說
「這首對你偏高，降 2 個」。這一支就是它。

這台機器有一個商用機沒有的優勢：麥克風的音高**每一幀都在偵測**
（`pitch-engine.js` 為了評分本來就要偵測）。所以不必請客人對著機器唱一段
音階做「音域測驗」—— 測驗會被跳過，而唱歌不會。音域是**唱出來的副產品**。

---

**決定一：音域的兩端是「你真的唱住過的音」，不是偵測到的最高與最低。**

自相關法（`detectUserPitch`）會有八度誤判：氣音很多的低音容易被判成低八度。
拿 min/max 當音域的話，整個晚上只要有一幀判錯，音域就寬了 12 個半音，
而那個數字之後會一直在。

所以兩端的定義是**有支撐的那一格**：某個半音格累積到 `EDGE_MIN_RATIO`
（總幀數的 0.5%，且至少 `EDGE_MIN_FRAMES` 幀）才算數。偶發的錯判永遠
到不了那個量，而真的唱得上去的音會在很多首歌裡重複出現。

順帶一提，八度誤判幾乎只往**下**跑（把基頻猜成一半），所以它污染的是下緣，
而下緣正好是建議 Key 裡權重最低的那一端（見決定四）。

**決定二：舒適音域是「你的聲音住在哪裡」，用最窄的那一段。**

上下緣是「碰得到」，不是「唱得舒服」。舒適區的定義是**裝得下 70% 演唱幀數
的最窄連續音域** —— 用百分位數（例如 p20~p85）也可以，但那是一段固定寬度的
切法，遇到音域偏一邊的人會把中心抓歪；最窄窗是資料自己決定寬度。

**決定三：一首歌的高音不是它最高的那個音。**

一首三分鐘的歌，橋段那兩秒的飆高音佔全曲人聲時間不到 1%。拿最高音當
「這首歌要求的高度」的話，包廂裡九成的歌都會被判成「你唱不上去」——
而那句話說多了就沒有人看了。

所以高音的定義是**照時長加權的分位數**：比它高的演唱時間不到 `DEMAND_TAIL`
（3%）的那個音。飆高音自然被排除在外，而副歌整段停在上面的歌則會如實反映。

**決定四：往上超出與往下超出的代價不對稱。**

唱不上去是**唱不出來**（破音、假音、或整句消失）；唱太低是**聲音變小、
變悶**，但句子還在。所以成本函數裡超出上緣的權重是超出下緣的三倍。
不分輕重的話，一首「高音差 2 個、低音差 2 個」的歌會被判成不必調 ——
而實際上降 2 個 Key 是對的。

**決定五：不調是有成本的選項裡最便宜的一個，所以它要贏平手。**

移調有代價：MV 的原唱還在原調（多人模式聽得到）、和聲跟著搬、
而且包廂裡其他人的耳朵記得原本的樣子。所以成本函數加一項 `SHIFT_COST`
乘上移調量，而且建議值要比「不調」好上 `MIN_GAIN` 才開口。
差一點點就叫人去調 Key 的系統，第三次之後就會被無視。

**決定六：資料不夠就說「還在認識你的聲音」，不要給一個猜的數字。**

跟對齊品質徽章同一條原則：沒有資料是「未知」，不是 0。門檻有兩道 ——
總幀數（`MIN_PROFILE_FRAMES`）與**不同的演唱次數**（`MIN_PROFILE_SONGS`）。
只有幀數門檻的話，一首音域很窄的抒情歌唱兩次就會把那首歌的音域
當成這個人的音域，然後拿它去評斷所有其他的歌。

**決定七：身分認暱稱，沒取暱稱的不建檔 —— 跟今晚擂台同一條線。**

「沒取暱稱的所有人算同一個人」在公平輪唱那裡是對的（那裡要的是秩序），
但在這裡是錯的：把一桌八個人的音高混成一份檔案，算出來的「舒適音域」
會寬到涵蓋所有人，於是對每一首歌的建議都是「不必調」—— 一個永遠說
「不必調」的功能等於沒有這個功能，而且看不出它壞了。

**決定八：檔案會遺忘。**

人的聲音會變（暖開了、唱累了、感冒、以及三個月之後），而同一個暱稱在
一台公用機器上更可能是不同的人。所以總幀數有上限，超過就整份等比例縮小
（指數遺忘）：分布的形狀留著，舊資料的份量持續變輕。再加上一顆
「重新認識我的聲音」的重設鍵 —— 那是唯一能瞬間交棒的方法。

存成 cache/vocal_range.json，重開機不會消失。
"""
import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("KaraTube.VocalRange")

# 音高直方圖的合法範圍（半音格）。`pitch-engine.js` 的偵測窗是 65~1200 Hz，
# 換算約 MIDI 36~86；這裡取 C2(36)~C6(84)，兩端各留一點，
# 界外的一律丟掉而不是夾回 —— 夾回會在邊界那一格堆出一座假的山。
MIN_MIDI = 36
MAX_MIDI = 84

# 某一格要有這麼多支撐才算「你真的唱住過這個音」（見決定一）。
# 兩個條件同時要成立：佔總幀數的比例，以及絕對幀數。
#   * 只看比例 —— 資料很少時 0.5% 可能只有 3 幀，一次錯判就成立。
#   * 只看絕對值 —— 唱了一整年之後 40 幀變成雜訊等級，音域會慢慢長寬。
EDGE_MIN_RATIO = 0.005
EDGE_MIN_FRAMES = 40

# 舒適音域要裝下多少比例的演唱幀數（見決定二）。
COMFORT_MASS = 0.70

# 建檔門檻：總幀數與不同的演唱次數（見決定六）。
# 幀數是「持續唱住的幀」，不是播放幀 —— 一首四分鐘的歌大約貢獻 3000~6000 幀。
MIN_PROFILE_FRAMES = 4000
MIN_PROFILE_SONGS = 3

# 遺忘：總幀數超過上限就整份等比例縮小到上限（見決定八）。
MAX_PROFILE_FRAMES = 400_000

# 一首歌的高音／低音是照時長加權、砍掉頭尾這麼多比例之後的那個音（見決定三）。
DEMAND_TAIL = 0.03

# 導唱音符總時長少於這麼多秒就不給建議：這首歌的人聲太少（純音樂、只抓到幾個音），
# 算出來的音域是雜訊。說「沒得算」比說一個數字誠實。
MIN_DEMAND_SECONDS = 20.0

# 建議 Key 的上下限。與 `pitch-engine.js` / 調音台的 ±6 同值 ——
# 建議一個調不到的數字，使用者按下去會發現機器自己夾回 6，
# 而畫面上兩個數字對不起來。
MAX_SHIFT = 6

# 成本函數的三個權重（見決定四、五）。
OVER_WEIGHT = 3.0    # 超出上緣：唱不上去
UNDER_WEIGHT = 1.0   # 低於下緣：聲音變悶
CENTER_WEIGHT = 0.25  # 整首歌的重心離舒適區中央多遠
SHIFT_COST = 0.15   # 每移一個半音的固定代價
MIN_GAIN = 0.75     # 要比「不調」好這麼多才開口建議

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note_name(midi: Optional[float]) -> str:
    """
    MIDI 音高 → 音名（60 → "C4"）。

    音名只在這裡算一次，API 一併回傳給前端 —— 兩邊各算一次的話，
    八度的基準（C4 = 60 還是 middle C = C3）遲早會不一致，
    而畫面上寫「你的音域 C3~A4」與「這首歌最高 A5」對不起來時，
    沒有任何人查得出來是哪一邊錯。
    """
    if midi is None:
        return ""
    try:
        m = int(round(float(midi)))
    except (TypeError, ValueError):
        return ""
    return f"{NOTE_NAMES[m % 12]}{m // 12 - 1}"


def _clean_bins(raw: Any) -> Dict[int, float]:
    """外來的直方圖 → {半音格: 幀數}。界外、負數、非數字一律丟掉。"""
    bins: Dict[int, float] = {}
    if not isinstance(raw, dict):
        return bins
    for key, value in raw.items():
        try:
            midi = int(key)
            count = float(value)
        except (TypeError, ValueError):
            continue
        if midi < MIN_MIDI or midi > MAX_MIDI:
            continue
        if not (count > 0):
            continue
        bins[midi] = bins.get(midi, 0.0) + count
    return bins


def _supported_edges(bins: Dict[int, float], total: float) -> Tuple[Optional[int], Optional[int]]:
    """有支撐的最低／最高格（決定一）。一格都沒撐起來就回 (None, None)。"""
    if total <= 0:
        return None, None
    threshold = max(EDGE_MIN_FRAMES, total * EDGE_MIN_RATIO)
    supported = sorted(m for m, c in bins.items() if c >= threshold)
    if not supported:
        return None, None
    return supported[0], supported[-1]


def _comfort_band(bins: Dict[int, float], total: float) -> Tuple[Optional[int], Optional[int]]:
    """
    裝得下 `COMFORT_MASS` 的最窄連續音域（決定二）。

    做法是最單純的雙指標掃描：右端往外推到累積量足夠，再把左端往內收到不能收，
    記下最窄的一段。格數只有五十上下，寫得漂亮不值得。

    平手時取**比較低**的那一段：人唱歌時往低的那一半比較穩（高音要支撐），
    所以兩段一樣窄的時候，低的那一段更可能是真正的主場。
    """
    if total <= 0:
        return None, None
    need = total * COMFORT_MASS
    keys = sorted(bins.keys())
    if not keys:
        return None, None

    best: Optional[Tuple[int, int]] = None
    best_width = None
    acc = 0.0
    left = 0
    for right in range(len(keys)):
        acc += bins[keys[right]]
        while acc - bins[keys[left]] >= need and left < right:
            acc -= bins[keys[left]]
            left += 1
        if acc >= need:
            width = keys[right] - keys[left]
            if best_width is None or width < best_width:
                best_width = width
                best = (keys[left], keys[right])
    if best is None:
        # 全部加起來都不到 70%（浮點誤差以外不會發生），退回整段
        return keys[0], keys[-1]
    return best


def profile_from_bins(bins: Dict[int, float], songs: int) -> Dict[str, Any]:
    """
    直方圖 → 這個人的音域檔案。純函數，測試直接餵直方圖。

    `ready` 為 False 時**不會**給 low/high —— 給了的話畫面就會顯示它，
    而使用者不會注意到旁邊那行「資料還不夠」。沒得說就什麼都不說。
    """
    total = float(sum(bins.values()))
    songs = max(0, int(songs))
    frames_needed = max(0, MIN_PROFILE_FRAMES - int(total))
    songs_needed = max(0, MIN_PROFILE_SONGS - songs)
    low, high = _supported_edges(bins, total)
    ready = (total >= MIN_PROFILE_FRAMES and songs >= MIN_PROFILE_SONGS
             and low is not None and high is not None)

    info: Dict[str, Any] = {
        "ready": ready,
        "frames": int(total),
        "songs": songs,
        "songs_needed": songs_needed,
        "frames_needed": frames_needed,
    }
    if not ready:
        return info

    comfort_low, comfort_high = _comfort_band(bins, total)
    info.update({
        "low": low,
        "high": high,
        "low_label": note_name(low),
        "high_label": note_name(high),
        "semitones": high - low,
        "comfort_low": comfort_low,
        "comfort_high": comfort_high,
        "comfort_low_label": note_name(comfort_low),
        "comfort_high_label": note_name(comfort_high),
    })
    return info


def demand_from_notes(notes: Any) -> Dict[str, Any]:
    """
    這首歌的導唱音符 → 它要求的音域（決定三）。

    音符來自 `pitch.json`（`{start, end, midi}`），權重是**時長**不是個數：
    照個數算的話，一連串十六分音符的裝飾音會壓過副歌那個撐了四拍的長音，
    而後者才是唱不上去的那一個。
    """
    weights: Dict[int, float] = {}
    total = 0.0
    if isinstance(notes, list):
        for note in notes:
            if not isinstance(note, dict):
                continue
            try:
                midi = float(note.get("midi", 0))
                start = float(note.get("start", 0))
                end = float(note.get("end", 0))
            except (TypeError, ValueError):
                continue
            duration = end - start
            if duration <= 0 or not (MIN_MIDI <= midi <= MAX_MIDI):
                continue
            slot = int(round(midi))
            weights[slot] = weights.get(slot, 0.0) + duration
            total += duration

    if total < MIN_DEMAND_SECONDS or not weights:
        return {"ready": False, "seconds": round(total, 1)}

    keys = sorted(weights.keys())
    tail = total * DEMAND_TAIL

    acc = 0.0
    low = keys[0]
    for m in keys:
        acc += weights[m]
        if acc >= tail:
            low = m
            break

    acc = 0.0
    high = keys[-1]
    for m in reversed(keys):
        acc += weights[m]
        if acc >= tail:
            high = m
            break

    acc = 0.0
    median = keys[0]
    for m in keys:
        acc += weights[m]
        if acc >= total / 2:
            median = m
            break

    return {
        "ready": True,
        "seconds": round(total, 1),
        "low": low,
        "high": high,
        "median": median,
        "low_label": note_name(low),
        "high_label": note_name(high),
        "peak": keys[-1],
        "peak_label": note_name(keys[-1]),
    }


def _shift_cost(shift: int, profile: Dict[str, Any], demand: Dict[str, Any]) -> float:
    """移調 `shift` 個半音之後，這首歌對這個人有多不合身（決定四、五）。"""
    over = max(0, (demand["high"] + shift) - profile["high"])
    under = max(0, profile["low"] - (demand["low"] + shift))
    comfort_center = (profile["comfort_low"] + profile["comfort_high"]) / 2.0
    center_gap = abs((demand["median"] + shift) - comfort_center)
    return (over * OVER_WEIGHT + under * UNDER_WEIGHT
            + center_gap * CENTER_WEIGHT + abs(shift) * SHIFT_COST)


def advise(profile: Dict[str, Any], demand: Dict[str, Any]) -> Dict[str, Any]:
    """
    音域檔案 + 這首歌的要求 → 建議移調幾個半音，以及一句人話。

    回傳的 `status` 決定畫面上要不要出現這一行：
      * `no_profile`  —— 還在認識這個人的聲音（附「還差幾首」）。
      * `no_demand`   —— 這首歌沒有夠多的導唱音符，沒得算。
      * `fit`         —— 合身，不必調。這是**好消息**，所以照樣說出口。
      * `advice`      —— 建議一個非 0 的移調量。
      * `out_of_range`—— **沒有任何一個 Key** 讓這首歌的高音進得來。
        這時候刻意不報成本最低的那個移調量：成本函數的 3:1 權重會一路壓到 −6，
        而那個答案同時錯了兩次（高音還是上不去、低音又被帶到唱不出聲的地方）。
        講的是差距與一個做得到的下一步（用假音，或換一首）。
    """
    if not profile.get("ready"):
        return {
            "status": "no_profile",
            "shift": 0,
            "songs_needed": profile.get("songs_needed", MIN_PROFILE_SONGS),
            "headline": "🎤 還在認識你的聲音",
            "detail": _learning_detail(profile),
        }
    if not demand.get("ready"):
        return {
            "status": "no_demand",
            "shift": 0,
            "headline": "",
            "detail": "這首歌抓到的導唱音符太少，沒辦法判斷高低。",
        }

    shifts = range(-MAX_SHIFT, MAX_SHIFT + 1)
    costs = {s: _shift_cost(s, profile, demand) for s in shifts}
    # 平手取移調量小的那一個：`SHIFT_COST` 已經讓 0 在數值上贏，
    # 這裡的排序是浮點數剛好相等時的最後一道保險。
    best = min(shifts, key=lambda s: (costs[s], abs(s), -s))
    if costs[0] - costs[best] < MIN_GAIN:
        best = 0

    def over_at(shift: int) -> int:
        return max(0, (demand["high"] + shift) - profile["high"])

    def under_at(shift: int) -> int:
        return max(0, profile["low"] - (demand["low"] + shift))

    # 「移調救得了嗎」問的是**有沒有任何一個**移調量讓這首歌整個進得來，
    # 不是「最低成本的那一個好不好」。兩者在一首比音域還寬的歌上會給出
    # 完全不同的答案，而那正是這個判斷存在的理由（見下面 out_of_range）。
    reachable = min(over_at(s) for s in shifts)

    if reachable > 0:
        # 沒有任何一個 Key 讓這首歌的高音進得來。
        #
        # 這時候**不能**報成本最低的那個移調量。成本函數裡「超出上緣」的權重
        # 是「低於下緣」的三倍（那個比重對一首調得進來的歌是對的），
        # 所以遇到一首比音域還寬的歌，它會一路壓到 −6 —— 而那個答案同時
        # 是錯的兩次：高音**還是**上不去，低音又被一起帶到唱不出聲的地方。
        # 使用者會按下去、發現更難唱，然後不再相信這一行。
        #
        # 唯一誠實的說法是講出那個差距，並且給一個做得到的下一步。
        gap = over_at(0)
        floor_note = note_name(demand["high"] - gap) if gap else profile["high_label"]
        detail = (f"這首最高 {demand['high_label']}，你目前唱得住到 {profile['high_label']}"
                  f"（差 {gap} 個半音）。")
        if reachable < gap:
            detail += (f"降到 −{MAX_SHIFT} 也還差 {reachable} 個半音，"
                       "而且低音會被一起帶下去 —— 兩頭都唱不到會比現在更難唱。")
        else:
            detail += "降 Key 會把低音一起帶下去，換不到高音那一端。"
        detail += "這一首建議用假音帶過，或換一首。"
        return {
            "status": "out_of_range",
            "shift": 0,
            "headline": f"🎤 這首歌的高音比你唱住過的最高音高 {gap} 個半音",
            "detail": detail,
            "gap": gap,
            "reachable_gap": reachable,
            "song_high": demand["high_label"],
            "song_low": demand["low_label"],
            "song_ceiling": floor_note,
        }

    if best == 0:
        fits = over_at(0) == 0 and under_at(0) == 0
        detail = (f"最高 {demand['high_label']}、最低 {demand['low_label']}，"
                  f"都在你唱得住的 {profile['low_label']}~{profile['high_label']} 之內。")
        if not fits:
            detail = "低音略低於你常唱的範圍，但降 Key 會把高音一起帶下去，換得不多。"
        return {
            "status": "fit", "shift": 0,
            "headline": "🎤 這首歌在你的音域裡" if fits else "🎤 這首歌不必調 Key",
            "detail": detail,
            "song_high": demand["high_label"], "song_low": demand["low_label"],
        }

    direction = "偏高" if best < 0 else "偏低"
    sign = "−" if best < 0 else "+"
    return {
        "status": "advice",
        "shift": best,
        "headline": f"🎤 這首歌對你{direction}，建議 {sign}{abs(best)} 個 Key",
        "detail": (f"這首最高 {demand['high_label']}、最低 {demand['low_label']}；"
                   f"你唱得住的是 {profile['low_label']}~{profile['high_label']}，"
                   f"常唱的是 {profile['comfort_low_label']}~{profile['comfort_high_label']}。"),
        "song_high": demand["high_label"],
        "song_low": demand["low_label"],
    }


def _learning_detail(profile: Dict[str, Any]) -> str:
    """「還在認識你的聲音」底下那句話要說得出**下一步**，不能只說資料不足。"""
    songs_needed = int(profile.get("songs_needed") or 0)
    if songs_needed > 0:
        return f"再唱 {songs_needed} 首就看得出你的音域（唱什麼都算，不必特地測）。"
    return "再唱一首就看得出你的音域（唱什麼都算，不必特地測）。"


class VocalRange:
    """
    每個人的音高直方圖（跟著暱稱走，整台機器共用一份）。

    跟評分歷史同一邊：音域是**這個人的聲音**，不是這一間包廂的性質，
    所以換包廂唱同一個暱稱拿到的是同一份檔案（對照：今晚擂台是分房的）。
    """

    def __init__(self, data_file: Path):
        self.data_file = Path(data_file)
        self._lock = threading.Lock()
        # 正規化後的暱稱 -> {"name": 顯示用原字串, "bins": {midi: frames}, "songs": n, "updated": iso}
        self._singers: Dict[str, Dict[str, Any]] = {}
        self._load()

    # ---------- 持久化 ----------

    def _load(self):
        if not self.data_file.exists():
            return
        try:
            with open(self.data_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            for key, entry in (data.get("singers") or {}).items():
                if not isinstance(entry, dict):
                    continue
                bins = _clean_bins(entry.get("bins"))
                if not bins:
                    continue
                self._singers[str(key)] = {
                    "name": str(entry.get("name") or key),
                    "bins": bins,
                    "songs": max(0, int(entry.get("songs") or 0)),
                    "updated": entry.get("updated") or "",
                }
        except Exception as e:
            # 壞檔不阻擋開機：音域檔是可以重新累積的東西，
            # 而唱歌本身一秒都不該因為它停下來。
            logger.error(f"讀取音域檔失敗，從空的開始：{e}")
            self._singers = {}

    def _save(self):
        try:
            self.data_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "singers": {
                    key: {
                        "name": entry["name"],
                        # JSON 的鍵一律是字串，讀回來時 `_clean_bins` 會轉回 int
                        "bins": {str(m): round(c, 2) for m, c in entry["bins"].items()},
                        "songs": entry["songs"],
                        "updated": entry["updated"],
                    }
                    for key, entry in self._singers.items()
                }
            }
            with open(self.data_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"寫入音域檔失敗：{e}")

    # ---------- 對外 ----------

    @staticmethod
    def normalize(singer: Any) -> str:
        """暱稱 → 檔案的鍵。前後空白與大小寫不同不該變成兩個人。"""
        return str(singer or "").strip().casefold()

    def submit(self, singer: Any, bins: Any) -> Dict[str, Any]:
        """
        收一次演唱的音高直方圖，併進這個人的檔案。

        回傳併完之後的檔案（前端可以直接拿去顯示「你的音域又寬了」）。
        沒取暱稱的直接回 `recorded: False` 並說明原因（決定七）——
        安靜地丟掉的話，使用者會以為功能壞了。
        """
        key = self.normalize(singer)
        clean = _clean_bins(bins)
        if not key:
            return {"recorded": False, "reason": "anonymous",
                    "message": "取一個暱稱就會開始累積你的音域。"}
        if not clean:
            return {"recorded": False, "reason": "empty",
                    "message": "這一次沒有收到足夠的歌聲。"}

        with self._lock:
            entry = self._singers.get(key)
            if entry is None:
                entry = {"name": str(singer).strip(), "bins": {}, "songs": 0, "updated": ""}
                self._singers[key] = entry
            # 顯示名沿用最後一次唱的寫法：同一個人改了大小寫或空白，
            # 畫面上要跟著他現在的寫法走。
            entry["name"] = str(singer).strip() or entry["name"]
            for midi, count in clean.items():
                entry["bins"][midi] = entry["bins"].get(midi, 0.0) + count
            entry["songs"] += 1
            entry["updated"] = datetime.now().isoformat(timespec="seconds")
            self._decay(entry)
            self._save()
            snapshot = dict(entry["bins"])
            songs = entry["songs"]

        result = profile_from_bins(snapshot, songs)
        result["recorded"] = True
        return result

    @staticmethod
    def _decay(entry: Dict[str, Any]):
        """
        總幀數超過上限就整份等比例縮小（決定八）。

        縮小而不是「丟掉最舊的」：直方圖裡沒有時間，一格裡的幀分不出是哪一天唱的。
        等比例縮小在數學上等價於對每一次演唱做指數加權 —— 形狀留著，
        舊資料的份量每次都變輕一點，而檔案大小有天花板。
        """
        total = sum(entry["bins"].values())
        if total <= MAX_PROFILE_FRAMES:
            return
        factor = MAX_PROFILE_FRAMES / total
        entry["bins"] = {m: c * factor for m, c in entry["bins"].items()
                         if c * factor > 0.01}

    def profile(self, singer: Any) -> Dict[str, Any]:
        """這個人的音域檔案。沒有這個人就回一份「還沒開始」的空檔案。"""
        key = self.normalize(singer)
        with self._lock:
            entry = self._singers.get(key)
            bins = dict(entry["bins"]) if entry else {}
            songs = entry["songs"] if entry else 0
            name = entry["name"] if entry else str(singer or "").strip()
        info = profile_from_bins(bins, songs)
        info["singer"] = name
        info["anonymous"] = not key
        return info

    def advice(self, singer: Any, notes: Any) -> Dict[str, Any]:
        """這個人 + 這首歌的導唱音符 → 建議 Key。"""
        profile = self.profile(singer)
        demand = demand_from_notes(notes)
        result = advise(profile, demand)
        result["profile"] = profile
        result["demand"] = demand
        return result

    def reset(self, singer: Any) -> bool:
        """把某個人的檔案整份刪掉（「重新認識我的聲音」）。"""
        key = self.normalize(singer)
        with self._lock:
            if key not in self._singers:
                return False
            del self._singers[key]
            self._save()
        return True

    def list_singers(self) -> List[Dict[str, Any]]:
        """櫃檯視角：這台機器上有幾份音域檔，各自累積到哪裡。"""
        with self._lock:
            items = [(key, dict(entry["bins"]), entry["songs"], entry["name"], entry["updated"])
                     for key, entry in self._singers.items()]
        out = []
        for _key, bins, songs, name, updated in items:
            info = profile_from_bins(bins, songs)
            info["singer"] = name
            info["updated"] = updated
            out.append(info)
        out.sort(key=lambda x: (not x["ready"], -x["frames"]))
        return out
