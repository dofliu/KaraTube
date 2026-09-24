/**
 * 伴奏移調器（升降 Key）的數值測試（node --test）。
 *
 * 跟和聲移調器同一個理由：移調是「聽起來對不對」完全等於「算得對不對」的模組，
 * 差一個半音就是整首歌走音 —— 而且是在包廂裡當著客人的面走音。
 * 所以核心 DSP 寫成不依賴 Web Audio 的純陣列運算，CI 每次都真的量一次。
 *
 * 這裡多守兩件和聲不需要守的事：
 *   * **原調要是逐樣本一模一樣的直通**（九成以上的時候都在這個狀態）。
 *   * **左右聲道的延遲量必須一致**，立體聲像才不會在移調之後左右飄。
 */
const test = require("node:test");
const assert = require("node:assert/strict");

const { MusicShifterCore, MUSIC_WINDOW_SECONDS } = require("../js/music-shifter-worklet.js");

const RATE = 48000;

function sine(freq, seconds, amp = 1, phase = 0) {
  const n = Math.floor(RATE * seconds);
  const buf = new Float32Array(n);
  for (let i = 0; i < n; i++) buf[i] = amp * Math.sin(2 * Math.PI * freq * i / RATE + phase);
  return buf;
}

/** 跑一段立體聲。回傳 [左, 右]。 */
function run(left, right, semitones, core = new MusicShifterCore(RATE, 2)) {
  const outL = new Float32Array(left.length);
  const outR = new Float32Array(left.length);
  core.process([left, right], [outL, outR], semitones);
  return [outL, outR];
}

