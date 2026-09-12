/**
 * 對唱模式的前端單元測試（node --test，不需要瀏覽器也不需要 npm 套件）。
 *
 * 這個功能有一種特別惡劣的壞法：**看起來完全正常，但分數是假的**。
 * 兩支麥克風在同一個房間，A 唱歌時 B 的麥克風也收得到 —— 串音判定一旦失效，
 * B 只要舉著麥克風站著就會有跟 A 差不多的分數，而畫面上完全看不出異狀
 * （兩邊的分數都在跳、Combo 也在累計）。現場沒有人有辦法發現這件事。
 *
 * 所以判定的每一條規則都在這裡釘死：電平主導、遲滯、音高分歧例外、
 * 以及「只有一個人在唱」時不要亂比勝負。
 */
const test = require("node:test");
const assert = require("node:assert/strict");

const {
  DuetScorer,
  compareSections,
  DUET_SILENCE_DB,
  SOLID_SIGNAL_DB,
  DEFAULT_MARGIN_DB,
  MIN_CREDITED_SECONDS,
  TIE_RATIO,
  MIN_DUEL_NOTE_FRAMES,
  MIN_DUEL_SPREAD,
  MIN_DUEL_BALANCE,
} = require("../js/duet-scorer.js");

const FRAME = 1 / 60;

/** dBFS 轉線性 RMS，測試裡用 dB 描述電平比較好讀。 */
function rms(db) {
  return Math.pow(10, db / 20);
}

/**
 * 餵 seconds 秒的幀，回傳最後一幀的判定。
 * @param {object} mics { aDb, bDb, aMidi, bMidi }
 */
function feed(duet, seconds, mics) {
  const frames = Math.max(1, Math.round(seconds / FRAME));
  let decision = null;
  for (let i = 0; i < frames; i++) {
    decision = duet.decide(FRAME, {
      rms: mics.aDb === null ? 0 : rms(mics.aDb),
      midi: mics.aMidi || 0,
    }, {
      rms: mics.bDb === null ? 0 : rms(mics.bDb),
      midi: mics.bMidi || 0,
    });
  }
  return decision;
}

test("關閉時不做任何判定：兩支麥克風各自照常計分", () => {
  const d = new DuetScorer({ enabled: false });
  const decision = feed(d, 1, { aDb: -20, bDb: null });
  assert.equal(decision.a, true);
  assert.equal(decision.b, true);
  assert.equal(decision.reason, "disabled");
});

test("兩邊都安靜：誰都不計分（底噪不該累積成績）", () => {
  const d = new DuetScorer({ enabled: true });
  const decision = feed(d, 1, { aDb: DUET_SILENCE_DB - 10, bDb: DUET_SILENCE_DB - 10 });
  assert.equal(decision.a, false);
  assert.equal(decision.b, false);
  assert.equal(decision.dominant, "both");
});

test("串音：A 唱歌、B 只收到漏過來的聲音（音高一樣）→ 只算 A", () => {
  const d = new DuetScorer({ enabled: true });
  // B 比 A 小 12 dB，超過預設門檻 9 dB
  const decision = feed(d, 2, { aDb: -20, bDb: -32, aMidi: 60, bMidi: 60 });
  assert.equal(decision.dominant, "a");
  assert.equal(decision.a, true);
  assert.equal(decision.b, false, "串音被算進 B 的成績了");
  assert.equal(decision.reason, "dominance");
});

test("一起唱（電平差不多）→ 兩邊都算", () => {
  const d = new DuetScorer({ enabled: true });
  const decision = feed(d, 2, { aDb: -20, bDb: -22, aMidi: 60, bMidi: 64 });
  assert.equal(decision.dominant, "both");
  assert.equal(decision.a, true);
  assert.equal(decision.b, true);
});

test("一邊完全靜音：有聲音的那邊主導（最乾淨的情況也要對）", () => {
  const d = new DuetScorer({ enabled: true });
  const decision = feed(d, 1, { aDb: null, bDb: -18, bMidi: 62 });
  assert.equal(decision.dominant, "b");
  assert.equal(decision.a, false);
  assert.equal(decision.b, true);
});

