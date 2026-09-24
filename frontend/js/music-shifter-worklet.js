/**
 * 伴奏移調器 (Music Pitch Shifter) — AudioWorklet
 *
 * 升降 Key 的那一半實作：把**伴奏與導唱人聲**這條匯流排即時移調，
 * 而且**不能改變速度**。點歌台按下「+2」之後，包廂裡的人聽到的必須是
 * 同一首歌、同樣的長度、同樣的歌詞時間點，只是整首高兩個半音。
 *
 * ## 為什麼不能用 playbackRate
 *
 * `audio.playbackRate = 2^(n/12)` 配上 `preservesPitch = false` 兩行就移調了，
 * 但它同時把速度乘上同一個比例：+6 個半音 = 快 41%。四分鐘的歌變成兩分五十秒，
 * 而歌詞、導唱音符、段落評分、錄音**全部**活在 `audio.currentTime` 上 ——
 * 那條時間軸自己跑掉的話，這個系統裡對時間有意見的每一個模組都要跟著歪。
 * 反過來（先變速再用時間伸縮補回來）則會讓 `currentTime` 與真正聽到的聲音
 * 差一個隨移調量變動的量，那是更難查的同一個病。
 *
 * 所以這裡的做法是：**播放速度一律 1.0**，時間軸完全不動，
 * 移調在 Web Audio 圖上自己算。整個系統其餘的部分不需要知道有這回事。
 *
 * ## 演算法
 *
 * 跟麥克風和聲用的是同一條式子（見 `harmony-worklet.js` 的長註解）：
 * 可變延遲移調，`output(t) = input(t - d(t))`，瞬時頻率比 = 1 - d'(t)，
 * 兩條相差半個視窗的讀取頭輪流出聲以藏住延遲走到底時的跳點。
 *
 * 但它**不是**和聲那一支的複製品，有三個地方為了「這是音樂不是人聲」而不同：
 *
 * 1. **視窗長度 90ms（和聲是 45ms）**。跳點的頻率 = |比例 - 1| ÷ 視窗長度，
 *    視窗加倍就等於接縫減半（+2 個半音時每秒 1.4 次而不是 2.7 次）。
 *    和聲不能這樣做 —— 它疊在主唱身上，延遲超過 30ms 就從「第二個人」
 *    變成「回音」；伴奏沒有這個限制，因為它旁邊沒有一個同時響著的原版可以比。
 *    代價是多 45ms 的延遲，那個延遲**有人在收**（見下面「延遲要有人認領」）。
 *
 * 2. **立體聲，而且兩個聲道共用同一條讀取頭軌跡**。兩個聲道各跑一份獨立的
 *    移調器是最容易寫錯的做法：兩邊的延遲量會因為浮點誤差慢慢分家，
 *    而左右聲道之間幾十微秒的時間差正是人耳判斷方位的依據 ——
 *    聽起來會像整個舞台在左右飄。軌跡算一次、兩個聲道讀同一個延遲量，
 *    立體聲像就跟原曲一模一樣。
 *
 * 3. **移調量 0 時是逐樣本一模一樣的直通**。延遲量停在視窗正中央不動，
 *    窗形固定在「只有一條讀取頭」那一側，讀的又是整數延遲 ——
 *    沒有內插、沒有交叉淡接、沒有任何運算誤差。原調（也就是九成以上的時候）
 *    聽到的必須是原本的檔案，這件事要能用測試釘住，不能只是「聽起來差不多」。
 *
 * ## 延遲要有人認領
 *
 * 移調一開，這條路徑就比原本多了半個視窗（45ms）的延遲。
 * 字幕與評分都照 `AudioContext` 的輸出延遲在補償，所以 `audio-effects.js`
 * 會把這 45ms 加進 `getOutputLatency()` —— 不加的話，升 Key 的那一刻
 * 全部的字幕會系統性地早 45ms，而使用者只會覺得「升 Key 之後字幕怪怪的」。
 *
 * ## 分層
 *
 *   MusicShifterCore       純陣列運算，不依賴 Web Audio → 可以用 node --test 驗證
 *   MusicShifterProcessor  AudioWorkletProcessor 外殼，只在瀏覽器裡註冊
 *
 * 跟和聲同一個理由：移調差一個半音就是整首歌走音，而那件事必須在 CI 裡
 * 量得出來，不能靠包廂裡的耳朵。
 */

