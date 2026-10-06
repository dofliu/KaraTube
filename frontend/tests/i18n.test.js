/**
 * 介面語言的測試（引擎 + 字典 + 版面）。
 *
 * 這個功能不會當掉，它會**默默地少掉幾句話**或**多翻了不該翻的東西**，
 * 兩種都不丟例外。所以三件事只有測試擋得住：
 *
 *   1. 每一種語言都要有基準語言的每一個 key（缺字 → 一頁英文裡混進中文）。
 *   2. 按鈕上的字不能撐爆版面（「插播」4 格 vs. "Play Next" 9 格）。
 *   3. 字典裡不准出現歌名、歌星、暱稱這些**資料**（翻掉就查不到那首歌）。
 *
 * 伺服器那一半（語言清單、夾限、Accept-Language）在 tests/test_i18n.py，
 * 那邊還有一條守衛把兩邊的語言清單釘在一起。
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const {
  I18N_BASE_LOCALE,
  normalizeLocale, resolveDeviceLocale, interpolate, createTranslator,
  displayWidth, overflowingKeys, applyTranslations, documentLangFor, localeLabel,
} = require("../js/i18n.js");

const {
  I18N_CATALOGS, I18N_LOCALES, I18N_WIDTH_BUDGET, I18N_ZH_TW, I18N_ZH_CN,
} = require("../js/i18n-catalog.js");

const FRONTEND = path.join(__dirname, "..");

// --- 挑語言 ---

test("瀏覽器送什麼都落在同一格", () => {
  assert.equal(normalizeLocale("zh-TW"), "zh-TW");
  assert.equal(normalizeLocale("zh_TW"), "zh-TW");
  assert.equal(normalizeLocale("zh-Hant-TW"), "zh-TW");
  assert.equal(normalizeLocale("EN-us"), "en");
  assert.equal(normalizeLocale("ja-JP"), "ja");
  assert.equal(normalizeLocale("klingon"), "");
  assert.equal(normalizeLocale(null), "");
});

test("還沒選過的裝置跟著瀏覽器的語言走", () => {
  // 日文系統的客人掃 QR 進來，第一眼就該是日文 —— 要他先在一頁中文裡
  // 找到語言鍵才看得懂，前提是他看得懂那顆鍵上的字，而那正是他沒有的東西。
  assert.equal(resolveDeviceLocale(null, ["ja-JP", "en-US"], I18N_BASE_LOCALE), "ja");
  // 清單上前面幾個我們沒有，就往下找，而不是直接掉回中文
  assert.equal(resolveDeviceLocale(null, ["fr-FR", "de", "en-GB"], I18N_BASE_LOCALE), "en");
  // 全部都沒有才回基準語言
  assert.equal(resolveDeviceLocale(null, ["fr", "de"], I18N_BASE_LOCALE), I18N_BASE_LOCALE);
});

test("選過的語言贏過瀏覽器的語言", () => {
  // 日文系統的人刻意選了中文，重開之後要還是中文。
  assert.equal(resolveDeviceLocale("zh-TW", ["ja-JP"], I18N_BASE_LOCALE), "zh-TW");
  // 存壞了（手改 localStorage、舊版留下來的值）則視同沒選過
  assert.equal(resolveDeviceLocale("klingon", ["ja"], I18N_BASE_LOCALE), "ja");
});

test("localStorage 讀不到（無痕模式）不會變成「鎖在中文」", () => {
  // 讀不到時呼叫端傳 null 進來，而 null 的意思是「還沒選過」——
  // 於是照樣跟著瀏覽器的語言走。
  assert.equal(resolveDeviceLocale(null, ["ja"], I18N_BASE_LOCALE), "ja");
});

// --- 查字與插槽 ---

test("{slot} 原樣塞回去：歌名不經過字典", () => {
  assert.equal(interpolate("已加入：{title}", { title: "稻香" }), "已加入：稻香");
  assert.equal(interpolate("Added: {title}", { title: "稻香" }), "Added: 稻香");
});

test("沒給值的 {slot} 留在畫面上，不要換成空字串", () => {
  // 「已加入：」這種半句話看起來像功能壞了一半，而且**沒有線索**；
  // 留著 {title} 的話，看到的人一眼知道是哪一格沒接上。
  assert.equal(interpolate("已加入：{title}", {}), "已加入：{title}");
  assert.equal(interpolate("已加入：{title}", { title: null }), "已加入：{title}");
  // 空字串是「真的沒有名字」，跟沒給是兩回事
  assert.equal(interpolate("已加入：{title}", { title: "" }), "已加入：");
});

test("查不到的 key 就把 key 印出來（醜是刻意的）", () => {
  const t = createTranslator(I18N_CATALOGS, "en", I18N_BASE_LOCALE);
  assert.equal(t("no.such.key"), "no.such.key");
  assert.equal(t(""), "");
  assert.equal(t(null), "");
});

test("缺字回退到基準語言", () => {
  const catalogs = { "zh-TW": { "a.b": "中文" }, en: {} };
  const t = createTranslator(catalogs, "en", "zh-TW");
  assert.equal(t("a.b"), "中文");
  assert.equal(t.has("a.b"), false);       // 正在吃回退，不是它自己的字
});

test("兩個翻譯器互不影響（舞台與點歌台可能同時在同一頁）", () => {
  const stage = createTranslator(I18N_CATALOGS, "ja", I18N_BASE_LOCALE);
  const desk = createTranslator(I18N_CATALOGS, "en", I18N_BASE_LOCALE);
  assert.equal(stage.locale, "ja");
  assert.equal(desk.locale, "en");
  assert.notEqual(stage("stage.recording"), desk("stage.recording"));
});

// --- 字典完整性 ---

test("每一種語言都有基準語言的每一個 key", () => {
  const baseKeys = Object.keys(I18N_ZH_TW).sort();
  for (const [code, catalog] of Object.entries(I18N_CATALOGS)) {
    const missing = baseKeys.filter((k) => !(k in catalog));
    assert.deepEqual(missing, [], `${code} 缺了 ${missing.length} 個 key：${missing.join(", ")}`);
  }
});

test("字典裡沒有基準語言沒有的 key（改名時漏掉的那一半）", () => {
  const baseKeys = new Set(Object.keys(I18N_ZH_TW));
  for (const [code, catalog] of Object.entries(I18N_CATALOGS)) {
    const extra = Object.keys(catalog).filter((k) => !baseKeys.has(k));
    assert.deepEqual(extra, [], `${code} 多了基準語言沒有的 key：${extra.join(", ")}`);
  }
});

test("沒有一句翻譯是原封不動抄過去的中文", () => {
  // 抄過去（偷懶或複製貼上漏改）在畫面上跟「翻好了」長得一模一樣，
  // 而且完整性測試抓不到 —— key 是在的。
  // emoji 與純符號不算（「🎤」在三種語言裡本來就一樣）。
  const hasCjk = (s) => /[㐀-鿿぀-ヿ]/.test(s);
  for (const code of ["en"]) {
    const copied = Object.keys(I18N_ZH_TW).filter(
      (k) => I18N_CATALOGS[code][k] === I18N_ZH_TW[k] && hasCjk(I18N_ZH_TW[k]));
    assert.deepEqual(copied, [], `${code} 有幾句直接抄了中文：${copied.join(", ")}`);
  }
});

// --- 簡體中文：不是把繁體逐字換成簡體 ---

// 繁中字典裡出現過、而且在簡體裡寫法不同的每一個字（用 OpenCC t2s 量出來的，
// 不是手挑的）。簡體字典裡出現其中任何一個，就是「複製繁中、只改了一半」。
// 繁中字典加了新字的時候這張表不會自己長大 —— 那一半由下面的
// 「跟繁中一字不差要列名」那條守衛接住。
const TRADITIONAL_ONLY =
  "並佇佔來個倫備別剛動務勢匯啟單嘯場塊尋對導帳帶幾庫廂張強後從愛換擊數時暫" +
  "會條機檔檯櫃歡歷歸沒減滿潔瀏為現環碼稱組結統經網線練總績續聲聽與薦處號螢" +
  "裝裡覽計訊設評話該認語說誰請識議變讓貼趨輪輸這進過遠選還鈴錄鎖開間隊隨響頁" +
  "預額類顯風麥麼點";

// 兩岸本來就同形的那幾句。不在這張表裡卻跟繁中一字不差的，就是漏翻。
// （完整性測試抓不到這種：key 是在的，字也是中文的。）
const SAME_IN_BOTH_SCRIPTS = new Set([
  "lib.find", "lib.voice", "lib.contest", "queue.quota_up_hint", "queue.rotation_reset",
  "deck.restart", "deck.play", "deck.skip", "deck.skip_hint", "deck.idle_title",
  "stage.pitch_fix",
]);

// 寫成簡體字的台灣用詞。逐字轉換做得出這些詞，而它們每一個字都是簡體 ——
// 所以「沒有繁體字」那條守衛擋不到，只能照詞擋。
// 右邊是對岸的說法，給補字的人參考。
const TAIWAN_TERMS = {
  "快取": "缓存", "萤幕": "屏幕", "伫列": "队列", "预设": "默认", "影片": "视频",
  "设定": "设置", "暱称": "昵称", "本机": "本地", "伺服器": "服务器", "网址": "链接",
  "柜台": "前台", "柜檯": "前台", "音档": "音频文件", "讯息": "消息", "滑鼠": "鼠标",
  "介面": "界面", "装置": "设备", "档案": "文件", "网路": "网络", "软体": "软件",
  "支援": "支持", "即时": "实时", "贴上": "粘贴", "歌星": "歌手", "按一下": "点一下",
};

test("简体字典裡沒有殘留的繁體字", () => {
  const bad = [];
  for (const [key, text] of Object.entries(I18N_ZH_CN)) {
    const found = Array.from(text).filter((ch) => TRADITIONAL_ONLY.includes(ch));
    if (found.length) bad.push(`${key}: ${found.join("")}「${text}」`);
  }
  assert.deepEqual(bad, [], `zh-CN 還有繁體字：\n  ${bad.join("\n  ")}`);
});

test("简体字典裡沒有台灣用詞（字是簡體，詞是台灣的）", () => {
  const bad = [];
  for (const [key, text] of Object.entries(I18N_ZH_CN)) {
    for (const [term, mainland] of Object.entries(TAIWAN_TERMS)) {
      if (text.includes(term)) bad.push(`${key}: 「${term}」→ 對岸說「${mainland}」`);
    }
  }
  assert.deepEqual(bad, [], `zh-CN 有台灣用詞：\n  ${bad.join("\n  ")}`);
});

test("跟繁中一字不差的句子要列名（否則跟漏翻分不出來）", () => {
  const copied = Object.keys(I18N_ZH_TW).filter(
    (k) => I18N_ZH_CN[k] === I18N_ZH_TW[k] && !SAME_IN_BOTH_SCRIPTS.has(k));
  assert.deepEqual(copied, [], `zh-CN 這幾句跟繁中一模一樣，卻沒有列在同形清單裡：${copied.join(", ")}`);
  // 反過來：列名的那幾句要真的還一樣。繁中改了字之後清單沒跟著清，
  // 這張表就會慢慢變成「漏翻的通行證」。
  const stale = [...SAME_IN_BOTH_SCRIPTS].filter((k) => I18N_ZH_CN[k] !== I18N_ZH_TW[k]);
  assert.deepEqual(stale, [], `同形清單裡這幾句已經不一樣了，請移出清單：${stale.join(", ")}`);
});

test("繁體字表本身沒有混進簡體也有的字（守衛不能誤殺）", () => {
  // 例如「後」「着」：前者只在繁體，後者兩岸都用。把兩岸通用的字放進表裡，
  // 簡體字典就永遠過不了，而下一個人的解法會是把這條測試刪掉。
  const zhCnChars = new Set(Object.values(I18N_ZH_CN).join(""));
  const overlap = Array.from(TRADITIONAL_ONLY).filter((ch) => zhCnChars.has(ch));
  assert.deepEqual(overlap, []);
  assert.equal(new Set(TRADITIONAL_ONLY).size, Array.from(TRADITIONAL_ONLY).length, "表裡有重複的字");
});

test("簡體系統的瀏覽器直接看到簡體，香港澳門留在繁體", () => {
  assert.equal(normalizeLocale("zh-CN"), "zh-CN");
  assert.equal(normalizeLocale("zh-Hans"), "zh-CN");
  assert.equal(normalizeLocale("zh_SG"), "zh-CN");
  assert.equal(normalizeLocale("zh-Hans-HK"), "zh-CN");
  assert.equal(normalizeLocale("zh-HK"), "zh-TW");
  assert.equal(normalizeLocale("zh-Hant-CN"), "zh-TW");
  assert.equal(normalizeLocale("zh"), "zh-TW");
  assert.equal(resolveDeviceLocale(null, ["zh-CN", "en"], I18N_BASE_LOCALE), "zh-CN");
  // 1.35 時代存下來的 "zh-TW" 不會在升級後自己變成簡體
  assert.equal(resolveDeviceLocale("zh-TW", ["zh-CN"], I18N_BASE_LOCALE), "zh-TW");
  assert.ok(localeLabel("zh-CN", I18N_LOCALES).includes("简体中文"));
});

// --- 版面寬度 ---

test("顯示寬度用的是看得到的寬度，不是字元數", () => {
  assert.equal(displayWidth("插播"), 4);
  assert.equal(displayWidth("Play Next"), 9);
  assert.equal(displayWidth("🎤"), 2);
  assert.equal(displayWidth(""), 0);
  assert.equal(displayWidth(null), 0);
  // 變體選擇符（⏱️ 的那個 U+FE0F）本身沒有寬度，不要算進去
  assert.equal(displayWidth("⏱️"), 2);
});

test("三種語言的按鈕都放得下（版面預算）", () => {
  for (const [code, catalog] of Object.entries(I18N_CATALOGS)) {
    const over = overflowingKeys(catalog, I18N_WIDTH_BUDGET);
    const report = over.map((o) => `${o.key}="${o.text}" ${o.width}>${o.limit}`);
    assert.deepEqual(report, [], `${code} 有 ${over.length} 句撐爆版面：\n  ${report.join("\n  ")}`);
  }
});

test("版面預算只列字典裡有的 key", () => {
  const unknown = Object.keys(I18N_WIDTH_BUDGET).filter((k) => !(k in I18N_ZH_TW));
  assert.deepEqual(unknown, [], `預算表列了字典裡沒有的 key：${unknown.join(", ")}`);
});

// --- 套用到 DOM ---

/** 最小的假 DOM：只要有 querySelectorAll / getAttribute 就夠了。 */
function fakeRoot(nodes) {
  return {
    querySelectorAll(selector) {
      const attr = selector.replace(/^\[|\]$/g, "");
      return nodes.filter((n) => n.attrs[attr] != null);
    },
  };
}
function fakeNode(attrs) {
  return {
    attrs,
    textContent: "",
    set: {},
    getAttribute(name) { return this.attrs[name]; },
    setAttribute(name, value) { this.set[name] = value; },
  };
}