test("音高分歧例外：B 小 10 dB 但唱的是別的音 → 還是算 B 的分", () => {
  const d = new DuetScorer({ enabled: true });
  // -30 dB 高於「訊號夠紮實」門檻，音高差 4 個半音（三度）
  const decision = feed(d, 2, { aDb: -20, bDb: -30, aMidi: 60, bMidi: 64 });
  assert.equal(decision.dominant, "a", "電平上仍然是 A 主導");
  assert.equal(decision.b, true, "唱不同音的第二個人被當成串音了");
  assert.equal(decision.reason, "pitch-divergence");
});

test("音高分歧不適用於微弱訊號：接近底噪的假音高不能放串音進來", () => {
  const d = new DuetScorer({ enabled: true });
  // -40 dB 在噪音閘門之上、但低於「訊號夠紮實」門檻
  assert.ok(-40 > DUET_SILENCE_DB && -40 < SOLID_SIGNAL_DB);
  const decision = feed(d, 2, { aDb: -20, bDb: -40, aMidi: 60, bMidi: 67 });
  assert.equal(decision.b, false);
  assert.equal(decision.reason, "dominance");
});

test("音高一樣就不是分歧：同一個聲音漏過去，音高當然一樣", () => {
  const d = new DuetScorer({ enabled: true });
  const decision = feed(d, 2, { aDb: -20, bDb: -30, aMidi: 60, bMidi: 60.5 });
  assert.equal(decision.b, false);
});

test("遲滯：進入獨佔之後，電平差縮小到門檻一半以上仍維持獨佔", () => {
  const d = new DuetScorer({ enabled: true });
  feed(d, 2, { aDb: -20, bDb: -32, aMidi: 60, bMidi: 60 });
  assert.equal(d.dominant, "a");
  // 差 6 dB：低於 9 dB 的進入門檻，但高於 4.5 dB 的釋放門檻 → 維持現狀
  const decision = feed(d, 1, { aDb: -20, bDb: -26, aMidi: 60, bMidi: 60 });
  assert.equal(decision.dominant, "a");
  assert.equal(decision.b, false);
});

test("遲滯：電平差縮到釋放門檻以內才回到兩邊都算", () => {
  const d = new DuetScorer({ enabled: true });
  feed(d, 2, { aDb: -20, bDb: -32, aMidi: 60, bMidi: 60 });
  const decision = feed(d, 1, { aDb: -20, bDb: -23, aMidi: 60, bMidi: 60 });
  assert.equal(decision.dominant, "both");
  assert.equal(decision.b, true);
});

test("遲滯：從『兩邊都算』出發，差 6 dB 不足以進入獨佔", () => {
  const d = new DuetScorer({ enabled: true });
  const decision = feed(d, 2, { aDb: -20, bDb: -26, aMidi: 60, bMidi: 60 });
  assert.equal(decision.dominant, "both");
  assert.equal(decision.b, true);
});

test("門檻可調：調高之後原本算串音的電平差就會被當成兩個人一起唱", () => {
  const d = new DuetScorer({ enabled: true, marginDb: 18 });
  const decision = feed(d, 2, { aDb: -20, bDb: -32, aMidi: 60, bMidi: 60 });
  assert.equal(decision.dominant, "both");
  assert.equal(decision.b, true);
});

test("門檻夾在合法範圍（設定頁滑到底也不能變成 0 dB 或 100 dB）", () => {
  assert.equal(new DuetScorer({ marginDb: 0 }).marginDb, 3);
  assert.equal(new DuetScorer({ marginDb: 999 }).marginDb, 24);
  assert.equal(new DuetScorer({ marginDb: "abc" }).marginDb, 3);
  assert.equal(new DuetScorer({}).marginDb, DEFAULT_MARGIN_DB);
});

test("改門檻不清掉統計：唱到一半調參數不該把前面唱的作廢", () => {
  const d = new DuetScorer({ enabled: true });
  feed(d, 3, { aDb: -20, bDb: -20, aMidi: 60, bMidi: 60 });
  const before = d.micSummary().a.credited_seconds;
  d.configure({ marginDb: 12 });
  assert.equal(d.micSummary().a.credited_seconds, before);
});

