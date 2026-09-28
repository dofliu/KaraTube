/**
 * 防嘯叫 / 回授抑制 (Feedback Guard)
 *
 * 包廂裡最刺耳的一種故障：有人把麥克風轉向喇叭、或走到喇叭前面，
 * 「喇叭 → 空氣 → 麥克風 → 喇叭」這條迴路在房間響應最高的那個頻率上
 * 開始自激，兩秒之內變成一聲尖叫。商用擴大機（金嗓／音圓的「防嘯叫」、
 * 專業機的 Feedback Exterminator）都是同一招：**找出正在自激的那個頻率，
 * 在那一點上挖一個很窄的凹槽**，把迴路增益壓到 1 以下，其餘頻率一動不動。
 *
 * 為什麼不是「整支麥克風轉小聲」：那能止叫，但同時把人聲一起弄小，
 * 而且下一次還是會叫（迴路增益只降了幾 dB）。窄凹槽的代價只有那一個
 * 三十分之一倍頻的頻帶 —— 在歌聲裡幾乎聽不出來，卻正好是迴路唯一的糧食。
 *
 * 這支檔案只做「判斷與配點」，純資料邏輯（不碰 DOM、不碰 Web Audio），
 * 這樣才能用 node --test 把每一條規則釘死。真正的濾波器在
 * audio-effects.js 的麥克風前級鏈上（一排常駐的 peaking biquad，
 * 沒用到的增益就是 0 dB，等於不存在）。
 *
 * ── 四個決定，每一個都對應一種真的會發生的災難 ──────────────────
 *
 * 1. **只在人聲真的會從喇叭出來的時候作用。**
 *    單人模式（戴耳機、人聲不進喇叭）根本沒有回授迴路，這時候挖凹槽
 *    是在沒有病的地方開刀：唱得久了一晚上會累積四個凹槽，而每一個都只是
 *    某一次唱得很直很長的長音。所以 armed 由演唱模式決定，關掉時全部放掉。
 *
 * 2. **「長音」與「嘯叫」要分得開，而且寧可晚半秒也不要誤判。**
 *    兩者在頻譜上都是一根很突出的窄峰。分得開的是**時間上的行為**：
 *      * 嘯叫釘死在同一個 bin 上（它是房間的共振，不是人）；
 *        人唱的長音有抖音，峰值會來回飄一兩個 bin。
 *      * 嘯叫只會越來越大（迴路增益 > 1 就是指數成長）；
 *        人唱的長音會衰減、會換氣。
 *      * 嘯叫是一根孤峰；人聲是一整列諧波，所以峰的 1/2、1/3 頻率上
 *        通常也有東西 —— 那代表這根峰是某個更低的基頻的泛音，不是自激。
 *    誤判的代價不對稱：漏判是繼續叫（兩秒後還會再抓一次），
 *    誤判是把人聲挖掉一塊而且**沒有人會知道原因**。所以判準偏保守，
 *    只有一個例外：電平已經很大又極度純的那種（真正在叫的時候），
 *    走快車道 0.18 秒就鎖 —— 那個當下多等 0.5 秒是要人摀耳朵的。
 *
 * 3. **凹槽一次只挖一格深，不夠再加深。**
 *    −18 dB 的凹槽一定止得住叫，但它同時把那個頻帶從人聲裡整個拿掉。
 *    所以從 −6 dB 開始，還在叫就 −10、−14、−18。多數情況第一格就夠了
 *    （迴路增益通常只超過 1 幾 dB），而第一格的音色代價幾乎是零。
 *
 * 4. **放掉要慢，而且一次只放一格。**
 *    這裡有個容易想錯的地方：凹槽一旦挖下去，嘯叫就從**麥克風的輸入**
 *    消失了（迴路斷了，房間裡不再有那個音）。所以機器**永遠看不到**
 *    「原因已經排除」—— 沒有任何訊號能證明麥克風已經離開喇叭。
 *    唯一能知道的方法是**放掉一格試試看**。所以放掉是定時的（每 45 秒
 *    回一格），而且同時只有一個凹槽在試 —— 全部一起放的話，
 *    原因還在的房間會當場四個頻率一起叫回來，那比沒有這個功能還糟。
 *
 * 用法：每一幀餵一次 update(dt, { spectrum, sampleRate, fftSize })，
 * 拿回這一幀該有的凹槽清單，直接送進 audio-effects。
 */

