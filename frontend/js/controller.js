// KaraTube Console & Mobile Remote Controller Logic
document.addEventListener("DOMContentLoaded", () => {
  const searchInput = document.getElementById("searchInput");
  const searchBtn = document.getElementById("searchBtn");
  const searchResults = document.getElementById("searchResults");
  const queueList = document.getElementById("queueList");
  const nowPlayingTitle = document.getElementById("nowPlayingTitle");
  const nowPlayingArtist = document.getElementById("nowPlayingArtist");
  const nowPlayingThumb = document.getElementById("nowPlayingThumb");
  const progressBar = document.getElementById("progressBar");
  const timeCurrent = document.getElementById("timeCurrent");
  const timeDuration = document.getElementById("timeDuration");

  // Control Deck Elements
  const playPauseBtn = document.getElementById("playPauseBtn");
  const skipBtn = document.getElementById("skipBtn");
  const restartBtn = document.getElementById("restartBtn");
  const vocalSlider = document.getElementById("vocalSlider");
  const vocalModeText = document.getElementById("vocalModeText");
  const keyDownBtn = document.getElementById("keyDownBtn");
  const keyUpBtn = document.getElementById("keyUpBtn");
  const keyResetBtn = document.getElementById("keyResetBtn");
  const keyValueText = document.getElementById("keyValueText");
  const micVolumeSlider = document.getElementById("micVolumeSlider");
  const micVolumeText = document.getElementById("micVolumeText");
  const micReverbSlider = document.getElementById("micReverbSlider");
  const micReverbText = document.getElementById("micReverbText");
  const micEchoSlider = document.getElementById("micEchoSlider");
  const micEchoText = document.getElementById("micEchoText");
  const micEchoRepeatSlider = document.getElementById("micEchoRepeatSlider");
  const micEchoRepeatText = document.getElementById("micEchoRepeatText");
  const micEchoTimeSlider = document.getElementById("micEchoTimeSlider");
  const micEchoTimeText = document.getElementById("micEchoTimeText");
  const micToneSlider = document.getElementById("micToneSlider");
  const micToneText = document.getElementById("micToneText");
  const modeSoloBtn = document.getElementById("modeSoloBtn");
  const modePartyBtn = document.getElementById("modePartyBtn");
  const modeHint = document.getElementById("modeHint");
  const dryVoiceBtn = document.getElementById("dryVoiceBtn");
  const mixerBtn = document.getElementById("mixerBtn");
  const mixerModal = document.getElementById("mixerModal");
  const closeMixerBtn = document.getElementById("closeMixerBtn");
  const lyricOffsetSlider = document.getElementById("lyricOffsetSlider");
  const lyricOffsetText = document.getElementById("lyricOffsetText");
  const musicVolumeSlider = document.getElementById("musicVolumeSlider");
  const musicVolumeText = document.getElementById("musicVolumeText");
  const pitchToggleBtn = document.getElementById("pitchToggleBtn");
  const libTabs = document.querySelectorAll(".lib-tab");
  const libSummary = document.getElementById("libSummary");

  // QR Modal Elements
  const qrModal = document.getElementById("qrModal");
  const qrBtn = document.getElementById("qrBtn");
  const closeQrBtn = document.getElementById("closeQrBtn");
  const qrImg = document.getElementById("qrImg");
  const qrUrlText = document.getElementById("qrUrlText");

  let currentKeyShift = 0;
  let isPlaying = false;
  let showPitch = true;

  // Initialize WebSocket
  window.api.initWebSocket();

  // Load Cached Songs on Launch
  loadCachedRecommendations();

  // Search Action
  searchBtn.addEventListener("click", () => performSearch());
  searchInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") performSearch();
  });

  async function performSearch() {
    const q = searchInput.value.trim();
    if (!q) return;

    searchResults.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--accent-cyan);">🔍 正在搜尋 YouTube 與 YouTube Music...</div>`;
    try {
      const res = await window.api.search(q);
      libTabs.forEach(t => t.classList.remove("active"));
      libSummary.textContent = `搜尋「${q}」`;
      renderSearchResults(res.results || []);
    } catch (e) {
      searchResults.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: #ff007f;">搜尋失敗，請檢查網路連線</div>`;
    }
  }

  async function loadCachedRecommendations() {
    try {
      const res = await window.api.getCachedSongs();
      libSummary.textContent = `已快取 ${(res.songs || []).length} 首`;
      if (res.songs && res.songs.length > 0) {
        renderSearchResults(res.songs, "📚 本地已快取歌曲 (免等待秒播)");
      }
    } catch (e) {}
  }

  // 熱門點唱排行：依實際上台演唱次數排序，點一下就能再點一次同一首
  async function loadRankings() {
    try {
      const res = await window.api.getRankings(24);
      const list = res.rankings || [];
      libSummary.textContent = `累計點唱 ${res.total_plays || 0} 次`;
      if (list.length === 0) {
        searchResults.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--text-muted);">還沒有人唱過歌，點播第一首吧！</div>`;
        return;
      }
      renderSearchResults(
        list.map(r => ({
          ...r,
          id: r.song_id,
          uploader: r.artist,
          is_cached: true,
          thumbnail: r.thumbnail || `https://i.ytimg.com/vi/${r.song_id}/mqdefault.jpg`
        })),
        "🏆 熱門點唱排行"
      );
    } catch (e) {
      searchResults.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: #ff007f;">排行讀取失敗</div>`;
    }
  }

  function switchLibrary(which) {
    libTabs.forEach(t => t.classList.toggle("active", t.dataset.lib === which));
    if (which === "rankings") loadRankings();
    else loadCachedRecommendations();
  }

  libTabs.forEach(tab => {
    tab.addEventListener("click", () => switchLibrary(tab.dataset.lib));
  });

  function renderSearchResults(songs, sectionTitle = "") {
    if (!songs || songs.length === 0) {
      searchResults.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--text-muted);">未找到相符歌曲</div>`;
      return;
    }

    let headerHtml = sectionTitle ? `<div style="grid-column: 1/-1; font-size: 16px; font-weight: 700; color: var(--accent-cyan); margin-bottom: 8px;">${sectionTitle}</div>` : "";

    const cardsHtml = songs.map(s => {
      const isCachedBadge = s.is_cached ? `<div class="cached-badge">⚡ 快取秒播</div>` : "";
      const rankBadge = s.rank
        ? `<div class="rank-badge ${s.rank <= 3 ? "" : "rank-other"}">${s.rank <= 3 ? ["🥇","🥈","🥉"][s.rank - 1] : "#" + s.rank}</div>`
        : "";
      const playCount = s.plays
        ? `<div class="play-count">🎤 已點唱 ${s.plays} 次${s.last_played ? " ・ 最近 " + s.last_played.slice(0, 10) : ""}</div>`
        : "";
      return `
        <div class="song-card">
          <div class="song-thumb-wrapper">
            <img class="song-thumb" src="${s.thumbnail}" alt="${s.title}" loading="lazy">
            ${rankBadge}
            ${isCachedBadge}
            <span class="song-duration">${s.duration_string || (s.duration ? formatTime(s.duration) : '')}</span>
          </div>
          <div class="song-info">
            <div class="song-title" title="${s.title}">${s.title}</div>
            <div class="song-artist">${s.uploader || s.artist || 'YouTube'}</div>
            ${playCount}
            <div class="song-actions">
              <button class="btn btn-primary" onclick="window.addSong('${s.id}', '${escapeAttr(s.title)}', '${escapeAttr(s.uploader || s.artist)}', '${s.thumbnail}', false)">
                🎤 點歌
              </button>
              <button class="btn btn-pink" onclick="window.addSong('${s.id}', '${escapeAttr(s.title)}', '${escapeAttr(s.uploader || s.artist)}', '${s.thumbnail}', true)">
                ⚡ 插播
              </button>
            </div>
          </div>
        </div>
      `;
    }).join("");

    searchResults.innerHTML = headerHtml + cardsHtml;
  }

  // Global Add Song Action
  window.addSong = async (id, title, artist, thumbnail, priority) => {
    try {
      await window.api.addToQueue({ id, title, artist, thumbnail }, priority);
      // Brief feedback toast
      showNotification(priority ? "⚡ 已成功插播到下一首！" : "🎤 已加入點歌佇列！");
    } catch (e) {
      alert("點歌失敗: " + e.message);
    }
  };

  // State Updates from WebSocket
  window.api.on("STATE_UPDATE", (msg) => {
    const state = msg.data;
    renderQueue(state);
    updateDeckControls(state);
  });

  // Time Updates from Stage Player
  window.api.on("TIME_UPDATE", (msg) => {
    const cur = msg.currentTime || 0;
    const dur = msg.duration || 1;
    timeCurrent.textContent = formatTime(cur);
    timeDuration.textContent = formatTime(dur);
    const pct = Math.min(100, (cur / dur) * 100);
    progressBar.style.width = `${pct}%`;
  });

  function renderQueue(state) {
    const cur = state.current_song;
    if (cur) {
      nowPlayingTitle.textContent = cur.title;
      nowPlayingArtist.textContent = cur.artist || "YouTube Music";
      nowPlayingThumb.src = cur.thumbnail || "https://img.youtube.com/vi/default.jpg";
    } else {
      nowPlayingTitle.textContent = "尚未播放歌曲";
      nowPlayingArtist.textContent = "請從左側點播歌曲";
      nowPlayingThumb.src = "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='54' height='38' fill='%23222'></svg>";
      timeCurrent.textContent = "00:00";
      timeDuration.textContent = "00:00";
      progressBar.style.width = "0%";
    }

    // Render upcoming queue
    const queue = state.queue || [];
    if (queue.length === 0) {
      queueList.innerHTML = `<div style="text-align: center; color: var(--text-muted); padding: 30px 10px; font-size: 13px;">點歌佇列為空<br>快搜尋並點播想唱的歌吧！</div>`;
      return;
    }

    queueList.innerHTML = queue.map((item, idx) => {
      let statusIndicator = "";
      if (item.status === "READY") {
        statusIndicator = `<span style="color: #10b981;">● 就緒</span>`;
      } else if (item.status === "ERROR") {
        statusIndicator = `<span style="color: #ef4444;">● 處理失敗</span>`;
      } else {
        statusIndicator = `<span style="color: var(--accent-cyan); animation: pulse 1.5s infinite;">⏳ ${item.status_text || '處理中'} (${item.progress}%)</span>`;
      }

      return `
        <div class="queue-item">
          <img class="queue-item-thumb" src="${item.thumbnail}">
          <div class="queue-item-info">
            <div class="queue-item-title" title="${item.title}">${item.title}</div>
            <div class="queue-item-status">${statusIndicator}</div>
          </div>
          <div class="queue-item-actions">
            <button class="btn btn-secondary btn-icon" style="width: 32px; height: 32px;" onclick="window.api.removeQueueItem('${item.queue_id}')" title="刪除">
              ✕
            </button>
          </div>
        </div>
      `;
    }).join("");
  }

  function updateDeckControls(state) {
    isPlaying = state.is_playing;
    playPauseBtn.textContent = isPlaying ? "⏸ 暫停" : "▶ 播放";

    if (state.vocal_volume !== undefined) {
      vocalSlider.value = state.vocal_volume;
      updateVocalLabel(state.vocal_volume);
    }

    // 舞台端用鍵盤調過字幕同步時，這裡的滑桿要跟著走
    if (state.lyric_offset_ms !== undefined &&
        parseInt(lyricOffsetSlider.value, 10) !== state.lyric_offset_ms) {
      lyricOffsetSlider.value = state.lyric_offset_ms;
      updateLyricOffsetLabel(state.lyric_offset_ms);
    }

    if (state.music_volume !== undefined &&
        parseFloat(musicVolumeSlider.value) !== state.music_volume) {
      musicVolumeSlider.value = state.music_volume;
      updateMusicVolumeLabel(state.music_volume);
    }

    if (state.show_pitch !== undefined && state.show_pitch !== showPitch) {
      updatePitchToggleLabel(state.show_pitch);
    }

    // 麥克風效果：手機端與桌機端可能同時開著，任何一邊改都要同步回來
    syncSlider(micVolumeSlider, micVolumeText, state.mic_volume, true);
    syncSlider(micReverbSlider, micReverbText, state.mic_reverb, true);
    syncSlider(micEchoSlider, micEchoText, state.mic_echo, true);
    syncSlider(micEchoRepeatSlider, micEchoRepeatText, state.mic_echo_repeat, true);
    syncSlider(micToneSlider, micToneText, state.mic_tone, true);
    if (state.sing_mode !== undefined && state.sing_mode !== singMode) {
      updateSingModeUI(state.sing_mode);
    }
    if (state.mic_echo_time_ms !== undefined &&
        parseInt(micEchoTimeSlider.value, 10) !== state.mic_echo_time_ms) {
      micEchoTimeSlider.value = state.mic_echo_time_ms;
      micEchoTimeText.textContent = `${state.mic_echo_time_ms} ms`;
    }
  }

  function syncSlider(slider, label, value, asPercent) {
    if (value === undefined || parseFloat(slider.value) === value) return;
    slider.value = value;
    if (label) label.textContent = asPercent ? `${Math.round(value * 100)}%` : String(value);
  }

  // Deck Controls Handlers
  playPauseBtn.addEventListener("click", () => {
    window.api.updateControl({ is_playing: !isPlaying });
  });

  skipBtn.addEventListener("click", () => {
    window.api.skipSong();
  });

  restartBtn.addEventListener("click", () => {
    window.api.restartSong();
  });

  // Vocal / Accompaniment Slider
  vocalSlider.addEventListener("input", (e) => {
    const val = parseFloat(e.target.value);
    updateVocalLabel(val);
    window.api.updateControl({ vocal_volume: val });
  });

  function updateVocalLabel(val) {
    if (val === 0) vocalModeText.textContent = "純伴奏 (KTV模式)";
    else if (val >= 0.9) vocalModeText.textContent = "原唱導唱 (100%)";
    else vocalModeText.textContent = `導唱 ${Math.round(val * 100)}%`;
  }

  // Key Shift (+/- Semitones)
  keyUpBtn.addEventListener("click", () => {
    if (currentKeyShift < 6) {
      currentKeyShift++;
      updateKeyShift();
    }
  });

  keyDownBtn.addEventListener("click", () => {
    if (currentKeyShift > -6) {
      currentKeyShift--;
      updateKeyShift();
    }
  });

  keyResetBtn.addEventListener("click", () => {
    currentKeyShift = 0;
    updateKeyShift();
  });

  function updateKeyShift() {
    keyValueText.textContent = (currentKeyShift > 0 ? `+${currentKeyShift}` : `${currentKeyShift}`);
    window.api.updateControl({ pitch_shift: currentKeyShift });
  }

  // --- 音量與麥克風效果 ---
  // 殘響、回音音量、回音重複、回音間隔各自獨立。
  // 舊版把「回音音量」和「重複次數」綁在同一個增益節點上，
  // 想調小聲就一定連重複次數一起變少，而且根本沒有 UI 接得到。
  function pct(el, v) { el.textContent = `${Math.round(v * 100)}%`; }

  function bindPercentSlider(slider, label, key, onLocal) {
    slider.addEventListener("input", (e) => {
      const v = parseFloat(e.target.value);
      pct(label, v);
      if (onLocal) onLocal(v);
      window.api.updateControl({ [key]: v });
    });
  }

  bindPercentSlider(micVolumeSlider, micVolumeText, "mic_volume", (v) => {
    // 拉到 0 等於切斷監聽，是止住嘯叫最快的手段，用顏色提示
    micVolumeText.style.color = v === 0 ? "var(--accent-pink)" : "var(--accent-cyan)";
  });
  bindPercentSlider(micReverbSlider, micReverbText, "mic_reverb");
  bindPercentSlider(micEchoSlider, micEchoText, "mic_echo");
  bindPercentSlider(micEchoRepeatSlider, micEchoRepeatText, "mic_echo_repeat");
  bindPercentSlider(micToneSlider, micToneText, "mic_tone");

  // 演唱模式。單人＝人聲不進喇叭，是筆電內建麥克風唯一不會嘯叫的用法。
  let singMode = "solo";
  function updateSingModeUI(mode) {
    singMode = mode === "party" ? "party" : "solo";
    const isParty = singMode === "party";
    modeSoloBtn.classList.toggle("active", !isParty);
    modePartyBtn.classList.toggle("active", isParty);
    modeHint.innerHTML = isParty
      ? "多人模式：人聲從喇叭放出。<b>請務必使用外接喇叭</b>，筆電喇叭與內建麥克風同機殼，震動會經外殼傳回麥克風。"
      : "單人模式：人聲不進喇叭，不可能有回授，評分照常。多人模式需搭配外接喇叭。";
  }
  modeSoloBtn.addEventListener("click", () => {
    updateSingModeUI("solo");
    window.api.updateControl({ sing_mode: "solo" });
  });
  modePartyBtn.addEventListener("click", () => {
    updateSingModeUI("party");
    window.api.updateControl({ sing_mode: "party" });
  });

  micEchoTimeSlider.addEventListener("input", (e) => {
    const ms = parseInt(e.target.value, 10);
    micEchoTimeText.textContent = `${ms} ms`;
    window.api.updateControl({ mic_echo_time_ms: ms });
  });

  // 一鍵乾聲：現場破音或嘯叫時最快的止血按鈕
  dryVoiceBtn.addEventListener("click", () => {
    micReverbSlider.value = 0; pct(micReverbText, 0);
    micEchoSlider.value = 0; pct(micEchoText, 0);
    window.api.updateControl({ mic_reverb: 0, mic_echo: 0 });
    showNotification("🎙️ 已切換為乾聲（殘響與回音關閉）");
  });

  // --- 調音台開關 ---
  mixerBtn.addEventListener("click", () => mixerModal.classList.add("open"));
  closeMixerBtn.addEventListener("click", () => mixerModal.classList.remove("open"));
  mixerModal.addEventListener("click", (e) => {
    if (e.target === mixerModal) mixerModal.classList.remove("open");
  });

  // 字幕同步微調。正值＝字幕延後，用來補喇叭/藍牙的輸出延遲與該首歌的殘差。
  function updateLyricOffsetLabel(ms) {
    const sign = ms > 0 ? "+" : "";
    lyricOffsetText.textContent = `${sign}${ms} ms`;
    lyricOffsetText.style.color = ms === 0 ? "var(--accent-cyan)" : "var(--accent-yellow)";
  }

  lyricOffsetSlider.addEventListener("input", (e) => {
    const ms = parseInt(e.target.value, 10);
    updateLyricOffsetLabel(ms);
    window.api.updateControl({ lyric_offset_ms: ms });
  });

  // 音樂（伴奏）音量。麥克風走另一條增益路徑，所以不受影響。
  function updateMusicVolumeLabel(v) {
    musicVolumeText.textContent = `${Math.round(v * 100)}%`;
    musicVolumeText.style.color = v === 0 ? "var(--accent-pink)" : "var(--accent-cyan)";
  }

  musicVolumeSlider.addEventListener("input", (e) => {
    const v = parseFloat(e.target.value);
    updateMusicVolumeLabel(v);
    window.api.updateControl({ music_volume: v });
  });

  // 音準導唱線顯示切換（舞台端也可以按 P）
  function updatePitchToggleLabel(on) {
    showPitch = on;
    pitchToggleBtn.classList.toggle("off", !on);
    pitchToggleBtn.textContent = on ? "顯示中" : "已隱藏";
    pitchToggleBtn.title = on ? "點擊隱藏舞台的音準導唱線" : "點擊顯示舞台的音準導唱線";
  }

  pitchToggleBtn.addEventListener("click", () => {
    updatePitchToggleLabel(!showPitch);
    window.api.updateControl({ show_pitch: showPitch });
  });

  lyricOffsetSlider.addEventListener("dblclick", () => {
    lyricOffsetSlider.value = 0;
    updateLyricOffsetLabel(0);
    window.api.updateControl({ lyric_offset_ms: 0 });
  });

  // Sound FX Buttons
  document.querySelectorAll(".sfx-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const effect = btn.dataset.effect;
      window.api.triggerSoundEffect(effect);
    });
  });

  // QR Modal
  qrBtn.addEventListener("click", async () => {
    const info = await window.api.getServerInfo();
    qrImg.src = "/api/qrcode";
    qrUrlText.textContent = info.web_url;
    qrModal.classList.add("open");
  });

  closeQrBtn.addEventListener("click", () => {
    qrModal.classList.remove("open");
  });

  // Helpers
  function formatTime(seconds) {
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  }

  function escapeAttr(str) {
    if (!str) return "";
    return str.replace(/'/g, "\\'").replace(/"/g, "&quot;");
  }

  function showNotification(msg) {
    const div = document.createElement("div");
    div.style.cssText = `
      position: fixed; top: 20px; right: 20px; z-index: 9999;
      background: linear-gradient(135deg, var(--accent-cyan), #0077b6);
      color: #000; font-weight: 700; padding: 12px 20px;
      border-radius: 12px; box-shadow: 0 4px 20px rgba(0,240,255,0.4);
      animation: fadeIn 0.3s ease;
    `;
    div.textContent = msg;
    document.body.appendChild(div);
    setTimeout(() => div.remove(), 2500);
  }
});
