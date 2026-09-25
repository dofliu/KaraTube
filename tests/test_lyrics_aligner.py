"""
歌詞對齊模組中「純函數」部分的單元測試。

對齊流程本身需要 GPU、Whisper 模型與真實音檔，CI 上跑不動；
但決定成敗的其實是這幾個純函數：LRC 解析、製作名單過濾、候選排序、
逐字時間分配。它們吃字串吐資料結構，全部測得動，也是實際出過包的地方
（把「作詞：XXX」當成歌詞唱出來、把翻唱版的 LRC 拿去對原曲）。

LyricsAligner 的建構子只記下模型名稱，Whisper 是第一次轉錄才載入，
所以這裡可以放心直接實例化。
"""
import pytest

from backend.pipeline.lyrics_aligner import LyricsAligner


@pytest.fixture(scope="module")
def aligner():
    return LyricsAligner(model_size="tiny", device="cpu", compute_type="int8")


# --- LRC 解析 ---

def test_parse_basic_lrc(aligner):
    lrc = "\n".join([
        "[00:12.50]第一句歌詞",
        "[00:18.20]第二句歌詞",
        "[01:05.00]第三句歌詞",
    ])
    parsed = aligner.parse_lrc_with_timestamps(lrc)
    assert [p["time"] for p in parsed] == [12.5, 18.2, 65.0]
    assert parsed[0]["text"] == "第一句歌詞"


def test_parse_handles_one_two_and_three_digit_fractions(aligner):
    """流通的 LRC 小數位有 1~3 位三種寫法，少認一種就少一句歌詞。"""
    parsed = aligner.parse_lrc_with_timestamps("[00:01.5]甲\n[00:02.25]乙\n[00:03.125]丙")
    assert [p["time"] for p in parsed] == [1.5, 2.25, 3.125]


def test_parse_expands_repeated_time_tags(aligner):
    """副歌常寫成一行多個時間標籤，必須展開成多筆。"""
    parsed = aligner.parse_lrc_with_timestamps("[00:10.00][01:10.00][02:10.00]副歌")
    assert [p["time"] for p in parsed] == [10.0, 70.0, 130.0]
    assert all(p["text"] == "副歌" for p in parsed)


def test_parse_sorts_and_dedupes_same_timestamp(aligner):
    """雙語 LRC 同一個時間點會有兩行（原文＋翻譯），只留第一筆。"""
    parsed = aligner.parse_lrc_with_timestamps("[00:20.00]後面的\n[00:10.00]前面的\n[00:10.01]翻譯")
    assert [p["time"] for p in parsed] == [10.0, 20.0]
    assert parsed[0]["text"] == "前面的"


def test_parse_skips_lines_without_time_tags(aligner):
    parsed = aligner.parse_lrc_with_timestamps("這行沒有時間標籤\n[ti:歌名]\n[00:05.00]真的歌詞")
    assert len(parsed) == 1
    assert parsed[0]["text"] == "真的歌詞"


def test_parse_drops_credits_and_copyright_lines(aligner):
    """製作名單被唱出來是實際發生過的問題：舞台會顯示「作詞：someone」。"""
    lrc = "\n".join([
        "[00:00.00]作詞：張三",
        "[00:01.00]作曲 : 李四",
        "[00:02.00]未經授權不得翻唱",
        "[00:03.00]【間奏】",
        "[00:04.00]這才是要唱的歌詞",
    ])
    parsed = aligner.parse_lrc_with_timestamps(lrc)
    assert [p["text"] for p in parsed] == ["這才是要唱的歌詞"]


def test_normal_lyrics_containing_credit_keywords_survive(aligner):
    """「詞」「曲」「唱」出現在歌詞裡很正常，不能因為含關鍵字就整行殺掉。"""
    lrc = "[00:01.00]為你寫一首歌\n[00:02.00]唱著我們的故事\n[00:03.00]一整瓶的夢境"
    parsed = aligner.parse_lrc_with_timestamps(lrc)
    assert len(parsed) == 3


