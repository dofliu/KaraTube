/**
 * 歌號點歌的說法測試。
 *
 * 「這組號碼是誰的」由伺服器決定（tests/test_song_numbers.py 釘著），
 * 這一支釘的是畫面上那幾句話 —— 歌號查不到的原因有四種，而其中兩種
 * （歌被刪了 vs. 號碼記錯了）如果講成同一句，使用者會去反覆確認一組
 * 從頭到尾都正確的號碼。
 */
const test = require("node:test");
const assert = require("node:assert/strict");

const {
  NUMBER_MIN_DIGITS,
  digitsOnly, pressDigit, eraseDigit, isComplete, queryDisplay,
  digitHasSongs, numberSummary, idleHint, lookupMessage, numberLabel,
} = require("../js/number-search.js");

// --- 鍵盤 ---

test("只收數字：貼上來的東西什麼都有", () => {
  assert.equal(digitsOnly("10#02-37"), "100237");
  assert.equal(digitsOnly(null), "");
});

test("按鍵一次一碼，打滿了就原地不動（不要把前面的擠掉）", () => {
  assert.equal(pressDigit("10023", "7"), "100237");
  assert.equal(pressDigit("1002377", "8"), "1002377");
  assert.equal(pressDigit("100", "x"), "100");
});

test("空的時候按退格不會變成 undefined", () => {
  assert.equal(eraseDigit(""), "");
  assert.equal(eraseDigit("1002"), "100");
});

test("打滿六碼才算打完", () => {
  assert.equal(isComplete("10023"), false);
  assert.equal(isComplete("100237"), true);
});

test("顯示固定留六格：包廂很暗，要一眼看出還差幾碼", () => {
  assert.equal(queryDisplay("100"), "1 0 0 ＿ ＿ ＿");
  assert.equal(queryDisplay(""), "＿ ＿ ＿ ＿ ＿ ＿");
  // 真的打到第七碼時就顯示七格，不要把那一碼藏起來
  assert.equal(queryDisplay("1002377").split(" ").length, 7);
});

test("「下一鍵」只用來標亮，十顆數字鍵永遠都能按", () => {
  // 標亮＝按下去會落在現有的歌上。沒標亮的鍵照樣按得下去 ——
  // 已下架號碼的數字永遠不會被標亮，而使用者正是為了那組號碼來的。
  assert.equal(digitHasSongs("2", ["1", "2"]), true);
  assert.equal(digitHasSongs("5", ["1", "2"]), false);
  assert.equal(digitHasSongs("5", []), false);
});

// --- 統計與提示 ---

test("統計講的是曲庫有幾首，不只是這一頁列了幾首", () => {
  const text = numberSummary({
    songs: [{}, {}], total: 9, library_total: 40, book: { available: true, retired: 0 },
  });
  assert.match(text, /曲庫 40 首/);
  assert.match(text, /符合 9 首/);
  assert.match(text, /先列出 2 首/);
});

test("已下架的號碼數字要講出來（那是號碼不回收的成本）", () => {
  const text = numberSummary({
    songs: [{}], total: 1, library_total: 3, book: { available: true, retired: 5 },
  });
  assert.match(text, /已下架 5 組號碼/);
});

test("號碼簿壞掉時，統計與提示都要說是機器的問題", () => {
  const res = { songs: [], total: 0, library_total: 12, book: { available: false } };
  assert.equal(numberSummary(res), "歌號暫停服務");
  assert.match(idleHint(res), /暫時停用/);
});

test("曲庫空的時候不要叫人去打號碼", () => {
  assert.match(idleHint({ library_total: 0, book: { available: true } }), /曲庫是空的/);
});

// --- 四種查號結果 ---

test("找到了就講歌名", () => {
  const msg = lookupMessage({ status: "ready", input: "100237",
                              song: { title: "稻香" } });
  assert.equal(msg.tone, "ok");
  assert.match(msg.text, /稻香/);
});

test("歌被刪掉：要講出原本是哪一首，而且號碼還是那一組", () => {
  const msg = lookupMessage({ status: "gone", input: "100237",
                              record: { title: "稻香" } });
  assert.equal(msg.tone, "gone");
  assert.match(msg.text, /稻香/);
  assert.match(msg.text, /還是這組號碼/);
});

test("沒發過這組號碼：講的是「記錯一碼」，不是「這首歌不見了」", () => {
  const msg = lookupMessage({ status: "unknown", input: "999999" });
  assert.equal(msg.tone, "miss");
  assert.match(msg.text, /999999/);
  assert.doesNotMatch(msg.text, /不在曲庫/);
});

test("打的不是號碼：講規則（六位數、100001 起跳）", () => {
  assert.match(lookupMessage({ status: "invalid", input: "123" }).text, /六位數/);
});

test("號碼簿壞掉是機器的問題，使用者再打幾次都沒有用", () => {
  assert.equal(lookupMessage({ status: "unavailable" }).tone, "error");
});

// --- 歌卡上的號碼 ---

test("還沒備好的歌沒有號碼，不要印成 0", () => {
  assert.equal(numberLabel(null), "");
  assert.equal(numberLabel(0), "");
  assert.equal(numberLabel(100237), "100237");
  assert.equal(numberLabel("100237").length, NUMBER_MIN_DIGITS);
});
