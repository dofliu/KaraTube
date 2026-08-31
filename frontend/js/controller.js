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
  // 已收藏歌曲的 song_id 集合，讓每張歌卡的星星即時反映收藏狀態
  let favoriteIds = new Set();

  // Initialize WebSocket
  window.api.initWebSocket();

  // Load Cached Songs on Launch
  refreshFavoriteIds().then(() => loadCachedRecommendations());

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

  async function refreshFavoriteIds() {
    try {
      const res = await window.api.getFavorites();
      favoriteIds = new Set(res.ids || []);
    } catch (e) {}
  }

  // 我的最愛：使用者主動收藏的常唱歌曲，最新收藏排最前面
  async function loadFavorites() {
    try {
      const res = await window.api.getFavorites();
      const list = res.favorites || [];
      favoriteIds = new Set(res.ids || []);
      libSummary.textContent = `已收藏 ${list.length} 首`;
      if (list.length === 0) {
        searchResults.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--text-muted);">還沒有收藏歌曲<br>點歌卡右上角的 ☆ 星星即可收藏常唱的歌！</div>`;
        return;
      }
      renderSearchResults(
        list.map(f => ({
          ...f,
          id: f.song_id,
          uploader: f.artist,
          thumbnail: f.thumbnail || `https://i.ytimg.com/vi/${f.song_id}/mqdefault.jpg`
        })),
        "⭐ 我的最愛"
      );
    } catch (e) {
      searchResults.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: #ff007f;">收藏清單讀取失敗</div>`;
    }
  }

  // 已唱歷史：今天唱過的歌一鍵再點。與排行不同，同一首唱三次就有三筆。
  async function loadHistory() {
    try {
      const res = await window.api.getHistory(60);
      const list = res.history || [];
      libSummary.textContent = `今天唱了 ${res.today_count || 0} 首 ・ 累計 ${res.total_count || 0} 首`;
      if (list.length === 0) {
        searchResults.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--text-muted);">還沒有演唱紀錄<br>唱完第一首歌就會出現在這裡！</div>`;
        return;
      }
      renderSearchResults(
        list.map(h => ({
          ...h,
          id: h.song_id,
          uploader: h.artist,
          is_cached: true,
          sung_at: h.sung_at,
          thumbnail: h.thumbnail || `https://i.ytimg.com/vi/${h.song_id}/mqdefault.jpg`
        })),
        "🕘 已唱歷史（最新在前）"
      );
    } catch (e) {
      searchResults.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: #ff007f;">已唱歷史讀取失敗</div>`;
    }
  }

  // 快取管理：看每首歌吃多少磁碟、刪除不唱的歌、重新處理壞掉的歌
  async function loadCacheManager() {
    try {
      const res = await window.api.getCacheInfo();
      const list = res.songs || [];
      libSummary.textContent = `${res.song_count || 0} 首 ・ 佔用 ${formatBytes(res.total_bytes || 0)} ・ 磁碟剩餘 ${formatBytes(res.disk_free_bytes || 0)}`;
      if (list.length === 0) {
        searchResults.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--text-muted);">快取是空的<br>點過的歌會存在這裡，第二次點就能秒播！</div>`;
        return;
      }
      const brokenNote = res.incomplete_count > 0
        ? `<div style="grid-column: 1/-1; font-size: 12px; color: var(--accent-yellow);">⚠️ 有 ${res.incomplete_count} 首不完整（處理中斷或失敗的殘留），可直接刪除或重新處理。</div>`
        : "";
      const rowsHtml = list.map(s => {
        const badge = s.complete
          ? `<span class="cache-badge ok">完整</span>`
          : `<span class="cache-badge broken" title="缺少：${(s.missing_files || []).join(", ")}">不完整</span>`;
        const thumb = s.thumbnail || `https://i.ytimg.com/vi/${s.song_id}/mqdefault.jpg`;
        const playBtn = s.complete
          ? `<button class="btn btn-primary" onclick="window.addSong('${s.song_id}', '${escapeAttr(s.title)}', '${escapeAttr(s.artist)}', '${thumb}', false)">🎤 點歌</button>`
          : "";
        return `
          <div class="cache-row">
            <img class="cache-row-thumb" src="${thumb}" loading="lazy" onerror="this.style.visibility='hidden'">
            <div class="cache-row-info">
              <div class="cache-row-title" title="${s.title || s.song_id}">${s.title || s.song_id}</div>
              <div class="cache-row-meta">${s.artist ? s.artist + " ・ " : ""}${formatBytes(s.size_bytes)} ${badge}</div>
            </div>
            <div class="cache-row-actions">
              ${playBtn}
              <button class="btn btn-secondary" onclick="window.reprocessSong('${s.song_id}', '${escapeAttr(s.title)}')" title="砍掉快取重新下載、分離、對字幕">🔁 重新處理</button>
              <button class="btn btn-secondary cache-del-btn" onclick="window.deleteCachedSong('${s.song_id}', '${escapeAttr(s.title)}')" title="刪除快取釋放磁碟空間">🗑️</button>
            </div>
          </div>`;
      }).join("");
      searchResults.innerHTML = `<div style="grid-column: 1/-1; font-size: 16px; font-weight: 700; color: var(--accent-cyan); margin-bottom: 8px;">🗂️ 快取管理</div>` + brokenNote + rowsHtml;
    } catch (e) {
      searchResults.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: #ff007f;">快取清單讀取失敗</div>`;
    }
  }

  window.deleteCachedSong = async (id, title) => {
    if (!confirm(`確定刪除「${title || id}」的快取嗎？\n下次再點這首會重新下載與處理。`)) return;
    try {
      await window.api.deleteCachedSong(id);
      showNotification("🗑️ 已刪除快取");
      loadCacheManager();
    } catch (e) {
      alert("刪除失敗: " + e.message);
    }
  };

  window.reprocessSong = async (id, title) => {
    if (!confirm(`重新處理「${title || id}」？\n會刪掉現有快取並重跑下載、AI 分離與字幕對齊（需要幾分鐘）。`)) return;
    try {
      await window.api.reprocessSong(id);
      showNotification("🔁 已排入重新處理，完成後自動就緒");
      loadCacheManager();
    } catch (e) {
      alert("重新處理失敗: " + e.message);
    }
  };

  window.retryQueueItem = async (queueId) => {
    try {
      await window.api.retryQueueItem(queueId);
      showNotification("🔁 重試中…");
    } catch (e) {
      alert("重試失敗: " + e.message);
    }
  };

  function switchLibrary(which) {
    libTabs.forEach(t => t.classList.toggle("active", t.dataset.lib === which));
    if (which === "rankings") loadRankings();
    else if (which === "favorites") loadFavorites();
    else if (which === "history") loadHistory();
    else if (which === "cache") loadCacheManager();
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
        : (s.sung_at
          ? `<div class="play-count">🕘 唱於 ${formatSungAt(s.sung_at)}</div>`
          : "");
      const isFav = favoriteIds.has(s.id);
      const favBtn = `<button class="fav-btn ${isFav ? "faved" : ""}" data-song-id="${s.id}"
        onclick="window.toggleFavorite(event, '${s.id}', '${escapeAttr(s.title)}', '${escapeAttr(s.uploader || s.artist)}', '${s.thumbnail}')"
        title="${isFav ? "取消收藏" : "收藏到我的最愛"}">${isFav ? "⭐" : "☆"}</button>`;
      return `
        <div class="song-card">
          <div class="song-thumb-wrapper">
            <img class="song-thumb" src="${s.thumbnail}" alt="${s.title}" loading="lazy">
            ${rankBadge}
            ${favBtn}
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

  // 收藏 / 取消收藏。星星就地更新，不重畫整個清單，
  // 這樣在搜尋結果頁收藏不會把捲動位置弄丟。
  window.toggleFavorite = async (event, id, title, artist, thumbnail) => {
    event.stopPropagation();
    try {
      const res = await window.api.toggleFavorite({ id, title, artist, thumbnail });
      if (res.favorited) favoriteIds.add(id);
      else favoriteIds.delete(id);
      document.querySelectorAll(`.fav-btn[data-song-id="${id}"]`).forEach(btn => {
        btn.classList.toggle("faved", res.favorited);
        btn.textContent = res.favorited ? "⭐" : "☆";
        btn.title = res.favorited ? "取消收藏" : "收藏到我的最愛";
      });
      showNotification(res.favorited ? "⭐ 已收藏到我的最愛！" : "已從我的最愛移除");
      // 我的最愛分頁裡取消收藏，該首要從清單消失
      const activeTab = document.querySelector(".lib-tab.active");
      if (!res.favorited && activeTab && activeTab.dataset.lib === "favorites") {
        loadFavorites();
      }
    } catch (e) {
      alert("收藏失敗: " + e.message);
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

      // 處理失敗的歌給一顆重試鈕：網路斷線、影片暫時抓不到之類的暫時性錯誤，
      // 重跑一次通常就過了，不用重新搜尋點歌。
      const retryBtn = item.status === "ERROR"
        ? `<button class="btn btn-secondary btn-icon" style="width: 32px; height: 32px;" onclick="window.retryQueueItem('${item.queue_id}')" title="重新處理">🔁</button>`
        : "";

      return `
        <div class="queue-item">
          <img class="queue-item-thumb" src="${item.thumbnail}">
          <div class="queue-item-info">
            <div class="queue-item-title" title="${item.title}">${item.title}</div>
            <div class="queue-item-status">${statusIndicator}</div>
          </div>
          <div class="queue-item-actions">
            ${retryBtn}
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

  // 舞台端唱完一首會廣播結算結果，點歌台同步顯示，讓包廂裡每支手機都看得到
  window.api.on("SCORE_FINAL", (msg) => {
    const r = msg.data || {};
    if (!r.title && !r.score) return;
    const bestPart = r.is_new_best ? " ・ 🎉 刷新個人最佳！" : "";
    showNotification(`🏁 ${r.title || "演唱結束"}：${r.score} 分（${r.grade || "-"}）${bestPart}`);
  });

  // Helpers
  function formatSungAt(iso) {
    if (!iso) return "";
    // sung_at 是伺服器本地時間，所以「今天」也要用本地日期算，不能用 UTC
    const now = new Date();
    const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
    const datePart = iso.slice(0, 10);
    const timePart = iso.slice(11, 16);
    return datePart === today ? `今天 ${timePart}` : `${datePart} ${timePart}`;
  }

  function formatBytes(bytes) {
    if (!bytes || bytes <= 0) return "0 B";
    const units = ["B", "KB", "MB", "GB", "TB"];
    const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
    const v = bytes / Math.pow(1024, i);
    return `${v >= 100 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
  }

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
