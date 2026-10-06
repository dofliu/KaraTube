"""
介面語言 (UI locales)

這台機器到這一版為止，介面上每一個字都是繁體中文。功能上它已經放得進店裡，
但放不進有外國客人的那幾間 —— 而「外國客人」在 KTV 不是邊緣情況：
商用點歌機（JOYSOUND、DAM）出廠就帶四到六種語言，因為包廂本來就是
觀光客、留學生、外派同事最常被帶去的地方之一。

伺服器在這件事上**刻意只做兩件事**，其餘全部留在瀏覽器端：

  1. 列出有哪些語言（`LOCALES`），以及怎麼把外面送進來的字串夾成其中一個
     （`coerce_locale`）。
  2. 保管**舞台的**語言（存在系統設定的 `stage_locale`）。

翻譯字典本身不在這裡，在 `frontend/js/i18n-catalog.js`。理由是版面：
同一句話的長度在三種語言裡差到一倍（「插播」兩個字 vs. "Play Next"），
會不會撐爆按鈕只有畫面知道，而字典跟版面放在一起才有人會同時改。
伺服器拿不到的東西就不該由伺服器保管。

--------------------------------------------------------------------------
決定一：語言是**誰的**屬性 —— 舞台是包廂的，手機是那個人的

這是整個功能最容易做錯的地方，而且做錯了之後沒有人講得出哪裡怪。

把語言做成「一台機器一個設定」（最直覺的做法）在包廂裡是錯的：
同一間包廂裡會同時有三支手機，其中一支的主人看不懂中文。
一個全域開關的意思是「為了那一位，其他兩位的點歌台也一起變成英文」——
於是沒有人會去動它，功能等於不存在。

反過來，把語言做成純粹「每台裝置自己記」也不夠：**舞台只有一塊螢幕**。
它不屬於誰，它屬於這間包廂，所以它的語言必須是一個大家看得到、
而且改了之後所有人都看到同一個結果的共享設定。

所以兩種所有權同時存在，而且分屬不同的儲存：

  * 舞台（`player.html`）：`stage_locale`，存在伺服器的系統設定裡，
    整間包廂共用，換裝置開舞台也一樣。
  * 點歌台與手機（`index.html`）：存在各自的 `localStorage`，
    誰都不會動到誰。伺服器**不知道**、也不需要知道這個值。

推論出來的一條：點歌台改語言**不會**連帶改舞台。要改舞台得到系統設定頁
（櫃檯管理鎖守著的那一頁）—— 因為那是會影響到其他人的動作。

--------------------------------------------------------------------------
決定二：哪些字不能翻

翻譯最常見的災難不是翻錯，是**把資料當成介面翻掉**。這台機器上有四種字
長得像介面文字，但它們一個字都不能動：

  * 歌名與歌星（「稻香」「周杰倫」）—— 翻掉之後使用者**查不到那首歌**，
    而他會以為曲庫裡沒有。何況它們在曲庫裡的身分就是那串字。
  * 客人取的暱稱 —— 那是人自己打的名字。
  * 包廂名稱、櫃檯打上舞台的跑馬燈訊息 —— 打字的人已經選好語言了。
  * 歌詞 —— 不必解釋。

這件事在前端是用**結構**保證的，不是靠一條記得要遵守的規則：
`t()` 只收 key，資料一律從 `{slot}` 插進去，所以「把歌名送進字典」
這個動作根本做不出來（字典裡沒有那個 key）。
`LOCALE_NEVER_TRANSLATE` 這張表留在這裡當文件與測試的錨點。
"""
from typing import Any, Dict, List, Optional

