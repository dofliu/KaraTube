/**
 * 音色等化器的純邏輯 (Tone EQ)
 *
 * 商用 KTV 擴大機面板上一定有的那三顆旋鈕：**高音 / 中音 / 低音**，
 * 而且麥克風一套、音樂一套。這台機器在這一版之前只有一顆**單向**的
 * 「高頻柔化」—— 它只能把聲音變悶，不能把聲音變亮。所以包廂裡最常見的
 * 兩句抱怨（「我的聲音好悶」「這首歌的低音太轟」）在這之前**沒有任何一顆
 * 旋鈕回答得了**，只能去動音量，而動音量會把另一個人也一起改掉。
 *
 * 這一支只放「要調成什麼樣子」的算術，不碰 Web Audio：
 *
 *   1. **補償量（makeup）算錯的後果是破音或忽然變小聲**，而那兩件事在包廂裡
 *      都會被講成「機器壞了」。它必須有測試釘著，而 BiquadFilterNode 在 node
 *      裡不存在 —— 算術寫在音訊引擎內部就一條都測不到。
 *   2. 點歌台（要畫滑桿、要說出「現在是什麼音色」）與舞台端（要套用）
 *      講的是同一組規則，兩邊各寫一次就會分岔。
 *
 * 純資料邏輯：不碰 DOM、不發網路請求、不依賴 AudioContext。
 */

// 兩套等化器。分成兩套而不是一套套用在全部聲音上，理由跟商用擴大機一樣：
// 目的不同。麥克風那套是**修這個人的聲音**（悶、尖、轟），音樂那套是
// **這個包廂喜歡的曲風**（重低音、清亮）。混成一套的話，「我的聲音太悶」
// 會被調成「整首歌的低音一起轟」，而下一個人唱的時候沒有人知道要調回去。
//
// 範圍刻意不一樣：
//   * 麥克風 ±12 dB —— 麥克風的音色差異本來就大（領夾、動圈、手機內建），
//     而且它是**生料**，調壞了下一首就調回來。
//   * 音樂 ±8 dB —— 伴奏是已經混好、而且已經被自動音量平衡（EBU R128）
//     對到 −14 LUFS 的成品。動它等於在破壞那個保證，所以給的空間比較小。
const EQ_TARGETS = {
  mic: {
    limitDb: 12,
    // 低音 200Hz：人聲的胸腔共鳴與近接效應（嘴巴貼著麥克風那種「轟」）就在這。
    // 更低沒有意義 —— 前級已經有一顆 110Hz 的高通把那底下全砍掉了。
    // 中音 1.8kHz：「人聲在伴奏裡浮不浮得出來」的位置（presence）。
    // 高音 4.5kHz：空氣感。刻意**不**跟既有的高頻柔化（highshelf 5.5kHz）
    //   放在同一點 —— 疊在一起的話兩顆旋鈕會互相抵消，而使用者的結論會是
    //   「其中一顆壞了」，不會想到是兩顆在同一個頻率上拔河。
    bands: [
      { key: "bass", type: "lowshelf", freq: 200, q: 0.7, label: "低音" },
      { key: "mid", type: "peaking", freq: 1800, q: 0.9, label: "中音" },
      { key: "treble", type: "highshelf", freq: 4500, q: 0.7, label: "高音" },
    ],
    // 補償用的能量佔比。人聲的能量集中在中頻，所以中音那一格的權重最大 ——
    // 權重錯了的症狀是「只調中音，音量卻沒跟著補回來」。
    weights: { bass: 0.28, mid: 0.50, treble: 0.22 },
  },
  music: {
    limitDb: 8,
    // 低音 120Hz：鼓與貝斯，不是人聲的 200Hz。兩套用同一個頻率的話，
    // 「音樂低音 +6」會順便把導唱人聲的胸腔聲一起抬起來。
    // 高音 6kHz：亮度。低於這個會動到導唱人聲的齒音區。
    bands: [
      { key: "bass", type: "lowshelf", freq: 120, q: 0.7, label: "低音" },
      { key: "mid", type: "peaking", freq: 1000, q: 0.8, label: "中音" },
      { key: "treble", type: "highshelf", freq: 6000, q: 0.7, label: "高音" },
    ],
    weights: { bass: 0.38, mid: 0.42, treble: 0.20 },
  },
};

const BAND_KEYS = ["bass", "mid", "treble"];

// 補償量的上下限。**刻意不對稱**，而且這是整支模組最重要的一個數字。
//
// 往下（使用者在提升、機器要補回來）給到 −12 dB：不補的話三段都拉 +10 的人
// 會直接把限幅器打到底，整首歌聽起來像被壓扁的罐頭，而他只是想要「亮一點」。
//
// 往上（使用者在削減、機器要補回來）只給 +3 dB：削減的動機通常正是
// 「太大聲／在嘯叫／太轟」，把它原封不動補回去等於沒調 —— 而且**補回去的
// 增益會原封不動回到回授迴路裡**，於是「把高音砍掉來壓嘯叫」這個最直覺的
// 動作會變成沒有效果，使用者查不出原因。
const MAKEUP_MIN_DB = -12;
const MAKEUP_MAX_DB = 3;

