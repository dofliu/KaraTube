/**
 * 音域採集 (Vocal Range Collector)
 *
 * 後端 `backend/services/vocal_range.py` 負責「這個人的音域是什麼、這首歌要調幾個
 * Key」；這裡只負責一件事 —— **把唱出來的音高變成一張直方圖**，而且只收
 * 真的是這個人唱出來的那些幀。
 *
 * 音高不自己偵測：`pitch-engine.js` 為了評分本來就每一幀偵測一次，
 * 這裡吃它 `tick()` 回傳的那份判定。各偵測各的話同一幀會算出兩個音高，
 * 而兩邊不一致時現場完全查不出來（評分正常、音域莫名其妙）。
 *
 * ---
 *
 * **只收「唱住了」的音，不收滑過去的音。**
 *
 * 一個人從 C4 唱到 G4，中間那條滑音會經過五、六個半音格。全部照收的話，
 * 直方圖會被**轉音的路徑**填滿，而那些音他其實一秒都沒有停在上面。
 * 所以一格要連續穩住 `SUSTAIN_FRAMES` 幀（約 0.13 秒）才開始記 ——
 * 到那個門檻之前累積的幀數會一次補記進去（那些幀本來就是唱住的一部分，
 * 只是當下還不知道）。
 *
 * 「穩住」的判準是**整段的擺幅**（`SUSTAIN_SPAN`，1.5 個半音），不是「這一幀
 * 離平均多遠」。第一版寫成後者，而抖音當場就鑽過去了：擺幅 ±0.5 的抖音，
 * 第二幀離「平均」（此時只有第一幀，也就是抖音的波峰）整整 1.0 個半音 ——
 * 於是每半個週期就斷一次，一個唱了四秒的長音一幀都收不到。
 * 改成看擺幅之後，抖音整段留著（擺幅 1.0 < 1.5），而滑音照樣擋得住：
 * 滑過兩個半音的那一刻擺幅就超過門檻，run 從那裡重新起算。
 *
 * **喇叭裡的原唱也是一個人在唱歌。**
 *
 * 多人模式的導唱人聲從喇叭進到麥克風，音高偵測分不出那是誰 —— 照收的話，
 * 累積出來的會是**這台機器唱過的歌的音域**，而不是這位客人的。
 * 分得開的不是音高是**電平**：自己對著麥克風唱，RMS 比房間裡的回音大一個級距。
 * 所以有一道 `MIN_RMS` 的門檻，寧可少收。
 * 少收的代價很小（音域是跨很多首歌累積的），多收的代價是整份檔案都是錯的，
 * 而且錯得很像對的。
 *
 * **升降 Key 不用折回去。**
 *
 * 伴奏升了 2 個 Key，人就跟著唱高 2 個 —— 而麥克風收到的正是他**唱出來**的音。
 * 音域檔案要的就是那個音（那是他的聲音做得到的事），不是這首歌原調的音。
 * 折回原調反而會把「他升 Key 之後唱得上去」記成「他唱不上去」。
 *
 * 純資料邏輯：不碰 DOM、不發網路請求，所以能用 node --test 直接跑。
 */

// 一格要連續穩住幾幀才開始記。約 0.13 秒 @60fps —— 再短會收進轉音，
// 再長會漏掉快歌裡真正唱到的短音。
const SUSTAIN_FRAMES = 8;

// 一段「唱住的音」整段的擺幅上限（半音）。流行唱法的抖音約 ±0.25~0.5
// （擺幅 0.5~1.0），所以 1.5 收得下抖音；而滑音只要跨過兩個半音就會超過它。
const SUSTAIN_SPAN = 1.5;

// 麥克風電平低於這個值的幀不收（見上面「喇叭裡的原唱」）。
// 與 `mic-agc.js` 的靜音判定同一個量級，但這裡刻意再嚴一點：
// 自動增益收錯一幀只是音量抖一下，音域收錯一幀會留在檔案裡。
const MIN_RMS = 0.02;

// 直方圖的合法範圍（半音格），與 backend/services/vocal_range.py 的
// MIN_MIDI / MAX_MIDI 同值。兩邊對不上的話，前端送出去的兩端會被後端
// 安靜地丟掉 —— 畫面上的音域比檔案裡的寬，而且沒有任何訊息。
const RANGE_MIN_MIDI = 36;
const RANGE_MAX_MIDI = 84;

class VocalRangeCollector {
  constructor() {
    this.reset();
  }

