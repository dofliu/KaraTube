"""
自動接歌（沒有人點歌時，機器自己接一首）

商用點歌機沒有「安靜」這個狀態：一首唱完、佇列空了，機器會自己接上下一首
（待機歌單、隨選推薦、促銷 MV）。包廂裡真正被那段安靜傷到的是氣氛 ——
一首唱完之後那三十秒沒有聲音，所有人會同時低頭滑手機，而那一桌就散了。

功能本身聽起來只是「隨機挑一首播」，難的全部在**機器什麼時候該閉嘴**：
一台會自己放歌的機器，只要挑錯時機就會變成包廂裡最吵的那個東西。
下面八個決定就是「什麼時候接、接什麼、接了之後算誰的」這三題的答案。

決定一：只從「已經備好的曲庫」挑
    自動接歌永遠不去 YouTube 抓新歌。抓一首要下載＋人聲分離＋歌詞對齊，
    那是幾分鐘的 GPU 工作，而它發生的時機正是「大家在聊天、等下一首」——
    等於機器趁包廂安靜的時候把 CPU 吃光，然後真的有人點歌時那一首跑不動。
    挑快取裡的歌則是零成本：檔案都在，按下去就出聲。

決定二：機器接的歌不算任何人的一首
    不進點唱排行、不進已唱歷史、不佔輪序、不吃點歌額度。

    排行那一條是硬性的，不是風格問題：自動接歌**照排行挑歌**，如果它播出來
    的歌又計進排行，那條排行就變成機器自己的回音 —— 熱門的歌被接得更多、
    被接得更多所以更熱門，三個晚上之後排行榜上只剩機器自己愛放的那五首，
    而且沒有人看得出來是怎麼變成這樣的。

決定三：有人點歌時，看「有沒有人已經唱進去了」再決定要不要當場讓位
    機器挑的那一首開播 45 秒內（大概還在前奏與第一段），真的有人點歌就
    立刻讓位 —— 那首歌本來就只是填空檔的，它沒有資格佔著麥克風。
    超過 45 秒就讓它唱完再換：已經唱到一半的人被切掉，比點歌的人多等
    兩分鐘難堪得多，而且多等的上限是一首歌的長度（可預期）。

    這跟包廂計時的決定一是同一個原則：機器可以決定「下一首播什麼」，
    但不該去停一個正在唱歌的人。

決定四：空了要先等一下下才接（預設 20 秒）
    切歌之後那幾秒鐘，多半有人正握著手機在找下一首。機器在那個時候搶著
    放歌，使用者的下一個動作是按切歌 —— 一個需要人動手關掉的貼心功能
    就是不貼心。等個二十秒還是沒有人點，那才是真的空了。

決定五：連續接幾首沒有人接手就停（預設 3 首）
    「沒有人點歌」最常見的原因不是大家都想聽機器放歌，是**沒有人在了**
    （去廁所、出去買東西、其實已經散場）。所以自動接歌必須有一個會自己
    停下來的理由：連著接三首都沒有人點歌，就安靜下來。
    有人點了一首，計數歸零，機器重新願意接。

    沒有這條的話，一台沒有人的包廂會整夜自己唱歌到隔天早上。

決定六：機器接的歌不會把包廂的錶打開
    包廂計時的自動開錶認的是「第一首歌開始播」，而那句話的原意是
    「這一場開始了」—— 客人還沒進來、機器自己接的那一首不算。
    不擋的話，最糟的情形是沒有人在的包廂自己把三小時的錶按下去。

決定七：時間到、停播中、有東西在跑流水線，一律不接
    散場畫面之後自己放歌，是這個功能最難堪的壞法。

決定八：預設關著
    這是一條會讓機器自己發出聲音的規則。跟公平輪唱、點歌額度、錄唱回放
    一樣：會改變包廂行為的功能，預設一律關著，包廂講好了再開。

純資料邏輯：不碰 DOM、不發網路請求、不做 I/O。挑歌用的清單由呼叫端餵進來
（`LibraryIndex.entries()` 那一份），亂數也可以注入，所以測試是決定性的。
"""
import random
from typing import Any, Dict, Iterable, List, Optional, Sequence

# --- 挑歌來源 ---
#   mixed     混著挑（最愛偏多、沒唱過的其次），預設
#   favorites 只挑我的最愛
#   popular   只挑常點的歌
#   fresh     只挑還沒唱過的（讓冷門歌有機會出場）
SOURCE_MIXED = "mixed"
SOURCE_FAVORITES = "favorites"
SOURCE_POPULAR = "popular"
SOURCE_FRESH = "fresh"
SOURCE_CHOICES = (SOURCE_MIXED, SOURCE_FAVORITES, SOURCE_POPULAR, SOURCE_FRESH)
DEFAULT_SOURCE = SOURCE_MIXED

