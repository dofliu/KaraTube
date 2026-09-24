// Web Audio API Audio Engine & Effects Processor
class AudioEngine {
  constructor() {
    this.ctx = null;
    this.audioInst = null;
    this.audioVoc = null;

    this.sourceInst = null;
    this.sourceVoc = null;

    this.gainInst = null;
    this.gainVoc = null;
    this.masterGain = null;

    // Mic & Reverb / Echo DSP
    this.micStream = null;
    this.micSource = null;
    this.micGain = null;
    this.reverbNode = null;
    this.delayNode = null;
    this.delayGain = null;
    this.micAnalyser = null;

    // 對唱模式的第二支麥克風。
    // 'off'     單麥（原行為）
    // 'device'  第二支麥克風是另一個輸入裝置（兩支 USB 麥克風）
    // 'channel' 同一個立體聲輸入裝置的左右聲道（兩支麥克風接一台音效介面，
    //           這是包廂真正的接法：L = A 麥、R = B 麥）
    this.duetMode = "off";
    this.duetStream = null;
    this.duetSource = null;
    this.channelSplitter = null;
    this.micAnalyserB = null;
    this.chainB = null;
    // 自動增益的節點與目前倍率。麥克風還沒開之前 setMicAutoGain 也可能被呼叫
    // （舞台端的迴圈不等麥克風權限），所以這裡先給合法初值。
    this.micAgcGain = null;
    this.micAgcLevel = 1.0;

    // 和聲（雙聲部）。移調在 AudioWorklet 裡做，所以要非同步載入模組，
    // 而且有可能根本不支援（AudioWorklet 需要 Chrome 66+／Safari 14.1+）。
    this.harmonyVoices = [];
    this.harmonyReady = false;
    this.harmonySupported = null;   // null = 還沒試過
    this._harmonyLoading = null;

    // 升降 Key（伴奏即時移調）。跟和聲一樣在 AudioWorklet 裡做，所以要非同步
    // 載入模組、也可能根本不支援。移調量記在這裡而不是只存在音訊圖上：
    // 音訊環境往往比第一次按下升降 Key 還晚建立（舞台要等使用者點一下才開音訊）。
    this.keyShift = 0;
    this.keyShiftNode = null;
    this.keyDryGain = null;
    this.keyWetGain = null;
    this.keyShiftActive = false;
    this.keyShiftReady = false;
    this.keyShiftSupported = null;   // null = 還沒試過
    this._keyShiftLoading = null;
    // 移調器那條路多出來的延遲（秒）。worklet 建好時會把實際值報過來，
    // 這個初值只是「還沒報到之前的合理估計」（90ms 視窗的一半）。
    this.keyShiftLatencySeconds = 0.045;

    // 情境背景用的音樂頻譜分析節點（延遲建立：沒開情境背景就不必多掛一個節點）
    this.musicAnalyser = null;

    this.isMicActive = false;
  }

  /**
   * 情境背景要看的音樂頻譜。
   *
   * 接在 normGain（伴奏＋導唱這條匯流排）上，刻意**不含麥克風與罐頭音效**：
   * 背景跟著人講話與掌聲抖動，看起來不是「有反應」而是「壞了」。
   * analyser 只旁聽、不往下接，所以掛上去不影響輸出。
   */
  getMusicAnalyser() {
    if (!this.ctx || !this.normGain) return null;
    if (!this.musicAnalyser) {
      this.musicAnalyser = this.ctx.createAnalyser();
      // 1024 點在 48kHz 下每個 bin 約 47Hz，分得出低頻鼓與人聲的差別就夠了；
      // 背景不需要更細的解析度，卻要為此每幀多算好幾倍的 FFT。
      this.musicAnalyser.fftSize = 1024;
      this.musicAnalyser.smoothingTimeConstant = 0.7;
      this.normGain.connect(this.musicAnalyser);
    }
    return this.musicAnalyser;
  }

  /**
   * 錄唱回放要錄的那一條串流（伴奏＋導唱＋麥克風＋效果＋罐頭音效）。
   *
   * 接在 mixBus 上，也就是**喇叭音量之前**：錄的是這首歌唱成什麼樣子，
   * 不是包廂當下開多大聲。
   *
   * MediaStreamDestination 只建一次並留著重複用。每首歌建一個新的會在
   * AudioContext 裡累積節點（Web Audio 的節點只要還有連線就不會被回收），
   * 唱一整晚之後 mixBus 身上會掛著幾十個沒人讀的目的節點，每一個都在跑重採樣。
   */
  getRecordingStream() {
    this.initContext();
    if (!this.mixBus) return null;
    if (!this.recordDest) {
      if (typeof this.ctx.createMediaStreamDestination !== "function") return null;
      this.recordDest = this.ctx.createMediaStreamDestination();
      this.mixBus.connect(this.recordDest);
    }
    return this.recordDest.stream;
  }

