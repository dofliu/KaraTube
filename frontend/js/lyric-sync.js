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

// --- 兩點校正（速度）---
//
// 「字幕對不上」的第三種症狀是**越唱越歪**：抓到的歌詞是速度不同的版本
// （為了避開版權比對，把整首調快 1~6% 是常見的上傳手法）。第一句對得剛剛好、
// 副歌慢半拍、片尾差兩三秒 —— 這種歪**加一個常數永遠修不好**，使用者會一路
// 按方向鍵按到整首都對不上，然後以為機器壞了。
//
// 修它需要兩個數字（一條直線：`音訊時間 = rate × 歌詞時間 + offset`），
// 而人能做的量測只有一種：「這一句現在開始」。兩個這樣的量測就唯一決定那條線。
// 所以不給滑桿 —— 1.03 跟 1.04 在片頭聽起來完全一樣（差 0.3 秒要唱到第三分鐘
// 才聽得出來），給滑桿等於要人用一個沒有即時回饋的動作去猜四位數。
const SYNC_MIN_RATE = 0.85;
const SYNC_MAX_RATE = 1.15;
// 比這個小的速度差直接當沒有（見後端 lyric_offsets.RATE_EPSILON）
const SYNC_RATE_EPSILON = 0.002;

// 兩點之間至少要隔這麼久（秒）。這是整個功能最重要的一個數字 ——
// **速度的誤差會被跨距放大**：兩點各有人按鍵的反應時間雜訊（±100ms 上下），
// 解出來的 rate 誤差大約是「雜訊 ÷ 跨距」。
//
//   跨距 20 秒  →  rate 誤差 1%   →  四分鐘的歌片尾多歪 2.4 秒（比原本更糟）
//   跨距 90 秒  →  rate 誤差 0.2% →  片尾 0.5 秒（聽不太出來）
//
// 也就是說「兩點靠太近」不是稍微不準，是**會把一首本來只歪一點的歌弄得更歪**，
// 而且是往看不見的方向（片尾沒有人在盯著字幕）。所以寧可請使用者再唱一段。
const ANCHOR_MIN_SPAN_S = 60;

// 按下去的位置離最近的句首超過這麼多（秒）就不收。到這個量級已經不是「對不上」，
// 是抓到了完全不同的一首歌 —— 那該按「重算歌詞」，不是繼續校正一條錯的直線。
const ANCHOR_SNAP_WINDOW_S = 8;

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
 *
 * `rate` 是兩點校正解出來的速度（見檔頭）。它只出現在 lyricTime 這一行，
 * 理由跟 songMs 一樣、而且更強：speed 錯的話評分視窗不是平移，是**越走越偏**，
 * 到副歌就完全對不到真實人聲了。
 *
 * 注意兩個偏移都在**同一個分子裡**：`(scoreTime − songMs) ÷ rate`。所以裝置延遲
 * 與這首歌的偏移仍然可以互相搬移（「升級成本機基準」那個動作在 rate ≠ 1 時
 * 照樣正確），而速度只縮放時間軸本身。
 */
