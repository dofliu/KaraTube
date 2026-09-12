// KaraTube Fullscreen Stage Player Orchestrator

/**
 * 媒體時鐘補間器
 *
 * HTMLMediaElement.currentTime 只在解碼區塊邊界更新（實務上每 20~100ms 才動一次），
 * 直接拿去畫 60fps 的逐字走字，會看到字一格一格跳而不是平滑推進 ——
 * 即使時間軸本身是準的，主觀上也像沒對準。
 * 這裡在兩次更新之間用牆上時鐘外插，並在媒體時間真的跳動時重新錨定。
 */
class MediaClock {
  constructor(el) {
    this.el = el;
    this.reset();
  }

  reset() {
    this.lastRaw = -1;
    this.anchorMedia = 0;
    this.anchorWall = 0;
    this.lastReturned = -1;
  }

  now() {
    const raw = this.el.currentTime || 0;
    const wall = performance.now() / 1000;

    if (raw !== this.lastRaw) {
      this.lastRaw = raw;
      this.anchorMedia = raw;
      this.anchorWall = wall;
    }

    if (this.el.paused) {
      this.lastReturned = this.anchorMedia;
      return this.anchorMedia;
    }

    const rate = this.el.playbackRate || 1;
    // 外插上限 250ms：媒體元素卡住時時鐘不能自己跑掉
    let t = Math.min(this.anchorMedia + (wall - this.anchorWall) * rate,
                     this.anchorMedia + 0.25);
    // 外插可能短暫超前實際媒體時間，重新錨定時不讓字幕倒退（誤差會自己收斂）
    if (t < this.lastReturned) t = this.lastReturned;
    this.lastReturned = t;
    return t;
  }

