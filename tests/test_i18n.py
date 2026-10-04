"""
介面語言的測試。

這個功能的失敗模式跟其他功能不一樣：它不會當掉，它會**默默地少掉幾句話**
（某一種語言漏了幾個 key）或者**多翻了不該翻的東西**（歌名）。
兩種都不會丟例外，所以只有測試擋得住。

這一支釘的是伺服器那一半（語言清單、夾限、Accept-Language 協商），
以及一條把前後端的語言清單綁在一起的守衛 —— 兩邊各列一份的話，
點歌台的語言選單會長出一個伺服器存不進去的選項。
字典本身的完整性與版面寬度在 frontend/tests/i18n.test.js。
"""
import json
import re
from pathlib import Path

import pytest

from backend.services.i18n import (BASE_LOCALE, DEFAULT_STAGE_LOCALE, LOCALE_CODES,
                                   LOCALE_NEVER_TRANSLATE, LOCALES, coerce_locale,
                                   locale_info, negotiate_locale, normalize_locale)
from backend.services.settings import SETTINGS_SPEC

REPO_ROOT = Path(__file__).resolve().parent.parent


# --- 語言清單 ---

def test_base_locale_is_in_the_list():
    assert BASE_LOCALE in LOCALE_CODES
    assert DEFAULT_STAGE_LOCALE in LOCALE_CODES


def test_every_locale_has_a_name_in_its_own_script():
    """
    語言選單上印的必須是那個語言自己的名字。

    一個看不懂中文的人在選單裡要找的是「English」「日本語」，
    不是「英文」「日文」—— 後者他看不懂，而他正是為了這件事來按這顆鍵的。
    """
    for item in LOCALES:
        assert item["code"] and item["name"] and item["english"]
    names = {item["code"]: item["name"] for item in LOCALES}
    assert names["en"] == "English"
    assert names["ja"] == "日本語"
    # 「日文」「英文」這種中文寫法不該出現在 name 欄位
    assert "日文" not in json.dumps(names, ensure_ascii=False)


# --- 正規化與夾限 ---

@pytest.mark.parametrize("raw,expected", [
    ("zh-TW", "zh-TW"), ("zh_TW", "zh-TW"), ("zh-Hant", "zh-TW"),
    ("zh-Hant-TW", "zh-TW"), ("ZH", "zh-TW"), ("  zh-hk  ", "zh-TW"),
    ("en", "en"), ("en-US", "en"), ("en-GB", "en"),
    ("ja", "ja"), ("ja-JP", "ja"), ("JP", "ja"),
])
def test_normalize_handles_what_browsers_actually_send(raw, expected):
    """navigator.language 在不同系統上長得不一樣，但要落在同一格。"""
    assert normalize_locale(raw) == expected


def test_simplified_chinese_falls_back_to_traditional_for_now():
    """
    這一版沒有簡體字典。簡體系統的使用者看繁體看得懂，看英文不一定 ——
    所以 zh-CN 對到繁中而不是被拒絕。補了簡體字典的那一天這條要改。
    """
    assert normalize_locale("zh-CN") == "zh-TW"
    assert normalize_locale("zh-Hans") == "zh-TW"


@pytest.mark.parametrize("raw", ["", None, "   ", "klingon", "xx-YY", 42, {}])
def test_normalize_returns_none_when_it_cannot_tell(raw):
    """
    對不上回 None 而不是預設值：呼叫端手上常常有一串候選
    （navigator.languages 是陣列），對不上的那一個要換下一個試。
    """
    assert normalize_locale(raw) is None


@pytest.mark.parametrize("raw", ["", None, "klingon", 42, [], "zh-Hant-XX-YY"])
def test_coerce_never_raises(raw):
    """
    設定檔被手改壞、舊版前端送了奇怪的值，都不該讓設定頁回 500。
    壞掉的失敗模式應該是「介面是中文的」（看得見、改得掉）。
    """
    assert coerce_locale(raw) == BASE_LOCALE


def test_coerce_respects_an_explicit_fallback():
    assert coerce_locale("klingon", fallback="ja") == "ja"
    # 連 fallback 都不合法的話，退回基準語言而不是把爛值傳出去
    assert coerce_locale("klingon", fallback="klingon") == BASE_LOCALE


# --- Accept-Language 協商 ---

def test_negotiate_picks_the_highest_quality():
    assert negotiate_locale("ja;q=0.9, en;q=0.4, zh-TW;q=0.1") == "ja"


def test_negotiate_keeps_client_order_when_quality_ties():
    """同 q 值時照客戶端寫的順序，不是字典序。"""
    assert negotiate_locale("ja, en") == "ja"
    assert negotiate_locale("en, ja") == "en"


def test_negotiate_skips_languages_we_do_not_have():
    """
    有法文字典之前，一個法文系統的客人該拿到他清單上的下一個語言，
    而不是直接掉回中文。
    """
    assert negotiate_locale("fr-FR, de;q=0.8, en;q=0.5") == "en"


