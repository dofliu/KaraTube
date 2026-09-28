/**
 * 防嘯叫的前端單元測試（node --test）。
 *
 * 這個功能兩個方向的失敗都很貴，所以兩邊都要釘：
 *   * 漏判 → 包廂裡繼續尖叫（這是使用者唯一會描述的症狀）。
 *   * 誤判 → 在人聲上挖了一個洞，而且**沒有任何人會知道原因** ——
 *     沒有訊息、沒有聲響，只是這位客人覺得今天麥克風怪怪的。
 * 所以測試裡有一半是「這些東西不可以被當成嘯叫」。
 */
const test = require("node:test");
const assert = require("node:assert/strict");

const {
  FeedbackGuard,
  findHowlPeaks,
  fbMedian,
  fbSemitonesApart,
  HOWL_PROMINENCE_DB,
  HOWL_ONSET_DB,
  HOWL_LOCK_SECONDS,
  HOWL_HOT_LOCK_SECONDS,
  HOWL_DECAY_TOLERANCE_DB,
  NOTCH_DEPTH_STEPS,
  NOTCH_Q,
  NOTCH_RELEASE_SECONDS,
  NOTCH_PROBE_GAP_SECONDS,
  OVERLOAD_HOLD_SECONDS,
} = require("../js/feedback-guard.js");

const SAMPLE_RATE = 48000;
const FFT_SIZE = 2048;
const BIN_HZ = SAMPLE_RATE / FFT_SIZE;   // 23.4375 Hz
const FRAME = 1 / 60;

function binOf(hz) {
  return Math.round(hz / BIN_HZ);
}

/** 一片平坦的底噪頻譜。 */
function floorSpectrum(db = -80) {
  return new Float32Array(FFT_SIZE / 2).fill(db);
}

/**
 * 在底噪上疊一根窄峰（含左右各一格的裙擺，真的 FFT 不會只亮一格）。
 * peaks = [{ hz, db }]
 */
function withPeaks(peaks, floorDb = -80) {
  const spec = floorSpectrum(floorDb);
  for (const { hz, db } of peaks) {
    const b = binOf(hz);
    spec[b] = db;
    if (b - 1 >= 0) spec[b - 1] = Math.max(spec[b - 1], db - 9);
    if (b + 1 < spec.length) spec[b + 1] = Math.max(spec[b + 1], db - 12);
  }
  return spec;
}

function frame(spectrum) {
  return { spectrum, sampleRate: SAMPLE_RATE, fftSize: FFT_SIZE };
}

/** 餵 seconds 秒同一片頻譜。 */
function feed(guard, seconds, spectrum, frameSec = FRAME) {
  const frames = Math.max(1, Math.round(seconds / frameSec));
  let last = guard.notches();
  for (let i = 0; i < frames; i++) last = guard.update(frameSec, frame(spectrum));
  return last;
}

function armed(options = {}) {
  return new FeedbackGuard(options).setArmed(true);
}

// ── 純函式 ────────────────────────────────────────────────────

test("fbMedian：奇數取中間、偶數取平均、空陣列回 -Infinity", () => {
  assert.equal(fbMedian([1, 5, 3]), 3);
  assert.equal(fbMedian([1, 2, 3, 4]), 2.5);
  assert.equal(fbMedian([]), -Infinity);
});

test("fbSemitonesApart：八度是 12 個半音，順序不影響結果", () => {
  assert.ok(Math.abs(fbSemitonesApart(440, 880) - 12) < 1e-9);
  assert.ok(Math.abs(fbSemitonesApart(880, 440) - 12) < 1e-9);
  assert.equal(fbSemitonesApart(440, 0), Infinity);
});

test("findHowlPeaks：底噪裡什麼都沒有", () => {
  assert.equal(findHowlPeaks(floorSpectrum(), SAMPLE_RATE, FFT_SIZE).length, 0);
});

