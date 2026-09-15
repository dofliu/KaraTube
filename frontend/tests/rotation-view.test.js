/**
 * 公平輪唱說法的前端單元測試（node --test）。
 *
 * 排序規則錯了會不公平，這一層錯了則是「使用者不知道發生什麼事」——
 * 而輪唱會把剛點的歌排到預期以外的位置，沒講清楚就跟壞掉沒兩樣。
 */
const test = require("node:test");
const assert = require("node:assert/strict");

const {
  ROTATION_ANON_LABEL,
  rotationBadge,
  singerSummary,
  rotationLine,
  rotationNameHint,
  rotationPlacementNote,
  rotationToggleLabel,
  rotationToggleNote,
} = require("../js/rotation-view.js");

// --- 輪次小標 ---

test("有輪次才長出小標", () => {
  assert.equal(rotationBadge(2), "第 2 輪");
  assert.equal(rotationBadge(1), "第 1 輪");
});

test("沒有輪次（沒開輪唱）不長出小標", () => {
  assert.equal(rotationBadge(undefined), "");
  assert.equal(rotationBadge(null), "");
  assert.equal(rotationBadge(0), "");
  assert.equal(rotationBadge("壞掉的值"), "");
});

// --- 一個人的狀態 ---

test("已唱與待唱都寫出來", () => {
  assert.equal(singerSummary({ name: "小明", sung: 2, pending: 1 }), "小明 已唱 2・待唱 1");
  assert.equal(singerSummary({ name: "小美", sung: 0, pending: 1 }), "小美 待唱 1");
  assert.equal(singerSummary({ name: "阿華", sung: 3, pending: 0 }), "阿華 已唱 3");
});

test("沒取暱稱的那一桶有自己的叫法", () => {
  // 「匿名」聽起來像一個特定的人，但它其實是「所有沒取名的合起來算一個」
  assert.equal(singerSummary({ name: "", anonymous: true, sung: 1, pending: 0 }),
    `${ROTATION_ANON_LABEL} 已唱 1`);
});

// --- 佇列上方那一行 ---

test("照伺服器給的順序列出來（＝上台順序）", () => {
  const line = rotationLine({
    singers: [
      { name: "小美", sung: 0, pending: 1 },
      { name: "小明", sung: 2, pending: 1 },
    ],
  });
  assert.match(line, /^小美 待唱 1/);
  assert.ok(line.includes("小明 已唱 2・待唱 1"));
});

test("人多的時候只列前幾位，其餘寫成等 N 人", () => {
  const singers = ["a", "b", "c", "d", "e", "f"].map(n => ({ name: n, sung: 1, pending: 1 }));
  const line = rotationLine({ singers }, 4);
  assert.ok(line.includes("…等 6 人"));
  assert.ok(!line.includes("e 已唱"));
});

test("還沒有人點歌時講得出來", () => {
  assert.equal(rotationLine({ singers: [] }), "還沒有人點歌");
  assert.equal(rotationLine(null), "還沒有人點歌");
});

// --- 沒人取暱稱的提醒 ---

test("一整間都沒取暱稱時要提醒（不然開關看起來像壞的）", () => {
  const hint = rotationNameHint({
    named_count: 0,
    singers: [{ name: "", anonymous: true, sung: 0, pending: 2 }],
  });
  assert.match(hint, /暱稱/);
});

test("有人取了暱稱就不再嘮叨", () => {
  assert.equal(rotationNameHint({
    named_count: 1,
    singers: [{ name: "小明", sung: 0, pending: 1 }],
  }), "");
});

test("完全還沒點歌時不提醒（那時候講也沒用）", () => {
  assert.equal(rotationNameHint({ named_count: 0, singers: [] }), "");
});

// --- 剛點完那一句 ---

test("沒開輪唱就不講位置（讓呼叫端用原本那句）", () => {
  assert.equal(rotationPlacementNote({ enabled: false, position: 3, round: 1 }), "");
  assert.equal(rotationPlacementNote(null), "");
});

test("直接上台時不講位置", () => {
  // 佇列本來是空的，快取歌會立刻開播 —— 這時候「排在第幾位」沒有意義
  assert.equal(rotationPlacementNote({ enabled: true, position: null, round: null }), "");
});

test("插到別人前面時明講插到幾首前面", () => {
  const note = rotationPlacementNote(
    { enabled: true, position: 2, round: 1, ahead_of: 3 }, "小美");
  assert.ok(note.includes("第 2 位"));
  assert.ok(note.includes("小美的第 1 輪"));
  assert.ok(note.includes("插到 3 首前面"));
});

test("排到最後面時不會硬講插隊", () => {
  const note = rotationPlacementNote({ enabled: true, position: 5, round: 3, ahead_of: 0 }, "小明");
  assert.ok(note.includes("第 5 位"));
  assert.ok(!note.includes("插到"));
});

test("沒取暱稱的人講「你的第 N 輪」", () => {
  const note = rotationPlacementNote({ enabled: true, position: 1, round: 1, ahead_of: 2 }, "");
  assert.ok(note.includes("你的第 1 輪"));
});

// --- 開關 ---

test("開關的狀態寫在字上，不是只靠顏色", () => {
  assert.equal(rotationToggleLabel(true), "🔁 輪唱中");
  assert.equal(rotationToggleLabel(false), "🔁 先到先唱");
});

test("切換時說清楚影響的是「接下來新點的歌」", () => {
  assert.match(rotationToggleNote(true), /接下來新點的歌/);
  assert.match(rotationToggleNote(true), /已經排好的順序不動/);
  assert.match(rotationToggleNote(false), /最後面/);
});