  reset() {
    /** @type {Object<number, number>} 半音格 -> 幀數 */
    this.bins = {};
    this.total = 0;
    this._runSum = 0;
    this._runCount = 0;
    this._runMin = 0;
    this._runMax = 0;
    this._credited = false;
  }

  /**
   * 餵一幀。吃的是 `PitchEngine.tick()` 回傳的那份判定。
   *
   * @param {{sang?: boolean, credited?: boolean, userMidi?: number, rms?: number}} frame
   */
  push(frame) {
    const f = frame || {};
    const midi = Number(f.userMidi) || 0;
    const loud = Number(f.rms) || 0;
    // `credited === false` 是對唱模式判定的串音：那是**另一位**演唱者的聲音
    // 從這支麥克風漏進來，記進來等於把他的音域寫進我的檔案。
    const usable = f.sang !== false && f.credited !== false
      && midi >= RANGE_MIN_MIDI && midi <= RANGE_MAX_MIDI
      && loud >= MIN_RMS;

    if (!usable) {
      this._endRun();
      return;
    }

    if (this._runCount > 0) {
      const span = Math.max(this._runMax, midi) - Math.min(this._runMin, midi);
      if (span > SUSTAIN_SPAN) this._endRun();  // 換音或滑走了：從這一幀重新起算
    }

    if (this._runCount === 0) {
      this._runMin = midi;
      this._runMax = midi;
    } else {
      this._runMin = Math.min(this._runMin, midi);
      this._runMax = Math.max(this._runMax, midi);
    }
    this._runSum += midi;
    this._runCount += 1;

    if (this._credited) {
      this._add(midi, 1);
    } else if (this._runCount >= SUSTAIN_FRAMES) {
      // 到門檻的這一刻把整段補記進去：那 8 幀本來就是這個音唱住的一部分，
      // 只是在第 8 幀之前還分不出它是長音還是滑過去。
      this._credited = true;
      this._add(this._runSum / this._runCount, this._runCount);
    }
  }

  _endRun() {
    this._runSum = 0;
    this._runCount = 0;
    this._runMin = 0;
    this._runMax = 0;
    this._credited = false;
  }

  _add(midi, frames) {
    const slot = Math.round(midi);
    if (slot < RANGE_MIN_MIDI || slot > RANGE_MAX_MIDI) return;
    this.bins[slot] = (this.bins[slot] || 0) + frames;
    this.total += frames;
  }

  /** 這一次演唱收到幾幀（呼叫端用它決定值不值得送出）。 */
  get frames() {
    return this.total;
  }

  /**
   * 送給後端的形狀。收得太少就回 null —— 一首只唱了兩句的歌
   * （被切歌、走錯包廂按到播放）不該影響一個人的音域檔案，
   * 而且那一首在後端會多算一首「演唱次數」，把建檔門檻白白用掉。
   */
  payload(minFrames = SUSTAIN_FRAMES * 40) {
    if (this.total < minFrames) return null;
    return { bins: Object.assign({}, this.bins), frames: this.total };
  }
}

/**
 * 建議 Key 那一行要不要出現在畫面上。
 *
 * 後端已經把話寫好了（`headline` / `detail`），這裡只判斷**顯示與否**：
 *   * `no_demand` —— 這首歌沒得算，整行不出現（講「沒得算」對唱歌的人
 *     沒有任何用處，而且它會出現在每一首還沒處理完的歌上）。
 *   * `no_profile` —— 出現，但講的是「再唱幾首」。沉默的話使用者會
 *     以為這個功能不存在，而它其實正在等他多唱幾首。
 *   * 其餘 —— 出現。
 */
function shouldShowAdvice(advice) {
  const status = advice && advice.status;
  return !!status && status !== "no_demand";
}

/**
 * 那一行要不要附一顆「套用」鍵：只有真的要移調、而且移調量不是 0 的時候。
 * `out_of_range` 沒有鍵 —— 那一句話的下一步是換一首歌或用假音，不是按鍵。
 */
function adviceHasApply(advice) {
  return !!advice && advice.status === "advice" && Number(advice.shift) !== 0;
}

if (typeof window !== "undefined") {
  window.VocalRangeCollector = VocalRangeCollector;
  window.VocalRangeView = { shouldShowAdvice, adviceHasApply };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    VocalRangeCollector,
    shouldShowAdvice,
    adviceHasApply,
    SUSTAIN_FRAMES,
    SUSTAIN_SPAN,
    MIN_RMS,
    RANGE_MIN_MIDI,
    RANGE_MAX_MIDI,
  };
}