  initContext() {
    if (!this.ctx) {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      this.ctx = new AudioCtx();

      // Master output
      this.masterGain = this.ctx.createGain();
      this.masterGain.gain.value = 1.0;
      this.masterGain.connect(this.ctx.destination);

      // 全部聲音（伴奏＋導唱＋麥克風＋效果＋罐頭音效）先匯到這裡，再進 masterGain。
      // 錄唱回放錄的是這一點，而**不是** masterGain 之後：
      // masterGain 是喇叭音量，有人把喇叭轉小（接電話、勸酒）錄下來的就跟著變小，
      // 甚至靜音 —— 那一次就沒了。錄音要的是「這首歌唱成什麼樣子」，
      // 跟包廂當下開多大聲是兩件事。
      this.mixBus = this.ctx.createGain();
      this.mixBus.gain.value = 1.0;
      this.mixBus.connect(this.masterGain);
      this.recordDest = null;

      // 自動音量平衡（EBU R128）的每曲增益。
      // 只掛在伴奏＋人聲這條路徑上 —— 麥克風與罐頭音效直接接 mixBus，
      // 所以換一首歌調整音量平衡時，唱歌的人不會忽然覺得自己的聲音變大變小。
      this.normGain = this.ctx.createGain();
      this.normGain.gain.value = 1.0;
      this.normalizationDb = 0;

      // 升降 Key 的兩條路：直通與移調，用兩顆增益交叉淡接。
      //
      //   normGain ─┬─→ keyDryGain ──────────────→ mixBus     （原調）
      //             └─→ music-shifter → keyWetGain → mixBus   （升降 Key）
      //
      // 分成兩條而不是「永遠走移調器、移調量 0 時直通」，是因為移調器
      // 再怎麼乾淨都會多 45ms 的延遲，而原調是九成以上的時候都在的狀態 ——
      // 那 45ms 會一直躺在伴奏與 MV 畫面之間，只為了一個沒有人在用的功能。
      //
      // 位置在 normGain **之後**：伴奏與導唱人聲一起移調（切回原唱時不會
      // 忽然跳回原本的調），而麥克風與罐頭音效直接接 mixBus 完全不受影響 ——
      // 升 Key 是把音樂搬到你唱得到的地方，不是把你的聲音變成別人。
      this.keyDryGain = this.ctx.createGain();
      this.keyDryGain.gain.value = 1.0;
      this.keyDryGain.connect(this.mixBus);
      this.keyWetGain = this.ctx.createGain();
      this.keyWetGain.gain.value = 0.0;
      this.keyWetGain.connect(this.mixBus);
      this.normGain.connect(this.keyDryGain);

      // Instrumental & Vocal tracks gain
      this.gainInst = this.ctx.createGain();
      this.gainInst.gain.value = 1.0;
      this.gainInst.connect(this.normGain);

      this.gainVoc = this.ctx.createGain();
      this.gainVoc.gain.value = 0.0; // Default to accompaniment only
      this.gainVoc.connect(this.normGain);

      // 導唱自動 ducking 的倍率，串在人聲軌與它的音量之前。
      // 刻意用兩個節點而不是把倍率乘進 gainVoc：
      // 使用者拉的導唱音量與機器的自動退場是兩件事，混在同一個 gain 上，
      // 唱到一半自動退場後再去動滑桿，滑桿的刻度就會跟實際音量對不起來。
      this.guideDuckGain = this.ctx.createGain();
      this.guideDuckGain.gain.value = 1.0;
      this.guideDuckGain.connect(this.gainVoc);
      this.guideDuckLevel = 1.0;

      this._initReverb();

      // 音訊環境比「點歌台送來的升降 Key」晚建立是常態（舞台要等第一次點擊
      // 才能開音訊，而點歌台可能早就按過 +2 了）。所以那個值先記著，
      // 這裡補做一次 —— 不補的話，開場第一首會唱在錯的調上，
      // 而畫面上的「+2」還亮著。
      if (this.keyShift) this.setKeyShift(this.keyShift);
    }

    if (this.ctx.state === 'suspended') {
      this.ctx.resume();
    }
  }

  // 送進 destination 的音訊要多久才真的從喇叭發出來。
  // media.currentTime 回報的是「已排程」的位置，字幕若直接照它畫就會系統性地比歌聲早，
  // Windows WASAPI 常見 40~200ms，藍牙喇叭更可到 300ms。
  //
  // 升降 Key 打開時還要再加上移調器那半個視窗（約 45ms）：那條路是**多出來的**
  // 延遲，字幕與評分都照這個函式在補償。不加的話，升 Key 的那一刻整首歌的
  // 字幕會一起早 45ms —— 而使用者只會知道「升 Key 之後字幕怪怪的」。
  getOutputLatency() {
    if (!this.ctx) return 0;
    return this._deviceLatency() + this.keyShiftLatency();
  }

  _deviceLatency() {
    const out = this.ctx.outputLatency;      // Chrome / Firefox：含作業系統與裝置緩衝
    if (typeof out === "number" && out > 0 && out < 1.0) return out;
    const base = this.ctx.baseLatency;       // Safari 只有這個，且只算 Web Audio 內部緩衝
    if (typeof base === "number" && base > 0 && base < 1.0) return base * 2;
    return 0.05;
  }

  // --- 升降 Key（伴奏即時移調，music-shifter-worklet.js）---

  /** 移調器這條路多出來的延遲（秒）。沒在移調（或不支援）就是 0。 */
  keyShiftLatency() {
    return this.keyShiftActive ? this.keyShiftLatencySeconds : 0;
  }

  /**
   * 這台機器做不做得到即時移調。
   * @returns {boolean|null} null = 還沒試過（音訊環境還沒建、還沒有人按過升降 Key）
   */
  keyShiftAvailable() {
    return this.keyShiftSupported;
  }

  /**
   * 載入移調用的 AudioWorklet 並接進伴奏匯流排。
   *
   * 第一次真的要用（移調量不為 0）才載入：原調的人不必為了一個沒用到的功能
   * 多下載一支模組、多跑一條音訊路徑。重複呼叫安全。
   */
  async initKeyShift() {
    if (this.keyShiftReady) return true;
    if (this.keyShiftSupported === false) return false;
    if (this._keyShiftLoading) return this._keyShiftLoading;

    this._keyShiftLoading = (async () => {
      if (!this.ctx || !this.keyWetGain) return false;   // 還沒輪到，不是不支援
      if (!this.ctx.audioWorklet) {
        this.keyShiftSupported = false;
        return false;
      }
      try {
        await this.ctx.audioWorklet.addModule("/js/music-shifter-worklet.js");
        this.keyShiftNode = new AudioWorkletNode(this.ctx, "music-shifter", {
          numberOfInputs: 1,
          numberOfOutputs: 1,
          outputChannelCount: [2],
        });
      } catch (e) {
        console.warn("移調模組載入失敗，此瀏覽器不支援升降 Key:", e);
        this.keyShiftSupported = false;
        return false;
      }
      // 延遲長度由 worklet 那一端依實際取樣率決定，所以是它報過來的。
      this.keyShiftNode.port.onmessage = (e) => {
        if (e.data && e.data.type === "latency") {
          this.keyShiftLatencySeconds = Number(e.data.seconds) || 0;
        }
      };
      this.normGain.connect(this.keyShiftNode);
      this.keyShiftNode.connect(this.keyWetGain);
      this.keyShiftSupported = true;
      this.keyShiftReady = true;
      return true;
    })();

    const ok = await this._keyShiftLoading;
    this._keyShiftLoading = null;
    return ok;
  }