function syncTimes({ audioTime = 0, outputLatency = 0, deviceMs = 0, songMs = 0,
                     rate = 1 } = {}) {
  const base = Number(audioTime) || 0;
  const latency = Number(outputLatency) || 0;
  const scoreTime = base - latency - clampSyncMs(deviceMs) / 1000;
  return {
    scoreTime,
    lyricTime: (scoreTime - clampSyncMs(songMs) / 1000) / clampRate(rate),
  };
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

/**
 * 夾成合法的速度倍率。認不得的、0、NaN 一律當 1.0。
 *
 * 「認不得就當 1.0」在這裡比偏移的「當 0」重要得多：rate 進到**分母**裡，
 * 一個 0 溜進去整首歌的字幕會直接消失（Infinity）或停在第一句 ——
 * 那不是「校正沒生效」，是舞台白掉。
 */
function clampRate(value) {
  const rate = Number(value);
  if (!Number.isFinite(rate)) return 1;
  // 0 與負數**不是「很慢的歌」，是壞值**（時間停住或倒著走）。夾成 0.85 會把
  // 一個明顯的錯誤變成一個看起來合理的數字，然後那首歌歪得莫名其妙。
  if (rate <= 0) return 1;
  const clamped = Math.max(SYNC_MIN_RATE, Math.min(SYNC_MAX_RATE, rate));
  if (Math.abs(clamped - 1) < SYNC_RATE_EPSILON) return 1;
  return Math.round(clamped * 1e6) / 1e6;
}

/** 速度的顯示字串（1.024× / 正常速度）。 */
function rateLabel(rate) {
  const r = clampRate(rate);
  return r === 1 ? "正常速度" : `${r.toFixed(3)}×`;
}

/**
 * 速度在講什麼。`音訊 = rate × 歌詞`，所以 rate > 1 代表同一段歌詞在這個上傳
 * 版本裡走得比較久 —— 也就是**抓到的那份歌詞是比較快的版本**。
 * 這句話要跟數字一起出現，否則 1.024 只是一個沒有意義的四位數。
 */
function rateDirection(rate) {
  const r = clampRate(rate);
  if (r === 1) return "";
  const pct = Math.abs(r - 1) * 100;
  return r > 1 ? `歌詞版本快了約 ${pct.toFixed(1)}%` : `歌詞版本慢了約 ${pct.toFixed(1)}%`;
}

/** 秒數的顯示字串（3.2 秒）。校正的量級在秒，不在毫秒。 */
function secondsLabel(seconds) {
  const s = Number(seconds);
  if (!Number.isFinite(s)) return "0 秒";
  return `${Math.abs(s).toFixed(1)} 秒`;
}

/**
 * 按下去的那一刻，使用者指的是哪一句？
 *
 * 唯一可用的線索是時間：取**歌詞時鐘上離按鍵最近的句首**。這是整個功能裡
 * 唯一一個機器猜得到、卻猜不準的地方 —— 字幕正歪著（那就是他按鍵的原因），
 * 所以「最近的句首」有可能是隔壁那一句，而對錯一句的後果不是差一點，
 * 是解出一條完全錯的直線。
 *
 * 機器沒辦法自己確認，所以改成**讓人確認**：回傳配對到的那一句歌詞原文，
 * 舞台把它印出來（「第 1 點：對到「xxx」」）。對錯了再按一次 A 就重來 ——
 * 把一個推不出來的推論，換成一個一眼看得出來的畫面。
 *
 * 回傳 `{ lineIdx, startS, text, gapS }`，或 `null`（沒有歌詞／離所有句首都太遠）。
 */
function snapAnchor(lyrics, lyricTime) {
  const lines = Array.isArray(lyrics) ? lyrics : [];
  const at = Number(lyricTime);
  if (!lines.length || !Number.isFinite(at)) return null;

  let best = null;
  lines.forEach((line, idx) => {
    const start = Number(line && line.start);
    if (!Number.isFinite(start)) return;
    const gap = at - start;
    if (!best || Math.abs(gap) < Math.abs(best.gapS)) {
      best = {
        lineIdx: Number.isFinite(Number(line.line_idx)) ? Number(line.line_idx) : idx,
        startS: start,
        text: String((line && line.text) || ""),
        gapS: gap,
      };
    }
  });
  if (!best || Math.abs(best.gapS) > ANCHOR_SNAP_WINDOW_S) return null;
  return best;
}

/**
 * 兩點解一條直線：`音訊時間 = rate × 歌詞時間 + offset`。
 *
 * 每一點是 `{ lrcS, audioS, text }` —— `lrcS` 是配對到的那一句在歌詞檔裡的
 * 起始時間，`audioS` 是按下去那一刻的**音訊時間**（已扣掉輸出延遲與裝置補償，
 * 也就是 scoreTime）。
 *
 * 人的反應時間（聽到才按，大約 +150~250ms）刻意**不補**：那是一個對兩點
 * 都一樣的常數，所以它在相減時整個消掉，一點都不會進到 rate 裡，只會落在
 * offset 上 —— 而 offset 正是使用者按 ← → 一秒鐘就修得掉的那個數字。
 * 拿一個猜的常數去修一個他本來就修得掉的東西，只會讓兩邊都說不清楚。
 *
 * 回傳 `{ ok: true, rate, offsetMs, spanS, flat }`，或
 * `{ ok: false, reason }`（reason: "points" | "span" | "order" | "rate" | "offset"）。
 * **拒絕比硬算出一個數字重要**：算出來的直線沒有人看得懂對不對，
 * 而它錯的地方在片尾 —— 沒有人在那裡盯著字幕。
 */
function solveTwoPoint(pointA, pointB) {
  const a = normalizeAnchor(pointA);
  const b = normalizeAnchor(pointB);
  if (!a || !b) return { ok: false, reason: "points" };

  // 照歌詞時間排先後。使用者一定是照播放順序按的，所以順序反過來只會是
  // 「其中一點對到別的句子了」——這裡先排好，讓 order 那條檢查說得出話。
  const [first, second] = a.lrcS <= b.lrcS ? [a, b] : [b, a];
  const spanS = second.lrcS - first.lrcS;
  const audioSpanS = second.audioS - first.audioS;

  if (spanS < ANCHOR_MIN_SPAN_S) return { ok: false, reason: "span", spanS };
  if (audioSpanS <= 0) return { ok: false, reason: "order", spanS };

  const rawRate = audioSpanS / spanS;
  if (!Number.isFinite(rawRate) || rawRate < SYNC_MIN_RATE || rawRate > SYNC_MAX_RATE) {
    return { ok: false, reason: "rate", rate: rawRate, spanS };
  }

  const rate = clampRate(rawRate);
  const offsetMs = Math.round((first.audioS - rate * first.lrcS) * 1000);
  if (Math.abs(offsetMs) > SYNC_MAX_OFFSET_MS) {
    return { ok: false, reason: "offset", offsetMs, rate, spanS };
  }
  // flat ＝ 速度其實沒問題，兩點只是把同一個常數偏移量了兩次。
  // 這時候存一個 1.0008 進去只會讓「這首校正過速度」這句話失真。
  return { ok: true, rate, offsetMs, spanS, flat: rate === 1 };
}

function normalizeAnchor(point) {
  if (!point) return null;
  const lrcS = Number(point.lrcS);
  const audioS = Number(point.audioS);
  if (!Number.isFinite(lrcS) || !Number.isFinite(audioS)) return null;
  return { lrcS, audioS, text: String(point.text || "") };
}

/**
 * 新舊兩條直線在**片尾**差多少秒 —— 也就是「不校正的話，唱到最後字幕會歪多少」。
 *
 * 校正的價值全部集中在後半段（前面本來就對得差不多，不然使用者第一句就發現了），
 * 所以要講的數字是片尾那一個。只說「速度 1.024×」的話沒有人知道那是大是小。
 */
function driftCorrectionS({ rate = 1, offsetMs = 0, prevRate = 1, prevOffsetMs = 0,
                            durationS = 0 } = {}) {
  const t = Math.max(0, Number(durationS) || 0);
  const dRate = clampRate(rate) - clampRate(prevRate);
  const dOffset = (clampSyncMs(offsetMs) - clampSyncMs(prevOffsetMs)) / 1000;
  return dRate * t + dOffset;
}

/**
 * 兩點校正每一步的舞台文案。`event.kind` 決定說什麼：
 *
 *   idle     沒有歌／沒有歌詞可以對
 *   nosnap   按下去的位置離所有句首都太遠（多半是抓到完全不同的歌詞）
 *   first    收下第 1 點
 *   restart  第 2 點離第 1 點太近 —— 當成「重新標記第 1 點」（見下）
 *   reject   兩點解不出合理的直線
 *   applied  解出來了，已經套用
 *
 * **restart 這一條是刻意的**：兩點太近時，把它當成「使用者剛剛按錯了、
 * 現在重標」比當成「錯誤」更常對 —— 按錯的人本來就會立刻再按一次。
 * 如果當成錯誤而保留舊的第 1 點，那個按錯的點就永遠拔不掉，
 * 他只能等它自己過期（而他不知道有過期這回事）。
 */
function twoPointToastLines(event = {}) {
  const kind = String(event.kind || "");

  if (kind === "idle") {
    return {
      main: "沒有歌在唱，兩點校正是綁在歌上的",
      hint: "播放中按 A 標第 1 點（副歌之前），唱到後段再按一次 A",
    };
  }
  if (kind === "nosnap") {
    return {
      main: "⚓ 這一下離任何一句歌詞都太遠，沒有收",
      hint: `按下去的位置要在某一句的 ${ANCHOR_SNAP_WINDOW_S} 秒內。` +
            "差這麼多通常是抓到了別首歌的歌詞 —— 那要用快取管理的「重算歌詞」",
    };
  }
  if (kind === "first" || kind === "restart") {
    const quoted = event.text ? `「${event.text}」` : "這一句";
    const lead = kind === "restart"
      ? "⚓ 兩點離太近，這一下當成重新標記第 1 點"
      : `⚓ 第 1 點：對到${quoted}`;
    const hint = kind === "restart"
      ? `第 1 點改成${quoted}　・　兩點至少要隔 ${ANCHOR_MIN_SPAN_S} 秒，` +
        "太近的話解出來的速度比原本更歪"
      : `再唱一段，到後段（副歌或片尾）某一句開口的瞬間再按一次 A。` +
        `對錯句子了就再按一次 A 重標　・　按 0 取消`;
    return { main: lead, hint };
  }
  if (kind === "reject") {
    return rejectLines(event);
  }
  if (kind === "applied") {
    const rate = clampRate(event.rate);
    const drift = Number(event.driftS) || 0;
    const main = rate === 1
      ? `🎬 速度沒問題，只校正了偏移 ${offsetLabel(event.offsetMs)}`
      : `🎬 速度 ${rateLabel(rate)}　偏移 ${offsetLabel(event.offsetMs)}`;
    const parts = [];
    if (rate !== 1) parts.push(rateDirection(rate));
    if (Math.abs(drift) >= 0.2) {
      parts.push(`片尾的字幕會往${drift > 0 ? "後" : "前"}移 ${secondsLabel(drift)}`);
    }
    parts.push("不對就按 0 全部歸零");
    return { main, hint: parts.join("　・　") };
  }
  return { main: "", hint: "" };
}

function rejectLines(event) {
  const reason = String(event.reason || "");
  if (reason === "span") {
    return {
      main: "⚓ 兩點離太近，沒有算",
      hint: `至少要隔 ${ANCHOR_MIN_SPAN_S} 秒（目前 ${secondsLabel(event.spanS)}）——` +
            "跨距太短時，按鍵的反應時間會被放大成好幾秒的速度誤差，比不校正更糟",
    };
  }
  if (reason === "order") {
    return {
      main: "⚓ 這兩點的先後對不起來，沒有算",
      hint: "多半是有一點對到了別的句子。按 A 重新標第 1 點",
    };
  }
  if (reason === "rate") {
    const r = Number(event.rate);
    const shown = Number.isFinite(r) ? `${r.toFixed(3)}×` : "一個離譜的值";
    return {
      main: `⚓ 算出來的速度是 ${shown}，不合理`,
      hint: "真正的變速上傳落在 0.85×~1.15× 之間，超出去幾乎一定是有一點對到" +
            "別的句子了。按 A 重新標第 1 點",
    };
  }
  if (reason === "offset") {
    return {
      main: `⚓ 算出來要移動 ${offsetLabel(event.offsetMs)}，超過上限`,
      hint: "差到兩秒以上通常不是對不準，是抓到了別首歌的歌詞 ——" +
            "用快取管理的「重算歌詞」比較快",
    };
  }
  return { main: "⚓ 這兩點算不出結果", hint: "按 A 重新標第 1 點" };
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
          `← → 50ms　・　Shift 10ms　・　0 只歸零這首　・　S 調整本機` +
          // 「越唱越歪」是方向鍵永遠修不好的那一種，所以提示要出現在他正在
          // 按方向鍵的時候 —— 那正是他一邊按一邊覺得「怎麼調都不對」的那一刻。
          `<br>越唱越歪（前面對、後面歪）按 A 做兩點校正`,
  };
}