test("findHowlPeaks：一根夠突出的窄峰抓得到，頻率誤差在一格以內", () => {
  const peaks = findHowlPeaks(withPeaks([{ hz: 2400, db: -20 }]), SAMPLE_RATE, FFT_SIZE);
  assert.equal(peaks.length, 1);
  assert.ok(Math.abs(peaks[0].freq - 2400) <= BIN_HZ);
  assert.ok(peaks[0].prominence >= HOWL_PROMINENCE_DB);
});

test("findHowlPeaks：突出度不夠的不算（寬帶噪音裡的小起伏）", () => {
  const spec = floorSpectrum(-40);
  spec[binOf(2400)] = -40 + (HOWL_PROMINENCE_DB - 4);
  assert.equal(findHowlPeaks(spec, SAMPLE_RATE, FFT_SIZE).length, 0);
});

test("findHowlPeaks：太小聲的峰不算 —— 安靜房間的底噪也有 20 dB 的起伏", () => {
  const peaks = findHowlPeaks(
    withPeaks([{ hz: 2400, db: HOWL_ONSET_DB - 6 }], -95), SAMPLE_RATE, FFT_SIZE);
  assert.equal(peaks.length, 0);
});

test("findHowlPeaks：低於 180 Hz 與高於 8 kHz 都不看", () => {
  assert.equal(findHowlPeaks(withPeaks([{ hz: 120, db: -20 }]), SAMPLE_RATE, FFT_SIZE).length, 0);
  assert.equal(findHowlPeaks(withPeaks([{ hz: 9500, db: -20 }]), SAMPLE_RATE, FFT_SIZE).length, 0);
});

test("findHowlPeaks：諧波列（人聲母音）不會被當成一堆嘯叫", () => {
  // 基頻 300 Hz 的一列諧波：泛音要被剔掉，只剩基頻那一根是候選
  const voice = [300, 600, 900, 1200, 1500].map((hz, i) => ({ hz, db: -22 - i * 2 }));
  const peaks = findHowlPeaks(withPeaks(voice), SAMPLE_RATE, FFT_SIZE);
  assert.equal(peaks.length, 1);
  assert.ok(Math.abs(peaks[0].freq - 300) <= BIN_HZ);
});

test("findHowlPeaks：自激長出二次諧波時，抓到的仍然是基頻那一根", () => {
  // 嘯叫被推到失真：3000 Hz 很大、6000 Hz 小一點。
  // 往下看諧波（而不是往上）才不會把 3000 那一根放掉。
  const peaks = findHowlPeaks(
    withPeaks([{ hz: 3000, db: -12 }, { hz: 6000, db: -24 }]), SAMPLE_RATE, FFT_SIZE);
  assert.equal(peaks.length, 1);
  assert.ok(Math.abs(peaks[0].freq - 3000) <= BIN_HZ);
});

// ── 什麼時候作用 ──────────────────────────────────────────────

test("沒開多人模式就完全不作用 —— 人聲不進喇叭時根本沒有回授迴路", () => {
  const guard = new FeedbackGuard();   // 預設 armed = false
  feed(guard, 5, withPeaks([{ hz: 2400, db: -18 }]));
  assert.equal(guard.notches().length, 0);
  assert.equal(guard.isActive(), false);
});

test("關掉功能等於沒有這個功能：既有的凹槽立刻放掉", () => {
  const guard = armed();
  feed(guard, 2, withPeaks([{ hz: 2400, db: -18 }]));
  assert.ok(guard.notches().length > 0);
  guard.configure({ enabled: false });
  assert.equal(guard.update(FRAME, frame(withPeaks([{ hz: 2400, db: -18 }]))).length, 0);
});

test("切回單人模式把凹槽全部放掉（迴路被物理性切斷了）", () => {
  const guard = armed();
  feed(guard, 2, withPeaks([{ hz: 2400, db: -18 }]));
  assert.ok(guard.notches().length > 0);
  guard.setArmed(false);
  assert.equal(guard.notches().length, 0);
});

