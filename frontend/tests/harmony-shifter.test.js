/**
 * 和聲移調器的數值測試（node --test）。
 *
 * 移調器是唯一「聽起來對不對」完全等於「算得對不對」的模組：
 * 差一個半音就是走音，而且是在包廂裡當著客人的面走音。
 * 所以核心 DSP 刻意寫成不依賴 Web Audio 的純陣列運算，
 * 好讓 CI 每次都真的量一次「輸入 220Hz、要求升八度，出來是不是 440Hz」。
 *
 * 量頻率用零交越計數：正弦波在這個用法下夠準（誤差只來自取樣量化，
 * 大小是 1/觀察窗長度），而且不必為了測試搬一整個 FFT 進來。
 */
const test = require("node:test");
const assert = require("node:assert/strict");

const { HarmonyShifterCore, WINDOW_SECONDS } = require("../js/harmony-worklet.js");

const RATE = 48000;

function sine(freq, seconds, amp = 1) {
  const n = Math.floor(RATE * seconds);
  const buf = new Float32Array(n);
  for (let i = 0; i < n; i++) buf[i] = amp * Math.sin(2 * Math.PI * freq * i / RATE);
  return buf;
}

function shift(input, semitones, core = new HarmonyShifterCore(RATE)) {
  const out = new Float32Array(input.length);
  core.process(input, out, semitones);
  return out;
}

/**
 * 用零交越計數量頻率。
 *
 * 跳過前四分之一（延遲視窗要走過一輪才進入穩態），其餘全部用上 ——
 * 觀察窗越長解析度越好（半秒的窗只能量到 ±2Hz，對 110Hz 就是 ±1.8% 的誤差，
 * 剛好會壓到容許值上，那種測試失敗查起來最浪費時間）。
 */
function measureFreq(buf) {
  const start = Math.floor(buf.length * 0.25);
  let crossings = 0;
  for (let i = start + 1; i < buf.length; i++) {
    if (buf[i - 1] <= 0 && buf[i] > 0) crossings++;
  }
  return crossings / ((buf.length - start) / RATE);
}

function rms(buf, fromRatio = 0.5) {
  const start = Math.floor(buf.length * fromRatio);
  let sum = 0;
  for (let i = start; i < buf.length; i++) sum += buf[i] * buf[i];
  return Math.sqrt(sum / (buf.length - start));
}

test("移調量 0：頻率不變，音量不變", () => {
  const input = sine(220, 1);
  const out = shift(input, 0);
  assert.ok(Math.abs(measureFreq(out) - 220) < 3, `freq=${measureFreq(out)}`);
  assert.ok(Math.abs(rms(out) - rms(input)) < 0.01, `rms=${rms(out)}`);
});

test("升一個八度：220Hz → 440Hz", () => {
  const out = shift(sine(220, 1), 12);
  const f = measureFreq(out);
  assert.ok(Math.abs(f - 440) / 440 < 0.02, `freq=${f}`);
});

test("降一個八度：220Hz → 110Hz", () => {
  const out = shift(sine(220, 1), -12);
  const f = measureFreq(out);
  assert.ok(Math.abs(f - 110) / 110 < 0.02, `freq=${f}`);
});

test("和聲實際會用到的度數都準（±2%）", () => {
  // 三度和聲會用到 +3 / +4，五度用 +6 / +7，下三度用 -3 / -4
  for (const semis of [3, 4, 6, 7, -3, -4, -5]) {
    const expected = 220 * Math.pow(2, semis / 12);
    const f = measureFreq(shift(sine(220, 1), semis));
    assert.ok(Math.abs(f - expected) / expected < 0.02,
      `${semis} 半音：期待 ${expected.toFixed(1)}Hz，量到 ${f.toFixed(1)}Hz`);
  }
});

test("兩條讀取頭的窗形加起來恆為 1（和聲音量不會隨移調量起伏）", () => {
  // 直流輸入是最乾淨的驗證：輸出 = wA·1 + wB·1，只要窗形互補就恆等於 1
  const dc = new Float32Array(RATE).fill(1);
  for (const semis of [0, 3, 4, 7, 12, -12]) {
    const out = shift(dc, semis);
    const tail = out.slice(Math.floor(out.length * 0.5));
    for (let i = 0; i < tail.length; i += 97) {
      assert.ok(Math.abs(tail[i] - 1) < 1e-4,
        `${semis} 半音在第 ${i} 個樣本增益是 ${tail[i]}`);
    }
  }
});

