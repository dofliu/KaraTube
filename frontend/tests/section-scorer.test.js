/**
 * 段落評分的前端單元測試（node --test，不需要瀏覽器也不需要 npm 套件）。
 *
 * section-scorer.js 刻意寫成純資料邏輯就是為了能這樣測：
 * 「唱完告訴你哪一段最好、哪一段要練」這種判斷一旦算錯，在真實包廂裡是看不出來的
 * —— 沒人知道機器說的對不對，所以只能靠測試守。
 */
const test = require("node:test");
const assert = require("node:assert/strict");

const {
  SectionScorer,
  gradeForAccuracy,
  normalizeSections,
  MIN_SECTION_NOTE_FRAMES,
  MIN_SECTION_SPREAD,
} = require("../js/section-scorer.js");

// 典型的一首歌：前奏 / 主歌 1 / 間奏 / 副歌 1 / 主歌 2
// （前奏與間奏會被濾掉，所以正規化之後剩三段，中間 40~50 秒是空隙）
const SECTIONS = [
  { index: 0, kind: "intro", label: "前奏", start: 0, end: 8, preview: "" },
  { index: 1, kind: "verse", label: "主歌 1", start: 8, end: 40, preview: "第一句歌詞" },
  { index: 2, kind: "interlude", label: "間奏 1", start: 40, end: 50, preview: "" },
  { index: 3, kind: "chorus", label: "副歌 1", start: 50, end: 80, preview: "副歌第一句" },
  { index: 4, kind: "verse", label: "主歌 2", start: 82, end: 110, preview: "第二段歌詞" },
];

/** 餵 frames 幀進某個時間點，其中前 hits 幀唱準、前 perfects 幀幾乎全準。 */
function feed(scorer, time, frames, hits, perfects = 0) {
  for (let i = 0; i < frames; i++) {
    scorer.count(time, {
      hasNote: true,
      sang: true,
      hit: i < hits,
      perfect: i < perfects,
    });
  }
}

function rowByLabel(summary, label) {
  return summary.sections.find((s) => s.label === label);
}

// ----------------------------------------------------------------------
// 等級換算（整首總評與單段評語共用同一份門檻）
// ----------------------------------------------------------------------

test("gradeForAccuracy 在每個門檻上都取較高的等級", () => {
  assert.equal(gradeForAccuracy(1), "SSS");
  assert.equal(gradeForAccuracy(0.75), "SSS");
  assert.equal(gradeForAccuracy(0.749), "SS");
  assert.equal(gradeForAccuracy(0.6), "SS");
  assert.equal(gradeForAccuracy(0.45), "S");
  assert.equal(gradeForAccuracy(0.3), "A");
  assert.equal(gradeForAccuracy(0.15), "B");
  assert.equal(gradeForAccuracy(0.149), "C");
  assert.equal(gradeForAccuracy(0), "C");
});

test("gradeForAccuracy 遇到壞值退成最低等級而不是 undefined", () => {
  assert.equal(gradeForAccuracy(undefined), "C");
  assert.equal(gradeForAccuracy(null), "C");
  assert.equal(gradeForAccuracy(NaN), "C");
  assert.equal(gradeForAccuracy("abc"), "C");
});

// ----------------------------------------------------------------------
// 段落清單正規化
// ----------------------------------------------------------------------

test("normalizeSections 濾掉沒人唱的段落，只留主歌與副歌", () => {
  const kept = normalizeSections(SECTIONS);
  assert.deepEqual(kept.map((s) => s.label), ["主歌 1", "副歌 1", "主歌 2"]);
  assert.deepEqual(kept.map((s) => s.kind), ["verse", "chorus", "verse"]);
});

test("normalizeSections 保留後端日後新增的段落種類", () => {
  const kept = normalizeSections([
    { index: 0, kind: "bridge", label: "橋段", start: 10, end: 20 },
  ]);
  assert.equal(kept.length, 1);
  assert.equal(kept[0].kind, "bridge");
});