test("開關切換兩個方向都重來：上一段的包絡線與統計不能沿用", () => {
  const d = new DuetScorer({ enabled: true });
  feed(d, 3, { aDb: -20, bDb: -32, aMidi: 60, bMidi: 60 });
  assert.ok(d.micSummary().a.credited_seconds > 0);
  d.configure({ enabled: false });
  assert.equal(d.micSummary().a.credited_seconds, 0);
  assert.equal(d.dominant, "both");
});

test("時間統計：被算到、有唱卻被判成串音、以及主導時間", () => {
  const d = new DuetScorer({ enabled: true });
  feed(d, 4, { aDb: -20, bDb: -32, aMidi: 60, bMidi: 60 });
  const s = d.micSummary();
  assert.ok(Math.abs(s.a.credited_seconds - 4) < 0.15, `A credited=${s.a.credited_seconds}`);
  // B 一路有聲音但都被判成串音
  assert.ok(s.b.denied_seconds > 3.5, `B denied=${s.b.denied_seconds}`);
  assert.equal(s.b.credited_seconds, 0);
  assert.ok(s.b.crosstalk_ratio > 0.95);
  assert.ok(s.a.lead_seconds > 3.5);
  assert.equal(s.a.crosstalk_ratio, 0);
});

test("超大 dt（分頁切回來）被夾住，不會一幀就累積好幾秒", () => {
  const d = new DuetScorer({ enabled: true });
  d.decide(30, { rms: rms(-20), midi: 60 }, { rms: 0, midi: 0 });
  // 看未四捨五入的累計值：夾在 0.25 秒（micSummary 只留一位小數，會顯示 0.3）
  assert.ok(d.stats.a.credited <= 0.25, `credited=${d.stats.a.credited}`);
});

test("徽章：被判成串音的那一位要問得出來（不然使用者以為評分壞了）", () => {
  const d = new DuetScorer({ enabled: true });
  feed(d, 2, { aDb: -20, bDb: -32, aMidi: 60, bMidi: 60 });
  assert.equal(d.mutedSinger(), "b");
  feed(d, 2, { aDb: -20, bDb: -20, aMidi: 60, bMidi: 60 });
  assert.equal(d.mutedSinger(), null);
});

// --- 對戰結果 ---

/** 讓兩位都累積足夠的「有唱」時間，才會進入對戰判定。 */
function bothSing(duet, seconds = MIN_CREDITED_SECONDS + 2) {
  feed(duet, seconds, { aDb: -20, bDb: -21, aMidi: 60, bMidi: 64 });
}

test("兩位都唱：分數高的勝出", () => {
  const d = new DuetScorer({ enabled: true });
  bothSing(d);
  const v = d.verdict({ score: 50000, accuracy: 0.6, grade: "SS" },
                      { score: 30000, accuracy: 0.4, grade: "A" });
  assert.equal(v.contested, true);
  assert.equal(v.winner, "a");
  assert.equal(v.margin, 20000);
  assert.equal(v.score_b, 30000);
});

test("分數只差一點算平手（逐幀評分的雜訊不該被當成勝負）", () => {
  const d = new DuetScorer({ enabled: true });
  bothSing(d);
  const v = d.verdict({ score: 50000 }, { score: 49000 });
  assert.equal(v.winner, "tie");
  assert.ok(v.margin_ratio <= TIE_RATIO);
});

test("剛好超過平手門檻就要分出勝負", () => {
  const d = new DuetScorer({ enabled: true });
  bothSing(d);
  const v = d.verdict({ score: 50000 }, { score: 48000 });   // 差 4%
  assert.equal(v.winner, "a");
});

test("只有一個人唱（另一支麥克風放在桌上）：不比勝負", () => {
  const d = new DuetScorer({ enabled: true });
  feed(d, MIN_CREDITED_SECONDS + 2, { aDb: -20, bDb: null, aMidi: 60 });
  const v = d.verdict({ score: 40000 }, { score: 0 });
  assert.equal(v.contested, false);
  assert.equal(v.winner, "a");
});

