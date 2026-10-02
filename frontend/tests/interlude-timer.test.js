/**
 * 間奏倒數的前端單元測試（node --test）。
 *
 * 這個功能的錯誤有一個共同點：**在包廂裡看起來都像「機器壞了」**。
 * 倒數閃一下就不見、數到 0 之後又數一次、跳過之後第一句已經唱到一半、
 * 尾奏數到 0 卻沒有東西要唱 —— 沒有一個會丟例外，也沒有一個查得到原因。
 * 所以門檻、交棒點、跳過目標、尾奏的特例全部在這裡釘死。
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const {
  InterludePlan,
  interludeClampMinGap,
  INTERLUDE_LABELS,
  INTERLUDE_MIN_GAP_SECONDS,
  INTERLUDE_MIN_GAP_FLOOR,
  INTERLUDE_MIN_GAP_CEIL,
  INTERLUDE_LEAD_IN_SECONDS,
  INTERLUDE_MIN_VISIBLE_SECONDS,
  INTERLUDE_SKIP_MIN_GAIN_SECONDS,
} = require("../js/interlude-timer.js");

/** 造一份歌詞：每行長 len 秒，第 i 行從 starts[i] 開始。 */
function lines(starts, len = 4) {
  return starts.map((s, i) => ({ line_idx: i, start: s, end: s + len, text: `line${i}` }));
}

// --- 空檔的判定 ---

test("長前奏算一段 intro，從 0 數到第一句", () => {
  const plan = InterludePlan.fromLyrics(lines([30, 36, 42]), 50);
  assert.equal(plan.gaps.length, 1);
  assert.deepEqual(
    { kind: plan.gaps[0].kind, start: plan.gaps[0].start, end: plan.gaps[0].end },
    { kind: "intro", start: 0, end: 30 });
});

test("短前奏不算（樂句之間的空檔不是間奏）", () => {
  const plan = InterludePlan.fromLyrics(lines([6, 14, 22]), 30);
  assert.equal(plan.gaps.length, 0);
});

test("句與句之間超過門檻才算 interlude", () => {
  // 0-4 唱、8-12 唱（空 4 秒，不算）、32-36 唱（空 20 秒，算）
  const plan = InterludePlan.fromLyrics(lines([0, 8, 32]), 40);
  const kinds = plan.gaps.map(g => g.kind);
  assert.deepEqual(kinds, ["interlude"]);
  assert.equal(plan.gaps[0].start, 12);
  assert.equal(plan.gaps[0].end, 32);
});

test("最後一句唱完到歌曲結束算 outro", () => {
  const plan = InterludePlan.fromLyrics(lines([0, 8]), 40);
  assert.deepEqual(plan.gaps.map(g => g.kind), ["outro"]);
  assert.equal(plan.gaps[0].start, 12);
  assert.equal(plan.gaps[0].end, 40);
});

test("拿不到歌曲長度時只是沒有尾奏，前奏與間奏照算", () => {
  const plan = InterludePlan.fromLyrics(lines([30, 80]), 0);
  assert.deepEqual(plan.gaps.map(g => g.kind), ["intro", "interlude"]);
  const infinite = InterludePlan.fromLyrics(lines([30, 80]), Infinity);
  assert.deepEqual(infinite.gaps.map(g => g.kind), ["intro", "interlude"]);
});

test("整首沒有歌詞不是一段長間奏，是一首沒有歌詞的歌", () => {
  assert.equal(InterludePlan.fromLyrics([], 240).gaps.length, 0);
  assert.equal(InterludePlan.fromLyrics(null, 240).gaps.length, 0);
});

test("重疊的歌詞行不會憑空長出間奏", () => {
  // 第二行比第一行早結束（合唱／翻譯行很常見）。照「前一行的 end」算的話，
  // 20 → 24 會被當成一段 16 秒的空檔，而那段時間其實一直有人在唱。
  const overlapping = [
    { start: 0, end: 40 },
    { start: 2, end: 20 },
    { start: 24, end: 30 },
  ];
  assert.equal(InterludePlan.fromLyrics(overlapping, 45).gaps.length, 0);
});

