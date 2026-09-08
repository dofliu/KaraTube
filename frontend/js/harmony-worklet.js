/**
 * 和聲移調器 (Harmony Pitch Shifter) — AudioWorklet
 *
 * 把麥克風訊號即時移調，讓 audio-effects.js 疊回主唱上變成第二個聲部。
 * 移多少由 harmony-planner.js 決定（音階上的度數），這裡只負責把它做出來。
 *
 * 為什麼要自己寫 DSP：Web Audio 沒有內建的移調節點。
 * `playbackRate` 只能對 buffer 用（麥克風是即時串流），
 * `detune` 只有 oscillator 有。所以移調必須自己算。
 *
 * 用的是經典的「可變延遲移調」（variable-delay / Doppler pitch shifter）：
 *
 *   output(t) = input(t - d(t))
 *
 * 這條式子的瞬時頻率比是 1 - d'(t)。也就是說延遲量只要以固定斜率變化，
 * 出來的音高就會被固定比例移動 —— 想升高八度（比例 2）就讓延遲以
 * 每秒 1 秒的速度縮短。但延遲不可能無限縮短，所以走到底就得跳回去，
 * 而那個跳點是波形的斷點，會爆一聲。
 *
 * 解法是兩條讀取頭：延遲量相差半個視窗，快跳到底的那一條先淡出、
 * 另一條淡入接手。兩條的增益永遠加起來等於 1，
 * 所以不會有「和聲音量隨移調量起伏」的問題。
 *
 * 交叉淡接刻意做得很短（只佔一輪的 30%，其餘時間單獨用一條讀取頭）。
 * 兩條同時出聲的期間，同一個聲音會以相差 depth/2 的兩個延遲相加 ——
 * 那就是一個梳型濾波器，某些頻率會互相抵消（實測 330Hz 掉了 27%）。
 * 把交叉區間縮短就等於把梳型效應限制在一輪的一小段裡：
 * 同樣的 330Hz 只掉不到 8%，主觀上從「聲音悶掉」變成聽不出來。
 * 拿掉交叉淡接不是選項 —— 那個跳點會變成每秒好幾次的爆音。
 *
 * 代價是移調越多、跳回去越頻繁，會有輕微的顆粒感（granular artifact）。
 * 這對和聲來說完全可以接受 —— 它本來就是效果音，
 * 真人和聲也不會跟主唱一模一樣。三度（比例 1.19）約每秒跳 4 次，
 * 每次都被窗形藏住，實際聽起來就是一個聲音略帶合成感的第二人。
 *
 * 檔案結構刻意分成兩層：
 *   HarmonyShifterCore     純陣列運算，不依賴 Web Audio → 可以用 node --test 驗證
 *   HarmonyShifterProcessor  AudioWorkletProcessor 外殼，只在瀏覽器裡註冊
 * 移調正確與否是這個功能唯一重要的事（差一個半音就是走音），
 * 所以核心一定要能在 CI 裡跑數值測試。
 */

// 延遲視窗深度（秒）。這個值是三方拉鋸的結果：
//   太短（< 20ms）→ 跳回去太頻繁，顆粒感變成明顯的震音
//   太長（> 80ms）→ 和聲比主唱慢半拍，聽起來像回音而不是和聲
// 45ms 的平均延遲是 22ms，剛好落在「像第二個人站在旁邊」的範圍。
const WINDOW_SECONDS = 0.045;

// 交叉淡接的半寬（佔一輪的比例）。兩條讀取頭同時出聲的時間 = 這個值的兩倍。
// 0.15 → 30% 的時間在交叉，70% 的時間只有一條讀取頭（完全沒有梳型效應）。
// 再縮短會讓淡接太陡而聽得到接縫；再放寬梳型效應就開始明顯。
const CROSSFADE = 0.15;

class HarmonyShifterCore {
  /**
   * @param {number} rate    取樣率（Hz）
   * @param {number} windowSeconds 延遲視窗深度，預設 WINDOW_SECONDS
   */
  constructor(rate, windowSeconds = WINDOW_SECONDS) {
    this.rate = rate > 0 ? rate : 48000;
    this.depth = Math.max(64, Math.floor(this.rate * windowSeconds));

    // 環形緩衝取 2 的次方，索引就能用位元遮罩取模。
    // JS 的位元運算走 int32 二補數，負索引 & mask 直接得到正確的環繞位置。
    let size = 1;
    while (size < this.depth + 4) size *= 2;
    this.size = size;
    this.mask = size - 1;
    this.buffer = new Float32Array(size);
    this.writeIdx = 0;

    // 延遲量（samples）從視窗中央起步：移調量為 0 時就是固定延遲，
    // 兩條讀取頭的窗形一條是 1 一條是 0，等於單純的延遲直通。
    this.delay = this.depth / 2;
  }

