/**
 * 智能修音 (Pitch Correction)
 *
 * 新一代商用點歌機與手機 K 歌 App 都有的那顆鍵（金嗓的「美聲」、
 * 全民K歌與唱吧的「智能修音」、DAM 的ピッチ補正）：唱出來的音高稍微偏了，
 * 機器在送進喇叭之前把它推回該在的位置。不是把人換成別人，
 * 是把「差一點點」補成「剛好」—— 而包廂裡**差一點點才是常態**。
 *
 * 這台機器在這一版之前，麥克風到喇叭之間完全沒有碰過音高：
 * 走音的那幾個字原封不動從喇叭出來，而唯一會提到它的是唱完之後的分數。
 * 也就是說整台機器對「唱不準」這件事只有**事後講評**，沒有**當下幫忙**。
 *
 * 這一支只放「要移多少」的判斷，不碰 Web Audio：
 * 實際的移調由 `harmony-shifter` 那顆 AudioWorklet 做（和聲用的是同一顆，
 * 見下方「為什麼共用」），接線與淡入淡出由 `audio-effects.js` 做。
 * 分開的理由跟 harmony-planner 一樣 —— 修多少是這個功能唯一重要的事
 * （多修一點就變成機器人、少修一點等於沒開），而 AudioWorklet 在 node 裡
 * 不存在，判斷寫在音訊引擎內部就一條都測不到。
 *
 * ---------------------------------------------------------------------------
 * 四個決定（每一個錯了都有很明確的現場症狀）
 * ---------------------------------------------------------------------------
 *
 * 一、**目標音是這首歌的導唱音符，不是最近的半音。**
 *     市面上最常見的修音是「吸附到最近的半音」（chromatic auto-tune），
 *     十行就寫得完，而且不需要知道任何關於這首歌的事。但它在包廂裡是錯的：
 *     一個人唱低了 70 cent 的時候，離他最近的半音是**他唱的那個錯音**，
 *     修音會把那個錯音修得更準、更有自信 —— 結果是「走音的地方反而更明顯」。
 *     真正該去的地方只有一個，就是導唱線上那個音；而這台機器本來就有
 *     （pitch.json 的 notes，評分與和聲已經在用同一份）。
 *     所以**沒有導唱音符的時刻一律不修**：前奏、間奏、念白、笑聲、講話、
 *     點歌的人在旁邊喊歌名 —— 這些時刻機器不知道「該唱什麼」，
 *     而不知道的時候最好的動作是不要動。
 *
 * 二、**差太多就不修。**
 *     誤差超過 CAPTURE_CENTS（150 cent，一個半音半）的時候，
 *     那通常不是「唱不準」，是**根本不是在唱這一句**：唱錯行、整段低八度、
 *     在跟旁邊的人講話、麥克風收到的是伴奏。硬修的結果是把那個聲音
 *     整個搬走一個半音以上，而移調器在那個量級上的顆粒感很明顯 ——
 *     聽起來就是外星人。所以捕捉範圍外一律放手（而且是**滑回去**，不是放掉）。
 *
 * 三、**修多快 ＝ 抖音與轉音活不活得下來。**
 *     這是整支模組最重要、也最容易被誤解的一個數字。修音如果瞬間到位，
 *     抖音（vibrato，5~7Hz、±50 cent）與轉音（滑音）會被完全抹平 ——
 *     那正是「T-Pain 效果」的由來，而那是一種效果，不是一台伴唱機該預設的事。
 *     所以修正量是**以時間常數慢慢滑過去的**：滑得比抖音慢，抖音就穿得過去；
 *     滑得比「唱完一個字之後定住的那個偏差」快，偏差就被修掉。
 *     強度那三段調的就是這一個數字（而不是只調「修多少比例」）：
 *     輕 150ms（抖音完全保留，只修定住之後的偏差）、
 *     中 90ms、強 45ms（接近即時，抖音會被削平一部分，聽得出修音的味道）。
 *
 * 四、**評分永遠不看修過的訊號。**
 *     這一條不在這個檔案裡，而在音訊圖的接法上：音準偵測用的 analyser 直接
 *     接在 `micSource` 上（`audio-effects.js` 的 `_routeInputs()`），
 *     而修音接在整條前級鏈的**最後面**。所以修音不可能影響分數 ——
 *     這是結構上的保證，不是一條記得要寫的 if。
 *     反過來說也成立：修音吃的是評分算出來的那一幀 `userMidi`，
 *     兩邊永遠看同一個數字，不會出現「分數說你準、修音卻在用力拉」。
 *     接反了的症狀是**所有人都一百分**，而那一天之後這台機器的分數
 *     就再也沒有意義了（而且沒有人查得出是哪一版開始的）。
 *
 * ---------------------------------------------------------------------------
 * 為什麼跟和聲共用同一顆移調器
 * ---------------------------------------------------------------------------
 * `harmony-shifter` 是可變延遲移調器：延遲量以固定斜率變化，走到底就跳回去，
 * 跳點藏在交叉淡接裡。顆粒感的多寡只跟**移調量**有關 —— 移得越多，
 * 讀取頭追得越快、跳得越頻繁。
 *
 * 而修音的移調量永遠在 ±1.5 半音以內，絕大多數時間在 ±0.3 半音以內：
 * 0.3 半音的比例是 1.0175，讀取頭要 2.5 秒才走完一輪。
 * 也就是說**修音是這顆移調器最輕鬆的工況**（和聲固定三度，每秒跳四次），
 * 顆粒感實際上聽不到。為它另外寫一顆相位聲碼器，換來的音質差異在包廂的
 * 喇叭上量不出來，卻多一份要維護的 DSP —— 而兩份 DSP 遲早會分家。
 *
 * 純資料邏輯：不碰 DOM、不發網路請求、不依賴 AudioContext。
 */

