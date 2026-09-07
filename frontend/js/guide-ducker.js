/**
 * 導唱音量自動 ducking (Guide Vocal Auto-Ducking)
 *
 * 商用 KTV（DAM 的「ガイドボーカル自動フェード」、金嗓的「導唱漸弱」）都有這一招：
 * 導唱人聲不是「開」或「關」兩種狀態，而是跟著唱的人走 ——
 * 唱穩了它自己退到背景，唱不下去（走音、忘詞、整句沒聲音）它馬上回來扶一把。
 *
 * 為什麼值得單獨做一個模組：這件事錯了會非常明顯地毀掉體驗。
 *   * 退得太快 → 副歌第一句唱準兩個字，導唱就消失，第三個字忘詞當場裸奔。
 *   * 回得太慢 → 已經走音兩秒了才聽到導唱，那時候人早就放棄這一句。
 *   * 沒有遲滯 → 命中率在門檻附近抖動時導唱一直忽大忽小，比全開還吵。
 * 所以這裡刻意做成「慢慢退、立刻回」的不對稱曲線，加上 Schmitt 遲滯門檻，
 * 而且全部寫成純資料邏輯（不碰 DOM、不碰 Web Audio），才能用 node --test 守住。
 *
 * 用法：每一幀餵一次 update(dt, frame)，拿回 0~1 的導唱增益倍率，
 * 乘在使用者自己設定的導唱音量上（所以導唱本來就關掉時，這裡怎麼算都還是 0）。
 */

// 命中率的觀察窗（秒）。用時間常數而不是幀數，才不會在 30fps 的機器上變成兩倍慢。
// 2.5 秒約等於一句歌詞：短於一句的失誤不該把導唱整個叫回來。
const CONFIDENCE_TAU = 2.5;

// Schmitt 遲滯：命中率升破 ENGAGE 才開始退，掉破 RELEASE 才回來。
// 兩個門檻中間的區域維持現狀 —— 這是「導唱忽大忽小」唯一有效的解法。
// 0.55/0.35 是照逐幀判定的實際手感抓的：逐幀命中比人以為的嚴格，
// 唱得相當不錯的人整首大約落在 0.5~0.7，照直覺設 0.8 會永遠不退。
const ENGAGE_CONFIDENCE = 0.55;
const RELEASE_CONFIDENCE = 0.35;

// 淡出慢（1.6 秒時間常數）、淡回快（0.22 秒）。
// 不對稱是刻意的：退場是背景動作，回來是救援。
const ATTACK_TAU = 1.6;
const RELEASE_TAU = 0.22;

// 開始退場前至少要累積這麼多秒的「有導唱音符」時間。
// 沒有這道門檻的話，前奏後第一個音唱準就會觸發，那只是運氣不是穩定。
const WARMUP_SECONDS = 3.0;

// 導唱降到多小聲以下算「真的退場了」（給徽章顯示與統計用）。
const ENGAGED_LEVEL_EPSILON = 0.97;

const DEFAULT_DEPTH = 0.6;

function clamp(value, lo, hi) {
  const n = Number(value);
  if (!Number.isFinite(n)) return lo;
  return Math.max(lo, Math.min(hi, n));
}

/**
 * 一階低通（指數趨近）。用 dt 與時間常數算係數，結果與更新頻率無關。
 * tau <= 0 視為立刻到位。
 */
function approach(current, target, dt, tau) {
  if (!(tau > 0)) return target;
  const alpha = 1 - Math.exp(-dt / tau);
  return current + (target - current) * alpha;
}

class GuideDucker {
  /**
   * @param {object} options
   *   enabled  是否啟用（關掉時 level 恆為 1，等於沒有這個功能）
   *   depth    最多降多少（0.6 = 最低降到原本的 40%）。1.0 會整個消音。
   */
  constructor(options = {}) {
    this.enabled = options.enabled === undefined ? true : !!options.enabled;
    this.depth = clamp(options.depth === undefined ? DEFAULT_DEPTH : options.depth, 0, 0.95);
    this.reset();
  }

