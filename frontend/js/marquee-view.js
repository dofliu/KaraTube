/**
 * 舞台訊息的說法 (Marquee View)
 *
 * 訊息本身在伺服器（backend/services/marquee.py）。這一支回答的是畫面那三個問題：
 *
 *   現在該顯示哪一則？   （同時有三則的時候輪播，不是三條疊在一起）
 *   要用什麼身體顯示？   （沒人唱歌時是大字卡，正在唱歌時降級成上緣那一條）
 *   這則還有效嗎？       （「您的餐點到了」過了四十分鐘就是錯的資訊）
 *
 * 時間的算法跟包廂計時同一套：**不比對絕對時刻**，而是拿「伺服器那一份快照裡
 * 的時間差」加上「收到快照之後過了多久」。舞台那台機器的時鐘不一定跟伺服器
 * 一樣（家用機器常常差好幾分鐘），比對絕對時刻的話，訊息會提早消失或不肯消失。
 * 兩個時間都出自同一台伺服器，相減就沒有時鐘問題。
 *
 * 輪播的相位在舞台自己算，不跟伺服器對齊 —— 跟倒數那個數字不一樣，
 * 「現在輪到第幾則」沒有兩個畫面對不起來的問題（只有舞台在顯示它）。
 *
 * 純資料邏輯：不碰 DOM、不發網路請求。
 */

// 緊急訊息多停留的倍率。它換到的優待是「久一點、排前面」，
// 不是「可以蓋住歌詞」—— 緊急與否都不會變成全螢幕擋住正在唱的人。
const MARQUEE_URGENT_HOLD = 1.5;

// 沒寫顯示秒數時的退路（伺服器一定會給，這裡守的是舊快照與壞資料）。
const MARQUEE_FALLBACK_SECONDS = 8;

/** 兩個伺服器時間相減（秒）。看不懂的值回 0，呼叫端就當它已經過期。 */
function marqueeSpanSeconds(fromIso, toIso) {
  const a = Date.parse(String(fromIso || ""));
  const b = Date.parse(String(toIso || ""));
  if (Number.isNaN(a) || Number.isNaN(b)) return 0;
  return (b - a) / 1000;
}

/**
 * 這則還剩幾秒。
 *
 * `since` 是「收到這份快照之後過了幾秒」—— 每一支呼叫這裡的函式都要傳，
 * 否則訊息會停在收到那一刻的狀態不肯消失。
 */
function marqueeRemainingSeconds(msg, snapshot, since = 0) {
  if (!msg || !snapshot) return 0;
  const life = marqueeSpanSeconds(snapshot.updated_at, msg.expires_at);
  return Math.max(0, life - Math.max(0, Number(since) || 0));
}

/** 還有效的訊息（順序照伺服器給的，緊急在前、釘住在後）。 */
function marqueeActive(snapshot, since = 0) {
  if (!snapshot || !Array.isArray(snapshot.messages)) return [];
  return snapshot.messages.filter((msg) => marqueeRemainingSeconds(msg, snapshot, since) > 0);
}

/** 這一則要停留幾秒。緊急的久一點，因為它通常是要人起身去做一件事。 */
function marqueeHoldSeconds(msg) {
  if (!msg) return MARQUEE_FALLBACK_SECONDS;
  let seconds = Number(msg.seconds);
  if (!Number.isFinite(seconds) || seconds <= 0) seconds = MARQUEE_FALLBACK_SECONDS;
  return msg.urgent ? seconds * MARQUEE_URGENT_HOLD : seconds;
}

/**
 * 現在輪到哪一則。回 null＝現在什麼都不該顯示。
 *
 * 一次只講一件事（見 marquee.py 決定四）：兩行以上的跑馬燈沒有人讀得完，
 * 而讀不完等於全都沒讀到。所以多則訊息是**輪播**，不是一起掛上去。
 */
function marqueeCurrent(snapshot, since = 0) {
  const active = marqueeActive(snapshot, since);
  if (active.length === 0) return null;
  if (active.length === 1) return active[0];
  const cycle = active.reduce((sum, msg) => sum + marqueeHoldSeconds(msg), 0);
  if (cycle <= 0) return active[0];
  let phase = (Math.max(0, Number(since) || 0)) % cycle;
  for (const msg of active) {
    const hold = marqueeHoldSeconds(msg);
    if (phase < hold) return msg;
    phase -= hold;
  }
  return active[active.length - 1];
}

