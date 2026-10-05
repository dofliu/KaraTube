"""
歌詞拼音標註 (Ruby lyrics)

1.34 把**介面**翻成了三種語言，但包廂裡那位看不懂中文的客人還是唱不出來：
按鈕上寫著 "Play Next"，歌詞上寫著「稻香」—— 他點得到歌，卻一個字都唸不出口。
商用點歌機早就有這一格（JOYSOUND 的「ふりがな」與「ローマ字」、DAM 的
ルビ表示）：**同一串字，上面多一行讀音**。這支就是那一行。

它不是翻譯。歌詞一個字都沒有被換掉 —— 換掉就違反 i18n.py 決定二的
「歌詞不翻」。拼音是**同一串字的讀音**，標在字的上面，底下那個字原封不動。

--------------------------------------------------------------------------
決定一：只標國語歌。標錯比不標更糟

pypinyin 給的是**華語（普通話）**讀音。把它套到台語歌、粵語歌上，
出來的每一個音節都是錯的：粵語的「我」唸 ngo5，標成 wǒ 的人照著唸出來的
是另一種語言 —— 而且他不會發現，因為他本來就不會唸。

這是一個**無聲的錯**，而且兩種漏法的代價不對稱：
  * 沒標：他知道自己不會唸，於是看旁邊的人怎麼唱。
  * 標錯：他以為自己會唸，於是大聲唱了一整首別的東西。

日語歌更乾脆地放棄：漢字的讀音要有詞典（而且同一個漢字在人名、地名、
歌詞裡的讀法都不一樣），純假名那一半轉羅馬字是機械性的，但**半首歌有拼音、
半首沒有**是最糟的版面 —— 使用者會以為沒拼音的那幾個字是不用唱的。

所以 `RUBY_LANGUAGES` 只有一格。其餘語言回一份 `available: False` 的文件
並帶上理由，讓畫面說得出「這首歌沒有拼音」而不是默默空白
（默默空白跟「功能壞了」在螢幕上長得一樣）。

--------------------------------------------------------------------------
決定二：多音字在這裡要**挑一個**，跟查歌索引剛好相反

`song_index.py` 對多音字存的是**所有讀音的首碼集合**（「重」= ㄓ ∪ ㄔ），
因為查詢要寬鬆：漏掉一個讀音，用那個讀音去按鍵盤的人就永遠查不到那首歌。

歌詞標注的方向剛好相反：**螢幕上一個字只有一行字的空間**，給兩個讀音等於
沒給（"chóng/zhòng" 擠在 46px 的字上面，誰也看不懂）。所以這裡走
pypinyin 的詞組消歧（預設模式，不開 heteronym）：「重新」→ chóng、
「重視」→ zhòng，靠的是前後字而不是單字的最常見讀音。

這一點值得寫下來，因為下一個讀到這兩支的人第一個念頭會是「統一一下」。
統一的方向不論哪一邊都是錯的：索引吃單一讀音會讓人查不到歌，
標注吃全部讀音會讓畫面變成一團。

--------------------------------------------------------------------------
決定三：拼音存成檔案，但**對不上就重算**，不靠任何人記得去刪它

拼音算一次要幾毫秒，存成 `ruby.json` 放在歌的資料夾裡（跟 lyrics.json
同一層，刪快取就跟著消失，不留孤兒）。問題在於 `lyrics.json` 是**會被換掉的**
（重算歌詞、兩點校正之後重新對齊），而換掉之後舊的拼音會**對到別的字上** ——
第三個字的拼音標在第五個字頭上，而且畫面上看起來完全正常。

這種錯不能靠「記得在重算歌詞時一起刪掉 ruby.json」來防：那是一條寫在另一個
檔案裡、三個月後沒有人記得的規則。所以改成結構保證 —— 每份拼音文件帶著
它當初是從哪一串字算出來的指紋（`fingerprint`），讀的時候重算一次指紋比對，
對不上就當作沒有這個檔案。重算歌詞的那條路因此**完全不必知道拼音的存在**。

--------------------------------------------------------------------------
決定四：空的那幾格要留著，不能擠掉

英文、數字、標點沒有拼音，那幾格回空字串而**不是跳過** ——
拼音陣列與歌詞的逐字陣列必須是同一個長度、同一個索引。
長度對不上時前端整行不標（`frontend/js/ruby-layout.js`），
因為「少一格」的症狀正好是決定三那種「整行往後位移一個字」的無聲錯誤。
"""
import hashlib
import logging
import re
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger("KaraTube.Ruby")

