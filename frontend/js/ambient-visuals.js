/**
 * 情境背景 (Ambient Stage Background) — 純資料邏輯
 *
 * 商用點歌機（錢櫃的情境畫面、金嗓的「背景影片」、JOYSOUND 的映像）都有這一套：
 * 沒有 MV 的時候，畫面不是黑的，是一段會動的情境視覺。
 * 這件事看起來只是裝飾，但包廂裡的黑畫面有很實際的副作用 ——
 * 沒有 MV 的歌看起來就像「這首歌壞掉了」，而且整個房間只剩字幕在發光。
 *
 * 這個檔只負責「要放哪一種背景、現在該有多亮、要不要跟著鼓點動」，
 * 完全不碰 DOM 與 Web Audio（真正畫圖的是 ambient-stage.js），
 * 所以可以用 node --test 把下面這幾件事守住：
 *
 *   1. 什麼時候該讓出畫面給真正的 MV（`AmbientDirector.layer`）。
 *   2. 抓到的 MV 其實是一張靜態圖時要認得出來（`StillnessProbe`）。
 *   3. 背景永遠不准閃（`MAX_LUMA_SLEW`）—— 見下方「不准閃」一節。
 *
 * ## 不准閃
 *
 * 背景會跟著音樂動，最直覺的做法是「鼓點一來就把整個畫面打亮」。
 * 那是**不能做**的：舞台螢幕在暗房間裡佔滿整面牆，高對比的亮度變化
 * 一秒三次以上就是光敏性癲癇的誘發條件（一般安全建議是 3 Hz 以下）。
 * 所以這裡把「亮度」與「動態」分成兩件事：
 *   * 亮度（`luma`）走限速器，每秒最多變 `MAX_LUMA_SLEW`，而且擺幅本來就小。
 *   * 鼓點只推動「動態」（`beatPulse`：粒子噴一下、波形推一下），不碰亮度。
 * 這樣背景還是活的，但它不會閃。
 *
 * ## 不准跟歌詞搶
 *
 * 第二個限制同樣重要：這是伴唱機，字幕看不清楚就什麼都不用談。
 * 亮度總共被壓三道：主題的 `baseLuma`、設定頁的亮度上限，
 * 以及畫面下半字幕區的 vignette。三道疊起來的結果只能用眼睛確認 ——
 * 第一版每一道看起來都「壓一點點」，疊完是一片全黑，
 * 是真的 render 出來才看到的（見 docs/PROGRESS_LOG.md）。
 * 「好看」在這裡永遠排在「看得清楚」後面，但「看不到」不算壓得好。
 */

// --- 亮度與動態 ---

// 亮度每秒最多變化多少（0~1 的刻度）。0.45 表示從最暗到最亮至少要 2.2 秒，
// 等效閃爍頻率遠低於 3 Hz 的安全線。這個值是這個檔案最不該被「微調」的常數。
const MAX_LUMA_SLEW = 0.45;

// 音樂能量的平滑：進得快（鼓點要跟得上）、退得慢（句尾不要忽然暗掉）。
const ENERGY_ATTACK_TAU = 0.08;
const ENERGY_RELEASE_TAU = 0.55;

// 鼓點推一下之後的衰減時間常數。0.18 秒 ≈ 看得到「彈一下」但不會拖成呼吸燈。
const BEAT_PULSE_TAU = 0.18;

// 待機（沒有歌在播）時的能量。不是 0 —— 完全不動的背景跟靜止圖沒兩樣，
// 但也不能照歌曲的擺幅動，待機畫面在包廂裡會亮一整個晚上。
const IDLE_ENERGY = 0.18;

// --- 頻譜 ---

// AnalyserNode 的位元組是 dB 對映（預設 -100 ~ -30 dB → 0 ~ 255）。
// 實際的音樂幾乎永遠落在 0.35~0.85 這一段，直接拿 bytes/255 當能量，
// 背景會從頭到尾停在高檔、完全不跟著歌走。所以這裡把那一段重新攤開。
const SPECTRUM_FLOOR = 0.30;
const SPECTRUM_CEIL = 0.85;

// 分頻點（Hz）。低頻給鼓與貝斯（背景的脈動看的是這一段），
// 中頻給人聲與主旋律，高頻只拿來當細節（不進能量，避免嘶聲讓背景抖）。
const BASS_HZ = [20, 250];
const MID_HZ = [250, 2000];
const TREBLE_HZ = [2000, 8000];

