/**
 * 歌詞拼音標注的版面規則（node --test）。
 *
 * 這一層錯掉的後果都在那塊大螢幕上，而且都是「看起來正常」的那一種：
 * 字級算太大會讓相鄰兩個字的拼音黏成一條字母流（使用者會停下來解讀它，
 * 而他停下來的那一秒正是要唱的那一秒）；長度比對漏掉會讓整行拼音
 * 往後位移一個字（畫面完全正常，只有看得懂的人才發現全錯）。
 *
 * 標什麼、標不標得準那一半在 backend/services/ruby.py 與 tests/test_ruby.py。
 */
const test = require("node:test");
const assert = require("node:assert/strict");

const {
  RUBY_MAX_PX,
  RUBY_MIN_PX,
  RUBY_MODES,
  DEFAULT_RUBY_MODE,
  coerceMode,
  rubyVisible,
  rubyFontSize,
  normalizeDoc,
  rubyForLine,
} = require("../js/ruby-layout.js");

// 舞台上一個全形字的格子寬度（46px 字 + 字距 + margin）。
const SLOT = 50;

function doc(lines, extra) {
  return Object.assign({ version: 1, style: "pinyin", language: "mandarin",
                         available: true, lines }, extra || {});
}

// --- 顯示模式 ---

test("模式只有三個，而且認不得的一律退回預設", () => {
  assert.deepEqual(RUBY_MODES, ["off", "auto", "on"]);
  assert.equal(coerceMode("ON"), "on");
  assert.equal(coerceMode(" auto "), "auto");
  for (const junk of [null, undefined, "", "yes", 7, {}]) {
    assert.equal(coerceMode(junk), DEFAULT_RUBY_MODE);
  }
});

test("auto 跟著舞台語言走：中文介面不顯示，英日文顯示", () => {
  assert.equal(rubyVisible("auto", "zh-TW", true), false);
  assert.equal(rubyVisible("auto", "zh-Hant", true), false);
  // 簡體中文的舞台也不標：看得懂那幾個字的人讀拼音只會慢半拍
  assert.equal(rubyVisible("auto", "zh-CN", true), false);
  assert.equal(rubyVisible("auto", "en", true), true);
  assert.equal(rubyVisible("auto", "ja", true), true);
});

test("off 一律不顯示，on 一律顯示", () => {
  assert.equal(rubyVisible("off", "en", true), false);
  assert.equal(rubyVisible("on", "zh-TW", true), true);
});

test("這首歌算不出拼音時，連 on 都不顯示", () => {
  // 日語歌、英文歌沒有東西可以顯示。這時候 on 還硬開的話，
  // 版面會為一行永遠空白的拼音保留一整排高度。
  assert.equal(rubyVisible("on", "en", false), false);
  assert.equal(rubyVisible("auto", "en", false), false);
});

// --- 字級 ---

test("短音節用上限，長音節縮小", () => {
  assert.equal(rubyFontSize("è", SLOT), RUBY_MAX_PX);
  assert.equal(rubyFontSize("wǒ", SLOT), RUBY_MAX_PX);
  const long = rubyFontSize("zhuāng", SLOT);
  assert.ok(long > 0 && long < RUBY_MAX_PX, `zhuāng -> ${long}`);
});

test("字級隨音節變長而單調不增", () => {
  // 不單調的話，同一行裡會出現「比較長的那個反而比較大」——
  // 那在畫面上讀起來像是某幾個字被特別標記了。
  let prev = Infinity;
  for (const syl of ["a", "ai", "zhu", "zhua", "zhuang", "zhuangx"]) {
    const px = rubyFontSize(syl, SLOT);
    if (px > 0) assert.ok(px <= prev, `${syl}: ${px} > ${prev}`);
    if (px > 0) prev = px;
  }
});