test("兩個人都只唱了兩句：唱太短不足以比，也不比", () => {
  const d = new DuetScorer({ enabled: true });
  feed(d, 3, { aDb: -20, bDb: -21, aMidi: 60, bMidi: 64 });
  const v = d.verdict({ score: 4000 }, { score: 1000 });
  assert.equal(v.contested, false);
  assert.equal(v.winner, null);
});

test("對戰結果附上串音比例：分數不公平的原因要說得出來", () => {
  const d = new DuetScorer({ enabled: true });
  // B 一直被判成串音，但仍累積了足夠的被算到時間（一起唱的段落）
  feed(d, MIN_CREDITED_SECONDS + 2, { aDb: -20, bDb: -21, aMidi: 60, bMidi: 64 });
  feed(d, 8, { aDb: -20, bDb: -34, aMidi: 60, bMidi: 60 });
  const v = d.verdict({ score: 50000 }, { score: 20000 });
  assert.equal(v.contested, true);
  assert.ok(v.mics.b.crosstalk_ratio > 0.3, `crosstalk=${v.mics.b.crosstalk_ratio}`);
});

test("壞資料不會炸：分數是 undefined / 文字時當成 0 分", () => {
  const d = new DuetScorer({ enabled: true });
  bothSing(d);
  const v = d.verdict({}, { score: "abc" });
  assert.equal(v.score_a, 0);
  assert.equal(v.score_b, 0);
  assert.equal(v.winner, "tie");
});

// --- 段落對決 (compareSections) ---
//
// 這一段的壞法跟串音一樣惡劣但更容易發生：**把分段接唱當成勝負**。
// 對唱歌曲本來就是主歌 1 你唱、主歌 2 我唱、副歌一起唱 ——
// 如果沒唱那一段就被畫成 0%，畫面會宣布「A 68% 完勝 B 0%」，
// 而被冤枉的那一位在包廂裡沒有辦法反駁（那一段本來就不是他的）。
// 所以「哪些段落可以比」的每一條規則都在這裡釘死。

/** 造一筆段落成績（形狀比照 section-scorer.js summary() 的每一列）。 */
function section(index, label, accuracy, noteFrames) {
  return {
    index,
    kind: label.startsWith("副歌") ? "chorus" : "verse",
    label,
    preview: "",
    start: index * 30,
    end: index * 30 + 28,
    note_frames: noteFrames,
    hit_frames: Math.round(noteFrames * accuracy),
    perfect_frames: 0,
    sang_frames: noteFrames,
    accuracy,
    grade: "S",
    graded: noteFrames >= MIN_DUEL_NOTE_FRAMES,
  };
}

test("段落對決的門檻與段落評分一致（不然兩張成績單會互相打嘴）", () => {
  const sectionScorer = require("../js/section-scorer.js");
  assert.equal(MIN_DUEL_NOTE_FRAMES, sectionScorer.MIN_SECTION_NOTE_FRAMES);
  assert.equal(MIN_DUEL_SPREAD, sectionScorer.MIN_SECTION_SPREAD);
});

test("兩人都唱同一段：命中率差得夠多就點出主場", () => {
  const duel = compareSections(
    [section(1, "副歌 1", 0.7, 400)],
    [section(1, "副歌 1", 0.4, 380)]);
  assert.equal(duel.rows.length, 1);
  const row = duel.rows[0];
  assert.equal(row.contested, true);
  assert.equal(row.leader, "a");
  assert.equal(row.margin, 0.3);
  assert.equal(duel.wins.a, 1);
  assert.equal(duel.a_best.label, "副歌 1");
  assert.equal(duel.b_best, null);
});

test("兩人都唱同一段但差距不到門檻：平手，不硬分高下", () => {
  const duel = compareSections(
    [section(1, "副歌 1", 0.61, 400)],
    [section(1, "副歌 1", 0.59, 400)]);
  assert.equal(duel.rows[0].leader, "tie");
  assert.equal(duel.wins.tie, 1);
  assert.equal(duel.a_best, null, "平手的段落不該被當成誰的主場");
});