// 延遲視窗深度（秒）。90ms → 平均延遲 45ms。
// 太短（< 60ms）接縫太頻繁，音樂會有可聽見的顫抖；
// 太長（> 140ms）瞬態被抹開，鼓點聽起來會糊掉，而且延遲開始咬到嘴型。
const MUSIC_WINDOW_SECONDS = 0.09;

// 交叉淡接的半寬（佔一輪的比例）。兩條讀取頭同時出聲的時間 = 這個值的兩倍。
// 跟和聲用同一個值：0.15 → 七成的時間只有一條讀取頭在出聲（完全沒有梳型效應），
// 再縮短就聽得到接縫。
const MUSIC_CROSSFADE = 0.15;

class MusicShifterCore {
  /**
   * @param {number} rate     取樣率（Hz）
   * @param {number} channels 聲道數（伴奏是 2）
   * @param {number} windowSeconds 延遲視窗深度，預設 MUSIC_WINDOW_SECONDS
   */
  constructor(rate, channels = 2, windowSeconds = MUSIC_WINDOW_SECONDS) {
    this.rate = rate > 0 ? rate : 48000;
    this.channels = Math.max(1, Math.floor(channels) || 1);
    // 深度取偶數：移調量 0 時延遲是 depth/2，那個值必須是整數樣本，
    // 直通才會是逐樣本相等而不是「內插出來的幾乎相等」。
    let depth = Math.max(128, Math.floor(this.rate * windowSeconds));
    if (depth % 2 !== 0) depth += 1;
    this.depth = depth;

    // 環形緩衝取 2 的次方，索引就能用位元遮罩取模。
    let size = 1;
    while (size < this.depth + 4) size *= 2;
    this.size = size;
    this.mask = size - 1;
    this.buffers = [];
    for (let ch = 0; ch < this.channels; ch++) {
      this.buffers.push(new Float32Array(size));
    }
    this.writeIdx = 0;
    this.delay = this.depth / 2;
  }

  /** 這條路徑多出來的延遲（秒）。`audio-effects.js` 要把它加進輸出延遲補償。 */
  get latencySeconds() {
    return (this.depth / 2) / this.rate;
  }

  /**
   * 處理一個區塊。
   *
   * @param {Array<Float32Array|null>} inputs  每個聲道的輸入（null = 這個聲道沒訊號）
   * @param {Array<Float32Array>} outputs      每個聲道的輸出
   * @param {number} semitones                 要移幾個半音（正 = 升高）
   */
  process(inputs, outputs, semitones) {
    if (!outputs || !outputs.length) return;
    const n = outputs[0].length;
    const chs = Math.min(this.channels, outputs.length);
    const ratio = Math.pow(2, (Number(semitones) || 0) / 12);
    // d'(t) = 1 - ratio：升高音高 → 延遲縮短 → 讀取頭追上寫入頭
    const step = 1 - ratio;
    const depth = this.depth;
    const half = depth / 2;
    const mask = this.mask;

    for (let i = 0; i < n; i++) {
      const w = this.writeIdx & mask;
      for (let ch = 0; ch < chs; ch++) {
        // 輸入聲道比輸出少（單聲道的伴奏檔）就複製第一個聲道 ——
        // 直接當成靜音的話，那首歌會只有一邊喇叭有聲音，
        // 而且只有升降 Key 打開時才會這樣（最難聯想的一種壞法）。
        const src = inputs && (inputs[ch] || inputs[0]);
        this.buffers[ch][w] = src ? src[i] : 0;
      }
      this.writeIdx++;

      let d = this.delay + step;
      // 走出視窗就跳回另一端。跳點上窗形是 0，所以聽不到這個斷點。
      if (d < 0) d += depth;
      else if (d > depth) d -= depth;
      this.delay = d;

      // 到跳點的環狀距離（0 ~ 0.5）。貼著跳點時交給另一條讀取頭，
      // 離跳點遠時自己獨唱（沒有梳型效應）。
      const u = d / depth;
      const s = u < 0.5 ? u : 1 - u;
      const wA = s >= MUSIC_CROSSFADE ? 1 : 0.5 - 0.5 * Math.cos(Math.PI * s / MUSIC_CROSSFADE);

      if (wA >= 1) {
        // 這一段路只有一條讀取頭：移調量 0 時永遠走這裡，而且 d 是整數，
        // `_read` 的內插係數為 0 —— 輸出與輸入逐樣本相等。
        for (let ch = 0; ch < chs; ch++) outputs[ch][i] = this._read(ch, d);
      } else {
        const wB = 1 - wA;
        // 另一條讀取頭相差半個視窗 —— 主讀取頭在跳點時，它正好在最安全的位置。
        // 兩個聲道**共用**這兩個延遲量，立體聲像才不會左右飄。
        const dB = d + half > depth ? d - half : d + half;
        for (let ch = 0; ch < chs; ch++) {
          outputs[ch][i] = wA * this._read(ch, d) + wB * this._read(ch, dB);
        }
      }
    }

    // 輸出聲道比核心多（單聲道來源接上立體聲輸出）：把算好的第一個聲道複製過去，
    // 不然多出來的那一邊會是靜音，聽起來像一邊喇叭壞了。
    for (let ch = chs; ch < outputs.length; ch++) outputs[ch].set(outputs[0]);
  }

