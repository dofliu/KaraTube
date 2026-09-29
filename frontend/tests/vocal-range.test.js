/**
 * 音域採集的前端單元測試（node --test）。
 *
 * 這一支收的東西會**留在檔案裡**，而且之後每一首歌的建議 Key 都讀它 ——
 * 所以測試的重心在「哪些幀不可以被收進來」：轉音路徑上的音、
 * 喇叭漏進麥克風的原唱、對唱的串音、界外的偵測結果。
 * 收少了只是晚幾首才建檔（使用者看得到「再唱 N 首」），
 * 收錯了是每一首歌的建議都偏掉，而且沒有任何地方看得出來。
 */
const test = require("node:test");
const assert = require("node:assert/strict");

const {
  VocalRangeCollector,
  shouldShowAdvice,
  adviceHasApply,
  SUSTAIN_FRAMES,
  SUSTAIN_SPAN,
  MIN_RMS,
  RANGE_MIN_MIDI,
  RANGE_MAX_MIDI,
} = require("../js/vocal-range.js");

/** 餵 n 幀同一個音（預設電平夠大、算得了分）。 */
function sing(collector, midi, frames, extra = {}) {
  for (let i = 0; i < frames; i++) {
    collector.push(Object.assign({ sang: true, credited: true, userMidi: midi, rms: 0.2 }, extra));
  }
}

test("唱住一個音：到門檻時把累積的幀一次補記進去", () => {
  const c = new VocalRangeCollector();
  sing(c, 60, SUSTAIN_FRAMES);
  assert.equal(c.frames, SUSTAIN_FRAMES, "第 8 幀應該一次補記 8 幀");
  assert.equal(c.bins[60], SUSTAIN_FRAMES);
});

test("到門檻之前一幀都不記（滑過去的音不算唱過）", () => {
  const c = new VocalRangeCollector();
  sing(c, 60, SUSTAIN_FRAMES - 1);
  assert.equal(c.frames, 0);
  assert.deepEqual(c.bins, {});
});

test("轉音：一路滑上去、每一格都只停幾幀，一格都不該被記住", () => {
  const c = new VocalRangeCollector();
  // 從 C4 滑到 G4，每個半音只停 3 幀
  for (let midi = 60; midi <= 67; midi++) sing(c, midi, 3);
  assert.equal(c.frames, 0, "轉音路徑上的音不是唱住的音");
});

test("穩住之後每一幀都記", () => {
  const c = new VocalRangeCollector();
  sing(c, 60, SUSTAIN_FRAMES + 20);
  assert.equal(c.frames, SUSTAIN_FRAMES + 20);
});

test("抖音（±0.5 半音來回）算同一個音，不會把 run 打斷", () => {
  const c = new VocalRangeCollector();
  for (let i = 0; i < 60; i++) {
    sing(c, 60 + (i % 2 === 0 ? 0.5 : -0.5), 1);
  }
  assert.equal(c.frames, 60, "抖音是長音的一部分，必須整段收進來");
  // 記在哪一格不重要（60 或 60±1 都行），重要的是沒有被丟掉
  const spread = Object.keys(c.bins).map(Number).sort((a, b) => a - b);
  assert.ok(spread[spread.length - 1] - spread[0] <= 1, "抖音不該散到兩個半音之外");
});

test("換音（擺幅超過門檻）會重新起算，新的音也要自己撐滿門檻", () => {
  const c = new VocalRangeCollector();
  sing(c, 60, SUSTAIN_FRAMES + 5);   // 13 幀
  sing(c, 67, SUSTAIN_FRAMES - 1);   // 7 幀，不到門檻
  assert.equal(c.bins[60], SUSTAIN_FRAMES + 5);
  assert.equal(c.bins[67], undefined, "換到新的音之後要重新撐滿門檻");
});

test("擺幅的邊界：剛好到門檻算同一段，超過就斷", () => {
  const inside = new VocalRangeCollector();
  sing(inside, 60, 4);
  sing(inside, 60 + SUSTAIN_SPAN, 4);
  assert.equal(inside.frames, SUSTAIN_FRAMES, "剛好到門檻：兩段合起來撐滿門檻");

  const outside = new VocalRangeCollector();
  sing(outside, 60, 4);
  sing(outside, 60 + SUSTAIN_SPAN + 0.01, 4);
  assert.equal(outside.frames, 0, "超過擺幅門檻：兩段各自都不到門檻");
});

