/**
 * 每人待唱上限（點歌額度）的說法測試。
 *
 * 這是整個系統裡唯一一個對使用者說「不行」的地方。規則本身在伺服器且已經有
 * 測試釘著（tests/test_song_quota.py），這一支釘的是**那句話有沒有講滿**：
 * 誰、現在幾首、什麼時候可以再點。少講最後一件，使用者的下一個動作就是
 * 再按一次（然後再被擋一次，然後認定點歌壞了）。
 */
const test = require("node:test");
const assert = require("node:assert/strict");

const {
  QUOTA_ANON_LABEL,
  quotaLimitLabel, quotaSingerSummary, quotaLine,
  quotaRejectionNote, quotaAddNote, quotaChangeNote,
} = require("../js/quota-view.js");

function singer(name, pending, limit, anonymous = false) {
  return { name, pending, anonymous, full: limit > 0 && pending >= limit,
           remaining: limit > 0 ? Math.max(0, limit - pending) : null };
}

// --- 按鈕上的字 ---

test("按鈕把狀態寫在字上（包廂的燈是暗的，只靠顏色看不出來）", () => {
  assert.match(quotaLimitLabel(3), /每人 3 首/);
  assert.match(quotaLimitLabel(0), /不限/);
  // 看不懂的值一律當成不限，跟伺服器端的 coerce_limit 同一個態度
  assert.match(quotaLimitLabel(null), /不限/);
  assert.match(quotaLimitLabel("壞掉的值"), /不限/);
});

// --- 用量那一行 ---

test("沒開額度時整行不出現（沒有規則的包廂不需要看到一排 x/y）", () => {
  assert.equal(quotaLine({ limit: 0, singers: [singer("小明", 2, 0)] }), "");
  assert.equal(quotaLine(null), "");
});

test("用量寫成 2/3，排滿的多一個字", () => {
  assert.equal(quotaSingerSummary(singer("小明", 2, 3), 3), "小明 2/3");
  assert.equal(quotaSingerSummary(singer("小明", 3, 3), 3), "小明 3/3 滿");
});

test("沒取暱稱的那一桶用固定的叫法，跟輪唱那一行同一個詞", () => {
  const s = singer("", 2, 3, true);
  assert.equal(quotaSingerSummary(s, 3), `${QUOTA_ANON_LABEL} 2/3`);
});

test("排滿的人一定排進被列出來的那幾個", () => {
  // 這一行存在的理由就是「誰快滿了」。把滿的人擠進「…等 N 人」裡面
  // 等於整行白寫 —— 而且滿的人正好是排在佇列比較後面的那一位。
  const singers = [
    singer("A", 1, 2), singer("B", 1, 2), singer("C", 1, 2),
    singer("D", 1, 2), singer("滿了", 2, 2),
  ];
  const line = quotaLine({ limit: 2, singers }, 2);
  assert.match(line, /滿了 2\/2 滿/);
  assert.match(line, /…等 5 人/);
});

test("沒有人排隊時講的是「目前沒有人排隊」，不是一行空白", () => {
  assert.match(quotaLine({ limit: 3, singers: [] }), /沒有人排隊/);
});

// --- 被擋下來的那句話 ---

test("被擋時講滿三件事：誰、現在幾首、什麼時候可以再點", () => {
  const note = quotaRejectionNote({
    limit: 2, pending: 2, name: "小明", anonymous: false, next_position: 3,
  });
  assert.match(note, /小明/);          // 誰
  assert.match(note, /2 首待唱/);       // 現在幾首
  assert.match(note, /上限 2 首/);      // 上限是多少
  assert.match(note, /第 3 位/);        // 什麼時候可以再點
});

test("沒取暱稱的人講的是完全不同的一句話，而且要帶出解法", () => {
  // 他不是「自己排太多」，是跟所有沒取名的人共用一份額度。這件事不講明白，
  // 他看到的是「我明明只點了一首就說我排了三首」—— 那看起來就是個 bug。
  const note = quotaRejectionNote({
    limit: 3, pending: 3, name: "", anonymous: true, next_position: 1,
  });
  assert.match(note, new RegExp(QUOTA_ANON_LABEL));
  assert.match(note, /共用同一份額度/);
  assert.match(note, /暱稱/);          // 解法：取個暱稱就有自己的額度
});

test("沒有 next_position 時仍然講得出「什麼時候」", () => {
  const note = quotaRejectionNote({ limit: 1, pending: 1, name: "小美" });
  assert.match(note, /唱完/);
});

test("結論整個缺席時也要有一句話，不能是 undefined", () => {
  assert.notEqual(quotaRejectionNote(null), "");
  assert.equal(typeof quotaRejectionNote(undefined), "string");
});

// --- 點成功之後補的那半句 ---

test("剩很多的時候不補話（每一句雜訊都會讓下一句重要的話被略過）", () => {
  assert.equal(quotaAddNote({ limit: 5, remaining: 4 }), "");
  assert.equal(quotaAddNote({ limit: 0, remaining: null }), "");
  assert.equal(quotaAddNote(null), "");
});

test("剩最後一兩首才講，剩 0 首講的是下一步不是「剩 0 首」", () => {
  assert.match(quotaAddNote({ limit: 3, remaining: 1 }), /還可以再排 1 首/);
  const last = quotaAddNote({ limit: 3, remaining: 0 });
  assert.match(last, /最後一首/);
  assert.doesNotMatch(last, /0 首/);
});

// --- 上限被改動時 ---

test("改上限時明講已經排好的歌不會被刪", () => {
  // 這是決定四在畫面上的體現：有人動了一個設定、別人排好的歌就消失，
  // 會是這個系統最嚴重的一次背叛，所以要在按下去的當下就否認它。
  const note = quotaChangeNote(3);
  assert.match(note, /3 首/);
  assert.match(note, /不會被刪/);
  assert.match(quotaChangeNote(0), /不限|想排幾首/);
});
