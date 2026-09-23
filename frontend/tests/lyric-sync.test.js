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

// --- 兩點校正（速度）---
//
// 守的是一件事：**寧可拒絕也不要算出一條錯的直線**。速度的錯誤不是差一點，
// 是隨時間放大，而且錯的地方在片尾 —— 沒有人在那裡盯著字幕。

test("速度的夾限與後端一致", () => {
  const py = fs.readFileSync(path.join(__dirname, "..", "..", "backend", "services",
                                       "lyric_offsets.py"), "utf8");
  assert.equal(LS.SYNC_MIN_RATE, Number(/MIN_RATE = ([\d.]+)/.exec(py)[1]));
  assert.equal(LS.SYNC_MAX_RATE, Number(/MAX_RATE = ([\d.]+)/.exec(py)[1]));
  assert.equal(LS.SYNC_RATE_EPSILON, Number(/RATE_EPSILON = ([\d.]+)/.exec(py)[1]));
});

test("速度認不得的值一律當 1，絕不當 0（它在分母裡）", () => {
  assert.equal(LS.clampRate(undefined), 1);
  assert.equal(LS.clampRate("abc"), 1);
  assert.equal(LS.clampRate(null), 1);
  assert.equal(LS.clampRate(0), 1);
  assert.equal(LS.clampRate(-1.02), 1);
  assert.equal(LS.clampRate(NaN), 1);
  assert.equal(LS.clampRate(Infinity), 1);
  assert.equal(LS.clampRate(9), LS.SYNC_MAX_RATE);
  assert.equal(LS.clampRate(0.1), LS.SYNC_MIN_RATE);
  assert.equal(LS.clampRate(1.0005), 1);       // 比 epsilon 小＝沒有速度問題
  assert.equal(LS.clampRate("1.024"), 1.024);
});

test("速度只縮放字幕時間，評分時間一動都不動", () => {
  const withRate = LS.syncTimes({ audioTime: 100, rate: 1.02 });
  const flat = LS.syncTimes({ audioTime: 100, rate: 1 });
  assert.equal(withRate.scoreTime, flat.scoreTime);   // ← 這一條是整個功能的紅線
  assert.ok(withRate.lyricTime < flat.lyricTime);
});

test("rate = 1 時退化成 v1.22 的算式（舊行為一格都不能差）", () => {
  const before = { audioTime: 100, outputLatency: 0.02, deviceMs: 80, songMs: -150 };
  const t = LS.syncTimes({ ...before, rate: 1 });
  assert.equal(t.scoreTime, 100 - 0.02 - 0.08);
  assert.equal(t.lyricTime, t.scoreTime + 0.15);
});

test("裝置延遲與這首歌的偏移在 rate ≠ 1 時仍然可以互換（升級成本機基準才會正確）", () => {
  const a = LS.syncTimes({ audioTime: 100, deviceMs: 0, songMs: 200, rate: 1.03 });
  const b = LS.syncTimes({ audioTime: 100, deviceMs: 200, songMs: 0, rate: 1.03 });
  assert.ok(Math.abs(a.lyricTime - b.lyricTime) < 1e-9);
});

const LYRICS = [
  { line_idx: 0, start: 12.0, end: 16.0, text: "第一句" },
  { line_idx: 1, start: 17.0, end: 21.0, text: "第二句" },
  { line_idx: 2, start: 95.0, end: 99.0, text: "副歌第一句" },
  { line_idx: 3, start: 180.0, end: 184.0, text: "最後一句" },
];

test("配對到離按鍵最近的那一句，而且說得出是哪一句", () => {
  const hit = LS.snapAnchor(LYRICS, 95.4);
  assert.equal(hit.lineIdx, 2);
  assert.equal(hit.text, "副歌第一句");
  assert.ok(Math.abs(hit.gapS - 0.4) < 1e-9);
});

test("離所有句首都太遠就不收（那是抓到別首歌的歌詞，不是對不準）", () => {
  assert.equal(LS.snapAnchor(LYRICS, 50), null);
  assert.equal(LS.snapAnchor([], 12), null);
  assert.equal(LS.snapAnchor(LYRICS, NaN), null);
});