try:  # pypinyin 已經是曲庫查歌（song_index）的依賴，這裡不多帶一個套件進來
    from pypinyin import Style as _Style, pinyin as _pinyin
    PINYIN_AVAILABLE = True
except Exception:  # pragma: no cover - 只有沒裝套件的環境會走到
    _Style = None
    _pinyin = None
    PINYIN_AVAILABLE = False

# 文件格式版本。改了標注規則（換風格、換語言範圍）就 +1，
# 讓已經算好的舊檔自動作廢重算 —— 不加版本的話，使用者會拿著新規則的畫面
# 看舊規則算出來的拼音，而且查不出為什麼只有某幾首不一樣。
RUBY_VERSION = 1

# 標注風格。目前只有一種：帶聲調符號的漢語拼音（wǒ、ài）。
#
# 為什麼不是注音符號：注音是**全形而且直排**的，疊在 46px 的字上面要四格高；
# 更關鍵的是，看得懂注音的人本來就認得那幾個字 —— 真正需要這一行的是
# 看不懂中文的那一位，而他只讀得懂羅馬字母。
RUBY_STYLE = "pinyin"

# 哪些語言標得準。只有一格，理由見檔頭決定一。
RUBY_LANGUAGES = ("mandarin",)

# 舞台要不要顯示拼音。三個選項而不是一個開關，因為「要不要」的答案
# 其實寫在另一個已經有的設定裡：舞台的介面語言就是「這塊螢幕現在是給誰看的」。
#   off  —— 一律不顯示（店家不要這一行，或者螢幕本來就小）
#   auto —— 舞台語言不是中文時才顯示（預設：有外國客人才出現）
#   on   —— 一律顯示（教小孩唱歌、帶長輩唱不認得的字）
RUBY_MODE_CHOICES = ("off", "auto", "on")
DEFAULT_RUBY_MODE = "auto"

# 沒有拼音時的理由代碼。畫面要說得出哪一種 —— 默默空白跟「功能壞了」
# 在螢幕上長得一模一樣。
REASON_OK = ""
REASON_UNSUPPORTED_LANGUAGE = "unsupported_language"
REASON_ENGINE_MISSING = "engine_missing"
REASON_NO_LYRICS = "no_lyrics"

# 漢字範圍。只對這些字查讀音，其餘（英數、假名、諺文、標點）一律留空格。
_HAN = re.compile(r"[一-鿿㐀-䶿]")


def is_supported_language(language: Any) -> bool:
    """這個語言別標得準嗎。認不得的語言一律當成「標不準」。"""
    return isinstance(language, str) and language in RUBY_LANGUAGES


def annotate_text(text: str) -> List[str]:
    """
    一串字 → 一個等長的拼音陣列。沒有讀音的位置是空字串。

    實作上**整段漢字一起送進 pypinyin**（而不是一個字一個字送）：
    詞組消歧要看得到前後字才work得出「重新 = chóng」與「重視 = zhòng」的差別，
    單字送進去拿到的永遠是那個字最常見的讀音。
    所以這裡先把連續的漢字切成一段一段，每段整個送，再照位置攤回去。

    回傳長度保證等於 `len(text)`（見檔頭決定四）。
    """
    chars = list(text or "")
    out = [""] * len(chars)
    if not chars or not PINYIN_AVAILABLE:
        return out

    i = 0
    n = len(chars)
    while i < n:
        if not _HAN.match(chars[i]):
            i += 1
            continue
        j = i
        while j < n and _HAN.match(chars[j]):
            j += 1
        run = "".join(chars[i:j])
        try:
            got = _pinyin(run, style=_Style.TONE)
        except Exception as exc:  # pragma: no cover - pypinyin 內部錯誤
            logger.warning("拼音標注失敗，這一段留空：%s", exc)
            got = []
        # pypinyin 對全漢字輸入會回傳「一個字一格」，但這是它的行為、不是契約；
        # 長度對不上時整段留空而不是硬塞 —— 錯位的拼音比沒有拼音更糟。
        if len(got) == (j - i):
            for k, item in enumerate(got):
                syllable = (item[0] if item else "") or ""
                # 查不到讀音時 pypinyin 會把原字回傳（errors='default'）。
                # 把漢字當拼音標在漢字上面是沒有意義的一行，留空。
                if syllable and not _HAN.search(syllable):
                    out[i + k] = syllable
        i = j
    return out


