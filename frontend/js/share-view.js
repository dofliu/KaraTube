/**
 * 分享頁的純顯示邏輯 (Share View)
 *
 * 掃了 QR 進來的那一頁要回答的其實只有四句話：這是哪一次演唱、唱得怎麼樣、
 * 還能聽多久、打不開的話是為什麼。這一支負責把資料翻成那四句話，
 * 不碰 DOM、不發網路請求（DOM 那一半在 share-page.js）。
 *
 * 抽成獨立模組的理由跟 take-rules.js 一樣：這些字串的錯法是「畫面上寫了
 * 還有 -3 小時」或「已過期的連結寫著還有 0 分鐘」，只有在真的把頁面打開
 * 才看得到 —— 而分享頁是給客人看的那一頁，錯字的代價比後台高。
 */

// 時間格式沿用錄唱回放那一套（`3:24`）。刻意不在這裡再寫一份：
// 同一段錄音的長度在後台清單與分享頁上寫成兩種樣子，是最沒必要的分岔。
// 瀏覽器裡 take-rules.js 先載入（見 share.html 的順序），node 測試走 require。
const SHARE_TAKE_RULES = (typeof window !== "undefined" && window.TakeRules)
  ? window.TakeRules
  : (typeof require === "function" ? require("./take-rules.js") : null);

// 伺服器產生的 token 形狀（urlsafe base64）。前端先擋一次是為了少打一次
// 一定會失敗的請求 —— 真正的把關在後端。
const SHARE_TOKEN_RE = /^[A-Za-z0-9_-]{16,64}$/;

/**
 * 從網址找出 token。`/share/<token>` 是正規寫法，`?t=<token>` 是退路
 * （有些通訊軟體會把路徑型網址的最後一段吃掉或加上追蹤參數）。
 * 找不到或形狀不對回傳 ""。
 */
function shareTokenFromLocation(loc) {
  const o = loc || {};
  const path = String(o.pathname || "");
  const m = path.match(/\/share\/([^/?#]+)/);
  let token = m ? m[1] : "";
  if (!token) {
    const q = String(o.search || "").match(/[?&]t=([^&#]+)/);
    token = q ? q[1] : "";
  }
  try {
    token = decodeURIComponent(token);
  } catch (e) { /* 壞掉的 percent-encoding：照原樣送去讓下面的正則擋掉 */ }
  return SHARE_TOKEN_RE.test(token) ? token : "";
}

/**
 * 「還能聽多久」。
 *
 * 刻意不給秒數：一個 23 小時後過期的連結寫成「還有 82800 秒」沒有人在讀，
 * 而剩不到一分鐘時寫「還有 0 分鐘」會讓人以為已經壞了。
 */
function expiryPhrase(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  if (s <= 0) return "這個連結已經過期";
  if (s < 60) return "剩不到 1 分鐘就過期";
  if (s < 3600) return `還有 ${Math.floor(s / 60)} 分鐘就過期`;
  if (s < 48 * 3600) return `還有 ${Math.floor(s / 3600)} 小時就過期`;
  return `還有 ${Math.floor(s / 86400)} 天就過期`;
}

/** 快過期了要提醒（一小時內）。畫面上用不同顏色講同一件事。 */
function expiryIsUrgent(seconds) {
  const s = Number(seconds) || 0;
  return s > 0 && s < 3600;
}

/** 副標題：誰唱的、什麼時候、多長。分數另外一行，因為那是重點。 */
function shareMetaLine(rec) {
  const e = rec || {};
  const bits = [];
  if (e.singer) bits.push(e.singer);
  if (e.mode === "duet") bits.push("對唱");
  if (e.artist) bits.push(e.artist);
  if (Number(e.duration_ms) > 0 && SHARE_TAKE_RULES) {
    bits.push(SHARE_TAKE_RULES.formatTakeDuration(e.duration_ms));
  }
  const when = String(e.created_at || "").replace("T", " ").slice(0, 16);
  if (when) bits.push(when);
  return bits.join("・");
}

/**
 * 分數那一行。沒有分數就回空字串讓畫面整行不要出現 ——
 * 「0 分」會被讀成「唱得很爛」，但實際上是那一次根本沒開評分。
 */
function shareScoreLine(rec) {
  const e = rec || {};
  const score = Number(e.score) || 0;
  if (score <= 0) return "";
  const acc = Number(e.accuracy) || 0;
  const parts = [`${score.toLocaleString()} 分`];
  if (e.grade) parts.push(e.grade);
  if (acc > 0) parts.push(`音準 ${Math.round(acc * 100)}%`);
  return parts.join("・");
}

/**
 * 打不開時要講的那句話。
 *
 * 伺服器講得比前端清楚（過期、撤銷、次數用完是三件事），所以 detail 優先；
 * 連不到伺服器時 detail 是空的，這時候才用狀態碼猜。分享連結最常見的
 * 失敗其實是第三種：人已經離開那個網路了 —— 所以那一句要講出網路。
 */
function shareErrorMessage(status, detail) {
  const text = String(detail || "").trim();
  if (text) return text;
  const code = Number(status) || 0;
  if (code === 404) return "這個分享連結不存在（可能是網址少了幾個字）";
  if (code === 410) return "這個分享連結已經失效了";
  if (code === 403) return "這台機器目前沒有開放錄音分享";
  return "連不上包廂的那台機器 —— 分享連結只在同一個網路（或設定過對外網址）裡打得開。";
}

if (typeof window !== "undefined") {
  window.ShareView = {
    SHARE_TOKEN_RE, shareTokenFromLocation, expiryPhrase, expiryIsUrgent,
    shareMetaLine, shareScoreLine, shareErrorMessage,
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    SHARE_TOKEN_RE, shareTokenFromLocation, expiryPhrase, expiryIsUrgent,
    shareMetaLine, shareScoreLine, shareErrorMessage,
  };
}