// 強度三段。`enabled` 是另一個獨立的開關 —— 「關閉」不是一種強度，
// 不然設定頁存著 strength="off" 的機器，下次有人打開修音時會打開一個沒有作用的功能。
//
// ratio  修正比例：1.0 = 完全修到導唱音符上，0.35 = 只把誤差拉近三分之一。
//        比例小於 1 不是偷懶，是**留下這個人的味道**：抒情歌刻意唱得比譜低一點
//        的那種處理，全修掉就變成伴唱帶自己在唱。
// glideMs 修正量滑過去的時間常數（見上方決定三）。
const PITCH_FIX_STRENGTHS = {
  light: {
    id: "light", label: "輕", ratio: 0.35, glideMs: 150,
    note: "只修定住之後的偏差，抖音與轉音完全保留",
  },
  medium: {
    id: "medium", label: "中", ratio: 0.65, glideMs: 90,
    note: "聽得出來比較準，但還聽得出是你在唱",
  },
  strong: {
    id: "strong", label: "強", ratio: 1.0, glideMs: 45,
    note: "貼著導唱音符走，抖音會被削掉一部分",
  },
};

const PITCH_FIX_STRENGTH_CHOICES = ["light", "medium", "strong"];
const DEFAULT_STRENGTH = "medium";

// 捕捉範圍（cent）。超出這個距離就不是「唱不準」而是「沒在唱這一句」。
// 150 cent = 一個半音半：半音（100）太窄，唱低一個全音的人常常只是
// 起音沒抓到、半秒之後就上來了，窄到把他排除掉等於整句都不修；
// 兩個半音（200）太寬，會開始把「唱錯音」一路拖到正確的音上，
// 而那個拖行的過程非常明顯（而且聽起來像機器在搶麥克風）。
const CAPTURE_CENTS = 150;

// 修正量的硬上限（半音）。捕捉範圍已經保證算出來的量不會超過 1.5，
// 這一道是給「以後有人把捕捉範圍開放成設定」準備的 —— 移調量越大顆粒感越重，
// 超過兩個半音之後那個聲音就不像本人了，而那是這個功能唯一不能犯的錯。
const MAX_SHIFT_SEMITONES = 2;

