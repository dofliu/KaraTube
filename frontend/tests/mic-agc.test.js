/**
 * 麥克風自動增益的前端單元測試（node --test，不需要瀏覽器也不需要 npm 套件）。
 *
 * 這個功能的失效方式全部都是「現場才發現，而且很痛」：
 * 安靜時偷偷加大 → 第一個字爆掉、多人模式當場嘯叫；升得太快 → 句與句之間底噪呼吸；
 * 降得太慢 → 貼麥克風大吼那一下削峰救不回來。所以三條安全規則在這裡逐條釘死。
 */
const test = require("node:test");
const assert = require("node:assert/strict");

const {
  MicAutoGain,
  dbFromRms,
  SILENCE_DB,
  CLIP_GUARD_DB,
  WARMUP_VOICED_SECONDS,
  TOLERANCE_DB,
  IDLE_RELEASE_SECONDS,
} = require("../js/mic-agc.js");

const FRAME = 1 / 60;

/** dBFS 轉線性 RMS（測試用的反函數，讓案例可以直接用 dB 描述）。 */
function rmsFromDb(db) {
  return Math.pow(10, db / 20);
}

/** 餵 seconds 秒、電平固定在 db 的幀。回傳最後的線性增益。 */
function feed(agc, seconds, db, frameSec = FRAME) {
  const frames = Math.max(1, Math.round(seconds / frameSec));
  const rms = db === -Infinity ? 0 : rmsFromDb(db);
  for (let i = 0; i < frames; i++) agc.update(frameSec, { rms });
  return agc.level;
}

test("預設狀態不動麥克風：增益 0 dB", () => {
  const agc = new MicAutoGain();
  assert.equal(agc.gainDb, 0);
  assert.equal(agc.level, 1.0);
  assert.equal(agc.isActive(), false);
});

test("dbFromRms：靜音回 -Infinity，不是某個有限的小數字", () => {
  assert.equal(dbFromRms(0), -Infinity);
  assert.equal(dbFromRms(-1), -Infinity);
  assert.equal(dbFromRms(NaN), -Infinity);
  // 0.1 = -20 dBFS，這是刻度是否正確的定錨點
  assert.ok(Math.abs(dbFromRms(0.1) + 20) < 1e-9);
});

// --- 安全規則 1：沒人唱的時候絕不加增益 ---

test("完全靜音餵一分鐘也不會加任何增益", () => {
  const agc = new MicAutoGain();
  feed(agc, 60, -Infinity);
  assert.equal(agc.gainDb, 0);
  assert.equal(agc.level, 1.0);
});

test("噪音閘門以下的底噪不算人聲，增益不動", () => {
  const agc = new MicAutoGain();
  feed(agc, 30, SILENCE_DB - 3);
  assert.equal(agc.gainDb, 0);
  assert.equal(agc.voicedSeconds, 0);
});

test("關掉功能：不管餵什麼，增益恆為 1", () => {
  const agc = new MicAutoGain({ enabled: false });
  feed(agc, 20, -34);
  assert.equal(agc.level, 1.0);
  assert.equal(agc.gainDb, 0);
});

// --- 暖機與容忍區 ---

test("暖機期間（人聲不足 1.2 秒）不調整", () => {
  const agc = new MicAutoGain();
  feed(agc, WARMUP_VOICED_SECONDS - 0.3, -34);
  assert.equal(agc.gainDb, 0);
});

test("電平已經在目標附近（差不到容忍區）就不要動", () => {
  const agc = new MicAutoGain({ targetDb: -18 });
  feed(agc, 12, -18.4);
  assert.ok(Math.abs(agc.gainDb) < TOLERANCE_DB, `gainDb=${agc.gainDb}`);
});

// --- 主要行為：把不同人拉到同一個目標 ---

test("唱得太小聲：慢慢加到目標（上限之內）", () => {
  const agc = new MicAutoGain({ targetDb: -18, maxBoostDb: 9 });
  feed(agc, 20, -24);           // 差 6 dB，在加成上限內
  assert.ok(Math.abs(agc.gainDb - 6) < 0.6, `gainDb=${agc.gainDb}`);
  assert.equal(agc.isActive(), true);
});

