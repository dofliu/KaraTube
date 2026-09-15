/**
 * 公平輪唱（排麥輪序）的說法 (Rotation View)
 *
 * 規則本身在伺服器（backend/services/rotation.py）—— 排序必須只有一份，
 * 否則兩支手機會各自算出不同的順序。這一支只負責**把規則講成人話**：
 * 「排在第 2 位（你的第 1 輪），插到 3 首歌前面」。
 *
 * 為什麼值得抽成一支有測試的純模組：輪唱會把使用者剛點的歌排到他預期以外的
 * 位置。這件事只要沒講清楚，看起來就跟「壞掉」一模一樣 —— 使用者會再點一次，
 * 然後佇列裡就多一首沒人要的歌。所以那句話跟排序規則一樣重要。
 *
 * 純資料邏輯：不碰 DOM、不發網路請求。
 */

// 沒取暱稱的那一桶在畫面上的叫法。刻意不寫「匿名」——
// 那聽起來像一個特定的人，但它其實是「所有沒取名的人合起來算一個」。
const ROTATION_ANON_LABEL = "沒取暱稱";

/** 佇列上那一顆「第 N 輪」的小標。沒有輪次（沒開輪唱）就不長出來。 */
function rotationBadge(round) {
  const n = Number(round);
  if (!Number.isFinite(n) || n < 1) return "";
  return `第 ${Math.floor(n)} 輪`;
}

/** 一個人的狀態：「小明 已唱 2・待唱 1」。 */
function singerSummary(singer) {
  if (!singer) return "";
  const name = singer.anonymous ? ROTATION_ANON_LABEL : (singer.name || ROTATION_ANON_LABEL);
  const parts = [];
  if (singer.sung) parts.push(`已唱 ${singer.sung}`);
  if (singer.pending) parts.push(`待唱 ${singer.pending}`);
  return parts.length ? `${name} ${parts.join("・")}` : name;
}

/**
 * 佇列上方那一行：現在的輪序全貌。
 *
 * 人多的時候不可能全列（手機上一行就滿了），所以最多列 `limit` 個人，
 * 其餘寫成「…等 N 人」——「還有誰」比「第 7 個人是誰」重要。
 */
function rotationLine(summary, limit = 4) {
  const singers = (summary && summary.singers) || [];
  const active = singers.filter(s => s.pending || s.sung);
  if (!active.length) return "還沒有人點歌";
  const shown = active.slice(0, limit).map(singerSummary).join("　");
  const rest = active.length - Math.min(active.length, limit);
  return rest > 0 ? `${shown}　…等 ${active.length} 人` : shown;
}

/**
 * 「輪唱開著，但一整間都沒有人取暱稱」的提醒。
 *
 * 這是輪唱唯一一個「開了卻什麼都沒發生」的情況（沒取名的全算同一個人），
 * 不講的話使用者會以為開關壞了。有一個人取名就不再嘮叨。
 */
function rotationNameHint(summary) {
  if (!summary) return "";
  if ((summary.named_count || 0) > 0) return "";
  const singers = summary.singers || [];
  const hasSongs = singers.some(s => s.pending || s.sung);
  if (!hasSongs) return "";
  return "現在沒有人設暱稱，所有人算同一位 —— 等於先到先唱。點右上角設個暱稱才排得進輪序。";
}

/**
 * 剛點完一首歌要對點歌的人說的那句話。
 *
 * 三種情況分得很開：
 *   * 沒開輪唱 → 回空字串，讓呼叫端用原本那句「已加入點歌佇列」。
 *   * 直接上台（佇列本來是空的）→ 也回空字串，位置這件事沒有意義。
 *   * 有插到別人前面 → 明講插到幾首前面。這是輪唱唯一會讓人愣一下的地方，
 *     而且被插到的人也在同一個包廂裡，含糊其辭只會變成「怎麼換它先」。
 */
function rotationPlacementNote(placement, singerName = "") {
  if (!placement || !placement.enabled) return "";
  const position = Number(placement.position);
  if (!Number.isFinite(position) || position < 1) return "";
  const who = (singerName || "").trim();
  const round = Number(placement.round);
  const roundText = Number.isFinite(round) && round >= 1
    ? `${who ? `${who}的` : "你的"}第 ${Math.floor(round)} 輪` : "";
  const head = `🔁 排在第 ${position} 位`;
  const ahead = Number(placement.ahead_of) || 0;
  const tail = ahead > 0 ? `，插到 ${ahead} 首前面` : "";
  return roundText ? `${head}（${roundText}）${tail}` : `${head}${tail}`;
}

/** 開關上的字。狀態要寫在按鈕上，不能只靠顏色（包廂燈是暗的）。 */
function rotationToggleLabel(enabled) {
  return enabled ? "🔁 輪唱中" : "🔁 先到先唱";
}

/** 開關切換時的通知：說的是「接下來新點的歌」，不是「現在這一排」。 */
function rotationToggleNote(enabled) {
  return enabled
    ? "🔁 公平輪唱已開啟：接下來新點的歌會照「這是誰的第幾首」排，已經排好的順序不動。"
    : "已改回先到先唱：新點的歌一律排到最後面。";
}

if (typeof window !== "undefined") {
  window.RotationView = {
    ROTATION_ANON_LABEL,
    rotationBadge, singerSummary, rotationLine, rotationNameHint,
    rotationPlacementNote, rotationToggleLabel, rotationToggleNote,
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    ROTATION_ANON_LABEL,
    rotationBadge, singerSummary, rotationLine, rotationNameHint,
    rotationPlacementNote, rotationToggleLabel, rotationToggleNote,
  };
}
