/**
 * 智能修音純邏輯的前端單元測試（node --test）。
 *
 * 這一層錯掉的後果全部發生在**使用者自己的聲音**上，而那是整台機器裡
 * 最難被回報清楚的一類問題（「我的聲音怪怪的」查不到任何線索）。所以這裡守四件事：
 *
 *   1. 目標音是導唱音符，而且**沒有導唱音符就不修**（否則會把走音修得更有自信）。
 *   2. 差太多就放手（那不是走音，是沒在唱這一句）。
 *   3. 修正量是**滑過去**的，而且滑的速度就是「抖音活不活得下來」那個旋鈕。
 *   4. 放手一定比修音慢（放手太快＝在字的尾巴上製造一個反向跳音）。
 */
const test = require("node:test");
const assert = require("node:assert/strict");

const {
  PitchFixer,
  PITCH_FIX_STRENGTHS,
  PITCH_FIX_STRENGTH_CHOICES,
  DEFAULT_STRENGTH,
  CAPTURE_CENTS,
  MAX_SHIFT_SEMITONES,
  RELEASE_MS,
  SHIFT_EPSILON,
  strengthSpec,
  clampStrength,
  centsError,
  inCaptureRange,
  targetShift,
  glideFactor,
} = require("../js/pitch-fix.js");

/** 一幀「唱在 userMidi 上、導唱音符是 noteMidi」的判定（pitch-engine.tick 的形狀）。 */
function frameAt(userMidi, noteMidi) {
  return { sang: userMidi > 0, hasNote: noteMidi > 0, userMidi, noteMidi };
}

/** 餵 n 幀同樣的輸入，回傳最後一次的結果（修正量是滑過去的，一幀看不出收斂）。 */
function run(fixer, frame, frames, dtMs = 16) {
  let out = null;
  for (let i = 0; i < frames; i++) out = fixer.update(dtMs, frame);
  return out;
}

// --- 強度表 ---

test("三段強度都在，而且照修得越強滑得越快排列", () => {
  assert.deepEqual(PITCH_FIX_STRENGTH_CHOICES, ["light", "medium", "strong"]);
  const [light, medium, strong] = PITCH_FIX_STRENGTH_CHOICES.map(strengthSpec);
  assert.ok(light.ratio < medium.ratio && medium.ratio < strong.ratio);
  // 修得越強，滑行的時間常數越短 —— 這一條反過來的話，「強」會變成
  // 「修得多但慢吞吞」，使用者按下去只會覺得三段都差不多
  assert.ok(light.glideMs > medium.glideMs && medium.glideMs > strong.glideMs);
  assert.equal(strong.ratio, 1.0);
});

test("認不得的強度回預設值，不是 null 也不是丟例外", () => {
  // "off" 是最可能出現的錯值：上一版如果把開關做成四選一，設定檔裡會留著它
  assert.equal(clampStrength("off"), DEFAULT_STRENGTH);
  assert.equal(clampStrength(""), DEFAULT_STRENGTH);
  assert.equal(clampStrength(undefined), DEFAULT_STRENGTH);
  assert.equal(clampStrength(null), DEFAULT_STRENGTH);
  assert.equal(clampStrength(7), DEFAULT_STRENGTH);
  assert.equal(clampStrength("light"), "light");
  assert.equal(strengthSpec("nope").id, DEFAULT_STRENGTH);
});

test("預設強度是「中」—— 第一次按下去的人該聽到自己的歌聲，不是修音的味道", () => {
  assert.equal(DEFAULT_STRENGTH, "medium");
  assert.ok(PITCH_FIX_STRENGTHS[DEFAULT_STRENGTH].ratio < 1.0);
});

// --- 誤差與捕捉範圍 ---

test("誤差的正負：唱低了要往上修", () => {
  // 導唱音符 60，人唱 59.5 → 低了半個半音 → 要往上修 +50 cent
  assert.equal(centsError(59.5, 60), 50);
  assert.equal(centsError(60.5, 60), -50);
  assert.equal(centsError(60, 60), 0);
});

test("讀不出音高回 null，不是 0 —— 0 的意思是「唱得剛好」", () => {
  assert.equal(centsError(0, 60), null);
  assert.equal(centsError(60, 0), null);
  assert.equal(centsError(NaN, 60), null);
  assert.equal(centsError(undefined, 60), null);
  // null 一律當成不在捕捉範圍內（沒有訊號的時候修音不該作用）
  assert.equal(inCaptureRange(null), false);
});

