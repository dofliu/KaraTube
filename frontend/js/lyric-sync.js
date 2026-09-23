/**
 * 字幕同步的兩個數字（純邏輯，沒有 DOM）。
 *
 * 「字幕對不上」有兩種原因，v1.21 為止它們被塞在同一個數字裡：
 *
 *   1. **這台裝置的延遲** —— 藍牙喇叭、HDMI 電視。每一首歌都一樣，而且瀏覽器
 *      已經量掉一部分（`AudioContext.outputLatency`），人補的只是殘差。
 *   2. **這首歌的 LRC 偏差** —— 抓到的歌詞是別的版本。只有這一首對不上，
 *      沒有任何量測值，只有人耳知道。
 *
 * 拆開之後最重要的那個決定是：**方向鍵與滑桿動的是「這首歌」。**
 * 兩種預設的失敗模式不對稱 ——
 *
 *   * 預設動裝置（舊行為）：一首爛 LRC 調出來的 +300ms 會**無聲地**跟著機器
 *     走到下一首、下一組客人、明天晚上。下一個唱的人看不到前一個人按過鍵盤，
 *     只會覺得「這台機器的字幕怪怪的」，然後往反方向再修一次 —— 兩三個晚上
 *     之後那個數字跟任何真實的物理量都沒有關係了。
 *   * 預設動這首歌：換了藍牙喇叭的人要每首重調。但這個失敗模式**大聲、有界、
 *     而且會自己報到**：同一個方向、差不多的量、每一首都這樣。機器看得見那個
 *     規律（所以可以主動問「要不要設成本機基準？」），人也看得見（第三次往同一
 *     邊拉的時候他自己就想到了），而且一個動作就救得回來。
 *
 * 所以這一支提供兩件事：把兩個數字說清楚的文案，以及那個「連續同方向」偵測器。
 * 偵測器只**指出**規律，永遠不自動套用 —— 機器看得到樣態，看不到成因
 * （同一個上傳者的三首歌共用同一份爛 LRC，跟喇叭一點關係都沒有）。
 */

// 兩個數字共用的上下限，跟後端 lyric_offsets.MAX_OFFSET_MS 一致
// （frontend/tests/lyric-sync.test.js 有一條測試把兩邊釘在一起）。
const SYNC_MAX_OFFSET_MS = 2000;

// 「連續同方向」要看幾首才開口。三首是「巧合」與「規律」之間的位置 ——
// 兩首太容易湊巧，四首的話使用者早就自己發現了。
const SYNC_BASELINE_SAMPLES = 3;
// 這幾首的調整量要多接近才算同一件事（毫秒）。差太多通常是各自的 LRC 問題。
const SYNC_BASELINE_SPREAD_MS = 60;
// 太小的調整不算訊號：50ms 以下多半是有人在試按鍵。
const SYNC_BASELINE_MIN_MS = 50;

/**
 * 舞台每一幀的**兩條時間軸**。這是整個功能唯一會「無聲造成傷害」的地方，
 * 所以從 renderLoop 抽出來讓測試守著。
 *
 *   scoreTime —— 現在從喇叭出來的是哪一刻的聲音。導唱音符（pitch.json）是從
 *     人聲軌抽的、活在**音訊時間軸**上，所以評分與音準線只能用它。
 *   lyricTime —— scoreTime 再扣掉這首歌的 LRC 偏差。歌詞檔對不對得上音訊是
 *     那份 LRC 自己的問題，只有字幕（與從歌詞算出來的段落邊界）該跟著它移動。
 *
 * 把 songMs 也減進 scoreTime 就是把計分視窗整段搬離真實人聲：為某一首歌調
 * +300ms 等於那一首的音準率與 Combo 無聲下降，而畫面上看不出任何異狀。
 */
function syncTimes({ audioTime = 0, outputLatency = 0, deviceMs = 0, songMs = 0 } = {}) {
  const base = Number(audioTime) || 0;
  const latency = Number(outputLatency) || 0;
  const scoreTime = base - latency - clampSyncMs(deviceMs) / 1000;
  return { scoreTime, lyricTime: scoreTime - clampSyncMs(songMs) / 1000 };
}

