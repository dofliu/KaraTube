/**
 * 包廂計時的說法測試。
 *
 * 計時的規則本身在伺服器且已經有測試釘著（tests/test_room_timer.py），
 * 這一支釘的是**畫面上那幾句話**，特別是最容易漏掉的那一句：
 * 「時間到會讓正在唱的那一首唱完」。少了它，最後一首的點歌者會以為
 * 自己被偷走一首歌；有了它，剩下的十分鐘大家會自己安排。
 */
const test = require("node:test");
const assert = require("node:assert/strict");

const {
  ROOM_STAGE_SHOW_SECONDS,
  roomClock, roomDuration, roomRemainingSeconds, roomEndsAtLabel,
  roomHeaderLabel, roomExpiryPolicyNote, roomStatusLine,
  roomAlertNote, roomTimeUpNote, roomAddNote, roomStageBadge, roomFinaleLines,
} = require("../js/room-view.js");

/** 伺服器送來的那一份快照。 */
function room(over = {}) {
  return {
    enabled: true, active: true, state: "running", running: true, expired: false,
    halted: false, total_seconds: 10800, elapsed_seconds: 600,
    remaining_seconds: 10200, overtime_seconds: 0, ends_at: null,
    expire_action: "finish_song", extend_minutes: 30,
    default_minutes: 180, warn_minutes: 10, last_call_minutes: 3,
    ...over,
  };
}

// --- 數字怎麼寫 ---

test("時鐘一小時以上是 H:MM:SS，以下是 MM:SS", () => {
  assert.equal(roomClock(3 * 3600 + 5 * 60 + 9), "3:05:09");
  assert.equal(roomClock(9 * 60 + 5), "09:05");
  assert.equal(roomClock(-5), "00:00");
  assert.equal(roomClock("壞掉的值"), "00:00");
});

test("句子裡的長度用讀得出來的寫法（「剩 02:14:30」很難唸）", () => {
  assert.equal(roomDuration(2 * 3600 + 14 * 60), "2 小時 14 分");
  assert.equal(roomDuration(3600), "1 小時");
  assert.equal(roomDuration(9 * 60 + 58), "9 分 58 秒");
  assert.equal(roomDuration(5 * 60), "5 分鐘");
  assert.equal(roomDuration(40), "40 秒");
});

// --- 每秒跳的那個數字 ---

test("倒數把「收到快照之後過了多久」算進去（伺服器五秒才推一次）", () => {
  assert.equal(roomRemainingSeconds(room({ remaining_seconds: 600 }), 7), 593);
});

test("停錶中不扣時間 —— 那正是這個功能承諾不會算的那段時間", () => {
  const paused = room({ running: false, state: "paused", remaining_seconds: 600 });
  assert.equal(roomRemainingSeconds(paused, 1200), 600);
});

test("倒數不會變成負數", () => {
  assert.equal(roomRemainingSeconds(room({ remaining_seconds: 5 }), 60), 0);
  assert.equal(roomRemainingSeconds(null, 10), 0);
});

// --- 點歌台 ---

test("沒開計時的包廂什麼都不顯示（不需要看到一個 00:00）", () => {
  assert.equal(roomHeaderLabel(room({ enabled: false })), "");
  assert.equal(roomStatusLine(room({ enabled: false })), "");
  assert.equal(roomStageBadge(room({ enabled: false })), "");
});

test("狀態寫在字上（包廂的燈是暗的，只靠顏色看不出來）", () => {
  assert.equal(roomHeaderLabel(room({ active: false })), "⏱️ 開始計時");
  assert.match(roomHeaderLabel(room({ remaining_seconds: 7200 })), /⏳ 2:00:00/);
  assert.match(roomHeaderLabel(room({ running: false, remaining_seconds: 600 })), /⏸️ 暫停/);
  assert.match(roomHeaderLabel(room({ expired: true })), /時間到/);
  assert.match(roomHeaderLabel(room({ expired: true, halted: true })), /結束/);
});