test("沒排序、壞掉的時間欄位一律正規化掉，不產生負長度的空檔", () => {
  const messy = [
    { start: 40, end: 44 },
    { start: "8", end: "12" },
    { start: NaN, end: 3 },
    { start: -5, end: 1 },
    null,
    { start: 20, end: 10 },   // end < start：當成零長度的一行
  ];
  const plan = InterludePlan.fromLyrics(messy, 80);
  for (const gap of plan.gaps) {
    assert.ok(gap.end > gap.start, `空檔長度必須為正：${JSON.stringify(gap)}`);
  }
});

test("門檻可調，而且壞值退回預設而不是關掉整個功能", () => {
  const long = InterludePlan.fromLyrics(lines([0, 20]), 60, { minGap: 25 });
  assert.equal(long.gaps.filter(g => g.kind === "interlude").length, 0);
  assert.equal(interludeClampMinGap("abc"), INTERLUDE_MIN_GAP_SECONDS);
  assert.equal(interludeClampMinGap(undefined), INTERLUDE_MIN_GAP_SECONDS);
  assert.equal(interludeClampMinGap(0), INTERLUDE_MIN_GAP_FLOOR);
  assert.equal(interludeClampMinGap(999), INTERLUDE_MIN_GAP_CEIL);
});

// --- 畫面上該顯示什麼 ---

test("空檔外什麼都不顯示", () => {
  const plan = InterludePlan.fromLyrics(lines([30, 36]), 100);
  assert.equal(plan.at(31), null);     // 正在唱
  assert.equal(plan.at(-1), null);
  assert.equal(plan.at("x"), null);
});

test("倒數數到第一個字，不是數到自己消失", () => {
  const plan = InterludePlan.fromLyrics(lines([30, 36]), 100);
  const view = plan.at(10);
  assert.equal(view.kind, "intro");
  assert.equal(view.label, INTERLUDE_LABELS.intro);
  assert.equal(view.remaining, 20);   // 30 − 10，不是 27 − 10
  assert.equal(view.total, 30);
});

test("剩三秒就交棒給預備光點：畫面上永遠只有一套倒數", () => {
  const plan = InterludePlan.fromLyrics(lines([30, 36]), 100);
  assert.ok(plan.at(30 - INTERLUDE_LEAD_IN_SECONDS - 0.01));
  assert.equal(plan.at(30 - INTERLUDE_LEAD_IN_SECONDS), null);
  assert.equal(plan.at(29.5), null);
});

test("交棒點與 karaoke-renderer 的預備光點視窗必須一致", () => {
  // 兩邊對不起來的後果是「數字消失了但光點還沒亮」（中間一段空白），
  // 或者兩套倒數同時在畫面上各數各的。三個月後改其中一邊的人不會記得另一邊。
  const src = fs.readFileSync(
    path.join(__dirname, "..", "js", "karaoke-renderer.js"), "utf8");
  const m = src.match(/timeUntilStart\s*<=\s*([\d.]+)/);
  assert.ok(m, "找不到 karaoke-renderer.js 的預備光點視窗");
  assert.equal(Number(m[1]), INTERLUDE_LEAD_IN_SECONDS);
});

test("短到只能閃一下的倒數乾脆不顯示（整段一致，不會數到一半才決定）", () => {
  // 空檔 = LEAD_IN + MIN_VISIBLE − 0.5，看得見的窗比門檻短
  const gap = INTERLUDE_LEAD_IN_SECONDS + INTERLUDE_MIN_VISIBLE_SECONDS - 0.5;
  const plan = new InterludePlan([{ kind: "interlude", start: 10, end: 10 + gap, length: gap }]);
  for (let t = 10; t < 10 + gap; t += 0.25) {
    assert.equal(plan.at(t), null, `t=${t} 不該顯示`);
  }
  // 剛好跨過門檻的那一段則整段都看得到
  const ok = INTERLUDE_LEAD_IN_SECONDS + INTERLUDE_MIN_VISIBLE_SECONDS;
  const shown = new InterludePlan([{ kind: "interlude", start: 10, end: 10 + ok, length: ok }]);
  assert.ok(shown.at(10));
  assert.ok(shown.at(10 + INTERLUDE_MIN_VISIBLE_SECONDS - 0.01));
});