def test_negotiate_survives_a_malformed_header():
    """
    這個標頭是使用者的瀏覽器送的，格式沒有任何保證。
    爛掉的那一段略過就好 —— 整串放棄的代價是回退到中文，
    也就是這個函式存在的意義整個消失。
    """
    assert negotiate_locale("ja;q=abc, en") == "ja"      # q 讀不出來不影響它排在前面
    assert negotiate_locale(",,, ;;; , ja") == "ja"
    assert negotiate_locale("*") == BASE_LOCALE
    assert negotiate_locale("en;q=0") == BASE_LOCALE     # q=0 的意思是「不要這個」
    assert negotiate_locale(None) == BASE_LOCALE


# --- 設定與 API ---

def test_stage_locale_is_a_settings_field_with_the_same_choices():
    """
    舞台的語言是**包廂的**屬性（只有一塊螢幕，不屬於誰），所以它在系統設定裡。
    選項必須就是支援的語言 —— 各列一份的話，設定頁會長出一個存不進去的選項。
    """
    spec = SETTINGS_SPEC["stage_locale"]
    assert spec["type"] == "choice"
    assert tuple(spec["choices"]) == LOCALE_CODES
    assert spec["default"] == DEFAULT_STAGE_LOCALE


def test_locale_info_clamps_a_broken_stage_locale():
    info = locale_info("klingon")
    assert info["stage_locale"] == BASE_LOCALE
    assert [item["code"] for item in info["locales"]] == list(LOCALE_CODES)
    assert info["base"] == BASE_LOCALE


def test_locale_info_does_not_ship_the_dictionary():
    """
    字典跟著 /js/i18n-catalog.js 走瀏覽器快取，不佔每一次開頁的 API 往返，
    而且舞台離線的時候照樣有字可用。
    """
    info = locale_info("ja")
    assert "catalog" not in info and "translations" not in info


# --- 前後端的兩張表要對得起來 ---

CATALOG_JS = REPO_ROOT / "frontend" / "js" / "i18n-catalog.js"
ENGINE_JS = REPO_ROOT / "frontend" / "js" / "i18n.js"


def test_frontend_locale_list_matches_the_backend():
    """
    前端自己也列了一份語言清單（離線開著的舞台不能只靠 API 才知道有哪些語言）。
    兩邊分岔的症狀是：點歌台的選單上有一個語言，但伺服器存不進去。
    """
    src = CATALOG_JS.read_text(encoding="utf-8")
    block = re.search(r"const I18N_LOCALES = \[(.*?)\];", src, re.S)
    assert block, "i18n-catalog.js 裡找不到 I18N_LOCALES"
    codes = re.findall(r'code:\s*"([^"]+)"', block.group(1))
    assert tuple(codes) == LOCALE_CODES


def test_frontend_base_locale_matches_the_backend():
    src = ENGINE_JS.read_text(encoding="utf-8")
    found = re.search(r'const I18N_BASE_LOCALE = "([^"]+)"', src)
    assert found and found.group(1) == BASE_LOCALE


def test_frontend_catalogs_declare_the_same_locales():
    src = CATALOG_JS.read_text(encoding="utf-8")
    block = re.search(r"const I18N_CATALOGS = \{(.*?)\};", src, re.S)
    assert block
    for code in LOCALE_CODES:
        assert f'"{code}"' in block.group(1) or f"{code}:" in block.group(1), code


def test_the_dictionary_never_contains_a_key_for_data():
    """
    翻譯最常見的災難不是翻錯，是把**資料**當成介面翻掉。
    歌名被翻掉的後果最嚴重：使用者從此查不到那首歌，而且他會以為曲庫裡沒有。

    所以字典裡不准出現這幾個名字的 key。真的需要把歌名放進句子，
    走的是 {title} 插槽（值原封不動塞回去），不是把歌名送進字典。
    """
    src = CATALOG_JS.read_text(encoding="utf-8")
    keys = set(re.findall(r'^\s*"([\w.]+)":', src, re.M))
    # 整段比，不是看字尾：`queue.title`（「點歌排隊佇列」那一行標題）是介面文字，
    # 而 `song.title` 是歌名。只比字尾的話前者會被誤判，而一個會誤判的守衛
    # 遲早會被人加一條例外繞過去 —— 然後下一次就是真的歌名溜進字典。
    banned_prefixes = ("song.", "artist.", "lyrics.")
    banned_exact = {"lyrics", "nickname", "room_name", "room.name",
                    "marquee_text", "marquee.text", "song_title", "song_artist"}
    offenders = sorted(k for k in keys
                       if k in banned_exact or k.startswith(banned_prefixes))
    assert not offenders, (
        f"字典裡出現了不該翻的東西：{offenders}。"
        f"這幾種是資料不是介面：{list(LOCALE_NEVER_TRANSLATE)}")