test("捕捉範圍的邊界：剛好在界上要收，超過一點就放手", () => {
  assert.equal(inCaptureRange(CAPTURE_CENTS), true);
  assert.equal(inCaptureRange(-CAPTURE_CENTS), true);
  assert.equal(inCaptureRange(CAPTURE_CENTS + 0.1), false);
  assert.equal(inCaptureRange(-CAPTURE_CENTS - 0.1), false);
});

test("捕捉範圍比一個半音寬 —— 起音沒抓到的人半秒之後就上來了", () => {
  // 100 cent（一個半音）太窄：唱低一個全音常常只是起音沒抓到，
  // 窄到把他排除掉等於整句都不修
  assert.ok(CAPTURE_CENTS > 100);
  // 200 cent 太寬：會開始把「唱錯音」一路拖到正確的音上，而那個拖行很明顯
  assert.ok(CAPTURE_CENTS < 200);
});

// --- 這一幀想修多少 ---

test("修正量 = 誤差 × 比例", () => {
  // 唱低 100 cent，強（比例 1.0）→ 整整修一個半音
  assert.equal(targetShift(100, "strong"), 1);
  // 同一個誤差，輕（0.35）→ 只拉回三分之一多一點
  assert.ok(Math.abs(targetShift(100, "light") - 0.35) < 1e-9);
  // 唱高了往下修
  assert.ok(targetShift(-100, "strong") < 0);
});

test("捕捉範圍外一律 0（不管哪一段強度）", () => {
  for (const id of PITCH_FIX_STRENGTH_CHOICES) {
    assert.equal(targetShift(CAPTURE_CENTS + 1, id), 0);
    assert.equal(targetShift(-500, id), 0);
    assert.equal(targetShift(null, id), 0);
  }
});

test("修正量永遠不超過硬上限 —— 超過兩個半音那個聲音就不像本人了", () => {
  // 捕捉範圍已經保證算不出超過 1.5 半音；這一條守的是「以後有人把範圍開大」
  assert.ok(MAX_SHIFT_SEMITONES * 100 >= CAPTURE_CENTS);
  for (const id of PITCH_FIX_STRENGTH_CHOICES) {
    assert.ok(Math.abs(targetShift(CAPTURE_CENTS, id)) <= MAX_SHIFT_SEMITONES);
  }
});

// --- 滑行 ---

test("幀距為 0 時一步都不走（掉幀與暫停不該讓修音跳一下）", () => {
  assert.equal(glideFactor(0, 90), 0);
  assert.equal(glideFactor(-5, 90), 0);
});

test("一個時間常數走完 63%，而且時間常數越短走得越快", () => {
  assert.ok(Math.abs(glideFactor(90, 90) - (1 - Math.exp(-1))) < 1e-9);
  assert.ok(glideFactor(16, 45) > glideFactor(16, 150));
});

test("掉幀不會讓修音變慢 —— 一大步要等於很多小步", () => {
  // 這正是用時間常數而不是固定比例的理由：分頁在背景時幀距會掉到一秒一幀
  const once = glideFactor(160, 90);
  let step = 0;
  for (let i = 0; i < 10; i++) step += (1 - step) * glideFactor(16, 90);
  assert.ok(Math.abs(once - step) < 1e-6);
});

// --- PitchFixer：什麼時候不修 ---

test("關著的時候永遠是 0，而且理由說得出來", () => {
  const fixer = new PitchFixer({ enabled: false });
  const out = run(fixer, frameAt(59, 60), 30);
  assert.equal(out.shift, 0);
  assert.equal(out.engaged, false);
  assert.equal(out.reason, "off");
  assert.equal(fixer.describe(), null);
});

test("沒人唱就不修（前奏、間奏、大家在聊天）", () => {
  const fixer = new PitchFixer({ enabled: true, strength: "strong" });
  const out = run(fixer, { sang: false, hasNote: true, userMidi: 0, noteMidi: 60 }, 30);
  assert.equal(out.reason, "idle");
  assert.equal(out.shift, 0);
});

test("沒有導唱音符就不修 —— 吸附到最近的半音會把走音修得更有自信", () => {
  const fixer = new PitchFixer({ enabled: true, strength: "strong" });
  // 有在唱（念白、講話、笑聲），但這一段沒有音符可以當目標
  const out = run(fixer, { sang: true, hasNote: false, userMidi: 59.3, noteMidi: 0 }, 30);
  assert.equal(out.reason, "no_note");
  assert.equal(out.shift, 0);
  assert.equal(fixer.statusText(), "這一段沒有導唱音符");
});