test("normalizeSections 丟掉時間軸壞掉與非物件的資料", () => {
  const kept = normalizeSections([
    null,
    "主歌 1",
    { kind: "verse", label: "零長度", start: 10, end: 10 },
    { kind: "verse", label: "倒著走", start: 30, end: 20 },
    { kind: "verse", label: "沒有時間" },
    { kind: "verse", label: "壞字串", start: "abc", end: 40 },
    { kind: "verse", label: "好的", start: 10, end: 20 },
  ]);
  assert.deepEqual(kept.map((s) => s.label), ["好的"]);
});

test("normalizeSections 依起點排序，並丟掉與前一段重疊的段落", () => {
  // 重疊會讓二分搜尋的前提失效（一個時間點對到兩段），寧可少評一段也不要對錯段
  const kept = normalizeSections([
    { kind: "verse", label: "後面的", start: 60, end: 90 },
    { kind: "verse", label: "前面的", start: 10, end: 40 },
    { kind: "chorus", label: "壓在前面的上面", start: 30, end: 70 },
  ]);
  assert.deepEqual(kept.map((s) => s.label), ["前面的", "後面的"]);
});

test("normalizeSections 沒給 index 就照順序補上", () => {
  const kept = normalizeSections([
    { kind: "verse", label: "A", start: 10, end: 20 },
    { kind: "chorus", label: "B", start: 20.5, end: 40 },
  ]);
  assert.deepEqual(kept.map((s) => s.index), [0, 1]);
});

test("normalizeSections 對空值與非陣列都回空清單", () => {
  assert.deepEqual(normalizeSections(undefined), []);
  assert.deepEqual(normalizeSections(null), []);
  assert.deepEqual(normalizeSections({}), []);
  assert.deepEqual(normalizeSections([]), []);
});

// ----------------------------------------------------------------------
// 時間 → 段落
// ----------------------------------------------------------------------

test("sectionIndexAt 找出時間落在哪一段，段落邊界算在段內", () => {
  const scorer = new SectionScorer(SECTIONS);
  assert.equal(scorer.sectionIndexAt(8), 0);      // 主歌 1 起點
  assert.equal(scorer.sectionIndexAt(25), 0);
  assert.equal(scorer.sectionIndexAt(40), 0);     // 主歌 1 終點
  assert.equal(scorer.sectionIndexAt(50), 1);     // 副歌 1 起點
  assert.equal(scorer.sectionIndexAt(100), 2);
});

test("sectionIndexAt 對段落之間、前奏、尾巴之外都回 -1", () => {
  const scorer = new SectionScorer(SECTIONS);
  assert.equal(scorer.sectionIndexAt(3), -1);     // 前奏（已濾掉）
  assert.equal(scorer.sectionIndexAt(45), -1);    // 間奏（已濾掉）
  assert.equal(scorer.sectionIndexAt(81), -1);    // 兩段之間的空隙
  assert.equal(scorer.sectionIndexAt(200), -1);   // 歌都唱完了
  assert.equal(scorer.sectionIndexAt(-1), -1);    // 字幕微調可能讓時間變負
  assert.equal(scorer.sectionIndexAt(NaN), -1);
  assert.equal(scorer.sectionIndexAt(undefined), -1);
});

test("沒有段落資料時 enabled 為 false，任何時間都找不到段落", () => {
  const scorer = new SectionScorer([]);
  assert.equal(scorer.enabled, false);
  assert.equal(scorer.sectionIndexAt(30), -1);
  assert.equal(scorer.count(30, { hasNote: true, hit: true }), -1);
  const summary = scorer.summary();
  assert.deepEqual(summary.sections, []);
  assert.equal(summary.best, null);
  assert.equal(summary.worst, null);
});

// ----------------------------------------------------------------------
// 逐幀累計
// ----------------------------------------------------------------------

