/**
 * 情境背景的前端單元測試（node --test，不需要瀏覽器也不需要 npm 套件）。
 *
 * 這一項的錯誤都是「現場沒有人會當場說出口」的那一種：
 * 把真的 MV 換成情境背景、背景忽明忽暗、同一首歌每次背景都不一樣。
 * 所以決策與限速全部寫成純邏輯，在這裡釘死。
 */
const test = require("node:test");
const assert = require("node:assert/strict");

const {
  AmbientDirector,
  StillnessProbe,
  BeatDetector,
  QualityGovernor,
  THEMES,
  THEME_IDS,
  analyseSpectrum,
  binIndex,
  pickTheme,
  MAX_LUMA_SLEW,
  IDLE_ENERGY,
  SPECTRUM_FLOOR,
  SPECTRUM_CEIL,
  STILL_DIFF_THRESHOLD,
  STILL_MIN_SAMPLES,
  STILL_MIN_SECONDS,
  QUALITY_LEVELS,
} = require("../js/ambient-visuals.js");

const FRAME = 1 / 30;

/** 餵 seconds 秒的幀，回傳最後一次的狀態。 */
function run(director, seconds, input) {
  let state = null;
  const frames = Math.round(seconds / FRAME);
  for (let i = 0; i < frames; i++) state = director.update(FRAME, input);
  return state;
}

// --- 畫面該給誰 ---

test("有 MV 而且還沒判定完：畫面留給 MV", () => {
  const d = new AmbientDirector({ mode: "auto" });
  d.startSong({ songId: "abc", hasVideo: true });
  assert.equal(d.layer, "video");
});

test("沒有 MV：情境背景接手", () => {
  const d = new AmbientDirector({ mode: "auto" });
  d.startSong({ songId: "abc", hasVideo: false });
  assert.equal(d.layer, "ambient");
});

test("hasVideo 未知時先當成有 —— 有 MV 的歌不該先閃一下情境背景", () => {
  const d = new AmbientDirector({ mode: "auto" });
  d.startSong({ songId: "abc" });
  assert.equal(d.layer, "video");
  // 真的載不起來才改口
  d.videoFailed();
  assert.equal(d.layer, "ambient");
});

test("抓到的 MV 其實是靜態圖：判定完由情境背景接手", () => {
  const d = new AmbientDirector({ mode: "auto" });
  d.startSong({ songId: "abc", hasVideo: true });
  for (let i = 0; i < STILL_MIN_SAMPLES + 1; i++) d.probeFrame(0.3, i * 2);
  assert.equal(d.layer, "ambient");
  assert.equal(d.needsProbe, false, "定案後不該再花取樣的成本");
});

test("mode=off：沒有 MV 也不放情境背景（維持加這個功能之前的行為）", () => {
  const d = new AmbientDirector({ mode: "off" });
  d.startSong({ songId: "abc", hasVideo: false });
  assert.equal(d.layer, "black");
  d.startSong({ songId: "abc", hasVideo: true });
  assert.equal(d.layer, "video");
});

test("mode=always：有 MV 也用情境背景，而且不必浪費取樣成本", () => {
  const d = new AmbientDirector({ mode: "always" });
  d.startSong({ songId: "abc", hasVideo: true });
  assert.equal(d.layer, "ambient");
  assert.equal(d.needsProbe, false);
});

// --- 靜態 MV 偵測 ---

test("看到一次真的在動就結案，不再等滿取樣數", () => {
  const probe = new StillnessProbe();
  probe.push(0.2, 1);
  probe.push(STILL_DIFF_THRESHOLD + 1, 2);
  assert.equal(probe.state, "moving");
  assert.equal(probe.settled, true);
});

test("取樣夠多但時間跨度不夠不下判斷（開頭的黑畫面淡入會騙人）", () => {
  const probe = new StillnessProbe();
  for (let i = 0; i < STILL_MIN_SAMPLES + 3; i++) probe.push(0.1, i * 0.2);
  assert.equal(probe.state, "unknown");
  probe.push(0.1, STILL_MIN_SECONDS + 1);
  assert.equal(probe.state, "still");
});

test("判定一旦定案就不再改（唱到一半來回切比兩種都不完美更糟）", () => {
  const probe = new StillnessProbe();
  for (let i = 0; i < STILL_MIN_SAMPLES; i++) probe.push(0, i * 2);
  assert.equal(probe.state, "still");
  probe.push(200, 99);
  assert.equal(probe.state, "still");
});