test("data-i18n 換 textContent、title 與 placeholder 換屬性", () => {
  const t = createTranslator(I18N_CATALOGS, "en", I18N_BASE_LOCALE);
  const text = fakeNode({ "data-i18n": "search.button" });
  const title = fakeNode({ "data-i18n-title": "deck.skip_hint" });
  const place = fakeNode({ "data-i18n-placeholder": "search.placeholder" });
  const count = applyTranslations(fakeRoot([text, title, place]), t);
  assert.equal(count, 3);
  assert.equal(text.textContent, "Search");
  assert.equal(title.set.title, t("deck.skip_hint"));
  assert.equal(place.set.placeholder, t("search.placeholder"));
});

test("套用器對爛輸入不丟例外（半路載入失敗時整頁不該停住）", () => {
  assert.equal(applyTranslations(null, () => "x"), 0);
  assert.equal(applyTranslations(fakeRoot([]), null), 0);
  assert.equal(applyTranslations({}, () => "x"), 0);
});

test("同一顆元素可以同時帶字與提示", () => {
  const t = createTranslator(I18N_CATALOGS, "ja", I18N_BASE_LOCALE);
  const el = fakeNode({ "data-i18n": "header.settings", "data-i18n-title": "header.settings_hint" });
  assert.equal(applyTranslations(fakeRoot([el]), t), 2);
  assert.equal(el.textContent, "⚙️ 設定");
  assert.equal(el.set.title, t("header.settings_hint"));
});

