"""
自動接歌（沒有人點歌時，機器自己接一首）的挑歌規則測試。

這一層錯掉的後果都不會當場看起來像壞掉 —— 機器照樣會出聲，只是挑了不該挑的歌：
把別人排在佇列裡的歌偷跑掉、整晚重複同一首、或是設了「只挑我的最愛」卻放了
別的歌卻不說。所以 autofill.py 開頭那幾個決定，每一個都在這裡釘一條測試。

亂數一律注入（`random.Random(種子)`），所以每一條都是決定性的。
"""
import random

import pytest

from backend.services.autofill import (
    DEFAULT_IDLE_SECONDS,
    DEFAULT_SOURCE,
    DEFAULT_STOP_AFTER,
    MAX_IDLE_SECONDS,
    MIN_STOP_AFTER,
    RECENT_MEMORY,
    REASON_FAVORITE,
    REASON_FRESH,
    REASON_POPULAR,
    REASON_RANDOM,
    SOURCE_FAVORITES,
    SOURCE_FRESH,
    SOURCE_POPULAR,
    WEIGHT_FAVORITE,
    WEIGHT_FRESH,
    WEIGHT_PLAIN,
    coerce_idle_seconds,
    coerce_source,
    coerce_stop_after,
    pick,
    reason_for,
    remember,
    weight_of,
    weighted_choice,
)


def song(song_id, plays=0, title=None):
    return {"song_id": song_id, "title": title or f"歌 {song_id}",
            "artist": "歌手", "thumbnail": "", "plays": plays}


LIBRARY = [song("aaa", plays=0), song("bbb", plays=1), song("ccc", plays=8)]


# --- 設定值的收斂 ---

def test_defaults_match_the_decisions():
    """預設：混著挑、空 20 秒才接、連著接 3 首就停（決定四、五）。"""
    assert DEFAULT_SOURCE == "mixed"
    assert DEFAULT_IDLE_SECONDS == 20
    assert DEFAULT_STOP_AFTER == 3


def test_coerce_source_falls_back_to_mixed():
    assert coerce_source("favorites") == SOURCE_FAVORITES
    assert coerce_source("FRESH") == SOURCE_FRESH
    assert coerce_source("不存在的來源") == DEFAULT_SOURCE
    assert coerce_source(None) == DEFAULT_SOURCE


def test_coerce_idle_seconds_clamps_and_keeps_zero():
    """0 是合法值（「空了就接」），所以不能被當成「沒設」而換成預設。"""
    assert coerce_idle_seconds(0) == 0
    assert coerce_idle_seconds(45) == 45
    assert coerce_idle_seconds(99999) == MAX_IDLE_SECONDS
    assert coerce_idle_seconds(-5) == 0
    # 看不懂的值才回預設
    assert coerce_idle_seconds("慢一點") == DEFAULT_IDLE_SECONDS


def test_coerce_stop_after_never_returns_zero():
    """0 等於「開著但永遠不接」—— 畫面說開著卻什麼都不會發生，是最難查的壞法。"""
    assert coerce_stop_after(0) == MIN_STOP_AFTER
    assert coerce_stop_after(5) == 5
    assert coerce_stop_after(999) == 20
    assert coerce_stop_after("三") == DEFAULT_STOP_AFTER


# --- 權重 ---

def test_mixed_prefers_favorites_then_unsung():
    """混著挑：最愛最高、沒唱過的次之、唱過而且不是最愛的最低。"""
    assert weight_of(song("x", plays=5), "mixed", ["x"]) == WEIGHT_FAVORITE
    assert weight_of(song("y", plays=0), "mixed", []) == WEIGHT_FRESH
    assert weight_of(song("z", plays=5), "mixed", []) == WEIGHT_PLAIN


def test_single_source_pools_exclude_everything_else():
    assert weight_of(song("a", plays=0), SOURCE_FAVORITES, []) == 0
    assert weight_of(song("a", plays=0), SOURCE_POPULAR, []) == 0
    assert weight_of(song("a", plays=3), SOURCE_FRESH, []) == 0


def test_popular_weight_is_capped():
    """一首唱過 30 次的歌不該有 30 倍的機會 —— 那等於整晚只放那一首。"""
    assert weight_of(song("a", plays=30), SOURCE_POPULAR, []) == 5
    assert weight_of(song("a", plays=2), SOURCE_POPULAR, []) == 2


def test_weighted_choice_respects_weights():
    pool = [song("a"), song("b")]
    # rng.random() 回 0.0 → 落在第一首；回 0.99 → 落在最後一首
    assert weighted_choice(pool, [1, 1], rng=FakeRng(0.0))["song_id"] == "a"
    assert weighted_choice(pool, [1, 1], rng=FakeRng(0.99))["song_id"] == "b"
    # 權重 0 的那一首永遠抽不到
    assert weighted_choice(pool, [0, 1], rng=FakeRng(0.0))["song_id"] == "b"
    assert weighted_choice(pool, [0, 0], rng=FakeRng(0.5)) is None
    assert weighted_choice([], [], rng=FakeRng(0.5)) is None


