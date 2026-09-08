/**
 * 和聲規劃 (Harmony Planner)
 *
 * 商用 KTV 都有「和聲」這顆按鈕（金嗓的 HARMONY、音圓的雙聲部、DAM 的ハモリ）：
 * 按下去之後你唱一個人，喇叭出來像兩個人 —— 多出來的那一個聲部跟著你的旋律
 * 疊在三度（或五度、低八度）上。
 *
 * 為什麼這件事不能用「把麥克風升 4 個半音再疊回去」草率解決：
 * 固定升 4 個半音在大調裡有一半的音會變成調外音。以 C 大調為例，
 * Do 的上三度是 Mi（+4 半音）沒問題，但 Re 的上三度是 Fa（+3 半音），
 * 硬套 +4 會唱出 Fa♯ —— 那是 G 大調的音，聽起來就是走音，而且是刺耳的那種走音。
 * 所以真正的和聲必須知道「現在是什麼調、這個音是音階的第幾度」，
 * 才能決定這一個音要疊 +3 還是 +4。
 *
 * 這個模組負責的就是那個判斷，而且刻意寫成純資料邏輯（不碰 DOM、不碰 Web Audio）：
 *   1. 從這首歌的導唱音符（pitch.notes）估出調性（Krumhansl-Schmuckler 相關法）。
 *   2. 針對現在這一個導唱音符，算出各聲部該移多少半音（音階上的度數，不是固定半音）。
 *   3. 決定這一幀和聲該不該出聲（沒人唱的時候出聲＝把呼吸聲與房間噪音升高八度播出來）。
 * 實際的移調由 harmony-worklet.js 做，音量與淡入淡出由 audio-effects.js 做。
 *
 * 調性估不出來的時候（音符太少、念白／饒舌曲、還在處理中沒有 pitch 資料）
 * 一律退成低八度疊唱 —— 八度在任何調、任何音都不會錯，
 * 寧可少一點花樣，也不要有一半機率唱出調外音。
 */

// Krumhansl-Kessler 的調性感知輪廓（大調／小調各 12 個音級的權重）。
// 這是心理聲學實驗量出來的「聽起來像這個調的程度」，不是隨手設的常數。
const MAJOR_PROFILE = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88];
const MINOR_PROFILE = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17];

const MAJOR_SCALE = [0, 2, 4, 5, 7, 9, 11];
const MINOR_SCALE = [0, 2, 3, 5, 7, 8, 10];

const PITCH_NAMES = ["C", "C♯", "D", "E♭", "E", "F", "F♯", "G", "A♭", "A", "B♭", "B"];

// 調性判定的兩道門檻。任一條沒過就退成低八度疊唱：
//   * 有效音符時間太短（不到 8 秒）：樣本不夠，相關係數只是雜訊。
//   * 相關係數太低（< 0.45）：這首歌的音級分布不像任何一個大小調
//     （純念白、電子音效被誤抓成音高、只有兩三個音的副歌 hook）。
const MIN_KEY_SECONDS = 8.0;
const MIN_KEY_CORRELATION = 0.45;

// 同一個導唱音符不重算移調量；換音符後也至少要隔這麼久才准再換一次。
// 沒有這道門檻的話，音符邊界上前後兩個音交替被判為 active 時，
// 移調量會在 +3 / +4 之間每幀跳一次 —— 聽起來像和聲在抖。
const MIN_HOLD_SECONDS = 0.08;

// 各聲部的音量修正。低八度的能量集中在低頻，同樣的增益聽起來重得多，
// 而且會蓋掉主唱的清晰度，所以收一點。
const VOICE_TRIM = { third: 1.0, fifth: 0.95, low_third: 1.0, octave: 0.72 };

/**
 * 和聲風格。每一種都是「音階上的度數」而不是固定半音數
 * （octave 是唯一的例外 —— 八度在任何調都是 12 個半音）。
 *
 *   third     上三度：最標準的 KTV 和聲，加了像多一個人幫你唱高音
 *   low_third 下三度：主唱在高音區時上三度會太尖，往下疊比較穩
 *   fifth     上五度：空心、開闊，適合搖滾與台語歌的長音
 *   octave    低八度疊唱：男聲厚度，任何調都不會錯，也是判不出調性時的退路
 *   duet      雙聲部：上三度＋低八度，一次兩個聲部（最接近「對唱」的聽感）
 */
const HARMONY_STYLES = {
  third: { label: "上三度", voices: [{ kind: "third", degrees: 2 }] },
  low_third: { label: "下三度", voices: [{ kind: "low_third", degrees: -2 }] },
  fifth: { label: "上五度", voices: [{ kind: "fifth", degrees: 4 }] },
  octave: { label: "低八度", voices: [{ kind: "octave", semitones: -12 }] },
  duet: {
    label: "雙聲部",
    voices: [{ kind: "third", degrees: 2 }, { kind: "octave", semitones: -12 }],
  },
};

const DEFAULT_STYLE = "third";
const DEFAULT_LEVEL = 0.5;

// 同時最多幾個聲部。改這個數字要連 audio-effects.js 建立的移調節點數一起改。
const MAX_VOICES = 2;