# 空了多久才接（秒）。見決定四。
DEFAULT_IDLE_SECONDS = 20
MIN_IDLE_SECONDS = 0
MAX_IDLE_SECONDS = 600

# 連續接幾首沒有人點歌就停。見決定五。
DEFAULT_STOP_AFTER = 3
MIN_STOP_AFTER = 1
MAX_STOP_AFTER = 20

# 機器接的歌開播幾秒內，有人點歌就立刻讓位。見決定三。
# 45 秒的理由：前奏加第一句大約就是這個尺度。再長就會切到正在唱的人，
# 再短則連前奏都還沒放完就讓位（那本來就該讓）。
YIELD_GRACE_SECONDS = 45.0

# 記得最近接過哪幾首，不要在同一晚一直接同一首。
# 12 首大約是一個小時的量 —— 比這更短會在一場裡重複，更長則會在小曲庫
# 裡把候選清光（清光之後會放寬，見 pick 的 relaxed）。
RECENT_MEMORY = 12

# mixed 的權重。最愛最高（那是使用者自己標的「我要唱這首」），
# 沒唱過的次之（曲庫裡最尷尬的事是下載了卻從來沒被翻出來），
# 唱過而且不是最愛的最低 —— 不是不挑，是不要一直挑。
WEIGHT_FAVORITE = 4
WEIGHT_FRESH = 2
WEIGHT_PLAIN = 1

# 常點的歌在 popular 來源裡照點唱次數加權，但封頂 ——
# 一首被唱過 30 次的歌不該有 30 倍的機會，那等於整晚只放那一首。
POPULAR_WEIGHT_CAP = 5

# 「為什麼挑這首」。畫面上要講得出來：機器自己放歌時，使用者第一個想問的
# 就是「它為什麼放這首」，答不出來的話這個功能看起來就只是亂放。
REASON_FAVORITE = "⭐ 我的最愛"
REASON_FRESH = "🌱 還沒唱過"
REASON_POPULAR = "🔥 常點的歌"
REASON_RANDOM = "🎲 曲庫隨機"

# 唱過幾次以上才算「常點的」。1 次多半是「試播看看」，不是喜歡。
POPULAR_MIN_PLAYS = 3


def coerce_source(value: Any) -> str:
    """把設定頁送來的來源收斂成合法值。認不得就回預設（混著挑）。"""
    text = str(value or "").strip().lower()
    return text if text in SOURCE_CHOICES else DEFAULT_SOURCE


def _coerce_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def coerce_idle_seconds(value: Any) -> int:
    """空了多久才接。看不懂的值回預設，不是 0 —— 0 是「立刻接」，
    那是一個明確的選擇，不該由一個打錯的字替使用者做。"""
    return _coerce_int(value, DEFAULT_IDLE_SECONDS, MIN_IDLE_SECONDS, MAX_IDLE_SECONDS)


def coerce_stop_after(value: Any) -> int:
    """連續接幾首就停。看不懂回預設；最低是 1（不能是 0 ——
    0 等於「開著但永遠不接」，畫面上說開著卻什麼都不會發生）。"""
    return _coerce_int(value, DEFAULT_STOP_AFTER, MIN_STOP_AFTER, MAX_STOP_AFTER)


def plays_of(entry: Dict[str, Any]) -> int:
    try:
        return max(0, int(entry.get("plays", 0) or 0))
    except (TypeError, ValueError):
        return 0


def reason_for(entry: Dict[str, Any], favorite_ids: Sequence[str] = ()) -> str:
    """
    挑中之後，講一句「為什麼是這首」。

    刻意照歌本身的性質講（是最愛、沒唱過、常點的），而不是照來源設定講：
    使用者關心的是這首歌跟自己的關係，不是機器用了哪一種挑法。
    """
    if entry.get("song_id") in set(favorite_ids or ()):
        return REASON_FAVORITE
    plays = plays_of(entry)
    if plays == 0:
        return REASON_FRESH
    if plays >= POPULAR_MIN_PLAYS:
        return REASON_POPULAR
    return REASON_RANDOM