test("count 把每一幀記進正確的段落並回傳段落編號", () => {
  const scorer = new SectionScorer(SECTIONS);
  assert.equal(scorer.count(20, { hasNote: true, sang: true, hit: true, perfect: true }), 0);
  assert.equal(scorer.count(60, { hasNote: true, sang: true, hit: false }), 1);

  const summary = scorer.summary(1);
  const verse = rowByLabel(summary, "主歌 1");
  const chorus = rowByLabel(summary, "副歌 1");
  assert.deepEqual(
    [verse.note_frames, verse.hit_frames, verse.perfect_frames, verse.sang_frames],
    [1, 1, 1, 1]);
  assert.deepEqual(
    [chorus.note_frames, chorus.hit_frames, chorus.perfect_frames, chorus.sang_frames],
    [1, 0, 0, 1]);
});

test("落在段落之間的幀不計入任何段落", () => {
  const scorer = new SectionScorer(SECTIONS);
  feed(scorer, 45, 40, 40);   // 間奏：使用者亂哼也不該算進主歌或副歌
  const summary = scorer.summary(1);
  assert.deepEqual(summary.sections.map((s) => s.note_frames), [0, 0, 0]);
  assert.equal(summary.graded_count, 0);
});

test("沒有導唱音符的幀不算進分母，但有唱到就記進發聲幀", () => {
  const scorer = new SectionScorer(SECTIONS);
  for (let i = 0; i < 50; i++) {
    scorer.count(20, { hasNote: false, sang: true, hit: false });
  }
  const verse = rowByLabel(scorer.summary(1), "主歌 1");
  assert.equal(verse.note_frames, 0);
  assert.equal(verse.sang_frames, 50);
  assert.equal(verse.accuracy, 0);
  assert.equal(verse.graded, false);
});

test("count 對 outcome 傳空物件或未傳都不會炸", () => {
  const scorer = new SectionScorer(SECTIONS);
  assert.equal(scorer.count(20, {}), 0);
  assert.equal(scorer.count(20), 0);
  const verse = rowByLabel(scorer.summary(1), "主歌 1");
  assert.equal(verse.note_frames, 0);
});

test("reset 與 setSections 都會歸零統計（換歌、重唱不能疊到上一輪）", () => {
  const scorer = new SectionScorer(SECTIONS);
  feed(scorer, 20, 100, 80);
  assert.equal(rowByLabel(scorer.summary(), "主歌 1").hit_frames, 80);

  scorer.reset();
  assert.equal(rowByLabel(scorer.summary(), "主歌 1").hit_frames, 0);

  feed(scorer, 20, 100, 80);
  scorer.setSections(SECTIONS);
  assert.equal(rowByLabel(scorer.summary(), "主歌 1").hit_frames, 0);
});

// ----------------------------------------------------------------------
// 結算：最佳／待加強段落
// ----------------------------------------------------------------------

test("summary 算出每段命中率並點名最佳與待加強段落", () => {
  const scorer = new SectionScorer(SECTIONS);
  feed(scorer, 20, 200, 160);   // 主歌 1：80%
  feed(scorer, 60, 200, 60);    // 副歌 1：30%
  feed(scorer, 90, 200, 120);   // 主歌 2：60%

  const summary = scorer.summary();
  assert.deepEqual(summary.sections.map((s) => s.accuracy), [0.8, 0.3, 0.6]);
  assert.deepEqual(summary.sections.map((s) => s.grade), ["SSS", "A", "SS"]);
  assert.equal(summary.graded_count, 3);
  assert.equal(summary.best.label, "主歌 1");
  assert.equal(summary.worst.label, "副歌 1");
});

test("summary 依歌曲順序回傳段落，不是依分數排序", () => {
  const scorer = new SectionScorer(SECTIONS);
  feed(scorer, 20, 100, 10);
  feed(scorer, 60, 100, 90);
  feed(scorer, 90, 100, 50);
  const summary = scorer.summary();
  assert.deepEqual(summary.sections.map((s) => s.label), ["主歌 1", "副歌 1", "主歌 2"]);
  assert.deepEqual(summary.sections.map((s) => s.index), [1, 3, 4]);
});