// 放手的時間常數（ms）。沒人唱、沒有導唱音符、誤差超出捕捉範圍時，
// 修正量**滑回 0** 而不是瞬間歸零：瞬間歸零等於在字的尾巴上製造一個
// 跟修正量一樣大的反向跳音，而那一下比原本的偏差還明顯。
// 比最慢的 glide 再慢一點，確保放手永遠不會比修音本身還急。
const RELEASE_MS = 180;

// 小於這個量就當成沒有修（半音）。0.01 半音 = 1 cent，遠低於人耳在
// 包廂裡分得出來的程度（約 5~10 cent）。存在的理由不是聽感而是
// **不要一直叫醒音訊執行緒**：k-rate 參數每改一次就是一次跨執行緒寫入，
// 而這個迴圈每秒跑 60 次。
const SHIFT_EPSILON = 0.01;

function clamp(value, lo, hi) {
  const n = Number(value);
  if (!Number.isFinite(n)) return lo;
  return Math.max(lo, Math.min(hi, n));
}

/** 認不得的強度一律回預設值（不是 null）—— 呼叫端不該為此多一條分支。 */
function strengthSpec(id) {
  return PITCH_FIX_STRENGTHS[id] || PITCH_FIX_STRENGTHS[DEFAULT_STRENGTH];
}

/** 正規化強度字串。舊版前端、還原回來的設定檔、手機送上來的值都走這裡。 */
function clampStrength(id) {
  return PITCH_FIX_STRENGTHS[id] ? String(id) : DEFAULT_STRENGTH;
}

/**
 * 誤差（cent）。正值 = 唱低了（要往上修）。
 *
 * 兩個音任何一個讀不出來就回 null 而不是 0 —— 0 的意思是「唱得剛好」，
 * 跟「不知道」是完全不同的兩件事，混在一起的症狀是沒有訊號時修音還在作用。
 */
function centsError(userMidi, targetMidi) {
  const user = Number(userMidi);
  const target = Number(targetMidi);
  if (!Number.isFinite(user) || user <= 0) return null;
  if (!Number.isFinite(target) || target <= 0) return null;
  return (target - user) * 100;
}

/** 這個誤差在捕捉範圍內嗎？（null＝沒有偵測到音高，一律當成不在範圍內） */
function inCaptureRange(cents) {
  return cents !== null && Number.isFinite(cents) && Math.abs(cents) <= CAPTURE_CENTS;
}

/**
 * 這一幀**想要**修多少（半音）。還沒經過時間平滑 —— 平滑是 update() 的事。
 */
function targetShift(cents, strength) {
  if (!inCaptureRange(cents)) return 0;
  const spec = strengthSpec(strength);
  const semitones = (cents / 100) * spec.ratio;
  return clamp(semitones, -MAX_SHIFT_SEMITONES, MAX_SHIFT_SEMITONES);
}

/**
 * 一階低通的係數：經過 dtMs 之後，要往目標走多少比例。
 *
 * 用 1 - exp(-dt/tau) 而不是固定比例，是因為**這個迴圈的幀距不固定**
 * （渲染迴圈跟著 requestAnimationFrame，而瀏覽器分頁在背景時會掉到每秒 1 幀）。
 * 固定比例的話，掉幀時修音會慢得像沒開；用時間常數算，掉幀只是走得比較大步，
 * 聽感不變。
 */
function glideFactor(dtMs, tauMs) {
  const dt = Math.max(0, Number(dtMs) || 0);
  const tau = Math.max(1, Number(tauMs) || 1);
  if (dt <= 0) return 0;
  return 1 - Math.exp(-dt / tau);
}

class PitchFixer {
  /**
   * @param {object} options
   *   enabled  是否啟用
   *   strength light / medium / strong
   */
  constructor(options = {}) {
    this.enabled = !!options.enabled;
    this.strength = clampStrength(options.strength);
    this.reset();
  }

