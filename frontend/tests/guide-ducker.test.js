/**
 * 導唱自動 ducking 的前端單元測試（node --test，不需要瀏覽器也不需要 npm 套件）。
 *
 * 這個功能在真實包廂裡最難驗證：導唱聲音變小的當下沒有人會去確認「它是不是該變小」，
 * 只會覺得「怪怪的」。所以退場條件、回來速度、遲滯區間全部在這裡釘死。
 */
const test = require("node:test");
const assert = require("node:assert/strict");

const {
  GuideDucker,
  ENGAGE_CONFIDENCE,
  RELEASE_CONFIDENCE,
  WARMUP_SECONDS,
} = require("../js/guide-ducker.js");

const FRAME = 1 / 60;

/** 餵 seconds 秒的幀，hitRatio 決定其中多少比例唱準（規律交錯，不用亂數）。 */
function sing(ducker, seconds, hitRatio, hasNote = true) {
  const frames = Math.round(seconds / FRAME);
  let credit = 0;
  for (let i = 0; i < frames; i++) {
    credit += hitRatio;
    const hit = credit >= 1;
    if (hit) credit -= 1;
    ducker.update(FRAME, { hasNote, sang: hasNote && hitRatio > 0, hit: hasNote && hit });
  }
  return ducker.level;
}

test("預設狀態不動導唱：一開始就是全開", () => {
  const d = new GuideDucker();
  assert.equal(d.level, 1.0);
  assert.equal(d.isDucking(), false);
});

test("暖機期間唱得再準也不退場", () => {
  const d = new GuideDucker();
  sing(d, WARMUP_SECONDS - 0.5, 1.0);
  assert.equal(d.engaged, false);
  assert.equal(d.level, 1.0);
});

test("持續唱準：暖機過後導唱退到設定的深度", () => {
  const d = new GuideDucker({ depth: 0.6 });
  sing(d, WARMUP_SECONDS + 12, 1.0);
  assert.equal(d.engaged, true);
  // 1 - depth = 0.4，容許收斂誤差
  assert.ok(Math.abs(d.level - 0.4) < 0.02, `level=${d.level}`);
  assert.equal(d.isDucking(), true);
});

test("退場是漸進的：剛觸發的那一瞬間不會直接掉到底", () => {
  const d = new GuideDucker({ depth: 0.6 });
  sing(d, WARMUP_SECONDS + 0.2, 1.0);
  assert.equal(d.engaged, true);
  assert.ok(d.level > 0.85, `淡出太快：level=${d.level}`);
});

test("開始走音：導唱在一秒內就回到接近全開", () => {
  const d = new GuideDucker({ depth: 0.6 });
  sing(d, WARMUP_SECONDS + 12, 1.0);
  const ducked = d.level;
  assert.ok(ducked < 0.5);

  sing(d, 6, 0.0);            // 整整六秒完全沒唱準
  assert.equal(d.engaged, false);
  const afterMiss = d.level;
  assert.ok(afterMiss > 0.98, `導唱沒回來：level=${afterMiss}`);
});

test("回來比退場快：同樣的時間量，救援的行程遠大於淡出的行程", () => {
  const down = new GuideDucker({ depth: 0.8 });
  sing(down, WARMUP_SECONDS + 12, 1.0);
  const settled = down.level;

  // 從退場狀態開始，只給 0.3 秒的失誤
  sing(down, 3, 0.0);          // 先讓信心度掉破 RELEASE
  const back = down.level;
  assert.ok(back - settled > 0.5, `回升幅度不足：${settled} → ${back}`);
});

test("遲滯：命中率在兩個門檻之間游走時狀態不翻來覆去", () => {
  const d = new GuideDucker({ depth: 0.6 });
  sing(d, WARMUP_SECONDS + 12, 1.0);
  assert.equal(d.engaged, true);

  // 命中率掉到兩個門檻中間（0.45）——已經不算穩，但也還沒到要救援
  sing(d, 8, (ENGAGE_CONFIDENCE + RELEASE_CONFIDENCE) / 2);
  assert.equal(d.engaged, true, "落在遲滯區間卻改變了狀態");
});