// 一鍵音色。商用機面板上那幾顆「人聲美化／重低音」。
// 每一組都是「一句話講得完的意圖」而不是調音師的曲線 ——
// 包廂裡沒有人會為了一首歌去推三根滑桿，但他講得出「我太悶了」。
const EQ_PRESETS = {
  mic: [
    { id: "flat", label: "原音", bass: 0, mid: 0, treble: 0, note: "三段都不動，等於沒開等化器" },
    { id: "bright", label: "清亮", bass: -2, mid: 1, treble: 4, note: "聲音悶、埋在伴奏裡時用" },
    { id: "warm", label: "厚實", bass: 4, mid: 0, treble: -2, note: "聲音單薄、太尖時用" },
    { id: "clear", label: "人聲突出", bass: -3, mid: 4, treble: 1, note: "伴奏很滿、聽不到自己時用" },
  ],
  music: [
    { id: "flat", label: "原音", bass: 0, mid: 0, treble: 0, note: "照歌本身的混音播" },
    { id: "deep", label: "重低音", bass: 5, mid: -1, treble: 2, note: "舞曲、快歌" },
    { id: "soft", label: "柔和", bass: 1, mid: 0, treble: -3, note: "抒情歌，或喇叭偏刺耳的包廂" },
    { id: "clarity", label: "清晰", bass: -2, mid: 2, treble: 3, note: "伴奏糊成一團時用" },
  ],
};

/** 這個目標（mic / music）的規格。認不得一律當 mic —— 回 undefined 的話呼叫端會整頁壞掉。 */
function targetSpec(target) {
  return EQ_TARGETS[target] || EQ_TARGETS.mic;
}

/** 單一段的夾限。NaN / 字串 / undefined 一律當 0（沒調），不是丟例外。 */
function clampBand(db, limitDb) {
  const limit = Number.isFinite(limitDb) ? Math.abs(limitDb) : 12;
  const n = Number(db);
  if (!Number.isFinite(n)) return 0;
  // 半格（0.5 dB）是人耳在包廂裡分得出來的最小差異，再細下去只是讓滑桿難推
  const snapped = Math.round(n * 2) / 2;
  return Math.max(-limit, Math.min(limit, snapped));
}

/**
 * 把一組（可能來自舊版前端、可能缺欄位、可能是字串）的設定正規化。
 *
 * 缺的欄位補 0 而不是沿用舊值：這支函式的呼叫端是「畫面要畫什麼」與
 * 「音訊要套什麼」，兩邊都必須從同一份完整的值出發。
 */
function normalizeTone(tone, target) {
  const spec = targetSpec(target);
  const src = tone && typeof tone === "object" ? tone : {};
  const out = {};
  for (const key of BAND_KEYS) out[key] = clampBand(src[key], spec.limitDb);
  return out;
}

/** 三段都是 0 —— 畫面要據此顯示「原音」，而不是「低 0／中 0／高 0」。 */
function isFlat(tone) {
  const t = tone || {};
  return BAND_KEYS.every((k) => Math.abs(Number(t[k]) || 0) < 0.01);
}

/**
 * 寬頻增益的估計值：三段依能量佔比加權相加。
 *
 * 這是一個**粗估**，而且刻意是粗估 —— 精確解要對這一首歌的頻譜積分，
 * 而那個值每一幀都在變，拿它當增益就是第二個自動增益，兩個會互相追著跑
 * （症狀是音量以一兩秒為週期上下呼吸，沒有人查得出來）。
 */
function broadbandDb(tone, target) {
  const spec = targetSpec(target);
  const t = normalizeTone(tone, target);
  let sum = 0;
  for (const key of BAND_KEYS) sum += (spec.weights[key] || 0) * t[key];
  return Math.round(sum * 10) / 10;
}

/**
 * 補償增益（dB）。等化器調完之後要補多少才讓整體音量大致不變。
 *
 * **只看設定值，不看訊號**（理由見 broadbandDb）。所以它是一個靜態的數字：
 * 推完滑桿就定下來，之後一幀都不會再動 —— 唱歌的人不會感覺到音量在呼吸。
 */
function makeupDb(tone, target) {
  const net = broadbandDb(tone, target);
  // `net === 0` 要特判：JS 的 `-0` 會讓「沒調」的補償變成 −0，
  // 而 −0 在畫面上印出來是 "-0"，使用者會以為等化器在偷偷動什麼
  const comp = net === 0 ? 0 : -net;
  return Math.max(MAKEUP_MIN_DB, Math.min(MAKEUP_MAX_DB, Math.round(comp * 10) / 10));
}

