/**
 * 和聲規劃的前端單元測試（node --test，不需要瀏覽器也不需要 npm 套件）。
 *
 * 和聲是整個系統裡「錯了最刺耳、但最難用耳朵定位」的功能：
 * 唱出調外音的時候，使用者只會覺得「這台機器的和聲很難聽」，
 * 不會知道是因為 Re 的上三度被算成 +4 而不是 +3。
 * 所以音階度數的算法、調性判定的門檻、以及「什麼時候不該出聲」全部釘死在這裡。
 */
const test = require("node:test");
const assert = require("node:assert/strict");

const {
  HarmonyPlanner,
  estimateKey,
  diatonicShift,
  MAJOR_SCALE,
  MIN_HOLD_SECONDS,
  VOICE_TRIM,
} = require("../js/harmony-planner.js");

const FRAME = 1 / 60;

/** 產生一段音符：從 midis 依序排開，每個音 dur 秒。 */
function notesFrom(midis, dur = 0.5) {
  return midis.map((midi, i) => ({ start: i * dur, end: (i + 1) * dur, midi }));
}

/** C 大調的音階跑法，長度足夠讓調性判定過關（> 8 秒有效音符）。 */
function cMajorNotes() {
  const scale = [60, 62, 64, 65, 67, 69, 71, 72];   // C D E F G A B C
  // 主音與屬音多給一點時間（真實歌曲就是這樣），相關係數才會漂亮
  const seq = [...scale, 60, 67, 60, 64, 67, 60, 62, 64, 65, 67, 60, 67, 60];
  return notesFrom(seq, 0.6);
}

// --- 音階度數 ---

test("上三度：C 大調的 Do 疊 +4、Re 疊 +3", () => {
  const key = { tonic: 0, mode: "major", scale: MAJOR_SCALE };
  // 這一條就是整個功能的存在理由：固定 +4 會讓 Re 唱出 Fa♯（調外音）
  assert.equal(diatonicShift(60, key, 2), 4);   // C → E
  assert.equal(diatonicShift(62, key, 2), 3);   // D → F
  assert.equal(diatonicShift(64, key, 2), 3);   // E → G
  assert.equal(diatonicShift(65, key, 2), 4);   // F → A
  assert.equal(diatonicShift(67, key, 2), 4);   // G → B
  assert.equal(diatonicShift(69, key, 2), 3);   // A → C
  assert.equal(diatonicShift(71, key, 2), 3);   // B → D
});

test("跨八度也對：高八度的 Do 疊的還是 +4", () => {
  const key = { tonic: 0, mode: "major", scale: MAJOR_SCALE };
  assert.equal(diatonicShift(72, key, 2), 4);
  assert.equal(diatonicShift(48, key, 2), 4);
  assert.equal(diatonicShift(74, key, 2), 3);   // 高八度的 Re
});

test("上五度：C 大調的 Do 疊 +7、Ti 疊 +6（減五度，不是完全五度）", () => {
  const key = { tonic: 0, mode: "major", scale: MAJOR_SCALE };
  assert.equal(diatonicShift(60, key, 4), 7);
  // B → F 是三全音。和聲照音階走就會走到這裡 —— 這是正確的（它在調內），
  // 硬要改成 +7 反而會唱出 F♯。
  assert.equal(diatonicShift(71, key, 4), 6);
});

test("下三度是負的，且同樣照音階", () => {
  const key = { tonic: 0, mode: "major", scale: MAJOR_SCALE };
  assert.equal(diatonicShift(64, key, -2), -4);  // E → C
  assert.equal(diatonicShift(65, key, -2), -3);  // F → D
});

test("非音階音（半音經過音）吸附到最近的音階音，不會跑出調外", () => {
  const key = { tonic: 0, mode: "major", scale: MAJOR_SCALE };
  const shift = diatonicShift(61, key, 2);       // C♯：不在 C 大調裡
  // 吸附到 C 或 D，兩者的上三度分別是 +4 / +3
  assert.ok(shift === 4 || shift === 3, `shift=${shift}`);
});

test("小調用小調音階：A 小調的 La 疊 +3（小三度）", () => {
  const notes = notesFrom([57, 59, 60, 62, 64, 65, 67, 69, 57, 64, 57, 60, 64, 57], 0.7);
  const key = estimateKey(notes);
  assert.ok(key, "應該判得出調性");
  // A 小調的主音上三度是 C，相差 3 個半音（大調會是 4）
  assert.equal(diatonicShift(57, { ...key, tonic: 9, mode: "minor", scale: [0, 2, 3, 5, 7, 8, 10] }, 2), 3);
});

// --- 調性判定 ---