// --- HTML 上的 key 一定要在字典裡 ---

test("HTML 裡標的每一個 key 字典裡都有", () => {
  // 漏掉的症狀是畫面上直接印出 `lib.favorites` 這種東西，
  // 而它只在切到那個語言、而且剛好看到那一區的時候才會被看見。
  const missing = [];
  for (const page of ["index.html", "player.html"]) {
    const html = fs.readFileSync(path.join(FRONTEND, page), "utf8");
    for (const m of html.matchAll(/data-i18n(?:-title|-placeholder)?="([^"]+)"/g)) {
      if (!(m[1] in I18N_ZH_TW)) missing.push(`${page}: ${m[1]}`);
    }
  }
  assert.deepEqual(missing, [], `HTML 標了字典裡沒有的 key：\n  ${missing.join("\n  ")}`);
});

test("字典裡的每一個 key 都真的在畫面上用到", () => {
  // 沒有用到的 key 是**假的覆蓋率**：它看起來像「這句翻好了」，
  // 但那顆按鈕上的字其實是 render 當場寫進去的中文。
  // 真的發生過一次 —— 最上排那幾顆狀態寫在字上的鍵（⏱️ 開始計時、🔁 先到先唱）
  // 標了 data-i18n，而第一次 render 就把整個 textContent（連同標記）蓋掉了。
  const used = new Set();
  for (const page of ["index.html", "player.html"]) {
    const html = fs.readFileSync(path.join(FRONTEND, page), "utf8");
    for (const m of html.matchAll(/data-i18n(?:-title|-placeholder)?="([^"]+)"/g)) {
      used.add(m[1]);
    }
  }
  // 從程式裡查字的那幾句（render 當場寫字的按鈕走這條，例如播放／暫停）
  for (const file of ["controller.js", "player.js"]) {
    const js = fs.readFileSync(path.join(FRONTEND, "js", file), "utf8");
    for (const m of js.matchAll(/\bt\(\s*"([a-z_]+\.[a-z_]+)"/g)) used.add(m[1]);
  }
  const orphans = Object.keys(I18N_ZH_TW).filter((k) => !used.has(k));
  assert.deepEqual(orphans, [], `字典裡有沒人用的 key：${orphans.join(", ")}`);
});

