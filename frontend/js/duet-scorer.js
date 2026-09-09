/**
 * 對唱模式 (Duet Mode) —— 兩支麥克風分別評分
 *
 * 商用點歌機的「對唱」不是把兩支麥克風混在一起打一個分數，而是各自算 ——
 * DAM 的デュエット採点、金嗓的男女對唱評分都會在唱完之後亮出「A 幾分、B 幾分」。
 * 包廂裡那句「這首我們對唱，看誰分數高」需要的就是這個。
 *
 * 難的地方不是加一個計分器，而是**串音 (crosstalk)**：
 * 兩支麥克風在同一個房間裡，A 唱歌的時候 B 的麥克風也收得到 A 的聲音。
 * 如果不處理，B 只要把麥克風舉著站在旁邊，分數就會跟 A 差不多 ——
 * 那這個功能就完全沒有意義了（而且是「看起來會動、實際上是假的」那種壞法）。
 *
 * 所以這個模組唯一的工作是回答每一幀的那個問題：**這一幀該算誰的？**
 *
 *   1. 電平主導 (level dominance)：近距離收音的串音通常比本人小 12~20 dB。
 *      兩支麥克風的電平差超過門檻，就只算大聲的那一位；差不多就兩邊都算
 *      （副歌一起唱本來就該兩個人都拿分）。
 *   2. Schmitt 遲滯：進入獨佔要差 marginDb，回到「兩人都算」只要差一半 ——
 *      沒有遲滯的話，門檻附近每一幀都在換人，分數會兩邊亂跳。
 *   3. 音高分歧 (pitch divergence) 例外：串音是同一個聲音的複製品，所以兩邊
 *      偵測到的音高會一樣。反過來說，**音高明顯不同就代表真的有兩個人在唱** ——
 *      這時即使音量小了 10 dB（唱得比較收、或麥克風靈敏度不同）也要給分。
 *      只有在訊號夠紮實（明顯高於底噪）時才敢用這一條，否則底噪的假音高會放串音進來。
 *
 * 計分本身沿用兩個 PitchEngine（同一套音準判定、同一套等級門檻），
 * 這裡只決定「哪一幀算誰的」以及唱完的對戰結果。
 *
 * 純資料邏輯：不碰 DOM、不碰 Web Audio、不發網路請求，所以能用 node --test 直接跑。
 * 瀏覽器端由 player.html 以 <script> 載入。
 */

// 噪音閘門：低於這個電平當成「這支麥克風沒人唱」。與麥克風自動增益同一個值
// （-45 dBFS ≈ 安靜房間的底噪加一點空調聲），兩邊的「有沒有人」才會是同一件事。
const SILENCE_DB = -45;

// 「訊號夠紮實」的門檻：比底噪高 12 dB 以上才敢相信它偵測到的音高。
// 音高分歧例外只在這條線之上生效 —— 底噪的自相關結果基本上是隨機數，
// 拿它去跟主唱比對「音高不一樣」，等於把串音全部放進來計分。
const SOLID_SIGNAL_DB = SILENCE_DB + 12;

// 電平主導的預設門檻（dB）。9 dB 是「串音進不來、但唱得比較收的人還進得來」的折衷：
// 近距離收音的串音多半在 -12 dB 以下，而兩個人音量差 9 dB 已經是很明顯的大小聲。
// 房間很小、喇叭很大聲時可以在設定頁調高（串音更嚴重），麥克風型號不同時也一樣。
const DEFAULT_MARGIN_DB = 9;

// 遲滯的下門檻＝上門檻的一半（至少 2 dB）。這兩個門檻中間維持現狀。
function releaseMargin(marginDb) {
  return Math.max(2, marginDb / 2);
}

// 音高差多少算「這是兩個不同的人在唱」。1.5 個半音：
// 同一個聲音經由空氣傳到另一支麥克風，音高偵測的差異遠小於這個值；
// 而真的唱不同聲部（三度、五度）至少差 3 個半音。1.5 留給偵測誤差。
const PITCH_DIVERGENCE_SEMITONES = 1.5;

