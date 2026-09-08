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

    this.isMicActive = false;
  }

  initContext() {
    if (!this.ctx) {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      this.ctx = new AudioCtx();

      // Master output
      this.masterGain = this.ctx.createGain();
      this.masterGain.gain.value = 1.0;
      this.masterGain.connect(this.ctx.destination);

      // 自動音量平衡（EBU R128）的每曲增益。
      // 只掛在伴奏＋人聲這條路徑上 —— 麥克風與罐頭音效直接接 masterGain，
      // 所以換一首歌調整音量平衡時，唱歌的人不會忽然覺得自己的聲音變大變小。
      this.normGain = this.ctx.createGain();
      this.normGain.gain.value = 1.0;
      this.normGain.connect(this.masterGain);
      this.normalizationDb = 0;

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
    }

    if (this.ctx.state === 'suspended') {
      this.ctx.resume();
    }
  }

  // 送進 destination 的音訊要多久才真的從喇叭發出來。
  // media.currentTime 回報的是「已排程」的位置，字幕若直接照它畫就會系統性地比歌聲早，
  // Windows WASAPI 常見 40~200ms，藍牙喇叭更可到 300ms。
  getOutputLatency() {
    if (!this.ctx) return 0;
    const out = this.ctx.outputLatency;      // Chrome / Firefox：含作業系統與裝置緩衝
    if (typeof out === "number" && out > 0 && out < 1.0) return out;
    const base = this.ctx.baseLatency;       // Safari 只有這個，且只算 Web Audio 內部緩衝
    if (typeof base === "number" && base > 0 && base < 1.0) return base * 2;
    return 0.05;
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
    this.reverbGain.connect(this.masterGain);

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
    this.echoOutGain.connect(this.masterGain);       // 輸出

    // 舊名稱保留，避免其他地方誤用時整個爆掉
    this.delayGain = this.echoOutGain;
  }

  async startMicrophone() {
    this.initContext();
    if (this.isMicActive) return true;

    try {
      this.micStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false,
          latency: 0.01
        }
      });

      this.micSource = this.ctx.createMediaStreamSource(this.micStream);
      this.micGain = this.ctx.createGain();
      this.micGain.gain.value = 1.0;

      // Pitch Analyser
      this.micAnalyser = this.ctx.createAnalyser();
      this.micAnalyser.fftSize = 2048;
      this.micSource.connect(this.micAnalyser);

      // 麥克風處理鏈。monitorGain 是「要不要從喇叭放出人聲」的總開關，
      // 放在效果送出之前，單人模式才能連殘響與回音一起靜音。
      this._buildMicChain();
      this.micSource.connect(this.micHighpass);
      this.micHighpass.connect(this.micDeEss);
      this.micDeEss.connect(this.micAgcGain);
      this.micAgcGain.connect(this.micLimiter);
      this.micLimiter.connect(this.micGain);

      this.micGain.connect(this.monitorGain);
      this.monitorGain.connect(this.masterGain); // Direct vocal
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
   */
  _buildMicChain() {
    if (this.micHighpass) return;

    this.micHighpass = this.ctx.createBiquadFilter();
    this.micHighpass.type = "highpass";
    this.micHighpass.frequency.value = 110;
    this.micHighpass.Q.value = 0.7;

    this.micDeEss = this.ctx.createBiquadFilter();
    this.micDeEss.type = "highshelf";
    this.micDeEss.frequency.value = 5500;
    this.micDeEss.gain.value = -5;

    // 麥克風自動增益的倍率。刻意跟 micGain（使用者的麥克風音量滑桿）分開兩個節點：
    // 使用者拉的音量與機器的自動調整是兩件事，混在同一個 gain 上，
    // 自動調整動過之後滑桿的刻度就跟實際音量對不起來了。
    this.micAgcGain = this.ctx.createGain();
    this.micAgcGain.gain.value = 1.0;
    this.micAgcLevel = 1.0;

    this.micLimiter = this.ctx.createDynamicsCompressor();
    this.micLimiter.threshold.value = -14;
    this.micLimiter.knee.value = 6;
    this.micLimiter.ratio.value = 12;
    this.micLimiter.attack.value = 0.003;
    this.micLimiter.release.value = 0.15;

    this.monitorGain = this.ctx.createGain();
    this.monitorGain.gain.value = 0;   // 預設不外放，由 setSingMode 決定
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
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          deviceId: deviceId ? { exact: deviceId } : undefined,
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false,
          latency: 0.01
        }
      });
      // 換裝置要重接整條鏈，舊的 stream 必須停掉否則會繼續佔用麥克風
      if (this.micSource) this.micSource.disconnect();
      if (this.micStream) this.micStream.getTracks().forEach(t => t.stop());

      this.micStream = stream;
      this.micSource = this.ctx.createMediaStreamSource(stream);
      this.micSource.connect(this.micAnalyser);
      this.micSource.connect(this.micHighpass);
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
      gain.connect(this.masterGain);
      noise.start();
    } else if (name === "boo") {
      // Boo sound (low frequency descending groan)
      osc.type = "sawtooth";
      osc.frequency.setValueAtTime(140, this.ctx.currentTime);
      osc.frequency.linearRampToValueAtTime(70, this.ctx.currentTime + 1.5);
      gain.gain.setValueAtTime(0.6, this.ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.01, this.ctx.currentTime + 1.5);
      osc.connect(gain);
      gain.connect(this.masterGain);
      osc.start();
      osc.stop(this.ctx.currentTime + 1.5);
    }
  }
}

window.audioEngine = new AudioEngine();