def test_extract_lyric_lines_returns_text_only(aligner):
    lines = aligner.extract_lyric_lines_from_lrc("[00:01.00]甲\n[00:02.00]乙")
    assert lines == ["甲", "乙"]


# --- LRC 可用性判斷 ---

def _long_lrc(lines=12, step=5.0):
    def tag(seconds):
        return f"[{int(seconds) // 60:02d}:{int(seconds) % 60:02d}.00]"
    return "\n".join(f"{tag(i * step)}這是第{i}句夠長的歌詞內容" for i in range(lines))


def test_lrc_usable_accepts_a_normal_lrc(aligner):
    assert aligner._lrc_usable(_long_lrc()) is True


def test_lrc_without_timestamps_is_unusable(aligner):
    """純伴奏／混音版的頁面常常只回一段沒有時間軸的說明文字。"""
    assert aligner._lrc_usable("這是一段沒有任何時間標籤的說明文字，" * 10) is False


def test_too_short_lrc_is_unusable(aligner):
    assert aligner._lrc_usable("[00:01.00]短") is False


def test_lrc_much_longer_than_the_video_is_rejected(aligner):
    """LRC 尾巴比影片長 45 秒以上，八成是抓到加長版或別首歌。"""
    assert aligner._lrc_usable(_long_lrc(lines=12, step=30.0), duration_hint=60.0) is False


def test_is_non_singing(aligner):
    assert aligner._is_non_singing("作詞：王五") is True
    assert aligner._is_non_singing("[間奏]") is True
    assert aligner._is_non_singing("Copyright 2020 Someone") is True
    assert aligner._is_non_singing("") is True
    assert aligner._is_non_singing("我還是會想念你") is False


# --- 候選排序 ---

def test_duration_match_dominates_ranking(aligner):
    """曲長是最強的訊號：長度對不上的翻唱版時間軸救不回來。"""
    exact = aligner._rank_candidate("晴天", "周杰倫", 269.0, "周杰倫", 270.0)
    way_off = aligner._rank_candidate("晴天", "周杰倫", 400.0, "周杰倫", 270.0)
    assert exact > way_off


def test_karaoke_and_instrumental_versions_are_penalised(aligner):
    original = aligner._rank_candidate("晴天", "周杰倫", 270.0, "周杰倫", 270.0)
    instrumental = aligner._rank_candidate("晴天 (伴奏)", "周杰倫", 270.0, "周杰倫", 270.0)
    live = aligner._rank_candidate("晴天 (Live)", "周杰倫", 270.0, "周杰倫", 270.0)
    assert instrumental < original
    assert live < original


# --- 逐字時間分配 ---

def test_char_weight_reflects_singing_time(aligner):
    """中文一字一音，拉丁字母只是音節的一部分，空白幾乎不佔時間。"""
    assert aligner._char_weight("愛") == 1.0
    assert aligner._char_weight("あ") == 1.0
    assert aligner._char_weight("가") == 1.0
    assert aligner._char_weight("a") < aligner._char_weight("愛")
    assert aligner._char_weight(" ") < aligner._char_weight("a")


def test_distribute_chars_covers_the_whole_line(aligner):
    chars = aligner._distribute_chars("我還是會想你", 10.0, 13.0, None)
    assert len(chars) == 6
    assert chars[0]["start"] == pytest.approx(10.0, abs=0.01)
    assert chars[-1]["end"] == pytest.approx(13.0, abs=0.01)
    assert [c["char"] for c in chars] == list("我還是會想你")


def test_distribute_chars_is_monotonic(aligner):
    """走字倒退回去就毀了 —— 每個字的結尾必須是下一個字的開頭。"""
    chars = aligner._distribute_chars("Never gonna 給你", 0.0, 4.0, None)
    for prev, nxt in zip(chars, chars[1:], strict=False):
        assert prev["end"] <= nxt["start"] + 1e-6
        assert nxt["end"] > nxt["start"]


