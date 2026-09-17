/**
 * 舞台訊息（跑馬燈）的說法測試。
 *
 * 規則本身在伺服器且已經有測試釘著（tests/test_marquee.py），這一支釘的是
 * **畫面上的三個決定**，而且每一個都是「不做會出事」的那種：
 *
 *   播歌中不准用大字卡   —— 大字卡蓋住的是正在唱的那個人
 *   訊息要自己消失       —— 靠伺服器推的話，心跳晚五秒它就多留五秒
 *   時間用相減、不比對絕對時刻 —— 舞台那台機器的時鐘跟伺服器常常差好幾分鐘
 */
const test = require("node:test");
const assert = require("node:assert/strict");

const {
  marqueeSpanSeconds, marqueeRemainingSeconds, marqueeActive, marqueeHoldSeconds,
  marqueeCurrent, marqueeStyle, marqueeLifeLabel, marqueeListLine,
  marqueeRejectNote, marqueeSentNote,
} = require("../js/marquee-view.js");

/** 伺服器送來的那一份快照。updated_at 是「這一份是幾點的」。 */
function snap(messages, updatedAt = "2026-09-17T21:00:00") {
  return {
    messages, count: messages.length, max_messages: 8, max_chars: 40,
    updated_at: updatedAt, enabled: true, default_seconds: 8,
    default_ttl_minutes: 10, card_when_idle: true,
  };
}

function msg(over = {}) {
  return {
    id: "m1", text: "您的餐點到了", sender: "櫃檯", urgent: false, pinned: false,
    seconds: 8, created_at: "2026-09-17T21:00:00",
    expires_at: "2026-09-17T21:10:00",
    ...over,
  };
}

// --- 時間怎麼算 ---

test("兩個伺服器時間相減，壞掉的值當成 0（呼叫端就當它過期）", () => {
  assert.equal(marqueeSpanSeconds("2026-09-17T21:00:00", "2026-09-17T21:10:00"), 600);
  assert.equal(marqueeSpanSeconds("壞掉", "2026-09-17T21:10:00"), 0);
  assert.equal(marqueeSpanSeconds("2026-09-17T21:00:00", null), 0);
});

test("剩餘時間是「快照裡的時間差」減掉「收到之後過了多久」", () => {
  const s = snap([msg()]);
  assert.equal(marqueeRemainingSeconds(s.messages[0], s, 0), 600);
  assert.equal(marqueeRemainingSeconds(s.messages[0], s, 90), 510);
  // 不會變成負數
  assert.equal(marqueeRemainingSeconds(s.messages[0], s, 9999), 0);
});

test("過期的訊息當場從清單上消失，不等伺服器推下一份", () => {
  const s = snap([msg()]);
  assert.equal(marqueeActive(s, 599).length, 1);
  assert.equal(marqueeActive(s, 601).length, 0);
});

test("沒有快照、或快照壞掉時回空陣列而不是爆掉", () => {
  assert.deepEqual(marqueeActive(null, 0), []);
  assert.deepEqual(marqueeActive({}, 0), []);
});

// --- 輪播 ---

test("緊急訊息停留久一點（它要人起身去做一件事）", () => {
  assert.equal(marqueeHoldSeconds(msg({ seconds: 8 })), 8);
  assert.equal(marqueeHoldSeconds(msg({ seconds: 8, urgent: true })), 12);
  // 壞掉的秒數走退路，不是 0（0 會讓輪播卡在同一則上）
  assert.equal(marqueeHoldSeconds(msg({ seconds: 0 })), 8);
  assert.equal(marqueeHoldSeconds(msg({ seconds: "很久" })), 8);
});