test("音符太少就不給調性（樣本不夠，相關係數只是雜訊）", () => {
  assert.equal(estimateKey(notesFrom([60, 62, 64], 0.5)), null);
  assert.equal(estimateKey([]), null);
  assert.equal(estimateKey(null), null);
});

test("C 大調的旋律判成 C 大調", () => {
  const key = estimateKey(cMajorNotes());
  assert.ok(key, "應該判得出調性");
  assert.equal(key.tonic, 0);
  assert.equal(key.mode, "major");
  assert.equal(key.name, "C 大調");
  assert.ok(key.confidence >= 0.45, `信心太低：${key.confidence}`);
});

test("移調過的旋律判出移調過的調（升 5 個半音 = F 大調）", () => {
  const shifted = cMajorNotes().map(n => ({ ...n, midi: n.midi + 5 }));
  const key = estimateKey(shifted);
  assert.ok(key);
  assert.equal(key.tonic, 5);
  assert.equal(key.mode, "major");
});

test("壞資料不會炸：缺欄位、負音高、start > end 一律略過", () => {
  const junk = [
    { start: 0, end: 1 },                     // 沒有 midi
    { start: 0, end: 1, midi: -3 },           // 負音高
    { start: 2, end: 1, midi: 60 },           // 時間反了
    { midi: 60 },                             // 沒有時間
    null,
  ];
  assert.equal(estimateKey(junk), null);
  assert.equal(estimateKey(junk.concat(cMajorNotes())).tonic, 0);
});

test("單一長音不能一個人決定整首的調（上限 2 秒）", () => {
  // 一個拖了 60 秒的 F♯ 加上一小段清楚的 C 大調旋律：
  // 沒有上限的話 F♯ 的權重會壓倒一切，把調性帶去別的地方
  const notes = [{ start: 0, end: 60, midi: 66 }].concat(cMajorNotes());
  const key = estimateKey(notes);
  assert.ok(key);
  assert.equal(key.tonic, 0);
});

// --- 出聲時機 ---

test("預設是關的，關著的時候不會有任何聲部", () => {
  const h = new HarmonyPlanner();
  const plan = h.update(FRAME, { sang: true, hasNote: true, noteMidi: 60, noteStart: 0 });
  assert.equal(plan.active, false);
  assert.deepEqual(plan.voices, []);
});

test("沒人唱就不出聲（否則疊出來的是移調過的呼吸聲與冷氣聲）", () => {
  const h = new HarmonyPlanner({ enabled: true });
  h.setNotes(cMajorNotes());
  const plan = h.update(FRAME, { sang: false, hasNote: true, noteMidi: 60, noteStart: 0 });
  assert.equal(plan.active, false);
  assert.equal(plan.voices.length, 0);
});

test("有人唱、有導唱音符：三度和聲出聲，移調量照音階", () => {
  const h = new HarmonyPlanner({ enabled: true, style: "third", level: 0.5 });
  h.setNotes(cMajorNotes());
  const plan = h.update(FRAME, { sang: true, hasNote: true, noteMidi: 62, noteStart: 1 });
  assert.equal(plan.active, true);
  assert.equal(plan.voices.length, 1);
  assert.equal(plan.voices[0].shift, 3);        // D → F
  assert.equal(plan.voices[0].gain, 0.5);
});

test("沒有導唱音符（前奏、間奏、念白）時三度和聲不出聲", () => {
  const h = new HarmonyPlanner({ enabled: true, style: "third" });
  h.setNotes(cMajorNotes());
  const plan = h.update(FRAME, { sang: true, hasNote: false, noteMidi: 0, noteStart: null });
  assert.equal(plan.active, false);
});

test("低八度不需要音階資訊，沒有導唱音符也能疊", () => {
  const h = new HarmonyPlanner({ enabled: true, style: "octave" });
  h.setNotes(cMajorNotes());
  const plan = h.update(FRAME, { sang: true, hasNote: false, noteMidi: 0, noteStart: null });
  assert.equal(plan.active, true);
  assert.equal(plan.voices[0].shift, -12);
});

test("判不出調性時，需要音階的風格自動退成低八度", () => {
  const h = new HarmonyPlanner({ enabled: true, style: "third" });
  h.setNotes(notesFrom([60, 61], 0.3));        // 樣本不足
  assert.equal(h.key, null);
  assert.equal(h.effectiveStyle(), "octave");
  const plan = h.update(FRAME, { sang: true, hasNote: true, noteMidi: 60, noteStart: 0 });
  assert.equal(plan.voices[0].shift, -12);
  assert.match(h.describe(), /低八度/);
});