test("兩頁 HTML 真的標過了（標記被整批改掉時要叫）", () => {
  for (const page of ["index.html", "player.html"]) {
    const html = fs.readFileSync(path.join(FRONTEND, page), "utf8");
    const count = [...html.matchAll(/data-i18n(?:-title|-placeholder)?="/g)].length;
    assert.ok(count >= 10, `${page} 只剩 ${count} 個 data-i18n 標記`);
  }
});

// --- 雜項 ---

test("<html lang> 跟著語言走", () => {
  // 瀏覽器照它決定斷行規則與預設字型，螢幕閱讀器照它決定用哪一種發音念。
  assert.equal(documentLangFor("ja-JP"), "ja");
  assert.equal(documentLangFor("klingon"), I18N_BASE_LOCALE);
});

test("語言鍵上印的是那個語言自己的名字", () => {
  // 一個看不懂中文的人在選單裡要找的是看得懂的那一個。
  assert.ok(localeLabel("en", I18N_LOCALES).includes("English"));
  assert.ok(localeLabel("ja", I18N_LOCALES).includes("日本語"));
  // 讀不懂的代碼視同基準語言（鍵上永遠印得出一個看得懂的名字，
  // 不會變成一顆印著 `klingon` 的鍵）
  assert.ok(localeLabel("klingon", I18N_LOCALES).includes("繁體中文"));
  // 清單裡真的沒有的語言才退回印代碼本身
  assert.equal(localeLabel("ja", [{ code: "en", name: "English" }]), "ja");
});
