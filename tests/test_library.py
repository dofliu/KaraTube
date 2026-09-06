"""曲庫瀏覽（分類 / 新歌榜 / 推薦歌單）單元測試。

語言判定與歌手正規化都是純函數，直接測；
LibraryIndex 則搭 tmp_path 上的假快取資料夾測，不碰網路也不用模型。
"""
import json
import time

from backend.services.library import (
    LANGUAGE_KEYS,
    UNKNOWN_ARTIST,
    LibraryIndex,
    artist_from_title,
    detect_language,
    language_label,
    looks_like_channel,
    lyrics_sample,
    normalize_artist,
)
from backend.services.storage import SongStorage


# --- 語言判定 ---

def test_detect_mandarin_from_lyrics():
    assert detect_language("稻香", "周杰倫", "還記得你說家是唯一的城堡 隨著稻香河流繼續奔跑") == "mandarin"


def test_detect_japanese_needs_kana_ratio():
    assert detect_language("残酷な天使のテーゼ", "高橋洋子",
                           "残酷な天使のように 少年よ神話になれ") == "japanese"
    # 國語歌名裡夾一個「の」不該被判成日文歌
    assert detect_language("我の愛", "某某", "我愛你這句話說了千百遍還是不夠") == "mandarin"


def test_detect_korean():
    assert detect_language("좋은 날", "아이유", "어쩌면 좋아 날 좋아하나요") == "korean"


def test_detect_english():
    assert detect_language("Yesterday", "The Beatles",
                           "Yesterday all my troubles seemed so far away") == "english"


def test_detect_cantonese_needs_two_markers():
    assert detect_language("喜帖街", "謝安琪", "忘掉種過的花 唔緊要 佢話咁樣先啱") == "cantonese"
    # 只有一個粵語字不足以定案，仍算國語
    assert detect_language("紅豆", "王菲", "有時候有時候我會相信一切有盡頭 佢") == "mandarin"


def test_detect_taiwanese_from_markers():
    assert detect_language("家後", "江蕙", "阮的一生獻乎恁兜 有一日咱都老 我會佇遮等你 袂放你孤單") == "taiwanese"


def test_explicit_language_hint_wins():
    assert detect_language("經典台語歌 - 望春風", "老歌頻道") == "taiwanese"
    assert detect_language("粵語金曲 - 海闊天空", "頻道") == "cantonese"


def test_detect_language_handles_empty_and_symbols():
    assert detect_language("", "", "") == "other"
    assert detect_language("♪♪♪", "", "") == "other"


def test_language_label_falls_back_to_other():
    assert language_label("mandarin") == "國語"
    assert language_label("klingon") == "其他"


# --- 歌手正規化 ---

def test_normalize_artist_strips_topic_and_official():
    assert normalize_artist("稻香", "周杰倫 - Topic") == "周杰倫"
    assert normalize_artist("突然好想你", "五月天 官方頻道") == "五月天"
    assert normalize_artist("Hello", "AdeleVEVO") == "Adele"


def test_normalize_artist_falls_back_to_title():
    # 頻道名一看就是唱片公司，改從「歌手 - 歌名」的標題前半段猜
    assert normalize_artist("林俊傑 - 江南", "華納音樂 Warner Music") == "林俊傑"
    assert normalize_artist("告五人｜披星戴月的想你", "滾石唱片") == "告五人"


def test_normalize_artist_unknown_when_nothing_usable():
    assert normalize_artist("無名歌曲", "") == UNKNOWN_ARTIST


def test_artist_from_title_rejects_unsplit_or_too_long():
    assert artist_from_title("沒有分隔符號的標題") == ""
    assert artist_from_title("這是一個非常非常非常非常非常非常冗長的頻道自我介紹字串 - 歌名") == ""


def test_looks_like_channel():
    assert looks_like_channel("華納音樂")
    assert looks_like_channel("Some Records")
    assert not looks_like_channel("周杰倫")