  /**
   * 升降 Key：把伴奏與導唱人聲移調幾個半音（速度不變）。
   *
   * 值先記下來再說 —— 音訊環境還沒建、worklet 還在載入的時候也可能被呼叫
   * （點歌台不等舞台準備好），那些情況都是「還沒輪到」，不是失敗。
   *
   * @returns {number} 實際採用的移調量（夾在 ±6，跟點歌台與後端同一個範圍）
   */
  setKeyShift(semitones) {
    const n = Math.max(-6, Math.min(6, Math.round(Number(semitones) || 0)));
    this.keyShift = n;
    if (!this.ctx || !this.keyWetGain) return n;

    if (n !== 0 && !this.keyShiftReady) {
      // 非同步載入，載好之後自己再走一次這個函式（此時 this.keyShift 可能
      // 已經被改成別的值了 —— 用最新的那個，不是這次進來的 n）。
      this.initKeyShift().then((ok) => { if (ok) this.setKeyShift(this.keyShift); });
      return n;
    }

    const node = this.keyShiftNode;
    const active = n !== 0 && this.keyShiftReady;
    const now = this.ctx.currentTime;

    if (node) {
      const param = node.parameters.get("shift");
      // 移調量變了不用排斜坡：worklet 裡的延遲量是連續的，改變的只是它
      // 前進的速度，所以直接設值不會有爆音（跟和聲同一個理由）。
      if (param) param.value = n;
      // 從原調切進移調（或反過來）時，移調器剛好是靜音的 —— 這時候清掉
      // 緩衝，免得它播出十分之一秒前那段「還沒移調」的聲音。
      if (active !== this.keyShiftActive && !this.keyShiftActive) {
        try { node.port.postMessage({ type: "reset" }); } catch (e) { /* 節點已收掉 */ }
      }
    }

    if (active !== this.keyShiftActive) {
      this.keyShiftActive = active;
      // 60ms 的交叉淡接。兩條路差的是 45ms 的延遲，交叉期間會聽到一瞬間的
      // 相位干涉 —— 但那一瞬間正是使用者自己按下升降 Key 的時候，
      // 而「按了鍵，聲音有反應」本來就是他要的。中間如果不淡接而是硬切，
      // 那就不是一瞬間的相位干涉，而是一聲爆音。
      this.keyWetGain.gain.setTargetAtTime(active ? 1 : 0, now, 0.06);
      this.keyDryGain.gain.setTargetAtTime(active ? 0 : 1, now, 0.06);
    }
    return n;
  }

  /** 換歌：清掉移調器裡上一首的殘留樣本（新的歌從靜音開始，清了不會有聲響）。 */
  resetKeyShift() {
    if (!this.keyShiftNode) return;
    try { this.keyShiftNode.port.postMessage({ type: "reset" }); } catch (e) { /* 節點已收掉 */ }
  }

  // Setup dual audio element sync
  setupTracks(audioInstEl, audioVocEl) {
    this.initContext();
    this.audioInst = audioInstEl;
    this.audioVoc = audioVocEl;

    if (!this.sourceInst) {
      this.sourceInst = this.ctx.createMediaElementSource(this.audioInst);
      this.sourceInst.connect(this.gainInst);
    }

    if (!this.sourceVoc) {
      this.sourceVoc = this.ctx.createMediaElementSource(this.audioVoc);
      this.sourceVoc.connect(this.guideDuckGain);
    }
  }

  /**
   * 導唱自動 ducking：套上這一幀的倍率（0~1）。
   *
   * 由 guide-ducker.js 算出來，這裡只負責送進音訊圖。
   * 每幀呼叫，所以用很短的時間常數（20ms）—— 夠平滑不會有 zipper noise，
   * 又不會在真的需要救援時再多壓一層延遲上去。
   */
  setGuideDuck(level) {
    const clamped = Math.max(0, Math.min(1, Number(level)));
    if (!Number.isFinite(clamped)) return this.guideDuckLevel;
    // 每一幀都被呼叫，值沒變就不要再排一次 —— 導唱關著時等於整條路徑不做事
    if (clamped === this.guideDuckLevel) return clamped;
    this.guideDuckLevel = clamped;
    if (this.guideDuckGain && this.ctx) {
      this.guideDuckGain.gain.setTargetAtTime(clamped, this.ctx.currentTime, 0.02);
    }
    return clamped;
  }

  setVocalVolume(volume) {
    if (this.gainVoc && this.ctx) {
      this.gainVoc.gain.setTargetAtTime(Math.max(0, Math.min(1, volume)), this.ctx.currentTime, 0.05);
    }
  }

  setMusicVolume(volume) {
    if (this.gainInst && this.ctx) {
      this.gainInst.gain.setTargetAtTime(Math.max(0, Math.min(1, volume)), this.ctx.currentTime, 0.05);
    }
  }

  /**
   * 自動音量平衡：套用這首歌的正規化增益（dB）。
   *
   * 伺服器已經用 EBU R128 量好每首歌的整合響度並算出增益，這裡只負責套用。
   * 夾在 ±12 dB：超出這個範圍通常是檔案本身有問題（近乎靜音的伴奏軌），
   * 硬拉只會把底噪一起放大。
   */
  setNormalizationDb(db) {
    const clamped = Math.max(-12, Math.min(12, Number(db) || 0));
    this.normalizationDb = clamped;
    if (this.normGain && this.ctx) {
      const linear = Math.pow(10, clamped / 20);
      // 0.08s 時間常數：換歌時聽不出切換動作，又快到前奏第一拍就位
      this.normGain.gain.setTargetAtTime(linear, this.ctx.currentTime, 0.08);
    }
    return clamped;
  }