// 電平包絡線：升得快（跟上句子的起音）、掉得慢（字與字之間的空隙不算變小聲）。
// 直接用瞬時 RMS 判主導會在幀與幀之間亂跳 —— 兩支麥克風收到的波形相位不同，
// 同一個聲音的瞬時振幅本來就會差。
const ENV_RISE_TAU = 0.05;
const ENV_FALL_TAU = 0.25;

// 一位演唱者至少要被算到這麼多秒才算「他有唱」。
// 對唱模式開著但只有一個人拿麥克風是很常見的（另一支放在桌上），
// 這種時候不該亮出「A 勝 B」的對戰結果 —— 那不是比賽，是誤會。
const MIN_CREDITED_SECONDS = 10;

// 分數差在這個比例之內算平手。逐幀評分本來就有雜訊，
// 差 1% 就宣布勝負只會讓人覺得這個分數是隨機的。
const TIE_RATIO = 0.03;

function clamp(value, lo, hi) {
  const n = Number(value);
  if (!Number.isFinite(n)) return lo;
  return Math.max(lo, Math.min(hi, n));
}

/** 一階低通（指數趨近）。用 dt 與時間常數算係數，30fps 與 120fps 行為一致。 */
function approach(current, target, dt, tau) {
  if (!(tau > 0)) return target;
  const alpha = 1 - Math.exp(-dt / tau);
  return current + (target - current) * alpha;
}

/**
 * 線性 RMS 轉 dBFS。0（或負數、NaN）回 -Infinity —— 那是真正的靜音，
 * 不要退回某個有限的小數值，否則噪音閘門會把「完全沒訊號」誤判成「很小的人聲」。
 */
function dbFromRms(rms) {
  const n = Number(rms);
  if (!Number.isFinite(n) || n <= 0) return -Infinity;
  return 20 * Math.log10(n);
}

function micDb(input) {
  if (!input) return -Infinity;
  if (input.db !== undefined) {
    const n = Number(input.db);
    return Number.isFinite(n) ? n : -Infinity;
  }
  return dbFromRms(input.rms);
}

function micMidi(input) {
  const n = Number(input && input.midi);
  return Number.isFinite(n) && n > 0 ? n : 0;
}

class DuetScorer {
  /**
   * @param {object} options
   *   enabled  是否啟用（關掉時兩邊都照常計分，等於兩個獨立的單人評分）
   *   marginDb 電平主導門檻（dB）
   */
  constructor(options = {}) {
    this.enabled = !!options.enabled;
    this.marginDb = clamp(options.marginDb === undefined ? DEFAULT_MARGIN_DB : options.marginDb,
                          3, 24);
    this.reset();
  }

  /**
   * 設定頁／點歌台改了就套用。
   *
   * 改門檻不重置統計 —— 唱到一半調參數不該把前面唱的都作廢。
   * 但開關切換兩個方向都要重來：關掉再打開時，包絡線與主導狀態都是上一段的殘留。
   */
  configure(options = {}) {
    if (options.enabled !== undefined) {
      const next = !!options.enabled;
      if (next !== this.enabled) this.reset();
      this.enabled = next;
    }
    if (options.marginDb !== undefined) {
      this.marginDb = clamp(options.marginDb, 3, 24);
    }
    return this;
  }

  /** 換歌、重唱都要歸零：包絡線、主導狀態與時間統計都不能跨曲累計。 */
  reset() {
    this.envA = -Infinity;
    this.envB = -Infinity;
    // 'both' = 兩邊都算，'a' / 'b' = 只算這一位（另一位判定為串音）
    this.dominant = "both";
    this.stats = {
      a: { credited: 0, denied: 0, lead: 0 },
      b: { credited: 0, denied: 0, lead: 0 },
    };
    this.lastDecision = { a: true, b: true, dominant: "both", reason: "idle" };
    return this;
  }

