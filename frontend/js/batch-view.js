/**
 * 排程預處理分頁的純顯示邏輯。
 *
 * 抽成獨立模組是為了測得到：「一批 120 首歌要在畫面上顯示哪 8 首」、
 * 「跨午夜的時段字串怎麼寫」這種規則錯了不會拋例外，只會讓使用者看到
 * 一個看似正常但其實在騙人的畫面 —— 那是最貴的錯。
 * 這裡的函式一律不碰 DOM、不發請求，輸入什麼就決定輸出什麼。
 */

// 項目狀態 → 徽章文字與樣式
const ITEM_STATUS = {
  PENDING: { label: "等待", cls: "pending" },
  RUNNING: { label: "處理中", cls: "running" },
  DONE: { label: "完成", cls: "done" },
  ERROR: { label: "失敗", cls: "error" },
  SKIPPED: { label: "略過", cls: "skip" },
};

const JOB_STATUS = {
  SCHEDULED: "🕒 已排程",
  RUNNING: "⚙️ 處理中",
  FINISHED: "✅ 已完成",
  CANCELLED: "✖ 已取消",
};

// 畫面上的挑選順序：正在跑的 → 失敗的 → 還沒跑的 → 跑完的 → 略過的。
// 順序就是「使用者現在想知道什麼」：進度、出了什麼問題、還剩多少。
const ITEM_RANK = { RUNNING: 0, ERROR: 1, PENDING: 2, DONE: 3, SKIPPED: 4 };

function itemStatusMeta(status) {
  return ITEM_STATUS[status] || ITEM_STATUS.PENDING;
}

function jobStatusLabel(status) {
  return JOB_STATUS[status] || status || "";
}

/**
 * 一批可能有上百首，全列出來會把畫面淹掉。
 * 挑重要的前 limit 首，同分時保持原本的排列順序（使用者貼上的順序）。
 */
function pickBatchItems(items, limit = 8) {
  if (!Array.isArray(items)) return [];
  return items
    .map((item, idx) => ({ item, idx }))
    .sort((a, b) => {
      const ra = ITEM_RANK[a.item && a.item.status];
      const rb = ITEM_RANK[b.item && b.item.status];
      return (ra === undefined ? 9 : ra) - (rb === undefined ? 9 : rb) || a.idx - b.idx;
    })
    .slice(0, Math.max(0, limit))
    .map(e => e.item);
}

/** 排程時段的人話版本。起訖相同代表使用者把時段限制關掉了。 */
function formatWindow(win) {
  if (!win) return "";
  if (win.all_day) return "全天候";
  const pad = (h) => String(h).padStart(2, "0");
  return `${pad(win.start_hour)}:00–${pad(win.end_hour)}:00`;
}

/** 狀態燈：正在處理 > 可以開工 > 待命。三種狀態不能同時亮，優先度就是這個順序。 */
function statusDotClass(state) {
  if (!state) return "waiting";
  if (state.working) return "working";
  return state.can_run ? "ready" : "waiting";
}

/**
 * 一批的計數摘要。0 的項目不顯示 —— 「⚠️ 0」會讓人以為出了什麼事。
 * 全部都是 0（剛建立、還沒開跑）時回空字串，呼叫端自己決定要寫什麼。
 */
function summarizeCounts(progress) {
  const p = progress || {};
  return [
    p.done ? `✅ ${p.done}` : "",
    p.error ? `⚠️ ${p.error}` : "",
    p.skipped ? `⏭ ${p.skipped}` : "",
    p.pending ? `🕒 ${p.pending}` : "",
  ].filter(Boolean).join(" ・ ");
}

/** 項目右側的細節：處理中顯示進度，失敗顯示原因，其餘留白。 */
function itemDetailText(item) {
  if (!item) return "";
  if (item.status === "RUNNING") {
    return `${item.status_text || ""} ${item.progress || 0}%`.trim();
  }
  if (item.status === "ERROR") return item.error || item.status_text || "";
  return "";
}

if (typeof window !== "undefined") {
  window.BatchView = {
    ITEM_STATUS, JOB_STATUS, itemStatusMeta, jobStatusLabel,
    pickBatchItems, formatWindow, statusDotClass, summarizeCounts, itemDetailText,
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    ITEM_STATUS, JOB_STATUS, ITEM_RANK, itemStatusMeta, jobStatusLabel,
    pickBatchItems, formatWindow, statusDotClass, summarizeCounts, itemDetailText,
  };
}
