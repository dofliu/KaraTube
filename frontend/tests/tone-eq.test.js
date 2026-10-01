/**
 * 音色等化器純邏輯的前端單元測試（node --test）。
 *
 * 這一層錯掉的後果都在喇叭上：補償算錯是破音或忽然變小聲，
 * 夾限漏掉是舊版前端送一個 +40 進來就把限幅器打爆。
 * 兩種症狀在包廂裡都會被講成「機器壞了」，而且沒有人會聯想到等化器。
 */
const test = require("node:test");
const assert = require("node:assert/strict");

const {
  EQ_PRESETS,
  MAKEUP_MIN_DB,
  MAKEUP_MAX_DB,
  targetSpec,
  clampBand,
  normalizeTone,
  isFlat,
  broadbandDb,
  makeupDb,
  makeupGain,
  bandSpecs,
  feedbackRisk,
  matchPreset,
  presetTone,
  formatDb,
  toneSummary,
} = require("../js/tone-eq.js");

// --- 夾限與正規化 ---

test("夾限：超出範圍夾回上下限，不是丟例外", () => {
  assert.equal(clampBand(99, 12), 12);
  assert.equal(clampBand(-99, 12), -12);
  assert.equal(clampBand(5, 12), 5);
});

test("夾限：認不得的值一律當 0（沒調），舊版前端送什麼都不會壞", () => {
  assert.equal(clampBand(undefined, 12), 0);
  assert.equal(clampBand(null, 12), 0);
  assert.equal(clampBand("abc", 12), 0);
  assert.equal(clampBand(NaN, 12), 0);
});

test("夾限：吸附到半格", () => {
  assert.equal(clampBand(3.26, 12), 3.5);
  assert.equal(clampBand(3.1, 12), 3);
  assert.equal(clampBand(-0.3, 12), -0.5);
});

test("夾限：字串數字照樣收（表單送上來的都是字串）", () => {
  assert.equal(clampBand("4", 12), 4);
  assert.equal(clampBand("-20", 12), -12);
});

test("正規化：缺的欄位補 0，不是沿用舊值", () => {
  assert.deepEqual(normalizeTone({ bass: 3 }, "mic"), { bass: 3, mid: 0, treble: 0 });
  assert.deepEqual(normalizeTone(null, "mic"), { bass: 0, mid: 0, treble: 0 });
  assert.deepEqual(normalizeTone("nope", "mic"), { bass: 0, mid: 0, treble: 0 });
});

test("兩套的範圍不一樣：麥克風 ±12、音樂 ±8", () => {
  assert.equal(targetSpec("mic").limitDb, 12);
  assert.equal(targetSpec("music").limitDb, 8);
  assert.equal(normalizeTone({ bass: 20 }, "mic").bass, 12);
  assert.equal(normalizeTone({ bass: 20 }, "music").bass, 8);
});

test("認不得的目標當成麥克風那一套，不回 undefined 讓呼叫端整頁壞掉", () => {
  assert.equal(targetSpec("nope").limitDb, 12);
  assert.deepEqual(normalizeTone({ treble: 99 }, "nope"), { bass: 0, mid: 0, treble: 12 });
});

test("三段都 0 就是原音", () => {
  assert.equal(isFlat({ bass: 0, mid: 0, treble: 0 }), true);
  assert.equal(isFlat({ bass: 0, mid: 0.5, treble: 0 }), false);
  assert.equal(isFlat({}), true);
});

// --- 補償 ---

test("沒調就不補償：一顆都沒推的時候增益是 1.0", () => {
  assert.equal(makeupDb({ bass: 0, mid: 0, treble: 0 }, "mic"), 0);
  assert.equal(makeupGain({ bass: 0, mid: 0, treble: 0 }, "mic"), 1);
});

test("提升要往下補：三段都 +6 的淨增益是 +6，補償就是 −6", () => {
  const tone = { bass: 6, mid: 6, treble: 6 };
  assert.equal(broadbandDb(tone, "mic"), 6);   // 權重加總是 1
  assert.equal(makeupDb(tone, "mic"), -6);
  assert.ok(makeupGain(tone, "mic") < 1);
});