  /**
   * 這一幀該算誰的。
   *
   * @param {number} dt 距離上一幀幾秒（外面請夾在合理範圍，分頁切回來時會是好幾秒）
   * @param {object} a  A 麥克風 `{ rms, midi }`（midi 可給上一幀偵測到的音高，0 = 沒有）
   * @param {object} b  B 麥克風，同上
   * @returns {{a: boolean, b: boolean, dominant: string, reason: string}}
   *   `a` / `b` = 這一幀要不要記進該位演唱者的成績。
   */
  decide(dt, a, b) {
    if (!this.enabled) {
      // 沒開對唱就不做任何判定：兩支麥克風各自照常計分（等於兩個獨立的單人評分）
      this.lastDecision = { a: true, b: true, dominant: "both", reason: "disabled" };
      return this.lastDecision;
    }

    const step = clamp(dt, 0, 0.25);
    const dbA = micDb(a);
    const dbB = micDb(b);

    // 包絡線。第一幀（-Infinity）直接跳到量到的值，否則指數趨近會永遠停在 -Infinity。
    this.envA = Number.isFinite(this.envA) && step > 0
      ? approach(this.envA, dbA, step, dbA > this.envA ? ENV_RISE_TAU : ENV_FALL_TAU)
      : dbA;
    this.envB = Number.isFinite(this.envB) && step > 0
      ? approach(this.envB, dbB, step, dbB > this.envB ? ENV_RISE_TAU : ENV_FALL_TAU)
      : dbB;

    const voicedA = this.envA >= SILENCE_DB;
    const voicedB = this.envB >= SILENCE_DB;

    // 主導狀態機（Schmitt 遲滯）。兩邊都沒聲音時回到中性，
    // 免得安靜段落把上一句的主導狀態一路帶到下一句。
    const diff = this.envA - this.envB;
    if (!voicedA && !voicedB) {
      this.dominant = "both";
    } else if (!Number.isFinite(diff)) {
      // 一邊完全靜音（-Infinity）：有聲音的那邊就是主導，這是最乾淨的情況
      this.dominant = voicedA ? "a" : "b";
    } else if (diff >= this.marginDb) {
      this.dominant = "a";
    } else if (diff <= -this.marginDb) {
      this.dominant = "b";
    } else if (Math.abs(diff) <= releaseMargin(this.marginDb)) {
      this.dominant = "both";
    }
    // 兩個門檻之間：維持現狀（這就是遲滯，也是「分數不會兩邊亂跳」的唯一解法）

    let creditA = voicedA;
    let creditB = voicedB;
    let reason = this.dominant === "both" ? "both" : "dominance";

    if (this.dominant === "a") creditB = false;
    if (this.dominant === "b") creditA = false;

    // 音高分歧例外：串音是同一個聲音的複製品，音高會一樣；音高明顯不同
    // 就代表真的有兩個人在唱，那就算被判成串音的那一邊也要給分。
    // 只在被壓的那一邊訊號夠紮實時才敢用 —— 底噪的假音高會把串音全部放進來。
    if (this.dominant !== "both") {
      const midiA = micMidi(a);
      const midiB = micMidi(b);
      const quiet = this.dominant === "a" ? "b" : "a";
      const quietDb = quiet === "a" ? this.envA : this.envB;
      const diverged = midiA > 0 && midiB > 0
        && Math.abs(midiA - midiB) >= PITCH_DIVERGENCE_SEMITONES;
      if (diverged && quietDb >= SOLID_SIGNAL_DB) {
        if (quiet === "a") creditA = voicedA;
        else creditB = voicedB;
        reason = "pitch-divergence";
      }
    }

    // 時間統計：誰被算到、誰有唱卻被判成串音（這兩個數字是現場排查的唯一線索）
    if (step > 0) {
      if (creditA) this.stats.a.credited += step;
      else if (voicedA) this.stats.a.denied += step;
      if (creditB) this.stats.b.credited += step;
      else if (voicedB) this.stats.b.denied += step;
      if (this.dominant === "a") this.stats.a.lead += step;
      if (this.dominant === "b") this.stats.b.lead += step;
    }

    this.lastDecision = { a: creditA, b: creditB, dominant: this.dominant, reason };
    return this.lastDecision;
  }