/**
 * 這則要用什麼身體顯示。
 *
 * `card` 是置中的大字卡，`band` 是上緣那一條。正在播歌時一律 band ——
 * 櫃檯要的是「一定看得到」，但「一定看得到」在副歌那一句等於「一定擋到」
 * （見 marquee.py 決定二）。緊急訊息也一樣降級。
 */
function marqueeStyle(msg, { playing = false, cardWhenIdle = true } = {}) {
  if (!msg) return "";
  if (playing) return "band";
  return cardWhenIdle ? "card" : "band";
}

/** 講給人聽的剩餘時間：「剩 8 分鐘」、「剩 40 秒」。釘住的不講時間（它就是要待著）。 */
function marqueeLifeLabel(msg, snapshot, since = 0) {
  if (!msg) return "";
  if (msg.pinned) return "📌 釘住";
  const left = Math.floor(marqueeRemainingSeconds(msg, snapshot, since));
  if (left <= 0) return "已消失";
  if (left >= 60) return `剩 ${Math.floor(left / 60)} 分鐘`;
  return `剩 ${left} 秒`;
}

/**
 * 點歌台訊息清單上的那一行。
 *
 * 誰送的只在這裡出現，舞台上不顯示 —— 台下的人要看的是那句話，不是誰打的。
 */
function marqueeListLine(msg, snapshot, since = 0) {
  if (!msg) return "";
  const tags = [];
  if (msg.urgent) tags.push("⚡ 緊急");
  const life = marqueeLifeLabel(msg, snapshot, since);
  if (life) tags.push(life);
  if (msg.sender) tags.push(`by ${msg.sender}`);
  return tags.length ? `${msg.text}（${tags.join("・")}）` : msg.text;
}

/**
 * 送不出去時說的話。
 *
 * 跟額度、計時那兩支同一個規矩：講規則，而且每一句「不行」後面都要有下一步。
 * 「訊息沒送出」如果只回一個紅框，送出的人會以為螢幕上已經有字了，
 * 然後對著客人說「您看一下螢幕」。
 */
function marqueeRejectNote(reason, detail = {}) {
  if (reason === "full") {
    const max = Math.max(1, Math.floor(Number(detail.max_messages) || 0));
    return `📺 舞台上已經排了 ${max} 則訊息，等一則播完、或先撤掉一則再送。`;
  }
  if (reason === "empty") {
    const chars = Math.max(1, Math.floor(Number(detail.max_chars) || 0));
    return `📺 訊息是空的（最多 ${chars} 個字，舞台上只有一行）。`;
  }
  if (reason === "disabled") {
    return "📺 舞台訊息目前關著，到系統設定頁的「📺 舞台訊息」打開。";
  }
  return "📺 訊息沒送出去。";
}

/** 送出成功之後回的那一句：講清楚它什麼時候會出現、什麼時候會消失。 */
function marqueeSentNote(msg, playing = false) {
  if (!msg) return "";
  const where = playing
    ? "已送到舞台上緣（正在播歌，不會蓋住歌詞）"
    : "已送到舞台正中央";
  const life = msg.pinned ? "會一直留著，直到撤掉" : "過一會兒自己消失";
  return `📺 ${where}，${life}。`;
}

if (typeof window !== "undefined") {
  window.MarqueeView = {
    MARQUEE_URGENT_HOLD, MARQUEE_FALLBACK_SECONDS,
    marqueeSpanSeconds, marqueeRemainingSeconds, marqueeActive, marqueeHoldSeconds,
    marqueeCurrent, marqueeStyle, marqueeLifeLabel, marqueeListLine,
    marqueeRejectNote, marqueeSentNote,
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    MARQUEE_URGENT_HOLD, MARQUEE_FALLBACK_SECONDS,
    marqueeSpanSeconds, marqueeRemainingSeconds, marqueeActive, marqueeHoldSeconds,
    marqueeCurrent, marqueeStyle, marqueeLifeLabel, marqueeListLine,
    marqueeRejectNote, marqueeSentNote,
  };
}
