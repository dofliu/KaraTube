/**
 * 錄唱回放的判斷規則 (Take Rules)
 *
 * 錄音本身在瀏覽器做（MediaRecorder，見 take-recorder.js），這一支只回答
 * 幾個「要不要 / 怎麼寫」的問題。抽成純邏輯模組有兩個理由：
 *
 *   1. 「這一次要不要留」的規則舞台端與點歌台都會講（一邊是決定，
 *      一邊是解釋為什麼清單上沒有剛剛那一首），兩邊各寫一次就會分岔。
 *   2. MediaRecorder 在 node 裡不存在，規則寫在錄音器內部就完全測不到 ——
 *      而錯在這裡的後果是「唱完一首，錄音沒了」，事後補不回來。
 *
 * 純資料邏輯：不碰 DOM、不發網路請求。
 */

// 想要的容器依序試。Chrome / Firefox 給 webm(Opus)，Safari 只給 mp4(AAC)。
// Opus 在 48kHz 單聲道約 40~64 kbps，一首歌 3 分鐘大約 1~1.5 MB，
// 這是「聽得出唱得好不好」與「不要吃光磁碟」之間合理的位置。
const TAKE_MIME_CANDIDATES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/ogg;codecs=opus",
  "audio/mp4",
];

// 錄音位元率。刻意不用預設值：瀏覽器預設常常是 128 kbps 立體聲，
// 錄的是同一支麥克風進來的單聲道人聲＋伴奏，那個位元率只是把檔案變大。
const TAKE_BITS_PER_SECOND = 96000;

/**
 * 挑一個這台瀏覽器錄得出來的容器。都不支援回傳 ""（呼叫端就不錄）。
 *
 * `isTypeSupported` 傳進來而不是直接抓 `window.MediaRecorder`，
 * 才能在 node 裡把各種瀏覽器的支援情形都跑一遍。
 */
function pickTakeMime(isTypeSupported, candidates) {
  const list = candidates || TAKE_MIME_CANDIDATES;
  if (typeof isTypeSupported !== "function") return "";
  for (const mime of list) {
    try {
      if (isTypeSupported(mime)) return mime;
    } catch (e) { /* 舊瀏覽器可能直接丟例外，當成不支援 */ }
  }
  return "";
}

/**
 * 這一次要不要留下來。回傳 `{ keep, reason }`，reason 是給人看的一句話。
 *
 * 三道門，每一道都是為了同一件事 —— 錄音跟歌曲快取共用一顆磁碟，
 * 留下沒有人會回頭聽的檔案，代價是把真正想找的那一次擠掉：
 *
 *   * 功能沒開 / 瀏覽器不支援 —— 根本不會有資料。
 *   * **沒有人真的開口唱** —— 前奏放一半被切歌、麥克風擺在桌上，
 *     錄到的是伴奏。判斷用的是「唱了多久」而不是「錄了多久」。
 *   * 檔案是空的 —— MediaRecorder 開起來就失敗時會給 0 bytes 的 Blob，
 *     存進去清單上就多一筆點下去沒聲音的項目。
 */
function shouldKeepTake(info) {
  const o = info || {};
  if (!o.enabled) return { keep: false, reason: "錄音功能沒有開啟" };
  if (!o.supported) return { keep: false, reason: "這個瀏覽器不支援錄音" };
  if (!(Number(o.bytes) > 0)) return { keep: false, reason: "錄音是空的" };
  const voiced = Number(o.voicedMs) || 0;
  const minSing = Number(o.minSingMs) || 0;
  if (voiced < minSing) {
    return {
      keep: false,
      reason: `這一次只唱了 ${Math.round(voiced / 1000)} 秒（要 ${Math.round(minSing / 1000)} 秒以上才留）`,
    };
  }
  return { keep: true, reason: "" };
}