test("雙聲部同時給上三度與低八度，低八度的音量再收一點", () => {
  const h = new HarmonyPlanner({ enabled: true, style: "duet", level: 0.6 });
  h.setNotes(cMajorNotes());
  const plan = h.update(FRAME, { sang: true, hasNote: true, noteMidi: 60, noteStart: 0 });
  assert.equal(plan.voices.length, 2);
  assert.equal(plan.voices[0].shift, 4);
  assert.equal(plan.voices[1].shift, -12);
  assert.ok(plan.voices[1].gain < plan.voices[0].gain, "低八度應該比三度小聲");
  assert.equal(plan.voices[1].gain, Math.round(0.6 * VOICE_TRIM.octave * 1000) / 1000);
});

test("和聲永遠不會蓋過主唱：音量拉到滿也夾在 0.85", () => {
  const h = new HarmonyPlanner({ enabled: true, style: "third", level: 1 });
  h.setNotes(cMajorNotes());
  const plan = h.update(FRAME, { sang: true, hasNote: true, noteMidi: 60, noteStart: 0 });
  assert.ok(plan.voices[0].gain <= 0.85, `gain=${plan.voices[0].gain}`);
});

test("音量 0 等於關掉", () => {
  const h = new HarmonyPlanner({ enabled: true, level: 0 });
  h.setNotes(cMajorNotes());
  const plan = h.update(FRAME, { sang: true, hasNote: true, noteMidi: 60, noteStart: 0 });
  assert.equal(plan.active, false);
});

test("同一個音符不重算移調量（音符邊界上不會抖）", () => {
  const h = new HarmonyPlanner({ enabled: true, style: "third" });
  h.setNotes(cMajorNotes());
  h.update(FRAME, { sang: true, hasNote: true, noteMidi: 60, noteStart: 0 });
  assert.equal(h.voices[0].shift, 4);
  // 同一個 noteStart，但音高欄位被別的東西污染 —— 不該改變已經定好的移調量
  const plan = h.update(FRAME, { sang: true, hasNote: true, noteMidi: 62, noteStart: 0 });
  assert.equal(plan.voices[0].shift, 4);
});

test("換音符後至少隔 MIN_HOLD_SECONDS 才換移調量", () => {
  const h = new HarmonyPlanner({ enabled: true, style: "third" });
  h.setNotes(cMajorNotes());
  h.update(FRAME, { sang: true, hasNote: true, noteMidi: 60, noteStart: 0 });
  // 下一幀就換音符：太快，維持原本的移調量
  let plan = h.update(FRAME, { sang: true, hasNote: true, noteMidi: 62, noteStart: 1 });
  assert.equal(plan.voices[0].shift, 4);
  // 等過保持時間就換得過去
  plan = h.update(MIN_HOLD_SECONDS + 0.01, { sang: true, hasNote: true, noteMidi: 62, noteStart: 1 });
  assert.equal(plan.voices[0].shift, 3);
});

test("換風格立刻生效，不被保持時間擋住", () => {
  const h = new HarmonyPlanner({ enabled: true, style: "third" });
  h.setNotes(cMajorNotes());
  h.update(FRAME, { sang: true, hasNote: true, noteMidi: 60, noteStart: 0 });
  h.configure({ style: "octave" });
  const plan = h.update(FRAME, { sang: true, hasNote: true, noteMidi: 60, noteStart: 0 });
  assert.equal(plan.voices[0].shift, -12);
});

test("換歌要重估調性，不能沿用上一首", () => {
  const h = new HarmonyPlanner({ enabled: true, style: "third" });
  h.setNotes(cMajorNotes());
  assert.equal(h.key.tonic, 0);
  h.setNotes(cMajorNotes().map(n => ({ ...n, midi: n.midi + 2 })));
  assert.equal(h.key.tonic, 2);
  assert.equal(h.voices.length, 0, "換歌要清掉上一首的聲部");
});

test("認不得的風格不會讓和聲變成隨機聲部", () => {
  const h = new HarmonyPlanner({ enabled: true, style: "不存在的風格" });
  assert.equal(h.style, "third");
  h.configure({ style: "亂送的" });
  assert.equal(h.style, "third");
});

test("徽章文字帶上調性（和聲不對時這是唯一有用的線索）", () => {
  const h = new HarmonyPlanner({ enabled: true, style: "third" });
  h.setNotes(cMajorNotes());
  assert.equal(h.describe(), "和聲 上三度（C 大調）");
  h.enabled = false;
  assert.equal(h.describe(), null);
});

test("dt 異常（分頁切回來的好幾秒）不會讓保持時間邏輯壞掉", () => {
  const h = new HarmonyPlanner({ enabled: true, style: "third" });
  h.setNotes(cMajorNotes());
  h.update(FRAME, { sang: true, hasNote: true, noteMidi: 60, noteStart: 0 });
  const plan = h.update(9999, { sang: true, hasNote: true, noteMidi: 62, noteStart: 1 });
  assert.equal(plan.voices[0].shift, 3);
  assert.ok(Number.isFinite(h.holdSeconds));
});