test("分段接唱：只有 A 唱的那一段標成「A 主唱」，不算 A 贏", () => {
  const duel = compareSections(
    [section(1, "主歌 1", 0.68, 400)],
    [section(1, "主歌 1", 0, 0)]);
  const row = duel.rows[0];
  assert.equal(row.contested, false, "沒唱的段落被拿去比勝負了");
  assert.equal(row.leader, null);
  assert.equal(row.main, "a");
  assert.equal(duel.wins.a, 0);
  assert.equal(duel.solo.a, 1);
  assert.equal(duel.contested_count, 0);
  assert.equal(duel.lead, null, "沒有對決段落就不該有整體結論");
});

test("參與量差太多（一個唱滿整段、一個只跟一句）：退成主唱，不並排比", () => {
  // B 只有 40 幀（過了評分門檻）但 A 有 400 幀 —— 只跟一句的人那一句準就是 100%
  const duel = compareSections(
    [section(1, "副歌 1", 0.6, 400)],
    [section(1, "副歌 1", 1.0, 40)]);
  const row = duel.rows[0];
  assert.equal(row.contested, false, "40 幀對 400 幀不是同一件事，不該並排比");
  assert.equal(row.main, "a");
  assert.ok(row.balance < MIN_DUEL_BALANCE);
});

test("兩邊都唱不夠的段落不列出來（沒有資訊就不要佔畫面）", () => {
  const duel = compareSections(
    [section(1, "間奏後", 0.5, 10)],
    [section(1, "間奏後", 0.5, 8)]);
  assert.equal(duel.rows.length, 0);
});

test("依歌曲順序排列，並統計比數與整體結論", () => {
  const duel = compareSections(
    [section(3, "副歌 2", 0.8, 400), section(1, "主歌 1", 0.7, 400),
     section(2, "副歌 1", 0.4, 400)],
    [section(1, "主歌 1", 0.5, 390), section(2, "副歌 1", 0.75, 400),
     section(3, "副歌 2", 0.5, 400)]);
  assert.deepEqual(duel.rows.map((r) => r.label), ["主歌 1", "副歌 1", "副歌 2"]);
  assert.equal(duel.wins.a, 2);
  assert.equal(duel.wins.b, 1);
  assert.equal(duel.lead, "a");
  assert.equal(duel.a_best.label, "副歌 2", "領先最多的那一段才是主場");
  assert.equal(duel.b_best.label, "副歌 1");
});

test("段落編號對不齊時不會錯位（少一段就少比一段，不會整排錯開）", () => {
  const duel = compareSections(
    [section(1, "主歌 1", 0.7, 400), section(2, "副歌 1", 0.3, 400)],
    [section(2, "副歌 1", 0.8, 400)]);
  const labels = duel.rows.map((r) => `${r.label}:${r.contested}`);
  assert.deepEqual(labels, ["主歌 1:false", "副歌 1:true"]);
  assert.equal(duel.rows[1].leader, "b");
});

test("壞資料不會炸：空的、null、缺欄位都回一份空結果", () => {
  for (const bad of [undefined, null, [], "nope", [null, {}, { index: "x" }]]) {
    const duel = compareSections(bad, bad);
    assert.equal(duel.rows.length, 0);
    assert.equal(duel.contested_count, 0);
    assert.equal(duel.lead, null);
  }
});

test("verdict() 帶上段落對決（只有一個人唱時形狀一樣，只是沒有對決段落）", () => {
  const d = new DuetScorer({ enabled: true });
  feed(d, MIN_CREDITED_SECONDS + 2, { aDb: -20, bDb: null, aMidi: 60 });
  const v = d.verdict(
    { score: 40000, sections: [section(1, "主歌 1", 0.6, 400)] },
    { score: 0, sections: [section(1, "主歌 1", 0, 0)] });
  assert.equal(v.contested, false);
  assert.ok(v.sections, "單人結算也要有 sections 欄位，舞台端才不用寫兩套判斷");
  assert.equal(v.sections.contested_count, 0);
  assert.equal(v.sections.rows[0].main, "a");
});
