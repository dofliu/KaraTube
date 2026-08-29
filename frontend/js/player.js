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

  // Initializing Engines
  const karaokeRenderer = new KaraokeRenderer(subtitlesContainer);
  const pitchEngine = new PitchEngine(pitchCanvas, scoreValueEl, comboValueEl);
  const clock = new MediaClock(audioInst);

  const OFFSET_STORAGE_KEY = "karatube_lyric_offset_ms";
  const PITCH_STORAGE_KEY = "karatube_show_pitch";
  const INPUT_DEV_KEY = "karatube_input_device";
  const OUTPUT_DEV_KEY = "karatube_output_device";

  let currentSongId = null;
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

  let outputLatency = 0.05;
  let syncToastTimer = null;
  let lastVocResync = 0;
  let lastVideoResync = 0;

  // Initialize Web Audio Engine
  window.audioEngine.setupTracks(audioInst, audioVoc);

  // Setup AudioContext & Mic on user click
  async function unlockAudio() {
    if (isAudioUnlocked) return;
    window.audioEngine.initContext();
    const micOk = await window.audioEngine.startMicrophone();
    if (micOk && window.audioEngine.micAnalyser) {
      pitchEngine.setAnalyser(window.audioEngine.micAnalyser);
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
    }
  });

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
      `・ 自動補償 ${(outputLatency * 1000).toFixed(0)}ms</span>`);
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

      if (savedIn) await window.audioEngine.setInputDevice(savedIn);
      if (savedOut && canSelectOutput) await window.audioEngine.setOutputDevice(savedOut);
    } catch (e) {
      console.warn("列舉音訊裝置失敗:", e);
    }
  }

  inputDeviceSelect.addEventListener("change", async (e) => {
    const id = e.target.value;
    const ok = await window.audioEngine.setInputDevice(id);
    try { localStorage.setItem(INPUT_DEV_KEY, id); } catch (err) { }
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
    if (open) refreshAudioDevices();
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

    window.audioEngine.setVocalVolume(state.vocal_volume !== undefined ? state.vocal_volume : 0.0);
    window.audioEngine.setMusicVolume(state.music_volume !== undefined ? state.music_volume : 1.0);
    window.audioEngine.setMicVolume(state.mic_volume !== undefined ? state.mic_volume : 1.0);
    window.audioEngine.setMicReverb(state.mic_reverb !== undefined ? state.mic_reverb : 0.25);
    window.audioEngine.setMicEcho(state.mic_echo !== undefined ? state.mic_echo : 0.15);
    window.audioEngine.setMicEchoRepeat(state.mic_echo_repeat !== undefined ? state.mic_echo_repeat : 0.4);
    window.audioEngine.setMicEchoTime(state.mic_echo_time_ms !== undefined ? state.mic_echo_time_ms : 280);
    window.audioEngine.setMicTone(state.mic_tone !== undefined ? state.mic_tone : 0.4);
    if (state.sing_mode !== undefined) applySingMode(state.sing_mode);

    // 點歌台（含手機）調整字幕同步時同步套用，但不要再廣播回去造成迴圈
    if (state.lyric_offset_ms !== undefined && state.lyric_offset_ms !== lyricOffsetMs) {
      setLyricOffset(state.lyric_offset_ms, false);
    }

    if (state.show_pitch !== undefined) {
      setShowPitch(state.show_pitch, false);
    }

    if (song && song.status === "READY") {
      if (song.song_id !== currentSongId) {
        loadAndPlaySong(song);
      } else {
        if (state.is_playing && (audioInst.paused || videoBg.paused)) {
          playMedia();
        } else if (!state.is_playing && (!audioInst.paused || !videoBg.paused)) {
          pauseMedia();
        }
      }
    } else if (!song) {
      currentSongId = null;
      titleEl.textContent = "KaraTube 伴唱系統";
      artistEl.textContent = "請使用點歌台或掃描 QR Code 點播歌曲";
      pauseMedia();
      karaokeRenderer.setLyrics([]);
      pitchEngine.setPitchData(null);
    }
  }

  async function loadAndPlaySong(song) {
    currentSongId = song.song_id;
    titleEl.textContent = song.title;
    artistEl.textContent = song.artist || "YouTube Music";

    const songBaseUrl = `/media/songs/${song.song_id}`;

    videoBg.src = `${songBaseUrl}/original_video.mp4`;
    audioInst.src = `${songBaseUrl}/instrumental.mp3`;
    audioVoc.src = `${songBaseUrl}/vocals.mp3`;

    const [lyrics, pitch] = await Promise.all([
      window.api.getLyrics(song.song_id),
      window.api.getPitch(song.song_id)
    ]);

    karaokeRenderer.setLyrics(lyrics);
    pitchEngine.setPitchData(pitch);

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
    videoBg.currentTime = 0;
    audioInst.currentTime = 0;
    audioVoc.currentTime = 0;
    clock.seekedTo(0);
    playMedia();
  }

  // Master Clock & 60 FPS Render Loop
  function renderLoop() {
    requestAnimationFrame(renderLoop);

    // 伴奏軌是唯一的主時鐘。人聲軌與影片都只是跟隨者，
    // 不能拿它們的 currentTime 回頭修正字幕，否則會互相拉扯。
    if (audioInst.paused || !(audioInst.currentTime > 0)) return;

    const audioTime = clock.now();
    const duration = audioInst.duration || 0;

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
    if (showPitch) pitchEngine.updateAndRender(displayTime);

    if (nowMs - lastTimeBroadcast > 400) {
      lastTimeBroadcast = nowMs;
      window.api.send("TIME_UPDATE", { currentTime: audioTime, duration: duration });
    }
  }

  audioInst.addEventListener("seeked", () => clock.seekedTo(audioInst.currentTime));
  audioInst.addEventListener("playing", () => clock.seekedTo(audioInst.currentTime));

  audioInst.addEventListener("ended", () => {
    console.log("Song audio finished. Advancing to next song...");
    window.api.send("SONG_ENDED");
  });

  requestAnimationFrame(renderLoop);
});
