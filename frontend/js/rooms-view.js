/**
 * 多包廂的說法與櫃檯總覽 (Rooms View)
 *
 * 規則在伺服器（backend/services/rooms.py）；這一支只負責**把一台機器帶十間
 * 包廂這件事講成人話**，以及決定櫃檯那一頁上「哪一間要排在最前面」。
 *
 * 為什麼值得抽成一支有測試的純模組：
 *
 *   1. **房號打錯的那一句話**。這個功能最糟的失敗是唱到別人的包廂去，
 *      而它的入口就是一個打錯的房號。伺服器會 404（絕不靜靜退回 default），
 *      但畫面上如果只寫「錯誤」，那個人下一步會去按重新整理 —— 然後再錯一次。
 *      所以那句話一定要帶著**下一步**（重掃門口那張 QR）。
 *
 *   2. **櫃檯那一頁的排序**。十間包廂列在一頁上，櫃檯真正要回答的問題只有
 *      一個：「我現在該去哪一間？」在叫櫃檯的排最前面，接著是快到時間的，
 *      然後才是照房號。照房號排的話，202 按了五分鐘的鈴會被排在 101 後面，
 *      而 101 什麼事都沒有。
 *
 * 純資料邏輯：不碰 DOM、不發網路請求。
 */

const ROOMS_DEFAULT_ID = "default";

/** 房號的形狀（跟後端 rooms.py 的 ID_PATTERN 同一套）。 */
const ROOM_ID_RE = /^[a-z0-9][a-z0-9-]{0,23}$/;

function isValidRoomId(id) {
  return ROOM_ID_RE.test(String(id == null ? "" : id).trim().toLowerCase());
}

/**
 * 畫面上叫這一間什麼。
 *
 * 名字與房號**都要出現**：名字是給人看的（「VIP 大包」），房號是用喊的、
 * 用手打的那一組（「掃不到 QR 就打 vip」）。只印名字的話，包廂裡的人
 * 永遠學不會自己那一間叫什麼。名字剛好就是房號時不重複印。
 */
function roomLabel(room) {
  if (!room) return "";
  const id = String(room.id || "");
  const name = String(room.name || "").trim();
  if (!name || name === id) return id;
  return `${name}（${id}）`;
}

/** 頁首那一顆房間鍵上的字。只有一間包廂時整顆不出現（見 shouldShowRoomSwitcher）。 */
function roomBadgeText(room) {
  if (!room) return "🏠 包廂";
  return `🏠 ${String(room.name || room.id || "")}`;
}

/**
 * 要不要顯示切換包廂那一組 UI。
 *
 * 只有一間的時候**整組不出現** —— 家裡那台機器不需要看到一個永遠只有
 * 一個選項的下拉選單，而這個功能對他們本來就該等於不存在。
 */
function shouldShowRoomSwitcher(rooms) {
  return Array.isArray(rooms) && rooms.length > 1;
}

/**
 * 房號不存在時畫面上那一句。
 *
 * 一定要有下一步，否則那個人只會一直按重新整理。
 */
function unknownRoomMessage(roomId, rooms) {
  const id = String(roomId || "");
  const list = Array.isArray(rooms) ? rooms : [];
  const known = list.map(r => r.id).join("、");
  const head = id ? `這台機器上沒有「${id}」這一間包廂。` : "沒有指定包廂。";
  if (!known) return `${head}請重新掃描包廂門口的 QR code。`;
  return `${head}現在開著的是：${known}。請重新掃描包廂門口的 QR code，或從櫃檯切換。`;
}

/**
 * 新增包廂的表單：這一組輸入收不收得下去，收不下去的話該說什麼。
 *
 * 房號空白是**合法**的（伺服器會從名字生一個），所以這裡只擋「打了但形狀不對」。
 */
function validateNewRoom(name, id, taken) {
  const rawId = String(id == null ? "" : id).trim();
  const list = (taken || []).map(x => String(x).toLowerCase());
  if (!String(name || "").trim() && !rawId) {
    return { ok: false, message: "請給這間包廂一個名字（例如「101」或「VIP 大包」）。" };
  }
  if (rawId && !isValidRoomId(rawId)) {
    return {
      ok: false,
      message: "房號只能用小寫英文、數字與連字號（1~24 字），例如 101、vip、room-3。" +
               "它會出現在網址與門口的 QR 上，所以要打得出來、念得出來。",
    };
  }
  if (rawId && list.includes(rawId.toLowerCase())) {
    return { ok: false, message: `房號「${rawId}」已經有一間了。同一個房號不能有兩間 —— 門口那張 QR 只能指到一間。` };
  }
  return { ok: true, message: "" };
}