/** 3 分 24 秒寫成 3:24。超過一小時才出現小時位。 */
function formatTakeDuration(ms) {
  const total = Math.max(0, Math.round((Number(ms) || 0) / 1000));
  const s = total % 60;
  const m = Math.floor(total / 60) % 60;
  const h = Math.floor(total / 3600);
  const mm = h > 0 ? String(m).padStart(2, "0") : String(m);
  return `${h > 0 ? `${h}:` : ""}${mm}:${String(s).padStart(2, "0")}`;
}

/**
 * 檔案大小。KB 以下不寫小數（「0.3 KB」比「273 B」難懂），
 * MB 以上寫一位小數（「287.4 MB」與「287 MB」在配額快滿時差很多）。
 */
function formatTakeSize(bytes) {
  const n = Math.max(0, Number(bytes) || 0);
  if (n < 1024) return `${Math.round(n)} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * 配額用量的一句話。上限為 0（不限制）時不講那一半 ——
 * 「12 / 0 首」會被讀成「已經超過上限」。
 */
function quotaSummary(stats) {
  const s = stats || {};
  const count = Number(s.count) || 0;
  const maxCount = Number(s.max_count) || 0;
  const bytes = Number(s.total_bytes) || 0;
  const maxBytes = Number(s.max_bytes) || 0;
  const countPart = maxCount > 0 ? `${count} / ${maxCount} 首` : `${count} 首`;
  const sizePart = maxBytes > 0
    ? `${formatTakeSize(bytes)} / ${formatTakeSize(maxBytes)}`
    : formatTakeSize(bytes);
  const pinned = Number(s.pinned_count) || 0;
  return `${countPart}・${sizePart}${pinned > 0 ? `（${pinned} 筆保留中）` : ""}`;
}

/**
 * 配額快滿時的提醒。門檻設在 90%：滿了才講已經來不及
 * （下一首唱完就有一筆舊的被刪掉了），而那一筆很可能就是想留的。
 */
function quotaWarning(stats) {
  const s = stats || {};
  const count = Number(s.count) || 0;
  const maxCount = Number(s.max_count) || 0;
  const bytes = Number(s.total_bytes) || 0;
  const maxBytes = Number(s.max_bytes) || 0;
  const pinned = Number(s.pinned_count) || 0;
  const ratios = [];
  if (maxCount > 0) ratios.push(count / maxCount);
  if (maxBytes > 0) ratios.push(bytes / maxBytes);
  const worst = ratios.length ? Math.max(...ratios) : 0;
  if (worst < 0.9) return "";
  if (maxCount > 0 && pinned >= maxCount) {
    return "保留中的錄音已佔滿配額，新的錄音會存不進來 —— 取消幾筆保留或到設定頁調高上限。";
  }
  return "錄音配額快滿了，再錄下去會從最舊、沒有標記保留的那一筆開始刪。";
}

/** 一筆錄音在清單上的副標題：唱的人、分數、長度、大小。 */
function takeSubtitle(entry) {
  const e = entry || {};
  const bits = [];
  if (e.singer) bits.push(e.singer);
  if (e.mode === "duet") bits.push("對唱");
  if (Number(e.score) > 0) bits.push(`${Number(e.score).toLocaleString()} 分${e.grade ? ` ${e.grade}` : ""}`);
  if (Number(e.duration_ms) > 0) bits.push(formatTakeDuration(e.duration_ms));
  bits.push(formatTakeSize(e.bytes));
  return bits.join("・");
}

if (typeof window !== "undefined") {
  window.TakeRules = {
    TAKE_MIME_CANDIDATES, TAKE_BITS_PER_SECOND,
    pickTakeMime, shouldKeepTake, formatTakeDuration, formatTakeSize,
    quotaSummary, quotaWarning, takeSubtitle,
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    TAKE_MIME_CANDIDATES, TAKE_BITS_PER_SECOND,
    pickTakeMime, shouldKeepTake, formatTakeDuration, formatTakeSize,
    quotaSummary, quotaWarning, takeSubtitle,
  };
}