// ── 抓與不抓 ──────────────────────────────────────────────────

test("持續的窄峰會被鎖定，而且第一格刻意很淺", () => {
  const guard = armed();
  const notches = feed(guard, HOWL_LOCK_SECONDS + 0.2, withPeaks([{ hz: 2400, db: -30 }]));
  assert.equal(notches.length, 1);
  assert.equal(notches[0].gainDb, NOTCH_DEPTH_STEPS[0]);
  assert.equal(notches[0].q, NOTCH_Q);
  assert.ok(Math.abs(notches[0].freq - 2400) <= BIN_HZ);
});

test("鎖定時間沒到就不動手（漏判只是晚半秒，誤判沒有人查得出來）", () => {
  const guard = armed();
  const notches = feed(guard, HOWL_LOCK_SECONDS - 0.15, withPeaks([{ hz: 2400, db: -30 }]));
  assert.equal(notches.length, 0);
});

test("已經在叫的那種（很大聲又極度純）走快車道，0.18 秒就壓下去", () => {
  const guard = armed();
  const notches = feed(guard, HOWL_HOT_LOCK_SECONDS + 0.05, withPeaks([{ hz: 2400, db: -8 }]));
  assert.equal(notches.length, 1);
});

test("會衰減的長音不算嘯叫 —— 自激只會越來越大", () => {
  const guard = armed();
  // 前 0.3 秒很突出，接著開始掉（人唱完一句）
  feed(guard, 0.3, withPeaks([{ hz: 2400, db: -25 }]));
  feed(guard, 0.5, withPeaks([{ hz: 2400, db: -25 - HOWL_DECAY_TOLERANCE_DB - 3 }]));
  assert.equal(guard.notches().length, 0);
});

test("抖音（峰值來回飄）不算嘯叫 —— 共振釘在同一格上不會動", () => {
  const guard = armed();
  const a = withPeaks([{ hz: 2400, db: -25 }]);
  const b = withPeaks([{ hz: 2400 + BIN_HZ * 3, db: -25 }]);
  for (let i = 0; i < 120; i++) {
    guard.update(FRAME, frame(i % 8 < 4 ? a : b));
  }
  assert.equal(guard.notches().length, 0);
});

test("抖音（連續來回掃）也不算 —— 這是現場真正的樣子，5 Hz、上下三格", () => {
  const guard = armed();
  const specs = [];
  for (let k = -3; k <= 3; k++) specs.push(withPeaks([{ hz: 2400 + BIN_HZ * k, db: -25 }]));
  // 5 Hz 的抖音：0.2 秒掃完一個來回
  for (let i = 0; i < 300; i++) {
    const phase = Math.sin((i * FRAME) * 2 * Math.PI * 5);
    const idx = Math.round((phase + 1) / 2 * (specs.length - 1));
    guard.update(FRAME, frame(specs[idx]));
  }
  assert.equal(guard.notches().length, 0);
});

test("一整列諧波的人聲唱久了也只會被盯上基頻，不會挖四個洞", () => {
  const guard = armed();
  const voice = withPeaks([300, 600, 900, 1200].map((hz, i) => ({ hz, db: -24 - i * 2 })));
  feed(guard, 3, voice);
  assert.ok(guard.notches().length <= 1);
});

// ── 深度階梯 ──────────────────────────────────────────────────

test("同一個頻率一直叫就加深一格，不是再開一格新的", () => {
  const guard = armed();
  const spec = withPeaks([{ hz: 2400, db: -20 }]);
  feed(guard, HOWL_LOCK_SECONDS + 0.1, spec);
  assert.equal(guard.notches().length, 1);
  const first = guard.notches()[0].gainDb;
  feed(guard, HOWL_LOCK_SECONDS + 0.1, spec);
  const second = guard.notches()[0].gainDb;
  assert.equal(guard.notches().length, 1);
  assert.ok(second < first, "第二次要更深");
});