def test_cjk_chars_get_more_time_than_latin_letters(aligner):
    chars = aligner._distribute_chars("愛a", 0.0, 2.0, None)
    cjk_dur = chars[0]["end"] - chars[0]["start"]
    latin_dur = chars[1]["end"] - chars[1]["start"]
    assert cjk_dur > latin_dur


def test_distribute_chars_on_empty_text(aligner):
    assert aligner._distribute_chars("", 1.0, 2.0, None) == []


def test_distribute_chars_survives_zero_length_line(aligner):
    """壞掉的 LRC 會給出 start == end 的行，不能因此丟例外或吐出倒退的時間。"""
    chars = aligner._distribute_chars("三個字", 5.0, 5.0, None)
    assert len(chars) == 3
    for prev, nxt in zip(chars, chars[1:], strict=False):
        assert prev["end"] <= nxt["start"] + 1e-6


# --- 自備歌詞（本機匯入時放在檔案旁邊的那份 .lrc）---
#
# 這一段是**行為**測試而不是純函數測試，但它跑得動：給了 local_lrc 就不上網，
# 而人聲活動分析（需要 librosa 與真實音檔）失敗時會退成 va=None，
# 剩下的路徑全部是 numpy。這幾條釘的是兩個不能退讓的決定。

LOCAL_LRC = "\n".join([
    "[00:10.00]午後的陽光",
    "[00:14.00]灑在稻田上",
    "[00:18.00]風吹過山崗",
    "[00:22.00]記得那年的夏天",
])


def test_sidecar_lrc_is_used_without_going_online(aligner, tmp_path, monkeypatch):
    """給了自備歌詞就不該再上網搜尋：本機檔案多半是網路上根本沒有的歌，
    而那一趟搜尋只會拖慢整批匯入（一首多等好幾秒，兩百首就是半小時）。"""
    def explode(*args, **kwargs):
        raise AssertionError("有了自備歌詞還去抓線上 LRC")

    monkeypatch.setattr(aligner, "fetch_lrc_candidates", explode)
    out = tmp_path / "lyrics.json"
    lines = aligner.align(tmp_path / "vocals.mp3", "稻香", "周杰倫",
                          output_json=out, local_lrc=LOCAL_LRC)
    assert [ln["text"] for ln in lines][:2] == ["午後的陽光", "灑在稻田上"]
    assert out.exists()

    import json
    report = json.loads((tmp_path / "alignment.json").read_text(encoding="utf-8"))
    assert report["source"] == "lrc_local"


def test_a_low_scoring_sidecar_lrc_is_not_silently_replaced(aligner, tmp_path, monkeypatch):
    """使用者親手放的歌詞被默默換成聽寫結果的話，他既看不到原因，
    也想不到要去哪裡改。分數照樣記進 alignment.json，讓畫面上的徽章去講。"""
    def explode(*args, **kwargs):
        raise AssertionError("自備歌詞不該掉進 Whisper 聽寫")

    monkeypatch.setattr(aligner, "transcribe_pure_acoustic", explode)
    monkeypatch.setattr(aligner, "fetch_lrc_candidates", lambda *a, **k: [])
    lines = aligner.align(tmp_path / "vocals.mp3", "稻香", "",
                          output_json=tmp_path / "lyrics.json", local_lrc=LOCAL_LRC)
    assert len(lines) >= 4


def test_a_plain_text_lyric_file_falls_back_to_the_normal_path(aligner, tmp_path, monkeypatch):
    """沒有時間戳的純文字歌詞解不出兩行以上。硬用的話整首歌只剩一行字，
    所以要退回一般流程（上網找 → 聽寫）。"""
    called = {"online": 0}

    def fake_fetch(*args, **kwargs):
        called["online"] += 1
        return []

    monkeypatch.setattr(aligner, "fetch_lrc_candidates", fake_fetch)
    monkeypatch.setattr(aligner, "transcribe_pure_acoustic", lambda *a, **k: [])
    aligner.align(tmp_path / "vocals.mp3", "稻香", "",
                  output_json=tmp_path / "lyrics.json",
                  local_lrc="午後的陽光\n灑在稻田上")
    assert called["online"] == 1