/** 點歌台滑桿旁邊那一行。手機上沒有鍵盤提示，所以只講數字與合計。 */
function deckOffsetSummary({ songMs = 0, deviceMs = 0, hasSong = true, rate = 1 } = {}) {
  if (!hasSong) return "沒有歌在唱";
  const song = clampSyncMs(songMs);
  const device = clampSyncMs(deviceMs);
  // 速度校正過的歌要說出來：手機上這一行是唯一看得到 rate 的地方，
  // 而「這首歌被動過速度」會改變使用者怎麼解讀滑桿（拉滑桿只平移，不改速度）。
  const speed = clampRate(rate) === 1 ? "" : `　・　速度 ${rateLabel(rate)}`;
  if (device === 0) {
    return `${offsetLabel(song)}${song ? `（${offsetDirection(song)}）` : ""}${speed}`;
  }
  return `${offsetLabel(song)}　＋ 舞台 ${offsetLabel(device)} ＝ ` +
         `${offsetLabel(song + device)}${speed}`;
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
    SYNC_MIN_RATE, SYNC_MAX_RATE, SYNC_RATE_EPSILON,
    ANCHOR_MIN_SPAN_S, ANCHOR_SNAP_WINDOW_S,
    clampSyncMs, clampRate, syncTimes, adoptDeviceOffset,
    offsetLabel, offsetDirection, rateLabel, rateDirection, secondsLabel,
    syncToastLines, deckOffsetSummary,
    baselineSuggestion, baselineHint, rebaseConfirmText,
    snapAnchor, solveTwoPoint, driftCorrectionS, twoPointToastLines,
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    SYNC_MAX_OFFSET_MS, SYNC_BASELINE_SAMPLES, SYNC_BASELINE_SPREAD_MS, SYNC_BASELINE_MIN_MS,
    SYNC_MIN_RATE, SYNC_MAX_RATE, SYNC_RATE_EPSILON,
    ANCHOR_MIN_SPAN_S, ANCHOR_SNAP_WINDOW_S,
    clampSyncMs, clampRate, syncTimes, adoptDeviceOffset,
    offsetLabel, offsetDirection, rateLabel, rateDirection, secondsLabel,
    syncToastLines, deckOffsetSummary,
    baselineSuggestion, baselineHint, rebaseConfirmText,
    snapAnchor, solveTwoPoint, driftCorrectionS, twoPointToastLines,
  };
}