// --- 靜態 MV 偵測 ---

// 兩張取樣畫面的平均亮度差（0~255）。真正的靜止圖只會有編碼雜訊（0~1），
// 再慢的實拍畫面也會超過 2。這個門檻寧可偏低 —— 誤判成「會動」只是照常放 MV，
// 誤判成「靜止圖」卻會把真的 MV 換掉，後者才是使用者會抱怨的那一種錯。
const STILL_DIFF_THRESHOLD = 2.0;
// 至少要這麼多筆取樣、且橫跨這麼多秒才敢說「這是一張圖」。
// 有些 MV 開頭是黑畫面淡入，只看前兩秒會誤判。
const STILL_MIN_SAMPLES = 4;
const STILL_MIN_SECONDS = 5.0;

// --- 畫質自動降級 ---

// 每幀的繪製成本（毫秒）超過這個值就降一級。舞台機常常是台迷你主機，
// 背景把 CPU 吃掉會讓評分與字幕掉幀 —— 那是本末倒置。
const QUALITY_DOWN_MS = 8.0;
// 低於這個值才升回去。兩個門檻中間留白（遲滯），否則會在邊界上一直跳。
const QUALITY_UP_MS = 4.0;
const QUALITY_LEVELS = ["low", "medium", "high"];

const BG_MODES = ["auto", "always", "off"];

/**
 * 主題表。顏色與動態都是資料，畫圖的那一支照著這裡畫 ——
 * 加一個主題不用動渲染器，也不用動設定頁（設定頁的選項來自後端 SPEC）。
 *
 *   base     底色（RGB）。整個畫面的最暗處。
 *   colors   主色，由暗到亮排。渲染器依 motion 決定怎麼用。
 *   motion   動態種類，對應 ambient-stage.js 裡的一個畫法。
 *   baseLuma 這個主題的亮度錨點（沒有音樂時的亮度）。
 *   swing    音樂能量最多把亮度往上推多少。
 *   density  粒子密度倍率（相對於畫質等級的基準值）。星空要密才像星空，
 *            火星太密就變成火災 —— 這個值是看著 render 出來的圖調的。
 */
const THEMES = {
  aurora: {
    id: "aurora",
    name: "極光",
    motion: "curtain",
    base: [8, 14, 28],
    colors: [[42, 118, 168], [64, 214, 196], [126, 92, 232]],
    baseLuma: 0.72,
    swing: 0.16,
  },
  starfield: {
    id: "starfield",
    name: "星空",
    motion: "stars",
    base: [6, 8, 20],
    colors: [[120, 150, 220], [220, 230, 255], [90, 70, 190]],
    baseLuma: 0.66,
    swing: 0.14,
    density: 1.9,
  },
  neon: {
    id: "neon",
    name: "霓虹",
    motion: "grid",
    base: [14, 6, 26],
    colors: [[255, 0, 127], [0, 229, 255], [140, 60, 220]],
    baseLuma: 0.74,
    swing: 0.18,
  },
  ocean: {
    id: "ocean",
    name: "海洋",
    motion: "waves",
    base: [4, 16, 30],
    colors: [[22, 96, 140], [46, 176, 190], [16, 60, 120]],
    baseLuma: 0.70,
    swing: 0.16,
  },
  ember: {
    id: "ember",
    name: "燭火",
    motion: "embers",
    base: [20, 10, 6],
    colors: [[190, 70, 30], [240, 150, 60], [120, 30, 40]],
    baseLuma: 0.70,
    swing: 0.18,
    density: 0.75,
  },
};

const THEME_IDS = Object.keys(THEMES);
const DEFAULT_THEME = "aurora";

function clamp(value, lo, hi) {
  const n = Number(value);
  if (!Number.isFinite(n)) return lo;
  return Math.max(lo, Math.min(hi, n));
}

/**
 * 一階低通（指數趨近）。與 guide-ducker.js／mic-agc.js 的同名函式同一個式子：
 * 前端沒有模組系統（都是 <script> 直接載入），共用就要多一支檔案與載入順序，
 * 這三行複製比那個代價小。改動請三邊一起改。
 */
function approach(current, target, dt, tau) {
  if (!(tau > 0)) return target;
  const alpha = 1 - Math.exp(-dt / tau);
  return current + (target - current) * alpha;
}

