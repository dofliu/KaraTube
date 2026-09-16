/**
 * 包廂計時的說法 (Room Timer View)
 *
 * 計時本身在伺服器（backend/services/room_timer.py）—— 時間只能有一份，否則
 * 點歌台與舞台會各自倒數出兩個不同的數字，而包廂裡的人會相信對自己有利的那個。
 * 這一支只負責**把時間講成人話**，外加一件伺服器做不到的事：讓那個數字每秒跳。
 *
 * 為什麼倒數要在前端跑：伺服器每五秒才推一次（而且只在有事發生時推整份狀態）。
 * 為了一個時鐘而每秒廣播一次整份狀態，是拿包廂的網路換一個本來就算得出來的
 * 數字。所以做法是「拿到快照那一刻的剩餘秒數 + 從那之後過了多久」——
 * 每一支呼叫這裡的函式都要傳 `since`（收到快照後過了幾秒）。
 *
 * 這一支要講的話有三種，而且語氣完全不同：
 *
 *   倒數    —— 一個數字，越接近結束越明顯，但不能蓋住歌詞
 *   提醒    —— 剩十分鐘、剩三分鐘。必須講滿：剩多久、時間到會怎樣、怎麼續
 *   時間到  —— 唯一一次要把「正在唱的那一首會唱完」講清楚的機會
 *
 * 第二件（時間到會怎樣）是最容易漏掉、也最重要的一句。少了它，最後一首的
 * 點歌者會以為自己被偷走一首歌；有了它，剩下的十分鐘大家會自己安排。
 *
 * 純資料邏輯：不碰 DOM、不發網路請求。
 */

// 進入「快沒時間了」的顯示模式：剩下這麼多秒以內，舞台角落才會出現倒數。
// 從頭到尾掛著一個倒數會變成包廂裡最亮的那個東西，而且沒有人需要在還剩
// 兩小時的時候盯著秒數看。
const ROOM_STAGE_SHOW_SECONDS = 10 * 60;

// 點歌時如果剩下的時間比這還短，就順便講一聲「這首可能唱不完」。
// 五分鐘是一首歌的長度 —— 這句話講在點歌的當下有用，講在歌被停下來的
// 那一刻就只是事後諸葛。
const ROOM_SHORT_TAIL_SECONDS = 5 * 60;