  /** 設定頁改了就套用。改深度不重置狀態 —— 唱到一半調滑桿不該讓導唱跳回全開。 */
  configure(options = {}) {
    if (options.enabled !== undefined) {
      const next = !!options.enabled;
      // 開關切換兩個方向都重來：關掉要立刻放掉導唱（不能等下一幀才回全開），
      // 打開要重新暖機（不能沿用關掉之前的信心度直接退場）。
      if (next !== this.enabled) this.reset();
      this.enabled = next;
    }
    if (options.depth !== undefined) {
      this.depth = clamp(options.depth, 0, 0.95);
    }
    return this;
  }

  /** 換歌、重唱都要歸零：信心度與暖機時間不能跨曲累計。 */
  reset() {
    this.level = 1.0;
    this.confidence = 0;
    this.engaged = false;
    this.noteSeconds = 0;
    this.duckedSeconds = 0;
    return this;
  }

  /**
   * 餵一幀，回傳這一幀該用的導唱增益倍率（0~1）。
   *
   * @param {number} dt    距離上一幀幾秒（外面請夾在合理範圍，分頁切回來時會是好幾秒）
   * @param {object} frame { hasNote, sang, hit } —— 與段落評分共用同一份逐幀判定
   */
  update(dt, frame) {
    const step = clamp(dt, 0, 0.25);
    if (!this.enabled) {
      this.confidence = 0;
      this.engaged = false;
      this.level = 1.0;
      return this.level;
    }
    if (step <= 0) return this.level;

    const hasNote = !!(frame && frame.hasNote);
    const hit = !!(frame && frame.hit);

    if (hasNote) {
      // 有導唱音符的時刻才更新信心度。沒唱（sang=false）自然就是 hit=false，
      // 所以「整句沒出聲」會跟走音一樣把導唱叫回來 —— 這正是忘詞時要的行為。
      this.confidence = approach(this.confidence, hit ? 1 : 0, step, CONFIDENCE_TAU);
      this.noteSeconds += step;
    }
    // 前奏／間奏（hasNote=false）不更新信心度也不重新判定：
    // 那段沒有導唱人聲可退，硬要衰減只會讓副歌一進來導唱先大聲一下再退，變成幫倒忙。

    if (this.noteSeconds >= WARMUP_SECONDS) {
      if (!this.engaged && this.confidence >= ENGAGE_CONFIDENCE) this.engaged = true;
      else if (this.engaged && this.confidence <= RELEASE_CONFIDENCE) this.engaged = false;
    }

    const target = this.engaged ? 1 - this.depth : 1;
    const tau = target < this.level ? ATTACK_TAU : RELEASE_TAU;
    this.level = clamp(approach(this.level, target, step, tau), 0, 1);
    // 浮點的指數趨近永遠差一點點才到 1，看起來會像「導唱回不到全開」
    if (!this.engaged && this.level > 0.999) this.level = 1.0;

    if (hasNote && this.level < ENGAGED_LEVEL_EPSILON) this.duckedSeconds += step;

    return this.level;
  }

  /** 導唱現在是不是真的被壓下去了（舞台徽章用這個決定要不要亮）。 */
  isDucking() {
    return this.enabled && this.level < ENGAGED_LEVEL_EPSILON;
  }

  /**
   * 唱畢統計：導唱在「有導唱音符的時間」裡有多少比例是退場的。
   *
   * 這個數字比總分更能說明一件事 —— 你有多不需要導唱。
   * 有效時間太短（不到 20 秒）就不給，兩句歌的比例只是雜訊。
   */
  summary() {
    const enough = this.noteSeconds >= 20;
    const ratio = this.noteSeconds > 0 ? this.duckedSeconds / this.noteSeconds : 0;
    return {
      note_seconds: Math.round(this.noteSeconds * 10) / 10,
      ducked_seconds: Math.round(this.duckedSeconds * 10) / 10,
      independence: enough ? Math.round(ratio * 1000) / 1000 : null,
    };
  }
}

if (typeof window !== "undefined") {
  window.GuideDucker = GuideDucker;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    GuideDucker,
    approach,
    CONFIDENCE_TAU,
    ENGAGE_CONFIDENCE,
    RELEASE_CONFIDENCE,
    ATTACK_TAU,
    RELEASE_TAU,
    WARMUP_SECONDS,
    ENGAGED_LEVEL_EPSILON,
    DEFAULT_DEPTH,
  };
}