/**
 * 伺服器上那份裝置延遲，這台舞台機要不要採用？回傳要採用的值，或 `null`＝不動。
 *
 * 規則：**只有還沒有自己意見的裝置才採用**（全新的機器、清過瀏覽器資料的
 * 櫃檯機 —— 那是一個起始值，不是命令）。已經調過的一律以自己的 localStorage
 * 為準，因為那個數字是「這條音訊路徑」的屬性，別台舞台機的喇叭跟我無關。
 *
 * 這條規則是有代價的教訓：共享狀態裡的 `lyric_offset_ms` 只活在伺服器記憶體裡，
 * 伺服器一重開就是 0。少了這道守門，重開伺服器之後每一台舞台都會被那個 0
 * 洗掉自己的喇叭補償，解鎖時再把 0 推回去 —— 一次靜悄悄的全機歸零。
 */
function adoptDeviceOffset({ serverMs, localMs = 0, hasLocal = false, unlocked = false } = {}) {
  if (serverMs === undefined || serverMs === null) return null;
  if (hasLocal || unlocked) return null;
  const next = clampSyncMs(serverMs);
  return next === clampSyncMs(localMs) ? null : next;
}

/** 夾成合法的偏移毫秒數。字串、null、NaN 一律當 0。 */
function clampSyncMs(value) {
  const ms = Math.round(Number(value));
  if (!Number.isFinite(ms)) return 0;
  return Math.max(-SYNC_MAX_OFFSET_MS, Math.min(SYNC_MAX_OFFSET_MS, ms));
}

/** 帶正負號的顯示字串（+150 ms / −80 ms / 0 ms）。用真正的減號，不是連字號。 */
function offsetLabel(ms) {
  const v = clampSyncMs(ms);
  if (v === 0) return "0 ms";
  return `${v > 0 ? "+" : "−"}${Math.abs(v)} ms`;
}

/** 正值＝字幕延後、負值＝字幕提前。這句話要跟數字一起出現，否則沒有人知道要按哪一邊。 */
function offsetDirection(ms) {
  const v = clampSyncMs(ms);
  if (v === 0) return "";
  return v > 0 ? "字幕延後" : "字幕提前";
}

/**
 * 舞台 toast 的兩行字。
 *
 * 第一行只講**剛剛被改動的那一個數字**（大字）：使用者按一下就看到哪個數字在動，
 * toast 本身就是教學。第二行是另一個數字與按鍵提示（小字、灰色）。
 *
 * `changed` 是 "song" 或 "device"；`autoMs` 是瀏覽器自動補償的量（唯讀）。
 */
function syncToastLines({ songMs = 0, deviceMs = 0, autoMs = 0, changed = "song",
                          songTitle = "", hasSong = true } = {}) {
  const song = clampSyncMs(songMs);
  const device = clampSyncMs(deviceMs);
  const auto = Math.round(Number(autoMs) || 0);

  if (!hasSong && changed === "song") {
    // 待機時沒有東西可以對齊，這時候調本來就沒有意義。刻意不偷偷改裝置值 ——
    // 那正是「沒人看著畫面時悄悄改掉全機基準」的那條路。
    return {
      main: "沒有歌在唱，字幕校正是綁在歌上的",
      hint: `這台機器的延遲請按 S 進音訊設定調整（目前 ${offsetLabel(device)}）`,
    };
  }

  if (changed === "device") {
    return {
      main: `🔊 這台機器 ${offsetLabel(device)}（${offsetDirection(device) || "不補償"}）`,
      hint: `這首歌 ${offsetLabel(song)}　・　自動補償 ${auto} ms　・　合計 ${offsetLabel(song + device)}`,
    };
  }

  const name = songTitle ? `　${songTitle}` : "";
  return {
    main: `🎬 這首歌 ${offsetLabel(song)}（${offsetDirection(song) || "不校正"}）${name}`,
    hint: `這台機器 ${offsetLabel(device)}　・　自動補償 ${auto} ms　・　` +
          `← → 50ms　・　Shift 10ms　・　0 只歸零這首　・　S 調整本機`,
  };
}

