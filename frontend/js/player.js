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
    // 響度目標或開關改了，正在唱的這首要立刻跟上，不用等下一首
    if (currentSongId) applyLoudness(currentSongId);
  });

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
      worst_section: result.worst_section ? result.worst_section.label : ""
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
    renderSectionBreakdown(result);
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
    } catch (e) {
      console.warn("結算成績上傳失敗:", e);
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
    pitchEngine.tick(displayTime);
    if (showPitch) pitchEngine.updateAndRender(displayTime);

    if (nowMs - lastTimeBroadcast > 400) {
      lastTimeBroadcast = nowMs;
      window.api.send("TIME_UPDATE", { currentTime: audioTime, duration: duration });
    }
  }

  audioInst.addEventListener("seeked", () => clock.seekedTo(audioInst.currentTime));
  audioInst.addEventListener("playing", () => clock.seekedTo(audioInst.currentTime));

  audioInst.addEventListener("ended", () => {
    console.log("Song audio finished.");
    const result = pitchEngine.getFinalResult();
    if (result.sang && currentSongMeta) {
      // 有真的開口唱才亮結算畫面；純放歌（沒人唱）直接進下一首
      showSettlement(currentSongMeta, result);
    } else {
      window.api.send("SONG_ENDED");
    }
  });

  requestAnimationFrame(renderLoop);
});
