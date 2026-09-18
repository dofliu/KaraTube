/**
 * 曲庫查歌的說法測試。
 *
 * 查得到什麼由伺服器決定（tests/test_song_index.py 釘著），這一支釘的是
 * **查不到的那一刻畫面上寫什麼**。空白畫面加上一句「找不到」，使用者的下一個
 * 動作就是回去用 YouTube 搜尋把已經在曲庫裡的歌再下載一次 —— 所以這裡要講得出
 * 是哪個條件擋住了（字數還開著？注音字典沒裝？還是真的沒有這首）。
 */
const test = require("node:test");
const assert = require("node:assert/strict");

const {
  isKeyChar, queryKeys, pressKey, backspace,
  queryLabel, resultSummary, emptyHint, charBucketLabel, keyEnabled, usableNextKeys,
} = require("../js/library-search.js");

// --- 按鍵 ---

test("注音符號與英數算一鍵，標點與空白不算", () => {
  assert.ok(isKeyChar("ㄉ"));
  assert.ok(isKeyChar("Y"));
  assert.ok(isKeyChar("7"));
  assert.ok(!isKeyChar(" "));
  assert.ok(!isKeyChar("，"));
  assert.ok(!isKeyChar(""));
});

test("逐鍵拆解跟伺服器同一套規則（英文轉大寫、標點丟掉）", () => {
  assert.deepEqual(queryKeys("ㄉㄒ"), ["ㄉ", "ㄒ"]);
  assert.deepEqual(queryKeys(" y o m "), ["Y", "O", "M"]);
  assert.deepEqual(queryKeys(null), []);
});

test("按鍵會累加，按到第 13 鍵就不再吃（沒有歌名要按那麼多字才查得到）", () => {
  assert.equal(pressKey("ㄉ", "ㄒ"), "ㄉㄒ");
  assert.equal(pressKey("", "y"), "Y");
  const full = "ㄅㄆㄇㄈㄉㄊㄋㄌㄍㄎㄏㄐ";  // 12 鍵
  assert.equal(queryKeys(full).length, 12);
  assert.equal(pressKey(full, "ㄑ"), full);
});

test("不是鍵的東西按不進去", () => {
  assert.equal(pressKey("ㄉ", " "), "ㄉ");
  assert.equal(pressKey("ㄉ", null), "ㄉ");
});

test("空的時候按退格不會變成 undefined", () => {
  assert.equal(backspace("ㄉㄒ"), "ㄉ");
  assert.equal(backspace(""), "");
  assert.equal(backspace(undefined), "");
});

// --- 條件列 ---

test("按了什麼一直看得到（按五顆之後沒有人記得自己按了什麼）", () => {
  assert.equal(queryLabel("ㄉㄒ", 0), "🔤 ㄉ ㄒ");
  assert.match(queryLabel("ㄉㄒ", 2), /ㄉ ㄒ/);
  assert.match(queryLabel("ㄉㄒ", 2), /2 個字/);
  assert.equal(queryLabel("", 0), "整個曲庫");
  assert.match(queryLabel("", 3), /3 個字/);
});

test("打中文字的時候原樣顯示，不會被拆成空的", () => {
  assert.equal(queryLabel("稻香", 0), "🔤 稻香");
});

// --- 結果統計 ---

test("統計講的是「曲庫有幾首」而不只是「找到幾首」", () => {
  assert.equal(resultSummary({ total: 3, library_total: 42, songs: [1, 2, 3] }),
    "曲庫 42 首 ・ 符合 3 首");
});

test("列不完的時候要說只列了一部分", () => {
  const songs = new Array(60).fill(0);
  assert.match(resultSummary({ total: 180, library_total: 200, songs }), /先列出 60 首/);
});

test("曲庫空的時候不要報一堆 0", () => {
  assert.equal(resultSummary({ total: 0, library_total: 0, songs: [] }), "曲庫是空的");
  assert.equal(resultSummary(null), "曲庫是空的");
});

// --- 查不到的時候 ---

test("曲庫是空的就直說，並指回搜尋框", () => {
  const hint = emptyHint({ library_total: 0, songs: [] });
  assert.match(hint, /曲庫是空的/);
  assert.match(hint, /搜尋框/);
});

test("字數條件還開著是最常見的誤會，要先講", () => {
  const hint = emptyHint({ library_total: 42, query: "ㄉㄒ", chars: 5 });
  assert.match(hint, /ㄉ ㄒ/);
  assert.match(hint, /5 個字/);
  assert.match(hint, /取消/);
});

test("只有字數條件時講的是字數", () => {
  const hint = emptyHint({ library_total: 42, query: "", chars: 9 });
  assert.match(hint, /9 個字/);
  assert.ok(!/取消/.test(hint));
});

test("伺服器沒裝注音字典時要說是功能關著，不是查不到", () => {
  const hint = emptyHint({ library_total: 42, query: "ㄉㄒ", chars: 0,
                           bopomofo_available: false });
  assert.match(hint, /pypinyin|注音字典/);
  assert.ok(!/沒有這首/.test(hint));
});

test("真的查不到時要說清楚注音查的是「每個字的第一個符號」", () => {
  const hint = emptyHint({ library_total: 42, query: "ㄉㄒ", chars: 0,
                           bopomofo_available: true });
  assert.match(hint, /第一個符號/);
  assert.match(hint, /搜尋框/);
});

test("直接打字查不到時提醒這裡只查曲庫", () => {
  const hint = emptyHint({ library_total: 42, query: "稻香", chars: 0 });
  assert.match(hint, /只查已經備好的歌|只查曲庫/);
});

// --- 字數按鈕 / 鍵盤 ---

test("字數按鈕上的字", () => {
  assert.deepEqual(charBucketLabel({ chars: 3, count: 12 }), { label: "3 個字", count: 12 });
  assert.deepEqual(charBucketLabel(null), { label: "0 個字", count: 0 });
});

test("沒有「下一鍵」資料時整個鍵盤都可按（寧可按下去查不到，也不要鎖住查得到的鍵）", () => {
  assert.ok(keyEnabled("ㄉ", []));
  assert.ok(keyEnabled("ㄉ", null));
});

test("有「下一鍵」資料時，會落空的鍵變灰", () => {
  assert.ok(keyEnabled("ㄒ", ["ㄒ", "ㄗ"]));
  assert.ok(!keyEnabled("ㄅ", ["ㄒ", "ㄗ"]));
});

test("下一格是英文單字時，整個注音鍵盤不會一起變灰（那個畫面看起來就是壞掉）", () => {
  const rows = [["ㄅ", "ㄆ"], ["ㄉ", "ㄒ"]];
  assert.deepEqual(usableNextKeys(rows, ["R"]), []);
  assert.deepEqual(usableNextKeys(rows, ["ㄒ", "R"]), ["ㄒ"]);
  assert.deepEqual(usableNextKeys(rows, []), []);
  assert.deepEqual(usableNextKeys(null, ["ㄒ"]), []);
});