test("音量守恆：移調後的 RMS 與輸入相差不到 8%", () => {
  // 8% 不是隨手設的容許值，是交叉淡接期間梳型效應的實測上限
  // （兩條讀取頭相差 depth/2，某些頻率會在那 30% 的時間裡互相抵消）。
  // 330Hz 附近剛好落在最深的那個抵消點上，所以拿它當守門的樣本。
  for (const freq of [220, 294, 330, 523, 880]) {
    const input = sine(freq, 1, 0.5);
    for (const semis of [3, 4, 7, -12]) {
      const out = shift(input, semis);
      const dev = Math.abs(rms(out) - rms(input)) / rms(input);
      assert.ok(dev < 0.08,
        `${freq}Hz ${semis} 半音掉了 ${(dev * 100).toFixed(1)}%`);
    }
  }
});

test("不會爆掉：輸出不超過輸入的峰值太多，也不會有 NaN", () => {
  const input = sine(440, 0.5, 0.9);
  const out = shift(input, 4);
  for (let i = 0; i < out.length; i++) {
    assert.ok(Number.isFinite(out[i]), `第 ${i} 個樣本是 ${out[i]}`);
    assert.ok(Math.abs(out[i]) <= 1.0, `第 ${i} 個樣本溢出：${out[i]}`);
  }
});

test("靜音進、靜音出（沒人唱的時候不會自己生出聲音）", () => {
  const out = shift(new Float32Array(RATE / 2), 4);
  assert.equal(rms(out, 0), 0);
});

test("分區塊處理與一次處理的結果一致（worklet 是 128 樣本一塊）", () => {
  const input = sine(262, 0.5);
  const whole = shift(input, 4);

  const core = new HarmonyShifterCore(RATE);
  const chunked = new Float32Array(input.length);
  const block = 128;
  for (let off = 0; off + block <= input.length; off += block) {
    const inBlock = input.subarray(off, off + block);
    const outBlock = new Float32Array(block);
    core.process(inBlock, outBlock, 4);
    chunked.set(outBlock, off);
  }
  const n = Math.floor(input.length / block) * block;
  for (let i = 0; i < n; i += 13) {
    assert.ok(Math.abs(whole[i] - chunked[i]) < 1e-6,
      `第 ${i} 個樣本不一致：${whole[i]} vs ${chunked[i]}`);
  }
});

test("延遲在一個視窗之內（和聲不能慢到變成回音）", () => {
  // 脈衝進去，量它多久之後出來
  const input = new Float32Array(RATE / 4);
  input[0] = 1;
  const out = shift(input, 0);
  let first = -1;
  for (let i = 0; i < out.length; i++) {
    if (Math.abs(out[i]) > 0.01) { first = i; break; }
  }
  assert.ok(first >= 0, "訊號完全沒出來");
  assert.ok(first / RATE <= WINDOW_SECONDS,
    `延遲 ${(first / RATE * 1000).toFixed(1)}ms 超過視窗 ${WINDOW_SECONDS * 1000}ms`);
});

test("reset 清掉殘留樣本（上一首的尾音不會被疊進下一首）", () => {
  const core = new HarmonyShifterCore(RATE);
  shift(sine(440, 0.2, 1.0), 4, core);
  core.reset();
  const out = shift(new Float32Array(RATE / 10), 4, core);
  assert.equal(rms(out, 0), 0);
});

test("取樣率不同也能建（48k 與 44.1k 的視窗長度不一樣）", () => {
  const a = new HarmonyShifterCore(44100);
  const b = new HarmonyShifterCore(48000);
  assert.ok(a.depth < b.depth);
  assert.equal(a.size & (a.size - 1), 0, "環形緩衝必須是 2 的次方");
  assert.equal(b.size & (b.size - 1), 0, "環形緩衝必須是 2 的次方");
  // 取樣率壞掉（0、負數、undefined）也要給得出可用的預設值
  assert.ok(new HarmonyShifterCore(0).depth > 0);
  assert.ok(new HarmonyShifterCore(undefined).depth > 0);
});