test("擺幅是看整段，不是看相鄰兩幀 —— 慢慢滑上去的音一樣擋得住", () => {
  const c = new VocalRangeCollector();
  // 每幀只升 0.2 個半音（相鄰兩幀永遠在容忍內），連滑 40 幀＝八個半音
  for (let i = 0; i < 40; i++) sing(c, 60 + i * 0.2, 1);
  // 會有幾段各自撐滿 8 幀（1.5 個半音 ÷ 0.2 ≈ 8 幀），但每一段都很短，
  // 重點是它不會被當成「唱住了一個音」整段四十幀記在同一格
  const perBin = Object.values(c.bins);
  assert.ok(Math.max(...perBin, 0) < 12, "滑音不該在任何一格堆出長音等級的幀數");
});

test("電平太小的幀不收（喇叭裡的原唱漏進麥克風）", () => {
  const c = new VocalRangeCollector();
  sing(c, 72, 100, { rms: MIN_RMS - 0.001 });
  assert.equal(c.frames, 0, "房間裡的回音不是這個人唱的");
});

test("電平剛好到門檻就收", () => {
  const c = new VocalRangeCollector();
  sing(c, 72, SUSTAIN_FRAMES, { rms: MIN_RMS });
  assert.equal(c.frames, SUSTAIN_FRAMES);
});

test("對唱串音（credited=false）不收 —— 那是另一個人的聲音", () => {
  const c = new VocalRangeCollector();
  sing(c, 64, 100, { credited: false });
  assert.equal(c.frames, 0);
});

test("沒偵測到音高的幀（sang=false / midi=0）不收，而且會打斷 run", () => {
  const c = new VocalRangeCollector();
  sing(c, 60, 5);
  c.push({ sang: false, credited: true, userMidi: 0, rms: 0.2 });
  sing(c, 60, 5);
  assert.equal(c.frames, 0, "中間斷掉就要重新撐滿門檻");
});

test("界外的偵測結果丟掉（八度誤判掉到 C1、或偵測到 D7）", () => {
  const c = new VocalRangeCollector();
  sing(c, RANGE_MIN_MIDI - 1, 100);
  sing(c, RANGE_MAX_MIDI + 1, 100);
  assert.equal(c.frames, 0);
});

test("界內的兩端要收得到", () => {
  const c = new VocalRangeCollector();
  sing(c, RANGE_MIN_MIDI, SUSTAIN_FRAMES);
  sing(c, RANGE_MAX_MIDI, SUSTAIN_FRAMES);
  assert.equal(c.bins[RANGE_MIN_MIDI], SUSTAIN_FRAMES);
  assert.equal(c.bins[RANGE_MAX_MIDI], SUSTAIN_FRAMES);
});

test("payload：收太少就回 null（被切歌的那一次不該佔掉一首的額度）", () => {
  const c = new VocalRangeCollector();
  sing(c, 60, 50);
  assert.equal(c.payload(), null);
});

test("payload：收夠了就給直方圖與總幀數", () => {
  const c = new VocalRangeCollector();
  sing(c, 60, 400);
  const p = c.payload();
  assert.ok(p, "400 幀應該送得出去");
  assert.equal(p.frames, 400);
  assert.equal(p.bins[60], 400);
});

test("payload 的直方圖是複本，之後再唱不會改到已經送出去的那一份", () => {
  const c = new VocalRangeCollector();
  sing(c, 60, 400);
  const p = c.payload();
  sing(c, 67, 400);
  assert.equal(p.bins[67], undefined);
});

test("reset 之後從零開始（換一首歌／換一個人）", () => {
  const c = new VocalRangeCollector();
  sing(c, 60, 400);
  c.reset();
  assert.equal(c.frames, 0);
  assert.deepEqual(c.bins, {});
  // 而且 run 狀態也要清掉：不清的話新的一首第一幀就直接被記進舊的那一段
  sing(c, 60, SUSTAIN_FRAMES - 1);
  assert.equal(c.frames, 0);
});

test("shouldShowAdvice：沒得算的那一種整行不出現，其餘都出現", () => {
  assert.equal(shouldShowAdvice({ status: "no_demand" }), false);
  assert.equal(shouldShowAdvice({ status: "no_profile" }), true);
  assert.equal(shouldShowAdvice({ status: "fit" }), true);
  assert.equal(shouldShowAdvice({ status: "advice" }), true);
  assert.equal(shouldShowAdvice({ status: "out_of_range" }), true);
  assert.equal(shouldShowAdvice(null), false);
  assert.equal(shouldShowAdvice({}), false);
});

test("adviceHasApply：只有真的要移調才給按鍵", () => {
  assert.equal(adviceHasApply({ status: "advice", shift: -2 }), true);
  assert.equal(adviceHasApply({ status: "advice", shift: 0 }), false);
  assert.equal(adviceHasApply({ status: "fit", shift: 0 }), false);
  assert.equal(adviceHasApply({ status: "out_of_range", shift: 0 }), false);
  assert.equal(adviceHasApply(null), false);
});