test("間奏（沒有導唱音符）不改變狀態，也不算進統計", () => {
  const d = new GuideDucker({ depth: 0.6 });
  sing(d, WARMUP_SECONDS + 12, 1.0);
  const before = { level: d.level, notes: d.noteSeconds };

  sing(d, 20, 0.0, /* hasNote */ false);
  assert.equal(d.engaged, true, "間奏把導唱叫回來了");
  assert.ok(Math.abs(d.level - before.level) < 0.01);
  assert.equal(d.noteSeconds, before.notes, "間奏不該累計有效時間");
});

test("關閉時完全不作用", () => {
  const d = new GuideDucker({ enabled: false, depth: 0.9 });
  sing(d, 60, 1.0);
  assert.equal(d.level, 1.0);
  assert.equal(d.isDucking(), false);
});

test("深度 0 等於停用，深度會夾在 0.95 以內（不讓導唱整個消失）", () => {
  const zero = new GuideDucker({ depth: 0 });
  sing(zero, WARMUP_SECONDS + 12, 1.0);
  assert.equal(zero.level, 1.0);

  const deep = new GuideDucker({ depth: 5 });
  assert.equal(deep.depth, 0.95);
  sing(deep, WARMUP_SECONDS + 20, 1.0);
  assert.ok(deep.level >= 0.05, `導唱被完全消音：level=${deep.level}`);
});

test("唱到一半改深度不會讓導唱跳回全開", () => {
  const d = new GuideDucker({ depth: 0.4 });
  sing(d, WARMUP_SECONDS + 12, 1.0);
  const before = d.level;
  d.configure({ depth: 0.8 });
  assert.equal(d.engaged, true);
  assert.equal(d.level, before, "改設定不該直接動當下的音量");
  sing(d, 12, 1.0);
  assert.ok(d.level < before, "改深之後應該繼續往下退");
});

test("從關到開會重新暖機，不沿用上一輪的信心度", () => {
  const d = new GuideDucker({ depth: 0.6 });
  sing(d, WARMUP_SECONDS + 12, 1.0);
  d.configure({ enabled: false });
  assert.equal(d.level, 1.0);
  d.configure({ enabled: true });
  assert.equal(d.noteSeconds, 0);
  sing(d, 1.0, 1.0);
  assert.equal(d.engaged, false, "重開之後沒有重新暖機");
});

test("reset() 清乾淨，換歌不會沿用上一首的狀態", () => {
  const d = new GuideDucker({ depth: 0.6 });
  sing(d, WARMUP_SECONDS + 12, 1.0);
  d.reset();
  assert.equal(d.level, 1.0);
  assert.equal(d.confidence, 0);
  assert.equal(d.engaged, false);
  assert.equal(d.noteSeconds, 0);
  assert.equal(d.duckedSeconds, 0);
});

test("statistics：唱得夠久才給獨立度，短歌回傳 null", () => {
  const short = new GuideDucker({ depth: 0.6 });
  sing(short, 10, 1.0);
  assert.equal(short.summary().independence, null);

  const full = new GuideDucker({ depth: 0.6 });
  sing(full, 90, 1.0);
  const s = full.summary();
  assert.ok(s.independence > 0.7, `獨立度偏低：${s.independence}`);
  assert.ok(s.ducked_seconds <= s.note_seconds);
});

test("更新頻率不影響行為：30fps 與 120fps 收斂到同一個位置", () => {
  function run(fps) {
    const d = new GuideDucker({ depth: 0.6 });
    const dt = 1 / fps;
    for (let i = 0; i < Math.round(20 * fps); i++) {
      d.update(dt, { hasNote: true, sang: true, hit: true });
    }
    return d.level;
  }
  assert.ok(Math.abs(run(30) - run(120)) < 0.01);
});

test("分頁切回來的巨大 dt 會被夾住，不會一步跳到底", () => {
  const d = new GuideDucker({ depth: 0.6 });
  sing(d, WARMUP_SECONDS + 12, 1.0);
  const before = d.level;
  d.update(30, { hasNote: true, sang: false, hit: false });   // 分頁睡了 30 秒
  assert.ok(d.level - before < 0.9, "單一幀的行程過大");
  assert.ok(d.noteSeconds < WARMUP_SECONDS + 20, "有效時間被灌了 30 秒");
});

test("非法輸入不會讓狀態變成 NaN", () => {
  const d = new GuideDucker({ depth: 0.6 });
  d.update(NaN, { hasNote: true, hit: true });
  d.update(FRAME, null);
  d.update(undefined, undefined);
  assert.ok(Number.isFinite(d.level));
  assert.ok(Number.isFinite(d.confidence));
});
