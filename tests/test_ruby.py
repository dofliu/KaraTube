"""
歌詞拼音標注的測試。

這個功能的失敗模式有兩種，而且兩種都**不會丟例外、畫面上也看不出來**：

  1. **標錯語言**：用華語讀音標台語／粵語歌。每一個音節都是錯的，
     而照著唸的人不會發現 —— 他本來就不會唸。
  2. **整行位移**：歌詞被換掉（重算歌詞）之後，舊的拼音還留著，
     於是第三個字的拼音標在第五個字頭上。畫面看起來完全正常。

所以這一支的主體是那兩件事的守衛，不是「拼音對不對」。
版面那一半（字級怎麼算、什麼時候整行不標）在 frontend/tests/ruby-layout.test.js。
"""
import pytest

from backend.services.ruby import (DEFAULT_RUBY_MODE, PINYIN_AVAILABLE, REASON_ENGINE_MISSING,
                                   REASON_NO_LYRICS, REASON_UNSUPPORTED_LANGUAGE,
                                   RUBY_LANGUAGES, RUBY_MODE_CHOICES, RUBY_STYLE, RUBY_VERSION,
                                   annotate_text, build, coerce_mode, empty_doc, fingerprint,
                                   is_stale, is_supported_language, line_chars)
from backend.services.library import LANGUAGE_KEYS
from backend.services.settings import SETTINGS_SPEC, default_settings

needs_pinyin = pytest.mark.skipif(not PINYIN_AVAILABLE, reason="沒裝 pypinyin")


def make_line(text, idx=0):
    """跟流水線產出的形狀一樣：逐字陣列，每一格一個字。"""
    n = max(1, len(text))
    return {
        "line_idx": idx,
        "text": text,
        "start": float(idx),
        "end": float(idx + 1),
        "words": [{"char": c, "start": idx + i / n, "end": idx + (i + 1) / n}
                  for i, c in enumerate(text)],
    }


# --- 決定一：只標國語歌 ---

def test_only_mandarin_is_annotated():
    """台語、粵語、日語歌一律不標。標錯比不標更糟（見 ruby.py 檔頭決定一）。"""
    assert RUBY_LANGUAGES == ("mandarin",)
    for key in LANGUAGE_KEYS:
        assert is_supported_language(key) == (key == "mandarin"), key


def test_unsupported_language_says_why():
    """
    默默空白跟「功能壞了」在螢幕上長得一模一樣，所以要說得出理由。
    """
    lines = [make_line("我唔鍾意")]
    doc = build(lines, "cantonese")
    assert doc["available"] is False
    assert doc["reason"] == REASON_UNSUPPORTED_LANGUAGE
    assert doc["lines"] == []


def test_unknown_language_is_not_annotated():
    """語言還沒判出來（None、空字串、亂碼）一律當成標不準。"""
    lines = [make_line("還沒分類")]
    for value in (None, "", "zh", "mandarin ", 123, {"key": "mandarin"}):
        assert build(lines, value)["available"] is False


def test_no_lyrics_is_its_own_reason():
    assert build([], "mandarin")["reason"] == REASON_NO_LYRICS


@needs_pinyin
def test_all_latin_lyrics_count_as_no_ruby():
    """
    整首歌一個漢字都沒有 = 沒有拼音。留一份全空的陣列只會讓舞台
    為它保留一整排高度（而那一排永遠是空的）。
    """
    doc = build([make_line("Hello world"), make_line("1 2 3 4")], "mandarin")
    assert doc["available"] is False
    assert doc["reason"] == REASON_NO_LYRICS


# --- 決定二：多音字挑一個，而且要靠詞組挑 ---

@needs_pinyin
def test_heteronyms_use_phrase_context():
    """
    「重」在「重新」唸 chóng、在「重視」唸 zhòng。單字送進去永遠只會拿到
    那個字最常見的讀音 —— 所以整段漢字要一起送（見 ruby.py annotate_text）。
    """
    chong = annotate_text("重新開始")
    zhong = annotate_text("我很重視你")
    assert chong[0].startswith("ch")
    assert zhong[2].startswith("zh")


@needs_pinyin
def test_one_syllable_per_char_not_a_set():
    """
    查歌索引對多音字存的是**所有讀音**（漏一個就查不到那首歌），
    標注剛好相反：螢幕上一個字只有一行字的空間，給兩個等於沒給。
    """
    for cell in annotate_text("重重的長長的"):
        assert "/" not in cell and " " not in cell


# --- 決定四：空的那幾格要留著 ---

@needs_pinyin
def test_length_always_matches_the_text():
    """
    拼音陣列與逐字陣列必須同長同序。少一格的症狀正好是「整行往後位移一個字」
    —— 那是會無聲出錯的那一種。
    """
    for text in ("我愛你", "Hello 世界", "2024 年的夏天", "", "！？。", "あいう漢字"):
        assert len(annotate_text(text)) == len(text), text


@needs_pinyin
def test_non_han_cells_are_empty_not_the_char_itself():
    """
    英數與標點沒有讀音。把原字回填上去的話，畫面上會出現「Hello」疊在
    「Hello」上面 —— 那是一行沒有意義的雜訊，而它會把目光拉走。
    """
    got = annotate_text("說Hello吧")
    assert got[0] and got[-1]
    assert got[1:6] == ["", "", "", "", ""]


