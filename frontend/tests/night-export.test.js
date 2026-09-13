/**
 * 整晚打包顯示邏輯的單元測試（node --test）
 *
 * 收場的時候使用者要做的是一個判斷：「這一場是不是我剛剛唱的那一場？」
 * 所以這裡測的都是會讓他按錯（或不敢按）的寫法：跨午夜的那一場寫成
 * `21:05 – 02:30`（看起來像打反了）、昨天的那一場寫著「今晚」、
 * 三百 MB 的包沒有先講大小。
 */
const test = require("node:test");
const assert = require("node:assert/strict");

const {
  sessionLabel, sessionSummary, downloadHint, singerChoices, busyNote, emptyNote,
} = require("../js/night-export.js");

const NIGHT = {
  started_at: "2026-09-13T21:05:00",
  ended_at: "2026-09-14T02:30:00",
  count: 23,
  bytes: 195_000_000,
  singers: ["阿明", "小美", "老王"],
};

// --- 標題 ---

test("跨午夜的那一場要寫出「隔天」", () => {
  // 只寫 21:05 – 02:30 會被讀成打反了
  const label = sessionLabel(NIGHT, new Date("2026-09-14T03:00:00"));
  assert.match(label, /隔天 02:30/);
  assert.match(label, /21:05/);
});

test("剛結束的那一場叫「今晚」", () => {
  assert.match(sessionLabel(NIGHT, new Date("2026-09-14T03:00:00")), /^今晚/);
});

test("隔了一天再回來看，同一場要改用日期，不能還寫「今晚」", () => {
  const label = sessionLabel(NIGHT, new Date("2026-09-15T20:00:00"));
  assert.ok(!label.includes("今晚"), label);
  assert.match(label, /^9\/13/);
});

test("同一天之內結束的場次不寫「隔天」", () => {
  const label = sessionLabel(
    { started_at: "2026-09-13T14:00:00", ended_at: "2026-09-13T16:20:00" },
    new Date("2026-09-13T17:00:00"));
  assert.ok(!label.includes("隔天"), label);
  assert.match(label, /14:00 – 16:20/);
});

test("時間壞掉時不要吐出 NaN", () => {
  assert.equal(sessionLabel({ started_at: "壞掉的時間" }, new Date()), "這一場");
  assert.match(sessionLabel({ started_at: "2026-09-13T21:05:00", ended_at: "壞的" },
                            new Date("2026-09-13T22:00:00")), /21:05$/);
  assert.equal(sessionLabel(null), "這一場");
});

// --- 副標與提示 ---

test("副標寫得出幾首、幾個人、多大", () => {
  const text = sessionSummary(NIGHT);
  assert.match(text, /23 首/);
  assert.match(text, /3 人/);
  assert.match(text, /MB/);
});

test("沒有暱稱的場次不要寫「0 人」", () => {
  const text = sessionSummary({ count: 4, bytes: 1024, singers: [] });
  assert.ok(!text.includes("人"), text);
  assert.match(text, /4 首/);
});

test("下載前先講大小與「進度條不會動」", () => {
  const hint = downloadHint(NIGHT);
  assert.match(hint, /\d+\.\d MB/);
  // 邊打包邊送就算不出總長度，不先說的話會被當成卡住
  assert.match(hint, /百分比/);
});

// --- 「只要某個人的」 ---

test("選單第一個是全部，後面才是這一場有唱的人", () => {
  const choices = singerChoices(NIGHT);
  assert.equal(choices[0].value, "");
  assert.match(choices[0].label, /全部（23 首）/);
  assert.deepEqual(choices.slice(1).map((c) => c.value), ["阿明", "小美", "老王"]);
});

test("沒有人留暱稱時只剩「全部」", () => {
  assert.equal(singerChoices({ count: 2, singers: [] }).length, 1);
  assert.equal(singerChoices(null).length, 1);
});

// --- 狀態 ---

test("有人正在打包時講得出在忙什麼", () => {
  assert.match(busyNote(true), /正在打包/);
  assert.equal(busyNote(false), "");
});

test("沒有錄音時指路，而不是留一片空白", () => {
  assert.match(emptyNote(), /唱完一首/);
});