  /** 從環形緩衝讀出「delay 個樣本之前」的值，含線性內插（延遲量可能是小數）。 */
  _read(ch, delaySamples) {
    const pos = this.writeIdx - 1 - delaySamples;
    const i0 = Math.floor(pos);
    const frac = pos - i0;
    const buf = this.buffers[ch];
    const s0 = buf[i0 & this.mask];
    if (frac === 0) return s0;
    const s1 = buf[(i0 + 1) & this.mask];
    return s0 + (s1 - s0) * frac;
  }

  /** 換歌：清掉緩衝與軌跡，免得上一首的尾音被移調後疊進這一首的第一秒。 */
  reset() {
    for (const buf of this.buffers) buf.fill(0);
    this.writeIdx = 0;
    this.delay = this.depth / 2;
  }
}

// --- AudioWorklet 外殼 ---
// 只在瀏覽器的 worklet 範圍註冊；node 載入這個檔案做測試時這一段不會執行。
if (typeof registerProcessor === "function" && typeof AudioWorkletProcessor === "function") {
  class MusicShifterProcessor extends AudioWorkletProcessor {
    static get parameterDescriptors() {
      return [{
        name: "shift",
        defaultValue: 0,
        // 系統的升降 Key 是 ±6，這裡放寬到 ±12 —— 夾限是點歌台那一端的事，
        // DSP 不該是第二個會偷偷改變使用者要求的地方。
        minValue: -12,
        maxValue: 12,
        // k-rate：一個區塊（128 樣本 ≈ 2.7ms）換一次就夠了。
        // 升降 Key 是「一首歌按幾次」的尺度，不需要逐樣本自動化。
        automationRate: "k-rate",
      }];
    }

    constructor(options) {
      super();
      const channels = (options && options.outputChannelCount && options.outputChannelCount[0]) || 2;
      // sampleRate 是 worklet 範圍的全域變數
      this.core = new MusicShifterCore(sampleRate, channels);
      // 建好之後第一件事就是告訴主執行緒「這條路多長」——
      // 字幕補償要用它，而主執行緒算不出來（depth 是這裡依取樣率決定的）。
      this.port.postMessage({ type: "latency", seconds: this.core.latencySeconds });
      this.port.onmessage = (e) => {
        if (e.data && e.data.type === "reset") this.core.reset();
      };
    }

    process(inputs, outputs, parameters) {
      const inChannels = inputs[0] || [];
      const outChannels = outputs[0];
      if (!outChannels || !outChannels.length) return true;
      // 沒有輸入時照樣要跑：緩衝裡還有上一刻的聲音，直接回 true 不寫輸出
      // 會讓那段殘響硬生生被切掉。餵 0 進去讓它自然走完。
      const shift = parameters && parameters.shift && parameters.shift.length
        ? parameters.shift[0] : 0;
      this.core.process(inChannels, outChannels, shift);
      return true;
    }
  }

  registerProcessor("music-shifter", MusicShifterProcessor);
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { MusicShifterCore, MUSIC_WINDOW_SECONDS, MUSIC_CROSSFADE };
}
