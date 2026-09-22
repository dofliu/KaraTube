/**
 * 服務鈴說法的單元測試（node --test）
 *
 * 這一支守的是「畫面講出來的那句話」。單子的規則在後端（那邊有自己的測試），
 * 這裡守的是**同一張單在兩個畫面上是同一個說法**，以及那幾句話不能講錯：
 *
 *   「已送出」與「櫃檯收到了」不能是同一句（分不出來的人會再按一次）。
 *   第二次按下去的回應不能跟第一次一樣（一樣的話他會以為第一次沒送出去）。
 *   等待時間要自己走（停住的數字看起來像系統當掉了）。
 *   已經有人應聲的單不該跟沒人理的那一張塗成一樣的紅色。
 */
const test = require("node:test");
const assert = require("node:assert/strict");

const view = require("../js/service-view.js");

const SPEC = [
  { key: "service", emoji: "🛎️", label: "服務人員" },
  { key: "food", emoji: "🍽️", label: "送餐／加點" },
  { key: "drink", emoji: "🧊", label: "加冰塊／飲料" },
];

/** 一張伺服器剛送過來的單。 */
function call(extra = {}) {
  return {
    id: "abc123",
    status: "WAITING",
    items: ["food"],
    note: "",
    by: "",
    presses: 1,
    waited_seconds: 0,
    ...extra,
  };
}

// --- 等待時間 ---

test("等待時間會自己走（快照的秒數 + 收到之後過了多久）", () => {
  // 不比對絕對時刻：手機的時鐘跟伺服器常常差好幾分鐘，
  // 比對的話客人手上會看到「等待 -3 分鐘」
  assert.equal(view.serviceWaitedSeconds(call({ waited_seconds: 120 }), 60), 180);
  assert.equal(view.serviceWaitedSeconds(call({ waited_seconds: 120 })), 120);
  assert.equal(view.serviceWaitedSeconds(null, 60), 0);
});

test("壞掉的秒數不會變成 NaN 出現在畫面上", () => {
  assert.equal(view.serviceWaitedSeconds(call({ waited_seconds: "什麼" }), 30), 30);
  assert.equal(view.serviceWaitedSeconds(call({ waited_seconds: -50 }), 0), 0);
});

test("等待時間講成人話，而且不顯示秒", () => {
  assert.equal(view.serviceWaitLabel(0), "剛剛");
  assert.equal(view.serviceWaitLabel(59), "剛剛");
  assert.equal(view.serviceWaitLabel(187), "3 分鐘");
  assert.equal(view.serviceWaitLabel(3600), "1 小時");
  assert.equal(view.serviceWaitLabel(3900), "1 小時 5 分");
});

// --- 狀態 ---

test("「已送出」與「櫃檯收到了」是兩句不同的話", () => {
  const sent = view.serviceStatusLabel(call());
  const acked = view.serviceStatusLabel(call({ status: "ACKED" }));
  assert.notEqual(sent, acked);
  // 分不出來的人沒有辦法判斷「有人在弄」還是「這顆鍵沒作用」，於是他會再按一次
  assert.match(acked, /收到/);
});

test("四種結案各有各的說法（取消跟完成不是同一件事）", () => {
  const done = view.serviceStatusLabel(call({ status: "DONE" }));
  const cancelled = view.serviceStatusLabel(call({ status: "CANCELLED" }));
  const expired = view.serviceStatusLabel(call({ status: "EXPIRED" }));
  assert.equal(new Set([done, cancelled, expired]).size, 3);
  assert.equal(view.serviceStatusLabel(null), "—");
});

test("還開著的只有等待中與櫃檯收到", () => {
  assert.equal(view.serviceIsOpen(call()), true);
  assert.equal(view.serviceIsOpen(call({ status: "ACKED" })), true);
  assert.equal(view.serviceIsOpen(call({ status: "DONE" })), false);
  assert.equal(view.serviceIsOpen(call({ status: "EXPIRED" })), false);
  assert.equal(view.serviceIsOpen(null), false);
});

// --- 變色 ---