test("權重加總是 1：不然「三段都 +N」就補不回 N", () => {
  for (const target of ["mic", "music"]) {
    const w = targetSpec(target).weights;
    const sum = w.bass + w.mid + w.treble;
    assert.ok(Math.abs(sum - 1) < 1e-9, `${target} 的權重加總是 ${sum}`);
  }
});

test("麥克風的中音權重最大：人聲的能量在中頻", () => {
  const w = targetSpec("mic").weights;
  assert.ok(w.mid > w.bass && w.mid > w.treble);
});

test("補償是不對稱的：削減最多只補回 +3 dB", () => {
  const cut = { bass: -12, mid: -12, treble: -12 };
  assert.equal(broadbandDb(cut, "mic"), -12);
  // 原封不動補回 +12 的話，「把高音砍掉壓嘯叫」就完全沒有效果
  assert.equal(makeupDb(cut, "mic"), MAKEUP_MAX_DB);
});

test("補償往下有底：極端提升也不會補到無聲", () => {
  const boost = { bass: 12, mid: 12, treble: 12 };
  assert.equal(makeupDb(boost, "mic"), MAKEUP_MIN_DB);
});

test("補償只看設定值，不看訊號：同一組值永遠算出同一個數字", () => {
  const tone = { bass: 4, mid: -2, treble: 5 };
  const first = makeupDb(tone, "mic");
  for (let i = 0; i < 5; i++) assert.equal(makeupDb(tone, "mic"), first);
});

test("補償用的是夾限後的值：送 +99 進來不會算出一個誇張的補償", () => {
  assert.equal(makeupDb({ bass: 99, mid: 99, treble: 99 }, "mic"),
               makeupDb({ bass: 12, mid: 12, treble: 12 }, "mic"));
});

// --- 濾波器參數 ---

test("三段的濾波器型別：低音與高音是 shelf、中音是 peaking", () => {
  const specs = bandSpecs({ bass: 3, mid: -1, treble: 2 }, "mic");
  assert.equal(specs.length, 3);
  assert.equal(specs[0].type, "lowshelf");
  assert.equal(specs[1].type, "peaking");
  assert.equal(specs[2].type, "highshelf");
  assert.deepEqual(specs.map((s) => s.gainDb), [3, -1, 2]);
});

test("麥克風的高音不跟既有的高頻柔化（5.5kHz）放在同一點", () => {
  const treble = bandSpecs({}, "mic")[2];
  assert.ok(treble.freq < 5500, "疊在一起的話兩顆旋鈕會互相抵消");
});

test("音樂的低音比麥克風的低：鼓與貝斯不是人聲的胸腔", () => {
  assert.ok(bandSpecs({}, "music")[0].freq < bandSpecs({}, "mic")[0].freq);
});

// --- 嘯叫風險提示 ---

test("沒推中高音就不示警（示警多了就沒有人看）", () => {
  assert.equal(feedbackRisk({ bass: 0, mid: 0, treble: 0 }, "mic").level, "safe");
  assert.equal(feedbackRisk({ bass: 12, mid: 0, treble: 0 }, "mic").level, "safe");
});

test("「厚實」那一組預設不示警", () => {
  const warm = EQ_PRESETS.mic.find((p) => p.id === "warm");
  assert.equal(feedbackRisk(warm, "mic").level, "safe");
});

test("高音推上去會示警，而且說得出下一步", () => {
  const watch = feedbackRisk({ bass: 0, mid: 0, treble: 5 }, "mic");
  assert.equal(watch.level, "watch");
  assert.ok(watch.message.length > 0);

  const risky = feedbackRisk({ bass: 0, mid: 6, treble: 8 }, "mic");
  assert.equal(risky.level, "risky");
  assert.ok(risky.message.includes("高音"));
});

test("削減不會示警：負值不加進風險分", () => {
  assert.equal(feedbackRisk({ bass: 0, mid: -12, treble: -12 }, "mic").level, "safe");
});

test("音樂那一套不講嘯叫：它不在麥克風的回授迴路裡", () => {
  assert.equal(feedbackRisk({ bass: 8, mid: 8, treble: 8 }, "music").level, "safe");
});

// --- 一鍵音色 ---