  setMasterVolume(volume) {
    if (this.masterGain && this.ctx) {
      this.masterGain.gain.setTargetAtTime(Math.max(0, Math.min(1, volume)), this.ctx.currentTime, 0.05);
    }
  }

  // Generate algorithmic impulse response for KTV Hall Reverb
  _initReverb() {
    const rate = this.ctx.sampleRate;
    // 1.6 秒、時間常數 0.35 秒 ≈ RT60 約 2.4 秒的中型空間。
    // 原本是 2.0 秒 / 時間常數 0.5 秒（RT60 約 3.5 秒），等於教堂，唱起來字會糊在一起。
    const length = Math.floor(rate * 1.6);
    const impulse = this.ctx.createBuffer(2, length, rate);
    const left = impulse.getChannelData(0);
    const right = impulse.getChannelData(1);

    // 高頻要比低頻衰減得快（模擬空氣吸收）。
    // 原本是純白噪，整條殘響尾巴的高頻跟起始一樣多，
    // 人聲卷積上去會變成沙沙的金屬感 —— 這是「尖銳」的來源之一。
    let lpL = 0, lpR = 0, peak = 0;
    for (let i = 0; i < length; i++) {
      const t = i / length;
      const decay = Math.exp(-i / (rate * 0.35));
      const cutoff = 0.75 * (1 - t) + 0.05;   // 一階 IIR 係數，越後面越暗
      lpL += cutoff * ((Math.random() * 2 - 1) - lpL);
      lpR += cutoff * ((Math.random() * 2 - 1) - lpR);
      left[i] = lpL * decay;
      right[i] = lpR * decay;
      peak = Math.max(peak, Math.abs(left[i]), Math.abs(right[i]));
    }
    // 低通會讓振幅掉不少，正規化回來，否則殘響滑桿的刻度會失真
    if (peak > 0) {
      const norm = 1 / peak;
      for (let i = 0; i < length; i++) { left[i] *= norm; right[i] *= norm; }
    }

    this.reverbNode = this.ctx.createConvolver();
    this.reverbNode.buffer = impulse;

    this.reverbGain = this.ctx.createGain();
    this.reverbGain.gain.value = 0.0;   // 實際值由 setMicReverb 決定
    this.reverbNode.connect(this.reverbGain);
    this.reverbGain.connect(this.mixBus);

    // --- KTV 回音 (Echo) ---
    // 回授量與輸出音量必須是兩個獨立的節點。
    // 舊版兩件事共用同一個 gain，想調小聲就一定連重複次數一起變少，
    // 而且那個節點沒有任何 UI 接得到，等於固定全開關不掉。
    this.delayNode = this.ctx.createDelay(1.0);
    this.delayNode.delayTime.value = 0.28;

    // 回授路徑加低通：每繞一圈高頻衰減多一點，重複聽起來會往後退，
    // 而不是像原本那樣每一次都同樣尖銳刺耳。
    this.echoDamp = this.ctx.createBiquadFilter();
    this.echoDamp.type = "lowpass";
    this.echoDamp.frequency.value = 3200;

    this.echoFeedbackGain = this.ctx.createGain();
    this.echoFeedbackGain.gain.value = 0.25;   // 約 4 次可聽見的重複

    this.echoOutGain = this.ctx.createGain();
    this.echoOutGain.gain.value = 0.0;         // 實際值由 setMicEcho 決定

    this.delayNode.connect(this.echoDamp);
    this.echoDamp.connect(this.echoFeedbackGain);
    this.echoFeedbackGain.connect(this.delayNode);   // 回授迴路
    this.delayNode.connect(this.echoOutGain);
    this.echoOutGain.connect(this.mixBus);           // 輸出

    // 舊名稱保留，避免其他地方誤用時整個爆掉
    this.delayGain = this.echoOutGain;
  }

  /**
   * 開一條麥克風輸入串流。
   *
   * @param {string} deviceId 指定裝置（空值 = 系統預設）
   * @param {boolean} stereo  要不要立體聲（對唱的「同一裝置左右聲道」模式需要）
   */
  async _openMicStream(deviceId = "", stereo = false) {
    const audio = {
      echoCancellation: false,
      noiseSuppression: false,
      autoGainControl: false,
      latency: 0.01,
    };
    if (deviceId) audio.deviceId = { exact: deviceId };
    // ideal 而不是 exact：拿不到立體聲時要能退回單聲道並告訴使用者，
    // 而不是整個 getUserMedia 失敗（那樣連麥克風都開不起來）。
    if (stereo) audio.channelCount = { ideal: 2 };
    return navigator.mediaDevices.getUserMedia({ audio });
  }

  /**
   * 換掉 A 麥的來源串流（換裝置、切換左右聲道對唱都要）。
   *
   * 舊的 source 節點一定要先 disconnect：停掉串流只讓它變成靜音，
   * 節點本身還掛在音訊圖上（接著 analyser 與前級鏈）。
   * 一直不拆的話每換一次裝置就多一個殭屍節點，最後量到的電平是好幾條路徑的和。
   */
  _replaceMicSource(stream) {
    if (this.micSource) {
      try { this.micSource.disconnect(); } catch (e) { /* 還沒接過 */ }
    }
    if (this.micStream) this.micStream.getTracks().forEach((t) => t.stop());
    this.micStream = stream;
    this.micSource = this.ctx.createMediaStreamSource(stream);
  }

  /** 這條串流實際拿到幾個聲道。左右聲道對唱要靠它確認硬體真的給了立體聲。 */
  _streamChannelCount(stream) {
    const track = stream && stream.getAudioTracks ? stream.getAudioTracks()[0] : null;
    if (!track || typeof track.getSettings !== "function") return 1;
    const count = Number(track.getSettings().channelCount);
    return Number.isFinite(count) && count > 0 ? count : 1;
  }