test("等久了才變色，而且已經有人應聲的降一級", () => {
  // 讓客人再按一次的從來不是等待本身，是沒有回音
  assert.equal(view.serviceUrgency(call(), 60), "calm");
  assert.equal(view.serviceUrgency(call(), 6 * 60), "warn");
  assert.equal(view.serviceUrgency(call(), 12 * 60), "late");
  assert.equal(view.serviceUrgency(call({ status: "ACKED" }), 6 * 60), "calm");
  assert.equal(view.serviceUrgency(call({ status: "ACKED" }), 12 * 60), "warn");
});

test("結案的單不變色", () => {
  assert.equal(view.serviceUrgency(call({ status: "DONE" }), 99 * 60), "closed");
});

// --- 品項 ---

test("品項照伺服器給的清單翻譯（兩端必須是同一組詞）", () => {
  assert.equal(view.serviceItemsLabel(call({ items: ["food", "drink"] }), SPEC),
               "🍽️送餐／加點＋🧊加冰塊／飲料");
  assert.equal(view.serviceItemsLabel(call({ items: [] }), SPEC), "");
});

test("認不得的品項照樣列出來，不是默默少一項", () => {
  // 少列一項會讓櫃檯少送一樣東西，而那比列出一個怪字串糟得多
  assert.equal(view.serviceItemsLabel(call({ items: ["teleport"] }), SPEC), "teleport");
});

// --- 兩端的那一句話 ---

test("客人那一句帶著狀態、品項與等了多久", () => {
  const line = view.serviceRoomLine(
    call({ items: ["drink"], note: "少冰", waited_seconds: 200 }), SPEC);
  assert.match(line, /已送出/);
  assert.match(line, /加冰塊/);
  assert.match(line, /少冰/);
  assert.match(line, /已等 3 分鐘/);
});

test("結案之後客人那一行就空了（那張單不再需要一直在畫面上）", () => {
  assert.equal(view.serviceRoomLine(call({ status: "DONE" }), SPEC), "");
});

test("櫃檯那一列寫得出「按了幾次」", () => {
  // 按第二次是真的資訊（這桌等急了），而它值得寫出來正是因為它沒有變成第二張單
  const line = view.serviceDeskLine(
    call({ presses: 3, by: "阿明", waited_seconds: 480 }), SPEC);
  assert.match(line, /按了 3 次/);
  assert.match(line, /已等 8 分鐘/);
  assert.match(line, /來自 阿明/);
});

test("只按過一次就不寫次數（寫「按了 1 次」是雜訊）", () => {
  assert.ok(!view.serviceDeskLine(call(), SPEC).includes("按了"));
  assert.equal(view.serviceDeskLine(null, SPEC), "");
});

test("紀錄那一列帶著櫃檯回的那句話", () => {
  const line = view.serviceHistoryLine(
    call({ status: "DONE", reply: "餐點五分鐘後到" }), SPEC);
  assert.match(line, /已完成/);
  assert.match(line, /餐點五分鐘後到/);
});

// --- 按下去之後那一句 ---

test("併單與新單是兩句不同的話", () => {
  const fresh = view.serviceSentNote({ ...call(), merged: false }, SPEC);
  const merged = view.serviceSentNote({ ...call(), merged: true, presses: 3 }, SPEC);
  assert.notEqual(fresh, merged);
  // 第二次按下去得到跟第一次一樣的「已送出」，那個人會以為第一次根本沒送出去
  assert.match(merged, /併進/);
  assert.match(merged, /3 次/);
  assert.equal(view.serviceSentNote(null, SPEC), "");
});

// --- 「不行」的時候 ---

test("每一句「不行」後面都有下一步", () => {
  assert.match(view.serviceRejectNote("empty"), /選一項/);
  assert.match(view.serviceRejectNote("disabled"), /系統設定/);
  assert.match(view.serviceRejectNote("none_open"), /沒有等待中/);
  assert.match(view.serviceRejectNote("stale"), /重看一次/);
  // 認不得的原因也要講出一句人話，不是空字串
  assert.ok(view.serviceRejectNote("什麼鬼").length > 0);
});
