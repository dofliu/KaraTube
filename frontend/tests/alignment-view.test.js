/**
 * 歌詞對齊品質的說法（node --test）
 *
 * 守的是三個最容易把使用者帶錯路的判讀：
 *   * 沒有診斷檔 ≠ 0 分（舊歌一律沒有，畫成紅色會讓人去重算一堆本來就對的歌）
 *   * source=whisper 的 score 恆為 0，那是「沒有 LRC 可對」不是「對得爛」
 *   * placeholder 才是真的沒有歌詞
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const AV = require("../js/alignment-view.js");

test("信任門檻與後端釘在一起", () => {
  const py = fs.readFileSync(path.join(__dirname, "..", "..", "backend", "pipeline",
                                       "lyrics_aligner.py"), "utf8");
  const min = Number(/MIN_TRUST_SCORE = ([\d.]+)/.exec(py)[1]);
  assert.equal(AV.ALIGN_MIN_TRUST_SCORE, min);
});

test("沒有診斷檔＝未知，不是 0 分", () => {
  const badge = AV.alignmentBadge(null);
  assert.equal(badge.label, "對齊未知");
  assert.equal(badge.tone, "muted");               // 灰的，不是紅的
  assert.match(badge.title, /之前處理的/);
  assert.ok(badge.rebuildHint);                    // 要說得出「按重算就會有」
});

test("LRC + 高分＝良好", () => {
  const badge = AV.alignmentBadge({ source: "lrc", score: 0.577, recall: 0.967,
                                    lines: 47, track: "稻香 / 周杰倫" });
  assert.equal(badge.label, "對齊良好");
  assert.equal(badge.tone, "ok");
  assert.match(badge.title, /稻香/);               // 對到哪一首要看得到
  assert.match(badge.title, /0\.577/);             // 原始數字退到 tooltip
  assert.match(badge.title, /97%/);                // 人聲覆蓋率
  assert.equal(badge.rebuildHint, "");             // 沒問題就不要慫恿人重算
});

test("LRC + 低分＝勉強／可疑，而且說得出下一步", () => {
  const weak = AV.alignmentBadge({ source: "lrc", score: 0.31, lines: 40 });
  assert.equal(weak.label, "對齊勉強");
  assert.equal(weak.tone, "warn");
  assert.match(weak.rebuildHint, /不同版本/);

  const bad = AV.alignmentBadge({ source: "lrc", score: 0.12, lines: 40 });
  assert.equal(bad.label, "對齊可疑");
  assert.equal(bad.tone, "bad");
  assert.match(bad.rebuildHint, /別首歌/);
});

test("whisper 的 0 分不能畫成「對得很爛」", () => {
  const badge = AV.alignmentBadge({ source: "whisper", score: 0.0, lines: 30 });
  assert.equal(badge.label, "聽寫字幕");
  assert.equal(badge.tone, "warn");
  assert.match(badge.title, /找不到可信的歌詞檔/);
  assert.ok(!badge.title.includes("分數"));        // 講分數就是誤導
});

test("placeholder 才是真的沒有歌詞", () => {
  const badge = AV.alignmentBadge({ source: "placeholder", score: 0.0, lines: 2 });
  assert.equal(badge.label, "沒有歌詞");
  assert.equal(badge.tone, "bad");
});

test("缺欄位不會吐出 undefined", () => {
  for (const a of [{}, { source: "lrc" }, { source: "lrc", score: "x" },
                   { source: "???" }, { source: "lrc", score: 0.5, recall: "x" }]) {
    const badge = AV.alignmentBadge(a);
    assert.ok(badge.label && !badge.title.includes("undefined"));
  }
});

// --- 能不能重算 ---

test("缺人聲軌就不能重算，而且要說得出為什麼", () => {
  const r = AV.canRebuildLyrics({ complete: false, missing_files: ["vocals.mp3", "lyrics.json"] });
  assert.equal(r.can, false);
  assert.match(r.reason, /重新處理/);
});

test("完整的歌可以重算", () => {
  assert.equal(AV.canRebuildLyrics({ complete: true, missing_files: [] }).can, true);
  assert.equal(AV.canRebuildLyrics({}).can, true);   // 資料不全時預設讓他試
});

// --- 確認與結果文案 ---

test("重算確認要講滿四件事", () => {
  const text = AV.rebuildConfirmText({
    title: "稻香", songId: "abc", offsetMs: 300,
    alignment: { source: "lrc", score: 0.2 },
  });
  assert.match(text, /稻香/);
  assert.match(text, /不重新下載/);      // 便宜在哪
  assert.match(text, /下一次播放/);      // 何時生效
  assert.match(text, /趨勢/);            // 會弄壞什麼
  assert.match(text, /300 ms/);          // 手動校正會被清掉
  assert.match(text, /對齊可疑/);        // 現在的狀態
});

test("沒有手動校正就不提那一句", () => {
  const text = AV.rebuildConfirmText({ title: "稻香", offsetMs: 0 });
  assert.ok(!text.includes("手動校正"));
});

test("重算結果要把新舊分數一起講", () => {
  const msg = AV.rebuildResultMessage(
    { source: "lrc", score: 0.21 }, { source: "lrc", score: 0.812 }, 300);
  assert.match(msg, /對齊可疑 → 對齊良好/);
  assert.match(msg, /0\.210 → 0\.812/);
  assert.match(msg, /手動字幕偏移已清掉/);
  assert.match(msg, /下次播放/);
});

test("從沒有診斷變成有，也講得出來", () => {
  const msg = AV.rebuildResultMessage(null, { source: "whisper", score: 0 }, 0);
  assert.match(msg, /對齊未知 → 聽寫字幕/);
  assert.ok(!msg.includes("undefined"));
});


// --- 自備歌詞（本機匯入時放在檔案旁邊的那份 .lrc）---

test("自備歌詞要標明來源，而且下一步跟線上歌詞不一樣", () => {
  // 重算只會再讀同一份檔案，所以修法是去換掉那個 .lrc。
  // 不講這一句的話，使用者會對著同一首歌按五次重算，每次結果都一樣。
  const badge = AV.alignmentBadge({ source: "lrc_local", score: 0.62, lines: 40 });
  assert.equal(badge.label, "自備歌詞");
  assert.equal(badge.tone, "ok");
  assert.match(badge.rebuildHint, /同一份 \.lrc|換掉/);
});

test("自備歌詞對不上時要說「那份歌詞可能是別的版本」，不是說機器壞了", () => {
  const badge = AV.alignmentBadge({ source: "lrc_local", score: 0.05, lines: 40 });
  assert.equal(badge.tone, "bad");
  assert.match(badge.title, /別的版本|對不太上/);
});