  async startMicrophone() {
    this.initContext();
    if (this.isMicActive) return true;

    try {
      this.micStream = await this._openMicStream();

      this.micSource = this.ctx.createMediaStreamSource(this.micStream);
      this.micGain = this.ctx.createGain();
      this.micGain.gain.value = 1.0;

      // Pitch Analyser
      this.micAnalyser = this.ctx.createAnalyser();
      this.micAnalyser.fftSize = 2048;

      // 麥克風處理鏈。monitorGain 是「要不要從喇叭放出人聲」的總開關，
      // 放在效果送出之前，單人模式才能連殘響與回音一起靜音。
      this._buildMicChain();
      this._routeInputs();
      this.micLimiter.connect(this.micGain);

      this.micGain.connect(this.monitorGain);
      this.monitorGain.connect(this.mixBus); // Direct vocal
      this.monitorGain.connect(this.reverbNode); // Reverb send
      this.monitorGain.connect(this.delayNode);  // Echo send

      this.isMicActive = true;
      this.setSingMode(this.singMode || "solo");
      // 和聲的移調節點要接在麥克風鏈之後，所以只能等到這裡才建。
      // 不 await：載入 worklet 模組要一次網路往返，不該擋住麥克風開起來
      // （和聲還沒好之前 setHarmonyVoices 就當成沒這個功能，安靜地不作用）。
      this.initHarmony().catch(() => {});
      console.log("Microphone & KTV DSP started successfully.");
      return true;
    } catch (e) {
      console.warn("Microphone access not granted or unavailable:", e);
      return false;
    }
  }

  /**
   * 麥克風前級處理鏈。
   *
   * 這裡能做的事有物理上限：軟體只能把回授門檻往上推幾個 dB，
   * 真正決定成敗的是幾何（近距離收音、喇叭朝向、離開筆電機殼）。
   * 但這四段各自處理一種實際會發生的問題：
   *   highpass —— 砍掉近接效應與桌面震動傳來的低頻
   *   deEss    —— 壓 5.5kHz 以上，回授自激與齒音都集中在這帶
   *   agcGain  —— 自動增益：把不同人、不同距離的音量拉到差不多（mic-agc.js 算，這裡只套）
   *   limiter  —— 迴路增益短暫超過 1 時把它壓住，不讓它一路長大成嘯叫
   *
   * 順序很重要：自動增益一定要在 limiter **之前**。
   * 反過來的話，AGC 加上去的增益就沒有任何東西擋著，
   * 判斷失誤（突然的咳嗽、拍打麥克風）會直接變成削峰的爆音。
   *
   * 寫成工廠（建好一條並在內部接完 highpass → deEss → agcGain → limiter）
   * 是因為對唱模式要兩條一模一樣的鏈。兩邊各寫一次的話，
   * 哪天調了 A 的 de-esser 卻忘了 B，兩支麥克風的音色就會不一樣 ——
   * 而那種差異在包廂裡只會被講成「B 麥比較差」，沒人會想到是程式。
   */
  _createMicChain() {
    const highpass = this.ctx.createBiquadFilter();
    highpass.type = "highpass";
    highpass.frequency.value = 110;
    highpass.Q.value = 0.7;

    const deEss = this.ctx.createBiquadFilter();
    deEss.type = "highshelf";
    deEss.frequency.value = 5500;
    deEss.gain.value = -5;

    // 麥克風自動增益的倍率。刻意跟音量滑桿分開兩個節點：
    // 使用者拉的音量與機器的自動調整是兩件事，混在同一個 gain 上，
    // 自動調整動過之後滑桿的刻度就跟實際音量對不起來了。
    const agcGain = this.ctx.createGain();
    agcGain.gain.value = 1.0;

    const limiter = this.ctx.createDynamicsCompressor();
    limiter.threshold.value = -14;
    limiter.knee.value = 6;
    limiter.ratio.value = 12;
    limiter.attack.value = 0.003;
    limiter.release.value = 0.15;

    highpass.connect(deEss);
    deEss.connect(agcGain);
    agcGain.connect(limiter);

    return { highpass, deEss, agcGain, limiter, agcLevel: 1.0 };
  }

  _buildMicChain() {
    if (this.micHighpass) return;

    const chain = this._createMicChain();
    this.micHighpass = chain.highpass;
    this.micDeEss = chain.deEss;
    this.micAgcGain = chain.agcGain;
    this.micAgcLevel = 1.0;
    this.micLimiter = chain.limiter;

    this.monitorGain = this.ctx.createGain();
    this.monitorGain.gain.value = 0;   // 預設不外放，由 setSingMode 決定
  }

  /**
   * 把輸入訊號接到量測用的 analyser 與前級鏈上。
   *
   * 每次換裝置、開關對唱都要重新走一次 —— 這裡是唯一決定「哪個訊號算誰」的地方，
   * 分散在各處接線的話，切換模式時一定會留下沒斷掉的舊連線
   * （那個 bug 的症狀是「B 麥的分數跟 A 一模一樣」，很難查）。
   *
   * analyser 一律接在前級鏈**之前**：自動增益是前饋的，量的必須是原始電平；
   * 對唱的串音判定也一樣要比原始電平，否則兩邊的自動增益會把音量差抹平，
   * 「誰在唱」就永遠判不出來。
   */
  _routeInputs() {
    if (!this.micSource) return;
    try { this.micSource.disconnect(); } catch (e) { /* 還沒接過 */ }
    if (this.channelSplitter) {
      try { this.channelSplitter.disconnect(); } catch (e) { /* 還沒接過 */ }
      this.channelSplitter = null;
    }
    if (this.duetSource) {
      try { this.duetSource.disconnect(); } catch (e) { /* 還沒接過 */ }
    }

    if (this.duetMode === "channel" && this.chainB) {
      // 立體聲輸入：左聲道給 A、右聲道給 B
      this.channelSplitter = this.ctx.createChannelSplitter(2);
      this.micSource.connect(this.channelSplitter);
      this.channelSplitter.connect(this.micAnalyser, 0);
      this.channelSplitter.connect(this.micHighpass, 0);
      this.channelSplitter.connect(this.micAnalyserB, 1);
      this.channelSplitter.connect(this.chainB.highpass, 1);
      return;
    }

    this.micSource.connect(this.micAnalyser);
    this.micSource.connect(this.micHighpass);

    if (this.duetMode === "device" && this.duetSource && this.chainB) {
      this.duetSource.connect(this.micAnalyserB);
      this.duetSource.connect(this.chainB.highpass);
    }
  }