test("導唱音符太少的段落不參加評分，也不會被點名", () => {
  const scorer = new SectionScorer(SECTIONS);
  feed(scorer, 20, 200, 100);                          // 主歌 1：50%，夠長
  feed(scorer, 60, 200, 180);                          // 副歌 1：90%，夠長
  feed(scorer, 90, MIN_SECTION_NOTE_FRAMES - 1, 0);    // 主歌 2：只有幾幀的 0%

  const summary = scorer.summary();
  assert.equal(summary.graded_count, 2);
  assert.equal(rowByLabel(summary, "主歌 2").graded, false);
  assert.equal(summary.best.label, "副歌 1");
  assert.equal(summary.worst.label, "主歌 1");   // 不是那個只唱幾幀的主歌 2
});

test("可評分的段落不到兩段就不點名（一段沒有比較的意義）", () => {
  const scorer = new SectionScorer(SECTIONS);
  feed(scorer, 20, 200, 20);
  const summary = scorer.summary();
  assert.equal(summary.graded_count, 1);
  assert.equal(summary.best, null);
  assert.equal(summary.worst, null);
});

test("整首表現太平均時不點名，免得無意義地嫌棄某一段", () => {
  const scorer = new SectionScorer(SECTIONS);
  feed(scorer, 20, 1000, 550);   // 55.0%
  feed(scorer, 60, 1000, 540);   // 54.0%
  feed(scorer, 90, 1000, 530);   // 53.0% —— 全距 0.02 < 門檻

  const summary = scorer.summary();
  assert.equal(summary.graded_count, 3);
  assert.ok(MIN_SECTION_SPREAD > 0.02);
  assert.equal(summary.best, null);
  assert.equal(summary.worst, null);
});

test("差距剛好到門檻就點名", () => {
  const scorer = new SectionScorer(SECTIONS);
  feed(scorer, 20, 1000, 600);   // 60%
  feed(scorer, 60, 1000, 550);   // 55% —— 全距剛好 0.05
  const summary = scorer.summary();
  assert.equal(summary.best.label, "主歌 1");
  assert.equal(summary.worst.label, "副歌 1");
});

test("同分時最佳取先唱到的那一段，結果穩定不隨排序實作而變", () => {
  const scorer = new SectionScorer(SECTIONS);
  feed(scorer, 20, 100, 90);   // 主歌 1：90%
  feed(scorer, 60, 100, 90);   // 副歌 1：90%（與主歌 1 同分）
  feed(scorer, 90, 100, 10);   // 主歌 2：10%
  const summary = scorer.summary();
  assert.equal(summary.best.label, "主歌 1");
  assert.equal(summary.worst.label, "主歌 2");
});

test("練唱模式重複唱同一段時，命中率是整段累計的比例", () => {
  const scorer = new SectionScorer(SECTIONS);
  // A-B 循環把副歌唱了兩輪：第一輪很差、第二輪練起來了
  feed(scorer, 60, 100, 20);
  feed(scorer, 60, 100, 80);
  feed(scorer, 20, 100, 50);
  const chorus = rowByLabel(scorer.summary(), "副歌 1");
  assert.equal(chorus.note_frames, 200);
  assert.equal(chorus.accuracy, 0.5);
});

test("summary 帶上段落的時間與首句預覽，前端才能標示是哪一段", () => {
  const scorer = new SectionScorer(SECTIONS);
  const chorus = rowByLabel(scorer.summary(1), "副歌 1");
  assert.equal(chorus.start, 50);
  assert.equal(chorus.end, 80);
  assert.equal(chorus.preview, "副歌第一句");
  assert.equal(chorus.kind, "chorus");
});

test("命中率四捨五入到小數三位，不會漏出浮點雜訊", () => {
  const scorer = new SectionScorer(SECTIONS);
  feed(scorer, 20, 300, 100);   // 1/3
  const verse = rowByLabel(scorer.summary(), "主歌 1");
  assert.equal(verse.accuracy, 0.333);
});
