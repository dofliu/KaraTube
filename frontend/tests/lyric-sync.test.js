/**
 * 字幕同步兩個數字的說法（node --test）
 *
 * 守的是「畫面不能騙人」：兩個數字要分得清楚（否則使用者不知道自己剛剛改了
 * 哪一個），待機時不能偷偷改到裝置基準，而「連續同方向」的偵測只能在真的
 * 看得出規律時開口 —— 亂建議會把一台本來好好的機器整個推歪。
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const LS = require("../js/lyric-sync.js");

test("夾限與後端一致", () => {
  const py = fs.readFileSync(path.join(__dirname, "..", "..", "backend", "services",
                                       "lyric_offsets.py"), "utf8");
  const max = Number(/MAX_OFFSET_MS = (\d+)/.exec(py)[1]);
  assert.equal(LS.SYNC_MAX_OFFSET_MS, max);
  assert.equal(LS.clampSyncMs(99999), max);
  assert.equal(LS.clampSyncMs(-99999), -max);
});

test("認不得的值一律當 0，不是 NaN", () => {
  assert.equal(LS.clampSyncMs(undefined), 0);
  assert.equal(LS.clampSyncMs("abc"), 0);
  assert.equal(LS.clampSyncMs(null), 0);
  assert.equal(LS.clampSyncMs("150"), 150);
  assert.equal(LS.clampSyncMs(150.4), 150);
});

test("數字帶正負號，而且講得出方向", () => {
  assert.equal(LS.offsetLabel(150), "+150 ms");
  assert.equal(LS.offsetLabel(-150), "−150 ms");
  assert.equal(LS.offsetLabel(0), "0 ms");
  assert.equal(LS.offsetDirection(150), "字幕延後");
  assert.equal(LS.offsetDirection(-150), "字幕提前");
  assert.equal(LS.offsetDirection(0), "");
});

test("toast 第一行只講剛剛被改動的那個數字", () => {
  const song = LS.syncToastLines({ songMs: 300, deviceMs: 80, autoMs: 48, changed: "song" });
  assert.match(song.main, /這首歌 \+300 ms/);
  assert.match(song.hint, /這台機器 \+80 ms/);      // 另一個退到第二行
  assert.match(song.hint, /自動補償 48 ms/);

  const device = LS.syncToastLines({ songMs: 300, deviceMs: 80, autoMs: 48, changed: "device" });
  assert.match(device.main, /這台機器 \+80 ms/);
  assert.match(device.hint, /這首歌 \+300 ms/);
  assert.match(device.hint, /合計 \+380 ms/);
});

test("待機時按方向鍵：說明，而不是偷偷改裝置基準", () => {
  const lines = LS.syncToastLines({ songMs: 0, deviceMs: 80, changed: "song", hasSong: false });
  assert.match(lines.main, /沒有歌在唱/);
  assert.match(lines.hint, /按 S/);                  // 要講得出「那去哪裡調」
});

test("點歌台那一行：沒有歌就講沒有歌，有裝置延遲就講合計", () => {
  assert.equal(LS.deckOffsetSummary({ hasSong: false }), "沒有歌在唱");
  assert.match(LS.deckOffsetSummary({ songMs: 300, deviceMs: 0 }), /\+300 ms/);
  assert.match(LS.deckOffsetSummary({ songMs: 300, deviceMs: 80 }), /＝ \+380 ms/);
});

// --- 連續同方向偵測 ---

test("三首同方向、量差不多才建議", () => {
  const s = LS.baselineSuggestion([300, 280, 320]);
  assert.ok(s);
  assert.equal(s.suggestMs, 300);        // 中位數
  assert.equal(s.samples, 3);
});

test("方向不一致不建議", () => {
  assert.equal(LS.baselineSuggestion([300, -280, 320]), null);
});

test("量差太多不建議（那是各自的 LRC 問題，不是喇叭）", () => {
  assert.equal(LS.baselineSuggestion([300, 800, 120]), null);
});

test("樣本不夠不建議", () => {
  assert.equal(LS.baselineSuggestion([300, 280]), null);
  assert.equal(LS.baselineSuggestion([]), null);
  assert.equal(LS.baselineSuggestion(null), null);
});

test("微小的調整不算訊號（有人在試按鍵）", () => {
  assert.equal(LS.baselineSuggestion([20, 30, 20]), null);
});

test("建議值取中位數，不被一首爛 LRC 拖走", () => {
  // 平均會是 (280+300+340)/3 ≈ 307；中位數 300 比較接近真正的喇叭延遲
  assert.equal(LS.baselineSuggestion([280, 300, 340]).suggestMs, 300);
});

test("只看最近三首", () => {
  // 前面兩首是反方向的舊資料，不該擋掉現在這三首的規律
  const s = LS.baselineSuggestion([-500, -400, 300, 280, 320]);
  assert.ok(s && s.suggestMs === 300);
});

test("建議只是一句話，不是一個動作", () => {
  const hint = LS.baselineHint(LS.baselineSuggestion([300, 280, 320]));
  assert.match(hint, /比較像喇叭的延遲/);
  assert.match(hint, /按 L/);
  assert.equal(LS.baselineHint(null), "");
});

// --- 升級成本機基準的確認文字 ---

test("升級確認要講滿三件事", () => {
  const text = LS.rebaseConfirmText({ deltaMs: 300, deviceMs: 80, tunedCount: 47 });
  assert.match(text, /\+80 ms → \+380 ms/);    // 基準會變成多少
  assert.match(text, /47 首/);                  // 會動到幾首（不講的話就是偷改）
  assert.match(text, /第二台舞台機/);            // 另一台不會跟著變
  assert.match(text, /回得去/);                  // 後悔的路
});

test("沒有校正過的歌就不提那一句", () => {
  const text = LS.rebaseConfirmText({ deltaMs: 300, deviceMs: 0, tunedCount: 0 });
  assert.ok(!text.includes("首歌會各減掉"));
});

test("沒有一個說法會吐出 undefined", () => {
  for (const args of [undefined, {}, { songMs: null }, { deviceMs: "x" }]) {
    assert.ok(!JSON.stringify(LS.syncToastLines(args)).includes("undefined"));
    assert.ok(!LS.deckOffsetSummary(args).includes("undefined"));
    assert.ok(!LS.rebaseConfirmText(args).includes("undefined"));
  }
});

// --- 兩條時間軸（這是整個功能唯一會「無聲造成傷害」的地方）---

test("評分時間只扣裝置延遲，字幕時間才扣這首歌的偏移", () => {
  const t = LS.syncTimes({ audioTime: 10, outputLatency: 0.05, deviceMs: 80, songMs: 300 });
  assert.equal(Math.round(t.scoreTime * 1000), 9870);   // 10 − 0.05 − 0.08
  assert.equal(Math.round(t.lyricTime * 1000), 9570);   // 再 − 0.30
});

test("這首歌的偏移不准移動評分時間", () => {
  // 同一首歌調了 +500ms，scoreTime 必須一個位元都不動 ——
  // 動了就是把計分視窗搬離真實人聲，那首歌的音準率會無聲下降
  const a = LS.syncTimes({ audioTime: 42, outputLatency: 0.05, deviceMs: 80, songMs: 0 });
  const b = LS.syncTimes({ audioTime: 42, outputLatency: 0.05, deviceMs: 80, songMs: 500 });
  assert.equal(a.scoreTime, b.scoreTime);
  assert.ok(b.lyricTime < a.lyricTime);
});

test("裝置延遲兩條都要扣（那是整台機器的聽覺延遲）", () => {
  const a = LS.syncTimes({ audioTime: 42, deviceMs: 0, songMs: 0 });
  const b = LS.syncTimes({ audioTime: 42, deviceMs: 200, songMs: 0 });
  assert.equal(Math.round((a.scoreTime - b.scoreTime) * 1000), 200);
  assert.equal(Math.round((a.lyricTime - b.lyricTime) * 1000), 200);
});

test("缺參數不會算出 NaN", () => {
  const t = LS.syncTimes({});
  assert.equal(t.scoreTime, 0);
  assert.equal(t.lyricTime, 0);
  assert.ok(Number.isFinite(LS.syncTimes({ audioTime: "x", deviceMs: null }).scoreTime));
});

// --- 伺服器的裝置延遲要不要採用 ---

test("已經調過的裝置不接受伺服器的值", () => {
  // 伺服器那份只活在記憶體裡，重開就是 0；沒有這條守門的話，
  // 重開伺服器會把每一台舞台的喇叭補償靜靜歸零
  assert.equal(LS.adoptDeviceOffset({ serverMs: 0, localMs: 80, hasLocal: true }), null);
  assert.equal(LS.adoptDeviceOffset({ serverMs: 999, localMs: 80, hasLocal: true }), null);
});

test("全新的裝置採用伺服器的值當起始值", () => {
  assert.equal(LS.adoptDeviceOffset({ serverMs: 120, localMs: 0, hasLocal: false }), 120);
});

test("解鎖之後就以自己為準（自己推上去的值不必再收回來）", () => {
  assert.equal(LS.adoptDeviceOffset({ serverMs: 120, hasLocal: false, unlocked: true }), null);
});

test("值一樣就不動（防 CONTROL↔STATE_UPDATE 回授）", () => {
  assert.equal(LS.adoptDeviceOffset({ serverMs: 80, localMs: 80, hasLocal: false }), null);
});

test("伺服器沒給這個欄位就不動", () => {
  assert.equal(LS.adoptDeviceOffset({ localMs: 80 }), null);
  assert.equal(LS.adoptDeviceOffset({ serverMs: null, localMs: 80 }), null);
});