  /**
   * 演唱模式。
   *   solo  單人：人聲完全不進喇叭 —— 回授迴路被物理性切斷，不可能嘯叫。
   *   party 多人：人聲外放，需要外接喇叭才實用。
   * 兩種模式的音準評分都正常，因為 micAnalyser 是直接接在 micSource 上。
   */
  setSingMode(mode) {
    this.singMode = mode === "party" ? "party" : "solo";
    if (this.monitorGain && this.ctx) {
      const v = this.singMode === "party" ? 1.0 : 0.0;
      this.monitorGain.gain.setTargetAtTime(v, this.ctx.currentTime, 0.08);
    }
    return this.singMode;
  }

  // 高頻柔化。0 = 不處理，1 = -12dB @5.5kHz。尖銳與回授都吃這一刀。
  setMicTone(amount) {
    if (this.micDeEss && this.ctx) {
      const v = -12 * Math.max(0, Math.min(1, amount));
      this.micDeEss.gain.setTargetAtTime(v, this.ctx.currentTime, 0.05);
    }
  }

  setMicVolume(volume) {
    if (this.micGain && this.ctx) {
      this.micGain.gain.setTargetAtTime(volume, this.ctx.currentTime, 0.05);
    }
  }

  /**
   * 麥克風自動增益：套上這一幀的倍率。
   *
   * 由 mic-agc.js 算出來（它量的是 micAnalyser 上的原始訊號，在這個節點之前），
   * 這裡只負責送進音訊圖。時間常數 15ms：夠平滑不會有 zipper noise，
   * 又不會在削峰保護真的要降的時候再多壓一層延遲上去。
   */
  setMicAutoGain(level) {
    const clamped = Math.max(0.1, Math.min(8, Number(level)));
    if (!Number.isFinite(clamped)) return this.micAgcLevel;
    // 每一幀都被呼叫，值沒變就不要再排一次（容忍區內幾乎每幀都沒變）
    if (clamped === this.micAgcLevel) return clamped;
    this.micAgcLevel = clamped;
    if (this.micAgcGain && this.ctx) {
      this.micAgcGain.gain.setTargetAtTime(clamped, this.ctx.currentTime, 0.015);
    }
    return clamped;
  }

  // --- 對唱模式（第二支麥克風）---
  //
  // 第二支麥克風走一條與 A 完全相同的前級鏈，最後也接到 monitorGain ——
  // 所以「人聲外放」的總開關、殘響與回音送出兩支麥克風是共用的
  // （兩支麥克風的效果不一樣，聽起來會像兩個不同的房間在對唱）。
  //
  // 但量測（analyser）與自動增益是各自獨立的：換人唱不用重調音量這件事，
  // 對唱模式下反而更重要 —— 兩個人的音量與距離幾乎不可能一樣。

  /**
   * 開第二支麥克風。
   *
   * @param {object} source
   *   mode 'device' = 另一個輸入裝置（兩支 USB 麥克風）
   *        'channel' = 同一個立體聲裝置的左右聲道（兩支麥克風接一台音效介面）
   *   deviceId 'device' 模式要用哪個裝置；'channel' 模式指的是 A 麥那個裝置
   * @returns {Promise<{ok: boolean, reason?: string, mode?: string}>}
   *   失敗一定要說得出原因 —— 這個功能壞掉的樣子是「B 麥的分數跟 A 一樣」，
   *   沒有明確的錯誤訊息，現場只會以為是評分不準。
   */
  async startDuetMic(source = {}) {
    this.initContext();
    if (!this.isMicActive) return { ok: false, reason: "麥克風還沒開啟" };

    const mode = source.mode === "channel" ? "channel" : "device";
    const deviceId = source.deviceId || "";

    if (mode === "device" && !deviceId) {
      return { ok: false, reason: "請選第二支麥克風的裝置" };
    }

    if (!this.chainB) {
      this.chainB = this._createMicChain();
      this.micAnalyserB = this.ctx.createAnalyser();
      this.micAnalyserB.fftSize = 2048;
      this.micGainB = this.ctx.createGain();
      this.micGainB.gain.value = 1.0;
      this.chainB.limiter.connect(this.micGainB);
      this.micGainB.connect(this.monitorGain);
      this.micAgcLevelB = 1.0;
    }

    try {
      if (mode === "channel") {
        // A 麥那條串流本來是單聲道開的，要重開成立體聲才有右聲道可以分
        const stream = await this._openMicStream(deviceId, true);
        if (this._streamChannelCount(stream) < 2) {
          stream.getTracks().forEach((t) => t.stop());
          return {
            ok: false,
            reason: "這個裝置只給單聲道，無法用左右聲道分兩支麥克風（請改選兩個不同的輸入裝置）",
          };
        }
        this._stopDuetStream();
        this._replaceMicSource(stream);
      } else {
        const stream = await this._openMicStream(deviceId, false);
        this._stopDuetStream();
        this.duetStream = stream;
        this.duetSource = this.ctx.createMediaStreamSource(stream);
      }
    } catch (e) {
      console.warn("第二支麥克風開啟失敗:", e);
      return { ok: false, reason: "第二支麥克風開啟失敗（裝置被占用或權限不足）" };
    }

    this.duetMode = mode;
    this._routeInputs();
    return { ok: true, mode };
  }

  _stopDuetStream() {
    if (this.duetStream) {
      this.duetStream.getTracks().forEach((t) => t.stop());
      this.duetStream = null;
    }
    this.duetSource = null;
  }