  configure(options = {}) {
    if (options.enabled !== undefined) {
      const next = !!options.enabled;
      // 關掉的那一刻把修正量清掉，不要讓它滑回去：使用者按下「關閉」是
      // 因為他**現在**就覺得怪，而淡出由音訊圖那一側的乾濕交叉做
      // （那一條是連續的，所以不會有爆音）。
      if (!next && this.enabled) this.shift = 0;
      this.enabled = next;
      // 徽章在下一幀之前就會被重畫（開關是使用者剛按下去的那一刻），
      // 理由留在舊值上的話畫面會先閃一下上一個狀態的文字。
      if (!next) this.reason = "off";
      else if (this.reason === "off") this.reason = "idle";
    }
    if (options.strength !== undefined) this.strength = clampStrength(options.strength);
    return this;
  }

  /** 換歌／重唱／換人。修正量與統計都屬於「這一次演唱」。 */
  reset() {
    this.shift = 0;
    this.engaged = false;
    // 還沒餵過任何一幀的狀態是「等你開口」，不是「關閉」—— 兩者在徽章上
    // 是完全不同的兩句話，而換歌之後本來就還沒有人開口。
    this.reason = this.enabled ? "idle" : "off";
    this.frames = 0;         // 有偵測到歌聲、而且有導唱音符的幀
    this.engagedFrames = 0;  // 其中真的有在修的幀
    this.centsSum = 0;       // 修正量（cent）的帶號總和 —— 帶號才看得出「一直偏高」
    this.centsAbsSum = 0;
    this.maxAbsCents = 0;
    return this;
  }

  /**
   * 餵一幀。
   *
   * @param {number} dtMs 距離上一幀幾毫秒
   * @param {object} frame 評分心跳那一幀（pitch-engine.tick 的回傳值）
   *                       —— `sang` / `hasNote` / `noteMidi` / `userMidi`
   *                       用的是同一份判定，不重新偵測一次音高。
   * @returns {{shift:number, engaged:boolean, reason:string}}
   *   reason: off | idle | no_note | out_of_range | on
   */
  update(dtMs, frame) {
    if (!this.enabled) {
      this.shift = 0;
      this.engaged = false;
      this.reason = "off";
      return this.snapshot();
    }

    const spec = strengthSpec(this.strength);
    const sang = !!(frame && frame.sang);
    const hasNote = !!(frame && frame.hasNote);
    const userMidi = Number(frame && frame.userMidi) || 0;
    const noteMidi = Number(frame && frame.noteMidi) || 0;

    let reason = "on";
    if (!sang || userMidi <= 0) reason = "idle";
    else if (!hasNote || noteMidi <= 0) reason = "no_note";

    let cents = null;
    if (reason === "on") {
      cents = centsError(userMidi, noteMidi);
      // 有音高、有音符，但差得太遠 —— 那不是走音，是沒在唱這一句（見決定二）
      if (!inCaptureRange(cents)) reason = "out_of_range";
      this.frames++;
    }

    const want = reason === "on" ? targetShift(cents, this.strength) : 0;
    // 往目標走用 glide，放手用 RELEASE：放手永遠不該比修音還急，
    // 不然「唱完一個字放開」會比「把那個字修準」更明顯。
    const tau = reason === "on" ? spec.glideMs : RELEASE_MS;
    const k = glideFactor(dtMs, tau);
    const next = this.shift + (want - this.shift) * k;

    this.shift = Math.abs(next) < SHIFT_EPSILON ? 0 : clamp(next, -MAX_SHIFT_SEMITONES, MAX_SHIFT_SEMITONES);
    this.engaged = reason === "on" && Math.abs(this.shift) >= SHIFT_EPSILON;

    if (this.engaged) {
      const appliedCents = this.shift * 100;
      this.engagedFrames++;
      this.centsSum += appliedCents;
      this.centsAbsSum += Math.abs(appliedCents);
      if (Math.abs(appliedCents) > this.maxAbsCents) this.maxAbsCents = Math.abs(appliedCents);
    }

    this.reason = reason;
    return this.snapshot();
  }