/**
 * FNV-1a 字串雜湊。用途只有一個：讓「自動」主題對同一首歌永遠挑到同一個背景。
 *
 * 隨機挑看起來比較豐富，但在包廂裡是反效果 ——
 * 同一首歌昨天是星空今天是霓虹，第一個反應是「機器怎麼怪怪的」。
 * 背景是歌的一部分，它要是穩定的。
 */
function hashString(text) {
  let hash = 0x811c9dc5;
  const s = String(text == null ? "" : text);
  for (let i = 0; i < s.length; i++) {
    hash ^= s.charCodeAt(i);
    // 乘上 FNV prime（16777619），用位移避免大整數失準
    hash = (hash + ((hash << 1) + (hash << 4) + (hash << 7) + (hash << 8) + (hash << 24))) >>> 0;
  }
  return hash >>> 0;
}

/** 這首歌該用哪個主題。setting 給具體主題就照給的，"auto" 依 songId 穩定挑一個。 */
function pickTheme(songId, setting) {
  if (setting && Object.prototype.hasOwnProperty.call(THEMES, setting)) return setting;
  if (!songId) return DEFAULT_THEME;
  return THEME_IDS[hashString(songId) % THEME_IDS.length];
}

function themeOf(id) {
  return THEMES[id] || THEMES[DEFAULT_THEME];
}

/** 某個頻率落在第幾個 FFT bin（夾在合法範圍內）。 */
function binIndex(hz, sampleRate, binCount) {
  const rate = Number(sampleRate) > 0 ? Number(sampleRate) : 48000;
  const bins = Math.max(1, Math.floor(binCount) || 1);
  const nyquist = rate / 2;
  const idx = Math.round((Number(hz) || 0) / nyquist * bins);
  return clamp(idx, 0, bins);
}

function bandLevel(bytes, lo, hi) {
  const from = Math.min(lo, bytes.length);
  const to = Math.min(Math.max(hi, from + 1), bytes.length);
  if (to <= from) return 0;
  let sum = 0;
  for (let i = from; i < to; i++) sum += bytes[i];
  const raw = sum / (to - from) / 255;
  // 把 dB 對映實際會用到的那一段攤成 0~1（見 SPECTRUM_FLOOR 的說明）
  return clamp((raw - SPECTRUM_FLOOR) / (SPECTRUM_CEIL - SPECTRUM_FLOOR), 0, 1);
}

/**
 * 把 AnalyserNode 的頻域位元組拆成三個頻段。
 * @param {Uint8Array|number[]} bytes getByteFrequencyData 的結果
 * @param {number} sampleRate AudioContext 的取樣率
 */
function analyseSpectrum(bytes, sampleRate) {
  const data = bytes || [];
  const n = data.length;
  if (!n) return { bass: 0, mid: 0, treble: 0, energy: 0 };
  const bass = bandLevel(data, binIndex(BASS_HZ[0], sampleRate, n), binIndex(BASS_HZ[1], sampleRate, n));
  const mid = bandLevel(data, binIndex(MID_HZ[0], sampleRate, n), binIndex(MID_HZ[1], sampleRate, n));
  const treble = bandLevel(data, binIndex(TREBLE_HZ[0], sampleRate, n), binIndex(TREBLE_HZ[1], sampleRate, n));
  // 能量以低頻為主：背景的脈動要跟著鼓走，跟著嘶聲走會抖得很廉價。
  const energy = clamp(bass * 0.62 + mid * 0.38, 0, 1);
  return { bass, mid, treble, energy };
}

/**
 * 鼓點偵測（很陽春，夠用就好）
 *
 * 低頻能量明顯高過自己的移動平均就算一下。不做節拍追蹤 ——
 * 背景只需要「這一下有重音」，不需要知道 BPM，而猜錯 BPM 的視覺比不對拍更糟。
 */
class BeatDetector {
  constructor(options = {}) {
    this.tau = options.tau === undefined ? 0.9 : options.tau;
    this.ratio = options.ratio === undefined ? 1.25 : options.ratio;
    this.floor = options.floor === undefined ? 0.08 : options.floor;
    // 不反應期：兩下之間至少隔這麼久。220ms ≈ 270 BPM，比任何歌都快，
    // 但足以擋掉同一下鼓的前後緣被算成兩次。
    this.refractory = options.refractory === undefined ? 0.22 : options.refractory;
    // 暖機：移動平均還沒追上實際音量之前不判鼓點。
    // 沒有這一段的話，前奏一進來的頭一秒會連噴好幾下 —— 平均值從 0 往上爬，
    // 這段期間「比平均大聲」是必然成立的，跟有沒有重音無關。
    this.warmup = options.warmup === undefined ? 0.35 : options.warmup;
    this.reset();
  }