# 介面語言。每一種都要有「用它自己的文字寫的名字」—— 一個看不懂中文的人
# 在語言選單裡要找的是「日本語」，不是「日文」。
#
# 簡體中文（1.36）排在繁中後面而不是最後：語言鍵是循環切換的，
# 兩種中文相鄰，切錯了按一下就回來。
#
# 為什麼是最早那三種：繁體中文是母語；英語是「看不懂中文的人至少看得懂」的
# 最大公約數；日語則是這台機器的主要客群裡唯一一個**非拉丁字母**的語言 ——
# 挑它當第三種，是為了讓版面在「字母語言」之外也被實際驗證過一次
# （日文的按鈕字長介於中英文之間，而且它會換行在跟中文不同的地方）。
LOCALES: List[Dict[str, str]] = [
    {"code": "zh-TW", "name": "繁體中文", "english": "Traditional Chinese", "flag": "🇹🇼"},
    {"code": "zh-CN", "name": "简体中文", "english": "Simplified Chinese", "flag": "🇨🇳"},
    {"code": "en", "name": "English", "english": "English", "flag": "🇬🇧"},
    {"code": "ja", "name": "日本語", "english": "Japanese", "flag": "🇯🇵"},
]

LOCALE_CODES = tuple(item["code"] for item in LOCALES)

# 基準語言。字典以它為準（每個 key 都一定有），其他語言缺字時回退到這裡。
# 選繁中而不是英文，是因為這個專案的字典是用中文寫的 ——
# 基準語言若設成英文，缺字的那一格會變成「原文沒有、譯文也沒有」。
BASE_LOCALE = "zh-TW"

# 舞台語言的預設值。刻意跟基準語言一樣：沒有人去設定的機器，行為跟這一版之前
# 一模一樣，升級不會讓任何一台現役機器的舞台突然變成英文。
DEFAULT_STAGE_LOCALE = BASE_LOCALE

# 外面進來的字串怎麼對到上面那三個。
#
# 這張表不是「多寫幾個別名比較保險」，它處理的是**瀏覽器真的會送什麼**：
# `navigator.language` 在不同系統上會是 zh-TW / zh-Hant / zh-Hant-TW / zh_TW，
# Accept-Language 還會帶 en-US、en-GB、ja-JP。全部要落在同一格。
#
# 簡體看的是**文字系統**（Hans），不是國家：zh-SG、zh-MY 也寫簡體，
# 而 zh-HK、zh-MO 寫的是繁體 —— 所以香港澳門留在繁中那一格。
# 一個不帶地區的 "zh" 仍然對到繁中（基準語言）：沒有線索的時候，
# 給的是這台機器出廠的樣子，而不是猜一個。
#
# 1.36 之前 zh-CN／zh-Hans 是對到繁中的（看得懂，但不是母語體驗）；
# 這兩行改指向 zh-CN 之後，原本存著 "zh-TW" 的裝置**不受影響** ——
# 存下來的是解析後的代碼，不是瀏覽器原本送的字串，所以沒有人的介面
# 會在升級後突然自己換成簡體。只有還沒選過語言的簡體系統會直接看到簡體。
_ALIASES = {
    "zh": "zh-TW", "zh-tw": "zh-TW", "zh-hant": "zh-TW", "zh-hant-tw": "zh-TW",
    "zh-hk": "zh-TW", "zh-mo": "zh-TW", "zh-hant-hk": "zh-TW",
    "zh-cn": "zh-CN", "zh-hans": "zh-CN", "zh-sg": "zh-CN", "zh-my": "zh-CN",
    "zh-hans-cn": "zh-CN", "zh-hans-sg": "zh-CN",
    "en": "en", "en-us": "en", "en-gb": "en", "en-au": "en", "en-ca": "en",
    "ja": "ja", "ja-jp": "ja", "jp": "ja",
}

# 這幾種東西永遠不翻（見檔頭「決定二」）。這張表是文件，也是測試的錨點：
# 哪天有人在字典裡加了一個叫 `song.title` 的 key，測試會問他這是不是
# 要把歌名翻掉。
LOCALE_NEVER_TRANSLATE = (
    "song_title",      # 歌名：翻掉就查不到那首歌
    "song_artist",     # 歌星：同上，而且曲庫裡的身分就是那串字
    "nickname",        # 客人自己打的名字
    "room_name",       # 包廂名稱（店家自己取的）
    "marquee_text",    # 櫃檯打上舞台的訊息（打字的人已經選好語言了）
    "lyrics",          # 歌詞
)