test("觀察窗用牆上時鐘：時間倒退（短片 loop 繞回 0）不會讓判定永遠不成立", () => {
  const probe = new StillnessProbe();
  // 模擬把影片的 currentTime 傳進來的那種倒退序列
  const times = [4, 0.5, 4.5, 1, 5, 1.5, 6, 7, 8, 9, 10];
  for (const t of times) probe.push(0.1, t);
  assert.equal(probe.state, "still", "倒退的時間戳不該把觀察窗算成負的");
});

test("取樣失敗（跨來源污染）就當作會動，照常放 MV", () => {
  const probe = new StillnessProbe();
  probe.giveUp();
  assert.equal(probe.state, "moving");
});

// --- 不准閃 ---

test("亮度變化受限速器管，任何一幀都不超過 MAX_LUMA_SLEW * dt", () => {
  const d = new AmbientDirector({ mode: "always", brightness: 1 });
  d.startSong({ songId: "abc", hasVideo: false });
  let prev = d.luma;
  // 每一幀在「全靜音」與「滿能量」之間跳，這是最壞的輸入
  for (let i = 0; i < 200; i++) {
    const s = d.update(FRAME, { playing: true, energy: i % 2 ? 1 : 0, bass: i % 2 ? 1 : 0 });
    assert.ok(Math.abs(s.luma - prev) <= MAX_LUMA_SLEW * FRAME + 1e-9,
      `第 ${i} 幀亮度跳了 ${Math.abs(s.luma - prev)}`);
    prev = s.luma;
  }
});

test("亮度上限：設定頁調低就真的變暗", () => {
  const bright = new AmbientDirector({ mode: "always", brightness: 1.0 });
  const dim = new AmbientDirector({ mode: "always", brightness: 0.3 });
  bright.startSong({ songId: "x" });
  dim.startSong({ songId: "x" });
  const a = run(bright, 8, { playing: true, energy: 0.5, bass: 0.5 });
  const b = run(dim, 8, { playing: true, energy: 0.5, bass: 0.5 });
  assert.ok(b.luma < a.luma * 0.6, `暗的那個應該明顯更暗：${b.luma} vs ${a.luma}`);
});

test("鼓點只推動態不碰亮度", () => {
  const d = new AmbientDirector({ mode: "always", brightness: 1 });
  d.startSong({ songId: "x" });
  // 先讓亮度穩定在固定能量上
  run(d, 8, { playing: true, energy: 0.4, bass: 0.0 });
  const before = d.luma;
  // 同樣的能量，但低頻猛敲
  const s = d.update(FRAME, { playing: true, energy: 0.4, bass: 1.0 });
  assert.ok(s.beatPulse > 0, "應該要認得出這一下鼓");
  assert.ok(Math.abs(s.luma - before) < 0.01, "亮度不該被鼓點推動");
});

test("待機（沒有歌）時仍然會動，但能量固定在低檔", () => {
  const d = new AmbientDirector({ mode: "always" });
  d.clearSong();
  const s = run(d, 10, { playing: false });
  assert.ok(Math.abs(s.energy - IDLE_ENERGY) < 0.01);
  assert.ok(s.luma > 0, "待機畫面不該是全黑");
  const phase1 = s.phase;
  const s2 = run(d, 2, { playing: false });
  assert.notEqual(s2.phase, phase1, "待機時相位還是要走（凍住就跟靜止圖一樣）");
});

// --- 主題 ---

test("自動主題對同一首歌永遠挑到同一個（背景是歌的一部分，不能每次都變）", () => {
  const first = pickTheme("dQw4w9WgXcQ", "auto");
  for (let i = 0; i < 50; i++) {
    assert.equal(pickTheme("dQw4w9WgXcQ", "auto"), first);
  }
  assert.ok(THEME_IDS.includes(first));
});

test("自動主題會分散到不同主題，而不是所有歌都同一個", () => {
  const seen = new Set();
  for (let i = 0; i < 200; i++) seen.add(pickTheme(`song-${i}`, "auto"));
  assert.ok(seen.size >= 3, `只用到 ${seen.size} 個主題`);
});

test("指定主題就照指定的；認不得的字串退回自動挑", () => {
  assert.equal(pickTheme("abc", "ocean"), "ocean");
  assert.ok(THEME_IDS.includes(pickTheme("abc", "no-such-theme")));
});

