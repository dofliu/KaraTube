/**
 * 介面語言引擎（純邏輯 + 一個很小的 DOM 套用器）。
 *
 * 字典在 `i18n-catalog.js`，伺服器那一半在 `backend/services/i18n.py`。
 * 這一支負責四件事：挑語言、查字、把字塞回 DOM、以及**守住版面**。
 *
 * ──────────────────────────────────────────────────────────────────
 * 決定一：`t()` 只收 key，資料一律從 {slot} 插進去
 *
 * 翻譯最常見的災難不是翻錯，是把**資料**當成介面翻掉 —— 歌名、歌星、
 * 客人取的暱稱、櫃檯打上舞台的訊息。歌名被翻掉的後果特別嚴重：
 * 使用者從此查不到那首歌，而且他會以為曲庫裡沒有。
 *
 * 所以這裡不提供「翻這段字」這種 API，只提供「查這個 key」。
 * 歌名要進句子就走 `t("queue.added", { title })` —— 字典裡存的是
 * 「已加入：{title}」／"Added: {title}"，那個 {title} 原封不動塞回去。
 * 把歌名送進字典這個動作**做不出來**，因為字典裡沒有那個 key。
 * 這是結構上的保證，不是一條記得要遵守的規則。
 *
 * ──────────────────────────────────────────────────────────────────
 * 決定二：缺字回退到基準語言，但「缺字」本身是要被擋下來的錯
 *
 * 回退鏈是 要的語言 → zh-TW → key 本身。回退到 key（`queue.add`）
 * 很醜，而那是刻意的：醜東西會被看到、會被修；默默回退到中文則會讓
 * 一頁英文介面裡混進三句中文，而翻譯的人永遠不知道漏了哪幾句。
 *
 * 真正的解法在測試裡：`frontend/tests/i18n.test.js` 要求每一種語言
 * 都有基準語言的**每一個** key。所以缺字在 CI 就被擋下來，回退鏈
 * 是給「使用者的瀏覽器快取到舊字典」這種跨版本的情況用的安全網。
 *
 * ──────────────────────────────────────────────────────────────────
 * 決定三：版面 —— 字長差一倍之後按鈕還放不放得下
 *
 * 「插播」兩個字，英文是 "Play Next"。中文兩個字在按鈕上佔 4 個半形寬，
 * 英文佔 9 個 —— 整整一倍多。KTV 的點歌台是觸控的，按鈕不能縮小，
 * 字撐出去的結果是換行、推開旁邊的鍵，或者被 ellipsis 切成 "Play N…"。
 *
 * 所以每一組「有寬度上限」的 key 在字典裡帶一個 `__width` 預算
 * （以半形為 1、全形為 2 計），測試把三種語言一起量過去。
 * 翻譯的人因此在 CI 就知道要改短（"Next" 而不是 "Play Next"），
 * 而不是等到有人把平板拿到包廂裡才發現第二排鍵被擠掉一顆。
 */

// 回退的終點。跟 backend/services/i18n.py 的 BASE_LOCALE 是同一個值，
// 測試（tests/test_i18n.py）把兩邊釘在一起。
const I18N_BASE_LOCALE = "zh-TW";

// 裝置語言存在哪。點歌台與手機各自記各自的 —— 同一間包廂的三支手機
// 各看各的語言，誰都不會動到誰（舞台的語言不走這裡，走伺服器設定）。
const I18N_DEVICE_KEY = "karatube.locale";

// 外面送進來的語言字串怎麼對到支援的語言。這張表要跟 backend 的 _ALIASES
// 對得起來（tests/test_i18n.py 有一條守衛把兩邊的對應結果釘在一起）：
// 對不上的話，同一支手機從 Accept-Language 拿到的語言會跟自己算出來的不同。
const I18N_ALIASES = {
  "zh": "zh-TW", "zh-tw": "zh-TW", "zh-hant": "zh-TW", "zh-hant-tw": "zh-TW",
  "zh-hk": "zh-TW", "zh-mo": "zh-TW", "zh-hant-hk": "zh-TW",
  "zh-cn": "zh-TW", "zh-hans": "zh-TW", "zh-sg": "zh-TW", "zh-hans-cn": "zh-TW",
  "en": "en", "en-us": "en", "en-gb": "en", "en-au": "en", "en-ca": "en",
  "ja": "ja", "ja-jp": "ja", "jp": "ja",
};

/**
 * 把一個語言字串對到支援的語言代碼；對不上回 ""。
 *
 * 回空字串而不是直接給預設值：呼叫端手上通常有一串候選
 * （navigator.languages 是陣列），對不上的那一個要換下一個試，
 * 而不是在第一個就定案成中文。
 */
function normalizeLocale(value) {
  if (value == null) return "";
  const text = String(value).trim().replace(/_/g, "-").toLowerCase();
  if (!text) return "";
  if (I18N_ALIASES[text]) return I18N_ALIASES[text];
  const parts = text.split("-");
  while (parts.length > 1) {
    parts.pop();
    const head = parts.join("-");
    if (I18N_ALIASES[head]) return I18N_ALIASES[head];
  }
  return "";
}