  seekedTo(t) {
    this.lastRaw = t;
    this.anchorMedia = t;
    this.anchorWall = performance.now() / 1000;
    this.lastReturned = t;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const videoBg = document.getElementById("videoBg");
  const audioInst = document.getElementById("audioInst");
  const audioVoc = document.getElementById("audioVoc");

  const titleEl = document.getElementById("stageTitle");
  const artistEl = document.getElementById("stageArtist");
  const scoreValueEl = document.getElementById("scoreValue");
  const comboValueEl = document.getElementById("comboValue");
  const nextSongToast = document.getElementById("nextSongToast");
  const syncToast = document.getElementById("syncToast");
  const subtitlesContainer = document.getElementById("subtitlesContainer");
  const pitchCanvas = document.getElementById("pitchCanvas");
  const practiceBadge = document.getElementById("practiceBadge");
  const practiceBadgeText = document.getElementById("practiceBadgeText");
  const guideDuckBadge = document.getElementById("guideDuckBadge");
  const guideDuckBadgeText = document.getElementById("guideDuckBadgeText");
  const audioPromptOverlay = document.getElementById("audioPromptOverlay");
  const audioSetupBtn = document.getElementById("audioSetupBtn");
  const audioSetupPanel = document.getElementById("audioSetupPanel");
  const closeAudioSetup = document.getElementById("closeAudioSetup");
  const modeSoloBtn = document.getElementById("modeSoloBtn");
  const modePartyBtn = document.getElementById("modePartyBtn");
  const modeHint = document.getElementById("modeHint");
  const inputDeviceSelect = document.getElementById("inputDeviceSelect");
  const outputDeviceSelect = document.getElementById("outputDeviceSelect");
  const outputHint = document.getElementById("outputHint");
  const micMeterFill = document.getElementById("micMeterFill");
  const micMeterText = document.getElementById("micMeterText");
  const micAgcBadge = document.getElementById("micAgcBadge");
  const micAgcBadgeText = document.getElementById("micAgcBadgeText");
  const harmonyBadge = document.getElementById("harmonyBadge");
  const harmonyBadgeText = document.getElementById("harmonyBadgeText");
  const duetBoard = document.getElementById("duetBoard");
  const duetRowA = document.getElementById("duetRowA");
  const duetRowB = document.getElementById("duetRowB");
  const duetSourceSelect = document.getElementById("duetSourceSelect");
  const duetSourceHint = document.getElementById("duetSourceHint");
  const micMeterFillB = document.getElementById("micMeterFillB");
  const micMeterTextB = document.getElementById("micMeterTextB");
  const micMeterRowB = document.getElementById("micMeterRowB");
  const ambientCanvas = document.getElementById("ambientCanvas");
  const ambientArt = document.getElementById("ambientArt");

  // Initializing Engines
  const karaokeRenderer = new KaraokeRenderer(subtitlesContainer);
  const pitchEngine = new PitchEngine(pitchCanvas, scoreValueEl, comboValueEl);
  // 對唱模式的第二位演唱者：同一套評分邏輯，但不自己畫圖也不寫主計分板
  // （軌跡疊在同一張畫布上，分數寫進對唱計分板）。
  const pitchEngineB = new PitchEngine(null, null, null, { label: "B" });
  const clock = new MediaClock(audioInst);
  // 導唱音量自動 ducking：唱穩了導唱自己退到背景，唱不下去它馬上回來。
  // 參數由設定頁決定，這裡先放預設值，收到 SETTINGS_UPDATE 再覆寫。
  const guideDucker = new GuideDucker({ enabled: true, depth: 0.6 });
  // 麥克風自動增益：把不同人、不同距離的音量拉到差不多，換人唱不用重調滑桿。
  // 參數同樣由設定頁決定；加成上限還會再依演唱模式收緊（多人模式離回授更近）。
  const micAgc = new MicAutoGain({ enabled: true, targetDb: -18 });
  // 和聲（雙聲部）：跟著旋律在音階上疊三度／五度／低八度。
  // 開關與風格是共享控制參數（點歌台可改），這裡先照預設值建。
  const harmony = new HarmonyPlanner({ enabled: false, style: "third", level: 0.5 });
  // 對唱模式：兩支麥克風分別評分，並負責回答「這一幀該算誰的」（串音判定）。
  const duet = new DuetScorer({ enabled: false });
  // 第二支麥克風的自動增益。跟 A 各自獨立 —— 兩個人的音量與距離不會一樣，
  // 共用一組增益的話等於用同一把尺量兩個人，音量差反而被放大。
  const micAgcB = new MicAutoGain({ enabled: true, targetDb: -18 });
  // 情境背景：沒抓到 MV（或抓到的其實是一張靜態圖）時的動態視覺。
  // 參數由設定頁決定，這裡先放預設值，收到 SETTINGS_UPDATE 再覆寫。
  const ambientStage = new AmbientStage(ambientCanvas, {
    artEl: ambientArt,
    videoEl: videoBg,
    director: { mode: "auto", theme: "auto", brightness: 0.6 },
  });

  const OFFSET_STORAGE_KEY = "karatube_lyric_offset_ms";
  const PITCH_STORAGE_KEY = "karatube_show_pitch";
  const INPUT_DEV_KEY = "karatube_input_device";
  const OUTPUT_DEV_KEY = "karatube_output_device";
  // 第二支麥克風的來源是「這台機器怎麼接的」（哪個裝置 / 左右聲道），
  // 不是包廂的共享設定，所以記在本機而不是共享狀態。
  const DUET_SOURCE_KEY = "karatube_duet_source";

  // 對唱模式第二位演唱者的音高軌跡顏色（A 是桃紅色，B 用青綠色分辨）
  const DUET_B_COLOR = "#7cf6a0";

  let currentSongId = null;
  let currentSongMeta = null;
  let isPlaying = false;
  let lastTimeBroadcast = 0;
  let isAudioUnlocked = false;

  // 字幕微調（毫秒，正值 = 字幕延後）。這台機器的喇叭延遲是固定的，
  // 所以記在 localStorage，下次開機直接沿用。
  let lyricOffsetMs = 0;
  try {
    const saved = parseInt(localStorage.getItem(OFFSET_STORAGE_KEY), 10);
    if (!Number.isNaN(saved)) lyricOffsetMs = Math.max(-2000, Math.min(2000, saved));
  } catch (e) { /* 無痕模式沒有 localStorage，忽略 */ }

  let showPitch = true;
  try {
    const savedPitch = localStorage.getItem(PITCH_STORAGE_KEY);
    if (savedPitch !== null) showPitch = savedPitch === "1";
  } catch (e) { /* 無痕模式沒有 localStorage，忽略 */ }
  document.body.classList.toggle("hide-pitch", !showPitch);

  // 片頭卡與結算畫面的秒數／開關可在點歌台的系統設定頁調整。
  // 這裡先放商用機的慣用值，連上 WebSocket 收到 SETTINGS_UPDATE 後覆寫。
  // （宣告放在最上面：SETTINGS_UPDATE 的處理器在下方註冊，不能落在暫時死區裡。）
  let introCardMs = 8000;
  let introCardEnabled = true;
  let settlementMs = 9000;
  let settlementEnabled = true;

  // 使用者設定的導唱音量（0 = 純伴奏）。自動 ducking 只在導唱真的有開的時候才有意義，
  // 所以要記住這個基準值來決定要不要跑那一段。
  let guideBaseVolume = 0;
  let lastGuideFrameMs = 0;
  let lastGuideBadgeMs = 0;

  // 麥克風自動增益的計時與 UI 更新節流。
  // 這一條迴圈在「還沒開始唱」的時候也要跑（設定面板的試音音量表），
  // 所以時間戳跟評分心跳分開記。
  let lastMicFrameMs = 0;
  let lastMicUiMs = 0;
  let micMeterTimer = null;

  // 和聲的計時。和聲只在演唱中有意義，所以跟評分心跳同一條迴圈。
  // 徽章文字記著上一次寫進去的內容，值沒變就不碰 DOM
  //（60fps 直接寫 textContent 會讓瀏覽器每幀重排一次版面）。
  let lastHarmonyFrameMs = 0;
  let lastHarmonyBadgeText = "";

  // --- 對唱模式的狀態 ---
  // duetEnabled 是共享狀態（點歌台按下去所有裝置同步），
  // duetActive 是「這台機器的第二支麥克風真的開起來了」——
  // 兩者必須分開：按鈕按下去但第二支麥克風開不起來（沒選裝置、裝置被占用）
  // 是很常見的情況，混成一個變數的話畫面會說在對唱，實際上只有一個人在計分。
  let duetEnabled = false;
  let duetActive = false;
  let duetNameA = "";
  let duetNameB = "";
  let duetSource = { mode: "device", deviceId: "" };
  try {
    const saved = JSON.parse(localStorage.getItem(DUET_SOURCE_KEY) || "null");
    if (saved && typeof saved === "object") {
      duetSource = {
        mode: saved.mode === "channel" ? "channel" : "device",
        deviceId: String(saved.deviceId || ""),
      };
    }
  } catch (e) { /* 沒存過或無痕模式，用預設值 */ }
  // 上一幀兩支麥克風偵測到的音高。串音判定要用它 ——
  // 串音是同一個聲音的複製品（音高一樣），音高明顯不同才代表真的有兩個人在唱。
  let lastMidiA = 0;
  let lastMidiB = 0;
  let lastDuetFrameMs = 0;
  let lastDuetBoardMs = 0;
  let lastMicFrameMsB = 0;

  let outputLatency = 0.05;
  let syncToastTimer = null;
  let lastVocResync = 0;
  let lastVideoResync = 0;

  // 練唱模式（A-B 區段循環）。區間是共享狀態，任何一台裝置設好，這裡就照著跳。
  let loopEnabled = false;
  let loopStart = null;
  let loopEnd = null;
  let lastLoopJump = 0;

  // Initialize Web Audio Engine
  window.audioEngine.setupTracks(audioInst, audioVoc);

  // Setup AudioContext & Mic on user click
  async function unlockAudio() {
    if (isAudioUnlocked) return;
    window.audioEngine.initContext();
    const micOk = await window.audioEngine.startMicrophone();
    if (micOk && window.audioEngine.micAnalyser) {
      pitchEngine.setAnalyser(window.audioEngine.micAnalyser);
      // 麥克風剛開起來才有電平可量，這裡歸零 dt 的起點，避免第一幀算出好幾秒
      resetMicAgc();
    }
    // 情境背景要跟著音樂動，而 AudioContext 只有在使用者互動後才建得起來
    const musicAnalyser = window.audioEngine.getMusicAnalyser();
    if (musicAnalyser) {
      ambientStage.setAnalyser(musicAnalyser, window.audioEngine.ctx.sampleRate);
    }
    isAudioUnlocked = true;
    outputLatency = window.audioEngine.getOutputLatency();
    console.log(`[KaraTube] 輸出延遲 ${(outputLatency * 1000).toFixed(0)}ms，字幕微調 ${lyricOffsetMs}ms`);
    if (audioPromptOverlay) {
      audioPromptOverlay.classList.add("hidden");
    }
    // 把本機記住的微調值推回共享狀態，讓點歌台的滑桿顯示一致
    window.api.send("CONTROL", { data: { lyric_offset_ms: lyricOffsetMs, show_pitch: showPitch } });
    await refreshAudioDevices();
    // 解鎖前點歌台就已經打開對唱的話，現在才輪得到第二支麥克風
    if (duetEnabled && !duetActive) await startDuetAudio();
    if (currentSongId) {
      playMedia();
    }
  }

  if (audioPromptOverlay) {
    audioPromptOverlay.addEventListener("click", unlockAudio);
  }
  document.body.addEventListener("click", unlockAudio);

  // Connect WebSocket
  window.api.initWebSocket();

  window.api.on("STATE_UPDATE", (msg) => {
    handleStateUpdate(msg.data);
  });

  window.api.on("SOUND_EFFECT", (msg) => {
    const effect = msg.effect;
    window.audioEngine.playSoundEffect(effect);
    pitchEngine.spawnToastFX(effect === "applause" ? "👏 掌聲鼓勵！" : (effect === "cheer" ? "🎉 尖叫歡呼！" : "💥 喝采！"));
  });

  window.api.on("CONTROL_COMMAND", (msg) => {
    if (msg.command === "RESTART") {
      restartCurrentSong();
    } else if (msg.command === "SEEK") {
      seekMedia(msg.position || 0);
    }
  });

  // --- 系統設定 ---
  // 片頭卡、結算畫面、自動音量平衡的參數都在點歌台的設定頁，改了立刻生效。
  window.api.on("SETTINGS_UPDATE", (msg) => {
    const s = msg.data || {};
    if (s.intro_card_enabled !== undefined) introCardEnabled = !!s.intro_card_enabled;
    if (s.intro_card_seconds !== undefined) introCardMs = Math.round(s.intro_card_seconds * 1000);
    if (s.settlement_enabled !== undefined) settlementEnabled = !!s.settlement_enabled;
    if (s.settlement_seconds !== undefined) settlementMs = Math.round(s.settlement_seconds * 1000);
    if (s.guide_duck_enabled !== undefined || s.guide_duck_depth !== undefined) {
      guideDucker.configure({ enabled: s.guide_duck_enabled, depth: s.guide_duck_depth });
      applyGuideDuck();
    }
    if (s.mic_agc_enabled !== undefined || s.mic_agc_target_db !== undefined) {
      micAgc.configure({ enabled: s.mic_agc_enabled, targetDb: s.mic_agc_target_db });
      // 關掉的當下就要放掉增益，不能等到下一幀（下一幀可能是暫停中，永遠不會來）
      window.audioEngine.setMicAutoGain(micAgc.enabled ? micAgc.level : 1.0);
      // 第二支麥克風吃同一組設定（兩支麥克風的自動增益政策不該不一樣）
      micAgcB.configure({ enabled: s.mic_agc_enabled, targetDb: s.mic_agc_target_db });
      window.audioEngine.setMicAutoGainB(micAgcB.enabled ? micAgcB.level : 1.0);
      renderMicMeter();
    }
    // 情境背景：模式／主題／亮度，改完當下就要看得到（不能等下一首）
    if (s.ambient_bg_mode !== undefined || s.ambient_bg_theme !== undefined ||
        s.ambient_bg_brightness !== undefined) {
      ambientStage.configure({
        mode: s.ambient_bg_mode,
        theme: s.ambient_bg_theme,
        brightness: s.ambient_bg_brightness,
      });
    }
    // 串音判定門檻：房間越小、喇叭越大聲，串音越嚴重，門檻就要調高
    if (s.duet_crosstalk_margin_db !== undefined) {
      duet.configure({ marginDb: s.duet_crosstalk_margin_db });
    }
    // 響度目標或開關改了，正在唱的這首要立刻跟上，不用等下一首
    if (currentSongId) applyLoudness(currentSongId);
  });

  // --- 導唱音量自動 ducking ---
  // 每一幀把評分心跳的判定餵給 GuideDucker，拿回導唱該用的倍率送進音訊圖。
  function updateGuideDuck(frame, nowMs) {
    // 導唱本來就關著（純伴奏）就不用算：這時候 ducking 沒有任何可退的東西，
    // 而且徽章亮起來只會讓人以為機器把導唱吃掉了。
    if (guideBaseVolume <= 0.01) {
      if (guideDucker.level !== 1.0) guideDucker.reset();
      // 下次導唱被打開時要從新的一幀開始算 dt，不能沿用純伴奏那段的時間戳
      lastGuideFrameMs = 0;
      applyGuideDuck();
      updateGuideDuckBadge(nowMs, false);
      return;
    }
    const dt = lastGuideFrameMs ? (nowMs - lastGuideFrameMs) / 1000 : 0;
    lastGuideFrameMs = nowMs;
    guideDucker.update(dt, frame);
    applyGuideDuck();
    updateGuideDuckBadge(nowMs, true);
  }

  function applyGuideDuck() {
    window.audioEngine.setGuideDuck(guideDucker.enabled ? guideDucker.level : 1.0);
  }

  /**
   * 舞台徽章：導唱退場時亮出來，並顯示現在剩多少。
   *
   * 沒有這個提示的話，導唱變小聲會被當成「機器出問題了」——
   * 商用機同樣會在畫面角落標示導唱狀態。文字每 250ms 才更新一次，
   * 60fps 直接寫 textContent 會讓瀏覽器每幀重排一次版面。
   */
  function updateGuideDuckBadge(nowMs, active) {
    if (!guideDuckBadge) return;
    const show = active && guideDucker.isDucking();
    if (!show) {
      if (guideDuckBadge.style.display !== "none") guideDuckBadge.style.display = "none";
      return;
    }
    guideDuckBadge.style.display = "flex";
    if (nowMs - lastGuideBadgeMs > 250) {
      lastGuideBadgeMs = nowMs;
      guideDuckBadgeText.textContent = `導唱自動淡出 ${Math.round(guideDucker.level * 100)}%`;
    }
  }

  // --- 麥克風自動增益 ---
  /**
   * 餵一幀麥克風電平給 AGC，把算出來的倍率送進音訊圖，順便更新音量表。
   *
   * 兩個地方會呼叫：演唱中的渲染迴圈（用評分心跳量到的 rms，不重複抓波形），
   * 以及設定面板開著但沒在唱的時候的試音迴圈。dt 由呼叫時間算，
   * 所以兩邊交替呼叫也不會讓時間常數失真。
   *
   * @param {number} rms   麥克風原始訊號的 RMS（0~1）
   * @param {number} nowMs performance.now()
   */
  function updateMicAgc(rms, nowMs) {
    const dt = lastMicFrameMs ? (nowMs - lastMicFrameMs) / 1000 : 0;
    lastMicFrameMs = nowMs;
    if (dt > 0) {
      micAgc.update(dt, { rms });
      window.audioEngine.setMicAutoGain(micAgc.enabled ? micAgc.level : 1.0);
    }
    // 60fps 直接寫 textContent 會讓瀏覽器每幀重排一次版面，10fps 已經夠即時
    if (nowMs - lastMicUiMs > 100) {
      lastMicUiMs = nowMs;
      renderMicMeter();
    }
  }

  /** 音量表與自動增益讀數（設定面板）＋舞台角落的自動增益徽章。 */
  function renderMicMeter() {
    const gainDb = micAgc.gainDb;
    const signed = `${gainDb >= 0 ? "+" : ""}${gainDb.toFixed(1)} dB`;

    if (micMeterFill) {
      micMeterFill.style.width = `${Math.round(micAgc.meterLevel() * 100)}%`;
      // 太小聲（表頭不到兩成）與快削峰（超過九成）都要一眼看得出來，
      // 因為這兩種情況軟體都幫不了 —— 前者要拿近一點，後者要拿遠一點。
      micMeterFill.dataset.zone = micAgc.meterLevel() < 0.2
        ? "low" : (micAgc.meterLevel() > 0.9 ? "hot" : "ok");
    }
    if (micMeterText) {
      micMeterText.textContent = micAgc.enabled
        ? `自動增益 ${signed}`
        : "自動增益：關閉";
    }

    if (micAgcBadge) {
      const show = micAgc.isActive();
      micAgcBadge.style.display = show ? "flex" : "none";
      if (show) micAgcBadgeText.textContent = `麥克風自動增益 ${signed}`;
    }

    // 第二支麥克風的音量表。對唱模式最常見的現場問題是「B 麥根本沒進訊號」
    // （選錯裝置、介面右聲道沒插），有一條自己的表頭三秒就看得出來。
    if (micMeterRowB) micMeterRowB.style.display = duetActive ? "block" : "none";
    if (duetActive && micMeterFillB) {
      const level = micAgcB.meterLevel();
      micMeterFillB.style.width = `${Math.round(level * 100)}%`;
      micMeterFillB.dataset.zone = level < 0.2 ? "low" : (level > 0.9 ? "hot" : "ok");
      if (micMeterTextB) {
        const gainDbB = micAgcB.gainDb;
        micMeterTextB.textContent = micAgcB.enabled
          ? `${singerName("b")}　自動增益 ${gainDbB >= 0 ? "+" : ""}${gainDbB.toFixed(1)} dB`
          : `${singerName("b")}　自動增益：關閉`;
      }
    }
  }

  /**
   * 設定面板開著、但沒有在唱的時候的試音迴圈。
   *
   * 為什麼需要：渲染迴圈在暫停時就 return 了，音量表會凍住。
   * 但「先對著麥克風講一句話看表頭有沒有動」正是這個面板最有用的時候
   * —— 麥克風沒插好、選錯裝置、靜音鍵沒開，在這裡三秒就看出來。
   */
  function startMicMeterLoop() {
    if (micMeterTimer) return;
    micMeterTimer = setInterval(() => {
      if (!audioInst.paused) return;   // 演唱中由渲染迴圈負責，不要兩邊都餵
      const now = performance.now();
      updateMicAgc(pitchEngine.measureRms(), now);
      // 對唱模式下 B 麥也要能試音 —— 開唱前確認兩支都有訊號正是這個面板的用途
      if (duetActive) updateMicAgcB(pitchEngineB.measureRms(), now);
    }, 66);
  }

  function stopMicMeterLoop() {
    if (!micMeterTimer) return;
    clearInterval(micMeterTimer);
    micMeterTimer = null;
  }

  /** 換麥克風：學到的增益是「上一支麥克風的靈敏度」，不能沿用。 */
  function resetMicAgc() {
    micAgc.reset();
    lastMicFrameMs = 0;
    window.audioEngine.setMicAutoGain(1.0);
    micAgcB.reset();
    lastMicFrameMsB = 0;
    window.audioEngine.setMicAutoGainB(1.0);
    renderMicMeter();
  }

  // --- 和聲（雙聲部）---
  /**
   * 餵一幀給和聲規劃，把各聲部的移調量與音量送進音訊圖。
   *
   * 吃的是同一份評分心跳判定（frame.noteMidi / frame.sang），
   * 不重新偵測一次音高 —— 兩邊算出不同結果的話會非常難查。
   */
  function updateHarmony(frame, nowMs) {
    if (!harmony.enabled) {
      // 關掉的當下要立刻收掉聲音，不能等下一幀（下一幀可能是暫停中，永遠不會來）
      if (lastHarmonyFrameMs !== 0) {
        lastHarmonyFrameMs = 0;
        harmony.reset();
        window.audioEngine.setHarmonyVoices([]);
      }
      updateHarmonyBadge();
      return;
    }
    const dt = lastHarmonyFrameMs ? (nowMs - lastHarmonyFrameMs) / 1000 : 0;
    lastHarmonyFrameMs = nowMs;
    const plan = harmony.update(dt, frame);
    window.audioEngine.setHarmonyVoices(plan.voices);
    updateHarmonyBadge();
  }

  /**
   * 舞台徽章：和聲開著時常駐，顯示風格與判到的調性。
   *
   * 調性寫在畫面上是刻意的：和聲聽起來不對的時候，
   * 「機器把這首歌判成 A 小調」是唯一有用的線索（判錯就是和聲錯的原因）。
   */
  function updateHarmonyBadge() {
    if (!harmonyBadge) return;
    const label = harmony.enabled ? harmony.describe() : null;
    if (!label) {
      if (harmonyBadge.style.display !== "none") harmonyBadge.style.display = "none";
      lastHarmonyBadgeText = "";
      return;
    }
    harmonyBadge.style.display = "flex";
    // 沒在出聲時講清楚原因：和聲要有人唱才會疊上去，
    // 不講的話「按了和聲卻沒聲音」會被當成故障（其實只是還沒開口）。
    const text = harmony.active ? label : `${label} · 等你開口`;
    if (text !== lastHarmonyBadgeText) {
      lastHarmonyBadgeText = text;
      harmonyBadgeText.textContent = text;
    }
  }

  /** 換歌／重唱：調性要重估，移調器裡上一首的殘留樣本也要清掉。 */
  function resetHarmony(notes) {
    if (notes !== undefined) {
      const key = harmony.setNotes(notes);
      if (harmony.enabled) {
        console.log(key
          ? `[KaraTube] 和聲調性：${key.name}（相關 ${key.confidence}，取樣 ${key.seconds}s）`
          : "[KaraTube] 和聲：判不出調性，退成低八度疊唱");
      }
    } else {
      harmony.reset();
    }
    lastHarmonyFrameMs = 0;
    window.audioEngine.resetHarmony();
    updateHarmonyBadge();
  }

  // --- 對唱模式（兩支麥克風分別評分）---

  /** 這位演唱者在畫面上叫什麼。點歌台沒設暱稱就用麥克風代號。 */
  function singerName(which) {
    const custom = which === "a" ? duetNameA : duetNameB;
    return (custom || "").trim() || (which === "a" ? "A 麥" : "B 麥");
  }

  /**
   * 共享狀態裡的對唱設定變了。
   *
   * 開關要真的去開／關第二支麥克風的硬體，所以這裡是非同步的；
   * 開不起來就把共享狀態改回關閉 —— 讓按鈕停在「開」但實際上沒作用，
   * 是最糟的選擇（畫面說在對唱，成績卻只有一個人的）。
   */
  async function applyDuetState(state) {
    if (state.duet_name_a !== undefined) duetNameA = String(state.duet_name_a || "");
    if (state.duet_name_b !== undefined) duetNameB = String(state.duet_name_b || "");
    pitchEngine.setLabel(duetEnabled ? singerName("a") : "");
    pitchEngineB.setLabel(singerName("b"));

    if (state.duet_enabled === undefined || !!state.duet_enabled === duetEnabled) {
      renderDuetBoard(performance.now(), true);
      return;
    }

    duetEnabled = !!state.duet_enabled;
    duet.configure({ enabled: duetEnabled });
    pitchEngine.setLabel(duetEnabled ? singerName("a") : "");

    if (!duetEnabled) {
      await disableDuetAudio();
      renderDuetBoard(performance.now(), true);
      return;
    }
    await startDuetAudio();
  }

  /**
   * 真的去把第二支麥克風開起來。
   *
   * 兩個地方會呼叫：點歌台按下對唱開關時，以及舞台被點擊解鎖音訊時
   * （開關可能在解鎖之前就按了）。
   */
  async function startDuetAudio() {
    if (!isAudioUnlocked) {
      // 舞台還沒被點過（瀏覽器的自動播放政策擋著），連 A 麥都還沒開。
      // 這時候不是「開不起來」而是「還沒輪到」—— 把意圖留著，
      // 解鎖時會再開一次。硬把開關關掉的話，點歌台那邊看起來就像按了沒反應。
      showToast("🎤🎤 對唱模式：點一下舞台畫面啟用麥克風後生效");
      renderDuetBoard(performance.now(), true);
      return false;
    }

    const result = await window.audioEngine.startDuetMic(duetSource);
    if (!result.ok) {
      duetActive = false;
      showToast(`⚠️ 對唱模式：${result.reason}`);
      // 改回關閉。這會再回來一次 STATE_UPDATE，但那時候
      // duet_enabled 已經與本地一致，所以不會無限來回。
      window.api.send("CONTROL", { data: { duet_enabled: false } });
      renderDuetBoard(performance.now(), true);
      return false;
    }
    duetActive = true;
    pitchEngineB.setAnalyser(window.audioEngine.micAnalyserB);
    // 第二位演唱者吃的是同一首歌的導唱音符與曲式（同一把尺才能比）
    pitchEngineB.setPitchData(pitchEngine.pitchData);
    pitchEngineB.setSections(pitchEngine.sectionScorer.sections);
    resetDuet();
    showToast(`🎤🎤 對唱模式已開啟（${singerName("a")} vs ${singerName("b")}）`);
    renderDuetBoard(performance.now(), true);
    return true;
  }

  async function disableDuetAudio() {
    duetActive = false;
    await window.audioEngine.stopDuetMic();
    micAgcB.reset();
    window.audioEngine.setMicAutoGainB(1.0);
    resetDuet();
  }

  /** 換歌／重唱：串音判定的包絡線與兩邊的成績都要歸零。 */
  function resetDuet() {
    duet.reset();
    pitchEngineB.resetScoring();
    lastMidiA = 0;
    lastMidiB = 0;
    lastDuetFrameMs = 0;
    micAgcB.resetStats();
  }

  /**
   * 這一幀該算誰的。
   *
   * 順序很重要：先量兩支麥克風的原始電平，判定完才叫兩邊的 tick()
   * （tick 會帶 reuseRms，不重抓波形）。反過來的話就得在還不知道
   * 「這一幀是誰在唱」的時候先計分，那就沒有串音判定可言了。
   */
  function decideDuetCredit(nowMs) {
    const rmsA = pitchEngine.measureRms();
    const rmsB = pitchEngineB.measureRms();
    const dt = lastDuetFrameMs ? (nowMs - lastDuetFrameMs) / 1000 : 0;
    lastDuetFrameMs = nowMs;
    return duet.decide(dt, { rms: rmsA, midi: lastMidiA }, { rms: rmsB, midi: lastMidiB });
  }

  /** 第二支麥克風的自動增益（與 A 同一套邏輯，各自一個實例）。 */
  function updateMicAgcB(rms, nowMs) {
    const dt = lastMicFrameMsB ? (nowMs - lastMicFrameMsB) / 1000 : 0;
    lastMicFrameMsB = nowMs;
    if (dt > 0) {
      micAgcB.update(dt, { rms });
      window.audioEngine.setMicAutoGainB(micAgcB.enabled ? micAgcB.level : 1.0);
    }
  }

  /**
   * 對唱計分板：兩位演唱者的即時分數、Combo，以及誰領先。
   *
   * 商用機的對唱畫面就是這樣：兩個人各自一條，分數即時跳。
   * 文字每 200ms 才更新一次 —— 60fps 直接寫 textContent，
   * 兩個人四個欄位等於每秒 240 次重排。
   */
  function renderDuetBoard(nowMs, force = false) {
    if (!duetBoard) return;
    const show = duetEnabled && duetActive;
    document.body.classList.toggle("duet-mode", show);
    if (!show) {
      if (duetBoard.style.display !== "none") duetBoard.style.display = "none";
      return;
    }
    duetBoard.style.display = "flex";
    if (!force && nowMs - lastDuetBoardMs < 200) return;
    lastDuetBoardMs = nowMs;

    const muted = duet.mutedSinger();
    const rows = [
      { el: duetRowA, which: "a", engine: pitchEngine },
      { el: duetRowB, which: "b", engine: pitchEngineB },
    ];
    const scoreA = pitchEngine.score;
    const scoreB = pitchEngineB.score;

    rows.forEach(({ el, which, engine }) => {
      if (!el) return;
      const leading = scoreA !== scoreB &&
        ((which === "a" && scoreA > scoreB) || (which === "b" && scoreB > scoreA));
      el.classList.toggle("leading", leading);
      // 被判成串音的那一位標明原因：不講的話「我明明在唱，分數卻不動」
      // 只會被當成評分壞了（實際上是兩支麥克風靠太近）。
      el.classList.toggle("muted", muted === which);
      el.querySelector(".duet-name").textContent = singerName(which);
      el.querySelector(".duet-score").textContent =
        engine.score.toString().padStart(6, "0");
      el.querySelector(".duet-combo").textContent =
        muted === which ? "串音靜音中" : (engine.combo > 2 ? `${engine.combo} COMBO!` : "");
    });
  }

  /** 換歌／重唱：ducking 的信心度與暖機時間都不能跨曲沿用。 */
  function resetGuideDuck() {
    guideDucker.reset();
    lastGuideFrameMs = 0;
    applyGuideDuck();
    updateGuideDuckBadge(performance.now(), false);
  }

  // --- 自動音量平衡 (EBU R128) ---
  // 伺服器已依這首歌的整合響度與設定的目標值算好增益，這裡只負責套上去。
  async function applyLoudness(songId) {
    try {
      const info = await window.api.getLoudness(songId);
      // 等回應的期間可能已經切歌了，那就別把上一首的增益套到這一首
      if (songId !== currentSongId) return;
      const applied = window.audioEngine.setNormalizationDb(info.gain_db || 0);
      if (info.measured) {
        console.log(`[KaraTube] 音量平衡：${info.lufs} LUFS → 目標 ${info.target_lufs}，套用 ${applied} dB`);
      }
    } catch (e) {
      window.audioEngine.setNormalizationDb(0);
    }
  }

  // --- 唱畢總評分結算畫面 ---
  // 歌唱完先亮 9 秒成績單（總分、等級、音準率、最大 Combo、個人最佳、擊敗比例），
  // 再通知後端切下一首。中途 切歌/點下一首 會直接收掉，不會卡住流程。
  const settlementOverlay = document.getElementById("settlementOverlay");
  const settleSongTitle = document.getElementById("settleSongTitle");
  const settleScore = document.getElementById("settleScore");
  const settleGrade = document.getElementById("settleGrade");
  const settleAccuracy = document.getElementById("settleAccuracy");
  const settleCombo = document.getElementById("settleCombo");
  const settleBest = document.getElementById("settleBest");
  const settleBeat = document.getElementById("settleBeat");
  const settleSections = document.getElementById("settleSections");
  const settleDuet = document.getElementById("settleDuet");
  const settleTrend = document.getElementById("settleTrend");
  const settleGuide = document.getElementById("settleGuide");
  const settleMic = document.getElementById("settleMic");
  let settlementTimer = null;

  function escapeHtml(text) {
    return String(text == null ? "" : text)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  /**
   * 段落表現長條圖：每一段的命中率＋最佳/待加強段落點名。
   *
   * 只畫「可評分」的段落（導唱音符夠多的那些）；不到兩段就整塊收起來 ——
   * 一段的長條圖沒有比較的意義，只是佔掉結算畫面的時間。
   */
  function renderSectionBreakdown(result) {
    if (!settleSections) return;
    const rows = (result.sections || []).filter(s => s.graded);
    if (rows.length < 2) {
      settleSections.innerHTML = "";
      settleSections.style.display = "none";
      return;
    }

    const best = result.best_section;
    const worst = result.worst_section;
    const pct = (v) => Math.round((v || 0) * 100);
    const verdict = best && worst
      ? `<div class="settlement-section-verdict">` +
        `<span class="verdict-best">💯 最佳段落 <b>${escapeHtml(best.label)}</b> ${pct(best.accuracy)}%</span>` +
        `<span class="verdict-worst">📈 待加強 <b>${escapeHtml(worst.label)}</b> ${pct(worst.accuracy)}%</span>` +
        `</div>`
      : `<div class="settlement-section-verdict"><span>整首表現平均，沒有明顯拖分的段落</span></div>`;

    const bars = rows.map(s => {
      const flag = best && s.index === best.index ? " is-best"
        : (worst && s.index === worst.index ? " is-worst" : "");
      const title = s.preview ? `${s.label}　${s.preview}` : s.label;
      return `<div class="settlement-section-row${flag}" title="${escapeHtml(title)}">` +
        `<span class="section-row-label">${escapeHtml(s.label)}</span>` +
        `<span class="section-row-bar"><i style="width:${pct(s.accuracy)}%"></i></span>` +
        `<span class="section-row-value">${pct(s.accuracy)}%</span>` +
        `</div>`;
    }).join("");

    settleSections.style.display = "block";
    settleSections.innerHTML =
      `<div class="settlement-label">📊 段落表現</div>${verdict}` +
      `<div class="settlement-section-bars">${bars}</div>`;
  }

  /**
   * 結算畫面的「跨場次趨勢」：這首歌你**一向**強在哪一段、弱在哪一段。
   *
   * 上面那張段落長條圖講的是這一次（副歌 1 只有 31%），這一塊講的是每一次
   * （副歌 1 一向比你自己的平均低 9 個百分點）。分開畫是有意的 ——
   * 今天的失誤與長期的弱點混在同一張圖上，看不出哪一個才需要去練。
   *
   * 中線是「這個人自己的平均」，往右是主場、往左是弱點；沒被點名的段落畫淡色
   * （幅度不夠或方向不一致），代表那一段還沒有結論而不是「剛好是 0」。
   */
  function renderTrend(trend) {
    if (!settleTrend) return;
    const view = window.TrendView;
    const summary = view ? view.describeTrend(trend) : { kind: "none" };
    if (summary.kind === "none") {
      settleTrend.style.display = "none";
      settleTrend.innerHTML = "";
      return;
    }

    // 場次不夠時只有一句「再唱幾次」，不畫沒有結論的長條圖
    const rows = summary.kind === "waiting" ? [] : view.trendRows(trend);
    const bars = rows.map((r) => {
      const width = Math.round(r.ratio * 50); // 各半邊最多 50%，中線在正中間
      const dim = r.named ? "" : " is-dim";
      const title = `${r.label}　平均 ${Math.round(r.accuracy * 100)}%　${r.appearances} 次`;
      return `<div class="trend-row${dim}" title="${escapeHtml(title)}">` +
        `<span class="trend-bar-side left">${r.side === "weak"
          ? `<i style="width:${width}%"></i>` : ""}</span>` +
        `<span class="trend-row-label">${escapeHtml(r.label)}</span>` +
        `<span class="trend-bar-side right">${r.side === "home"
          ? `<i style="width:${width}%"></i>` : ""}</span>` +
        `<span class="trend-row-value">${escapeHtml(r.text)}</span>` +
        `</div>`;
    }).join("");

    const detail = summary.detail
      ? `<div class="settlement-trend-detail">${escapeHtml(summary.detail)}</div>` : "";
    settleTrend.style.display = "block";
    settleTrend.innerHTML =
      `<div class="settlement-trend-headline">${escapeHtml(summary.headline)}</div>${detail}` +
      (bars ? `<div class="settlement-trend-bars">${bars}</div>` : "");
  }

  /**
   * 對唱的跨場次趨勢：兩位各一行，不畫長條圖。
   *
   * 對唱結算畫面已經有一整排段落對決的對拉長條圖了，再疊兩張趨勢圖
   * 會變成三張長得很像的圖擠在九秒的畫面裡 —— 每一張都看不完。
   * 所以這裡只留兩句話，各自的完整趨勢圖在點歌台的「我的成績」看。
   */
  function renderDuetTrends(data) {
    if (!settleTrend) return;
    const view = window.TrendView;
    const lines = ["a", "b"].map((which) => {
      const side = (data && data[which]) || {};
      const summary = view ? view.describeTrend(side.trend) : { kind: "none" };
      if (summary.kind === "none" || summary.kind === "waiting") return "";
      const who = side.singer || (which === "a" ? "A 麥" : "B 麥");
      return `<div class="settlement-trend-headline">` +
        `<b>${escapeHtml(who)}</b>　${escapeHtml(summary.headline)}</div>`;
    }).filter(Boolean).join("");

    settleTrend.style.display = lines ? "block" : "none";
    settleTrend.innerHTML = lines;
  }

  /**
   * 結算畫面的「導唱獨立度」：這首歌有多少比例的時間你不需要導唱帶。
   *
   * 比總分更能說明進步 —— 分數會被歌難不難影響，
   * 但「上週副歌整段靠導唱，這週導唱退場了七成」是很直接的回饋。
   * 導唱沒開、或唱的時間太短不足以判斷時就整塊不顯示，不要硬給一個數字。
   */
  function renderGuideIndependence() {
    if (!settleGuide) return;
    const s = guideDucker.summary();
    if (!guideDucker.enabled || guideBaseVolume <= 0.01 || s.independence === null) {
      settleGuide.style.display = "none";
      settleGuide.textContent = "";
      return;
    }
    settleGuide.style.display = "block";
    settleGuide.textContent = `🎚️ 導唱獨立度 ${Math.round(s.independence * 100)}%（導唱自動淡出的時間比例）`;
  }

  /**
   * 結算畫面的麥克風建議。
   *
   * 只在「機器持續大幅補償」時才出現，而且說的是唱歌的人能做的動作：
   * 一直要加很多 dB＝離麥克風太遠（軟體能加的量有上限，加到底就只是放大底噪）；
   * 一直要減很多 dB 或已經削峰＝貼太近，音色會悶而且爆音救不回來。
   * 差不到 3 dB 就不要講話 —— 每首歌都跳一行建議會變成沒人看的雜訊。
   */
  function renderMicAdvice() {
    if (!settleMic) return;
    const s = micAgc.summary();
    const avg = s.average_gain_db;
    let text = "";
    if (micAgc.enabled && avg !== null) {
      if (s.clip_guarded) {
        text = `🎤 麥克風輸入過大（自動降了 ${Math.abs(avg).toFixed(1)} dB 並啟動削峰保護），建議拿遠一點`;
      } else if (avg >= 3) {
        text = `🎤 自動增益平均 +${avg.toFixed(1)} dB —— 麥克風可以拿近一點，音色會更紮實`;
      } else if (avg <= -3) {
        text = `🎤 自動增益平均 ${avg.toFixed(1)} dB —— 麥克風稍微拿遠一點，會少一點噴麥`;
      }
    }
    settleMic.style.display = text ? "block" : "none";
    settleMic.textContent = text;
  }

  function hideSettlement() {
    if (settlementTimer) { clearTimeout(settlementTimer); settlementTimer = null; }
    if (settlementOverlay) settlementOverlay.classList.remove("show");
  }

  function finishSettlement() {
    if (!settlementTimer) return; // 已被切歌等流程收掉，別重複送 SONG_ENDED
    clearTimeout(settlementTimer);
    settlementTimer = null;
    settlementOverlay.classList.remove("show");
    window.api.send("SONG_ENDED");
  }

  // 分數從 0 滾動到總分，商用機結算畫面的儀式感
  function animateScoreCount(target) {
    const durationMs = 1400;
    const start = performance.now();
    function step(now) {
      const t = Math.min(1, (now - start) / durationMs);
      const eased = 1 - Math.pow(1 - t, 3);
      settleScore.textContent = Math.round(target * eased).toLocaleString();
      if (t < 1 && settlementOverlay.classList.contains("show")) {
        requestAnimationFrame(step);
      }
    }
    requestAnimationFrame(step);
  }

  /**
   * 每段的命中率，壓成入庫需要的兩個欄位（外加給後端再守一次門的音符幀數）。
   *
   * 1.7.0 之前這一整串是「現場資訊」不入庫，只送最佳／最差兩個標籤。
   * 但那兩個標籤說的是**這一次**哪一段唱壞了，跨場次的「你一向掉副歌」
   * 只能從每一場的完整段落命中率長出來 —— 標籤本身沒有幅度可以平均。
   */
  function sectionPayload(result) {
    return (result.sections || [])
      .filter((s) => s && s.graded)
      .map((s) => ({ label: s.label, accuracy: s.accuracy, note_frames: s.note_frames }));
  }

  /** 送進 /api/scores 的成績單。段落點名只送標籤，長條圖是現場資訊不必入庫。 */
  function scorePayload(song, result) {
    return {
      song_id: song.song_id,
      title: song.title,
      artist: song.artist,
      thumbnail: song.thumbnail,
      score: result.score,
      accuracy: result.accuracy,
      max_combo: result.max_combo,
      grade: result.grade,
      best_section: result.best_section ? result.best_section.label : "",
      worst_section: result.worst_section ? result.worst_section.label : "",
      sections: sectionPayload(result)
    };
  }

  async function showSettlement(song, result) {
    hideIntroCard(); // 極短的歌可能唱完時片頭卡還亮著
    if (!settlementOverlay || !settlementEnabled) {
      // 設定關掉結算畫面時仍要記成績，只是不佔用畫面時間，直接進下一首
      if (settlementEnabled === false && song) {
        window.api.submitScore(scorePayload(song, result)).catch(() => {});
      }
      window.api.send("SONG_ENDED");
      return;
    }
    settleSongTitle.textContent = song.title || "";
    settleGrade.textContent = result.grade;
    settleGrade.dataset.grade = result.grade;
    settleAccuracy.textContent = `${Math.round(result.accuracy * 100)}%`;
    settleCombo.textContent = `${result.max_combo}`;
    settleBest.textContent = "";
    settleBeat.textContent = "";
    // 上一首可能是對唱，對戰區塊要收掉（不然單人成績單上會留著別人的比分）
    if (settleDuet) {
      settleDuet.innerHTML = "";
      settleDuet.style.display = "none";
    }
    renderSectionBreakdown(result);
    // 趨勢要等 /api/scores 回來才知道（含這一次的歷史才算數），先收起來
    renderTrend(null);
    renderGuideIndependence();
    renderMicAdvice();
    settlementOverlay.classList.add("show");
    animateScoreCount(result.score);

    settlementTimer = setTimeout(finishSettlement, settlementMs);

    try {
      const res = await window.api.submitScore(scorePayload(song, result));
      const r = (res && res.result) || {};
      if (r.is_new_best) {
        settleBest.textContent = r.previous_best != null
          ? `🎉 刷新個人最佳！（原紀錄 ${r.previous_best.toLocaleString()} 分）`
          : "🎉 本曲首次演唱，個人最佳紀錄達成！";
      } else if (r.best_score != null) {
        settleBest.textContent = `🏆 本曲個人最佳 ${r.best_score.toLocaleString()} 分`;
      }
      if (r.beat_percent != null) {
        settleBeat.textContent = `擊敗全場 ${r.beat_percent}% 的演唱`;
      }
      renderTrend(r.trend);
    } catch (e) {
      console.warn("結算成績上傳失敗:", e);
    }
  }

  /** 對唱結算送進 /api/scores/duet 的成績單（兩位一起送，見 api.js 的說明）。 */
  function duetScorePayload(song, resultA, resultB, verdict) {
    const duel = (verdict && verdict.sections) || {};
    const side = (which, result) => {
      // 段落對決的主場段落（對唱才有）：長條圖是現場資訊不入庫，
      // 但「這首歌的副歌一向是我的主場」是回頭看歷史時真正有用的一句話。
      const spot = which === "a" ? duel.a_best : duel.b_best;
      return {
        singer: singerName(which),
        score: result.score,
        accuracy: result.accuracy,
        max_combo: result.max_combo,
        grade: result.grade,
        best_section: result.best_section ? result.best_section.label : "",
        worst_section: result.worst_section ? result.worst_section.label : "",
        duel_section: spot ? spot.label : "",
        // 兩位各自的段落命中率分開送：對唱的串音判定已經把每一幀歸給其中一位，
        // 所以這兩串是各自的實力，合起來平均反而誰的趨勢都算不出來。
        sections: sectionPayload(result),
      };
    };
    return {
      song_id: song.song_id,
      title: song.title,
      artist: song.artist,
      thumbnail: song.thumbnail,
      a: side("a", resultA),
      b: side("b", resultB),
    };
  }

  // 段落對決最多畫幾段。九秒的結算畫面塞不下一首歌的每一段，
  // 而且十段長條圖沒有人看得完 —— 超過就只留真的有對決的段落。
  const MAX_DUEL_ROWS = 6;

  /**
   * 段落對決：同一段落兩位的命中率往中間對拉的長條圖。
   *
   * 兩個人各畫一張獨立的長條圖是最直覺的做法，也是最沒用的做法 ——
   * 「A 的副歌 1 是 68%」跟「B 的副歌 1 是 41%」分在兩張圖上，眼睛得自己配對。
   * 對拉的畫法（左邊 A、右邊 B、段落標籤在中間）把比較這件事畫進版面本身，
   * 掃一眼就知道哪幾段偏一邊。
   *
   * 分工段落（只有一個人唱的主歌）照樣列出來但不給名次，並標明「主唱」——
   * 對唱歌曲本來就是分段接唱，把那些段落畫成 68% 對 0% 是在誣賴沒唱的那一位。
   */
  function renderDuetSectionDuel(duel) {
    const rows0 = (duel && duel.rows) || [];
    // 只有一段可比就沒有比較的意義（跟單人成績單的段落長條圖同一個判準）
    if (rows0.length < 2) return "";

    let rows = rows0;
    if (rows.length > MAX_DUEL_ROWS) {
      const contested = rows.filter((r) => r.contested);
      rows = (contested.length >= 2 ? contested : rows).slice(0, MAX_DUEL_ROWS);
    }
    const omitted = rows0.length - rows.length;

    const pct = (v) => Math.round((v || 0) * 100);
    const nameOf = (which) => escapeHtml(singerName(which));

    const summary = duel.contested_count === 0
      ? `<span>兩位是分段接唱，沒有同時唱的段落可以比高低</span>`
      : `<span class="duel-count-a">${nameOf("a")} ${duel.wins.a} 段</span>` +
        `<span class="duel-count-b">${nameOf("b")} ${duel.wins.b} 段</span>` +
        (duel.wins.tie > 0 ? `<span class="duel-count-tie">平手 ${duel.wins.tie} 段</span>` : "");

    const spotlight = ["a", "b"].map((which) => {
      const best = which === "a" ? duel.a_best : duel.b_best;
      if (!best) return "";
      return `<span class="duel-spot duel-spot-${which}">` +
        `⭐ ${nameOf(which)}的主場 <b>${escapeHtml(best.label)}</b> +${pct(best.margin)}%</span>`;
    }).join("");

    const row = (r) => {
      const cls = r.contested
        ? (r.leader === "tie" ? " is-tie" : ` is-${r.leader}`)
        : " is-solo";
      const title = r.contested
        ? `${r.label}　${nameOf("a")} ${pct(r.accuracy_a)}% / ${nameOf("b")} ${pct(r.accuracy_b)}%`
        : `${r.label}　由 ${singerName(r.main)} 主唱（分段接唱，不比高低）`;
      // 分工段落只畫主唱那一邊，另一邊留白並標成「—」：
      // 畫成 0% 的長條看起來像「唱了但完全沒中」，那是兩件完全不同的事
      const show = (which) => r.contested || r.main === which;
      const value = (which) => (show(which)
        ? `${pct(which === "a" ? r.accuracy_a : r.accuracy_b)}%` : "—");
      const width = (which) => (show(which)
        ? pct(which === "a" ? r.accuracy_a : r.accuracy_b) : 0);
      const soloTag = r.contested ? "" : `<em class="duel-solo-tag">主唱</em>`;
      return `<div class="duet-duel-row${cls}" title="${escapeHtml(title)}">` +
        `<span class="duel-pct duel-pct-a">${value("a")}</span>` +
        `<span class="duel-side duel-side-a"><i style="width:${width("a")}%"></i></span>` +
        `<span class="duel-label">${escapeHtml(r.label)}${soloTag}</span>` +
        `<span class="duel-side duel-side-b"><i style="width:${width("b")}%"></i></span>` +
        `<span class="duel-pct duel-pct-b">${value("b")}</span>` +
        `</div>`;
    };

    const note = omitted > 0
      ? `<div class="duel-note">（另有 ${omitted} 段未列出，完整段落成績記在評分歷史）</div>`
      : "";

    return `<div class="duet-section-duel">` +
      `<div class="settlement-label">🎼 段落對決</div>` +
      `<div class="duel-summary">${summary}${spotlight}</div>` +
      `<div class="duel-rows">${rows.map(row).join("")}</div>${note}</div>`;
  }

  /**
   * 對唱對戰結果：兩欄成績並排，上面一條勝負橫幅，下面段落對決。
   *
   * 段落長條圖不是兩個人各畫一張（塞不進結算畫面的九秒，也沒人配對得起來），
   * 而是把同一段的兩個命中率往中間對拉 —— 見 renderDuetSectionDuel。
   */
  function renderDuetVerdict(verdict, resultA, resultB) {
    if (!settleDuet) return;
    const pct = (v) => `${Math.round((v || 0) * 100)}%`;
    const banner = verdict.winner === "tie"
      ? `<div class="duet-banner is-tie">🤝 平手！只差 ${verdict.margin.toLocaleString()} 分</div>`
      : `<div class="duet-banner">🏆 ${escapeHtml(singerName(verdict.winner))} 勝出` +
        `<span>領先 ${verdict.margin.toLocaleString()} 分</span></div>`;

    const column = (which, result) => {
      const win = verdict.winner === which ? " is-winner" : "";
      const mic = verdict.mics[which];
      // 串音比例高就直接講出來：那不是唱不好，是兩支麥克風靠太近，
      // 分數本身已經不公平，不說的話使用者會以為自己真的輸了。
      const warn = mic.crosstalk_ratio >= 0.3
        ? `<span class="duet-col-warn">⚠️ 有 ${pct(mic.crosstalk_ratio)} 的時間被判為串音</span>`
        : "";
      return `<div class="duet-col${win}">` +
        `<div class="duet-col-name">${escapeHtml(singerName(which))}</div>` +
        `<div class="duet-col-score">${result.score.toLocaleString()}</div>` +
        `<div class="duet-col-grade" data-grade="${escapeHtml(result.grade)}">${escapeHtml(result.grade)}</div>` +
        `<div class="duet-col-stats">音準 ${pct(result.accuracy)} ・ COMBO ${result.max_combo}</div>` +
        `<div class="duet-col-best" data-singer="${which}"></div>${warn}</div>`;
    };

    settleDuet.style.display = "block";
    settleDuet.innerHTML = `<div class="settlement-label">🎤🎤 對唱結果</div>${banner}` +
      `<div class="duet-cols">${column("a", resultA)}${column("b", resultB)}</div>` +
      renderDuetSectionDuel(verdict.sections);
  }

  /**
   * 對唱模式的唱畢結算。
   *
   * 主分數區顯示勝出者的成績（平手時顯示較高的那一份），
   * 詳細的兩欄比較在下面的對戰區塊。
   */
  async function showDuetSettlement(song, resultA, resultB, verdict) {
    hideIntroCard();
    const top = verdict.winner === "b" ? resultB : resultA;
    if (!settlementOverlay || !settlementEnabled) {
      if (settlementEnabled === false && song) {
        window.api.submitDuetScore(duetScorePayload(song, resultA, resultB, verdict)).catch(() => {});
      }
      window.api.send("SONG_ENDED");
      return;
    }

    settleSongTitle.textContent = song.title || "";
    settleGrade.textContent = top.grade;
    settleGrade.dataset.grade = top.grade;
    settleAccuracy.textContent = `${Math.round(top.accuracy * 100)}%`;
    settleCombo.textContent = `${top.max_combo}`;
    settleBest.textContent = "";
    settleBeat.textContent = "";
    if (settleSections) {
      settleSections.innerHTML = "";
      settleSections.style.display = "none";
    }
    renderDuetVerdict(verdict, resultA, resultB);
    renderTrend(null);
    renderGuideIndependence();
    renderMicAdvice();
    settlementOverlay.classList.add("show");
    animateScoreCount(top.score);

    settlementTimer = setTimeout(finishSettlement, settlementMs);

    try {
      const res = await window.api.submitDuetScore(duetScorePayload(song, resultA, resultB, verdict));
      const data = (res && res.result) || {};
      ["a", "b"].forEach((which) => {
        const r = data[which] || {};
        const el = settleDuet && settleDuet.querySelector(`.duet-col-best[data-singer="${which}"]`);
        if (!el) return;
        if (r.is_new_best) {
          el.textContent = r.previous_best != null
            ? `🎉 刷新紀錄（原 ${r.previous_best.toLocaleString()}）`
            : "🎉 本曲首次演唱";
        } else if (r.best_score != null) {
          el.textContent = `🏆 本曲最佳 ${r.best_score.toLocaleString()}`;
        }
      });
      renderDuetTrends(data);
    } catch (e) {
      console.warn("對唱結算成績上傳失敗:", e);
    }
  }

  if (settlementOverlay) {
    // 點一下結算畫面直接進下一首，不用等倒數
    settlementOverlay.addEventListener("click", (e) => {
      e.stopPropagation();
      finishSettlement();
    });
  }

  // --- 導唱片頭卡 ---
  // 商用點歌機在前奏亮出「歌名 / 演唱者 / 點歌人」的開場卡。
  // 只在歌曲從頭開始（點播、重唱）時顯示，暫停再繼續不會重出。
  const introCard = document.getElementById("introCard");
  const introTitle = document.getElementById("introTitle");
  const introArtist = document.getElementById("introArtist");
  const introRequester = document.getElementById("introRequester");
  let introCardTimer = null;

  function hideIntroCard() {
    if (introCardTimer) { clearTimeout(introCardTimer); introCardTimer = null; }
    if (introCard) introCard.classList.remove("show");
  }

  function showIntroCard(song) {
    if (!introCard || !song || !introCardEnabled) return;
    hideIntroCard();
    introTitle.textContent = song.title || "";
    introArtist.textContent = song.artist ? `演唱者：${song.artist}` : "";
    introRequester.textContent = song.requested_by ? `點歌：${song.requested_by}` : "";
    introCard.classList.add("show");
    introCardTimer = setTimeout(hideIntroCard, introCardMs);
  }

  // --- 字幕同步微調 ---
  function showToast(html) {
    if (!syncToast) return;
    syncToast.innerHTML = html;
    syncToast.style.display = "block";
    if (syncToastTimer) clearTimeout(syncToastTimer);
    syncToastTimer = setTimeout(() => { syncToast.style.display = "none"; }, 2600);
  }

  function showSyncToast() {
    const sign = lyricOffsetMs > 0 ? "+" : "";
    const desc = lyricOffsetMs === 0 ? "自動補償" : (lyricOffsetMs > 0 ? "字幕延後" : "字幕提前");
    showToast(
      `🎬 字幕同步 ${sign}${lyricOffsetMs} ms（${desc}）` +
      `<span class="sync-hint">← → 調整 50ms ・ Shift+← → 微調 10ms ・ 0 歸零 ・ P 開關音準線 ` +
      `・ D 對唱模式 ・ 自動補償 ${(outputLatency * 1000).toFixed(0)}ms</span>`);
  }

  function setLyricOffset(ms, broadcast = true) {
    lyricOffsetMs = Math.max(-2000, Math.min(2000, Math.round(ms)));
    try { localStorage.setItem(OFFSET_STORAGE_KEY, String(lyricOffsetMs)); } catch (e) { }
    showSyncToast();
    if (broadcast) {
      window.api.send("CONTROL", { data: { lyric_offset_ms: lyricOffsetMs } });
    }
  }

  // 音準導唱線顯示切換。關掉後 MV 畫面完整露出來。
  function setShowPitch(show, broadcast = true) {
    showPitch = !!show;
    document.body.classList.toggle("hide-pitch", !showPitch);
    try { localStorage.setItem(PITCH_STORAGE_KEY, showPitch ? "1" : "0"); } catch (e) { }
    if (broadcast) {
      window.api.send("CONTROL", { data: { show_pitch: showPitch } });
    }
  }

  // --- 演唱模式與音訊裝置 ---
  let singMode = "solo";

  function applySingMode(mode) {
    singMode = window.audioEngine.setSingMode(mode);
    const isParty = singMode === "party";
    // 多人模式人聲會從喇叭出來，每多加 1 dB 就離回授近 1 dB，
    // 所以自動增益的加成上限跟著收緊（configure 會立刻把現有增益夾回新範圍）。
    micAgc.configure({ maxBoostDb: isParty ? 6 : 9 });
    window.audioEngine.setMicAutoGain(micAgc.enabled ? micAgc.level : 1.0);
    micAgcB.configure({ maxBoostDb: isParty ? 6 : 9 });
    window.audioEngine.setMicAutoGainB(micAgcB.enabled ? micAgcB.level : 1.0);
    modeSoloBtn.classList.toggle("active", !isParty);
    modePartyBtn.classList.toggle("active", isParty);
    modeHint.innerHTML = isParty
      ? "多人模式：人聲會從喇叭放出來。<b>務必使用外接喇叭</b> —— 筆電喇叭與內建麥克風在同一個機殼裡，震動會經外殼直接傳回麥克風，這條路徑拉開距離也沒用。"
      : "單人模式：人聲不進喇叭，不可能有回授。音準評分照常運作。";
  }

  modeSoloBtn.addEventListener("click", () => {
    applySingMode("solo");
    window.api.send("CONTROL", { data: { sing_mode: "solo" } });
  });
  modePartyBtn.addEventListener("click", () => {
    applySingMode("party");
    window.api.send("CONTROL", { data: { sing_mode: "party" } });
  });

  function fillSelect(sel, devices, savedId, defaultLabel) {
    sel.innerHTML = `<option value="">${defaultLabel}</option>` +
      devices.map(d => `<option value="${d.id}">${d.label}</option>`).join("");
    if (savedId && devices.some(d => d.id === savedId)) sel.value = savedId;
  }

  async function refreshAudioDevices() {
    try {
      const { inputs, outputs, canSelectOutput } = await window.audioEngine.listDevices();
      let savedIn = null, savedOut = null;
      try {
        savedIn = localStorage.getItem(INPUT_DEV_KEY);
        savedOut = localStorage.getItem(OUTPUT_DEV_KEY);
      } catch (e) { }

      fillSelect(inputDeviceSelect, inputs, savedIn, "系統預設麥克風");
      fillSelect(outputDeviceSelect, outputs, savedOut, "系統預設輸出");

      outputDeviceSelect.disabled = !canSelectOutput;
      outputHint.textContent = canSelectOutput
        ? "選外接喇叭或音效介面，可以直接消掉機殼傳導那條回授路徑。"
        : "此瀏覽器不支援指定輸出裝置（需 Chrome 110+），請改從作業系統的音效設定切換。";

      fillDuetSourceSelect(inputs, savedIn);

      if (savedIn) await window.audioEngine.setInputDevice(savedIn);
      if (savedOut && canSelectOutput) await window.audioEngine.setOutputDevice(savedOut);
    } catch (e) {
      console.warn("列舉音訊裝置失敗:", e);
    }
  }

  /**
   * 第二支麥克風的來源選單。
   *
   * 兩種接法都要支援，因為它們對應兩種真實的硬體：
   *   * 兩個輸入裝置 —— 兩支 USB 麥克風，最便宜的做法。
   *   * 同一裝置的左右聲道 —— 兩支麥克風接一台音效介面／混音器，
   *     這是包廂真正的接法（也只有這條路能用真正的動圈麥克風）。
   * A 麥自己那個裝置不列進「另一個裝置」的選項：同一個裝置開兩次會拿到
   * 一模一樣的訊號，兩個人的分數就會完全一樣 —— 那是最難察覺的壞法。
   */
  function fillDuetSourceSelect(inputs, currentInputId) {
    if (!duetSourceSelect) return;
    const others = inputs.filter((d) => d.id && d.id !== (currentInputId || ""));
    duetSourceSelect.innerHTML =
      `<option value="channel">同一裝置的右聲道（立體聲介面 / 混音器）</option>` +
      others.map((d) => `<option value="device:${d.id}">${d.label}</option>`).join("");
    const wanted = duetSource.mode === "channel" ? "channel" : `device:${duetSource.deviceId}`;
    if ([...duetSourceSelect.options].some((o) => o.value === wanted)) {
      duetSourceSelect.value = wanted;
    } else {
      // 記住的裝置不在了（USB 麥克風被拔掉）：退回左右聲道，不要留一個選不到的值
      duetSource = { mode: "channel", deviceId: "" };
      duetSourceSelect.value = "channel";
    }
    if (duetSourceHint) {
      duetSourceHint.textContent = others.length
        ? "兩支 USB 麥克風選各自的裝置；兩支麥克風接同一台音效介面就用左右聲道。"
        : "只找到一個輸入裝置：兩支麥克風請接同一台立體聲音效介面（左=A、右=B）。";
    }
  }

  if (duetSourceSelect) {
    duetSourceSelect.addEventListener("change", async (e) => {
      const value = e.target.value || "channel";
      duetSource = value.startsWith("device:")
        ? { mode: "device", deviceId: value.slice(7) }
        : { mode: "channel", deviceId: "" };
      try { localStorage.setItem(DUET_SOURCE_KEY, JSON.stringify(duetSource)); } catch (err) { }
      if (!duetEnabled) {
        showToast("🎤🎤 已記住第二支麥克風來源，開啟對唱模式即生效");
        return;
      }
      // 對唱正開著就當場換過去（不然要關掉再開一次才會生效）
      await window.audioEngine.stopDuetMic();
      const result = await window.audioEngine.startDuetMic(duetSource);
      duetActive = result.ok;
      if (result.ok) {
        pitchEngineB.setAnalyser(window.audioEngine.micAnalyserB);
        resetDuet();
        showToast("🎤🎤 已切換第二支麥克風");
      } else {
        showToast(`⚠️ 對唱模式：${result.reason}`);
        window.api.send("CONTROL", { data: { duet_enabled: false } });
      }
      renderDuetBoard(performance.now(), true);
    });
  }

  inputDeviceSelect.addEventListener("change", async (e) => {
    const id = e.target.value;
    const ok = await window.audioEngine.setInputDevice(id);
    try { localStorage.setItem(INPUT_DEV_KEY, id); } catch (err) { }
    // 新裝置的靈敏度跟舊的無關（動圈換成電容差得很遠），學到的增益要丟掉重學
    if (ok) resetMicAgc();
    showToast(ok ? "🎙️ 已切換麥克風" : "⚠️ 麥克風切換失敗，沿用原裝置");
  });

  outputDeviceSelect.addEventListener("change", async (e) => {
    const id = e.target.value;
    const ok = await window.audioEngine.setOutputDevice(id);
    try { localStorage.setItem(OUTPUT_DEV_KEY, id); } catch (err) { }
    showToast(ok ? "🔈 已切換輸出裝置" : "⚠️ 此瀏覽器不支援指定輸出裝置");
  });

  function toggleAudioSetup(show) {
    const open = show !== undefined ? show : audioSetupPanel.style.display === "none";
    audioSetupPanel.style.display = open ? "block" : "none";
    if (open) {
      refreshAudioDevices();
      renderMicMeter();
      startMicMeterLoop();   // 面板關著就沒必要每 66ms 抓一次波形
    } else {
      stopMicMeterLoop();
    }
  }

  audioSetupBtn.addEventListener("click", (e) => { e.stopPropagation(); toggleAudioSetup(); });
  closeAudioSetup.addEventListener("click", (e) => { e.stopPropagation(); toggleAudioSetup(false); });
  audioSetupPanel.addEventListener("click", (e) => e.stopPropagation());

  document.addEventListener("keydown", (e) => {
    const step = e.shiftKey ? 10 : 50;
    if (e.key === "ArrowLeft") {
      e.preventDefault();
      setLyricOffset(lyricOffsetMs - step);      // 字幕提前
    } else if (e.key === "ArrowRight") {
      e.preventDefault();
      setLyricOffset(lyricOffsetMs + step);      // 字幕延後
    } else if (e.key === "0") {
      e.preventDefault();
      setLyricOffset(0);
    } else if (e.key === "p" || e.key === "P") {
      e.preventDefault();
      setShowPitch(!showPitch);
      showToast(showPitch ? "🎯 音準導唱線：顯示" : "🎬 音準導唱線：隱藏（按 P 復原）");
    } else if (e.key === "s" || e.key === "S") {
      e.preventDefault();
      toggleAudioSetup();
    } else if (e.key === "d" || e.key === "D") {
      // 對唱一鍵開關：舞台前面的人不會回去點歌台按（第二支麥克風常常是臨時遞過來的）
      e.preventDefault();
      window.api.send("CONTROL", { data: { duet_enabled: !duetEnabled } });
    } else if (e.key === "m" || e.key === "M") {
      // 現場嘯叫時的緊急切換：一鍵回到單人模式，人聲立刻離開喇叭
      e.preventDefault();
      const next = singMode === "party" ? "solo" : "party";
      applySingMode(next);
      window.api.send("CONTROL", { data: { sing_mode: next } });
      showToast(next === "party" ? "🔊 多人模式（人聲外放）" : "🎧 單人模式（人聲不進喇叭）");
    }
  });

  function handleStateUpdate(state) {
    const song = state.current_song;

    if (state.queue && state.queue.length > 0) {
      nextSongToast.innerHTML = `下一首：<span>${state.queue[0].title}</span>`;
      nextSongToast.style.display = "block";
    } else {
      nextSongToast.style.display = "none";
    }

    guideBaseVolume = state.vocal_volume !== undefined ? Number(state.vocal_volume) || 0 : 0.0;
    window.audioEngine.setVocalVolume(guideBaseVolume);
    window.audioEngine.setMusicVolume(state.music_volume !== undefined ? state.music_volume : 1.0);
    window.audioEngine.setMicVolume(state.mic_volume !== undefined ? state.mic_volume : 1.0);
    window.audioEngine.setMicReverb(state.mic_reverb !== undefined ? state.mic_reverb : 0.25);
    window.audioEngine.setMicEcho(state.mic_echo !== undefined ? state.mic_echo : 0.15);
    window.audioEngine.setMicEchoRepeat(state.mic_echo_repeat !== undefined ? state.mic_echo_repeat : 0.4);
    window.audioEngine.setMicEchoTime(state.mic_echo_time_ms !== undefined ? state.mic_echo_time_ms : 280);
    window.audioEngine.setMicTone(state.mic_tone !== undefined ? state.mic_tone : 0.4);
    if (state.harmony_enabled !== undefined || state.harmony_style !== undefined ||
        state.harmony_level !== undefined) {
      const wasEnabled = harmony.enabled;
      harmony.configure({
        enabled: state.harmony_enabled,
        style: state.harmony_style,
        level: state.harmony_level,
      });
      // 剛被打開：這首歌的調性可能還沒估過（開機時和聲是關著的）
      if (harmony.enabled && !wasEnabled) {
        window.audioEngine.initHarmony().then((ok) => {
          // 只有真的確定不支援才警告。麥克風還沒開起來時也會拿到 false，
          // 那只是「還沒輪到」（開麥克風時會自己再試一次），不是壞了。
          if (!ok && window.audioEngine.harmonySupported === false) {
            showToast("⚠️ 此瀏覽器不支援和聲（需 AudioWorklet）");
          }
        });
        resetHarmony(pitchEngine.pitchData ? pitchEngine.pitchData.notes : []);
      } else if (!harmony.enabled && wasEnabled) {
        resetHarmony();
      }
      updateHarmonyBadge();
    }
    if (state.sing_mode !== undefined) applySingMode(state.sing_mode);
    // 對唱模式：開關會去動硬體（第二支麥克風），所以是非同步的。
    // 不 await —— 這個函式後面還要處理播放狀態，等麥克風開起來會讓畫面卡住。
    if (state.duet_enabled !== undefined || state.duet_name_a !== undefined ||
        state.duet_name_b !== undefined) {
      applyDuetState(state).catch((e) => console.warn("對唱模式切換失敗:", e));
    }

    // 點歌台（含手機）調整字幕同步時同步套用，但不要再廣播回去造成迴圈
    if (state.lyric_offset_ms !== undefined && state.lyric_offset_ms !== lyricOffsetMs) {
      setLyricOffset(state.lyric_offset_ms, false);
    }

    if (state.show_pitch !== undefined) {
      setShowPitch(state.show_pitch, false);
    }

    applyLoopState(state);

    if (song && song.status === "READY") {
      if (song.song_id !== currentSongId) {
        loadAndPlaySong(song);
      } else if (settlementTimer) {
        // 結算畫面亮著時歌已唱完但共享狀態仍是 is_playing，
        // 這裡不能把唱完的歌又拉回來重播
      } else {
        if (state.is_playing && (audioInst.paused || videoBg.paused)) {
          playMedia();
        } else if (!state.is_playing && (!audioInst.paused || !videoBg.paused)) {
          pauseMedia();
        }
      }
    } else if (!song) {
      hideSettlement();
      hideIntroCard();
      currentSongId = null;
      currentSongMeta = null;
      titleEl.textContent = "KaraTube 伴唱系統";
      artistEl.textContent = "請使用點歌台或掃描 QR Code 點播歌曲";
      pauseMedia();
      karaokeRenderer.setLyrics([]);
      pitchEngine.setPitchData(null);
      pitchEngine.setSections([]);
      pitchEngineB.setPitchData(null);
      pitchEngineB.setSections([]);
      resetHarmony([]);
      resetGuideDuck();
      resetDuet();
      // 待機畫面一樣走情境背景（商用機的待機情境畫面），只是能量固定在低檔
      ambientStage.clearSong();
    }
  }

  async function loadAndPlaySong(song) {
    // 切歌或下一首開始時，把還亮著的結算畫面收掉（不送 SONG_ENDED，佇列已前進）
    hideSettlement();
    currentSongId = song.song_id;
    currentSongMeta = song;
    titleEl.textContent = song.title;
    artistEl.textContent = song.artist || "YouTube Music";
    showIntroCard(song);

    const songBaseUrl = `/media/songs/${song.song_id}`;

    // 情境背景要先知道這首歌的狀況再載 MV：has_video 是後端從 metadata 讀的，
    // 沒有 MV 的歌可以直接上情境背景，不必等 <video> 404 回來才發現。
    ambientStage.startSong({
      songId: song.song_id,
      hasVideo: song.has_video === undefined ? undefined : song.has_video !== false,
      thumbnail: song.thumbnail,
    });

    videoBg.src = `${songBaseUrl}/original_video.mp4`;
    audioInst.src = `${songBaseUrl}/instrumental.mp3`;
    audioVoc.src = `${songBaseUrl}/vocals.mp3`;

    // 音量平衡不擋播放：量測資料要現算的舊歌可能要花一兩秒，
    // 先用上一首的增益開唱，算完再平滑接上（setTargetAtTime 不會有爆音）。
    applyLoudness(song.song_id);

    const [lyrics, pitch, structure] = await Promise.all([
      window.api.getLyrics(song.song_id),
      window.api.getPitch(song.song_id),
      // 段落評分是附加資訊，抓不到不能擋播放（歌詞還沒好的歌就是沒有曲式）
      window.api.getSections(song.song_id).catch(() => ({ sections: [] }))
    ]);

    karaokeRenderer.setLyrics(lyrics);
    pitchEngine.setPitchData(pitch);
    // 先 setPitchData（它會歸零評分）再載段落，順序反了段落統計會被清掉
    pitchEngine.setSections((structure && structure.sections) || []);
    // 對唱的第二位吃同一份導唱音符與曲式（同一把尺才比得出誰唱得好）
    pitchEngineB.setPitchData(pitch);
    pitchEngineB.setSections((structure && structure.sections) || []);
    resetDuet();
    // 和聲的調性是從這首歌的導唱音符估出來的，換歌一定要重估（不能沿用上一首的調）
    resetHarmony((pitch && pitch.notes) || []);
    resetGuideDuck();
    // 只清這一首的自動增益統計，學到的增益保留（見 mic-agc.js resetStats 的說明）
    micAgc.resetStats();

    videoBg.currentTime = 0;
    audioInst.currentTime = 0;
    audioVoc.currentTime = 0;
    clock.seekedTo(0);
    lastVocResync = lastVideoResync = performance.now();

    playMedia();
  }

  function playMedia() {
    window.audioEngine.initContext();
    outputLatency = window.audioEngine.getOutputLatency();
    videoBg.play().catch(() => {});
    audioInst.play().catch((err) => {
      console.warn("Audio autoplay blocked by browser policy, awaiting user click:", err);
      if (!isAudioUnlocked && audioPromptOverlay) {
        audioPromptOverlay.classList.remove("hidden");
      }
    });
    audioVoc.play().catch(() => {});
    isPlaying = true;
  }

  function pauseMedia() {
    videoBg.pause();
    audioInst.pause();
    audioVoc.pause();
    isPlaying = false;
  }

  function restartCurrentSong() {
    // 結算畫面亮著時按重唱：收掉結算（不送 SONG_ENDED），從頭再來
    hideSettlement();
    // 重唱是新的一輪演唱，評分歸零重計，結算成績才不會兩輪疊在一起
    pitchEngine.resetScoring();
    resetDuet();
    resetGuideDuck();
    // 調性不用重估（還是同一首歌），但移調器裡的殘留樣本要清掉
    resetHarmony();
    micAgc.resetStats();
    showIntroCard(currentSongMeta);
    videoBg.currentTime = 0;
    audioInst.currentTime = 0;
    audioVoc.currentTime = 0;
    clock.seekedTo(0);
    playMedia();
  }

  /**
   * 跳到指定秒數。三條軌（伴奏、人聲、MV）一起搬，時鐘重新錨定。
   *
   * 卡在最後 0.15 秒之外：seek 到 duration 會立刻觸發 ended，
   * 使用者想跳到尾奏卻直接被結算掉，那不是他要的。
   */
  function seekMedia(seconds) {
    if (!currentSongId) return;
    let target = Math.max(0, Number(seconds) || 0);
    const duration = audioInst.duration;
    if (duration && target > duration - 0.15) target = Math.max(0, duration - 0.15);

    videoBg.currentTime = target;
    audioInst.currentTime = target;
    audioVoc.currentTime = target;
    clock.seekedTo(target);
    lastVocResync = lastVideoResync = performance.now();
    // 舊的音高軌跡時間都落在新位置的「未來」，留著會在導唱線上畫出鬼影。分數不歸零。
    pitchEngine.clearTrail();
    pitchEngineB.clearTrail();
  }

  // --- 練唱模式：A-B 區段循環 ---
  function updatePracticeBadge() {
    if (!practiceBadge) return;
    if (!loopEnabled || loopStart === null || loopEnd === null) {
      practiceBadge.style.display = "none";
      return;
    }
    practiceBadge.style.display = "flex";
    practiceBadgeText.textContent = `練唱循環 ${formatClock(loopStart)} – ${formatClock(loopEnd)}`;
  }

  function formatClock(seconds) {
    const s = Math.max(0, Math.floor(seconds || 0));
    return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  }

  function applyLoopState(state) {
    const wasEnabled = loopEnabled;
    loopEnabled = !!state.loop_enabled;
    loopStart = state.loop_start === null || state.loop_start === undefined ? null : Number(state.loop_start);
    loopEnd = state.loop_end === null || state.loop_end === undefined ? null : Number(state.loop_end);
    updatePracticeBadge();
    if (loopEnabled && !wasEnabled && loopStart !== null && loopEnd !== null) {
      showToast(`🔁 練唱循環開啟 ${formatClock(loopStart)} – ${formatClock(loopEnd)}`);
      // 剛開循環時人已經唱過 B 點的話，先拉回 A 點，不必等這一輪跑完整首
      if (clock.now() > loopEnd) seekMedia(loopStart);
    } else if (!loopEnabled && wasEnabled) {
      showToast("▶️ 練唱循環已關閉");
    }
  }

  // Master Clock & 60 FPS Render Loop
  function renderLoop() {
    requestAnimationFrame(renderLoop);

    // 情境背景在待機（沒有歌）與暫停時也要動，所以放在主時鐘的守門之前。
    // 它自己會節流到 30fps、分頁看不見就完全不畫，成本不會落在這一條迴圈上。
    ambientStage.frame(performance.now(), !audioInst.paused && audioInst.currentTime > 0);

    // 伴奏軌是唯一的主時鐘。人聲軌與影片都只是跟隨者，
    // 不能拿它們的 currentTime 回頭修正字幕，否則會互相拉扯。
    if (audioInst.paused || !(audioInst.currentTime > 0)) return;

    const audioTime = clock.now();
    const duration = audioInst.duration || 0;

    // 練唱循環：唱過 B 點就跳回 A 點。
    // 200ms 冷卻是必要的 —— mp3 的 seek 只能落在解碼區塊邊界，
    // 跳回去之後補間時鐘要一兩幀才重新錨定，沒有冷卻會在 B 點瘋狂連續跳。
    if (loopEnabled && loopStart !== null && loopEnd !== null && audioTime >= loopEnd) {
      const nowMsLoop = performance.now();
      if (nowMsLoop - lastLoopJump > 200) {
        lastLoopJump = nowMsLoop;
        seekMedia(loopStart);
      }
      return;
    }

    // 字幕要對齊的是「現在聽到的聲音」，不是「已經送進音效卡的位置」，
    // 所以要扣掉輸出延遲；lyricOffsetMs 讓使用者再補場地差異。
    const displayTime = audioTime - outputLatency - lyricOffsetMs / 1000;

    const nowMs = performance.now();

    // 人聲軌漂移修正：mp3 seek 不精確，每幀硬拉會造成爆音，
    // 所以只在漂超過 120ms 時修一次，並留 1.5 秒冷卻。
    if (!audioVoc.paused) {
      const drift = audioVoc.currentTime - audioTime;
      if (Math.abs(drift) > 0.12 && nowMs - lastVocResync > 1500) {
        audioVoc.currentTime = audioTime;
        lastVocResync = nowMs;
      }
    }

    // 背景 MV 只是氣氛，容忍度可以更寬
    if (!videoBg.paused) {
      const vdrift = videoBg.currentTime - audioTime;
      if (Math.abs(vdrift) > 0.35 && nowMs - lastVideoResync > 3000) {
        videoBg.currentTime = audioTime;
        lastVideoResync = nowMs;
      }
    }

    karaokeRenderer.update(displayTime);

    // 評分心跳與畫面分離：音準線隱藏時照樣計分，唱畢結算才公平
    let frame;
    let frameB = null;
    if (duetActive) {
      // 對唱：先量兩支麥克風的電平判定這一幀算誰的，再各自計分
      // （tick 帶 reuseRms，同一幀不會重抓兩次波形）
      const credit = decideDuetCredit(nowMs);
      frame = pitchEngine.tick(displayTime, { credit: credit.a, reuseRms: true });
      frameB = pitchEngineB.tick(displayTime, { credit: credit.b, reuseRms: true });
      lastMidiA = frame.userMidi;
      lastMidiB = frameB.userMidi;
      updateMicAgcB(frameB.rms, nowMs);
      renderDuetBoard(nowMs);
    } else {
      frame = pitchEngine.tick(displayTime);
    }

    // 同一份判定直接餵給自動 ducking，不重新偵測一次音高。
    // 對唱模式看的是「有沒有人唱準」—— 兩個人只要有一個唱得穩，
    // 導唱就該退到背景（只看 A 的話，B 獨唱的段落導唱會一直全開）。
    const guideFrame = frameB
      ? { hasNote: frame.hasNote, sang: frame.sang || frameB.sang, hit: frame.hit || frameB.hit }
      : frame;
    updateGuideDuck(guideFrame, nowMs);
    // 自動增益吃的是同一幀量到的麥克風原始電平（frame.rms），也不重複抓波形
    updateMicAgc(frame.rms, nowMs);
    // 和聲吃的是同一幀的導唱音符（frame.noteMidi）與「有沒有人在唱」（frame.sang）。
    // 對唱模式下和聲只疊在 A 麥上 —— 第二支麥克風本身就是第二個聲部，
    // 再疊機器和聲會變成四個聲部混在一起，誰都聽不清楚。
    updateHarmony(frame, nowMs);
    if (showPitch) {
      pitchEngine.updateAndRender(displayTime,
        duetActive ? [{ engine: pitchEngineB, color: DUET_B_COLOR }] : []);
    }

    if (nowMs - lastTimeBroadcast > 400) {
      lastTimeBroadcast = nowMs;
      window.api.send("TIME_UPDATE", { currentTime: audioTime, duration: duration });
    }
  }

  // MV 根本沒下到（很多歌只有音訊）或檔案壞了：情境背景立刻接手，不留黑畫面。
  videoBg.addEventListener("error", () => ambientStage.videoFailed());

  audioInst.addEventListener("seeked", () => clock.seekedTo(audioInst.currentTime));
  audioInst.addEventListener("playing", () => clock.seekedTo(audioInst.currentTime));

  audioInst.addEventListener("ended", () => {
    console.log("Song audio finished.");
    const result = pitchEngine.getFinalResult();

    if (duetActive && currentSongMeta) {
      const resultB = pitchEngineB.getFinalResult();
      const verdict = duet.verdict(result, resultB);
      if (verdict.contested) {
        // 兩位都真的唱了才亮對戰成績單
        showDuetSettlement(currentSongMeta, result, resultB, verdict);
        return;
      }
      // 對唱模式開著但只有一個人唱（另一支麥克風放在桌上）：
      // 亮那一位的單人成績單，硬要比出勝負只會是誤會
      if (verdict.winner === "b" && resultB.sang) {
        showSettlement(currentSongMeta, resultB);
        return;
      }
      if (verdict.winner !== "a" && !result.sang) {
        window.api.send("SONG_ENDED");
        return;
      }
    }

    if (result.sang && currentSongMeta) {
      // 有真的開口唱才亮結算畫面；純放歌（沒人唱）直接進下一首
      showSettlement(currentSongMeta, result);
    } else {
      window.api.send("SONG_ENDED");
    }
  });

  requestAnimationFrame(renderLoop);
});