class FakeRng:
    """固定回同一個 0..1 數字的假亂數，讓加權抽樣變成可以直接斷言的事。"""

    def __init__(self, value):
        self.value = value

    def random(self):
        return self.value


# --- 挑歌 ---

def test_pick_returns_none_for_empty_library():
    assert pick([], rng=random.Random(1)) is None


def test_pick_never_takes_a_song_that_is_already_queued():
    """
    硬排除（決定：佇列裡的歌一首都不能被機器偷跑）。

    只剩一首沒被排走時就挑那一首；全部都在佇列裡就什麼都不接 ——
    寧可安靜，也不能把別人排好的歌搶先播掉。
    """
    result = pick(LIBRARY, busy_ids=["aaa", "bbb"], rng=random.Random(1))
    assert result["song_id"] == "ccc"
    assert pick(LIBRARY, busy_ids=["aaa", "bbb", "ccc"], rng=random.Random(1)) is None


def test_recently_played_songs_are_skipped():
    """剛接過的不要再接 —— 同一晚聽到第二次同一首，包廂就會去按切歌。"""
    for seed in range(20):
        result = pick(LIBRARY, recent_ids=["aaa", "bbb"], rng=random.Random(seed))
        assert result["song_id"] == "ccc"
        assert result["relaxed"] is False


def test_tiny_library_repeats_rather_than_going_silent():
    """
    曲庫只有三首、三首都剛接過：寧可重複也不要安靜，但要標出 relaxed
    （畫面才講得出「曲庫的歌不夠，開始重複了」）。
    """
    result = pick(LIBRARY, recent_ids=["aaa", "bbb", "ccc"], rng=random.Random(3))
    assert result is not None
    assert result["relaxed"] is True


def test_busy_ids_are_never_relaxed():
    """軟排除會放寬，硬排除不會：剩下的歌全在佇列裡時，答案是 None 而不是重複。"""
    assert pick(LIBRARY, busy_ids=["aaa", "bbb", "ccc"],
                recent_ids=["aaa"], rng=random.Random(3)) is None


def test_favorites_source_only_picks_favorites():
    for seed in range(20):
        result = pick(LIBRARY, source=SOURCE_FAVORITES, favorite_ids=["bbb"],
                      rng=random.Random(seed))
        assert result["song_id"] == "bbb"
        assert result["source_fallback"] is False


def test_empty_source_pool_falls_back_to_whole_library():
    """
    設了「只挑我的最愛」但一首最愛都沒有：退回整個曲庫，並標 source_fallback。

    另一種寫法是什麼都不接。那會讓設定頁上寫著「開啟」的功能永遠不出聲，
    而使用者查不出是因為自己沒有收藏過任何一首。
    """
    result = pick(LIBRARY, source=SOURCE_FAVORITES, favorite_ids=[],
                  rng=random.Random(2))
    assert result is not None
    assert result["source_fallback"] is True
    assert result["source"] == SOURCE_FAVORITES


def test_fresh_source_only_picks_unsung_songs():
    for seed in range(20):
        assert pick(LIBRARY, source=SOURCE_FRESH,
                    rng=random.Random(seed))["song_id"] == "aaa"


def test_popular_source_skips_never_played_songs():
    for seed in range(20):
        assert pick(LIBRARY, source=SOURCE_POPULAR,
                    rng=random.Random(seed))["song_id"] in {"bbb", "ccc"}


# --- 為什麼挑這首 ---

def test_reason_describes_the_song_not_the_source():
    assert reason_for(song("a", plays=9), ["a"]) == REASON_FAVORITE
    assert reason_for(song("a", plays=0), []) == REASON_FRESH
    assert reason_for(song("a", plays=9), []) == REASON_POPULAR
    # 唱過一兩次、又不是最愛：老實說是隨機挑的，不要硬掰一個理由
    assert reason_for(song("a", plays=1), []) == REASON_RANDOM


def test_pick_reports_reason_and_pool_size():
    result = pick(LIBRARY, favorite_ids=["ccc"], rng=random.Random(0))
    assert result["reason"] in {REASON_FAVORITE, REASON_FRESH, REASON_POPULAR, REASON_RANDOM}
    assert result["pool"] == 3
    assert result["song"]["title"].startswith("歌 ")


def test_entries_without_song_id_are_ignored():
    """壞掉的曲庫項目（沒有 song_id）不該讓整個心跳掛掉。"""
    assert pick([{"title": "沒有 id 的東西"}], rng=random.Random(0)) is None


# --- 最近接過的記憶 ---

def test_remember_keeps_latest_and_trims():
    recent = []
    for i in range(RECENT_MEMORY + 5):
        recent = remember(recent, f"s{i}")
    assert len(recent) == RECENT_MEMORY
    assert recent[-1] == f"s{RECENT_MEMORY + 4}"
    assert "s0" not in recent


def test_remember_moves_repeat_to_the_end_instead_of_duplicating():
    recent = remember(remember(["a", "b"], "c"), "a")
    assert recent == ["b", "c", "a"]


@pytest.mark.parametrize("limit", [0, -3])
def test_remember_limit_is_at_least_one(limit):
    assert remember(["a", "b"], "c", limit=limit) == ["c"]