test("縮到下限還放不下就不標（回 0）", () => {
  // 窄到不可能放下的格子：寧可空白，也不要一團看不清楚的字母。
  assert.equal(rubyFontSize("zhuāng", 8), 0);
  assert.ok(rubyFontSize("zhuāng", 8) < RUBY_MIN_PX);
});

test("沒有音節、沒有寬度一律回 0，不丟例外", () => {
  for (const [syl, slot] of [["", SLOT], [null, SLOT], ["wǒ", 0],
                             ["wǒ", -5], ["wǒ", NaN], ["wǒ", null]]) {
    assert.equal(rubyFontSize(syl, slot), 0);
  }
});

test("字級是整數 px", () => {
  // 半格的字級在不同瀏覽器上會 round 到不同邊，而「同一首歌在兩台機器上
  // 長得不一樣」是查不出原因的那種回報。
  for (const syl of ["a", "ai", "zhu", "zhuang"]) {
    const px = rubyFontSize(syl, SLOT);
    assert.equal(px, Math.floor(px));
  }
});

// --- 文件正規化：對不上就整份不用 ---

test("認不得的文件一律回 null", () => {
  for (const junk of [null, undefined, "ruby", 0, {}, { available: true },
                      { available: true, lines: "nope" }]) {
    assert.equal(normalizeDoc(junk, 2), null);
  }
});

test("available: false 的文件沒有東西可以畫", () => {
  assert.equal(normalizeDoc(doc([["wǒ"]], { available: false }), 1), null);
});

test("行數對不上整份不用", () => {
  // 行數對不上代表這份拼音算的是另一份歌詞（重算歌詞之後的殘留檔），
  // 而它的症狀是整首歌的拼音都標在別的字上面。
  assert.equal(normalizeDoc(doc([["wǒ"], ["ài"]]), 3), null);
  assert.deepEqual(normalizeDoc(doc([["wǒ"], ["ài"]]), 2), [["wǒ"], ["ài"]]);
});

// --- 逐行：長度對不上就整行不標 ---

test("字數對得上才給那一行", () => {
  const rows = [["wǒ", "ài", "nǐ"]];
  assert.deepEqual(rubyForLine(rows, 0, 3), ["wǒ", "ài", "nǐ"]);
  assert.equal(rubyForLine(rows, 0, 4), null);
  assert.equal(rubyForLine(rows, 0, 2), null);
});

test("取不到的行回 null，不丟例外", () => {
  assert.equal(rubyForLine([["wǒ"]], 5, 1), null);
  assert.equal(rubyForLine([["wǒ"]], -1, 1), null);
  assert.equal(rubyForLine(null, 0, 1), null);
  assert.equal(rubyForLine([null], 0, 1), null);
});

test("整行都沒有讀音就當作沒有（純英文的那幾句）", () => {
  // 回 null 讓呼叫端連空的 DOM 節點都不要建。
  assert.equal(rubyForLine([["", "", ""]], 0, 3), null);
  assert.deepEqual(rubyForLine([["", "ài", ""]], 0, 3), ["", "ài", ""]);
});

// --- 跟伺服器那一半對齊 ---

test("模式清單跟 backend/services/ruby.py 是同一份", () => {
  const fs = require("node:fs");
  const path = require("node:path");
  const src = fs.readFileSync(
    path.join(__dirname, "..", "..", "backend", "services", "ruby.py"), "utf8");
  const m = src.match(/RUBY_MODE_CHOICES = \(([^)]*)\)/);
  assert.ok(m, "找不到 RUBY_MODE_CHOICES");
  const codes = m[1].split(",").map(s => s.trim().replace(/^"|"$/g, "")).filter(Boolean);
  assert.deepEqual(codes, RUBY_MODES);
  const d = src.match(/DEFAULT_RUBY_MODE = "([^"]+)"/);
  assert.ok(d, "找不到 DEFAULT_RUBY_MODE");
  assert.equal(d[1], DEFAULT_RUBY_MODE);
});