def line_chars(line: Dict[str, Any]) -> str:
    """
    一行歌詞在畫面上實際被拆成哪些字。

    舞台端的渲染順序是「有 `words` 就用 `words`，沒有才退回 `text` 拆字」
    （見 frontend/js/karaoke-renderer.js），所以拼音也要照同一個順序取 ——
    兩邊各自決定的話，遇到 `words` 與 `text` 不一致的那幾行就會整行位移。
    """
    words = line.get("words") if isinstance(line, dict) else None
    if isinstance(words, list) and words:
        return "".join(str(w.get("char", "")) for w in words if isinstance(w, dict))
    return str((line or {}).get("text", "") or "")


def fingerprint(lines: Sequence[Dict[str, Any]]) -> str:
    """
    這份歌詞的指紋。歌詞被換掉（重算歌詞、重新對齊）之後指紋就不一樣，
    已經算好的拼音因此自動作廢（見檔頭決定三）。

    只看**字**不看時間：調字幕偏移、兩點校正改的是每個字的 start/end，
    字本身沒變，拼音當然也沒變 —— 把時間算進指紋的話，每調一次字幕
    就要把整首歌的拼音重算一次。
    """
    h = hashlib.sha1()
    h.update(f"v{RUBY_VERSION}|{RUBY_STYLE}\n".encode("utf-8"))
    for line in lines or []:
        h.update(line_chars(line).encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()[:16]


def empty_doc(language: Any, reason: str,
              lines: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """沒有拼音的那一份文件。`reason` 讓畫面說得出為什麼（而不是默默空白）。"""
    return {
        "version": RUBY_VERSION,
        "style": RUBY_STYLE,
        "language": language if isinstance(language, str) else "",
        "available": False,
        "reason": reason,
        "fingerprint": fingerprint(lines or []),
        "lines": [],
    }


def build(lines: Sequence[Dict[str, Any]], language: Any) -> Dict[str, Any]:
    """
    算出整首歌的拼音文件。這是純函數：同一份歌詞永遠得到同一份結果。

    算不出來不是錯誤，是一種結果 —— 回 `available: False` 加上理由，
    呼叫端照樣可以存下來（存下來才不會每次載歌都重算一次日語歌）。
    """
    if not lines:
        return empty_doc(language, REASON_NO_LYRICS, lines)
    if not is_supported_language(language):
        return empty_doc(language, REASON_UNSUPPORTED_LANGUAGE, lines)
    if not PINYIN_AVAILABLE:
        return empty_doc(language, REASON_ENGINE_MISSING, lines)

    out_lines = [annotate_text(line_chars(line)) for line in lines]
    # 整首歌一個拼音都沒有（歌詞全是英文、或者只有「請跟隨伴奏音樂盡情歡唱」
    # 那種佔位詞）就當作沒有：留一份全空的陣列只會讓舞台為它保留一整排高度。
    if not any(any(cell for cell in row) for row in out_lines):
        return empty_doc(language, REASON_NO_LYRICS, lines)

    return {
        "version": RUBY_VERSION,
        "style": RUBY_STYLE,
        "language": language,
        "available": True,
        "reason": REASON_OK,
        "fingerprint": fingerprint(lines),
        "lines": out_lines,
    }


def is_stale(doc: Any, lines: Sequence[Dict[str, Any]], language: Any) -> bool:
    """
    存檔裡那份拼音還能用嗎。四件事任何一件對不上就重算：
    格式版本、標注風格、語言別、歌詞指紋。

    刻意不做「部分修補」：拼音便宜（幾毫秒），而修補的程式碼是那種
    寫完就沒有人會再讀、但會無聲地把位置弄錯的東西。
    """
    if not isinstance(doc, dict):
        return True
    if doc.get("version") != RUBY_VERSION or doc.get("style") != RUBY_STYLE:
        return True
    # 語言別是歌的屬性，分類完成（或被重新判定）之後會變 ——
    # 從 other 變成 mandarin 的那一首，這時候才會長出拼音。
    if doc.get("language") != (language if isinstance(language, str) else ""):
        return True
    if doc.get("fingerprint") != fingerprint(lines):
        return True
    # 說自己有拼音、卻跟歌詞不等長的文件一律不信（決定四）。
    if doc.get("available"):
        rows = doc.get("lines")
        if not isinstance(rows, list) or len(rows) != len(lines):
            return True
    return False


def coerce_mode(value: Any, fallback: str = DEFAULT_RUBY_MODE) -> str:
    """外面送進來的顯示模式夾成合法值。認不得就退回預設，不丟例外。"""
    text = str(value or "").strip().lower()
    return text if text in RUBY_MODE_CHOICES else fallback