/**
 * 這台裝置該用哪一種語言。
 *
 * 順序是「自己存過的 > 瀏覽器的語言 > 基準語言」，而第二順位是整件事的重點：
 * 一個日文系統的客人掃 QR 進來，應該**直接**看到日文。要他先在一頁中文裡
 * 找到語言鍵才看得懂，前提是他看得懂那顆鍵上的字 —— 而那正是他沒有的東西。
 *
 * `stored` 傳 null 代表「這台裝置還沒選過」，跟「選了中文」是兩回事：
 * 選過中文的日文系統使用者，重開之後要還是中文。
 */
function resolveDeviceLocale(stored, browserLanguages, fallback) {
  const base = normalizeLocale(fallback) || I18N_BASE_LOCALE;
  const picked = normalizeLocale(stored);
  if (picked) return picked;
  const list = Array.isArray(browserLanguages)
    ? browserLanguages
    : (browserLanguages ? [browserLanguages] : []);
  for (const tag of list) {
    const code = normalizeLocale(tag);
    if (code) return code;
  }
  return base;
}

/** 把 {slot} 換成值。沒給的 slot **原樣留著**（見下）。 */
function interpolate(template, vars) {
  const text = String(template == null ? "" : template);
  if (!vars) return text;
  return text.replace(/\{(\w+)\}/g, (whole, name) => {
    const value = vars[name];
    // 給了 undefined / null 就把 {slot} 留在畫面上。換成空字串比較「好看」，
    // 但那會讓「已加入：」這種句子看起來像功能壞了一半而且**沒有線索**；
    // 留著 {title} 的話，看到的人一眼知道是哪一格沒接上。
    return value == null ? whole : String(value);
  });
}

/**
 * 字典查詢器。`catalogs` 是 { "zh-TW": {...}, en: {...}, ja: {...} }。
 *
 * 回傳的 `t` 是一個函式，而不是一個會被四處改狀態的全域物件 ——
 * 舞台（包廂的語言）與點歌台（這台裝置的語言）可能同時存在於同一個
 * 瀏覽器分頁裡（例如把舞台嵌在點歌台旁邊預覽），兩邊各拿各的 `t`，
 * 不會互相覆蓋。
 */
function createTranslator(catalogs, locale, baseLocale) {
  const all = catalogs || {};
  const base = normalizeLocale(baseLocale) || I18N_BASE_LOCALE;
  const code = normalizeLocale(locale) || base;
  const primary = all[code] || {};
  const fallback = all[base] || {};
  function t(key, vars) {
    const name = String(key == null ? "" : key);
    if (!name) return "";
    let raw = primary[name];
    if (raw == null) raw = fallback[name];
    // 連基準語言都沒有：把 key 本身印出來。醜，而且是刻意的 ——
    // 看得到才修得掉（見檔頭決定二）。
    if (raw == null) return name;
    return interpolate(raw, vars);
  }
  t.locale = code;
  t.base = base;
  /** 這個 key 在這個語言裡有沒有自己的字（沒有＝正在吃回退）。 */
  t.has = (key) => Object.prototype.hasOwnProperty.call(primary, String(key));
  return t;
}

/**
 * 顯示寬度（半形 1、全形 2）。
 *
 * 不用 `text.length`：那會說「插播」比 "Play Next" 短 7 格，
 * 而在按鈕上它只短 5 格。版面預算要用看得到的寬度量，不是字元數。
 *
 * CJK、全形標點、假名算 2；emoji 也算 2（🎤 在按鈕上就是一個字的寬度）。
 * 這個估算不精確到像素，但它要擋的是「差一倍」那種量級的問題，
 * 而對那個量級它夠準。
 */
function displayWidth(text) {
  const s = String(text == null ? "" : text);
  let width = 0;
  for (const ch of s) {                     // for..of 走的是 code point，emoji 不會被拆成兩半
    const cp = ch.codePointAt(0);
    if (cp >= 0x1100 && (
      cp <= 0x115f ||                       // 韓文字母
      (cp >= 0x2e80 && cp <= 0xa4cf) ||     // CJK 部首、注音、漢字、假名
      (cp >= 0xac00 && cp <= 0xd7a3) ||     // 韓文音節
      (cp >= 0xf900 && cp <= 0xfaff) ||     // CJK 相容漢字
      (cp >= 0xfe30 && cp <= 0xfe6f) ||     // CJK 相容標點
      (cp >= 0xff00 && cp <= 0xff60) ||     // 全形英數與標點
      (cp >= 0xffe0 && cp <= 0xffe6) ||
      (cp >= 0x1f300 && cp <= 0x1faff)      // emoji 本區
    )) width += 2;
    // 雜項技術符號與雜項圖形（⏱ ⏭ ⏸ ⭐ ✅ …）。它們的碼位排在 CJK 之前，
    // 所以上面那串 cp >= 0x1100 的判斷接不到 —— 而這台機器的按鈕上到處都是
    // 這幾顆（⏱️ 開始計時、⏭ 切歌、⭐ 我的最愛）。漏算的話版面預算會低估
    // 中文那一欄的寬度，於是英文超標的那幾句反而通過。
    else if ((cp >= 0x2300 && cp <= 0x23ff) ||
             (cp >= 0x2600 && cp <= 0x27bf) ||
             (cp >= 0x2b00 && cp <= 0x2bff)) width += 2;
    else if (cp >= 0xfe00 && cp <= 0xfe0f) width += 0;   // 變體選擇符本身沒有寬度
    else width += 1;
  }
  return width;
}

