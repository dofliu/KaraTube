/**
 * 麥克風自動增益 (Mic Auto Gain Control)
 *
 * 包廂裡最常見的抱怨不是音準也不是效果，而是「換人唱就要重調麥克風音量」：
 * 上一位貼著麥克風大聲吼，下一位離 20 公分小聲哼，同一支麥克風的輸入電平
 * 可以差 20 dB 以上。商用機（金嗓的 AGC、音圓的自動音量、DAM 的マイクレベル自動）
 * 都是同一招 —— 機器自己把每個人拉到差不多的音量。
 *
 * 這裡刻意用「前饋 (feed-forward)」而不是「回授 (feedback)」：
 * 量的是**麥克風原始訊號**（analyser 直接接在 micSource 上，在增益節點之前），
 * 算出來的增益套在後面的節點。如果反過來量已經放大過的訊號，
 * 「放大 → 量到更大 → 少放大 → 量到更小」會變成一個會呼吸的迴路，
 * 而且它的時間常數跟房間殘響耦合在一起，現場幾乎無法調。
 *
 * 三條安全規則（順序就是優先度，每一條都對應一個真的會發生的災難）：
 *   1. 沒人唱的時候絕不加增益。安靜的房間也有底噪，照著「拉到目標音量」算下去
 *      會一路加到上限，然後第一個字進來直接爆掉，多人模式還會當場嘯叫。
 *   2. 要降立刻降、要升慢慢升。降是保護（削峰、回授邊緣），升是舒適；
 *      升太快會讓句與句之間的空隙把底噪一起抬起來，聽起來像機器在呼吸。
 *   3. 增益範圍夾住，而且多人模式（人聲外放）的加成上限更低 ——
 *      每多加 1 dB 就離回授近 1 dB，這是物理，軟體只能選擇不要去踩。
 *
 * 全部寫成純資料邏輯（不碰 DOM、不碰 Web Audio），才能用 node --test 守住。
 * 用法：每一幀餵一次 update(dt, { rms })，拿回線性增益倍率，
 * 乘在使用者自己設定的麥克風音量之前（所以滑桿的刻度仍然是使用者說的話）。
 */

// 目標輸出電平（dBFS）。-18 dBFS 是「大聲但離削峰還有一段」的常用工作點：
// 唱歌的動態比說話大，留 18 dB 餘裕才吃得下副歌那一下。
const DEFAULT_TARGET_DB = -18;

// 增益範圍（dB，兩邊都是幅度）。
// 加成上限刻意比削減小：加太多是把底噪與回授一起放大，削減只是變小聲。
const DEFAULT_MAX_BOOST_DB = 9;
const DEFAULT_MAX_CUT_DB = 12;

// 多人模式（人聲從喇叭出來）的加成上限。回授餘裕直接被吃掉，所以更保守。
const PARTY_MAX_BOOST_DB = 6;

// 噪音閘門：低於這個電平當成「沒人唱」，完全不調整增益。
// -45 dBFS 大約是安靜房間的底噪加一點空調聲。
const SILENCE_DB = -45;

// 削峰保護線：預估輸出（輸入 + 目前增益）超過這裡就立刻降，不等平滑、不看容忍區。
const CLIP_GUARD_DB = -6;

// 輸入電平的包絡線：升得快（跟上句子的起音）、掉得慢（字與字之間的空隙不算變小聲）。
const ENV_RISE_TAU = 0.08;
const ENV_FALL_TAU = 0.6;

// 增益本身的移動速度。降 0.25 秒、升 3 秒 —— 不對稱是刻意的（安全規則 2）。
const GAIN_DOWN_TAU = 0.25;
const GAIN_UP_TAU = 3.0;
// 削峰保護的緊急降速：一幀就要看得到效果。
const GAIN_PANIC_TAU = 0.06;
// 長時間沒人唱之後把增益放回中性的速度（慢，聽不出來）。
const GAIN_IDLE_TAU = 4.0;