test("每個主題的資料都完整，而且亮度錨點都壓在不搶字幕的範圍", () => {
  for (const id of THEME_IDS) {
    const t = THEMES[id];
    assert.equal(t.id, id);
    assert.ok(t.name, `${id} 少了顯示名稱`);
    assert.ok(Array.isArray(t.colors) && t.colors.length >= 3, `${id} 的顏色不足`);
    assert.ok(t.baseLuma + t.swing <= 1.0,
      `${id} 的亮度錨點超出 0~1（${t.baseLuma + t.swing}）`);
    // 音樂只能「推一點」亮度。擺幅比這大就會變成跟著歌忽明忽暗，
    // 即使有限速器擋住閃爍，觀感也會像螢幕壞了。
    assert.ok(t.swing <= 0.25, `${id} 的音樂擺幅太大（${t.swing}）`);
  }
});

// --- 頻譜 ---

test("分頻點照取樣率換算（48k 與 44.1k 的 bin 不一樣）", () => {
  assert.equal(binIndex(0, 48000, 512), 0);
  assert.equal(binIndex(24000, 48000, 512), 512);
  assert.equal(binIndex(250, 48000, 512), Math.round(250 / 24000 * 512));
  assert.ok(binIndex(250, 44100, 512) > binIndex(250, 48000, 512));
});

test("安靜（位元組全在噪音底以下）時能量是 0，不是一個小小的常數", () => {
  const bytes = new Uint8Array(512).fill(Math.floor(SPECTRUM_FLOOR * 255) - 10);
  const s = analyseSpectrum(bytes, 48000);
  assert.equal(s.energy, 0);
  assert.equal(s.bass, 0);
});

test("音樂的實際位元組範圍會攤成 0~1，不是永遠貼在高檔", () => {
  const mid = new Uint8Array(512).fill(Math.round((SPECTRUM_FLOOR + SPECTRUM_CEIL) / 2 * 255));
  const loud = new Uint8Array(512).fill(Math.round(SPECTRUM_CEIL * 255));
  const a = analyseSpectrum(mid, 48000);
  const b = analyseSpectrum(loud, 48000);
  assert.ok(a.energy > 0.35 && a.energy < 0.65, `中等音量應落在中間，得到 ${a.energy}`);
  assert.ok(b.energy > 0.95);
});

test("能量以低頻為主：只有高頻的訊號不該把背景推到滿", () => {
  const bytes = new Uint8Array(512);
  // 只填 2kHz 以上
  for (let i = binIndex(2000, 48000, 512); i < 512; i++) bytes[i] = 255;
  const s = analyseSpectrum(bytes, 48000);
  assert.ok(s.treble > 0.9);
  assert.ok(s.energy < 0.1, `高頻不該推動能量，得到 ${s.energy}`);
});

test("空的頻譜資料不會炸（音訊還沒解鎖時就是這個狀態）", () => {
  const s = analyseSpectrum(new Uint8Array(0), 48000);
  assert.deepEqual(s, { bass: 0, mid: 0, treble: 0, energy: 0 });
});

// --- 鼓點 ---

test("持續的低頻不算鼓點（只有比自己的平均大聲才算）", () => {
  const b = new BeatDetector();
  let hits = 0;
  for (let i = 0; i < 300; i++) if (b.push(0.6, FRAME)) hits++;
  assert.ok(hits <= 1, `穩定的低頻不該一直判成鼓點，判了 ${hits} 次`);
});

test("不反應期擋掉同一下鼓的前後緣", () => {
  const b = new BeatDetector();
  for (let i = 0; i < 60; i++) b.push(0.15, FRAME);
  let hits = 0;
  // 連續 5 幀都很大聲 = 同一下鼓
  for (let i = 0; i < 5; i++) if (b.push(0.9, FRAME)) hits++;
  assert.equal(hits, 1);
});

// --- 畫質自動降級 ---

test("畫太慢就降級，回復要連續很多幀才升回去（遲滯）", () => {
  const q = new QualityGovernor({ upStreakNeeded: 10 });
  assert.equal(q.level, "high");
  for (let i = 0; i < 60; i++) q.push(30);
  assert.equal(q.level, "low", "一直超出預算應該降到底");
  for (let i = 0; i < 5; i++) q.push(0.5);
  assert.equal(q.level, "low", "才輕鬆五幀不該就升回去");
  for (let i = 0; i < 200; i++) q.push(0.5);
  assert.equal(q.level, "high");
  assert.ok(QUALITY_LEVELS.includes(q.level));
});