def weight_of(entry: Dict[str, Any], source: str, favorite_ids: Sequence[str]) -> int:
    """
    這首歌在這個來源裡有多少機會。0 = 不在這個來源的池子裡。

    權重而不是排序：排序（照點唱次數排、挑最高的）會讓同一首歌被接到爛，
    而包廂第二次聽到同一首歌就會去按切歌。
    """
    favorites = set(favorite_ids or ())
    song_id = entry.get("song_id")
    plays = plays_of(entry)
    is_favorite = song_id in favorites

    if source == SOURCE_FAVORITES:
        return WEIGHT_FAVORITE if is_favorite else 0
    if source == SOURCE_POPULAR:
        return min(plays, POPULAR_WEIGHT_CAP) if plays > 0 else 0
    if source == SOURCE_FRESH:
        return WEIGHT_FRESH if plays == 0 else 0
    # mixed
    if is_favorite:
        return WEIGHT_FAVORITE
    if plays == 0:
        return WEIGHT_FRESH
    return WEIGHT_PLAIN


def weighted_choice(pool: Sequence[Dict[str, Any]], weights: Sequence[int],
                    rng: Optional[random.Random] = None) -> Optional[Dict[str, Any]]:
    """加權抽一首。總權重是 0 或池子是空的就回 None（由呼叫端決定要不要放寬）。"""
    total = sum(weights)
    if not pool or total <= 0:
        return None
    picker = rng or random
    target = picker.random() * total
    upto = 0.0
    for entry, weight in zip(pool, weights, strict=False):
        upto += weight
        if target < upto:
            return entry
    return pool[-1]  # 浮點誤差的保險絲：抽到尾巴就是最後一首


def pick(entries: Iterable[Dict[str, Any]], *,
         source: str = DEFAULT_SOURCE,
         favorite_ids: Sequence[str] = (),
         busy_ids: Sequence[str] = (),
         recent_ids: Sequence[str] = (),
         rng: Optional[random.Random] = None) -> Optional[Dict[str, Any]]:
    """
    挑一首機器要接的歌。挑不到回 None（曲庫是空的、或剩下的全在佇列裡）。

    兩種排除的份量完全不同，所以處理方式也不同：

      * `busy_ids`（正在播、已經排在佇列裡的）是**硬排除**，任何情況下都不放寬。
        接一首已經排在佇列裡的歌，等於機器把別人排好的歌偷跑掉，
        而那個人待會會看到自己點的歌「已經唱過了」。
      * `recent_ids`（剛剛接過的）是**軟排除**：小曲庫（只有三首歌）會被它
        清空，這時候寧可重複也不要安靜 —— 但回傳裡會標 `relaxed`，
        畫面才講得出「曲庫的歌不夠，開始重複了」。

    來源池空了（設「只挑我的最愛」但一首最愛都沒有）也一樣：退回整個曲庫，
    而不是什麼都不做。一個開著卻永遠不出聲的功能，使用者只會當它壞了。
    """
    pool: List[Dict[str, Any]] = [e for e in entries if e.get("song_id")]
    busy = set(busy_ids or ())
    pool = [e for e in pool if e["song_id"] not in busy]
    if not pool:
        return None

    recent = set(recent_ids or ())
    candidates = [e for e in pool if e["song_id"] not in recent]
    relaxed = False
    if not candidates:
        candidates, relaxed = pool, True

    source = coerce_source(source)
    weights = [weight_of(e, source, favorite_ids) for e in candidates]
    fallback = False
    if sum(weights) <= 0:
        # 來源池是空的（例如設了「只挑我的最愛」但還沒收藏任何一首）
        weights = [WEIGHT_PLAIN] * len(candidates)
        fallback = True

    chosen = weighted_choice(candidates, weights, rng=rng)
    if chosen is None:
        return None
    return {
        "song": chosen,
        "song_id": chosen["song_id"],
        "reason": reason_for(chosen, favorite_ids),
        "source": source,
        # 來源池空了而退回整個曲庫。設定頁說「只挑我的最愛」卻放了別的歌時，
        # 畫面要能解釋為什麼。
        "source_fallback": fallback,
        # 剛接過的歌被迫重複（曲庫太小）
        "relaxed": relaxed,
        "pool": len(candidates),
    }


def remember(recent: Sequence[str], song_id: str,
             limit: int = RECENT_MEMORY) -> List[str]:
    """把剛接過的歌記進「最近」，並裁到上限。同一首不重複記兩筆。"""
    kept = [sid for sid in recent if sid != song_id]
    kept.append(song_id)
    return kept[-max(1, limit):]