  reset() {
    this.average = 0;
    this.sinceBeat = this.refractory;
    this.elapsed = 0;
    this.samples = 0;
    return this;
  }

  /** 餵一幀的低頻能量，回傳這一幀是不是鼓點。 */
  push(bass, dt) {
    const step = clamp(dt, 0, 0.25);
    const level = clamp(bass, 0, 1);
    this.sinceBeat += step;
    this.elapsed += step;
    // 第一幀直接當成平均值：從 0 開始爬會製造上面說的那串假鼓點
    if (this.samples === 0) this.average = level;
    this.samples += 1;
    const hit = this.elapsed >= this.warmup &&
      level > this.floor &&
      level > this.average * this.ratio &&
      this.sinceBeat >= this.refractory;
    // 平均值一律更新（包含鼓點那一幀）：不更新的話安靜段落的平均會卡在舊值
    this.average = approach(this.average, level, step, this.tau);
    if (hit) this.sinceBeat = 0;
    return hit;
  }
}

/**
 * 靜態 MV 偵測
 *
 * 為什麼需要：YouTube 上大量的「音樂版／歌詞版」上傳其實是**一張圖配四分鐘音訊**。
 * 這種歌 `video_path` 是有的（檔案真的下載到了），所以「有沒有影片」這個問題
 * 會回答「有」，舞台就放一張凍結的畫面放整首 —— 那正是情境背景要解決的情況。
 *
 * 判定方式是外面每隔一秒多把畫面縮到 32x18 取一次樣，把「跟上一張的平均亮度差」
 * 丟進來。這裡只做決策，不碰 canvas。
 *
 * 兩個方向都是**閂鎖**的（一旦定案就不再改）：唱到一半背景在 MV 與情境之間來回切，
 * 比兩種都不完美的選擇更糟。
 */
class StillnessProbe {
  constructor(options = {}) {
    this.threshold = options.threshold === undefined ? STILL_DIFF_THRESHOLD : options.threshold;
    this.minSamples = options.minSamples === undefined ? STILL_MIN_SAMPLES : options.minSamples;
    this.minSeconds = options.minSeconds === undefined ? STILL_MIN_SECONDS : options.minSeconds;
    this.reset();
  }

  reset() {
    this.state = "unknown";   // unknown | still | moving
    this.samples = 0;
    this.firstAt = null;
    this.lastAt = null;
    this.maxDiff = 0;
    return this;
  }

  /** 已經定案了嗎（定案後外面就可以停止取樣，省掉每秒一次的 drawImage）。 */
  get settled() {
    return this.state !== "unknown";
  }

  /**
   * 餵一筆取樣。
   * @param {number} diff 與上一張取樣畫面的平均亮度差（0~255）
   * @param {number} atSeconds 取樣當下的**牆上時鐘**秒數（單調遞增）。
   *   刻意不用影片的 currentTime：短片配長歌時 <video loop> 會一直繞回 0，
   *   用它算「觀察了幾秒」會得到負數，判定就永遠不會成立。
   *   倒退的輸入一律當成 0 秒（夾住），不讓它把觀察窗算短。
   */
  push(diff, atSeconds) {
    if (this.settled) return this.state;
    const d = Number(diff);
    if (!Number.isFinite(d) || d < 0) return this.state;
    const t = Number.isFinite(Number(atSeconds)) ? Number(atSeconds) : 0;

    this.samples += 1;
    this.maxDiff = Math.max(this.maxDiff, d);
    if (this.firstAt === null) this.firstAt = t;
    this.lastAt = t;

    // 看到一次真的在動就結案。動得起來的畫面不會是圖，不必再等。
    if (d >= this.threshold) {
      this.state = "moving";
      return this.state;
    }
    const watched = Math.max(0, this.lastAt - this.firstAt);
    if (this.samples >= this.minSamples && watched >= this.minSeconds) {
      this.state = "still";
    }
    return this.state;
  }