// 只在這個頻段裡找。下限 180 Hz：再低的迴授在包廂的小喇叭上幾乎不會發生，
// 而那一段全是人聲基頻與鼓聲 —— 在那裡挖凹槽的誤判成本最高。
// 上限 8 kHz：再高的能量已經被前級的高頻柔化壓過一輪，而且那一段
// bin 很寬（2048 點在 48k 下一格 23 Hz，8 kHz 上下只有幾個 bin 的差別）。
const HOWL_MIN_HZ = 180;
const HOWL_MAX_HZ = 8000;

// 一根峰要比「附近的地板」高出這麼多 dB 才算候選。
// 地板取的是中位數而不是平均：平均會被峰自己拉高，越尖的峰越測不出來。
const HOWL_PROMINENCE_DB = 18;

// 比這還小聲的峰不算。安靜房間的底噪裡也有起伏，照樣算得出 18 dB 的突出，
// 但那不是嘯叫（嘯叫的定義之一就是「大聲」）。
const HOWL_ONSET_DB = -52;

// 快車道：電平與突出度都到這個程度，就是真的在叫了，不必等滿鎖定時間。
const HOWL_HOT_DB = -14;
const HOWL_HOT_PROMINENCE_DB = 26;

// 算地板時跳過峰兩側各 3 格（峰本身會糊到隔壁），往外取到第 12 格。
const HOWL_SKIRT_BINS = 3;
const HOWL_FLOOR_BINS = 12;

// 峰要連續存在這麼久才鎖定（快車道用 HOT 那一個）。
const HOWL_LOCK_SECONDS = 0.5;
const HOWL_HOT_LOCK_SECONDS = 0.18;

// 鎖定期間峰值比自己的最高點掉超過這麼多 dB，就認定是人唱的長音（會衰減），
// 候選作廢。嘯叫在鎖定的這半秒裡只會往上走。
const HOWL_DECAY_TOLERANCE_DB = 5;

// 峰值飄超過這麼多格就當成換了一根（抖音會飄，共振不會）。
const HOWL_DRIFT_BINS = 1;

// 候選在某一幀消失還可以撐這麼久（頻譜每幀都有抖動，一格都不能漏會太嚴苛）。
const HOWL_MISS_SECONDS = 0.12;

// 漏掉一幀要從已累積的時間裡扣掉幾倍回去。
//
// 這一條才是抖音真正的擋牆：光靠「漏太久就作廢」擋不住 5 Hz 的抖音 ——
// 它離開某一格的時間（約 0.1 秒）剛好落在容忍值裡面，於是一格一格地
// 斷斷續續累積，唱久了照樣鎖定。真正在自激的頻率**一幀都不會漏**，
// 所以用「漏一幀賠三幀」把間歇性的東西永遠擋在門檻外，
// 同時留住對單幀雜訊的寬容。
const HOWL_MISS_PENALTY = 3;

// 凹槽的深度階梯（dB）。第一格刻意很淺 —— 多數迴路只超過 1 幾 dB。
const NOTCH_DEPTH_STEPS = [-6, -10, -14, -18];

// 凹槽的 Q。14 大約是 1/10 倍頻，跟專業回授抑制器的固定濾波器同一個量級：
// 窄到唱歌聽不出來，又寬到不會因為房間共振漂了半個 bin 就失效。
const NOTCH_Q = 14;

// 同一個凹槽要蓋住的範圍（半音）。新抓到的頻率落在既有凹槽的這個距離內，
// 算成「同一個點又叫了」去加深，而不是再開一格。
// 0.7 個半音約等於 Q=14 的 −3dB 頻寬，兩者刻意對齊。
const NOTCH_MERGE_SEMITONES = 0.7;

// 每隔這麼久把一個凹槽回退一格（見上面的決定 4）。
const NOTCH_RELEASE_SECONDS = 45;

// 兩次回退之間至少隔這麼久，而且同時只有一個凹槽在試。
const NOTCH_PROBE_GAP_SECONDS = 8;