  /** 這一幀被判為串音而沒算分的是哪一位（舞台徽章用；沒有就回 null）。 */
  mutedSinger() {
    if (!this.enabled) return null;
    const d = this.lastDecision;
    if (d.dominant === "a" && !d.b) return "b";
    if (d.dominant === "b" && !d.a) return "a";
    return null;
  }

  /**
   * 兩位演唱者的麥克風時間統計。
   *
   * `sang` 是「這一位真的有唱」—— 對唱模式開著但只有一個人拿麥克風時，
   * 舞台端要靠這個決定亮單人成績單還是對戰成績單。
   * `crosstalk_ratio` 是「有唱卻被判成串音的比例」：這個數字高（> 0.3）就代表
   * 兩支麥克風靠太近或門檻設太高，是現場唯一能拿來調的線索。
   */
  micSummary() {
    const one = (s) => {
      const voiced = s.credited + s.denied;
      return {
        credited_seconds: Math.round(s.credited * 10) / 10,
        denied_seconds: Math.round(s.denied * 10) / 10,
        lead_seconds: Math.round(s.lead * 10) / 10,
        crosstalk_ratio: voiced > 0 ? Math.round((s.denied / voiced) * 1000) / 1000 : 0,
        sang: s.credited >= MIN_CREDITED_SECONDS,
      };
    };
    return { a: one(this.stats.a), b: one(this.stats.b) };
  }

  /**
   * 對戰結果。吃的是兩個 PitchEngine 各自的 `getFinalResult()`，
   * 所以兩邊的音準判定、等級門檻與總分算法完全一樣（要比就得同一把尺）。
   *
   * @returns {object}
   *   `contested` —— 兩位都真的唱了，值得亮對戰結果；false 時舞台端退回單人成績單。
   *   `winner` —— 'a' / 'b' / 'tie'（`contested` 為 false 時是唱的那一位或 null）。
   */
  verdict(resultA, resultB) {
    const mics = this.micSummary();
    const scoreA = Math.max(0, Math.round(Number(resultA && resultA.score) || 0));
    const scoreB = Math.max(0, Math.round(Number(resultB && resultB.score) || 0));
    const bothSang = mics.a.sang && mics.b.sang;

    if (!bothSang) {
      const only = mics.a.sang ? "a" : (mics.b.sang ? "b" : null);
      return {
        contested: false,
        winner: only,
        margin: 0,
        margin_ratio: 0,
        score_a: scoreA,
        score_b: scoreB,
        mics,
      };
    }

    const margin = Math.abs(scoreA - scoreB);
    const top = Math.max(scoreA, scoreB);
    const ratio = top > 0 ? margin / top : 0;
    const winner = ratio <= TIE_RATIO ? "tie" : (scoreA > scoreB ? "a" : "b");

    return {
      contested: true,
      winner,
      margin,
      margin_ratio: Math.round(ratio * 1000) / 1000,
      score_a: scoreA,
      score_b: scoreB,
      accuracy_a: Number(resultA && resultA.accuracy) || 0,
      accuracy_b: Number(resultB && resultB.accuracy) || 0,
      grade_a: (resultA && resultA.grade) || "",
      grade_b: (resultB && resultB.grade) || "",
      mics,
    };
  }
}

if (typeof window !== "undefined") {
  window.DuetScorer = DuetScorer;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    DuetScorer,
    dbFromRms,
    releaseMargin,
    SILENCE_DB,
    SOLID_SIGNAL_DB,
    DEFAULT_MARGIN_DB,
    PITCH_DIVERGENCE_SEMITONES,
    MIN_CREDITED_SECONDS,
    TIE_RATIO,
  };
}