test("兩點解出速度與偏移", () => {
  // 上傳版本比歌詞版本慢 2%：歌詞 12s 的那一句在音訊的 12.24s 才唱到
  const solved = LS.solveTwoPoint(
    { lrcS: 12, audioS: 12 * 1.02 },
    { lrcS: 180, audioS: 180 * 1.02 });
  assert.equal(solved.ok, true);
  assert.ok(Math.abs(solved.rate - 1.02) < 1e-6);
  assert.equal(solved.offsetMs, 0);
});

test("速度與偏移一起解（片頭被剪掉、又變了速）", () => {
  const solved = LS.solveTwoPoint(
    { lrcS: 12, audioS: 12 * 1.02 + 0.3 },
    { lrcS: 180, audioS: 180 * 1.02 + 0.3 });
  assert.equal(solved.ok, true);
  assert.ok(Math.abs(solved.rate - 1.02) < 1e-6);
  assert.equal(solved.offsetMs, 300);
});

test("兩點按反了照樣解得出來（照歌詞時間排先後）", () => {
  const forward = LS.solveTwoPoint({ lrcS: 12, audioS: 12.24 }, { lrcS: 180, audioS: 183.6 });
  const backward = LS.solveTwoPoint({ lrcS: 180, audioS: 183.6 }, { lrcS: 12, audioS: 12.24 });
  assert.deepEqual(forward, backward);
});

test("兩點太近就不算 —— 那會把一首只歪一點的歌弄得更歪", () => {
  const solved = LS.solveTwoPoint({ lrcS: 12, audioS: 12.5 }, { lrcS: 40, audioS: 40.9 });
  assert.equal(solved.ok, false);
  assert.equal(solved.reason, "span");
});

test("按鍵誤差會被短跨距放大成片尾好幾秒的歪 —— 最小跨距是必要的，不是保守", () => {
  // 同一份 0.15 秒的按鍵誤差（第 2 點晚按了），跨距愈短、解出來的速度愈離譜。
  // 用真正的求解器算，因為要守的是「這個常數夠不夠大」，不是一則算術。
  const driftAtSpan = (spanS) => Math.abs(LS.driftCorrectionS({
    rate: (spanS + 0.15) / spanS, offsetMs: 0, durationS: 240,
  }));
  // 跨距 20 秒：片尾被這一次「校正」推歪 1.5 秒以上 —— 比不校正還糟
  assert.ok(driftAtSpan(20) > 1.5);
  // 最小跨距：同樣的按鍵誤差在片尾只剩 0.7 秒以內
  assert.ok(driftAtSpan(LS.ANCHOR_MIN_SPAN_S) < 0.7);
  // 而最小跨距本身是收得下的（這一條守著「夠大」與「還能用」的另一邊）
  assert.equal(LS.solveTwoPoint(
    { lrcS: 12, audioS: 12 },
    { lrcS: 12 + LS.ANCHOR_MIN_SPAN_S, audioS: 12 + LS.ANCHOR_MIN_SPAN_S + 0.15 }).ok, true);
  // 而 20 秒那種跨距根本不會被收下
  assert.equal(LS.solveTwoPoint({ lrcS: 12, audioS: 12 },
                                { lrcS: 32, audioS: 32.15 }).reason, "span");
});

test("速度算出來離譜就拒絕（一定是有一點對到別的句子了）", () => {
  const solved = LS.solveTwoPoint({ lrcS: 12, audioS: 12 }, { lrcS: 100, audioS: 140 });
  assert.equal(solved.ok, false);
  assert.equal(solved.reason, "rate");
});

test("先後順序對不起來就拒絕", () => {
  const solved = LS.solveTwoPoint({ lrcS: 12, audioS: 180 }, { lrcS: 180, audioS: 12 });
  assert.equal(solved.ok, false);
  assert.equal(solved.reason, "order");
});

test("偏移超過上限就拒絕（那是抓到別首歌，不是對不準）", () => {
  const solved = LS.solveTwoPoint(
    { lrcS: 12, audioS: 12 + 3.5 }, { lrcS: 180, audioS: 180 + 3.5 });
  assert.equal(solved.ok, false);
  assert.equal(solved.reason, "offset");
});

test("缺數字就拒絕，不會算出 NaN 直線", () => {
  assert.equal(LS.solveTwoPoint(null, { lrcS: 1, audioS: 1 }).reason, "points");
  assert.equal(LS.solveTwoPoint({ lrcS: "x", audioS: 1 }, { lrcS: 180, audioS: 180 }).reason,
               "points");
});