test("每一組預設都在自己那一套的範圍內", () => {
  for (const target of ["mic", "music"]) {
    const limit = targetSpec(target).limitDb;
    for (const p of EQ_PRESETS[target]) {
      for (const k of ["bass", "mid", "treble"]) {
        assert.ok(Math.abs(p[k]) <= limit, `${target}/${p.id} 的 ${k} 超出 ±${limit}`);
      }
    }
  }
});

test("每一組預設都有一句「什麼時候用」", () => {
  for (const target of ["mic", "music"]) {
    for (const p of EQ_PRESETS[target]) {
      assert.ok(p.label && p.note, `${target}/${p.id} 少了說明`);
    }
  }
});

test("套用預設之後認得出是哪一顆", () => {
  for (const target of ["mic", "music"]) {
    for (const p of EQ_PRESETS[target]) {
      assert.equal(matchPreset(presetTone(p.id, target), target), p.id);
    }
  }
});

test("自己推出來的值是 custom，不會硬套到某一顆上", () => {
  assert.equal(matchPreset({ bass: 1, mid: -3, treble: 2 }, "mic"), "custom");
});

test("認不得的預設 id 回原音，不是 null", () => {
  assert.deepEqual(presetTone("nope", "mic"), { bass: 0, mid: 0, treble: 0 });
});

test("原音那一組是三個 0：乾聲／歸零都靠它", () => {
  assert.deepEqual(presetTone("flat", "mic"), { bass: 0, mid: 0, treble: 0 });
  assert.deepEqual(presetTone("flat", "music"), { bass: 0, mid: 0, treble: 0 });
});

// --- 顯示 ---

test("數字顯示：0 不帶正負號、正值帶 +、負值用真的減號", () => {
  assert.equal(formatDb(0), "0");
  assert.equal(formatDb(3), "+3");
  assert.equal(formatDb(-3), "−3");
  assert.equal(formatDb(2.5), "+2.5");
});

test("說明文字：原音就講原音，不要印三個 0", () => {
  assert.equal(toneSummary({ bass: 0, mid: 0, treble: 0 }, "mic"), "原音（等化器沒有作用）");
});

test("說明文字：有調就同時講出三段與補償量", () => {
  const text = toneSummary({ bass: 4, mid: 0, treble: -2 }, "mic");
  assert.ok(text.includes("低音 +4"));
  assert.ok(text.includes("中音 0"));
  assert.ok(text.includes("高音 −2"));
  assert.ok(text.includes("補"));
});

// --- 畫面上的滑桿與這裡的範圍釘在一起 ---
//
// 滑桿推得到的位置比伺服器收得下的多，症狀是「推到底，放手之後跳回去」——
// 使用者的結論是這根滑桿壞了，而原因在另外兩個檔案裡的兩個數字。
const fs = require("node:fs");
const path = require("node:path");

const INDEX_HTML = fs.readFileSync(
  path.join(__dirname, "..", "index.html"), "utf8");

function sliderRange(id) {
  const re = new RegExp(`id="${id}"[^>]*`);
  const tag = INDEX_HTML.match(re);
  assert.ok(tag, `點歌台上找不到 ${id}`);
  const min = tag[0].match(/min="(-?[0-9.]+)"/);
  const max = tag[0].match(/max="(-?[0-9.]+)"/);
  assert.ok(min && max, `${id} 少了 min/max`);
  return { min: Number(min[1]), max: Number(max[1]) };
}

test("點歌台的等化器滑桿範圍 = tone-eq.js 的上限", () => {
  const cases = [
    ["micEqBassSlider", "mic"], ["micEqMidSlider", "mic"], ["micEqTrebleSlider", "mic"],
    ["musicEqBassSlider", "music"], ["musicEqMidSlider", "music"],
    ["musicEqTrebleSlider", "music"],
  ];
  for (const [id, target] of cases) {
    const limit = targetSpec(target).limitDb;
    assert.deepEqual(sliderRange(id), { min: -limit, max: limit }, id);
  }
});

test("點歌台的等化器滑桿步進 = 半格（跟夾限吸附的格子一樣）", () => {
  for (const id of ["micEqBassSlider", "musicEqTrebleSlider"]) {
    const tag = INDEX_HTML.match(new RegExp(`id="${id}"[^>]*`))[0];
    assert.match(tag, /step="0\.5"/, `${id} 的步進跟夾限吸附的格子對不上`);
  }
});