test("倒數那一行講得出幾點結束（「我們唱到幾點」是包廂最常問的一句）", () => {
  const ends = new Date();
  ends.setHours(23, 30, 0, 0);
  const line = roomStatusLine(room({ remaining_seconds: 3600, ends_at: ends.toISOString() }));
  assert.match(line, /還剩 1 小時/);
  assert.match(line, /23:30 結束/);
  // 壞掉的時間字串只是少講那半句，不會讓整行變成 Invalid Date
  assert.equal(roomEndsAtLabel({ ends_at: "不是時間" }), "");
});

test("時間到那一行要講出「唱完這一首」與怎麼續", () => {
  const line = roomStatusLine(room({ expired: true, running: true, overtime_seconds: 90 }));
  assert.match(line, /唱完/);
  assert.match(line, /佇列不會被清掉/);
  assert.match(line, /續時|＋30 分/);
});

// --- 提醒 ---

test("剩十分鐘的提醒要講滿：剩多久、時間到會怎樣、怎麼續", () => {
  const note = roomAlertNote({ kind: "warn", remaining_seconds: 600 }, room());
  assert.match(note, /剩 10 分鐘/);
  assert.match(note, /唱完/);          // 時間到會怎樣
  assert.match(note, /＋30 分/);       // 怎麼續
});

test("最後召集講的是「該挑哪一首」，不是又講一次規則", () => {
  const note = roomAlertNote({ kind: "last_call", remaining_seconds: 180 }, room());
  assert.match(note, /最後 3 分鐘/);
  assert.match(note, /趕快點|來不及/);
});

test("時間到那一次是唯一一次要把「正在唱的那一首會唱完」講清楚的機會", () => {
  const note = roomAlertNote({ kind: "expired", remaining_seconds: 0 }, room());
  assert.match(note, /這一首唱完/);
  assert.match(note, /佇列裡的歌會留著/);
});

test("設成「只提醒」時就不能講會停歌（那是另一台機器的行為）", () => {
  const notify = room({ expire_action: "notify_only" });
  assert.match(roomExpiryPolicyNote(notify), /不會停/);
  const note = roomAlertNote({ kind: "expired", remaining_seconds: 0 }, notify);
  assert.doesNotMatch(note, /唱完就結束/);
});

// --- 點歌時 ---

test("時間到還來點歌：說明規則，並且講出下一步與「排好的歌還在」", () => {
  const note = roomTimeUpNote(room({ expired: true }));
  assert.match(note, /不會播/);
  assert.match(note, /＋30 分/);
  assert.match(note, /已經排好的歌都還在/);
});

test("剩很多時間就不要多嘴（每一句雜訊都會讓下一句重要的話被略過）", () => {
  assert.equal(roomAddNote(room({ remaining_seconds: 7200 })), "");
  assert.equal(roomAddNote(room({ enabled: false, remaining_seconds: 60 })), "");
  assert.equal(roomAddNote(room({ active: false })), "");
  assert.match(roomAddNote(room({ remaining_seconds: 252 })), /這首可能唱不完/);
});

// --- 舞台 ---

test("舞台只在最後十分鐘亮出倒數（從頭掛到尾會變成最亮的那個東西）", () => {
  assert.equal(roomStageBadge(room({ remaining_seconds: ROOM_STAGE_SHOW_SECONDS + 1 })), "");
  assert.match(roomStageBadge(room({ remaining_seconds: 5 * 60 })), /剩 05:00/);
});

test("停錶中舞台不掛倒數（不動的倒數看起來像當機）", () => {
  assert.equal(roomStageBadge(room({ running: false, remaining_seconds: 300 })), "");
});

test("時間到之後舞台改口說「唱完這一首」，停住之後才說結束", () => {
  assert.match(roomStageBadge(room({ expired: true })), /唱完這一首/);
  assert.match(roomStageBadge(room({ expired: true, halted: true })), /結束/);
});

test("散場畫面第二行回答「那我排的歌呢」", () => {
  const withQueue = roomFinaleLines(room({ expired: true, halted: true }), 4);
  assert.match(withQueue.title, /歡唱時間結束/);
  assert.match(withQueue.detail, /還有 4 首歌留著/);
  const empty = roomFinaleLines(room({ expired: true, halted: true }), 0);
  assert.doesNotMatch(empty.detail, /首歌留著/);
  assert.match(empty.detail, /續時/);
});