/** 點歌台滑桿旁邊那一行。手機上沒有鍵盤提示，所以只講數字與合計。 */
function deckOffsetSummary({ songMs = 0, deviceMs = 0, hasSong = true } = {}) {
  if (!hasSong) return "沒有歌在唱";
  const song = clampSyncMs(songMs);
  const device = clampSyncMs(deviceMs);
  if (device === 0) return `${offsetLabel(song)}${song ? `（${offsetDirection(song)}）` : ""}`;
  return `${offsetLabel(song)}　＋ 舞台 ${offsetLabel(device)} ＝ ${offsetLabel(song + device)}`;
}

/**
 * 「連續同方向」偵測：最近幾首歌各自**第一次**的調整量，看得出是喇叭的延遲嗎？
 *
 * 只收第一次是刻意的：同一首歌的後續微調是在修飾同一個判斷，算進去會讓
 * 一首歌投好幾票。回傳 `null`（沒話說）或 `{ suggestMs, samples }`。
 *
 * 建議值取**中位數**不取平均 —— 一首 800ms 的爛 LRC 不該把基準整個拖走。
 */
function baselineSuggestion(firstAdjustments) {
  const list = (Array.isArray(firstAdjustments) ? firstAdjustments : [])
    .map(clampSyncMs)
    .filter(ms => Math.abs(ms) >= SYNC_BASELINE_MIN_MS);
  if (list.length < SYNC_BASELINE_SAMPLES) return null;

  const recent = list.slice(-SYNC_BASELINE_SAMPLES);
  const positive = recent.every(ms => ms > 0);
  const negative = recent.every(ms => ms < 0);
  if (!positive && !negative) return null;          // 方向不一致：不是同一件事

  const sorted = [...recent].sort((a, b) => a - b);
  const spread = sorted[sorted.length - 1] - sorted[0];
  if (Math.abs(spread) > SYNC_BASELINE_SPREAD_MS) return null;   // 量差太多：各自的問題

  const median = sorted[Math.floor(sorted.length / 2)];
  return { suggestMs: median, samples: recent.length };
}

/** 偵測到規律時多出來的那一行字。只提示，不自動套用。 */
function baselineHint(suggestion) {
  if (!suggestion) return "";
  return `最近 ${suggestion.samples} 首都往同一邊調了約 ${offsetLabel(suggestion.suggestMs)}` +
         ` —— 這比較像喇叭的延遲，按 L 可以設成本機基準`;
}

/**
 * 「升級成本機基準」的確認文字。
 *
 * 必須講滿三件事：會把裝置基準改成多少、會同時調整幾首已校正的歌、
 * 以及第二台舞台機不會跟著變（它有自己的音訊路徑）。
 * 不講第二件事的話，使用者不會知道這個動作碰了他昨天校好的 47 首歌。
 */
function rebaseConfirmText({ deltaMs = 0, deviceMs = 0, tunedCount = 0 } = {}) {
  const delta = clampSyncMs(deltaMs);
  const next = clampSyncMs(deviceMs + delta);
  const lines = [
    `把這首歌的 ${offsetLabel(delta)} 設成這台舞台機的延遲基準？`,
    "",
    `・本機基準：${offsetLabel(deviceMs)} → ${offsetLabel(next)}`,
  ];
  if (tunedCount > 0) {
    lines.push(`・已校正過的 ${tunedCount} 首歌會各減掉 ${offsetLabel(delta)}（總效果不變）`);
  }
  lines.push("・如果還有第二台舞台機，那一台要自己再調一次（延遲是那條音訊路徑的屬性）");
  lines.push("", "反向再做一次就回得去。");
  return lines.join("\n");
}

if (typeof window !== "undefined") {
  window.LyricSync = {
    SYNC_MAX_OFFSET_MS, SYNC_BASELINE_SAMPLES, SYNC_BASELINE_SPREAD_MS, SYNC_BASELINE_MIN_MS,
    clampSyncMs, syncTimes, adoptDeviceOffset,
    offsetLabel, offsetDirection, syncToastLines, deckOffsetSummary,
    baselineSuggestion, baselineHint, rebaseConfirmText,
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    SYNC_MAX_OFFSET_MS, SYNC_BASELINE_SAMPLES, SYNC_BASELINE_SPREAD_MS, SYNC_BASELINE_MIN_MS,
    clampSyncMs, syncTimes, adoptDeviceOffset,
    offsetLabel, offsetDirection, syncToastLines, deckOffsetSummary,
    baselineSuggestion, baselineHint, rebaseConfirmText,
  };
}