test("唱得太大聲：往下減到目標", () => {
  const agc = new MicAutoGain({ targetDb: -18 });
  feed(agc, 12, -10);           // 需要 -8 dB
  assert.ok(Math.abs(agc.gainDb + 8) < 0.6, `gainDb=${agc.gainDb}`);
});

test("加成有上限：離麥克風太遠不會一路加到底噪也放大", () => {
  const agc = new MicAutoGain({ targetDb: -18, maxBoostDb: 9 });
  feed(agc, 40, -40);           // 理論上要 +22 dB
  assert.ok(Math.abs(agc.gainDb - 9) < 0.2, `gainDb=${agc.gainDb}`);
});

test("削減也有上限", () => {
  const agc = new MicAutoGain({ targetDb: -18, maxCutDb: 12 });
  feed(agc, 20, -1);            // 理論上要 -17 dB
  assert.ok(Math.abs(agc.gainDb + 12) < 0.2, `gainDb=${agc.gainDb}`);
});

// --- 安全規則 2：要降立刻降、要升慢慢升 ---

test("不對稱曲線：0.4 秒內降得比升得多很多", () => {
  const down = new MicAutoGain({ targetDb: -18 });
  feed(down, 3, -18);                    // 先穩在目標
  const before = down.gainDb;
  feed(down, 0.4, -8);                   // 突然大聲 10 dB
  const droppedDb = before - down.gainDb;

  const up = new MicAutoGain({ targetDb: -18 });
  feed(up, 3, -18);
  const upBefore = up.gainDb;
  feed(up, 0.4, -28);                    // 突然小聲 10 dB
  const raisedDb = up.gainDb - upBefore;

  assert.ok(droppedDb > 3, `0.4 秒只降了 ${droppedDb.toFixed(2)} dB，太慢`);
  assert.ok(raisedDb < 1.5, `0.4 秒就升了 ${raisedDb.toFixed(2)} dB，太快`);
  assert.ok(droppedDb > raisedDb * 2, "降的速度必須明顯快於升");
});

test("削峰保護：預估輸出超過保護線時無視暖機立刻降", () => {
  const agc = new MicAutoGain();
  // 一開始就貼著麥克風大吼（-2 dBFS），還在暖機期
  feed(agc, 0.2, CLIP_GUARD_DB + 4);
  assert.ok(agc.gainDb < -2, `gainDb=${agc.gainDb}（削峰保護沒動作）`);
  assert.equal(agc.summary().clip_guarded, true);
});

test("削峰保護線是速度地板，不是「不准再降」的地板", () => {
  // 貼著麥克風吼：保護線只要求「不要削峰」（降到 -6 dBFS 就夠），
  // 但目標電平要求再小 12 dB。兩者衝突時必須聽目標電平的 ——
  // 否則整首歌都會被鎖在剛好不削峰的最大聲，AGC 等於沒作用。
  const agc = new MicAutoGain({ targetDb: -18, maxCutDb: 12 });
  const inputDb = CLIP_GUARD_DB + 4;      // -2 dBFS
  feed(agc, 6, inputDb);
  const outDb = inputDb + agc.gainDb;
  assert.ok(outDb <= CLIP_GUARD_DB, `outDb=${outDb}（還在削峰邊緣）`);
  // 削減上限 12 dB，所以只能降到 -14 dBFS，到不了 -18 —— 這是刻意的天花板
  assert.ok(Math.abs(agc.gainDb + 12) < 0.3, `gainDb=${agc.gainDb}`);
});

// --- 空檔處理 ---

test("短暫的句間空隙不會讓增益跑掉", () => {
  const agc = new MicAutoGain({ targetDb: -18 });
  feed(agc, 20, -24);
  const learned = agc.gainDb;
  feed(agc, 1.5, -Infinity);            // 換句之間停 1.5 秒
  assert.ok(Math.abs(agc.gainDb - learned) < 0.05, "空隙時增益不該移動");
});

test("長時間沒人唱：增益慢慢放回中性，不要一直放大空檔的底噪", () => {
  const agc = new MicAutoGain({ targetDb: -18 });
  feed(agc, 20, -26);
  assert.ok(agc.gainDb > 4);
  feed(agc, IDLE_RELEASE_SECONDS + 1, -Infinity);
  const midway = agc.gainDb;
  assert.ok(midway > 0, "才剛過門檻不該瞬間歸零（那會聽得出來）");
  feed(agc, 30, -Infinity);
  assert.equal(agc.gainDb, 0, "安靜夠久之後要回到中性");
});