test("差太多就放手（唱錯行、整段低八度、麥克風收到的是伴奏）", () => {
  const fixer = new PitchFixer({ enabled: true, strength: "strong" });
  const out = run(fixer, frameAt(48, 60), 30);   // 整整低一個八度
  assert.equal(out.reason, "out_of_range");
  assert.equal(out.shift, 0);
  assert.equal(fixer.statusText(), "差太多，先不修");
});

// --- PitchFixer：真的在修的時候 ---

test("唱低了往上修，而且收斂到「誤差 × 比例」", () => {
  const fixer = new PitchFixer({ enabled: true, strength: "strong" });
  const out = run(fixer, frameAt(59.5, 60), 120);   // 低 50 cent
  assert.equal(out.reason, "on");
  assert.equal(out.engaged, true);
  assert.ok(out.shift > 0, "唱低了要往上修");
  assert.ok(Math.abs(out.shift - 0.5) < 0.01, `收斂值應該接近 0.5，實際 ${out.shift}`);
});

test("唱高了往下修", () => {
  const fixer = new PitchFixer({ enabled: true, strength: "strong" });
  const out = run(fixer, frameAt(60.5, 60), 120);
  assert.ok(out.shift < 0);
});

test("「輕」只拉回一部分 —— 比例小於 1 是為了留下這個人的味道", () => {
  const light = new PitchFixer({ enabled: true, strength: "light" });
  const strong = new PitchFixer({ enabled: true, strength: "strong" });
  const a = run(light, frameAt(59.5, 60), 200);
  const b = run(strong, frameAt(59.5, 60), 200);
  assert.ok(a.shift < b.shift);
  assert.ok(Math.abs(a.shift - 0.5 * PITCH_FIX_STRENGTHS.light.ratio) < 0.01);
});

test("修正量是滑過去的，第一幀不會直接到位", () => {
  const fixer = new PitchFixer({ enabled: true, strength: "strong" });
  const first = fixer.update(16, frameAt(59.5, 60));
  assert.ok(first.shift > 0);
  assert.ok(first.shift < 0.5 * 0.5, `第一幀就修掉一半以上＝瞬間到位，實際 ${first.shift}`);
});

test("越強滑得越快（同樣的幀數走得更接近目標）", () => {
  const light = new PitchFixer({ enabled: true, strength: "light" });
  const strong = new PitchFixer({ enabled: true, strength: "strong" });
  const frame = frameAt(59.5, 60);
  const a = run(light, frame, 3);
  const b = run(strong, frame, 3);
  // 比的是「走完自己目標的幾成」，不是絕對值（兩段的目標本來就不一樣）
  const aProgress = a.shift / (0.5 * PITCH_FIX_STRENGTHS.light.ratio);
  const bProgress = b.shift / (0.5 * PITCH_FIX_STRENGTHS.strong.ratio);
  assert.ok(bProgress > aProgress);
});

test("抖音穿得過去：輕比強留下更多抖動", () => {
  // 6Hz、±40 cent 的抖音。修音如果完全跟上，輸出就是一條死平的線 ——
  // 那正是「機器人聲」的由來，而抖音是人聲最值錢的東西之一。
  function residual(strength) {
    const fixer = new PitchFixer({ enabled: true, strength });
    const dtMs = 16;
    let maxCorr = 0;
    let minCorr = 0;
    for (let i = 0; i < 400; i++) {
      const t = (i * dtMs) / 1000;
      const vibrato = 0.4 * Math.sin(2 * Math.PI * 6 * t);   // 半音
      const out = fixer.update(dtMs, frameAt(60 + vibrato, 60));
      if (i > 150) {   // 等它進入穩態
        maxCorr = Math.max(maxCorr, out.shift);
        minCorr = Math.min(minCorr, out.shift);
      }
    }
    return maxCorr - minCorr;   // 修正量跟著抖音擺動的幅度
  }
  // 擺動越小＝抖音被修掉得越少
  assert.ok(residual("light") < residual("strong"),
            "輕的修音應該追不上抖音，所以抖音活得下來");
});

// --- 放手 ---

test("放手是滑回去的，不是瞬間歸零", () => {
  const fixer = new PitchFixer({ enabled: true, strength: "strong" });
  run(fixer, frameAt(59.5, 60), 120);
  const before = fixer.shift;
  // 下一幀人停止唱（換氣）
  const out = fixer.update(16, { sang: false, hasNote: true, userMidi: 0, noteMidi: 60 });
  assert.ok(out.shift > 0, "瞬間歸零等於在字的尾巴上製造一個反向跳音");
  assert.ok(out.shift < before);
});