test("加深有底：不會挖到比最深的那一格還深", () => {
  const guard = armed();
  const spec = withPeaks([{ hz: 2400, db: -20 }]);
  feed(guard, 20, spec);
  const deepest = NOTCH_DEPTH_STEPS[NOTCH_DEPTH_STEPS.length - 1];
  assert.equal(guard.notches().length, 1);
  assert.equal(guard.notches()[0].gainDb, deepest);
});

test("不同頻率各開一格", () => {
  const guard = armed();
  feed(guard, HOWL_LOCK_SECONDS + 0.1, withPeaks([{ hz: 2400, db: -20 }]));
  feed(guard, HOWL_LOCK_SECONDS + 0.1, withPeaks([{ hz: 5000, db: -20 }]));
  assert.equal(guard.notches().length, 2);
  // 回傳依頻率排序，讓音訊圖那邊的對應是穩定的
  assert.ok(guard.notches()[0].freq < guard.notches()[1].freq);
});

// ── 上限與「壓不住了」 ────────────────────────────────────────

test("凹槽用完就舉手，而且刻意不自己把麥克風轉小聲", () => {
  const guard = armed({ maxFilters: 2 });
  for (const hz of [1000, 2000, 3000]) {
    feed(guard, HOWL_LOCK_SECONDS + 0.1, withPeaks([{ hz, db: -20 }]));
  }
  assert.equal(guard.notches().length, 2);
  assert.equal(guard.summary().overloaded, true);
});

test("「壓不住了」的旗子會自己放下 —— 不會消失的警告等於沒有警告", () => {
  const guard = armed({ maxFilters: 1 });
  feed(guard, HOWL_LOCK_SECONDS + 0.1, withPeaks([{ hz: 1000, db: -20 }]));
  feed(guard, HOWL_LOCK_SECONDS + 0.1, withPeaks([{ hz: 3000, db: -20 }]));
  assert.equal(guard.summary().overloaded, true);
  feed(guard, OVERLOAD_HOLD_SECONDS + 1, floorSpectrum());
  assert.equal(guard.summary().overloaded, false);
});

test("調小數量上限時多出來的凹槽立刻砍掉，留下比較深的那幾個", () => {
  const guard = armed({ maxFilters: 4 });
  feed(guard, HOWL_LOCK_SECONDS + 0.1, withPeaks([{ hz: 1000, db: -20 }]));
  feed(guard, HOWL_LOCK_SECONDS + 0.1, withPeaks([{ hz: 1000, db: -20 }]));  // 加深
  feed(guard, HOWL_LOCK_SECONDS + 0.1, withPeaks([{ hz: 5000, db: -20 }]));
  assert.equal(guard.notches().length, 2);
  guard.configure({ maxFilters: 1 });
  assert.equal(guard.notches().length, 1);
  assert.ok(Math.abs(guard.notches()[0].freq - 1000) <= BIN_HZ * 2);
});

test("數量上限夾在合法範圍內（設定頁手滑不該讓舞台掛掉）", () => {
  assert.equal(new FeedbackGuard({ maxFilters: 0 }).maxFilters, 1);
  assert.equal(new FeedbackGuard({ maxFilters: 99 }).maxFilters, 8);
  assert.equal(new FeedbackGuard({ maxFilters: NaN }).maxFilters, 1);
});

// ── 放掉的節奏 ────────────────────────────────────────────────

test("安靜下來之後不會立刻放掉 —— 放掉的當下原因可能還在", () => {
  const guard = armed();
  feed(guard, HOWL_LOCK_SECONDS + 0.1, withPeaks([{ hz: 2400, db: -20 }]));
  feed(guard, NOTCH_RELEASE_SECONDS - 5, floorSpectrum(), 0.05);
  assert.equal(guard.notches().length, 1);
});