@needs_pinyin
def test_build_rows_line_up_with_lyrics():
    lines = [make_line("我愛你", 0), make_line("Hello", 1), make_line("再見", 2)]
    doc = build(lines, "mandarin")
    assert doc["available"] is True
    assert len(doc["lines"]) == len(lines)
    for row, line in zip(doc["lines"], lines, strict=True):
        assert len(row) == len(line["text"])


# --- 逐字陣列才是畫面上的那一串字 ---

def test_line_chars_prefers_words_over_text():
    """
    舞台端的渲染順序是「有 words 就用 words」。兩邊各自決定的話，
    遇到 words 與 text 不一致的那幾行就會整行位移。
    """
    line = {"text": "不是這個", "words": [{"char": c} for c in "是這個"]}
    assert line_chars(line) == "是這個"


def test_line_chars_falls_back_to_text():
    assert line_chars({"text": "只有文字"}) == "只有文字"
    assert line_chars({}) == ""


# --- 決定三：指紋對不上就重算 ---

def test_fingerprint_changes_when_lyrics_change():
    a = [make_line("第一句"), make_line("第二句")]
    b = [make_line("第一句"), make_line("第二句改過了")]
    assert fingerprint(a) != fingerprint(b)


def test_fingerprint_ignores_timing():
    """
    調字幕偏移、兩點校正改的是每個字的 start/end，字本身沒變 ——
    把時間算進指紋的話，每調一次字幕就要把整首歌的拼音重算一次。
    """
    a = [make_line("同一句話")]
    b = [make_line("同一句話")]
    for w in b[0]["words"]:
        w["start"] += 3.5
        w["end"] += 3.5
    b[0]["start"] += 3.5
    assert fingerprint(a) == fingerprint(b)


@needs_pinyin
def test_stale_detection_covers_the_four_ways_it_can_rot():
    lines = [make_line("我愛你")]
    doc = build(lines, "mandarin")
    assert is_stale(doc, lines, "mandarin") is False

    # 1. 歌詞被換掉（重算歌詞）
    assert is_stale(doc, [make_line("另一句歌詞")], "mandarin") is True
    # 2. 語言別變了（分類完成，從 other 變成 mandarin）
    assert is_stale(doc, lines, "japanese") is True
    # 3. 格式版本升了
    assert is_stale({**doc, "version": RUBY_VERSION + 1}, lines, "mandarin") is True
    # 4. 標注風格換了
    assert is_stale({**doc, "style": "bopomofo"}, lines, "mandarin") is True


def test_stale_detection_rejects_junk():
    lines = [make_line("我愛你")]
    for junk in (None, [], "ruby", 0, {"version": RUBY_VERSION}):
        assert is_stale(junk, lines, "mandarin") is True


@needs_pinyin
def test_a_doc_that_claims_ruby_but_has_wrong_row_count_is_stale():
    """
    說自己有拼音、卻跟歌詞不等長的文件一律不信：那正是「整行位移」的來源。
    """
    lines = [make_line("第一句"), make_line("第二句")]
    doc = build(lines, "mandarin")
    doc["lines"] = doc["lines"][:1]
    assert is_stale(doc, lines, "mandarin") is True


def test_empty_doc_is_not_stale_for_the_same_song():
    """
    日語歌算出來的是一份 available: False 的文件，而它**要存得住** ——
    每次載入都重算一次的話，答案永遠一樣但 CPU 一直在算。
    """
    lines = [make_line("君の名は")]
    doc = empty_doc("japanese", REASON_UNSUPPORTED_LANGUAGE, lines)
    assert is_stale(doc, lines, "japanese") is False


# --- 顯示模式 ---

def test_mode_choices_and_default():
    assert RUBY_MODE_CHOICES == ("off", "auto", "on")
    assert DEFAULT_RUBY_MODE in RUBY_MODE_CHOICES


def test_coerce_mode_never_raises():
    assert coerce_mode("ON") == "on"
    assert coerce_mode("  auto ") == "auto"
    for junk in (None, "", "yes", 7, {"mode": "on"}):
        assert coerce_mode(junk) == DEFAULT_RUBY_MODE


def test_settings_spec_matches_the_rules_here():
    """
    設定頁的選項與這支的規則是同一份。兩邊各寫一份的話，設定頁會長出
    一個伺服器存不進去的選項（而畫面上看起來是存進去了）。
    """
    spec = SETTINGS_SPEC["stage_ruby"]
    assert tuple(spec["choices"]) == RUBY_MODE_CHOICES
    assert spec["default"] == DEFAULT_RUBY_MODE
    assert default_settings()["stage_ruby"] == DEFAULT_RUBY_MODE


def test_style_is_recorded_in_every_doc():
    """
    風格寫進文件裡，換風格（哪天補上注音）時舊檔才會自動作廢 ——
    不然使用者會拿著新規則的畫面看舊規則算出來的拼音。
    """
    doc = empty_doc("english", REASON_UNSUPPORTED_LANGUAGE, [])
    assert doc["style"] == RUBY_STYLE
    assert doc["version"] == RUBY_VERSION


@pytest.mark.skipif(PINYIN_AVAILABLE, reason="裝了 pypinyin 就走不到退化路徑")
def test_without_pypinyin_the_feature_degrades_quietly():
    doc = build([make_line("我愛你")], "mandarin")
    assert doc["available"] is False
    assert doc["reason"] == REASON_ENGINE_MISSING