function clamp(value, lo, hi) {
  const n = Number(value);
  if (!Number.isFinite(n)) return lo;
  return Math.max(lo, Math.min(hi, n));
}

function pearson(a, b) {
  const n = a.length;
  let sa = 0, sb = 0;
  for (let i = 0; i < n; i++) { sa += a[i]; sb += b[i]; }
  const ma = sa / n, mb = sb / n;
  let num = 0, da = 0, db = 0;
  for (let i = 0; i < n; i++) {
    const x = a[i] - ma, y = b[i] - mb;
    num += x * y;
    da += x * x;
    db += y * y;
  }
  if (da <= 0 || db <= 0) return 0;
  return num / Math.sqrt(da * db);
}

/**
 * 從導唱音符估出調性。
 *
 * 權重用「時間長度」而不是「出現次數」：主音與屬音通常是長音，
 * 經過音又快又多，照次數算會把裝飾音當成調性骨幹。
 *
 * @param {Array} notes [{ start, end, midi }]
 * @returns {{ tonic, mode, confidence, seconds, scale, name }|null} 判不出來回 null
 */
function estimateKey(notes) {
  const weights = new Array(12).fill(0);
  let total = 0;
  for (const note of Array.isArray(notes) ? notes : []) {
    const midi = Number(note && note.midi);
    const start = Number(note && note.start);
    const end = Number(note && note.end);
    if (!Number.isFinite(midi) || midi <= 0) continue;
    if (!Number.isFinite(start) || !Number.isFinite(end)) continue;
    const dur = end - start;
    if (!(dur > 0)) continue;
    // 單一長音上限 2 秒：尾奏那個拖了 12 秒的長音不該一個人決定整首的調
    const capped = Math.min(dur, 2.0);
    weights[((Math.round(midi) % 12) + 12) % 12] += capped;
    total += capped;
  }
  if (total < MIN_KEY_SECONDS) return null;

  let best = null;
  for (let tonic = 0; tonic < 12; tonic++) {
    const rotated = weights.slice(tonic).concat(weights.slice(0, tonic));
    const rMajor = pearson(rotated, MAJOR_PROFILE);
    const rMinor = pearson(rotated, MINOR_PROFILE);
    if (!best || rMajor > best.confidence) best = { tonic, mode: "major", confidence: rMajor };
    if (rMinor > best.confidence) best = { tonic, mode: "minor", confidence: rMinor };
  }
  if (!best || best.confidence < MIN_KEY_CORRELATION) return null;

  return {
    tonic: best.tonic,
    mode: best.mode,
    confidence: Math.round(best.confidence * 1000) / 1000,
    seconds: Math.round(total * 10) / 10,
    scale: best.mode === "major" ? MAJOR_SCALE : MINOR_SCALE,
    name: `${PITCH_NAMES[best.tonic]} ${best.mode === "major" ? "大調" : "小調"}`,
  };
}

/**
 * 在音階上往上／往下走幾度，回傳要移幾個半音。
 *
 * 例：C 大調的 Re 往上兩度 = Fa，兩者相差 3 個半音（不是 4）。
 * 這一個函式就是「和聲不會唱出調外音」的全部原因。
 *
 * 遇到非音階音（半音經過音、藍調音）就吸附到最近的音階音再算度數：
 * 寧可平行三度，也不要跑出調外 —— 前者只是少一點色彩，後者是走音。
 *
 * @param {number} midi    導唱音符（MIDI 音高）
 * @param {object} key     estimateKey() 的結果
 * @param {number} degrees 正 = 往上幾度，負 = 往下幾度（2 = 三度，4 = 五度）
 * @returns {number} 要移幾個半音（可能是負的）
 */
function diatonicShift(midi, key, degrees) {
  const scale = key.scale;
  const pc = (((Math.round(midi) - key.tonic) % 12) + 12) % 12;

  // 找到這個音在音階上的位置；不在音階上就吸附到最近的音階音
  let index = scale.indexOf(pc);
  if (index < 0) {
    let bestDist = 99;
    for (let i = 0; i < scale.length; i++) {
      const d = Math.abs(scale[i] - pc);
      if (d < bestDist) { bestDist = d; index = i; }
    }
  }

  const target = index + degrees;
  // 走出 0~6 的範圍就是跨了八度，補回八度差
  const octaves = Math.floor(target / scale.length);
  const wrapped = ((target % scale.length) + scale.length) % scale.length;
  return (scale[wrapped] + octaves * 12) - scale[index];
}

class HarmonyPlanner {
  /**
   * @param {object} options
   *   enabled 是否啟用
   *   style   HARMONY_STYLES 的鍵（third / low_third / fifth / octave / duet）
   *   level   和聲音量（0~1），實際送進音訊圖前還會乘上各聲部的 trim
   */
  constructor(options = {}) {
    this.enabled = !!options.enabled;
    this.style = HARMONY_STYLES[options.style] ? options.style : DEFAULT_STYLE;
    this.level = clamp(options.level === undefined ? DEFAULT_LEVEL : options.level, 0, 1);
    this.key = null;
    this.reset();
  }