/** 補償增益的線性倍率（直接餵給 GainNode）。 */
function makeupGain(tone, target) {
  return Math.pow(10, makeupDb(tone, target) / 20);
}

/**
 * 每一段濾波器的實際參數。音訊引擎照這張表設節點，順序就是串接順序。
 */
function bandSpecs(tone, target) {
  const spec = targetSpec(target);
  const t = normalizeTone(tone, target);
  return spec.bands.map((b) => ({
    key: b.key, label: b.label, type: b.type, freq: b.freq, q: b.q, gainDb: t[b.key],
  }));
}

/**
 * 嘯叫風險的提示（只對麥克風那一套有意義）。
 *
 * 回授幾乎都發生在 1.5k~6kHz —— 房間的共振、麥克風的指向性與喇叭的效率
 * 在那一帶同時達到最大。所以**中高音的提升是直接加進迴路增益裡的**：
 * 高音 +8 dB 等於把「麥克風可以離喇叭多近」這條線整個往後推。
 *
 * 這裡只**說**，不自動把滑桿拉回去：防嘯叫已經會在真的叫起來時挖凹槽，
 * 而一個會自己改掉使用者剛推上去的滑桿的系統，是包廂裡最難查的那種壞法。
 *
 * @returns {{level:string, message:string}} level: safe | watch | risky
 */
function feedbackRisk(tone, target) {
  if ((target || "mic") !== "mic") return { level: "safe", message: "" };
  const t = normalizeTone(tone, "mic");
  // 低音不計：200Hz 以下幾乎不會自激（房間在那一帶的 Q 很低），
  // 把它算進去只會讓「厚實」那一組預設一直在示警，而示警多了就沒有人看。
  const score = Math.max(0, t.treble) + Math.max(0, t.mid) * 0.6;
  if (score < 3) return { level: "safe", message: "" };
  if (score < 7) {
    return {
      level: "watch",
      message: "中高音提升之後比較容易嘯叫，麥克風別對著喇叭。",
    };
  }
  return {
    level: "risky",
    message: "中高音提升很多，多人模式下很可能嘯叫。真的叫起來先把高音拉回 0，再看要不要降麥克風音量。",
  };
}

/** 這組值對得上哪一個一鍵音色？對不上回 "custom"（畫面才知道哪一顆要亮）。 */
function matchPreset(tone, target) {
  const list = EQ_PRESETS[target] || EQ_PRESETS.mic;
  const t = normalizeTone(tone, target);
  for (const p of list) {
    if (BAND_KEYS.every((k) => Math.abs(t[k] - p[k]) < 0.01)) return p.id;
  }
  return "custom";
}

/** 取一個一鍵音色的三個值（認不得回原音，不是 null —— 呼叫端不該為此多一條分支）。 */
function presetTone(id, target) {
  const list = EQ_PRESETS[target] || EQ_PRESETS.mic;
  const found = list.find((p) => p.id === id) || list[0];
  return { bass: found.bass, mid: found.mid, treble: found.treble };
}

/** 單一格的顯示文字：0 要寫成 "0"，正值要帶 +，負值用真的減號（−，不是 hyphen）。 */
function formatDb(db) {
  const n = clampBand(db, 24);
  if (Math.abs(n) < 0.01) return "0";
  const body = Number.isInteger(n) ? String(Math.abs(n)) : Math.abs(n).toFixed(1);
  return (n > 0 ? "+" : "−") + body;
}

/**
 * 一行說明。畫面上就印這一句 —— 三個數字分開看沒有人判讀得出來現在是什麼音色。
 */
function toneSummary(tone, target) {
  const t = normalizeTone(tone, target);
  if (isFlat(t)) return "原音（等化器沒有作用）";
  const spec = targetSpec(target);
  const parts = spec.bands.map((b) => `${b.label} ${formatDb(t[b.key])}`);
  const comp = makeupDb(t, target);
  const tail = Math.abs(comp) < 0.05 ? "" : `，整體補 ${formatDb(comp)} dB`;
  return `${parts.join("／")} dB${tail}`;
}

if (typeof window !== "undefined") {
  window.ToneEq = {
    EQ_TARGETS, EQ_PRESETS, BAND_KEYS, MAKEUP_MIN_DB, MAKEUP_MAX_DB,
    targetSpec, clampBand, normalizeTone, isFlat, broadbandDb,
    makeupDb, makeupGain, bandSpecs, feedbackRisk,
    matchPreset, presetTone, formatDb, toneSummary,
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    EQ_TARGETS, EQ_PRESETS, BAND_KEYS, MAKEUP_MIN_DB, MAKEUP_MAX_DB,
    targetSpec, clampBand, normalizeTone, isFlat, broadbandDb,
    makeupDb, makeupGain, bandSpecs, feedbackRisk,
    matchPreset, presetTone, formatDb, toneSummary,
  };
}