def test_lyrics_sample_joins_and_truncates():
    lyrics = [{"text": "第一句"}, {"text": "第二句"}, "壞資料", {"nope": 1}]
    assert lyrics_sample(lyrics) == "第一句 第二句"
    assert lyrics_sample(None) == ""
    long_lyrics = [{"text": "字" * 100} for _ in range(20)]
    assert len(lyrics_sample(long_lyrics, max_chars=50)) == 50


# --- LibraryIndex ---

def make_song(root, song_id, title, artist, lyrics_text="", complete=True, age_days=0):
    song_dir = root / song_id
    song_dir.mkdir(parents=True)
    (song_dir / "metadata.json").write_text(
        json.dumps({"id": song_id, "title": title, "artist": artist, "duration": 200},
                   ensure_ascii=False), encoding="utf-8")
    (song_dir / "lyrics.json").write_text(
        json.dumps([{"start": 0, "end": 3, "text": lyrics_text}], ensure_ascii=False),
        encoding="utf-8")
    if complete:
        (song_dir / "instrumental.mp3").write_bytes(b"x")
        (song_dir / "vocals.mp3").write_bytes(b"x")
    if age_days:
        stamp = time.time() - age_days * 86400
        import os
        os.utime(song_dir, (stamp, stamp))
    return song_dir


class FakeStats:
    def __init__(self, data):
        self._data = data

    def snapshot(self):
        return {k: dict(v) for k, v in self._data.items()}


class FakeHistory:
    def __init__(self, entries):
        self._entries = entries

    def recent(self, limit=50):
        return list(self._entries[:limit])


def build_library(tmp_path, stats=None, history=None):
    return LibraryIndex(SongStorage(tmp_path), play_stats=stats, song_history=history)


def test_entries_skip_incomplete_songs(tmp_path):
    make_song(tmp_path, "ok1", "稻香", "周杰倫 - Topic", "隨著稻香河流繼續奔跑")
    make_song(tmp_path, "bad1", "壞掉的歌", "頻道", "詞", complete=False)
    entries = build_library(tmp_path).entries()
    assert [e["song_id"] for e in entries] == ["ok1"]
    assert entries[0]["artist_name"] == "周杰倫"
    assert entries[0]["language"] == "mandarin"
    assert entries[0]["language_label"] == "國語"


def test_classification_is_cached_in_metadata(tmp_path):
    # 歌名與頻道名都是英文，語言完全由歌詞決定 —— 這樣才看得出「重算」有沒有生效
    make_song(tmp_path, "s1", "Dao Xiang", "Jay Chou - Topic", "隨著稻香河流繼續奔跑")
    lib = build_library(tmp_path)
    lib.entries()
    meta = json.loads((tmp_path / "s1" / "metadata.json").read_text(encoding="utf-8"))
    assert meta["language"] == "mandarin"
    assert meta["artist_name"] == "Jay Chou"
    # 歌詞整份換掉也不會重算 —— 分類已經跟著 metadata 存下來了
    (tmp_path / "s1" / "lyrics.json").write_text(
        json.dumps([{"text": "Yesterday all my troubles seemed so far away"}]),
        encoding="utf-8")
    assert lib.entries()[0]["language"] == "mandarin"
    # 明確要求重算才會照新歌詞改判
    assert lib.entries(refresh=True)[0]["language"] == "english"


def test_facets_counts_languages_and_artists(tmp_path):
    make_song(tmp_path, "a", "稻香", "周杰倫 - Topic", "隨著稻香河流繼續奔跑")
    make_song(tmp_path, "b", "青花瓷", "周杰倫 - Topic", "天青色等煙雨而我在等你")
    make_song(tmp_path, "c", "Yesterday", "The Beatles - Topic",
              "Yesterday all my troubles seemed so far away")
    facets = build_library(tmp_path).facets()
    counts = {lang["key"]: lang["count"] for lang in facets["languages"]}
    assert counts["mandarin"] == 2
    assert counts["english"] == 1
    assert [lang["key"] for lang in facets["languages"]] == list(LANGUAGE_KEYS)
    # 歌多的歌手排前面
    assert facets["artists"][0]["name"] == "周杰倫"
    assert facets["artists"][0]["count"] == 2
    assert facets["total"] == 3
    assert facets["artist_count"] == 2