/** 時鐘格式：一小時以上是 H:MM:SS，以下是 MM:SS（前面那個 0 只是雜訊）。 */
function roomClock(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

/**
 * 講給人聽的長度：「2 小時 14 分」、「9 分 58 秒」、「40 秒」。
 *
 * 跟 roomClock 分開是因為兩者用在不同的地方：時鐘是**盯著看**的（要對齊、
 * 要固定寬度），這一個是**讀出來**的（放在句子裡，「剩 02:14:30」很難唸）。
 */
function roomDuration(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  if (total >= 3600) {
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    return m > 0 ? `${h} 小時 ${m} 分` : `${h} 小時`;
  }
  if (total >= 60) {
    const m = Math.floor(total / 60);
    const s = total % 60;
    return s > 0 ? `${m} 分 ${s} 秒` : `${m} 分鐘`;
  }
  return `${total} 秒`;
}

/**
 * 現在還剩幾秒（把「收到快照之後過了多久」算進去）。
 *
 * 停錶中不扣時間 —— 中場休息時倒數還在跑的話，回來會發現少了二十分鐘，
 * 而那二十分鐘正是這個功能承諾不會算的。
 */
function roomRemainingSeconds(room, since = 0) {
  if (!room || !room.active) return 0;
  const base = Math.max(0, Math.floor(Number(room.remaining_seconds) || 0));
  if (!room.running || room.expired) return base;
  return Math.max(0, base - Math.max(0, Math.floor(Number(since) || 0)));
}

/** 預計結束的時刻：「23:30」。停錶中沒有這件事（伺服器會給 null）。 */
function roomEndsAtLabel(room) {
  if (!room || !room.ends_at) return "";
  const t = new Date(room.ends_at);
  if (Number.isNaN(t.getTime())) return "";
  return `${String(t.getHours()).padStart(2, "0")}:${String(t.getMinutes()).padStart(2, "0")}`;
}

/**
 * 點歌台上那一顆鍵／那一行字。
 *
 * 狀態寫在字上，不能只靠顏色（包廂的燈是暗的，而這是整台機器上唯一一個
 * 「看錯就會吵起來」的數字）。
 */
function roomHeaderLabel(room, since = 0) {
  if (!room || !room.enabled) return "";
  if (!room.active) return "⏱️ 開始計時";
  const left = roomRemainingSeconds(room, since);
  if (room.expired) return room.halted ? "⏰ 歡唱結束" : "⏰ 時間到";
  if (!room.running) return `⏸️ 暫停 ${roomClock(left)}`;
  return `⏳ ${roomClock(left)}`;
}

/**
 * 時間到之後會發生什麼事 —— 這句話要在**提醒的時候**就先講明白。
 *
 * 不講的話，最後一首的點歌者會以為自己被偷走一首歌；講了的話，
 * 剩下的十分鐘大家會自己安排（誰唱最後一首是包廂自己會排好的事）。
 */
function roomExpiryPolicyNote(room) {
  if (!room) return "";
  if (room.expire_action === "notify_only") return "時間到只會提醒，不會停歌。";
  return "時間到會讓正在唱的那一首唱完，然後就不再播下一首（佇列不會被清掉）。";
}

/** 續時要怎麼續。每一句「不行」後面都要有下一步，否則它只是個死路。 */
function roomExtendHint(room) {
  const minutes = Math.max(0, Math.floor(Number(room && room.extend_minutes) || 0));
  return minutes > 0
    ? `需要的話在點歌台按「＋${minutes} 分」續時。`
    : "需要的話在點歌台續時。";
}

/**
 * 佇列上方那一行：現在的計時狀況。
 *
 * 沒開計時就整行不出現 —— 沒有在算時間的包廂不需要看到一個 00:00。
 */
function roomStatusLine(room, since = 0) {
  if (!room || !room.enabled) return "";
  if (!room.active) return "尚未開始計時";
  if (room.expired) {
    const over = Math.max(0, Math.floor(Number(room.overtime_seconds) || 0));
    const tail = over >= 60 ? `（已超過 ${roomDuration(over)}）` : "";
    return room.halted
      ? `⏰ 歡唱時間結束${tail}・${roomExtendHint(room)}`
      : `⏰ 時間到${tail}・${roomExpiryPolicyNote(room)}${roomExtendHint(room)}`;
  }
  const left = roomRemainingSeconds(room, since);
  if (!room.running) return `⏸️ 已暫停計時・還剩 ${roomDuration(left)}`;
  const ends = roomEndsAtLabel(room);
  return ends ? `⏳ 還剩 ${roomDuration(left)}（${ends} 結束）` : `⏳ 還剩 ${roomDuration(left)}`;
}

/**
 * 提醒跳出來時要說的那一句（見本檔開頭的三件事）。
 *
 * `alert.kind` 有三種，語氣刻意不同：
 *   warn      還有時間決定要不要續 —— 只講事實與選項
 *   last_call 來不及續的話這就是最後一首 —— 講的是「該挑哪一首」
 *   expired   時間到 —— 唯一一次要把「正在唱的那一首會唱完」講清楚的機會
 */
function roomAlertNote(alert, room) {
  if (!alert) return "";
  const left = Math.max(0, Math.floor(Number(alert.remaining_seconds) || 0));
  const policy = roomExpiryPolicyNote(room);
  const extend = roomExtendHint(room);
  if (alert.kind === "expired") {
    return room && room.expire_action === "notify_only"
      ? `⏰ 歡唱時間到了。${extend}`
      : `⏰ 歡唱時間到了 —— 這一首唱完就結束，佇列裡的歌會留著。${extend}`;
  }
  if (alert.kind === "last_call") {
    return `⏰ 最後 ${roomDuration(left)}：想唱的趕快點，現在點的可能來不及唱。${extend}`;
  }
  return `⏳ 歡唱時間剩 ${roomDuration(left)}。${policy}${extend}`;
}

/**
 * 時間到之後還來點歌，被擋下來時說的話。
 *
 * 跟額度那一支同一個規矩：說明規則，並且講出下一步。這裡的下一步是續時，
 * 而且要講明白「排好的歌還在」—— 不講的話，使用者的第一個念頭是
 * 「那我剛剛排的五首是不是都沒了」。
 */
function roomTimeUpNote(room) {
  const extend = roomExtendHint(room);
  return `⏰ 歡唱時間已經結束了，現在點的歌不會播。${extend}`
    + "（已經排好的歌都還在，續時之後接著唱。）";
}

/**
 * 點歌成功之後補的那半句：「歡唱時間剩 4 分 12 秒，這首可能唱不完」。
 *
 * 只在真的快沒時間的時候才說。剩兩小時還跳這種提示是純粹的雜訊，
 * 而每一句雜訊都會讓下一句真正重要的話被略過不看。
 */
function roomAddNote(room, since = 0) {
  if (!room || !room.enabled || !room.active) return "";
  if (room.expired) return "";
  const left = roomRemainingSeconds(room, since);
  if (left <= 0 || left > ROOM_SHORT_TAIL_SECONDS) return "";
  return `歡唱時間剩 ${roomDuration(left)}，這首可能唱不完`;
}

/**
 * 舞台角落的倒數。回空字串＝現在不該顯示。
 *
 * 只在最後十分鐘（ROOM_STAGE_SHOW_SECONDS）出現。從頭掛到尾的話它會變成
 * 包廂裡最亮的那個東西，而且沒有人需要在還剩兩小時的時候盯著秒數看。
 * 停錶中也不顯示：中場休息時舞台上掛著一個不動的倒數，看起來像當機。
 */
function roomStageBadge(room, since = 0) {
  if (!room || !room.enabled || !room.active) return "";
  if (room.expired) return room.halted ? "⏰ 歡唱時間結束" : "⏰ 時間到・唱完這一首";
  if (!room.running) return "";
  const left = roomRemainingSeconds(room, since);
  if (left > ROOM_STAGE_SHOW_SECONDS) return "";
  return `⏳ 剩 ${roomClock(left)}`;
}

/**
 * 散場畫面上的兩行字。
 *
 * 第一行是結論，第二行是「東西都還在」與下一步 —— 因為看到這個畫面的人，
 * 下一個念頭一定是「那我排的歌呢」。
 */
function roomFinaleLines(room, pendingCount = 0) {
  const pending = Math.max(0, Math.floor(Number(pendingCount) || 0));
  const kept = pending > 0
    ? `佇列裡還有 ${pending} 首歌留著，續時之後接著唱。`
    : "續時就可以繼續唱。";
  return { title: "🎤 歡唱時間結束", detail: `${kept}${roomExtendHint(room)}` };
}

if (typeof window !== "undefined") {
  window.RoomView = {
    ROOM_STAGE_SHOW_SECONDS, ROOM_SHORT_TAIL_SECONDS,
    roomClock, roomDuration, roomRemainingSeconds, roomEndsAtLabel,
    roomHeaderLabel, roomExpiryPolicyNote, roomExtendHint, roomStatusLine,
    roomAlertNote, roomTimeUpNote, roomAddNote, roomStageBadge, roomFinaleLines,
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    ROOM_STAGE_SHOW_SECONDS, ROOM_SHORT_TAIL_SECONDS,
    roomClock, roomDuration, roomRemainingSeconds, roomEndsAtLabel,
    roomHeaderLabel, roomExpiryPolicyNote, roomExtendHint, roomStatusLine,
    roomAlertNote, roomTimeUpNote, roomAddNote, roomStageBadge, roomFinaleLines,
  };
}