test("一次只講一件事：多則訊息輪播，不是一起掛上去", () => {
  const a = msg({ id: "a", text: "第一則", seconds: 5 });
  const b = msg({ id: "b", text: "第二則", seconds: 5 });
  const s = snap([a, b]);
  assert.equal(marqueeCurrent(s, 0).id, "a");
  assert.equal(marqueeCurrent(s, 4).id, "a");
  assert.equal(marqueeCurrent(s, 6).id, "b");
  // 一輪走完回到第一則
  assert.equal(marqueeCurrent(s, 11).id, "a");
});

test("只剩一則時就一直是那一則；一則都沒有時回 null（畫面什麼都不顯示）", () => {
  assert.equal(marqueeCurrent(snap([msg()]), 300).id, "m1");
  assert.equal(marqueeCurrent(snap([]), 0), null);
  assert.equal(marqueeCurrent(snap([msg()]), 601), null);
});

test("輪播只在還活著的訊息之間跑（過期的不會佔掉一輪的時間）", () => {
  const dead = msg({ id: "dead", expires_at: "2026-09-17T21:00:30", seconds: 5 });
  const alive = msg({ id: "alive", seconds: 5 });
  const s = snap([dead, alive]);
  assert.equal(marqueeCurrent(s, 60).id, "alive");
});

// --- 用什麼身體顯示（這個功能最重要的一條規則）---

test("播歌中一律是上緣那一條 —— 沒有任何訊息重要到可以蓋住正在唱的那個人", () => {
  const m = msg();
  assert.equal(marqueeStyle(m, { playing: true, cardWhenIdle: true }), "band");
  // 緊急也一樣降級：它換到的優待是排前面、久一點，不是蓋住歌詞
  assert.equal(marqueeStyle(msg({ urgent: true }), { playing: true }), "band");
});

test("沒有人在唱歌時才用大字卡，而且設定關得掉", () => {
  const m = msg();
  assert.equal(marqueeStyle(m, { playing: false, cardWhenIdle: true }), "card");
  assert.equal(marqueeStyle(m, { playing: false, cardWhenIdle: false }), "band");
  // 沒有訊息就什麼都不顯示
  assert.equal(marqueeStyle(null, { playing: false }), "");
});

// --- 點歌台清單上的說法 ---

test("剩餘時間講得出來；釘住的不講時間（它就是要待著）", () => {
  const s = snap([msg()]);
  assert.equal(marqueeLifeLabel(s.messages[0], s, 0), "剩 10 分鐘");
  assert.equal(marqueeLifeLabel(s.messages[0], s, 570), "剩 30 秒");
  assert.equal(marqueeLifeLabel(s.messages[0], s, 700), "已消失");
  assert.equal(marqueeLifeLabel(msg({ pinned: true }), s, 0), "📌 釘住");
});

test("清單那一行帶得出「誰送的」（舞台上不顯示，這裡要）", () => {
  const s = snap([msg({ urgent: true })]);
  const line = marqueeListLine(s.messages[0], s, 0);
  assert.match(line, /您的餐點到了/);
  assert.match(line, /⚡ 緊急/);
  assert.match(line, /by 櫃檯/);
});

// --- 送不出去時說的話 ---

test("每一句「不行」後面都要有下一步", () => {
  assert.match(marqueeRejectNote("full", { max_messages: 8 }), /8 則/);
  assert.match(marqueeRejectNote("full", { max_messages: 8 }), /撤掉/);
  assert.match(marqueeRejectNote("empty", { max_chars: 40 }), /40 個字/);
  assert.match(marqueeRejectNote("disabled"), /系統設定/);
  // 沒見過的原因也要講一句人話，不能是 undefined
  assert.ok(marqueeRejectNote("something-new").length > 0);
});

test("送出成功要講它會出現在哪、什麼時候消失", () => {
  assert.match(marqueeSentNote(msg(), true), /不會蓋住歌詞/);
  assert.match(marqueeSentNote(msg(), false), /正中央/);
  assert.match(marqueeSentNote(msg({ pinned: true }), false), /直到撤掉/);
  assert.equal(marqueeSentNote(null, false), "");
});