/**
 * 用零交越計數量頻率。正弦波在這個用法下夠準（誤差只來自取樣量化），
 * 而且不必為了測試搬一整個 FFT 進來。前四分之一跳掉 —— 延遲視窗要走過
 * 一輪才進入穩態。
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

test("原調（0 半音）是逐樣本一模一樣的直通，只差一個固定延遲", () => {
  const core = new MusicShifterCore(RATE, 2);
  const delay = core.depth / 2;
  const left = sine(220, 0.5);
  const right = sine(330, 0.5);
  const [outL, outR] = run(left, right, 0, core);

  // 延遲之後的每一個樣本都要**完全相等**（不是「幾乎相等」）：
  // 原調聽到的必須是原本那個檔案，不是一份重新內插出來的近似值。
  for (let i = delay; i < left.length; i++) {
    assert.equal(outL[i], left[i - delay], `左聲道第 ${i} 個樣本`);
    assert.equal(outR[i], right[i - delay], `右聲道第 ${i} 個樣本`);
  }
});

test("延遲量 = 半個視窗，而且說得出來（字幕補償要用）", () => {
  const core = new MusicShifterCore(RATE, 2);
  assert.ok(Math.abs(core.latencySeconds - MUSIC_WINDOW_SECONDS / 2) < 1e-6);
  // 45ms 是「升 Key 之後字幕會早這麼多」的量，大到一定要補
  assert.ok(core.latencySeconds > 0.03 && core.latencySeconds < 0.08);
});

test("升一個八度：220Hz → 440Hz", () => {
  const [out] = run(sine(220, 1), sine(220, 1), 12);
  const f = measureFreq(out);
  assert.ok(Math.abs(f - 440) / 440 < 0.02, `freq=${f}`);
});

test("降一個八度：440Hz → 220Hz", () => {
  const [out] = run(sine(440, 1), sine(440, 1), -12);
  const f = measureFreq(out);
  assert.ok(Math.abs(f - 220) / 220 < 0.02, `freq=${f}`);
});

test("系統實際用得到的每一個 Key（±1 ~ ±6）都移對位置", () => {
  for (const semi of [-6, -4, -2, -1, 1, 2, 4, 6]) {
    const base = 220;
    const [out] = run(sine(base, 1), sine(base, 1), semi);
    const want = base * Math.pow(2, semi / 12);
    const got = measureFreq(out);
    assert.ok(Math.abs(got - want) / want < 0.02,
      `${semi} 半音：want=${want.toFixed(1)}Hz got=${got.toFixed(1)}Hz`);
  }
});

test("移調不改變音量（交叉淡接的兩條讀取頭加起來永遠是 1）", () => {
  const input = sine(220, 1);
  for (const semi of [-6, -3, 3, 6]) {
    const [out] = run(input, input, semi);
    // 交叉淡接期間同一個聲音以兩個延遲相加會有梳型效應，所以容許 ±25%，
    // 守的是「音量不會隨移調量整個垮掉或爆掉」這件事。
    assert.ok(Math.abs(rms(out) - rms(input)) < 0.25 * rms(input),
      `${semi} 半音：rms=${rms(out).toFixed(3)} vs ${rms(input).toFixed(3)}`);
  }
});

test("左右聲道共用同一條讀取頭軌跡：同樣的輸入出來完全一樣", () => {
  // 兩個聲道餵同一段訊號，輸出必須逐樣本相等。
  // 各跑一份獨立的移調器時，兩邊的延遲量會慢慢分家，
  // 而左右幾十微秒的差別正是人耳判斷方位的依據 —— 舞台會聽起來在飄。
  const mono = sine(330, 1);
  const [outL, outR] = run(mono, Float32Array.from(mono), 3);
  for (let i = 0; i < outL.length; i++) {
    assert.equal(outL[i], outR[i], `第 ${i} 個樣本左右不一致`);
  }
});

test("聲道之間不串音：只有左聲道有訊號時，右聲道保持安靜", () => {
  const left = sine(220, 0.5);
  const right = new Float32Array(left.length);
  const [outL, outR] = run(left, right, 5);
  assert.ok(rms(outL) > 0.3, `左聲道應該有聲音 rms=${rms(outL)}`);
  assert.equal(rms(outR), 0);
});

test("單聲道核心配立體聲輸出：第二個聲道是複製品，不是靜音", () => {
  const core = new MusicShifterCore(RATE, 1);
  const input = sine(220, 0.3);
  const outL = new Float32Array(input.length);
  const outR = new Float32Array(input.length);
  core.process([input], [outL, outR], 2);
  assert.ok(rms(outL) > 0.3);
  for (let i = 0; i < outL.length; i++) assert.equal(outL[i], outR[i]);
});

test("單聲道的伴奏檔配立體聲輸出：兩邊喇叭都要有聲音", () => {
  // 輸入只有一個聲道（單聲道的伴奏 mp3），輸出是立體聲。
  // 第二個聲道當成靜音的話，那首歌只有一邊喇叭會響 ——
  // 而且只有升降 Key 打開的時候才這樣。
  const core = new MusicShifterCore(RATE, 2);
  const mono = sine(220, 0.3);
  const outL = new Float32Array(mono.length);
  const outR = new Float32Array(mono.length);
  core.process([mono], [outL, outR], 4);
  assert.ok(rms(outL) > 0.3, `左聲道 rms=${rms(outL)}`);
  for (let i = 0; i < outL.length; i++) assert.equal(outL[i], outR[i]);
});

test("reset 之後不會留下上一首的尾音", () => {
  const core = new MusicShifterCore(RATE, 2);
  const loud = sine(220, 0.2, 1);
  run(loud, loud, 4, core);
  core.reset();

  const silence = new Float32Array(Math.floor(RATE * 0.2));
  const [outL, outR] = run(silence, silence, 4, core);
  assert.equal(rms(outL, 0), 0);
  assert.equal(rms(outR, 0), 0);
});

test("沒有輸入的聲道當成靜音，不會丟例外", () => {
  const core = new MusicShifterCore(RATE, 2);
  const outL = new Float32Array(256);
  const outR = new Float32Array(256);
  core.process([], [outL, outR], 2);
  assert.equal(rms(outL, 0), 0);
  assert.equal(rms(outR, 0), 0);
});

test("移調量超出 ±12 也不會爆掉（夾限是點歌台的事，DSP 只算）", () => {
  const [out] = run(sine(220, 0.5), sine(220, 0.5), 24);
  for (let i = 0; i < out.length; i++) {
    assert.ok(Number.isFinite(out[i]), `第 ${i} 個樣本不是有限數`);
  }
});

test("跨區塊連續處理與一次處理整段等價（讀取頭軌跡不會在區塊邊界重來）", () => {
  const input = sine(220, 0.5);
  const [whole] = run(input, input, 3);

  const core = new MusicShifterCore(RATE, 2);
  const chunked = new Float32Array(input.length);
  const size = 128;                      // AudioWorklet 的區塊大小
  for (let off = 0; off < input.length; off += size) {
    const n = Math.min(size, input.length - off);
    const inL = input.subarray(off, off + n);
    const outL = new Float32Array(n);
    const outR = new Float32Array(n);
    core.process([inL, inL], [outL, outR], 3);
    chunked.set(outL, off);
  }
  for (let i = 0; i < input.length; i++) {
    assert.ok(Math.abs(whole[i] - chunked[i]) < 1e-6, `第 ${i} 個樣本 ${whole[i]} vs ${chunked[i]}`);
  }
});