def test_browse_filters_and_sorts(tmp_path):
    make_song(tmp_path, "a", "稻香", "周杰倫 - Topic", "隨著稻香河流繼續奔跑", age_days=3)
    make_song(tmp_path, "b", "Yesterday", "The Beatles - Topic",
              "Yesterday all my troubles seemed so far away", age_days=1)
    stats = FakeStats({"b": {"plays": 5, "last_played": "2026-09-05T20:00:00"}})
    lib = build_library(tmp_path, stats=stats)

    assert [s["song_id"] for s in lib.browse(language="mandarin")] == ["a"]
    assert [s["song_id"] for s in lib.browse(artist="The Beatles")] == ["b"]
    assert lib.browse(language="korean") == []
    assert lib.browse(artist="不存在的歌手") == []
    # 沒給篩選條件＝全部，預設依加入時間新到舊
    assert [s["song_id"] for s in lib.browse()] == ["b", "a"]
    assert [s["song_id"] for s in lib.browse(sort="plays")] == ["b", "a"]
    assert [s["song_id"] for s in lib.browse(sort="title")] == ["b", "a"]
    assert lib.browse(limit=1) == lib.browse()[:1]


def test_browse_includes_play_counts(tmp_path):
    make_song(tmp_path, "a", "稻香", "周杰倫 - Topic", "隨著稻香河流繼續奔跑")
    stats = FakeStats({"a": {"plays": 3, "last_played": "2026-09-05T20:00:00"}})
    song = build_library(tmp_path, stats=stats).browse()[0]
    assert song["plays"] == 3
    assert song["last_played"] == "2026-09-05T20:00:00"
    assert song["is_cached"] is True


def test_new_songs_marks_recent(tmp_path):
    make_song(tmp_path, "fresh", "新歌", "歌手A - Topic", "今天剛加入的歌")
    make_song(tmp_path, "old", "舊歌", "歌手B - Topic", "很久以前加入的歌", age_days=60)
    songs = build_library(tmp_path).new_songs(limit=10)
    assert [s["song_id"] for s in songs] == ["fresh", "old"]
    assert songs[0]["is_new"] is True
    assert songs[1]["is_new"] is False


def test_recommendations_cover_reasons(tmp_path):
    make_song(tmp_path, "old_friend", "老歌", "歌手A - Topic", "唱過很多次的歌", age_days=30)
    make_song(tmp_path, "same_artist", "同歌手新歌", "歌手A - Topic", "同一位歌手還沒唱過的歌")
    make_song(tmp_path, "never", "沒唱過", "歌手B - Topic", "全新的歌")
    stats = FakeStats({"old_friend": {"plays": 4, "last_played": "2026-01-01T10:00:00"}})
    history = FakeHistory([{"song_id": "old_friend"}])
    recs = build_library(tmp_path, stats=stats, history=history).recommendations(limit=5)

    by_id = {r["song_id"]: r for r in recs}
    assert by_id["old_friend"]["reason_tag"] == "old_friend"
    assert by_id["same_artist"]["reason_tag"] == "same_artist"
    assert by_id["never"]["reason_tag"] == "never_played"
    assert all(r["reason"] for r in recs)
    # 不重複推薦同一首
    assert len(by_id) == len(recs)


def test_recommendations_respect_limit_and_empty_library(tmp_path):
    assert build_library(tmp_path).recommendations() == []
    for i in range(6):
        make_song(tmp_path, f"s{i}", f"歌{i}", "歌手 - Topic", "歌詞內容")
    assert len(build_library(tmp_path).recommendations(limit=3)) == 3


def test_index_survives_broken_lyrics_file(tmp_path):
    song_dir = make_song(tmp_path, "s1", "歌名", "歌手 - Topic", "歌詞")
    (song_dir / "lyrics.json").write_text("{ 這不是 JSON", encoding="utf-8")
    entries = build_library(tmp_path).entries()
    assert len(entries) == 1
    assert entries[0]["language"] in LANGUAGE_KEYS