// 凹槽數量的預設值與上限。超過上限代表問題不在某幾個頻率上，
// 而是整體增益開太大 —— 那是人要處理的事（見 overloaded）。
const NOTCH_DEFAULT_MAX = 4;
const NOTCH_HARD_MAX = 8;

// 「壓不住了」這面旗子舉起來之後撐多久。不會自己消失的警告最後等於沒有警告：
// 使用者把麥克風拿開了，畫面卻還在罵他，下一次他就不看了。
const OVERLOAD_HOLD_SECONDS = 20;

function fbClamp(value, lo, hi) {
  const n = Number(value);
  if (!Number.isFinite(n)) return lo;
  return Math.max(lo, Math.min(hi, n));
}

/** 中位數。拿來當「附近的地板」，因為它不會被峰自己拉高。 */
function fbMedian(values) {
  if (!values.length) return -Infinity;
  const sorted = Array.prototype.slice.call(values).sort((a, b) => a - b);
  const mid = sorted.length >> 1;
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

/** 兩個頻率差幾個半音（永遠回非負數）。 */
function fbSemitonesApart(a, b) {
  if (!(a > 0) || !(b > 0)) return Infinity;
  return Math.abs(12 * Math.log2(a / b));
}

/**
 * 從一幀頻譜裡找出「看起來像嘯叫」的窄峰。
 *
 * 輸入是 AnalyserNode.getFloatFrequencyData() 那種 dB 陣列（長度 fftSize/2）。
 * 只做這一幀看得出來的事 —— 時間上的判斷（持續、成長、飄移）在 update() 裡，
 * 拆開是為了讓兩邊都能單獨測。
 *
 * @returns {Array<{bin:number, freq:number, db:number, prominence:number}>}
 *          依突出度由高到低排序
 */
function findHowlPeaks(spectrum, sampleRate, fftSize, options = {}) {
  const out = [];
  if (!spectrum || !spectrum.length || !(sampleRate > 0)) return out;
  const bins = spectrum.length;
  const size = fftSize > 0 ? fftSize : bins * 2;
  const binHz = sampleRate / size;
  if (!(binHz > 0)) return out;

  const onsetDb = options.onsetDb === undefined ? HOWL_ONSET_DB : options.onsetDb;
  const prominenceDb = options.prominenceDb === undefined
    ? HOWL_PROMINENCE_DB : options.prominenceDb;

  const lo = Math.max(1, Math.ceil(HOWL_MIN_HZ / binHz));
  const hi = Math.min(bins - 2, Math.floor(HOWL_MAX_HZ / binHz));

  for (let i = lo; i <= hi; i++) {
    const db = spectrum[i];
    if (!Number.isFinite(db) || db < onsetDb) continue;
    // 必須是局部最大（跟左右鄰居比）。平頂（寬帶噪音）不是嘯叫。
    if (!(db >= spectrum[i - 1] && db > spectrum[i + 1])) continue;

    const floorBins = [];
    for (let k = HOWL_SKIRT_BINS; k <= HOWL_FLOOR_BINS; k++) {
      const left = spectrum[i - k];
      const right = spectrum[i + k];
      if (Number.isFinite(left)) floorBins.push(left);
      if (Number.isFinite(right)) floorBins.push(right);
    }
    if (floorBins.length < 4) continue;
    const prominence = db - fbMedian(floorBins);
    if (!(prominence >= prominenceDb)) continue;

    out.push({ bin: i, freq: i * binHz, db, prominence });
  }

  // 諧波剔除：這根峰如果剛好是某根**更低的**峰的整數倍，那它多半是那根低音的
  // 泛音（人聲的母音就是一整列這種東西），不是自激。真正在叫的那一根是孤峰。
  //
  // 只往下看不往上看是刻意的，而且方向不能反過來：嘯叫被喇叭推到失真時
  // 自己也會長出二次諧波，照「有泛音就是人聲」去判的話，被放掉的正好是
  // 要抓的那根基頻。倍數看到 8 是因為母音的共振峰常落在第五到第八泛音上，
  // 只看 2、3 的話 5f 那一根會漏掉（而它照樣夠突出、夠持久）。
  //
  // 代價（知道且接受）：真的同時有 1 kHz 與 2 kHz 兩個自激點時，2 kHz 那一根
  // 會被當成泛音放過。但壓掉 1 kHz 之後迴路就斷了，2 kHz 多半跟著消失；
  // 萬一沒有，下一輪它會以孤峰的身分再被抓到。
  const strong = out.filter((p) => p.prominence >= prominenceDb * 0.6);
  const filtered = out.filter((peak) => {
    for (const lower of strong) {
      if (lower === peak || !(lower.freq > 0) || lower.freq >= peak.freq) continue;
      const ratio = peak.freq / lower.freq;
      const n = Math.round(ratio);
      if (n < 2 || n > 8) continue;
      if (fbSemitonesApart(peak.freq, lower.freq * n) <= 0.6) return false;
    }
    return true;
  });

  filtered.sort((a, b) => b.prominence - a.prominence);
  return filtered;
}

class FeedbackGuard {
  /**
   * @param {object} options
   *   enabled     是否啟用（關掉時凹槽全放掉，等於沒有這個功能）
   *   maxFilters  最多同時幾個凹槽
   */
  constructor(options = {}) {
    this.enabled = options.enabled === undefined ? true : !!options.enabled;
    this.maxFilters = Math.round(fbClamp(
      options.maxFilters === undefined ? NOTCH_DEFAULT_MAX : options.maxFilters, 1, NOTCH_HARD_MAX));
    this.armed = false;
    this.reset();
  }

  /**
   * 設定頁改了就套用。
   *
   * 調小數量上限時要立刻砍掉多出來的凹槽（留著的話它們會一直在音訊圖上，
   * 而設定頁說的是另一個數字）。砍最淺的那幾個：深的那幾個是真的在止叫。
   */
  configure(options = {}) {
    if (options.enabled !== undefined) {
      const next = !!options.enabled;
      if (next !== this.enabled) this.reset();
      this.enabled = next;
    }
    if (options.maxFilters !== undefined) {
      this.maxFilters = Math.round(fbClamp(options.maxFilters, 1, NOTCH_HARD_MAX));
      if (this.slots.length > this.maxFilters) {
        this.slots.sort((a, b) => a.step - b.step);
        this.slots = this.slots.slice(this.slots.length - this.maxFilters);
        this._sortSlots();
      }
    }
    return this;
  }

  /**
   * 人聲現在會不會從喇叭出來（多人模式＝會）。
   *
   * 從 true 變 false 就把凹槽全部放掉：迴路已經被物理性切斷，
   * 再留著只是白白在人聲上挖洞（決定 1）。反方向不做任何事 ——
   * 剛切到多人模式時還沒有任何證據說哪個頻率會叫。
   */
  setArmed(armed) {
    const next = !!armed;
    if (this.armed && !next) this.reset();
    this.armed = next;
    return this;
  }

  reset() {
    this.slots = [];            // 目前掛著的凹槽
    this.nextSlotId = 1;        // 凹槽的身分證（見 notches() 的說明）
    this.candidates = new Map();  // bin -> 追蹤中的候選
    this.probeCooldown = 0;     // 距離下一次可以回退還有幾秒
    this.probingFreq = null;    // 正在試放的那個凹槽（同時只有一個）
    this.overloaded = false;    // 凹槽用完了還在叫
    this.overloadSeconds = 0;   // 旗子舉起來多久了（到時間自己放下）
    this.caughtCount = 0;       // 這一場總共抓到幾次（統計用）
    this.lastCatchFreq = null;
    return this;
  }

  _sortSlots() {
    this.slots.sort((a, b) => a.freq - b.freq);
  }

  _slotNear(freq) {
    for (const slot of this.slots) {
      if (fbSemitonesApart(slot.freq, freq) <= NOTCH_MERGE_SEMITONES) return slot;
    }
    return null;
  }

  /**
   * 餵一幀頻譜，回傳這一幀該有的凹槽清單。
   *
   * @param {number} dt    距離上一幀幾秒
   * @param {object} frame { spectrum, sampleRate, fftSize }
   *        spectrum = AnalyserNode.getFloatFrequencyData 的 dB 陣列
   *        量的必須是**麥克風原始訊號**（凹槽之前）—— 量凹槽之後的話，
   *        凹槽一掛上去證據就消失，深度永遠停在第一格。
   */
  update(dt, frame) {
    const step = fbClamp(dt, 0, 0.25);
    if (!this.enabled || !this.armed) {
      if (this.slots.length) this.reset();
      return this.notches();
    }
    if (step <= 0) return this.notches();

    const peaks = findHowlPeaks(
      frame && frame.spectrum,
      frame && frame.sampleRate,
      frame && frame.fftSize,
    );

    this._track(step, peaks);
    this._release(step);

    if (this.overloaded) {
      this.overloadSeconds += step;
      if (this.overloadSeconds > OVERLOAD_HOLD_SECONDS) {
        this.overloaded = false;
        this.overloadSeconds = 0;
      }
    }
    return this.notches();
  }

  /** 候選的追蹤與鎖定（決定 2、3）。 */
  _track(step, peaks) {
    const seen = new Set();

    for (const peak of peaks) {
      // 找一個位置夠近的既有候選（抖音會飄，但只飄一兩格）
      let key = null;
      for (const bin of this.candidates.keys()) {
        if (Math.abs(bin - peak.bin) <= HOWL_DRIFT_BINS) { key = bin; break; }
      }

      if (key === null) {
        this.candidates.set(peak.bin, {
          seconds: 0, missSeconds: 0,
          peakDb: peak.db, freq: peak.freq,
          hot: peak.db >= HOWL_HOT_DB && peak.prominence >= HOWL_HOT_PROMINENCE_DB,
        });
        seen.add(peak.bin);
        continue;
      }

      const cand = this.candidates.get(key);
      seen.add(key);
      cand.missSeconds = 0;
      cand.seconds += step;
      cand.freq = peak.freq;
      if (peak.db > cand.peakDb) cand.peakDb = peak.db;
      if (peak.db >= HOWL_HOT_DB && peak.prominence >= HOWL_HOT_PROMINENCE_DB) cand.hot = true;

      // 掉下來了：人唱的長音會衰減、會換氣，自激不會。作廢重來。
      if (peak.db < cand.peakDb - HOWL_DECAY_TOLERANCE_DB) {
        this.candidates.delete(key);
        continue;
      }

      const needed = cand.hot ? HOWL_HOT_LOCK_SECONDS : HOWL_LOCK_SECONDS;
      if (cand.seconds >= needed) {
        this._catch(cand.freq);
        this.candidates.delete(key);
      }
    }

    // 沒被看到的候選慢慢過期（頻譜每幀都在抖，一格都不能漏會太嚴苛）
    for (const [bin, cand] of Array.from(this.candidates)) {
      if (seen.has(bin)) continue;
      cand.missSeconds += step;
      cand.seconds = Math.max(0, cand.seconds - step * HOWL_MISS_PENALTY);
      if (cand.missSeconds > HOWL_MISS_SECONDS) this.candidates.delete(bin);
    }
  }

  /** 抓到一個要壓的頻率：既有的加深一格，沒有的開一格新的。 */
  _catch(freq) {
    this.caughtCount += 1;
    this.lastCatchFreq = freq;

    const existing = this._slotNear(freq);
    if (existing) {
      // 正在試放的那一個又叫回來了 —— 試放失敗，原因還在
      if (this.probingFreq !== null
          && fbSemitonesApart(this.probingFreq, existing.freq) <= NOTCH_MERGE_SEMITONES) {
        this.probingFreq = null;
      }
      existing.step = Math.min(existing.step + 1, NOTCH_DEPTH_STEPS.length - 1);
      existing.heldSeconds = 0;
      this.overloaded = false;
      return;
    }

    if (this.slots.length >= this.maxFilters) {
      // 凹槽用完了還在叫。這不是「再挖一個」能解決的事 ——
      // 刻意**不**自己把麥克風轉小聲：使用者會聽到麥克風莫名其妙變小，
      // 而且找不到是誰動的（他沒碰滑桿）。這裡只舉手，畫面上講出實體的解法。
      this.overloaded = true;
      this.overloadSeconds = 0;
      return;
    }

    this.slots.push({ id: this.nextSlotId++, freq, step: 0, heldSeconds: 0 });
    this._sortSlots();
    this.overloaded = false;
  }

  /** 定時回退（決定 4）：每次只放一格，而且同時只有一個凹槽在試。 */
  _release(step) {
    for (const slot of this.slots) slot.heldSeconds += step;

    if (this.probeCooldown > 0) {
      this.probeCooldown = Math.max(0, this.probeCooldown - step);
      return;
    }
    if (this.probingFreq !== null) {
      // 上一次試放還沒有被推翻：撐過一個完整的間隔就算成功，換下一個試
      const probing = this._slotNear(this.probingFreq);
      if (!probing || probing.heldSeconds >= NOTCH_RELEASE_SECONDS) this.probingFreq = null;
      if (this.probingFreq !== null) return;
    }

    // 挑掛最久的那一個回退一格。掛最久的最有可能是「原因早就不在了」。
    let target = null;
    for (const slot of this.slots) {
      if (slot.heldSeconds < NOTCH_RELEASE_SECONDS) continue;
      if (!target || slot.heldSeconds > target.heldSeconds) target = slot;
    }
    if (!target) return;

    target.heldSeconds = 0;
    this.probeCooldown = NOTCH_PROBE_GAP_SECONDS;
    if (target.step > 0) {
      target.step -= 1;
      this.probingFreq = target.freq;
    } else {
      this.slots = this.slots.filter((s) => s !== target);
      this.probingFreq = null;
      if (!this.slots.length) this.overloaded = false;
    }
  }

  /**
   * 這一幀要送進音訊圖的凹槽（身分、頻率、深度、Q），依頻率排序。
   *
   * `id` 不是裝飾：音訊圖那邊是一排固定的濾波器，要靠它認出「還是同一個凹槽」。
   * 只照清單順序對應的話，中間插進一個新的頻率會讓後面每一個濾波器
   * 都換到別的頻率上 —— 那是一排正在作用中的濾波器同時掃頻，
   * 在包廂裡聽起來是「噗」的一聲，而且原本壓住的那幾個點會同時鬆開。
   */
  notches() {
    return this.slots.map((slot) => ({
      id: slot.id,
      freq: Math.round(slot.freq * 10) / 10,
      gainDb: NOTCH_DEPTH_STEPS[slot.step],
      q: NOTCH_Q,
    }));
  }

  /** 機器現在是不是真的壓著什麼（舞台徽章用這個決定要不要亮）。 */
  isActive() {
    return this.enabled && this.armed && this.slots.length > 0;
  }

  /** 唱畢／現場診斷用的數字。 */
  summary() {
    return {
      notches: this.slots.length,
      max_filters: this.maxFilters,
      deepest_db: this.slots.length
        ? Math.min.apply(null, this.slots.map((s) => NOTCH_DEPTH_STEPS[s.step]))
        : 0,
      caught: this.caughtCount,
      overloaded: this.overloaded,
      frequencies: this.slots.map((s) => Math.round(s.freq)),
    };
  }
}

if (typeof window !== "undefined") {
  window.FeedbackGuard = FeedbackGuard;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    FeedbackGuard,
    findHowlPeaks,
    fbMedian,
    fbSemitonesApart,
    HOWL_MIN_HZ,
    HOWL_MAX_HZ,
    HOWL_PROMINENCE_DB,
    HOWL_ONSET_DB,
    HOWL_HOT_DB,
    HOWL_HOT_PROMINENCE_DB,
    HOWL_LOCK_SECONDS,
    HOWL_HOT_LOCK_SECONDS,
    HOWL_DECAY_TOLERANCE_DB,
    HOWL_MISS_PENALTY,
    NOTCH_DEPTH_STEPS,
    NOTCH_Q,
    NOTCH_MERGE_SEMITONES,
    NOTCH_RELEASE_SECONDS,
    NOTCH_PROBE_GAP_SECONDS,
    NOTCH_DEFAULT_MAX,
    NOTCH_HARD_MAX,
    OVERLOAD_HOLD_SECONDS,
  };
}
