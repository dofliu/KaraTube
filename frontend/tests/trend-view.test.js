/**
 * 跨場次段落趨勢呈現的前端單元測試（node --test）。
 *
 * 這一層的錯誤特別難在包廂裡發現：畫面上那句話永遠「看起來很合理」，
 * 只有把同一份資料跟後端的結論對起來才知道講反了沒。
 */
const test = require("node:test");
const assert = require("node:assert/strict");

const {
  describeTrend,
  trendRows,
  formatDelta,
  directionText,
  MAX_TREND_ROWS,
} = require("../js/trend-view.js");

function okTrend(extra = {}) {
  return Object.assign({
    status: "ok",
    performances: 5,
    sections: [
      { label: "主歌 1", mean_delta: 0.12, mean_accuracy: 0.72, appearances: 5, named: true },
      { label: "副歌 1", mean_delta: -0.09, mean_accuracy: 0.51, appearances: 5, named: true },
    ],
    home: { label: "主歌 1", mean_delta: 0.12 },
    weak: { label: "副歌 1", mean_delta: -0.09 },
    direction: "steady",
    delta_accuracy: 0.01,
  }, extra);
}

test("沒有資料時整塊不顯示", () => {
  assert.equal(describeTrend({ status: "none" }).kind, "none");
  assert.equal(describeTrend(null).kind, "none");
  assert.equal(describeTrend(undefined).headline, "");
});

test("場次不夠時講「再唱幾次」，不是沉默", () => {
  const d = describeTrend({ status: "insufficient", performances: 1, needed: 2 });
  assert.equal(d.kind, "waiting");
  assert.match(d.headline, /再唱 2 次/);
  assert.match(d.detail, /1 次/);
});

test("主場與弱點都講成正數的百分點", () => {
  const d = describeTrend(okTrend());
  assert.equal(d.kind, "verdict");
  assert.match(d.headline, /近 5 次/);
  assert.match(d.headline, /主場 主歌 1（一向高 12 個百分點）/);
  // 弱點講「低 9」而不是「高 −9」，而且句子裡不帶正負號 ——
  // 方向已經由「高」／「低」講完，「一向低 +9」要讀兩次才看得懂
  assert.match(d.headline, /待加強 副歌 1（一向低 9 個百分點）/);
  assert.doesNotMatch(d.headline, /[+−]/);
});

test("唱得平均是好消息，要明說而不是留白", () => {
  const d = describeTrend(okTrend({ home: null, weak: null }));
  assert.equal(d.kind, "flat");
  assert.match(d.headline, /沒有明顯弱點/);
});

test("只點得出一邊時就只講一邊", () => {
  const d = describeTrend(okTrend({ weak: null }));
  assert.match(d.headline, /主場/);
  assert.doesNotMatch(d.headline, /待加強/);
});

test("進步／退步判不出來就不講", () => {
  assert.equal(directionText({ direction: null, delta_accuracy: null }), "");
  assert.match(directionText({ direction: "improving", delta_accuracy: 0.08 }), /進步了 8/);
  assert.match(directionText({ direction: "slipping", delta_accuracy: -0.08 }), /退了 8/);
  assert.match(directionText({ direction: "steady", delta_accuracy: 0.01 }), /穩定/);
});

test("負號用得出得來的減號，不是連字號", () => {
  assert.equal(formatDelta(0.123), "+12");
  assert.equal(formatDelta(-0.094), "−9");
  // 四捨五入到 0 的那一格寫「±0」：Math.round 往 +∞ 捨，
  // 不特別處理的話往下偏的 −0.4 個百分點會顯示成「+0」
  assert.equal(formatDelta(0), "±0");
  assert.equal(formatDelta(-0.004), "±0");
  assert.equal(formatDelta(0.004), "±0");
});

test("長條圖照歌曲順序、依最大偏差正規化", () => {
  const rows = trendRows(okTrend());
  assert.deepEqual(rows.map((r) => r.label), ["主歌 1", "副歌 1"]);
  assert.equal(rows[0].side, "home");
  assert.equal(rows[1].side, "weak");
  assert.equal(rows[0].ratio, 1);            // 偏差最大的那一段畫滿
  assert.ok(Math.abs(rows[1].ratio - 0.75) < 1e-9);
});

test("每一段都剛好等於平均時不會除以零", () => {
  const rows = trendRows(okTrend({
    sections: [
      { label: "A", mean_delta: 0, mean_accuracy: 0.5, named: false },
      { label: "B", mean_delta: 0, mean_accuracy: 0.5, named: false },
    ],
  }));
  assert.deepEqual(rows.map((r) => r.ratio), [0, 0]);
});

test("超過上限時留下偏差最大的幾段，但仍照歌曲順序畫", () => {
  const sections = [];
  // 偏差由小到大排在歌曲順序上，被砍掉的應該是前面幾段
  for (let i = 0; i < MAX_TREND_ROWS + 3; i++) {
    sections.push({ label: `第 ${i} 段`, mean_delta: i / 100, mean_accuracy: 0.5, named: false });
  }
  const rows = trendRows(okTrend({ sections }));
  assert.equal(rows.length, MAX_TREND_ROWS);
  assert.deepEqual(rows.map((r) => r.label), sections.slice(3).map((s) => s.label));
});

test("沒有段落資料就沒有長條圖", () => {
  assert.deepEqual(trendRows({ status: "ok" }), []);
  assert.deepEqual(trendRows(null), []);
});