  /** 取樣本身失敗（跨來源污染 canvas、解碼器不給畫面）就當作會動，照常放 MV。 */
  giveUp() {
    if (!this.settled) this.state = "moving";
    return this.state;
  }
}

/**
 * 畫質自動降級
 *
 * 背景的預算是「不影響評分與字幕」。實測繪製成本超過預算就少畫一點，
 * 而不是讓整台機器掉幀。升回去的門檻比降下來的低很多（遲滯），
 * 否則會在邊界上一路跳，那比一直待在低畫質還難看。
 */
class QualityGovernor {
  constructor(options = {}) {
    this.downMs = options.downMs === undefined ? QUALITY_DOWN_MS : options.downMs;
    this.upMs = options.upMs === undefined ? QUALITY_UP_MS : options.upMs;
    // 升級要連續這麼多次都很輕鬆才給（降級只要一次），
    // 因為「偶爾一幀很快」在垃圾回收的間隙很常見。
    this.upStreakNeeded = options.upStreakNeeded === undefined ? 90 : options.upStreakNeeded;
    this.reset();
  }

  reset() {
    this.level = "high";
    this.avgMs = 0;
    this.upStreak = 0;
    this.samples = 0;
    return this;
  }

  /** 餵這一幀的繪製毫秒數，回傳現在該用的畫質等級。 */
  push(frameMs) {
    const ms = clamp(frameMs, 0, 200);
    this.samples += 1;
    // 前幾幀含 canvas 配置與字型載入，不具代表性，只拿來初始化平均
    this.avgMs = this.samples <= 3 ? ms : approach(this.avgMs, ms, 1, 20);

    const idx = QUALITY_LEVELS.indexOf(this.level);
    if (this.avgMs > this.downMs && idx > 0) {
      this.level = QUALITY_LEVELS[idx - 1];
      this.upStreak = 0;
      return this.level;
    }
    if (this.avgMs < this.upMs) {
      this.upStreak += 1;
      if (this.upStreak >= this.upStreakNeeded && idx < QUALITY_LEVELS.length - 1) {
        this.level = QUALITY_LEVELS[idx + 1];
        this.upStreak = 0;
      }
    } else {
      this.upStreak = 0;
    }
    return this.level;
  }
}

/**
 * 情境背景的總指揮
 *
 * 回答三個問題：畫面現在該給誰（MV／情境／全黑）、這一幀該有多亮、要不要彈一下。
 */
class AmbientDirector {
  /**
   * @param {object} options
   *   mode        "auto"（沒有可用的 MV 才出場）／"always"（一律用情境背景）／"off"（關閉）
   *   theme       主題 id 或 "auto"（依歌曲穩定挑一個）
   *   brightness  亮度上限（0~1）。字幕看不清楚就往下調。
   */
  constructor(options = {}) {
    this.mode = BG_MODES.includes(options.mode) ? options.mode : "auto";
    this.themeSetting = options.theme || "auto";
    this.brightness = clamp(options.brightness === undefined ? 0.85 : options.brightness, 0.3, 1);
    this.probe = new StillnessProbe();
    this.beats = new BeatDetector();
    this.quality = new QualityGovernor();
    this.songId = null;
    this.hasVideo = false;
    this.themeId = pickTheme(null, this.themeSetting);
    this.resetMotion();
  }

  configure(options = {}) {
    if (options.mode !== undefined && BG_MODES.includes(options.mode)) this.mode = options.mode;
    if (options.theme !== undefined && options.theme) {
      this.themeSetting = options.theme;
      this.themeId = pickTheme(this.songId, this.themeSetting);
    }
    if (options.brightness !== undefined) {
      this.brightness = clamp(options.brightness, 0.3, 1);
    }
    return this;
  }

  resetMotion() {
    this.energy = 0;
    this.luma = 0;
    this.phase = 0;
    this.beatPulse = 0;
    this.beats.reset();
    return this;
  }

  /**
   * 換歌。
   * @param {object} song { songId, hasVideo } —— hasVideo 未知（undefined）時先當成有，
   *   等 <video> 真的載不起來再用 videoFailed() 改口。「先當成沒有」會讓
   *   每一首有 MV 的歌都先閃一下情境背景，那比慢一秒換過去明顯得多。
   */
  startSong(song = {}) {
    this.songId = song.songId || null;
    this.hasVideo = song.hasVideo === undefined ? true : !!song.hasVideo;
    this.themeId = pickTheme(this.songId, this.themeSetting);
    this.probe.reset();
    this.resetMotion();
    return this;
  }