  /**
   * 立刻放掉修正量，但**不清掉統計**。
   *
   * 用在「這一刻不該再修了」而演唱還在繼續的時候（使用者關掉修音、切進對唱）。
   * 跟 reset() 分開是因為那兩件事在現場是不同的：reset 是換歌／重唱
   * （上一次演唱結束了），release 只是這條路暫時不走 —— 把統計一起清掉的話，
   * 中途關掉再打開的那一次，console 上那一行會只算到後半首。
   */
  release() {
    this.shift = 0;
    this.engaged = false;
    this.reason = this.enabled ? "idle" : "off";
    return this.snapshot();
  }

  snapshot() {
    return { shift: this.shift, engaged: this.engaged, reason: this.reason };
  }

  /**
   * 這一次演唱的修音統計。
   *
   * 用途是**講得出這台機器幫了多少忙**：修音開著的時候，
   * 「我今天唱得不錯」有多少是自己的，使用者有權知道。
   * meanCents 帶號是刻意的 —— 一直是正的代表這個人整首都唱低，
   * 那不是修音該解決的問題（那是這首歌對他太高，該建議降 Key）。
   */
  stats() {
    const engaged = this.engagedFrames;
    return {
      frames: this.frames,
      engagedFrames: engaged,
      // 有導唱音符、而且真的在唱的時間裡，有多少比例被修過
      engagedRatio: this.frames > 0 ? Math.round((engaged / this.frames) * 1000) / 1000 : 0,
      meanCents: engaged > 0 ? Math.round((this.centsSum / engaged) * 10) / 10 : 0,
      meanAbsCents: engaged > 0 ? Math.round((this.centsAbsSum / engaged) * 10) / 10 : 0,
      maxCents: Math.round(this.maxAbsCents * 10) / 10,
    };
  }

  /** 舞台徽章要顯示的文字（沒啟用時回 null）。 */
  describe() {
    if (!this.enabled) return null;
    return `修音 ${strengthSpec(this.strength).label}`;
  }

  /**
   * 徽章上那一句「現在在做什麼」。
   *
   * 沒在修的時候一定要講原因 —— 不講的話「開了修音但聽起來一樣」
   * 會被當成故障，而四種原因裡有三種是完全正常的。
   */
  statusText() {
    switch (this.reason) {
      case "on": return this.engaged ? "修正中" : "已經很準";
      case "no_note": return "這一段沒有導唱音符";
      case "out_of_range": return "差太多，先不修";
      case "idle": return "等你開口";
      default: return "";
    }
  }

  /** 唱畢寫進 console 的一行（修音開著時才有意義）。 */
  summary() {
    if (!this.enabled || this.frames === 0) return "";
    const s = this.stats();
    if (s.engagedFrames === 0) return "修音：整首都在捕捉範圍內沒有需要修的地方";
    const pct = Math.round(s.engagedRatio * 100);
    const dir = s.meanCents > 0 ? "往上" : "往下";
    return `修音：${pct}% 的時間有作用，平均${dir} ${Math.abs(s.meanCents)} cent，最多 ${s.maxCents} cent`;
  }
}

if (typeof window !== "undefined") {
  window.PitchFixer = PitchFixer;
  window.PITCH_FIX_STRENGTHS = PITCH_FIX_STRENGTHS;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    PitchFixer,
    PITCH_FIX_STRENGTHS,
    PITCH_FIX_STRENGTH_CHOICES,
    DEFAULT_STRENGTH,
    CAPTURE_CENTS,
    MAX_SHIFT_SEMITONES,
    RELEASE_MS,
    SHIFT_EPSILON,
    strengthSpec,
    clampStrength,
    centsError,
    inCaptureRange,
    targetShift,
    glideFactor,
  };
}
