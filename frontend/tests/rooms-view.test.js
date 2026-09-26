/**
 * 多包廂說法與櫃檯總覽排序的前端單元測試（node --test）。
 *
 * 這一層錯的後果有兩種，都不會有人回報：房號打錯時畫面沒講下一步
 * （那個人一直按重新整理），以及櫃檯總覽把真正在等的那一間排到第七列
 * （櫃檯以為沒事）。
 */
const test = require("node:test");
const assert = require("node:assert/strict");

const {
  isValidRoomId,
  roomLabel,
  roomBadgeText,
  shouldShowRoomSwitcher,
  unknownRoomMessage,
  validateNewRoom,
  roomErrorMessage,
  roomUrgency,
  sortRoomsForDesk,
  roomDeskLine,
  laneSummary,
} = require("../js/rooms-view.js");

// --- 房號形狀（跟後端同一套規則）---

test("合法的房號", () => {
  for (const id of ["default", "101", "vip", "room-3", "a"]) {
    assert.equal(isValidRoomId(id), true, id);
  }
});

test("不合法的房號一律擋下來，不做修補", () => {
  for (const id of ["", "包廂A", "ro om", "-lead", "Room_1", "x".repeat(25), null]) {
    assert.equal(isValidRoomId(id), false, String(id));
  }
});

test("大小寫收斂成同一間（檔案系統上它們不是兩個東西）", () => {
  assert.equal(isValidRoomId(" VIP "), true);
});

// --- 叫法 ---

test("名字與房號都要出現：房號是用喊的那一組", () => {
  assert.equal(roomLabel({ id: "vip", name: "VIP 大包" }), "VIP 大包（vip）");
});

test("名字剛好就是房號時不重複印", () => {
  assert.equal(roomLabel({ id: "101", name: "101" }), "101");
  assert.equal(roomLabel({ id: "101", name: "" }), "101");
});

test("頁首那一顆鍵只印名字（那一行擠不下房號）", () => {
  assert.equal(roomBadgeText({ id: "vip", name: "VIP 大包" }), "🏠 VIP 大包");
});

test("只有一間包廂時整組切換 UI 不出現", () => {
  assert.equal(shouldShowRoomSwitcher([{ id: "default" }]), false);
  assert.equal(shouldShowRoomSwitcher([]), false);
  assert.equal(shouldShowRoomSwitcher([{ id: "default" }, { id: "vip" }]), true);
});

// --- 房號打錯 ---

test("房號不存在時一定要講下一步", () => {
  const msg = unknownRoomMessage("102", [{ id: "101" }, { id: "vip" }]);
  assert.match(msg, /102/);
  assert.match(msg, /101、vip/);
  assert.match(msg, /QR/);
});

test("連清單都拿不到時照樣要講下一步", () => {
  assert.match(unknownRoomMessage("102", []), /QR/);
});

// --- 新增包廂的表單 ---

test("房號留白是合法的（伺服器會從名字生一個）", () => {
  assert.equal(validateNewRoom("101 包廂", "", []).ok, true);
});

test("名字與房號都空白就收不下去", () => {
  assert.equal(validateNewRoom("", "", []).ok, false);
});

test("打了但形狀不對要講清楚規則與理由", () => {
  const v = validateNewRoom("包廂", "房間 A", []);
  assert.equal(v.ok, false);
  assert.match(v.message, /小寫英文/);
  assert.match(v.message, /QR/);
});

test("重複的房號當場擋下來（門口那張 QR 只能指到一間）", () => {
  const v = validateNewRoom("另一間", "VIP", ["vip", "default"]);
  assert.equal(v.ok, false);
  assert.match(v.message, /已經有一間/);
});

// --- 伺服器錯誤的人話 ---

test("刪不掉預設包廂要說出理由", () => {
  assert.match(roomErrorMessage({ error: "default_room_locked" }), /至少要有一間/);
});

test("還在唱歌的包廂要說出裡面有什麼", () => {
  const msg = roomErrorMessage({ error: "room_in_use", now_playing: "稻香", queue_length: 3 });
  assert.match(msg, /稻香/);
  assert.match(msg, /3 首/);
  assert.match(msg, /舞台螢幕會立刻失效/);
});

test("認不得的代碼不會炸掉，給一句中性的話", () => {
  assert.equal(typeof roomErrorMessage(null), "string");
  assert.equal(typeof roomErrorMessage({ error: "??" }), "string");
});

// --- 櫃檯總覽的排序 ---

const idle = { id: "c", room: {}, service_open: null };
const waitingCall = { id: "a", room: {}, service_open: { status: "waiting" } };
const ackedCall = { id: "b", room: {}, service_open: { status: "acked" } };
const almostUp = { id: "d", room: { active: true, remaining_seconds: 300 }, service_open: null };

test("在叫櫃檯而且沒人回應的排最前面", () => {
  assert.equal(roomUrgency(waitingCall), 0);
  assert.equal(roomUrgency(ackedCall), 1);
  assert.equal(roomUrgency(almostUp), 2);
  assert.equal(roomUrgency(idle), 3);
});

test("照房號排的話，按了五分鐘鈴的 202 會排在沒事的 101 後面", () => {
  const order = sortRoomsForDesk([idle, almostUp, ackedCall, waitingCall]).map(r => r.id);
  assert.deepEqual(order, ["a", "b", "d", "c"]);
});

test("同樣緊急時照房號（每次重畫順序要一樣，不然按鍵會在手指底下換位置）", () => {
  const rows = [{ id: "202", room: {} }, { id: "101", room: {} }];
  assert.deepEqual(sortRoomsForDesk(rows).map(r => r.id), ["101", "202"]);
});

test("正在唱得高興的包廂不會往上跳（櫃檯不需要為它做任何事）", () => {
  const singing = { id: "e", room: {}, service_open: null,
                    now_playing: { title: "稻香" }, queue_length: 4 };
  assert.equal(roomUrgency(singing), 3);
});

// --- 總覽那一列的第二行 ---

test("在唱歌就講現在唱什麼、誰點的、還有幾首", () => {
  const line = roomDeskLine({ now_playing: { title: "稻香", requested_by: "阿明" },
                                queue_length: 3 });
  assert.match(line, /稻香/);
  assert.match(line, /阿明/);
  assert.match(line, /待唱 2/);
});

test("沒在唱但還在跑流水線要講得出來（不然那間看起來像空的）", () => {
  assert.match(roomDeskLine({ processing: 2, queue_length: 2 }), /處理中 2/);
});

test("沒有裝置連線跟沒有人點歌是兩件事", () => {
  assert.match(roomDeskLine({ devices: 1 }), /沒有人點歌/);
  assert.match(roomDeskLine({ devices: 0 }), /沒有裝置/);
});

// --- 車道 ---

test("排隊要講得出「前面還有幾首」，但不講「還要幾分鐘」", () => {
  const line = laneSummary({ running_count: 1, waiting: 3 });
  assert.match(line, /3 首排隊/);
  assert.doesNotMatch(line, /分鐘/);
});

test("閒著的時候也講得出來", () => {
  assert.match(laneSummary({ running_count: 0, waiting: 0 }), /很閒/);
  assert.equal(laneSummary(null), "");
});