def normalize_locale(value: Any) -> Optional[str]:
    """
    把一個語言字串對到支援的語言代碼；對不上回 None。

    回 None 而不是直接給預設值，是因為呼叫端對「對不上」的處理不一樣：
    設定檔讀到爛值要夾回預設（`coerce_locale`），
    但瀏覽器送來的 Accept-Language 有三四個候選，對不上的那一個
    應該換下一個試，而不是在第一個就定案。
    """
    if value is None:
        return None
    text = str(value).strip().replace("_", "-").lower()
    if not text:
        return None
    if text in _ALIASES:
        return _ALIASES[text]
    # zh-Hant-TW 這種三段的，逐段往前砍（zh-hant-tw → zh-hant → zh）。
    parts = text.split("-")
    while len(parts) > 1:
        parts.pop()
        head = "-".join(parts)
        if head in _ALIASES:
            return _ALIASES[head]
    return None


def coerce_locale(value: Any, fallback: str = BASE_LOCALE) -> str:
    """
    夾成一個一定存在的語言代碼。設定檔、API body 都走這一條。

    跟這個專案其他的 coerce_* 一樣：爛值一律夾回合法值而不是丟例外。
    語言設定壞掉的失敗模式應該是「介面是中文的」（看得見、改得掉），
    不該是整個設定頁回 500。
    """
    code = normalize_locale(value)
    if code:
        return code
    return fallback if fallback in LOCALE_CODES else BASE_LOCALE


def negotiate_locale(accept_language: Any, fallback: str = BASE_LOCALE) -> str:
    """
    從 HTTP `Accept-Language` 挑一個語言出來。

    用在**第一次**開這一頁、裝置上還沒有存過偏好的時候：一個日文系統的客人
    掃了 QR 進來，應該直接看到日文，而不是先看到一頁中文、自己去找語言鍵。
    （找得到的前提是他看得懂那顆鍵上的字，而那正是他沒有的東西。）

    照 q 值排序。格式爛掉的那一段直接略過而不是整串放棄 —— 這個標頭是
    使用者的瀏覽器送的，我們對它的格式沒有任何保證，而放棄的代價是
    回退到中文，也就是這個函式存在的意義整個消失。
    """
    raw = "" if accept_language is None else str(accept_language)
    candidates = []
    for index, chunk in enumerate(raw.split(",")):
        piece = chunk.strip()
        if not piece:
            continue
        tag, _, params = piece.partition(";")
        quality = 1.0
        for param in params.split(";"):
            key, _, val = param.partition("=")
            if key.strip().lower() == "q":
                try:
                    quality = float(val.strip())
                except (TypeError, ValueError):
                    # q 讀不出來就**當作沒寫**（維持 1.0），不要判成 0。
                    # 判 0 等於把這個語言從候選裡刪掉，而使用者真正想要的
                    # 正是那一個 —— 一個格式有瑕疵的標頭不該讓他拿到中文。
                    quality = 1.0
        tag = tag.strip()
        if not tag or quality <= 0:
            continue
        if tag == "*":
            continue
        # index 當第二排序鍵：同 q 值時照客戶端寫的順序，而不是看字典序。
        candidates.append((-quality, index, tag))
    for _, _, tag in sorted(candidates):
        code = normalize_locale(tag)
        if code:
            return code
    return fallback if fallback in LOCALE_CODES else BASE_LOCALE


def locale_info(stage_locale: Any) -> Dict[str, Any]:
    """
    `GET /api/i18n` 的內容。

    刻意**不**包含翻譯字典本身（字典跟著 `/js/i18n-catalog.js` 走瀏覽器快取，
    不佔每一次開頁的 API 往返），只回答三件事：
    有哪些語言、舞台現在是哪一種、缺字的時候回退到哪一種。
    """
    return {
        "locales": [dict(item) for item in LOCALES],
        "base": BASE_LOCALE,
        "stage_locale": coerce_locale(stage_locale, DEFAULT_STAGE_LOCALE),
        "never_translate": list(LOCALE_NEVER_TRANSLATE),
    }