test("撐過一個回退週期就退一格，退到 0 就把那一格收回來", () => {
  const guard = armed();
  feed(guard, HOWL_LOCK_SECONDS + 0.1, withPeaks([{ hz: 2400, db: -20 }]));
  assert.equal(guard.notches()[0].gainDb, NOTCH_DEPTH_STEPS[0]);
  // 第一格已經是最淺，所以一個週期之後整格收回
  feed(guard, NOTCH_RELEASE_SECONDS + 2, floorSpectrum(), 0.05);
  assert.equal(guard.notches().length, 0);
});

test("深的凹槽一次只退一格，不是一口氣放掉", () => {
  const guard = armed();
  const spec = withPeaks([{ hz: 2400, db: -20 }]);
  feed(guard, 20, spec);   // 一路加深到底
  assert.equal(guard.notches()[0].gainDb, NOTCH_DEPTH_STEPS[3]);
  feed(guard, NOTCH_RELEASE_SECONDS + 2, floorSpectrum(), 0.05);
  assert.equal(guard.notches()[0].gainDb, NOTCH_DEPTH_STEPS[2]);
});

test("同時只有一個凹槽在試放：原因還在的房間不會四個頻率一起叫回來", () => {
  const guard = armed();
  for (const hz of [1000, 2000, 3000]) {
    feed(guard, HOWL_LOCK_SECONDS + 0.1, withPeaks([{ hz, db: -20 }]));
  }
  assert.equal(guard.notches().length, 3);
  // 三格都掛滿了一個週期，但一個週期只收得回一格
  feed(guard, NOTCH_RELEASE_SECONDS + NOTCH_PROBE_GAP_SECONDS / 2, floorSpectrum(), 0.05);
  assert.equal(guard.notches().length, 2);
});

test("試放之後又叫回來 → 立刻加深，而且不會被當成新的一格", () => {
  const guard = armed();
  const spec = withPeaks([{ hz: 2400, db: -20 }]);
  feed(guard, 20, spec);                                   // 深到底
  feed(guard, NOTCH_RELEASE_SECONDS + 2, floorSpectrum(), 0.05);   // 退一格
  assert.equal(guard.notches()[0].gainDb, NOTCH_DEPTH_STEPS[2]);
  feed(guard, HOWL_LOCK_SECONDS + 0.2, spec);              // 又叫了
  assert.equal(guard.notches().length, 1);
  assert.equal(guard.notches()[0].gainDb, NOTCH_DEPTH_STEPS[3]);
});

// ── 其他 ──────────────────────────────────────────────────────

test("summary 講得出現場需要的三件事：幾個點、最深多少、有沒有壓不住", () => {
  const guard = armed();
  feed(guard, HOWL_LOCK_SECONDS + 0.1, withPeaks([{ hz: 2400, db: -20 }]));
  const s = guard.summary();
  assert.equal(s.notches, 1);
  assert.equal(s.deepest_db, NOTCH_DEPTH_STEPS[0]);
  assert.equal(s.overloaded, false);
  assert.ok(s.caught >= 1);
  assert.equal(s.frequencies.length, 1);
});

test("空的／壞的頻譜不會讓它爆掉（舞台在麥克風權限還沒下來時也會轉）", () => {
  const guard = armed();
  assert.deepEqual(guard.update(FRAME, null), []);
  assert.deepEqual(guard.update(FRAME, { spectrum: null }), []);
  assert.deepEqual(guard.update(FRAME, { spectrum: new Float32Array(0), sampleRate: 0 }), []);
  assert.deepEqual(guard.update(0, frame(withPeaks([{ hz: 2400, db: -8 }]))), []);
});

test("分頁切回來那種超大 dt 會被夾住，不會一幀就跳過整個鎖定時間", () => {
  const guard = armed();
  // 單獨一幀 10 秒：夾到 0.25 秒，不足以鎖定普通候選
  assert.equal(guard.update(10, frame(withPeaks([{ hz: 2400, db: -30 }]))).length, 0);
});