// 開始調整前至少要聽到這麼多秒的人聲。
// 沒有這道門檻的話，「喂喂測試」那一聲就會把增益整個拉走。
const WARMUP_VOICED_SECONDS = 1.2;

// 容忍區：差不到 1 dB 就不要「開始」調整。
// 人耳聽不出 1 dB，但增益一直在微調會讓底噪的音色一直變，那是聽得出來的。
//
// 注意這是遲滯而不是死區：一旦開始調整就一路調到位（差 SETTLED_DB 以內才收手）。
// 寫成死區的話每次都會停在離目標 1 dB 的地方，等於系統性地永遠差一格 ——
// 而且下一句稍微大聲一點又會再往回停在另一邊，長期看就是在目標附近來回晃。
const TOLERANCE_DB = 1.0;
const SETTLED_DB = 0.15;

// 連續安靜超過這麼久，就把增益慢慢放回 0 dB。
// 為什麼要放回去：唱完一首到下一首之間可能有一兩分鐘，
// 一直掛著 +9 dB 等於把整段空檔的底噪放大 9 dB（多人模式就是回授的溫床）。
const IDLE_RELEASE_SECONDS = 8;

// 增益差多少才算「機器真的在幫忙」（UI 徽章與統計用）。
const ACTIVE_GAIN_DB = 1.0;

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

/** 一幀的輸入電平（dBFS）。可以直接給 db，也可以給線性 rms。 */
function frameDb(frame) {
  if (!frame) return -Infinity;
  if (frame.db !== undefined) {
    const n = Number(frame.db);
    return Number.isFinite(n) ? n : -Infinity;
  }
  return dbFromRms(frame.rms);
}

class MicAutoGain {
  /**
   * @param {object} options
   *   enabled     是否啟用（關掉時增益恆為 1，等於沒有這個功能）
   *   targetDb    目標輸出電平（dBFS）
   *   maxBoostDb  最多加多少 dB
   *   maxCutDb    最多減多少 dB（正數，代表幅度）
   */
  constructor(options = {}) {
    this.enabled = options.enabled === undefined ? true : !!options.enabled;
    this.targetDb = clamp(options.targetDb === undefined ? DEFAULT_TARGET_DB : options.targetDb, -40, -3);
    this.maxBoostDb = clamp(options.maxBoostDb === undefined ? DEFAULT_MAX_BOOST_DB : options.maxBoostDb, 0, 24);
    this.maxCutDb = clamp(options.maxCutDb === undefined ? DEFAULT_MAX_CUT_DB : options.maxCutDb, 0, 24);
    this.reset();
  }

  /**
   * 設定頁或演唱模式改了就套用。
   *
   * 改參數不重置學到的增益 —— 唱到一半調目標值不該讓麥克風先變回原始音量。
   * 但開關切換兩個方向都重來：關掉要立刻放掉增益，打開要重新暖機
   * （不能沿用上次關掉前的包絡線就直接調整）。
   */
  configure(options = {}) {
    if (options.enabled !== undefined) {
      const next = !!options.enabled;
      if (next !== this.enabled) this.reset();
      this.enabled = next;
    }
    if (options.targetDb !== undefined) this.targetDb = clamp(options.targetDb, -40, -3);
    if (options.maxBoostDb !== undefined) this.maxBoostDb = clamp(options.maxBoostDb, 0, 24);
    if (options.maxCutDb !== undefined) this.maxCutDb = clamp(options.maxCutDb, 0, 24);
    // 範圍縮小（例如切到多人模式）時，現有增益要立刻夾回新範圍：
    // 這是安全動作，不能等下一幀慢慢趨近。
    this.gainDb = this._clampGain(this.gainDb);
    this.level = Math.pow(10, this.gainDb / 20);
    return this;
  }