test("放手一定比修音慢", () => {
  assert.ok(RELEASE_MS > Math.max(...PITCH_FIX_STRENGTH_CHOICES.map((id) => strengthSpec(id).glideMs)));
});

test("放手最後會真的到 0", () => {
  const fixer = new PitchFixer({ enabled: true, strength: "strong" });
  run(fixer, frameAt(59.5, 60), 120);
  const out = run(fixer, { sang: false, hasNote: true, userMidi: 0, noteMidi: 60 }, 200);
  assert.equal(out.shift, 0);
  assert.equal(out.engaged, false);
});

test("小於 1 cent 就當成 0 —— 不要一直叫醒音訊執行緒", () => {
  assert.ok(SHIFT_EPSILON <= 0.01);
  const fixer = new PitchFixer({ enabled: true, strength: "strong" });
  const out = run(fixer, frameAt(60.001, 60), 60);
  assert.equal(out.shift, 0);
  assert.equal(out.engaged, false);
  assert.equal(out.reason, "on");           // 有在唱、也有音符，只是不需要修
  assert.equal(fixer.statusText(), "已經很準");
});

// --- 開關與重置 ---

test("關掉的那一刻修正量就清掉，不等它滑回去", () => {
  const fixer = new PitchFixer({ enabled: true, strength: "strong" });
  run(fixer, frameAt(59.5, 60), 120);
  assert.ok(fixer.shift > 0);
  fixer.configure({ enabled: false });
  assert.equal(fixer.shift, 0);
});

test("release 放掉修正量但留下統計；reset 兩個都清", () => {
  const fixer = new PitchFixer({ enabled: true, strength: "strong" });
  run(fixer, frameAt(59.5, 60), 120);
  const framesBefore = fixer.stats().frames;
  assert.ok(framesBefore > 0);

  fixer.release();
  assert.equal(fixer.shift, 0);
  assert.equal(fixer.stats().frames, framesBefore, "中途關掉再打開，統計不該只算後半首");

  fixer.reset();
  assert.equal(fixer.stats().frames, 0);
  assert.equal(fixer.stats().engagedFrames, 0);
});

// --- 統計與文字 ---

test("統計帶號：一直往上修代表這首歌對他太高", () => {
  const fixer = new PitchFixer({ enabled: true, strength: "strong" });
  run(fixer, frameAt(59.5, 60), 200);
  const stats = fixer.stats();
  assert.ok(stats.meanCents > 0, "整首都唱低 → 平均修正量為正");
  assert.ok(stats.engagedRatio > 0.8);
  assert.ok(stats.maxCents > 0);
});

test("沒有導唱音符的時間不進分母 —— 不然前奏越長比例看起來越低", () => {
  const fixer = new PitchFixer({ enabled: true, strength: "strong" });
  run(fixer, { sang: false, hasNote: false, userMidi: 0, noteMidi: 0 }, 300);   // 前奏
  assert.equal(fixer.stats().frames, 0);
  run(fixer, frameAt(59.5, 60), 100);
  assert.equal(fixer.stats().frames, 100);
});

test("徽章文字：開著就有，而且不作用時講得出原因", () => {
  const fixer = new PitchFixer({ enabled: true, strength: "medium" });
  assert.equal(fixer.describe(), "修音 中");
  assert.equal(fixer.statusText(), "等你開口");
  run(fixer, frameAt(59.5, 60), 60);
  assert.equal(fixer.statusText(), "修正中");
});

test("唱畢那一行講得出「機器幫了多少」", () => {
  const fixer = new PitchFixer({ enabled: true, strength: "strong" });
  assert.equal(fixer.summary(), "", "沒有任何一幀有音符時不要印一行沒有內容的統計");
  run(fixer, frameAt(59.5, 60), 200);
  const line = fixer.summary();
  assert.match(line, /修音：/);
  assert.match(line, /cent/);
});

test("整首都很準的時候說得出「沒有需要修的地方」", () => {
  const fixer = new PitchFixer({ enabled: true, strength: "strong" });
  run(fixer, frameAt(60, 60), 100);
  assert.match(fixer.summary(), /沒有需要修的地方/);
});

// --- 壞資料 ---

test("亂七八糟的幀不會讓修音爆掉或吐出 NaN", () => {
  const fixer = new PitchFixer({ enabled: true, strength: "strong" });
  for (const bad of [null, undefined, {}, { sang: true, hasNote: true },
                     { sang: true, hasNote: true, userMidi: "x", noteMidi: "y" }]) {
    const out = fixer.update(16, bad);
    assert.ok(Number.isFinite(out.shift));
    assert.equal(out.shift, 0);
  }
});