/**
 * 量一份字典有沒有撐爆版面。
 *
 * `budgets` 是 { key: 最大顯示寬度 }。回傳超標的清單（給測試印出來），
 * 空陣列＝都放得下。回傳而不是丟例外：一次要看到**全部**超標的 key，
 * 翻譯的人才能一輪改完，而不是修一個、再跑一次、再看到下一個。
 */
function overflowingKeys(catalog, budgets) {
  const out = [];
  const table = budgets || {};
  const dict = catalog || {};
  for (const key of Object.keys(table)) {
    const limit = Number(table[key]) || 0;
    if (limit <= 0) continue;
    const text = dict[key];
    if (text == null) continue;             // 缺字由完整性測試負責，不在這裡重複叫
    const width = displayWidth(text);
    if (width > limit) out.push({ key, text, width, limit });
  }
  return out;
}

/**
 * 把翻譯套回 DOM。
 *
 * 只認三種標記，而且都是**屬性**而不是把字寫死在 HTML 裡：
 *   data-i18n             → textContent
 *   data-i18n-title       → title（滑鼠提示；這台機器上很多說明文字在這裡）
 *   data-i18n-placeholder → placeholder（搜尋框）
 *
 * 為什麼不做 innerHTML 版本：那會讓字典變成可以塞標籤的地方，
 * 而字典是會被翻譯的人（不一定是工程師）改的檔案。
 * 真的需要粗體的句子就拆成兩個 key，或者留在程式裡組。
 *
 * `root` 只需要有 `querySelectorAll`，所以測試不必起一個真的瀏覽器。
 */
function applyTranslations(root, t) {
  if (!root || typeof root.querySelectorAll !== "function" || typeof t !== "function") {
    return 0;
  }
  let count = 0;
  const jobs = [
    ["data-i18n", (el, text) => { el.textContent = text; }],
    ["data-i18n-title", (el, text) => {
      if (typeof el.setAttribute === "function") el.setAttribute("title", text);
      else el.title = text;
    }],
    ["data-i18n-placeholder", (el, text) => {
      if (typeof el.setAttribute === "function") el.setAttribute("placeholder", text);
      else el.placeholder = text;
    }],
  ];
  for (const [attr, assign] of jobs) {
    const nodes = root.querySelectorAll("[" + attr + "]") || [];
    for (const el of Array.from(nodes)) {
      const key = typeof el.getAttribute === "function"
        ? el.getAttribute(attr)
        : (el.dataset || {})[attr];
      if (!key) continue;
      assign(el, t(key));
      count++;
    }
  }
  return count;
}

/**
 * `<html lang>` 要跟著改。
 *
 * 這不是裝飾：瀏覽器照 lang 決定斷行規則與預設字型，螢幕閱讀器照它決定
 * 用哪一種發音念。一頁英文介面掛著 lang="zh-TW"，朗讀出來是中文腔的英文。
 */
function documentLangFor(locale) {
  const code = normalizeLocale(locale) || I18N_BASE_LOCALE;
  return code;
}

/**
 * 語言鍵上要印什麼。
 *
 * 印的是**那個語言自己的名字**（English / 日本語 / 繁體中文），不是
 * 「英文」「日文」—— 一個看不懂中文的人在選單裡要找的是看得懂的那一個。
 * 這也是為什麼語言選單是整個介面裡唯一一處**不跟著語言變**的文字。
 */
function localeLabel(locale, locales) {
  const code = normalizeLocale(locale) || I18N_BASE_LOCALE;
  for (const item of (locales || [])) {
    if (item && item.code === code) return (item.flag ? item.flag + " " : "") + item.name;
  }
  return code;
}

if (typeof window !== "undefined") {
  window.I18n = {
    I18N_BASE_LOCALE, I18N_DEVICE_KEY,
    normalizeLocale, resolveDeviceLocale, interpolate, createTranslator,
    displayWidth, overflowingKeys, applyTranslations, documentLangFor, localeLabel,
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    I18N_BASE_LOCALE, I18N_DEVICE_KEY, I18N_ALIASES,
    normalizeLocale, resolveDeviceLocale, interpolate, createTranslator,
    displayWidth, overflowingKeys, applyTranslations, documentLangFor, localeLabel,
  };
}