  /**
   * 處理一個區塊。
   *
   * @param {Float32Array} input   輸入樣本
   * @param {Float32Array} output  輸出樣本（可與 input 同長度的另一個陣列）
   * @param {number} semitones     要移幾個半音（正 = 升高）
   */
  process(input, output, semitones) {
    const n = output.length;
    const ratio = Math.pow(2, (Number(semitones) || 0) / 12);
    // d'(t) = 1 - ratio：升高音高 → 延遲縮短 → 讀取頭追上寫入頭
    const rate = 1 - ratio;
    const depth = this.depth;
    const buf = this.buffer;
    const mask = this.mask;

    for (let i = 0; i < n; i++) {
      buf[this.writeIdx & mask] = input ? input[i] : 0;
      this.writeIdx++;

      let d = this.delay + rate;
      // 走出視窗就跳回另一端。跳點上窗形是 0，所以聽不到這個斷點。
      if (d < 0) d += depth;
      else if (d > depth) d -= depth;
      this.delay = d;

      // 主讀取頭走到多遠（0 或 1 都是它的跳點，0.5 是離跳點最遠的地方）
      const u = d / depth;
      // 到跳點的環狀距離（0 ~ 0.5）。這個距離決定它能出多少聲：
      // 貼著跳點時交給另一條讀取頭，離跳點遠時自己獨唱（沒有梳型效應）。
      const s = u < 0.5 ? u : 1 - u;
      const wA = s >= CROSSFADE ? 1 : 0.5 - 0.5 * Math.cos(Math.PI * s / CROSSFADE);
      const wB = 1 - wA;

      // 另一條讀取頭相差半個視窗 —— 主讀取頭在跳點時，它正好在最安全的位置
      const half = depth / 2;
      const dB = d + half > depth ? d - half : d + half;

      output[i] = wB > 0
        ? wA * this._read(d) + wB * this._read(dB)
        : this._read(d);   // 七成的時間走這條：只有一條讀取頭，訊號完全乾淨
    }
  }

  /** 從環形緩衝讀出「delay 個樣本之前」的值，含線性內插（延遲量是小數）。 */
  _read(delaySamples) {
    const pos = this.writeIdx - 1 - delaySamples;
    const i0 = Math.floor(pos);
    const frac = pos - i0;
    const s0 = this.buffer[i0 & this.mask];
    const s1 = this.buffer[(i0 + 1) & this.mask];
    return s0 + (s1 - s0) * frac;
  }

  /** 換人唱／換歌：清掉緩衝，避免上一首的尾音被移調後疊進這一首。 */
  reset() {
    this.buffer.fill(0);
    this.writeIdx = 0;
    this.delay = this.depth / 2;
  }
}

// --- AudioWorklet 外殼 ---
// 只在瀏覽器的 worklet 範圍註冊；node 載入這個檔案做測試時這一段不會執行。
if (typeof registerProcessor === "function" && typeof AudioWorkletProcessor === "function") {
  class HarmonyShifterProcessor extends AudioWorkletProcessor {
    static get parameterDescriptors() {
      return [{
        name: "shift",
        defaultValue: 0,
        minValue: -24,
        maxValue: 24,
        // k-rate：一個區塊（128 樣本 ≈ 2.7ms）換一次移調量就夠了，
        // 和聲的移調量是「一個音符換一次」，不需要逐樣本自動化。
        automationRate: "k-rate",
      }];
    }

    constructor() {
      super();
      // sampleRate 是 worklet 範圍的全域變數
      this.core = new HarmonyShifterCore(sampleRate);
      this.port.onmessage = (e) => {
        if (e.data && e.data.type === "reset") this.core.reset();
      };
    }

    process(inputs, outputs, parameters) {
      const input = inputs[0] && inputs[0][0];
      const outChannels = outputs[0];
      if (!outChannels || !outChannels.length) return true;

      const out = outChannels[0];
      if (!input) {
        out.fill(0);
      } else {
        this.core.process(input, out, parameters.shift[0]);
      }
      // 單聲道算一次，其餘聲道複製 —— 和聲不需要立體聲展開
      for (let ch = 1; ch < outChannels.length; ch++) outChannels[ch].set(out);
      return true;
    }
  }

  registerProcessor("harmony-shifter", HarmonyShifterProcessor);
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { HarmonyShifterCore, WINDOW_SECONDS };
}