// --- 一致性與設定 ---

test("30fps 與 120fps 的結果一致（時間常數式，不是每幀固定量）", () => {
  const slow = new MicAutoGain({ targetDb: -18 });
  const fast = new MicAutoGain({ targetDb: -18 });
  feed(slow, 8, -26, 1 / 30);
  feed(fast, 8, -26, 1 / 120);
  assert.ok(Math.abs(slow.gainDb - fast.gainDb) < 0.15,
    `30fps=${slow.gainDb} vs 120fps=${fast.gainDb}`);
});

test("分頁切回來的超大 dt 被夾住，不會一次跳完", () => {
  const agc = new MicAutoGain({ targetDb: -18 });
  feed(agc, 3, -18);
  const before = agc.gainDb;
  agc.update(30, { rms: rmsFromDb(-30) });   // 分頁凍結 30 秒後回來
  assert.ok(agc.gainDb - before < 2, `一幀就升了 ${(agc.gainDb - before).toFixed(2)} dB`);
});

test("縮小加成上限（切到多人模式）立刻把現有增益夾回新範圍", () => {
  const agc = new MicAutoGain({ targetDb: -18, maxBoostDb: 9 });
  feed(agc, 40, -40);
  assert.ok(agc.gainDb > 8.5);
  agc.configure({ maxBoostDb: 6 });
  assert.ok(Math.abs(agc.gainDb - 6) < 1e-9, `gainDb=${agc.gainDb}`);
  assert.ok(Math.abs(agc.level - Math.pow(10, 6 / 20)) < 1e-9);
});

test("改目標值不重置學到的增益，切開關才重置", () => {
  const agc = new MicAutoGain({ targetDb: -18 });
  feed(agc, 20, -24);
  const learned = agc.gainDb;
  agc.configure({ targetDb: -20 });
  assert.equal(agc.gainDb, learned, "改目標值不該讓麥克風先跳回原始音量");

  agc.configure({ enabled: false });
  assert.equal(agc.gainDb, 0);
  agc.configure({ enabled: true });
  assert.equal(agc.gainDb, 0, "重新打開要從中性重新暖機");
});

test("換歌只清統計，學到的增益留著（同一個人常唱好幾首）", () => {
  const agc = new MicAutoGain({ targetDb: -18 });
  feed(agc, 20, -24);
  const learned = agc.gainDb;
  agc.resetStats();
  assert.equal(agc.gainDb, learned);
  assert.equal(agc.voicedSeconds, 0);
  assert.equal(agc.summary().average_gain_db, null);
});

test("音量表刻度：靜音在最左、太小聲偏左、削峰邊緣接近滿格", () => {
  const agc = new MicAutoGain();
  assert.equal(agc.meterLevel(), 0);
  feed(agc, 0.2, -50);
  assert.ok(agc.meterLevel() < 0.2, `meter=${agc.meterLevel()}`);
  feed(agc, 0.2, -8);
  assert.ok(agc.meterLevel() > 0.9, `meter=${agc.meterLevel()}`);
});

test("統計：唱不到 10 秒不給平均值（兩句歌的平均只是雜訊）", () => {
  const agc = new MicAutoGain({ targetDb: -18 });
  feed(agc, 5, -26);
  assert.equal(agc.summary().average_gain_db, null);
  feed(agc, 20, -26);
  const s = agc.summary();
  assert.ok(s.average_gain_db > 0, `average=${s.average_gain_db}`);
  assert.ok(s.peak_input_db < -20 && s.peak_input_db > -30, `peak=${s.peak_input_db}`);
  assert.equal(s.clip_guarded, false);
});

test("壞資料（NaN、負數、undefined frame）不會讓增益變成 NaN", () => {
  const agc = new MicAutoGain();
  agc.update(FRAME, { rms: NaN });
  agc.update(FRAME, { rms: -5 });
  agc.update(FRAME, undefined);
  agc.update(NaN, { rms: 0.1 });
  assert.ok(Number.isFinite(agc.gainDb), `gainDb=${agc.gainDb}`);
  assert.ok(Number.isFinite(agc.level), `level=${agc.level}`);
  assert.equal(agc.gainDb, 0);
});