  /**
   * 關掉第二支麥克風，回到單麥。
   *
   * 左右聲道模式要把 A 麥的串流重開成單聲道 —— 留著立體聲串流不會壞，
   * 但右聲道的訊號會一直混進 A 的量測裡（原本是 B 的聲音），
   * 等於關掉對唱之後 A 的分數還在被別人影響。
   */
  async stopDuetMic() {
    if (this.duetMode === "off") return true;
    const wasChannel = this.duetMode === "channel";
    this.duetMode = "off";
    this._stopDuetStream();
    if (this.micGainB && this.ctx) {
      this.micGainB.gain.setTargetAtTime(0, this.ctx.currentTime, 0.05);
    }

    if (wasChannel) {
      try {
        this._replaceMicSource(await this._openMicStream("", false));
      } catch (e) {
        console.warn("回復單聲道麥克風失敗，沿用現有串流:", e);
      }
    }
    this._routeInputs();
    if (this.micGainB && this.ctx) {
      // 重新接好之後才把音量放回來，避免切換的瞬間漏出一段未處理的訊號
      this.micGainB.gain.setTargetAtTime(1, this.ctx.currentTime, 0.05);
    }
    return true;
  }

  /** 第二支麥克風的音量滑桿。 */
  setMicVolumeB(volume) {
    if (this.micGainB && this.ctx) {
      this.micGainB.gain.setTargetAtTime(Math.max(0, Math.min(2, Number(volume) || 0)),
                                         this.ctx.currentTime, 0.05);
    }
  }

  /** 第二支麥克風的自動增益倍率（與 A 各自獨立，兩個人的距離不會一樣）。 */
  setMicAutoGainB(level) {
    const clamped = Math.max(0.1, Math.min(8, Number(level)));
    if (!Number.isFinite(clamped)) return this.micAgcLevelB;
    if (clamped === this.micAgcLevelB) return clamped;
    this.micAgcLevelB = clamped;
    if (this.chainB && this.ctx) {
      this.chainB.agcGain.gain.setTargetAtTime(clamped, this.ctx.currentTime, 0.015);
    }
    return clamped;
  }

  // --- 和聲（雙聲部）---
  //
  // 麥克風訊號複製兩份、各自移調（harmony-worklet.js），再疊回人聲那條路徑上。
  // 移多少由 harmony-planner.js 依調性與音階度數決定，這裡只負責送進音訊圖。
  //
  // 接點刻意選在 micGain 之後、monitorGain 之前：
  //   * micGain 之後 → 使用者的麥克風音量滑桿會一起帶動和聲（音量比例才不會跑掉）
  //   * monitorGain 之前 → 和聲跟主唱吃同一個「人聲外放」總開關，
  //     也一起吃殘響與回音送出。單人模式（人聲不進喇叭）時和聲自然也不會出現，
  //     這是刻意的：和聲從喇叭出來會被麥克風收回去再移調一次，
  //     那條路徑會一路往上疊成嘯叫，比單純的回授更難止。

  /**
   * 載入移調用的 AudioWorklet 並建立和聲聲部。
   *
   * 呼叫時機是麥克風開起來之後（需要 micGain 當來源）。重複呼叫安全。
   * @returns {Promise<boolean>} 這台機器支不支援和聲
   */
  async initHarmony(voiceCount = 2) {
    if (this.harmonyReady) return true;
    if (this.harmonySupported === false) return false;
    if (this._harmonyLoading) return this._harmonyLoading;

    this._harmonyLoading = (async () => {
      if (!this.ctx || !this.micGain) {
        // 音訊環境還沒建、麥克風還沒開，都只是「還沒輪到」，不是不支援。
        // （麥克風開起來時會再呼叫一次；判成不支援的話那次就不會再試了。）
        return false;
      }
      if (!this.ctx.audioWorklet) {
        this.harmonySupported = false;
        return false;
      }
      try {
        await this.ctx.audioWorklet.addModule("/js/harmony-worklet.js");
      } catch (e) {
        console.warn("和聲模組載入失敗，此瀏覽器不支援和聲:", e);
        this.harmonySupported = false;
        return false;
      }
      for (let i = 0; i < voiceCount; i++) {
        const node = new AudioWorkletNode(this.ctx, "harmony-shifter", {
          numberOfInputs: 1,
          numberOfOutputs: 1,
          outputChannelCount: [1],
        });
        const gain = this.ctx.createGain();
        gain.gain.value = 0;             // 預設不出聲，由 setHarmonyVoices 決定
        this.micGain.connect(node);
        node.connect(gain);
        gain.connect(this.monitorGain);
        this.harmonyVoices.push({ node, gain, level: 0, shift: 0 });
      }
      this.harmonySupported = true;
      this.harmonyReady = true;
      return true;
    })();

    const ok = await this._harmonyLoading;
    this._harmonyLoading = null;
    return ok;
  }

  /**
   * 套用這一幀的和聲聲部。
   *
   * @param {Array<{shift:number, gain:number}>} voices
   *   harmony-planner.js 算出來的聲部；空陣列 = 這一幀不要和聲。
   *   多出來的聲部（超過建立的節點數）忽略，少的那些淡出到 0。
   */
  setHarmonyVoices(voices) {
    if (!this.harmonyReady || !this.ctx) return false;
    const list = Array.isArray(voices) ? voices : [];
    const now = this.ctx.currentTime;

    this.harmonyVoices.forEach((voice, i) => {
      const plan = list[i];
      const target = plan ? Math.max(0, Math.min(0.85, Number(plan.gain) || 0)) : 0;

      if (plan) {
        const shift = Math.max(-24, Math.min(24, Number(plan.shift) || 0));
        // 移調量變了不用怕爆音：worklet 裡的延遲量是連續的，
        // 改變的只是它前進的速度。所以直接設值，不排斜坡。
        if (shift !== voice.shift) {
          voice.shift = shift;
          const param = voice.node.parameters.get("shift");
          if (param) param.value = shift;
        }
      }

      // 每一幀都被呼叫，值沒變就不要再排一次（沒和聲時等於整條路徑不做事）
      if (target === voice.level) return;
      voice.level = target;
      // 淡入 60ms、淡出 120ms：進得快一點才跟得上句子的開頭，
      // 出得慢一點才不會在字與字之間的氣口一直斷斷續續。
      voice.gain.gain.setTargetAtTime(target, now, target > 0 ? 0.06 : 0.12);
    });
    return true;
  }