test("前奏倒數讓開導唱片頭卡，片頭卡收掉才接手", () => {
  const plan = InterludePlan.fromLyrics(lines([30, 36]), 100);
  assert.equal(plan.at(3, { blockUntil: 8 }), null);
  assert.equal(plan.at(7.99, { blockUntil: 8 }), null);
  assert.ok(plan.at(8, { blockUntil: 8 }));
});

test("片頭卡蓋掉之後只剩兩秒的前奏，整段都不顯示", () => {
  // 25 秒前奏、片頭卡 20 秒 → 看得見的窗只有 25−3−20 = 2 秒
  const plan = InterludePlan.fromLyrics(lines([25, 40]), 100);
  for (let t = 20; t < 22; t += 0.2) {
    assert.equal(plan.at(t, { blockUntil: 20 }), null);
  }
});

test("blockUntil 只管前奏，間奏不受影響", () => {
  const plan = InterludePlan.fromLyrics(lines([0, 40]), 100);
  const view = plan.at(6, { blockUntil: 20 });
  assert.equal(view.kind, "interlude");
});

// --- 尾奏 ---

test("尾奏不倒數也不給跳過", () => {
  const plan = InterludePlan.fromLyrics(lines([0, 8]), 60);
  const view = plan.at(30);
  assert.equal(view.kind, "outro");
  assert.equal(view.label, INTERLUDE_LABELS.outro);
  assert.equal(view.remaining, null);
  assert.equal(view.resumeAt, null);
  assert.equal(view.skippable, false);
});

test("尾奏一路顯示到歌曲結束（不提早三秒收掉）", () => {
  const plan = InterludePlan.fromLyrics(lines([0, 8]), 60);
  assert.ok(plan.at(59.5));
  assert.equal(plan.at(60), null);
});

// --- 跳過 ---

test("跳過的目標是「還剩三秒」，不是第一個字", () => {
  const plan = InterludePlan.fromLyrics(lines([40, 48]), 120);
  const view = plan.at(12);
  assert.equal(view.skippable, true);
  assert.equal(view.resumeAt, 40 - INTERLUDE_LEAD_IN_SECONDS);
});

test("省不到三秒就不給跳（按了只前進半秒等於壞掉的按鈕）", () => {
  const plan = InterludePlan.fromLyrics(lines([40, 48]), 120);
  const gain = INTERLUDE_SKIP_MIN_GAIN_SECONDS;
  assert.equal(plan.at(40 - INTERLUDE_LEAD_IN_SECONDS - gain).skippable, true);
  assert.equal(plan.at(40 - INTERLUDE_LEAD_IN_SECONDS - gain + 0.5).skippable, false);
  // 不給跳不等於不顯示：倒數照常數完
  assert.ok(plan.at(40 - INTERLUDE_LEAD_IN_SECONDS - 0.5));
});

test("關掉跳過時倒數照顯示，只是不給按", () => {
  const plan = InterludePlan.fromLyrics(lines([40, 48]), 120);
  const view = plan.at(12, { skipEnabled: false });
  assert.ok(view);
  assert.equal(view.skippable, false);
});

test("跳過會越過 A-B 循環的 B 點時不給跳（跳完會被拉回來）", () => {
  const plan = InterludePlan.fromLyrics(lines([40, 48]), 120);
  assert.equal(plan.at(12, { loopEnabled: true, loopEnd: 20 }).skippable, false);
  // B 點在空檔之後就沒問題
  assert.equal(plan.at(12, { loopEnabled: true, loopEnd: 60 }).skippable, true);
  // 循環沒開就不看 B 點（上一次練唱留下來的點不該影響這一首）
  assert.equal(plan.at(12, { loopEnabled: false, loopEnd: 20 }).skippable, true);
});

test("progress 從 0 走到 1，而且永遠夾在區間內", () => {
  const plan = InterludePlan.fromLyrics(lines([40, 48]), 120);
  assert.equal(plan.at(0.001).progress < 0.01, true);
  const late = plan.at(40 - INTERLUDE_LEAD_IN_SECONDS - 0.01);
  assert.ok(late.progress > 0.9 && late.progress <= 1);
});

test("三種空檔的名字分得開", () => {
  assert.deepEqual(Object.keys(INTERLUDE_LABELS).sort(), ["interlude", "intro", "outro"]);
  assert.equal(new Set(Object.values(INTERLUDE_LABELS)).size, 3);
});