test("速度其實沒問題時就只留偏移（不存一個 1.0008 讓人誤會）", () => {
  const solved = LS.solveTwoPoint(
    { lrcS: 12, audioS: 12.2 }, { lrcS: 180, audioS: 180.21 });
  assert.equal(solved.ok, true);
  assert.equal(solved.rate, 1);
  assert.equal(solved.flat, true);
});

test("片尾會移動多少秒 —— 校正的價值全在後半段", () => {
  const drift = LS.driftCorrectionS({ rate: 1.02, offsetMs: 0, durationS: 240 });
  assert.ok(Math.abs(drift - 4.8) < 1e-6);
  assert.equal(LS.driftCorrectionS({ rate: 1, offsetMs: 300, durationS: 240 }), 0.3);
  assert.equal(LS.driftCorrectionS({}), 0);
});

test("第 1 點要印出配對到的那一句（機器猜得到、猜不準，所以讓人確認）", () => {
  const lines = LS.twoPointToastLines({ kind: "first", text: "副歌第一句" });
  assert.ok(lines.main.includes("副歌第一句"));
  assert.ok(lines.hint.includes("A"));
});

test("第 2 點太近＝重標第 1 點，不是錯誤（按錯的人本來就會立刻再按一次）", () => {
  const lines = LS.twoPointToastLines({ kind: "restart", text: "第二句" });
  assert.ok(lines.main.includes("重新標記"));
  assert.ok(lines.hint.includes("第二句"));
});

test("每一種拒絕都說得出為什麼、以及接下來按什麼", () => {
  ["span", "order", "rate", "offset", "???"].forEach(reason => {
    const lines = LS.twoPointToastLines({ kind: "reject", reason, spanS: 20, rate: 1.9,
                                          offsetMs: 3500 });
    assert.ok(lines.main.length > 0, reason);
    assert.ok(lines.hint.length > 0, reason);
    assert.ok(!/undefined|NaN/.test(lines.main + lines.hint), reason);
  });
});

test("解出來之後要講速度在講什麼，還有片尾會差多少", () => {
  const lines = LS.twoPointToastLines({ kind: "applied", rate: 1.02, offsetMs: -150,
                                        driftS: -4.8 });
  assert.ok(lines.main.includes("1.020×"));
  assert.ok(lines.main.includes("−150 ms"));
  assert.ok(lines.hint.includes("4.8 秒"));
  assert.ok(lines.hint.includes("0"));       // 不對就按 0 歸零
});

test("速度沒問題時不要講速度（那只會讓人以為機器動了它）", () => {
  const lines = LS.twoPointToastLines({ kind: "applied", rate: 1, offsetMs: 200, driftS: 0.2 });
  assert.ok(lines.main.includes("速度沒問題"));
  assert.ok(!lines.main.includes("×"));
});

test("沒有歌在唱時按 A：說明，不是靜靜不做事", () => {
  const lines = LS.twoPointToastLines({ kind: "idle" });
  assert.ok(lines.main.includes("沒有歌"));
  assert.ok(lines.hint.includes("A"));
});

test("速度的說法沒有一個會吐出 undefined", () => {
  [undefined, null, 0, 1, 1.024, 0.97, "abc"].forEach(r => {
    assert.ok(!/undefined|NaN/.test(LS.rateLabel(r) + LS.rateDirection(r)), String(r));
  });
  assert.equal(LS.rateLabel(1), "正常速度");
  assert.equal(LS.rateLabel(1.024), "1.024×");
  assert.ok(LS.rateDirection(1.02).includes("快"));
  assert.ok(LS.rateDirection(0.98).includes("慢"));
  assert.equal(LS.rateDirection(1), "");
});

test("點歌台那一行在速度校正過時要說得出來（手機上唯一看得到 rate 的地方）", () => {
  const plain = LS.deckOffsetSummary({ songMs: 100, deviceMs: 0, rate: 1 });
  const tuned = LS.deckOffsetSummary({ songMs: 100, deviceMs: 0, rate: 1.024 });
  assert.ok(!plain.includes("×"));
  assert.ok(tuned.includes("1.024×"));
});
