/**
 * 跨場次段落趨勢的呈現 (Cross-session Section Trend View)
 *
 * 後端 `backend/services/section_trends.py` 算出「這個人在這首歌一向強在哪一段」，
 * 這裡只負責把那份資料變成一句話與一張圖。刻意抽成獨立模組，因為同一份結論
 * 要出現在兩個地方：舞台的唱畢結算畫面、點歌台的「我的成績」分頁。
 * 兩邊各寫一次的話，同一份資料會講出兩種說法（一邊說「低 9 分」、一邊說「9%」）。
 *
 * 畫法沿用段落對決的**對拉長條圖**：中線是「這個人自己的平均」，
 * 往右是主場、往左是弱點。畫絕對命中率的話看到的是這首歌哪一段難唱，
 * 不是這個人哪一段弱 —— 而後者才是練歌時要看的東西。
 *
 * 純資料邏輯：不碰 DOM、不發網路請求，所以能用 node --test 直接跑。
 */

// 長條圖最多畫幾段。與段落對決同一個上限（MAX_DUEL_ROWS）——
// 結算畫面只亮九秒，十段長條圖沒有人看得完。
const MAX_TREND_ROWS = 6;

function toPercentPoints(delta) {
  return Math.round(Number(delta) * 100);
}

/**
 * 偏差寫成「+12」／「−9」個百分點。負號用 U+2212 而不是連字號，對齊才不會歪。
 *
 * 四捨五入到 0 的那一格寫「±0」而不是「+0」：JS 的 Math.round 往 +∞ 捨，
 * −0.4 個百分點會變成「+0」—— 一個往下偏的段落顯示成正的，那是錯的。
 */
function formatDelta(delta) {
  const pp = toPercentPoints(delta);
  if (pp === 0) return "±0";
  return pp > 0 ? `+${pp}` : `−${Math.abs(pp)}`;
}

/**
 * 一句話結論。回傳 `{ kind, headline, detail }`，`kind` 決定前端要不要顯示：
 *   * `none` —— 沒有段落資料（舊紀錄或第一次唱），整塊不顯示。
 *   * `waiting` —— 有資料但場次不夠，講「再唱幾次」而不是沉默：
 *     使用者才知道這塊不是壞了，是還在累積。
 *   * `flat` —— 場次夠但唱得很平均，明說「沒有明顯弱點」（那是好消息）。
 *   * `verdict` —— 點得出主場／弱點。
 */
function describeTrend(trend) {
  const t = trend || {};
  if (t.status === "insufficient") {
    const need = Math.max(1, Number(t.needed) || 1);
    return {
      kind: "waiting",
      headline: `📊 再唱 ${need} 次，就能看出你在這首歌一向強在哪一段`,
      detail: `已累積 ${Number(t.performances) || 0} 次演唱`,
    };
  }
  if (t.status !== "ok") return { kind: "none", headline: "", detail: "" };

  // 句子裡不帶正負號：方向已經由「高」／「低」講完了，
  // 「一向低 +13 個百分點」要讀兩次才看得懂。長條圖上的數字才需要正負號
  // （那裡沒有句子，符號就是方向）。
  const magnitude = (delta) => Math.abs(toPercentPoints(delta));
  const parts = [];
  if (t.home) parts.push(`🏠 主場 ${t.home.label}（一向高 ${magnitude(t.home.mean_delta)} 個百分點）`);
  if (t.weak) parts.push(`📈 待加強 ${t.weak.label}（一向低 ${magnitude(t.weak.mean_delta)} 個百分點）`);

  const times = `近 ${t.performances} 次`;
  if (!parts.length) {
    return {
      kind: "flat",
      headline: `📊 ${times}每一段都唱得很平均，沒有明顯弱點`,
      detail: directionText(t),
    };
  }
  return { kind: "verdict", headline: `📊 ${times}：${parts.join("　")}`, detail: directionText(t) };
}

/** 整體在進步還是退步。判不出來（場次不足）就不講 —— 硬掰一個方向比不講更糟。 */
function directionText(trend) {
  const t = trend || {};
  if (!t.direction || t.delta_accuracy == null) return "";
  const pp = Math.abs(toPercentPoints(t.delta_accuracy));
  if (t.direction === "improving") return `↗ 這段期間進步了 ${pp} 個百分點`;
  if (t.direction === "slipping") return `↘ 這段期間退了 ${pp} 個百分點`;
  return "→ 表現穩定";
}

/**
 * 對拉長條圖的資料：每段一列，`ratio` 是相對於「最大偏差」的長度（0~1）。
 *
 * 用最大偏差正規化而不是固定刻度：整首唱得很平均時固定刻度會畫出一排看不見的
 * 短線（看起來像壞了），而正規化之後至少看得出哪一段偏哪一邊 ——
 * 幅度小到不值得點名這件事由 `named` 表達（沒點名的畫淡色），不是靠長度。
 */
function trendRows(trend, maxRows = MAX_TREND_ROWS) {
  const t = trend || {};
  const rows = Array.isArray(t.sections) ? t.sections : [];
  if (!rows.length) return [];

  // 超過上限時留下偏差最大的幾段，但仍照歌曲順序畫
  // （排名是另一件事，長條圖要能照著歌順著讀）
  let picked = rows;
  if (rows.length > maxRows) {
    const ranked = rows.slice().sort(
      (a, b) => Math.abs(b.mean_delta) - Math.abs(a.mean_delta));
    const keep = new Set(ranked.slice(0, maxRows));
    picked = rows.filter((r) => keep.has(r));
  }

  const peak = Math.max(...picked.map((r) => Math.abs(Number(r.mean_delta) || 0)), 0);
  return picked.map((r) => {
    const delta = Number(r.mean_delta) || 0;
    return {
      label: String(r.label || ""),
      delta,
      text: formatDelta(delta),
      side: delta >= 0 ? "home" : "weak",
      // peak 為 0（每一段都剛好等於平均）時不能除下去，整排畫成 0 長度就好
      ratio: peak > 0 ? Math.abs(delta) / peak : 0,
      accuracy: Number(r.mean_accuracy) || 0,
      named: r.named === true,
      appearances: Number(r.appearances) || 0,
    };
  });
}

if (typeof window !== "undefined") {
  window.TrendView = { describeTrend, trendRows, formatDelta, directionText, MAX_TREND_ROWS };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { describeTrend, trendRows, formatDelta, directionText, MAX_TREND_ROWS };
}