  /** 換麥克風、關掉功能都要歸零。換歌刻意不歸零 —— 見 player.js 的說明。 */
  reset() {
    this.gainDb = 0;
    this.level = 1.0;
    this.envelopeDb = null;      // 還沒聽到任何人聲
    this.inputDb = -Infinity;    // 最近一幀的原始電平（音量表用）
    this.voicedSeconds = 0;
    this.silenceSeconds = 0;
    this.gainDbSeconds = 0;      // 有人聲的時間裡增益的積分（算平均用）
    this.peakInputDb = -Infinity;
    this.panicCount = 0;
    this.adjusting = false;      // 遲滯狀態：現在是不是正在往目標移動
    return this;
  }

  /**
   * 只歸零這一首的統計，保留學到的增益。
   *
   * 換歌時刻意不呼叫 reset()：下一位唱的人多半還沒換（一個人常唱兩三首），
   * 把增益丟回 0 dB 只會讓他每首歌的第一句都先小聲三秒。
   * 但「這首歌平均幫你調了多少」必須是這一首的數字，所以統計要清掉。
   *
   * 副作用（刻意留著）：voicedSeconds 同時是暖機計時器，所以換歌後會重新暖機
   * 1.2 秒。這正好是想要的行為 —— 換歌很可能換人，前奏那一兩句先維持現有增益、
   * 聽清楚新的人有多大聲再動，比立刻跟著第一個字亂調安全。
   */
  resetStats() {
    this.voicedSeconds = 0;
    this.gainDbSeconds = 0;
    this.peakInputDb = -Infinity;
    this.panicCount = 0;
    return this;
  }

  _clampGain(db) {
    return clamp(db, -this.maxCutDb, this.maxBoostDb);
  }

  /**
   * 餵一幀，回傳這一幀該用的線性增益倍率。
   *
   * @param {number} dt    距離上一幀幾秒（外面請夾在合理範圍，分頁切回來時會是好幾秒）
   * @param {object} frame { rms } 麥克風原始訊號的 RMS（0~1），或 { db } 直接給 dBFS
   */
  update(dt, frame) {
    const step = clamp(dt, 0, 0.25);
    if (!this.enabled) {
      this.gainDb = 0;
      this.level = 1.0;
      this.inputDb = frameDb(frame);
      return this.level;
    }
    if (step <= 0) return this.level;

    const db = frameDb(frame);
    this.inputDb = db;
    const voiced = db > SILENCE_DB;

    if (voiced) {
      this.voicedSeconds += step;
      this.silenceSeconds = 0;
      if (db > this.peakInputDb) this.peakInputDb = db;
      // 第一幀直接錨定在當下電平，不要從 0 dBFS 慢慢滑下來 ——
      // 那段下滑期間算出來的增益全是錯的（會誤以為輸入很大聲）。
      this.envelopeDb = this.envelopeDb === null
        ? db
        : approach(this.envelopeDb, db, step, db > this.envelopeDb ? ENV_RISE_TAU : ENV_FALL_TAU);
    } else {
      this.silenceSeconds += step;
    }

    let desiredDb = this.gainDb;   // 預設：維持現狀（安靜、暖機中、落在容忍區）
    let tau = GAIN_UP_TAU;
    let panic = false;

    if (voiced) {
      // 依電平算「想要的增益」。暖機過了、而且值得動（遲滯）才會有值。
      let levelDesired = null;
      if (this.voicedSeconds >= WARMUP_VOICED_SECONDS) {
        const wanted = this._clampGain(this.targetDb - this.envelopeDb);
        const gap = Math.abs(wanted - this.gainDb);
        if (!this.adjusting && gap >= TOLERANCE_DB) this.adjusting = true;
        if (this.adjusting) {
          levelDesired = wanted;
          if (gap < SETTLED_DB) this.adjusting = false;   // 到位了，收手
        }
      }

      // 削峰保護：量的是原始訊號，所以預估輸出 = 輸入 + 目前增益。
      // 這是唯一可以無視暖機與遲滯的情況，而且用最快的時間常數。
      const outDb = db + this.gainDb;
      if (outDb > CLIP_GUARD_DB) {
        panic = true;
        const guard = this._clampGain(CLIP_GUARD_DB - db);
        // 電平判斷要求降得比保護線更多時聽它的（例如貼著麥克風吼：
        // 保護線只要求不削峰，但目標電平要求再小 7 dB）。取兩者中較小的那個，
        // 否則保護線會變成「不准再降」的地板，把音量鎖在最大聲。
        desiredDb = levelDesired !== null ? Math.min(guard, levelDesired) : guard;
        tau = GAIN_PANIC_TAU;
      } else if (levelDesired !== null) {
        desiredDb = levelDesired;
        tau = levelDesired < this.gainDb ? GAIN_DOWN_TAU : GAIN_UP_TAU;
      }
    } else if (this.silenceSeconds > IDLE_RELEASE_SECONDS && this.gainDb !== 0) {
      // 沒人唱很久了：把增益放回中性，別讓它繼續放大空檔的底噪
      desiredDb = 0;
      tau = GAIN_IDLE_TAU;
      this.adjusting = false;   // 下一個人開口時重新判斷要不要調
    }

    if (panic) this.panicCount += 1;

    this.gainDb = this._clampGain(approach(this.gainDb, desiredDb, step, tau));
    // 指數趨近永遠差一點點才到 0，放回中性時會停在 0.001 dB 這種數字上
    if (desiredDb === 0 && Math.abs(this.gainDb) < 0.05) this.gainDb = 0;
    this.level = Math.pow(10, this.gainDb / 20);

    if (voiced) this.gainDbSeconds += this.gainDb * step;

    return this.level;
  }