/** 伺服器回的錯誤代碼 → 人話。 */
function roomErrorMessage(detail) {
  const reason = (detail && (detail.error || detail.reason)) || "";
  if (reason === "room_exists") return "這個房號已經有一間了。";
  if (reason === "bad_room_id") return "房號只能用小寫英文、數字與連字號（1~24 字）。";
  if (reason === "too_many_rooms") return `這台機器最多帶 ${(detail && detail.limit) || ""} 間包廂。`;
  if (reason === "default_room_locked") return "預設包廂不能刪除（一台機器至少要有一間）。";
  if (reason === "no_such_room") return "找不到這一間包廂，它可能剛剛被關掉了。";
  if (reason === "room_in_use") {
    const parts = [];
    if (detail && detail.now_playing) parts.push(`正在唱《${detail.now_playing}》`);
    if (detail && detail.queue_length) parts.push(`佇列裡還有 ${detail.queue_length} 首`);
    const what = parts.length ? parts.join("、") : "裡面還有人在唱歌";
    return `這間包廂${what}，確定要關掉嗎？關掉之後那台舞台螢幕會立刻失效。`;
  }
  return "這個動作沒有完成。";
}

/**
 * 櫃檯總覽上一間包廂的緊急程度（數字小的排前面）。
 *
 * 0：有人在叫櫃檯而且還沒有人回應 —— 那是唯一一件「現在就要有人動」的事。
 * 1：櫃檯已經收到、還沒處理完。
 * 2：歡唱時間快到了（剩 10 分鐘以內）—— 續時要在時間到之前問。
 * 3：其他。
 */
function roomUrgency(row) {
  const call = row && row.service_open;
  if (call) return call.status === "waiting" ? 0 : 1;
  const room = (row && row.room) || {};
  if (room.active && Number(room.remaining_seconds) <= 600) return 2;
  return 3;
}

/**
 * 櫃檯總覽的排序：先照緊急程度，同樣緊急時照房號。
 *
 * 排序刻意**不**看「正在唱什麼」：一間包廂唱得正高興不需要櫃檯做任何事，
 * 而它在畫面上往上跳只會把真正在等的那一間擠下去。
 */
function sortRoomsForDesk(rows) {
  return (rows || []).slice().sort((a, b) => {
    const ua = roomUrgency(a);
    const ub = roomUrgency(b);
    if (ua !== ub) return ua - ub;
    return String(a.id).localeCompare(String(b.id));
  });
}

/** 總覽那一列的第二行：這一間現在在做什麼。 */
function roomDeskLine(row) {
  if (!row) return "";
  if (row.now_playing && row.now_playing.title) {
    const who = row.now_playing.requested_by ? `（${row.now_playing.requested_by}）` : "";
    const queued = row.queue_length > 1 ? `　待唱 ${row.queue_length - 1}` : "";
    return `🎵 ${row.now_playing.title}${who}${queued}`;
  }
  if (row.processing) return `⏳ 處理中 ${row.processing} 首`;
  if (row.queue_length) return `📋 待唱 ${row.queue_length} 首`;
  if (row.devices) return "💤 沒有人點歌";
  return "— 沒有裝置連線";
}

/**
 * 處理車道現在的樣子，講給人聽。
 *
 * 「還要幾分鐘」刻意不講：那取決於這首歌多長、CPU 多快、前面那首跑到哪，
 * 猜錯了比不講更糟（使用者會照那個數字決定要不要再等）。
 */
function laneSummary(lane) {
  if (!lane) return "";
  const waiting = Number(lane.waiting) || 0;
  const running = Number(lane.running_count) || 0;
  if (!running && !waiting) return "處理流水線：現在很閒";
  if (!waiting) return "處理流水線：正在處理 1 首";
  return `處理流水線：正在處理 1 首，另外 ${waiting} 首排隊中（輪流，不是先到先跑）`;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    ROOMS_DEFAULT_ID,
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
  };
}