  /** 回到待機（沒有歌）。待機畫面一樣走情境背景，只是能量固定在低檔。 */
  clearSong() {
    this.songId = null;
    this.hasVideo = false;
    this.themeId = pickTheme(null, this.themeSetting);
    this.probe.reset();
    this.resetMotion();
    return this;
  }

  /** <video> 載不起來（沒下到 MV、檔案壞了）。 */
  videoFailed() {
    this.hasVideo = false;
    return this;
  }

  /** 要不要繼續對影格取樣（判定完就不必再花這個成本）。 */
  get needsProbe() {
    return this.mode === "auto" && this.hasVideo && !this.probe.settled;
  }

  probeFrame(diff, atSeconds) {
    return this.probe.push(diff, atSeconds);
  }

  /**
   * 現在該顯示哪一層：
   *   "video"   真正的 MV
   *   "ambient" 情境背景
   *   "black"   什麼都不放（使用者把情境背景關掉，而且這首沒有 MV）
   */
  get layer() {
    if (this.mode === "always") return "ambient";
    if (this.mode === "off") return this.hasVideo ? "video" : "black";
    if (!this.hasVideo) return "ambient";
    // 判定中（unknown）先放 MV：還沒有證據說它是張圖之前，把真的 MV 換掉才是錯的。
    return this.probe.state === "still" ? "ambient" : "video";
  }

  get theme() {
    return themeOf(this.themeId);
  }

  /**
   * 餵一幀。
   * @param {number} dt 距離上一幀幾秒
   * @param {object} input { energy, bass, playing } —— 沒有 playing 就當待機
   * @returns {object} 渲染器要的狀態
   */
  update(dt, input = {}) {
    const step = clamp(dt, 0, 0.25);
    const theme = this.theme;
    const playing = !!input.playing;
    const targetEnergy = playing ? clamp(input.energy, 0, 1) : IDLE_ENERGY;

    const tau = targetEnergy > this.energy ? ENERGY_ATTACK_TAU : ENERGY_RELEASE_TAU;
    this.energy = clamp(approach(this.energy, targetEnergy, step, tau), 0, 1);

    // 鼓點只推動態，不碰亮度（見檔頭「不准閃」）
    const beat = playing ? this.beats.push(clamp(input.bass, 0, 1), step) : false;
    if (beat) this.beatPulse = 1;
    this.beatPulse = clamp(approach(this.beatPulse, 0, step, BEAT_PULSE_TAU), 0, 1);

    // 亮度：主題錨點 + 音樂擺幅，乘上使用者的亮度上限，再走限速器
    const target = clamp((theme.baseLuma + this.energy * theme.swing) * this.brightness, 0, 1);
    const maxDelta = MAX_LUMA_SLEW * step;
    this.luma += clamp(target - this.luma, -maxDelta, maxDelta);
    this.luma = clamp(this.luma, 0, 1);

    // 相位：整體流動速度。能量高時流得快一點，但底速不為 0（安靜段落不能凍住）。
    this.phase = (this.phase + step * (0.05 + this.energy * 0.12)) % 1;

    return {
      theme,
      luma: this.luma,
      energy: this.energy,
      beat,
      beatPulse: this.beatPulse,
      phase: this.phase,
      quality: this.quality.level,
    };
  }

  /** 把這一幀的繪製成本回報給畫質調節器。 */
  reportFrameCost(ms) {
    return this.quality.push(ms);
  }
}

if (typeof window !== "undefined") {
  window.AmbientDirector = AmbientDirector;
  window.AmbientThemes = THEMES;
  window.analyseAmbientSpectrum = analyseSpectrum;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    AmbientDirector,
    StillnessProbe,
    BeatDetector,
    QualityGovernor,
    THEMES,
    THEME_IDS,
    DEFAULT_THEME,
    BG_MODES,
    analyseSpectrum,
    binIndex,
    pickTheme,
    hashString,
    approach,
    MAX_LUMA_SLEW,
    IDLE_ENERGY,
    SPECTRUM_FLOOR,
    SPECTRUM_CEIL,
    STILL_DIFF_THRESHOLD,
    STILL_MIN_SAMPLES,
    STILL_MIN_SECONDS,
    QUALITY_LEVELS,
  };
}