  configure(options = {}) {
    if (options.enabled !== undefined) this.enabled = !!options.enabled;
    if (options.style !== undefined && HARMONY_STYLES[options.style]) {
      if (options.style !== this.style) {
        this.style = options.style;
        // 換風格要立刻重算移調量，不能被 MIN_HOLD_SECONDS 擋住 ——
        // 使用者按下「低八度」卻還聽到三度，會以為按鈕壞了
        this.lastNoteStart = null;
        this.holdSeconds = MIN_HOLD_SECONDS;
      }
    }
    if (options.level !== undefined) this.level = clamp(options.level, 0, 1);
    return this;
  }

  /** 載入這首歌的導唱音符並估調性。換歌一定要呼叫（調性不能沿用上一首）。 */
  setNotes(notes) {
    this.key = estimateKey(notes);
    this.reset();
    return this.key;
  }

  reset() {
    this.voices = [];
    this.active = false;
    this.lastNoteStart = null;
    this.holdSeconds = MIN_HOLD_SECONDS;
    return this;
  }

  /** 這首歌實際會用的風格。判不出調性時，需要音階的風格一律退成低八度。 */
  effectiveStyle() {
    if (!this.key && this.style !== "octave") return "octave";
    return this.style;
  }

  /** 舞台徽章要顯示的文字（沒啟用時回 null）。 */
  describe() {
    if (!this.enabled) return null;
    const style = HARMONY_STYLES[this.effectiveStyle()];
    const label = style ? style.label : "";
    if (!this.key) return `和聲 ${label}`;
    return `和聲 ${label}（${this.key.name}）`;
  }

  /**
   * 餵一幀，回傳這一幀各聲部該用的移調量與音量。
   *
   * @param {number} dt    距離上一幀幾秒
   * @param {object} frame { sang, hasNote, noteMidi, noteStart }
   *                       —— 與評分心跳共用同一份判定，不重新偵測一次音高
   * @returns {{ active: boolean, voices: Array<{shift, gain, kind}> }}
   */
  update(dt, frame) {
    const step = clamp(dt, 0, 0.25);
    this.holdSeconds += step;

    if (!this.enabled || this.level <= 0) {
      this.active = false;
      this.voices = [];
      return this.snapshot();
    }

    const sang = !!(frame && frame.sang);
    const hasNote = !!(frame && frame.hasNote);
    const noteMidi = Number(frame && frame.noteMidi) || 0;
    const styleKey = this.effectiveStyle();
    const style = HARMONY_STYLES[styleKey];

    // 沒人唱就不出聲。這一條不是節能，是必要的：
    // 和聲是把麥克風訊號移調再疊回去，沒人唱的時候疊出來的是
    // 升高八度的呼吸聲、椅子聲與空調聲，非常明顯而且很吵。
    if (!sang) {
      this.active = false;
      return this.snapshot();
    }

    // 需要音階度數的風格（三度、五度）一定要有導唱音符才知道現在是第幾度。
    // 前奏、間奏、念白段沒有音符 —— 這時候只有八度疊唱能安全地出聲。
    const needsNote = style.voices.some(v => v.degrees !== undefined);
    if (needsNote && !(hasNote && noteMidi > 0)) {
      this.active = false;
      return this.snapshot();
    }

    // 同一個音符不重算；換音符也要等 MIN_HOLD_SECONDS，避免在音符邊界上抖動
    const noteStart = frame && frame.noteStart !== undefined ? frame.noteStart : null;
    const noteChanged = noteStart !== this.lastNoteStart;
    if (!this.voices.length || (noteChanged && this.holdSeconds >= MIN_HOLD_SECONDS)) {
      this.lastNoteStart = noteStart;
      this.holdSeconds = 0;
      this.voices = style.voices.slice(0, MAX_VOICES).map(voice => {
        const shift = voice.semitones !== undefined
          ? voice.semitones
          : diatonicShift(noteMidi, this.key, voice.degrees);
        return {
          kind: voice.kind,
          shift,
          // 和聲永遠不該蓋過主唱：整體上限 0.85，低八度再多收一點
          gain: Math.round(clamp(this.level * (VOICE_TRIM[voice.kind] || 1), 0, 0.85) * 1000) / 1000,
        };
      });
    }

    this.active = this.voices.length > 0;
    return this.snapshot();
  }

  snapshot() {
    return {
      active: this.active,
      // 沒在出聲的時候回空陣列：呼叫端就不必自己再判斷一次 active
      voices: this.active ? this.voices.map(v => ({ ...v })) : [],
    };
  }
}

if (typeof window !== "undefined") {
  window.HarmonyPlanner = HarmonyPlanner;
  window.HARMONY_STYLES = HARMONY_STYLES;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    HarmonyPlanner,
    HARMONY_STYLES,
    estimateKey,
    diatonicShift,
    pearson,
    MAJOR_SCALE,
    MINOR_SCALE,
    MIN_KEY_SECONDS,
    MIN_KEY_CORRELATION,
    MIN_HOLD_SECONDS,
    MAX_VOICES,
    VOICE_TRIM,
    DEFAULT_STYLE,
    DEFAULT_LEVEL,
  };
}