  /** 換歌／重唱：清掉移調器裡的殘留樣本，免得上一首的尾音被疊進這一首。 */
  resetHarmony() {
    this.harmonyVoices.forEach((voice) => {
      voice.level = 0;
      if (this.ctx) voice.gain.gain.setTargetAtTime(0, this.ctx.currentTime, 0.05);
      try { voice.node.port.postMessage({ type: "reset" }); } catch (e) { /* 節點已收掉 */ }
    });
  }

  // --- 音訊裝置選擇 ---
  // 多人模式一定要外接喇叭：把輸出從筆電喇叭移走，
  // 才能斷掉「喇叭震動經機殼直接傳到內建麥克風」這條靠距離解決不了的路徑。

  async listDevices() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) {
      return { inputs: [], outputs: [], canSelectOutput: false };
    }
    const devices = await navigator.mediaDevices.enumerateDevices();
    const pick = (kind) => devices
      .filter(d => d.kind === kind)
      .map(d => ({ id: d.deviceId, label: d.label || "（需先允許麥克風才會顯示名稱）" }));
    return {
      inputs: pick("audioinput"),
      outputs: pick("audiooutput"),
      // setSinkId 在 AudioContext 上需要 Chrome 110+；Firefox/Safari 目前沒有
      canSelectOutput: !!(this.ctx && typeof this.ctx.setSinkId === "function")
    };
  }

  async setInputDevice(deviceId) {
    if (!this.isMicActive) return false;
    try {
      // 左右聲道對唱時新裝置也要開成立體聲，否則換完裝置 B 麥就沒訊號了
      const stream = await this._openMicStream(deviceId, this.duetMode === "channel");
      if (this.duetMode === "channel" && this._streamChannelCount(stream) < 2) {
        stream.getTracks().forEach((t) => t.stop());
        console.warn("新裝置不支援立體聲，左右聲道對唱無法沿用，沿用原裝置");
        return false;
      }
      // 換裝置要重接整條鏈，舊的 stream 必須停掉否則會繼續佔用麥克風
      this._replaceMicSource(stream);
      this._routeInputs();
      return true;
    } catch (e) {
      console.warn("切換麥克風失敗:", e);
      return false;
    }
  }

  async setOutputDevice(deviceId) {
    if (!this.ctx || typeof this.ctx.setSinkId !== "function") return false;
    try {
      await this.ctx.setSinkId(deviceId || "");
      return true;
    } catch (e) {
      console.warn("切換輸出裝置失敗:", e);
      return false;
    }
  }

  // 殘響（空間感）。0 = 完全乾聲。
  setMicReverb(amount) {
    if (this.reverbGain && this.ctx) {
      const v = Math.max(0, Math.min(1, amount)) * 0.5;
      this.reverbGain.gain.setTargetAtTime(v, this.ctx.currentTime, 0.05);
    }
  }

  // 回音音量。0 = 完全關掉，不會再有任何重複。
  setMicEcho(amount) {
    if (this.echoOutGain && this.ctx) {
      const v = Math.max(0, Math.min(1, amount)) * 0.6;
      this.echoOutGain.gain.setTargetAtTime(v, this.ctx.currentTime, 0.05);
    }
  }

  // 回音重複次數（回授量）。上限 0.6，再高會自激變成嘯叫。
  setMicEchoRepeat(amount) {
    if (this.echoFeedbackGain && this.ctx) {
      const v = Math.max(0, Math.min(0.6, Math.max(0, Math.min(1, amount)) * 0.6));
      this.echoFeedbackGain.gain.setTargetAtTime(v, this.ctx.currentTime, 0.05);
    }
  }

  // 回音間隔（毫秒）。KTV 常用 120~350ms。
  setMicEchoTime(ms) {
    if (this.delayNode && this.ctx) {
      const v = Math.max(0.05, Math.min(0.8, ms / 1000));
      this.delayNode.delayTime.setTargetAtTime(v, this.ctx.currentTime, 0.05);
    }
  }

  // Play Sound FX (Applause, Cheers, Boo)
  playSoundEffect(name) {
    this.initContext();
    const osc = this.ctx.createOscillator();
    const gain = this.ctx.createGain();

    if (name === "applause" || name === "cheer") {
      // Synthesize cheering / crowd applause noise
      const bufferSize = this.ctx.sampleRate * 2;
      const buffer = this.ctx.createBuffer(1, bufferSize, this.ctx.sampleRate);
      const data = buffer.getChannelData(0);
      for (let i = 0; i < bufferSize; i++) {
        data[i] = (Math.random() * 2 - 1) * Math.exp(-i / (bufferSize * 0.6));
      }
      const noise = this.ctx.createBufferSource();
      noise.buffer = buffer;
      
      const filter = this.ctx.createBiquadFilter();
      filter.type = "bandpass";
      filter.frequency.value = 1200;

      noise.connect(filter);
      filter.connect(gain);
      gain.gain.setValueAtTime(0.8, this.ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.01, this.ctx.currentTime + 2.0);
      gain.connect(this.mixBus);
      noise.start();
    } else if (name === "boo") {
      // Boo sound (low frequency descending groan)
      osc.type = "sawtooth";
      osc.frequency.setValueAtTime(140, this.ctx.currentTime);
      osc.frequency.linearRampToValueAtTime(70, this.ctx.currentTime + 1.5);
      gain.gain.setValueAtTime(0.6, this.ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.01, this.ctx.currentTime + 1.5);
      osc.connect(gain);
      gain.connect(this.mixBus);
      osc.start();
      osc.stop(this.ctx.currentTime + 1.5);
    }
  }
}

window.audioEngine = new AudioEngine();
