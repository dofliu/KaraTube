/**
 * 本機匯入分頁的顯示邏輯測試（node --test，不需要瀏覽器）。
 *
 * 這一頁的錯誤不會拋例外，只會**安靜地騙人**：
 *   * 把「已經在曲庫裡」的檔案顯示成可以匯入 —— 使用者按下去，等十分鐘，
 *     換來一首他本來就有的歌；
 *   * 掃到 0 個檔案時只說「沒有檔案」—— 他不知道資料夾在哪、也不知道
 *     是他還沒放，還是放了但機器不認得（這兩件事的下一步完全不同）。
 *
 * 所以這裡測的幾乎都是「下一步講清楚了沒有」。
 */
const test = require("node:test");
const assert = require("node:assert/strict");

const {
  fileStateMeta,
  canSelect,
  defaultSelection,
  formatSize,
  fileDetail,
  duplicateWarning,
  summaryText,
  guidanceText,
  skippedSummary,
  truncationNote,
  confirmText,
  resultMessage,
} = require("../js/local-import.js");

function file(state, extra = {}) {
  return { path: `${state}.mp4`, state, title: "稻香", artist: "周杰倫",
           kind: "video", size_bytes: 50 * 1024 * 1024, has_lrc: false, ...extra };
}

// --- 哪些勾得起來 ---

test("已經在曲庫裡的、正在處理的都勾不起來", () => {
  assert.equal(canSelect(file("new")), true);
  assert.equal(canSelect(file("failed")), true, "失敗過的要能再試一次");
  assert.equal(canSelect(file("imported")), false);
  assert.equal(canSelect(file("queued")), false);
});

test("預設只勾沒匯入過的，失敗過的不預設勾", () => {
  // 失敗過的多半是檔案本身有問題，再跑一次還是會失敗 ——
  // 使用者應該先看一眼那個原因，所以不幫他勾。
  const picked = defaultSelection([file("new"), file("failed"), file("imported")]);
  assert.deepEqual(picked, ["new.mp4"]);
});

test("狀態徽章四種都有自己的說法", () => {
  const labels = ["new", "imported", "queued", "failed"].map(s => fileStateMeta(s).label);
  assert.equal(new Set(labels).size, 4);
  assert.equal(fileStateMeta("沒看過這種").label, fileStateMeta("new").label);
});

// --- 一列上的資訊 ---

test("每一列都要講「有沒有附 .lrc」", () => {
  // 那是這條路上最重要的歌詞來源，而且只有在匯入**之前**補得了
  assert.match(fileDetail(file("new", { has_lrc: true })), /已附 \.lrc/);
  assert.match(fileDetail(file("new", { has_lrc: false })), /無 \.lrc/);
});

test("純音檔要先講「會用情境背景」，不是等他匯完才發現沒有畫面", () => {
  assert.match(fileDetail(file("new", { kind: "audio" })), /情境背景/);
});

test("同名的歌只提醒不擋", () => {
  assert.match(duplicateWarning(file("new", { duplicate_of: "abc" })), /已經有同名/);
  // 已經在曲庫裡的那一列不必再提醒一次（它本來就是同一首）
  assert.equal(duplicateWarning(file("imported", { duplicate_of: "abc" })), "");
  assert.equal(duplicateWarning(file("new")), "");
});

test("檔案大小講到看得懂就好", () => {
  assert.equal(formatSize(3 * 1024 * 1024 * 1024), "3.0 GB");
  assert.equal(formatSize(52 * 1024 * 1024), "52 MB");
  assert.equal(formatSize(900), "1 KB");
  assert.equal(formatSize(undefined), "1 KB");
});

// --- 最上面那段話：現在該做什麼 ---

test("資料夾是空的：要講出路徑與檔名怎麼取", () => {
  const text = guidanceText({ exists: true, root: "/data/import", counts: { total: 0 } });
  assert.match(text, /\/data\/import/);
  assert.match(text, /歌手 - 歌名/);
  assert.match(text, /\.lrc/);
});

test("有檔案但一個都不支援：要跟「還沒放檔案」講不同的話", () => {
  const empty = guidanceText({ exists: true, root: "/x", counts: { total: 0 } });
  const unsupported = guidanceText({
    exists: true, root: "/x", counts: { total: 0 },
    skipped: [{ path: "a.rmvb" }, { path: "b.doc" }],
  });
  assert.notEqual(empty, unsupported);
  assert.match(unsupported, /2 個檔案/);
});

test("資料夾根本沒建起來是另一回事（寫入權限）", () => {
  assert.match(guidanceText({ exists: false, root: "/data/import" }), /權限|還沒建立/);
});

test("全部都匯入過了：要說原始檔案可以自己清掉", () => {
  const text = guidanceText({ exists: true, counts: { total: 3, new: 0, imported: 3 } });
  assert.match(text, /不會被刪掉|備份/);
});

test("有東西可以匯入時，要先講「歌名現在改最便宜」", () => {
  const text = guidanceText({ exists: true, counts: { total: 3, new: 3 } });
  assert.match(text, /歌名/);
  assert.match(text, /複製|不會被動到/);
});

// --- 摘要與截斷 ---

test("摘要只列非零的類別", () => {
  const text = summaryText({ counts: { total: 5, new: 2, imported: 3, queued: 0, failed: 0 } });
  assert.match(text, /可匯入 2/);
  assert.match(text, /已在曲庫 3/);
  assert.doesNotMatch(text, /處理中/);
  assert.equal(summaryText({ counts: { total: 0 } }), "匯入資料夾是空的");
});

test("檔案太多被截斷時一定要說出來", () => {
  // 靜悄悄只列前 N 個，使用者會以為剩下的檔案機器讀不到
  assert.equal(truncationNote({ truncated: false }), "");
  assert.match(truncationNote({ truncated: true, max_files: 400 }), /400/);
});

// --- 送出前後 ---

test("確認文字要講清楚「什麼時候開始跑」", () => {
  assert.match(confirmText(3, true), /馬上開始/);
  assert.match(confirmText(3, false), /排程時段/);
  // 兩種都要講「要跑多久」與「原始檔案不會被動到」
  for (const text of [confirmText(3, true), confirmText(3, false)]) {
    assert.match(text, /分鐘/);
    assert.match(text, /不會被移動或刪除/);
  }
});

test("送出之後，收不下來的檔案要一起講", () => {
  assert.match(resultMessage({ accepted: 4, failed: [] }), /已排入 4 首/);
  assert.match(resultMessage({ accepted: 4, failed: [{ path: "x" }] }), /1 個檔案讀不到/);
  assert.match(resultMessage({ accepted: 0, failed: [] }), /沒有任何檔案/);
});

test("被略過的檔案：標題只講數量，理由逐檔列", () => {
  // 寫死「不是支援的影音格式」的話，一個「檔案太小」被略過的檔案會配上
  // 一個錯的理由 —— 而使用者很確定他放進去的是一支影片
  assert.equal(skippedSummary({ skipped: [] }), "");
  const text = skippedSummary({ skipped: [{ path: "a.rmvb" }, { path: "b.mp4" }] });
  assert.match(text, /2 個檔案/);
  assert.doesNotMatch(text, /格式/);
});