  /** 機器現在是不是真的在幫忙調（UI 徽章用這個決定要不要顯示）。 */
  isActive() {
    return this.enabled && Math.abs(this.gainDb) >= ACTIVE_GAIN_DB;
  }

  /**
   * 音量表要顯示的 0~1 刻度。
   *
   * 對映的是 -54 ~ -6 dBFS：這段是麥克風實際會用到的範圍，
   * 拿整個 -inf~0 去畫的話，唱歌時指針永遠只在右半邊抖，看不出「太小聲」。
   */
  meterLevel() {
    if (!Number.isFinite(this.inputDb)) return 0;
    return clamp((this.inputDb + 54) / 48, 0, 1);
  }

  /**
   * 唱畢統計：這首歌機器平均幫你調了多少。
   *
   * 有效時間太短（不到 10 秒）就不給 —— 兩句歌的平均只是雜訊。
   * 這個數字的用途是回答「我是不是該把麥克風拿近一點」：
   * 一直是 +8 dB 表示唱得太遠，一直是 -10 dB 表示貼太近（而且大概會削峰）。
   */
  summary() {
    const enough = this.voicedSeconds >= 10;
    const avg = this.voicedSeconds > 0 ? this.gainDbSeconds / this.voicedSeconds : 0;
    return {
      voiced_seconds: Math.round(this.voicedSeconds * 10) / 10,
      gain_db: Math.round(this.gainDb * 10) / 10,
      average_gain_db: enough ? Math.round(avg * 10) / 10 : null,
      peak_input_db: Number.isFinite(this.peakInputDb)
        ? Math.round(this.peakInputDb * 10) / 10
        : null,
      clip_guarded: this.panicCount > 0,
    };
  }
}

if (typeof window !== "undefined") {
  window.MicAutoGain = MicAutoGain;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    MicAutoGain,
    dbFromRms,
    approach,
    DEFAULT_TARGET_DB,
    DEFAULT_MAX_BOOST_DB,
    DEFAULT_MAX_CUT_DB,
    PARTY_MAX_BOOST_DB,
    SILENCE_DB,
    CLIP_GUARD_DB,
    WARMUP_VOICED_SECONDS,
    TOLERANCE_DB,
    IDLE_RELEASE_SECONDS,
    ACTIVE_GAIN_DB,
  };
}
