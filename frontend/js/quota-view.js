/**
 * 每人待唱上限（點歌額度）的說法 (Quota View)
 *
 * 規則本身在伺服器（backend/services/song_quota.py）—— 額度必須只有一份，
 * 否則兩支手機會各自算出不同的剩餘量，而其中一支會在按下去之後才發現被擋。
 * 這一支只負責**把規則講成人話**。
 *
 * 為什麼一個「被拒絕」的訊息值得抽成一支有測試的模組：這是整個系統裡唯一一個
 * 對使用者說「不行」的地方。說不清楚的話，使用者的下一個動作是再按一次
 * （然後再被擋一次，然後認定點歌壞了）。所以這句話一定要講滿三件事：
 *
 *   誰（是你、還是「沒取暱稱」那一整桶）
 *   現在幾首（以及上限是多少）
 *   什麼時候可以再點（就是你排最前面那一首唱完的時候）
 *
 * 少講最後一件，前兩件就只是在解釋一個死路。
 *
 * 純資料邏輯：不碰 DOM、不發網路請求。
 */

// 沒取暱稱的那一桶在畫面上的叫法。跟輪唱那一支用同一個詞 ——
// 同一個概念在兩行字裡有兩個名字，使用者會以為那是兩件事。
// （刻意不共用常數：兩支模組載入順序不保證，重複宣告會讓後面那一支整支不執行。）
const QUOTA_ANON_LABEL = "沒取暱稱";

/** 上限按鈕上的字。0 = 不限。狀態寫在字上，不能只靠顏色（包廂燈是暗的）。 */
function quotaLimitLabel(limit) {
  const n = Math.max(0, Math.floor(Number(limit) || 0));
  return n > 0 ? `🎫 每人 ${n} 首` : "🎫 不限首數";
}

/** 一個人的用量：「小明 2/3」，排滿了多一個字。 */
function quotaSingerSummary(singer, limit) {
  if (!singer) return "";
  const name = singer.anonymous ? QUOTA_ANON_LABEL : (singer.name || QUOTA_ANON_LABEL);
  const n = Math.max(0, Math.floor(Number(limit) || 0));
  if (n <= 0) return `${name} ${singer.pending || 0}`;
  const used = `${name} ${singer.pending || 0}/${n}`;
  return singer.full ? `${used} 滿` : used;
}

/**
 * 佇列上方那一行：現在誰排了幾首。
 *
 * 跟輪序那一行一樣最多列 `limit` 個人（手機上一行就滿了），其餘寫成「…等 N 人」。
 * 但這裡多一條規則：**排滿的人一定排進被列出來的那幾個**。這一行存在的理由
 * 就是「誰快滿了」，把滿的人擠到「…等 N 人」裡面等於整行白寫。
 */
function quotaLine(summary, maxShown = 4) {
  if (!summary) return "";
  const limit = Math.max(0, Math.floor(Number(summary.limit) || 0));
  if (limit <= 0) return "";
  const singers = (summary.singers || []).filter(s => s && s.pending);
  if (!singers.length) return `每人最多排 ${limit} 首待唱・目前沒有人排隊`;

  // 滿的人優先，其餘維持原本的順序（＝他下一首在佇列的位置）
  const ordered = [...singers.filter(s => s.full), ...singers.filter(s => !s.full)];
  const shown = ordered.slice(0, maxShown)
    .map(s => quotaSingerSummary(s, limit)).join("　");
  const rest = ordered.length - Math.min(ordered.length, maxShown);
  const head = `每人最多排 ${limit} 首待唱`;
  return rest > 0 ? `${head}　${shown}　…等 ${ordered.length} 人` : `${head}　${shown}`;
}

/**
 * 被擋下來時要說的那句話（見本檔開頭的三件事）。
 *
 * 沒取暱稱的那一桶講的是完全不同的一句話，因為他的處境也完全不同：
 * 他不是「自己排太多」，是**跟所有沒取名的人共用一份額度**。這件事不講明白，
 * 他看到的是「我明明只點了一首就說我排了三首」—— 那看起來就是個 bug。
 * 而且這一句要帶出解法（取個暱稱），因為取暱稱真的會讓他拿到自己的額度。
 */
function quotaRejectionNote(quota) {
  if (!quota) return "點歌額度已滿。";
  const limit = Math.max(0, Math.floor(Number(quota.limit) || 0));
  const pending = Math.max(0, Math.floor(Number(quota.pending) || 0));
  const position = Number(quota.next_position);
  // 「什麼時候可以再點」：他排最前面的那一首唱完的時候。
  const when = Number.isFinite(position) && position >= 1
    ? `你的下一首排在第 ${Math.floor(position)} 位，唱完就空出一格。`
    : "等其中一首唱完就可以再點。";

  if (quota.anonymous) {
    return `⚠️ 「${QUOTA_ANON_LABEL}」已經排了 ${pending} 首待唱（上限 ${limit} 首）。`
      + `沒取暱稱的所有人共用同一份額度 —— 到右上角取個暱稱，就有屬於自己的 ${limit} 首。`;
  }
  const who = (quota.name || "").trim();
  return `⚠️ ${who ? `${who} ` : ""}已經排了 ${pending} 首待唱（上限 ${limit} 首）。${when}`;
}

/**
 * 點歌成功之後補的那半句：「還可以再排 1 首」。
 *
 * 只在剩下的額度**少到需要知道**的時候才說。額度開 5 首、他排第 1 首就跳
 * 「還可以再排 4 首」是純粹的雜訊，而每一句雜訊都會讓下一句真正重要的話
 * 被略過不看。剩 0 首時講的是下一步（等一首唱完），不是「剩 0 首」。
 */
function quotaAddNote(quota, noisyBelow = 2) {
  if (!quota) return "";
  const limit = Math.max(0, Math.floor(Number(quota.limit) || 0));
  if (limit <= 0) return "";
  const remaining = Number(quota.remaining);
  if (!Number.isFinite(remaining)) return "";
  if (remaining <= 0) return "這是你的最後一首額度，等一首唱完再點下一首";
  if (remaining > noisyBelow) return "";
  return `還可以再排 ${Math.floor(remaining)} 首`;
}

/** 上限被改動時的通知。講的是「接下來新點的歌」，已經排好的一首都不動。 */
function quotaChangeNote(limit) {
  const n = Math.max(0, Math.floor(Number(limit) || 0));
  if (n <= 0) return "已取消點歌額度：每個人想排幾首都可以。";
  return `🎫 每人最多排 ${n} 首待唱。已經排好的歌不會被刪掉，`
    + `超過的人要等自己的歌唱完才點得了新的。`;
}

if (typeof window !== "undefined") {
  window.QuotaView = {
    QUOTA_ANON_LABEL,
    quotaLimitLabel, quotaSingerSummary, quotaLine,
    quotaRejectionNote, quotaAddNote, quotaChangeNote,
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    QUOTA_ANON_LABEL,
    quotaLimitLabel, quotaSingerSummary, quotaLine,
    quotaRejectionNote, quotaAddNote, quotaChangeNote,
  };
}
