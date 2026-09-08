/**
 * 排程預處理分頁的顯示邏輯測試（node --test，不需要瀏覽器）。
 *
 * 這一頁的錯法很陰險：畫面照樣長出來，只是顯示的東西不對 ——
 * 一批 120 首歌只挑 8 首上畫面，挑錯了使用者就看不到正在跑的那首，
 * 會以為機器沒在動；跨午夜的時段字串寫反了，他會照著錯的時間去等。
 */
const test = require("node:test");
const assert = require("node:assert/strict");

const {
  pickBatchItems,
  formatWindow,
  statusDotClass,
  summarizeCounts,
  itemDetailText,
  itemStatusMeta,
  jobStatusLabel,
} = require("../js/batch-view.js");

function item(status, title) {
  return { status, title, song_id: title };
}

test("挑選顯示項目：正在跑的與失敗的優先浮上來", () => {
  const items = [
    item("DONE", "a"), item("DONE", "b"), item("PENDING", "c"),
    item("ERROR", "d"), item("RUNNING", "e"), item("SKIPPED", "f"),
  ];
  const picked = pickBatchItems(items, 4).map(i => i.title);
  assert.deepEqual(picked, ["e", "d", "c", "a"]);
});

test("挑選顯示項目：同狀態維持使用者貼上的順序", () => {
  const items = [item("PENDING", "1"), item("PENDING", "2"), item("PENDING", "3")];
  assert.deepEqual(pickBatchItems(items, 3).map(i => i.title), ["1", "2", "3"]);
});

test("挑選顯示項目：少於上限時全部顯示，壞資料不會炸", () => {
  assert.equal(pickBatchItems([item("DONE", "x")], 8).length, 1);
  assert.deepEqual(pickBatchItems(null), []);
  assert.deepEqual(pickBatchItems([], 8), []);
  // 認不得的狀態排到最後，但不能讓整個清單消失
  assert.equal(pickBatchItems([{ status: "???" }], 8).length, 1);
});

test("時段字串：一般時段、跨午夜、全天候", () => {
  assert.equal(formatWindow({ start_hour: 2, end_hour: 6, all_day: false }), "02:00–06:00");
  assert.equal(formatWindow({ start_hour: 23, end_hour: 5, all_day: false }), "23:00–05:00");
  assert.equal(formatWindow({ start_hour: 4, end_hour: 4, all_day: true }), "全天候");
  assert.equal(formatWindow(null), "");
});

test("狀態燈：處理中最優先，其次才是能不能開工", () => {
  assert.equal(statusDotClass({ working: true, can_run: false }), "working");
  assert.equal(statusDotClass({ working: false, can_run: true }), "ready");
  assert.equal(statusDotClass({ working: false, can_run: false }), "waiting");
  assert.equal(statusDotClass(null), "waiting");
});

test("計數摘要：0 的項目不顯示，免得看起來像出了事", () => {
  assert.equal(summarizeCounts({ done: 3, error: 0, skipped: 1, pending: 2 }),
    "✅ 3 ・ ⏭ 1 ・ 🕒 2");
  assert.equal(summarizeCounts({ done: 0, error: 0, skipped: 0, pending: 0 }), "");
  assert.equal(summarizeCounts(undefined), "");
});

test("項目細節：處理中報進度、失敗報原因、其餘留白", () => {
  assert.equal(itemDetailText({ status: "RUNNING", status_text: "AI 分離人聲", progress: 40 }),
    "AI 分離人聲 40%");
  assert.equal(itemDetailText({ status: "ERROR", error: "影片已下架" }), "影片已下架");
  // 失敗但沒有 error 欄位時退回 status_text，不要留一片空白讓人以為沒事
  assert.equal(itemDetailText({ status: "ERROR", status_text: "處理失敗" }), "處理失敗");
  assert.equal(itemDetailText({ status: "DONE" }), "");
  assert.equal(itemDetailText(null), "");
});

test("狀態徽章與任務狀態：認不得的值也要有東西可顯示", () => {
  assert.equal(itemStatusMeta("DONE").label, "完成");
  assert.equal(itemStatusMeta("???").cls, "pending");
  assert.equal(jobStatusLabel("FINISHED"), "✅ 已完成");
  assert.equal(jobStatusLabel("WEIRD"), "WEIRD");
  assert.equal(jobStatusLabel(undefined), "");
});
