// KaraTube Console & Mobile Remote Controller Logic
document.addEventListener("DOMContentLoaded", () => {
  const searchInput = document.getElementById("searchInput");
  const searchBtn = document.getElementById("searchBtn");
  const searchResults = document.getElementById("searchResults");
  const queueList = document.getElementById("queueList");
  // 公平輪唱（排麥輪序）
  const rotationToggle = document.getElementById("rotationToggle");
  const rotationBar = document.getElementById("rotationBar");
  const rotationLineEl = document.getElementById("rotationLine");
  const rotationHintEl = document.getElementById("rotationHint");
  const rotationResetBtn = document.getElementById("rotationResetBtn");
  const quotaLabelBtn = document.getElementById("quotaLabel");
  const quotaDownBtn = document.getElementById("quotaDownBtn");
  const quotaUpBtn = document.getElementById("quotaUpBtn");
  const quotaBar = document.getElementById("quotaBar");
  const quotaLineEl = document.getElementById("quotaLine");
  // 自動接歌（沒有人點歌時，機器自己接一首）與 🎲 來一首
  const autofillLineEl = document.getElementById("autofillLine");
  const randomPickBtn = document.getElementById("randomPickBtn");
  // 包廂計時（歡唱時間）
  const roomTimerBox = document.getElementById("roomTimer");
  const roomClockBtn = document.getElementById("roomClockBtn");
  const roomExtendBtn = document.getElementById("roomExtendBtn");
  const roomPauseBtn = document.getElementById("roomPauseBtn");
  const roomBar = document.getElementById("roomBar");
  const roomLineEl = document.getElementById("roomLine");
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
  const keyOrigBtn = document.getElementById("keyOrigBtn");
  const keyMaleBtn = document.getElementById("keyMaleBtn");
  const keyFemaleBtn = document.getElementById("keyFemaleBtn");
  const nickBtn = document.getElementById("nickBtn");
  const nickText = document.getElementById("nickText");
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
  // 練唱模式（A-B 循環）
  const progressTrack = document.getElementById("progressTrack");
  const loopRangeMark = document.getElementById("loopRangeMark");
  const loopSetABtn = document.getElementById("loopSetABtn");
  const loopSetBBtn = document.getElementById("loopSetBBtn");
  const loopToggleBtn = document.getElementById("loopToggleBtn");
  const loopClearBtn = document.getElementById("loopClearBtn");
  const loopChorusBtn = document.getElementById("loopChorusBtn");
  const loopAText = document.getElementById("loopAText");
  const loopBText = document.getElementById("loopBText");
  const loopHint = document.getElementById("loopHint");
  const sectionList = document.getElementById("sectionList");
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
  // 舞台端每 400ms 回報一次播放位置；點歌台自己不播音樂，這是唯一的時間來源
  let lastKnownTime = 0;
  let lastKnownDuration = 0;
  // 練唱 A-B 區間（共享狀態的鏡像）與這首歌的曲式分析快取
  let loopState = { enabled: false, start: null, end: null };
  let currentSongId = null;
  let sectionCache = { songId: null, data: null };
  // 已收藏歌曲的 song_id 集合，讓每張歌卡的星星即時反映收藏狀態
  let favoriteIds = new Set();

  // --- 多人包廂暱稱 ---
  // 每台裝置（桌機點歌台、每支掃碼進來的手機）各自記自己的暱稱，
  // 點歌時一起送出，佇列與舞台片頭卡就看得到「這首是誰點的」。
  const NICK_STORAGE_KEY = "karatube_nickname";
  let nickname = "";
  try { nickname = (localStorage.getItem(NICK_STORAGE_KEY) || "").trim(); } catch (e) { }

  function updateNickUI() {
    if (nickText) nickText.textContent = nickname ? nickname : "設定暱稱";
  }

  function promptNickname() {
    const input = prompt("輸入你的暱稱（顯示在點歌佇列與舞台片頭卡）：", nickname);
    if (input === null) return; // 按取消不動
    nickname = input.trim().slice(0, 24);
    try { localStorage.setItem(NICK_STORAGE_KEY, nickname); } catch (e) { }
    updateNickUI();
    showNotification(nickname ? `👤 暱稱已設定為「${nickname}」` : "已清除暱稱");
  }

  if (nickBtn) nickBtn.addEventListener("click", promptNickname);
  updateNickUI();

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

  /**
   * 我的成績：跨場次的段落趨勢（這首歌你一向強在哪一段）。
   *
   * 舞台的結算畫面只亮九秒，而且唱完當下人在喘 —— 真正會回頭看的是這裡。
   * 每一列是「一首歌 × 一位演唱者」，唱滿三次（同一種曲式）才會出現：
   * 唱一次就講「你一向如何」是在唬人。
   */
  async function loadTrends() {
    try {
      const res = await window.api.getScoreTrends(30);
      const rows = res.trends || [];
      libSummary.textContent = rows.length
        ? `${rows.length} 首歌累積出趨勢（同一首唱滿 3 次就會出現）`
        : "還沒有歌累積到 3 次演唱";
      if (!rows.length) {
        searchResults.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--text-muted);">還看不出趨勢<br>同一首歌唱滿 3 次，這裡就會告訴你「你一向強在哪一段」</div>`;
        return;
      }
      searchResults.innerHTML =
        `<div style="grid-column: 1/-1; font-size: 16px; font-weight: 700; color: var(--accent-cyan); margin-bottom: 8px;">📊 我的成績（跨場次段落趨勢）</div>` +
        rows.map(renderTrendCard).join("");
    } catch (e) {
      searchResults.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: #ff007f;">成績讀取失敗</div>`;
    }
  }

  function renderTrendCard(trend) {
    const view = window.TrendView;
    const summary = view.describeTrend(trend);
    const thumb = trend.thumbnail || `https://i.ytimg.com/vi/${trend.song_id}/mqdefault.jpg`;
    const who = trend.singer ? `<span class="trend-card-singer">${escapeHtml(trend.singer)}</span>` : "";
    const bars = view.trendRows(trend).map((r) => {
      const width = Math.round(r.ratio * 50);
      return `<div class="trend-row${r.named ? "" : " is-dim"}" title="平均 ${Math.round(r.accuracy * 100)}%・${r.appearances} 次">` +
        `<span class="trend-bar-side left">${r.side === "weak" ? `<i style="width:${width}%"></i>` : ""}</span>` +
        `<span class="trend-row-label">${escapeHtml(r.label)}</span>` +
        `<span class="trend-bar-side right">${r.side === "home" ? `<i style="width:${width}%"></i>` : ""}</span>` +
        `<span class="trend-row-value">${escapeHtml(r.text)}</span>` +
        `</div>`;
    }).join("");
    const detail = summary.detail
      ? `<div class="trend-card-detail">${escapeHtml(summary.detail)}</div>` : "";

    return `
      <div class="trend-card">
        <div class="trend-card-head">
          <img class="trend-card-thumb" src="${thumb}" loading="lazy" onerror="this.style.visibility='hidden'">
          <div class="trend-card-info">
            <div class="trend-card-title" title="${escapeAttr(trend.title || trend.song_id)}">${escapeHtml(trend.title || trend.song_id)}</div>
            <div class="trend-card-meta">${who}唱過 ${trend.performances} 次 ・ 最佳 ${(trend.best_score || 0).toLocaleString()} 分</div>
          </div>
          <button class="btn btn-primary" onclick="window.addSong('${trend.song_id}', '${escapeAttr(trend.title)}', '${escapeAttr(trend.artist || "")}', '${thumb}', false)">🎤 再唱一次</button>
        </div>
        <div class="trend-card-headline">${escapeHtml(summary.headline)}</div>
        ${detail}
        <div class="trend-card-bars">${bars}</div>
      </div>`;
  }

  /**
   * 錄唱回放：把剛剛唱的那一次聽回來。
   *
   * 每一列一次演唱（同一首唱三次就有三列），就地播放、可下載、可標記保留。
   * 「保留」是這一頁最重要的按鈕：配額滿了會從最舊的開始刪，
   * 而唱得最好的那一次通常就是最舊的那一次。
   */
  // 這台機器能不能把錄音轉成 MP3（ffmpeg 在不在、設定頁有沒有開）。
  // 記在這裡是因為每一張卡片都要問一次，而答案整頁都一樣。
  let mp3Support = { available: false, reason: "", message: "" };

  async function loadRecordings() {
    try {
      const res = await window.api.getRecordings(100);
      const list = res.recordings || [];
      const rules = window.TakeRules;
      mp3Support = res.mp3 || mp3Support;
      const cacheLine = rules.mp3CacheSummary(res.stats || {});
      libSummary.textContent = [rules.quotaSummary(res.stats || {}), cacheLine]
        .filter(Boolean).join("　");

      const warning = rules.quotaWarning(res.stats || {});
      const warnHtml = warning
        ? `<div class="rec-warning">⚠️ ${escapeHtml(warning)}</div>` : "";
      // 轉不了 MP3 的時候講一次就好（不是每張卡片都講）。
      // 這一行是說給管理員聽的：客人拿回去的檔案打不開，原因在這裡。
      const mp3Note = rules.mp3UnavailableNote(mp3Support);
      const mp3Html = (mp3Note && list.length)
        ? `<div class="rec-warning">🎧 ${escapeHtml(mp3Note)}</div>` : "";
      // 功能沒開時清單一定是空的。不講的話使用者會以為錄音壞了 ——
      // 而真正要做的事（去設定頁打開）在另一頁，不指路就找不到。
      const offHtml = res.enabled === false
        ? `<div class="rec-warning">🔇 錄唱回放目前是關閉的。到「⚙️ 系統設定」打開「錄唱回放」後，下一首唱的就會錄起來。</div>`
        : "";

      if (!list.length) {
        searchResults.innerHTML =
          `<div style="grid-column: 1/-1;">${offHtml}${warnHtml}</div>` +
          `<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--text-muted);">還沒有錄音<br>唱完一首（唱夠久）就會出現在這裡</div>`;
        return;
      }

      const clearBtn = `<button class="btn btn-secondary" onclick="window.clearRecordings()">🗑️ 清空（保留標記的）</button>`;
      // MP3 快取的清除鈕只在真的有快取時出現 —— 沒有東西可以清的按鈕
      // 只會讓人按一下然後看到「沒有可清除的」。
      const clearMp3Btn = cacheLine
        ? `<button class="btn btn-secondary" onclick="window.clearMp3Cache()" title="只刪掉轉好的 MP3，錄音不會動（下次按 MP3 會重轉一份）">🧹 清除 MP3 快取</button>`
        : "";
      searchResults.innerHTML =
        `<div style="grid-column: 1/-1; display: flex; align-items: center; gap: 12px; flex-wrap: wrap;">` +
        `<span style="font-size: 16px; font-weight: 700; color: var(--accent-cyan);">🎙️ 錄唱回放</span>${clearBtn}${clearMp3Btn}</div>` +
        `<div style="grid-column: 1/-1;">${offHtml}${warnHtml}${mp3Html}</div>` +
        `<div id="nightSessions" class="night-sessions" style="grid-column: 1/-1;"></div>` +
        list.map(renderRecordingCard).join("");

      // 整晚打包是收場時的那句「今天晚上的通通給我一份」。
      // 清單先長出來、場次晚一步補進去（多一次請求，但那一次很便宜）：
      // 打包是加分項，問不到場次不該讓錄音清單也跟著不見。
      loadNightSessions();
    } catch (e) {
      searchResults.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: #ff007f;">錄音清單讀取失敗</div>`;
    }
  }

  function renderRecordingCard(rec) {
    const rules = window.TakeRules;
    const thumb = rec.thumbnail || `https://i.ytimg.com/vi/${rec.song_id}/mqdefault.jpg`;
    const when = String(rec.created_at || "").replace("T", " ").slice(0, 16);
    const pinIcon = rec.pinned ? "📌 已保留" : "📍 保留";
    return `
      <div class="rec-card${rec.pinned ? " is-pinned" : ""}">
        <div class="rec-card-head">
          <img class="rec-card-thumb" src="${thumb}" loading="lazy" onerror="this.style.visibility='hidden'">
          <div class="rec-card-info">
            <div class="rec-card-title" title="${escapeAttr(rec.title || rec.song_id)}">${escapeHtml(rec.title || rec.song_id)}</div>
            <div class="rec-card-meta">${escapeHtml(rules.takeSubtitle(rec))}</div>
            <div class="rec-card-when">${escapeHtml(when)}</div>
          </div>
        </div>
        <audio class="rec-audio" controls preload="none" onloadedmetadata="window.fixRecDuration(this)"
               src="${window.api.recordingAudioUrl(rec.id)}"></audio>
        <div class="rec-card-actions">
          <button class="btn btn-primary" onclick="window.addSong('${rec.song_id}', '${escapeAttr(rec.title)}', '${escapeAttr(rec.artist || "")}', '${thumb}', false)">🎤 再唱一次</button>
          <a class="btn btn-secondary" href="${window.api.recordingAudioUrl(rec.id, true)}" download>⬇️ 下載</a>
          ${mp3Support.available ? `<button class="btn btn-secondary" onclick="window.downloadRecordingMp3('${rec.id}', this)" title="轉成 MP3 再下載（車機、舊手機、傳給別人用）">🎧 MP3</button>` : ""}
          <button class="btn btn-secondary" onclick="window.shareRecording('${rec.id}')" title="產生有時效的連結與 QR，讓唱的人自己把這一次帶走">🔗 分享</button>
          <button class="btn btn-secondary" onclick="window.pinRecording('${rec.id}')">${pinIcon}</button>
          <button class="btn btn-secondary" onclick="window.deleteRecording('${rec.id}')">🗑️ 刪除</button>
        </div>
      </div>`;
  }

  /**
   * 整晚打包：把一整場包成一個 zip 帶走。
   *
   * 分享連結解決的是「一個人帶走自己那一首」，這裡解決的是收場時的
   * 另一句話 —— 一首一首按下載是二十三次另存新檔，而且存出來散在
   * 資料夾裡分不出誰是誰、哪一首在前面（zip 裡的檔名有序號與時間）。
   *
   * 畫面上只長最近的幾場：更早以前的通常已經被配額清掉了，
   * 列一串點下去只會拿到「這一場已經不在了」的場次沒有意義。
   */
  const NIGHT_SESSIONS_SHOWN = 3;
  // 最後一次問到的場次狀態。按下去之前要先看有沒有人正在打包（同時只做一份）。
  let nightSessions = { sessions: [], busy: false };

  async function loadNightSessions() {
    const box = document.getElementById("nightSessions");
    if (!box) return;
    try {
      nightSessions = await window.api.getRecordingSessions();
    } catch (e) {
      box.innerHTML = "";     // 問不到就整條不長，不要留一行錯誤訊息在清單上面
      return;
    }
    const view = window.NightExport;
    const sessions = (nightSessions.sessions || []).slice(0, NIGHT_SESSIONS_SHOWN);
    if (!view || !sessions.length) { box.innerHTML = ""; return; }

    const busy = view.busyNote(nightSessions.busy);
    box.innerHTML =
      `<div class="night-title">📦 整晚打包（一個 zip 帶走一整場）</div>` +
      (busy ? `<div class="rec-warning">⏳ ${escapeHtml(busy)}</div>` : "") +
      sessions.map(renderNightSession).join("");
  }

  function renderNightSession(session) {
    const view = window.NightExport;
    const key = escapeAttr(session.key);
    // 「只要某個人的」：一桌八個人，不是每個人都想把另外七個人的版本帶走
    const options = view.singerChoices(session)
      .map((c) => `<option value="${escapeAttr(c.value)}">${escapeHtml(c.label)}</option>`)
      .join("");
    return `
      <div class="night-row">
        <div class="night-info">
          <div class="night-when">${escapeHtml(view.sessionLabel(session))}</div>
          <div class="night-meta">${escapeHtml(view.sessionSummary(session))}</div>
        </div>
        <select class="night-singer" id="nightSinger-${key}" title="只打包某一位唱的">${options}</select>
        <button class="btn btn-primary" onclick="window.downloadNight('${key}')"
                title="${escapeAttr(view.downloadHint(session))}">⬇️ 打包下載</button>
      </div>`;
  }

  /**
   * 按下打包。
   *
   * 刻意用瀏覽器自己的下載（`<a download>`）而不是 fetch 成 blob：
   * 一包可能三百 MB，fetch 會把整包先讀進瀏覽器記憶體，手機上直接當掉。
   *
   * 按之前先重問一次場次：同時只打一包（那條網路正是舞台在用的），
   * 有人正在打的話當場說一聲，而不是讓瀏覽器下載回一個裝著錯誤訊息的檔案。
   * 兩次之間仍有極短的空窗（別台裝置同一秒按下去），那種情況下拿到的是
   * 一個很小的檔案 —— 重按一次就好，所以不值得為它把下載改成 fetch。
   */
  window.downloadNight = async (key) => {
    try {
      nightSessions = await window.api.getRecordingSessions();
    } catch (e) { /* 問不到就照按，真的忙的話伺服器會回 429 */ }
    if (nightSessions.busy) {
      alert(window.NightExport.busyNote(true));
      loadNightSessions();
      return;
    }
    const picker = document.getElementById(`nightSinger-${key}`);
    const singer = picker ? picker.value : "";
    const session = (nightSessions.sessions || []).find((s) => s.key === key);
    if (!session) {
      alert("這一場已經不在了（可能被配額清掉），請重新整理清單");
      loadRecordings();
      return;
    }
    showNotification(`📦 開始打包 ${window.NightExport.downloadHint(session)}`);
    const a = document.createElement("a");
    a.href = window.api.sessionZipUrl(key, singer);
    a.rel = "noopener";
    // 刻意**不設** download：設了的話瀏覽器會拿網址的最後一段當檔名，
    // 存出來是一個叫「zip」的無副檔名檔案。伺服器的 Content-Disposition
    // 已經寫好「KaraTube 20260913 23首.zip」了。
    document.body.appendChild(a);
    a.click();
    a.remove();
    // 打包期間那個位子是佔著的，畫面上要看得出來（下一個人按了會被擋）
    setTimeout(loadNightSessions, 1500);
  };

  /**
   * 讓錄音的進度條可以拖。
   *
   * MediaRecorder 產生的 webm 標頭裡**沒有 Duration** —— 它是串流容器，
   * 開始錄的時候還不知道會錄多久。瀏覽器因此把 `audio.duration` 當成
   * `Infinity`，進度條變成一條拖不動的線：想重聽副歌只能從頭放。
   * （清單上的長度不受影響 —— 那是錄的時候量好存在伺服器的。）
   *
   * 業界通用的解法：先 seek 到一個不可能的時間點，瀏覽器為了回答
   * 「到底有多長」會把整個檔案掃過一遍，`durationchange` 就會帶著真正的長度回來，
   * 這時候再把播放位置放回 0。只做一次（dataset 記著），
   * 而且只在真的要播的時候做（preload="none"）—— 一開分頁就掃五十個檔案，
   * 使用者會看到一串轉圈圈卻不知道在等什麼。
   */
  window.fixRecDuration = (el) => {
    if (!el || el.dataset.durationFixed || el.duration !== Infinity) return;
    el.dataset.durationFixed = "1";
    const onDurationChange = () => {
      if (!Number.isFinite(el.duration)) return;
      el.removeEventListener("durationchange", onDurationChange);
      el.currentTime = 0;
    };
    el.addEventListener("durationchange", onDurationChange);
    el.currentTime = 1e101;
  };

  /**
   * 分享這一次：產（或沿用）一個有時效的連結，把 QR 亮出來讓人掃走。
   *
   * 唱完那一句「傳給我」在這裡結案。刻意**沿用**已經有效的連結：
   * 每按一次分享就換一個新的，等於讓剛剛掃過的人手上那一張 QR 失效。
   */
  // 目前亮在畫面上的那一個連結（複製與撤銷都對著它）
  let currentShare = null;

  window.shareRecording = async (recId) => {
    const modal = document.getElementById("shareModal");
    const linkBox = document.getElementById("shareLinkText");
    const qrImg = document.getElementById("shareQrImg");
    const hint = document.getElementById("shareHint");
    modal.classList.add("open");
    linkBox.textContent = "產生中…";
    qrImg.removeAttribute("src");
    hint.textContent = "";
    try {
      const res = await window.api.shareRecording(recId);
      const share = res.share || {};
      currentShare = share;
      linkBox.textContent = share.url || "";
      qrImg.src = share.qr_url;
      hint.textContent = window.ShareView
        ? `${window.ShareView.expiryPhrase(share.expires_in_seconds)}・只在這個網路裡打得開`
        : "";
    } catch (e) {
      linkBox.textContent = "";
      hint.textContent = e.message;
      currentShare = null;
    }
  };

  /**
   * 複製連結。
   *
   * `navigator.clipboard` 在**非 HTTPS** 的頁面上不存在 —— 而這套系統正是
   * 用 http://192.168.x.x 開的，所以那條路平常就走不通。真正會用到的是
   * 後面那條 execCommand 的老路；兩條都不行時要明講「請長按複製」，
   * 按了沒反應會被當成系統壞了。
   */
  window.copyShareLink = async () => {
    const url = currentShare && currentShare.url;
    const hint = document.getElementById("shareHint");
    if (!url) return;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(url);
        hint.textContent = "已複製連結 ✅";
        return;
      }
    } catch (e) { /* 落到下面的老方法 */ }
    try {
      const ta = document.createElement("textarea");
      ta.value = url;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(ta);
      hint.textContent = ok ? "已複製連結 ✅" : "複製不成功，請長按上面的網址自行複製";
    } catch (e) {
      hint.textContent = "複製不成功，請長按上面的網址自行複製";
    }
  };

  window.revokeShareLink = async () => {
    if (!currentShare || !currentShare.token) return;
    if (!confirm("撤銷這個分享連結？已經掃過 QR 的人會立刻打不開。")) return;
    try {
      await window.api.revokeShare(currentShare.token);
      document.getElementById("shareLinkText").textContent = "（已撤銷）";
      document.getElementById("shareQrImg").removeAttribute("src");
      document.getElementById("shareHint").textContent = "已撤銷，再按一次分享會產生新的連結";
      currentShare = null;
    } catch (e) { alert("撤銷失敗: " + e.message); }
  };

  window.closeShareModal = () => {
    document.getElementById("shareModal").classList.remove("open");
  };

  // 點背景關閉，跟系統設定／QR 那兩個彈窗一樣 ——
  // 同一個畫面上三個彈窗，其中一個關不掉會被當成當機。
  const shareModalEl = document.getElementById("shareModal");
  if (shareModalEl) {
    shareModalEl.addEventListener("click", (e) => {
      if (e.target === shareModalEl) window.closeShareModal();
    });
  }

  window.pinRecording = async (recId) => {
    try {
      await window.api.pinRecording(recId);
      loadRecordings();
    } catch (e) { alert("保留狀態切換失敗: " + e.message); }
  };

  window.deleteRecording = async (recId) => {
    if (!confirm("刪除這一次的錄音？刪掉就找不回來了。")) return;
    try {
      await window.api.deleteRecording(recId);
      loadRecordings();
    } catch (e) { alert("錄音刪除失敗: " + e.message); }
  };

  window.clearRecordings = async () => {
    if (!confirm("清空錄音？標記保留（📌）的那幾筆會留下來。")) return;
    try {
      const res = await window.api.clearRecordings(false);
      loadRecordings();
      if (!res.removed) alert("沒有可清除的錄音（標記保留的不會被清掉）");
    } catch (e) { alert("錄音清空失敗: " + e.message); }
  };

  /**
   * 轉成 MP3 再下載。
   *
   * 刻意不用 `<a download>` 直接指過去：第一次按的時候伺服器要真的跑一次
   * ffmpeg（一首歌幾秒鐘），而 `<a>` 在那幾秒裡**看起來完全沒反應** ——
   * 使用者的下一個動作一定是再按一次，然後再一次。所以這裡自己 fetch，
   * 按鈕當場變成「轉檔中…」並鎖起來，失敗也講得出是為什麼。
   *
   * 檔名從 Content-Disposition 挖（伺服器那邊已經把歌名與演唱者組好了）：
   * 挖不到就退成 `<id>.mp3`，至少不會是一串沒有副檔名的亂碼。
   */
  window.downloadRecordingMp3 = async (recId, btn) => {
    const original = btn ? btn.innerHTML : "";
    if (btn) { btn.disabled = true; btn.innerHTML = "⏳ 轉檔中…"; }
    let url = "";
    try {
      const res = await fetch(window.api.recordingAudioUrl(recId, true, "mp3"));
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail || `MP3 轉檔失敗 (${res.status})`);
      }
      const blob = await res.blob();
      const name = window.TakeRules.filenameFromDisposition(
        res.headers.get("content-disposition")) || `${recId}.mp3`;
      url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      showNotification("🎧 MP3 已下載");
      loadRecordings();   // 快取用量變了，順手把那一行更新
    } catch (e) {
      alert("MP3 下載失敗: " + e.message);
    } finally {
      // blob URL 不收的話，一個晚上下載十首就有十份檔案留在記憶體裡。
      // 延遲一拍再收：有些瀏覽器是在 click 之後才真的去讀那個 URL。
      if (url) setTimeout(() => URL.revokeObjectURL(url), 60000);
      if (btn) { btn.disabled = false; btn.innerHTML = original; }
    }
  };

  window.clearMp3Cache = async () => {
    try {
      const res = await window.api.clearMp3Cache();
      showNotification(res.removed ? `🧹 已清除 ${res.removed} 份 MP3` : "沒有可清除的 MP3");
      loadRecordings();
    } catch (e) { alert("MP3 快取清除失敗: " + e.message); }
  };

  // 舞台錄好一首就廣播過來。正在看這一頁時自動長出來 ——
  // 唱完走回點歌台按重新整理才看得到，會被當成「沒錄到」。
  window.api.on("RECORDING_SAVED", () => {
    const active = document.querySelector(".lib-tab.active");
    if (active && active.dataset.lib === "recordings") loadRecordings();
  });

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

  // --- 排程預處理（半夜整批把歌處理好）---
  // 隨選處理的代價是第一次點一首新歌要等好幾分鐘。這一頁把等待挪到沒人唱的時候：
  // 晚上把整張播放清單貼進來，機器在設定的時段裡一首一首跑完，
  // 隔天所有歌都是「⚡ 快取秒播」。有人在唱歌時它會自己讓開。
  // 純顯示邏輯（挑要顯示哪幾首、時段字串、狀態燈）住在 batch-view.js，那邊有單元測試
  const BatchView = window.BatchView;
  let batchState = null;

  async function loadBatch() {
    try {
      batchState = await window.api.getBatchState();
      renderBatch();
    } catch (e) {
      searchResults.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: #ff007f;">排程狀態讀取失敗</div>`;
    }
  }

  function batchStatusLine(state) {
    const windowText = BatchView.formatWindow(state.window);
    const dotClass = BatchView.statusDotClass(state);
    return `
      <div class="batch-status">
        <span class="batch-dot ${dotClass}"></span>
        <div>
          <div class="batch-status-main">${state.can_run ? "可以開工" : "待命中"}
            <span class="batch-status-reason">${escapeHtml(state.reason || "")}</span>
          </div>
          <div class="batch-status-sub">排程時段 ${windowText}
            ・ 待處理 ${state.pending_total || 0} 首
            ${state.stage_busy ? "・ 🎤 舞台使用中" : ""}
            ${state.enabled ? "" : "・ ⚠️ 功能已在系統設定中關閉"}
          </div>
        </div>
      </div>`;
  }

  // 表單是即時重畫的（每處理完一首歌就會收到 BATCH_UPDATE），
  // 所以重畫前要把使用者打到一半的字接住，不然貼了 30 行清單會憑空消失。
  function readBatchForm() {
    const sources = document.getElementById("batchSources");
    const name = document.getElementById("batchName");
    const startNow = document.getElementById("batchStartNow");
    if (!sources) return null;
    return {
      sources: sources.value,
      name: name ? name.value : "",
      startNow: startNow ? startNow.checked : false,
      focusId: document.activeElement ? document.activeElement.id : "",
      selStart: sources.selectionStart,
      selEnd: sources.selectionEnd,
    };
  }

  function restoreBatchForm(saved) {
    if (!saved) return;
    const sources = document.getElementById("batchSources");
    const name = document.getElementById("batchName");
    const startNow = document.getElementById("batchStartNow");
    if (sources) sources.value = saved.sources;
    if (name) name.value = saved.name;
    if (startNow) startNow.checked = saved.startNow;
    if (saved.focusId === "batchSources" && sources) {
      sources.focus();
      try { sources.setSelectionRange(saved.selStart, saved.selEnd); } catch (e) { }
    } else if (saved.focusId && document.getElementById(saved.focusId)) {
      document.getElementById(saved.focusId).focus();
    }
  }

  function batchFormHtml(state) {
    const forceBtn = state.force_run
      ? `<button class="btn btn-secondary" onclick="window.setBatchForce(false)" title="回到只在排程時段處理">🕒 照表操課</button>`
      : `<button class="btn btn-secondary" onclick="window.setBatchForce(true)" title="不等時段，現在就開始處理（有人唱歌時仍會讓開）">⚡ 立即開始</button>`;
    return `
      <div class="batch-form">
        <textarea id="batchSources" class="batch-textarea" rows="3"
          placeholder="貼上 YouTube 播放清單網址、單曲網址或歌名關鍵字，一行一個（例：https://www.youtube.com/playlist?list=... 或「周杰倫 稻香」）"></textarea>
        <div class="batch-form-row">
          <input type="text" id="batchName" class="batch-name-input" maxlength="40" placeholder="這批的名稱（選填，例：週末歌單）">
          <label class="batch-check"><input type="checkbox" id="batchStartNow"> 立即開始</label>
          <button class="btn btn-primary" onclick="window.submitBatchJob()">🌙 加入排程</button>
          ${forceBtn}
          <button class="btn btn-secondary" onclick="window.clearFinishedBatch()" title="清掉已完成／已取消的紀錄">🧹 清紀錄</button>
        </div>
      </div>`;
  }

  function batchItemHtml(item) {
    const meta = BatchView.itemStatusMeta(item.status);
    const title = escapeHtml(item.title || item.song_id);
    return `
      <div class="batch-item">
        <span class="batch-item-badge ${meta.cls}">${meta.label}</span>
        <span class="batch-item-title" title="${title}">${title}</span>
        <span class="batch-item-detail">${escapeHtml(BatchView.itemDetailText(item))}</span>
      </div>`;
  }

  function batchJobHtml(job) {
    const p = job.progress || {};
    const shown = BatchView.pickBatchItems(job.items || []);
    const rest = (job.items || []).length - shown.length;
    const counts = BatchView.summarizeCounts(p);
    const canCancel = (p.pending || 0) > 0;
    const canRetry = (p.error || 0) > 0;
    return `
      <div class="batch-job">
        <div class="batch-job-head">
          <div class="batch-job-title">${escapeHtml(job.name || "排程任務")}
            <span class="batch-job-status">${BatchView.jobStatusLabel(job.status)}</span>
          </div>
          <div class="batch-job-actions">
            ${canRetry ? `<button class="btn btn-secondary" onclick="window.batchJobAction('${job.job_id}', 'retry')" title="只重跑失敗的那幾首">🔁 重試失敗</button>` : ""}
            ${canCancel ? `<button class="btn btn-secondary" onclick="window.batchJobAction('${job.job_id}', 'cancel')" title="取消還沒處理的歌">✖ 取消</button>` : ""}
            <button class="btn btn-secondary cache-del-btn" onclick="window.batchJobAction('${job.job_id}', 'delete')" title="刪除這筆紀錄（已處理好的歌留在曲庫）">🗑️</button>
          </div>
        </div>
        <div class="batch-progress"><div class="batch-progress-bar" style="width: ${p.percent || 0}%"></div></div>
        <div class="batch-job-meta">${p.settled || 0} / ${p.total || 0} 首 ・ ${counts || "尚未開始"}
          ${job.requested_by ? ` ・ 👤 ${escapeHtml(job.requested_by)}` : ""}
          ${job.created_at ? ` ・ 建立於 ${formatSungAt(job.created_at)}` : ""}</div>
        <div class="batch-items">${shown.map(batchItemHtml).join("")}
          ${rest > 0 ? `<div class="batch-item batch-item-more">…還有 ${rest} 首</div>` : ""}</div>
      </div>`;
  }

  function renderBatch() {
    if (!batchState) return;
    const saved = readBatchForm();
    const jobs = batchState.jobs || [];
    libSummary.textContent = jobs.length
      ? `${jobs.length} 筆排程 ・ 待處理 ${batchState.pending_total || 0} 首`
      : "尚未建立排程";
    const jobsHtml = jobs.length
      ? jobs.map(batchJobHtml).join("")
      : `<div style="grid-column: 1/-1; text-align: center; padding: 30px; color: var(--text-muted);">還沒有排程任務<br>把整張播放清單貼上去，半夜自己跑完，隔天全部秒播！</div>`;
    searchResults.innerHTML =
      `<div style="grid-column: 1/-1; font-size: 16px; font-weight: 700; color: var(--accent-cyan); margin-bottom: 8px;">🌙 排程預處理</div>`
      + batchStatusLine(batchState) + batchFormHtml(batchState) + jobsHtml;
    restoreBatchForm(saved);
  }

  window.submitBatchJob = async () => {
    const form = readBatchForm();
    if (!form || !form.sources.trim()) {
      alert("請先貼上播放清單網址、歌曲網址或關鍵字（一行一個）");
      return;
    }
    showNotification("🌙 正在展開清單…");
    try {
      const res = await window.api.createBatchJob({
        sources: form.sources, name: form.name,
        startNow: form.startNow, requestedBy: nickname,
      });
      batchState = res.state || batchState;
      const failed = res.failed || [];
      const count = (res.job && res.job.items ? res.job.items.length : 0);
      showNotification(failed.length
        ? `🌙 已排入 ${count} 首（${failed.length} 行找不到歌）`
        : `🌙 已排入 ${count} 首`);
      const sources = document.getElementById("batchSources");
      if (sources) sources.value = failed.join("\n");   // 失敗的留在框裡讓使用者修
      loadBatch();
    } catch (e) {
      alert("排程建立失敗: " + e.message);
    }
  };

  window.setBatchForce = async (force) => {
    try {
      const res = await window.api.setBatchForce(force);
      batchState = res.state || batchState;
      showNotification(res.force_run ? "⚡ 立即開始處理" : "🕒 回到排程時段處理");
      renderBatch();
    } catch (e) {
      alert("切換失敗: " + e.message);
    }
  };

  window.batchJobAction = async (jobId, action) => {
    const prompts = {
      cancel: "取消這批還沒處理的歌嗎？\n正在處理的那一首會跑完（中途砍掉只會留下半成品）。",
      delete: "刪除這筆排程紀錄嗎？\n已經處理好的歌會留在曲庫裡，不受影響。",
    };
    if (prompts[action] && !confirm(prompts[action])) return;
    try {
      await window.api.batchJobAction(jobId, action);
      const done = { retry: "🔁 失敗的歌已重新排隊", cancel: "✖ 已取消", delete: "🗑️ 已刪除紀錄" };
      showNotification(done[action] || "已更新");
      loadBatch();
    } catch (e) {
      alert("操作失敗: " + e.message);
    }
  };

  window.clearFinishedBatch = async () => {
    try {
      const res = await window.api.clearFinishedBatchJobs();
      showNotification(res.removed ? `🧹 已清除 ${res.removed} 筆紀錄` : "沒有可清除的紀錄");
      loadBatch();
    } catch (e) {
      alert("清除失敗: " + e.message);
    }
  };

  // 伺服器每處理完一首歌就廣播一次，開著這一頁的裝置會即時看到進度
  window.api.on("BATCH_UPDATE", (msg) => {
    batchState = msg.data;
    const activeTab = document.querySelector(".lib-tab.active");
    if (activeTab && activeTab.dataset.lib === "batch") renderBatch();
  });

  // --- 分類瀏覽（語言別 / 歌手）---
  // 商用點歌機的「分類點歌」：先選語言別或歌手，再從清單裡挑歌。
  // 語言與歌手是伺服器從歌名、頻道名與歌詞判定後快取在 metadata 裡的。
  let browseFilter = { language: "", artist: "", sort: "recent" };

  const SORT_LABELS = [
    ["recent", "最新加入"],
    ["plays", "最常點唱"],
    ["title", "歌名"],
    ["artist", "歌手"],
  ];

  function facetChip(kind, value, label, count, active) {
    const countHtml = (count === null || count === undefined)
      ? "" : `<span class="facet-count">${count}</span>`;
    return `<button class="facet-chip ${active ? "active" : ""}"
      onclick="window.setLibFilter('${kind}', '${escapeAttr(value)}')">${escapeHtml(label)}${countHtml}</button>`;
  }

  function buildFacetBar(facets) {
    const langs = (facets.languages || []).filter(l => l.count > 0);
    const artists = facets.artists || [];
    const langChips = [facetChip("language", "", "全部語言", facets.total || 0, !browseFilter.language)]
      .concat(langs.map(l => facetChip("language", l.key, l.label, l.count,
        browseFilter.language === l.key))).join("");
    const artistChips = [facetChip("artist", "", "全部歌手", artists.length, !browseFilter.artist)]
      .concat(artists.map(a => facetChip("artist", a.name, a.name, a.count,
        browseFilter.artist === a.name))).join("");
    const sortChips = SORT_LABELS.map(([key, label]) =>
      facetChip("sort", key, label, null, browseFilter.sort === key)).join("");
    return `
      <div class="facet-bar">
        <div class="facet-row"><span class="facet-label">🌏 語言</span><div class="facet-chips">${langChips}</div></div>
        <div class="facet-row"><span class="facet-label">🎤 歌手</span><div class="facet-chips facet-chips-scroll">${artistChips}</div></div>
        <div class="facet-row"><span class="facet-label">↕️ 排序</span><div class="facet-chips">${sortChips}</div></div>
      </div>`;
  }

  window.setLibFilter = (kind, value) => {
    // 查歌分頁的字數鈕借用同一顆 chip 元件，但它切的是查歌條件而不是瀏覽條件
    if (kind === "findchars") return window.findSetChars(value);
    if (kind === "sort") browseFilter.sort = value || "recent";
    else if (kind === "language") browseFilter.language = value;
    else if (kind === "artist") browseFilter.artist = value;
    loadBrowse();
  };

  async function loadBrowse() {
    try {
      const [facets, res] = await Promise.all([
        window.api.getLibraryFacets(),
        window.api.getLibrarySongs(browseFilter),
      ]);
      // 歌卡顯示的是整理過的歌手名（artist_name），不是原始頻道名
      const songs = (res.songs || []).map(s => ({ ...s, uploader: s.artist_name }));
      libSummary.textContent = `曲庫 ${facets.total || 0} 首 ・ ${facets.artist_count || 0} 位歌手`;
      const emptyText = (facets.total || 0) === 0
        ? "曲庫是空的<br>搜尋並點一首歌，處理完就會自動歸類到這裡！"
        : "這個分類目前沒有歌，換一個分類看看";
      renderSearchResults(songs, "🎼 分類瀏覽", buildFacetBar(facets), emptyText);
    } catch (e) {
      searchResults.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: #ff007f;">曲庫分類讀取失敗</div>`;
    }
  }

  // --- 曲庫查歌（注音首字 / 歌名字數）---
  // 商用點歌機的實體鍵盤查法。跟上面的搜尋框是兩件事：搜尋框查的是 YouTube
  // （點下去要等 AI 跑），這一頁只查**已經備好的曲庫**，查到的每一首都是秒播。
  // 注音、字數與直接打字共用同一個查詢字串（伺服器本來就三種都吃），
  // 使用者才不會遇到「我打在哪一個框裡」這種問題。
  let findKeyboard = null;              // 鍵盤與字數桶（曲庫沒變就不用重抓）
  let findState = { query: "", chars: 0, nextKeys: [] };
  let findTypingTimer = null;
  let findInputFocused = false;

  function findKeyHtml(key, enabled) {
    return `<button class="find-key${enabled ? "" : " find-key-off"}"
      ${enabled ? "" : "disabled"}
      onclick="window.findPress('${key}')">${key}</button>`;
  }

  function buildFindBar(keyboard, res) {
    const LS = window.LibrarySearch;
    const kb = keyboard || {};
    const available = kb.bopomofo_available !== false;
    const buckets = kb.char_buckets || [];
    // 會讓整個鍵盤一起變灰的「下一鍵」等於沒給資訊（見 usableNextKeys）
    const nextKeys = LS.usableNextKeys(kb.rows, findState.nextKeys);

    const charChips = [facetChip("findchars", "0", "不限字數", null, !findState.chars)]
      .concat(buckets.map(b => {
        const { label, count } = LS.charBucketLabel(b);
        return facetChip("findchars", String(b.chars), label, count,
          findState.chars === b.chars);
      })).join("");

    const keyboardHtml = available
      ? (kb.rows || []).map(row =>
        `<div class="find-key-row">${row.map(k =>
          findKeyHtml(k, LS.keyEnabled(k, nextKeys))).join("")}</div>`).join("")
      : `<div class="find-keyboard-off">這台伺服器沒有安裝注音字典（pypinyin），
          注音查歌關閉中；字數與打字查詢照常可用。</div>`;

    // 查詢列擺在鍵盤上面：按到第五顆的時候，人要看得到自己按了什麼
    return `
      <div class="find-bar">
        <div class="find-row">
          <span class="facet-label">🔤 查歌</span>
          <input type="text" id="findInput" class="find-input"
                 value="${escapeAttr(findState.query)}"
                 placeholder="按下面的注音，或直接打歌名／歌手"
                 oninput="window.findTyped(this.value)"
                 onfocus="window.findFocus(true)" onblur="window.findFocus(false)">
          <button class="btn btn-secondary find-act" onclick="window.findBackspace()"
                  title="退一格">⌫</button>
          <button class="btn btn-secondary find-act" onclick="window.findClear()"
                  title="把注音、字數條件全部清掉">清除</button>
        </div>
        <div class="find-row">
          <span class="facet-label">🔎 條件</span>
          <span class="find-query-label">${escapeHtml(
            LS.queryLabel(findState.query, findState.chars))}</span>
        </div>
        <div class="find-row"><span class="facet-label">🔢 字數</span>
          <div class="facet-chips facet-chips-scroll">${charChips}</div></div>
        <div class="find-keyboard">${keyboardHtml}</div>
      </div>`;
  }

  async function loadFind(reloadKeyboard = true) {
    try {
      if (reloadKeyboard || !findKeyboard) findKeyboard = await window.api.getFindKeys();
      const res = await window.api.findInLibrary({
        q: findState.query, chars: findState.chars, limit: 60 });
      findState.nextKeys = res.next_keys || [];
      // 這一頁的歌卡標題用萃出來的歌名本體，不是整串 YouTube 標題 ——
      // 查的是「稻香」，列出來卻是「周杰倫 Jay Chou - 稻香…【4K】」很難掃視。
      const songs = (res.songs || []).map(s => ({
        ...s, title: s.core_title || s.title, uploader: s.artist_name }));
      libSummary.textContent = window.LibrarySearch.resultSummary(res);
      renderSearchResults(songs, "🔤 曲庫查歌（列出來的都是快取秒播）",
        buildFindBar(findKeyboard, res), window.LibrarySearch.emptyHint(res));
      restoreFindFocus();
    } catch (e) {
      searchResults.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: #ff007f;">曲庫查歌讀取失敗</div>`;
    }
  }

  // 每查一次就重畫整區，輸入框會跟著換掉一個新的 DOM 節點 ——
  // 不把游標放回去的話，打字打到第二個字就會發現自己在對著空氣打。
  function restoreFindFocus() {
    if (!findInputFocused) return;
    const input = document.getElementById("findInput");
    if (!input) return;
    input.focus();
    const end = input.value.length;
    try { input.setSelectionRange(end, end); } catch (e) { /* 舊瀏覽器不支援就算了 */ }
  }

  window.findFocus = (focused) => { findInputFocused = !!focused; };

  window.findPress = (key) => {
    findState.query = window.LibrarySearch.pressKey(findState.query, key);
    loadFind(false);
  };

  window.findBackspace = () => {
    findState.query = window.LibrarySearch.backspace(findState.query);
    loadFind(false);
  };

  window.findClear = () => {
    findState.query = "";
    findState.chars = 0;
    loadFind(false);
  };

  window.findSetChars = (value) => {
    const n = Math.max(0, parseInt(value, 10) || 0);
    // 再按一次同一個字數＝取消（點歌機上最常見的動作是「我按錯了」）
    findState.chars = (findState.chars === n) ? 0 : n;
    loadFind(false);
  };

  // 打字每一鍵都查一次會把伺服器打爆（每次查詢都要掃一遍曲庫），
  // 所以停手 250ms 才送出 —— 中文輸入法選字的時候更需要這個緩衝。
  window.findTyped = (value) => {
    findState.query = value || "";
    clearTimeout(findTypingTimer);
    findTypingTimer = setTimeout(() => loadFind(false), 250);
  };

  // --- 歌號點歌（六位數）---
  // 包廂裡唯一可以用喊的那一條點歌路（「幫我點 100237」）。其他每一條
  // 都得走過去看螢幕，而這一條只要記得住六個數字 —— 前提是號碼到處印得出來
  // （見 renderSearchResults 的 numberBadge）且**永遠不會變成別首歌**
  // （見 backend/services/song_numbers.py）。
  let numberState = { query: "", nextDigits: [], lookup: null };

  // 數字鍵**永遠可以按**（跟注音鍵盤刻意不同，理由見 number-search.js 的
  // digitHasSongs）：已下架號碼的數字不會出現在「下一鍵」裡，變灰的話
  // 使用者就永遠打不完那組號碼，也就永遠看不到「這首歌已經不在曲庫了」。
  // 標亮是正面提示（按下去會落在現有的歌上），不是許可。
  function numberKeyHtml(digit, live) {
    return `<button class="find-key number-key${live ? " number-key-live" : ""}"
      onclick="window.numberPress('${digit}')">${digit}</button>`;
  }

  function buildNumberBar(res) {
    const NS = window.NumberSearch;
    const data = res || {};
    const available = !(data.book && data.book.available === false);
    const digits = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"];
    // 打滿之後就沒有「下一鍵」可算了，這時候一顆都不標亮。
    const nextDigits = NS.isComplete(numberState.query) ? [] : numberState.nextDigits;
    const keysHtml = available
      ? `<div class="find-key-row">${digits.map(d =>
          numberKeyHtml(d, NS.digitHasSongs(d, nextDigits))).join("")}</div>`
      : `<div class="find-keyboard-off">歌號簿讀不出來，歌號點歌暫時停用
          （其他查歌方式照常可用）。</div>`;

    // 打滿六碼**不**直接點歌，要再按一次確認：按錯一碼在點歌機上是常事，
    // 直接送出的話那首歌已經排進佇列了，而使用者的下一個動作是去把它刪掉。
    const msg = numberState.lookup ? NS.lookupMessage(numberState.lookup) : null;
    const song = (numberState.lookup && numberState.lookup.song) || null;
    const confirmHtml = song
      ? `<button class="btn btn-primary number-confirm"
           onclick="window.addSong('${song.song_id}', '${escapeAttr(song.core_title || song.title)}', '${escapeAttr(song.artist_name || song.artist || "")}', '${song.thumbnail}', false)">
           🎤 點這首（${escapeHtml(song.core_title || song.title)}）</button>`
      : "";
    const msgHtml = msg && msg.text
      ? `<div class="number-msg number-msg-${msg.tone}">${escapeHtml(msg.text)}</div>`
      : "";

    return `
      <div class="find-bar number-bar">
        <div class="find-row">
          <span class="facet-label">🔢 歌號</span>
          <span class="number-display">${escapeHtml(
            window.NumberSearch.queryDisplay(numberState.query))}</span>
          <button class="btn btn-secondary find-act" onclick="window.numberBackspace()"
                  title="退一格">⌫</button>
          <button class="btn btn-secondary find-act" onclick="window.numberClear()"
                  title="清掉重打">清除</button>
        </div>
        ${msgHtml}
        ${confirmHtml ? `<div class="find-row">${confirmHtml}</div>` : ""}
        <div class="find-keyboard">${keysHtml}</div>
      </div>`;
  }

  async function loadNumbers() {
    try {
      const res = await window.api.getSongNumbers({ prefix: numberState.query, limit: 40 });
      numberState.nextDigits = res.next_digits || [];
      // 打滿六碼才去查號：沒打完就查，使用者每按一下都會看到一次
      // 「沒有這組歌號」—— 那句話在他還在打的時候是騙人的。
      numberState.lookup = window.NumberSearch.isComplete(numberState.query)
        ? await window.api.lookupSongNumber(numberState.query)
        : null;
      const songs = (res.songs || []).map(s => ({
        ...s, title: s.core_title || s.title, uploader: s.artist_name }));
      libSummary.textContent = window.NumberSearch.numberSummary(res);
      renderSearchResults(songs, "🔢 歌號點歌（打號碼，或按前幾碼看看有哪些歌）",
        buildNumberBar(res), window.NumberSearch.idleHint(res));
    } catch (e) {
      searchResults.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: #ff007f;">歌號讀取失敗</div>`;
    }
  }

  window.numberPress = (digit) => {
    numberState.query = window.NumberSearch.pressDigit(numberState.query, digit);
    loadNumbers();
  };

  window.numberBackspace = () => {
    numberState.query = window.NumberSearch.eraseDigit(numberState.query);
    numberState.lookup = null;
    loadNumbers();
  };

  window.numberClear = () => {
    numberState.query = "";
    numberState.lookup = null;
    loadNumbers();
  };

  // --- 歌星查歌（按歌星名字的注音首字）---
  // 跟上面的「注音查歌」是同一副鍵盤、不同索引：那邊按的是歌名的字，
  // 這邊按的是**歌星名字**的字。包廂裡更常用的其實是這一邊 ——
  // 想不起歌名的人，一定記得自己要唱誰。
  let artistKeyboard = null;
  let artistState = { query: "", artist: "", nextKeys: [] };
  let artistTypingTimer = null;
  let artistInputFocused = false;

  function artistChipHtml(artist, selected) {
    const info = window.ArtistSearch.artistLabel(artist);
    const active = window.ArtistSearch.isSelected(artist, selected);
    const title = info.note || (info.unknown ? "這些歌認不出是誰唱的" : info.label);
    return `<button class="facet-chip artist-chip ${active ? "active" : ""}"
      title="${escapeAttr(title)}"
      onclick="window.artistPick('${escapeAttr(artist.id || "")}')">${escapeHtml(info.label)}${
      info.note ? `<span class="artist-alias">${escapeHtml(info.note)}</span>` : ""
    }<span class="facet-count">${info.count}</span></button>`;
  }

  function buildArtistBar(keyboard, res) {
    const AS = window.ArtistSearch;
    const kb = keyboard || {};
    const available = kb.bopomofo_available !== false;
    const nextKeys = window.LibrarySearch.usableNextKeys(kb.rows, artistState.nextKeys);
    const artists = res.artists || [];

    const chips = artists.map(a => artistChipHtml(a, res.selected)).join("");
    const keyboardHtml = available
      ? (kb.rows || []).map(row =>
        `<div class="find-key-row">${row.map(k =>
          `<button class="find-key${window.LibrarySearch.keyEnabled(k, nextKeys) ? "" : " find-key-off"}"
            ${window.LibrarySearch.keyEnabled(k, nextKeys) ? "" : "disabled"}
            onclick="window.artistPress('${k}')">${k}</button>`).join("")}</div>`).join("")
      : `<div class="find-keyboard-off">這台伺服器沒有安裝注音字典（pypinyin），
          注音查歌星關閉中；直接打歌星的名字照常可用。</div>`;

    return `
      <div class="find-bar">
        <div class="find-row">
          <span class="facet-label">🎤 歌星</span>
          <input type="text" id="artistInput" class="find-input"
                 value="${escapeAttr(artistState.query)}"
                 placeholder="按下面的注音，或直接打歌星的名字"
                 oninput="window.artistTyped(this.value)"
                 onfocus="window.artistFocus(true)" onblur="window.artistFocus(false)">
          <button class="btn btn-secondary find-act" onclick="window.artistBackspace()"
                  title="退一格">⌫</button>
          <button class="btn btn-secondary find-act" onclick="window.artistClear()"
                  title="把注音與選定的歌星清掉">清除</button>
        </div>
        <div class="find-row">
          <span class="facet-label">🔎 條件</span>
          <span class="find-query-label">${escapeHtml(
            AS.queryLabel(artistState.query, res.selected))}</span>
        </div>
        <div class="find-row"><span class="facet-label">🎼 歌星</span>
          <div class="facet-chips facet-chips-scroll">${
            chips || `<span class="find-query-label">${AS.emptyHint(res)}</span>`}</div></div>
        <div class="find-keyboard">${keyboardHtml}</div>
      </div>`;
  }

  async function loadArtists(reloadKeyboard = true) {
    try {
      if (reloadKeyboard || !artistKeyboard) {
        artistKeyboard = await window.api.getArtistKeys();
      }
      const res = await window.api.findArtists({
        q: artistState.query, artist: artistState.artist, limit: 80 });
      artistState.nextKeys = res.next_keys || [];
      // 伺服器可能自己選了一位（只剩一位的時候），本地狀態要跟上，
      // 不然下一次查詢會把那位又弄丟
      if (res.selected && res.selected.id) artistState.artist = res.selected.id;
      const songs = (res.songs || []).map(s => ({
        ...s, title: s.core_title || s.title, uploader: s.artist_name }));
      libSummary.textContent = window.ArtistSearch.resultSummary(res);
      renderSearchResults(songs, window.ArtistSearch.songHeading(res),
        buildArtistBar(artistKeyboard, res), window.ArtistSearch.emptyHint(res));
      restoreArtistFocus();
    } catch (e) {
      searchResults.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: #ff007f;">歌星查歌讀取失敗</div>`;
    }
  }

  function restoreArtistFocus() {
    if (!artistInputFocused) return;
    const input = document.getElementById("artistInput");
    if (!input) return;
    input.focus();
    const end = input.value.length;
    try { input.setSelectionRange(end, end); } catch (e) { /* 舊瀏覽器不支援就算了 */ }
  }

  window.artistFocus = (focused) => { artistInputFocused = !!focused; };

  window.artistPress = (key) => {
    artistState.query = window.LibrarySearch.pressKey(artistState.query, key);
    // 多按一個鍵＝重新縮範圍，之前選定的那位要放掉（不放的話歌單不會動，
    // 使用者會以為鍵盤壞了）
    artistState.artist = "";
    loadArtists(false);
  };

  window.artistBackspace = () => {
    artistState.query = window.LibrarySearch.backspace(artistState.query);
    artistState.artist = "";
    loadArtists(false);
  };

  window.artistClear = () => {
    artistState.query = "";
    artistState.artist = "";
    loadArtists(false);
  };

  window.artistPick = (id) => {
    artistState.artist = window.ArtistSearch.toggleArtist(artistState.artist, id);
    loadArtists(false);
  };

  window.artistTyped = (value) => {
    artistState.query = value || "";
    artistState.artist = "";
    clearTimeout(artistTypingTimer);
    artistTypingTimer = setTimeout(() => loadArtists(false), 250);
  };

  // --- 新歌榜 + 推薦歌單 ---
  // 新歌榜＝最近加入曲庫的歌；推薦歌單＝依點唱紀錄推回「接下來唱什麼」，
  // 每首都附推薦理由，使用者才知道為什麼會出現這首。
  async function loadNewAndRecommend() {
    try {
      const [newRes, recRes] = await Promise.all([
        window.api.getNewSongs(18),
        window.api.getRecommendations(12),
      ]);
      const newSongs = (newRes.songs || []).map(s => ({ ...s, uploader: s.artist_name }));
      const recs = recRes.songs || [];
      libSummary.textContent = `${newRes.new_days || 14} 天內新增 ${newSongs.filter(s => s.is_new).length} 首`;
      if (newSongs.length === 0) {
        renderSearchResults([], "🆕 新歌榜", "",
          "曲庫還沒有歌<br>點播第一首歌，處理完就會出現在新歌榜！");
        return;
      }
      renderSearchResults(newSongs, "🆕 新歌榜（最近加入曲庫）");
      if (recs.length > 0) {
        // 推薦歌單接在新歌榜後面，兩區共用同一個 grid，卡片尺寸才一致
        searchResults.insertAdjacentHTML("beforeend",
          `<div style="grid-column: 1/-1; font-size: 16px; font-weight: 700; color: var(--accent-cyan); margin: 16px 0 8px;">💡 為你推薦</div>`
          + recs.map(recommendCardHtml).join(""));
      }
    } catch (e) {
      searchResults.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: #ff007f;">新歌與推薦讀取失敗</div>`;
    }
  }

  function recommendCardHtml(s) {
    const isFav = favoriteIds.has(s.song_id);
    return `
      <div class="song-card">
        <div class="song-thumb-wrapper">
          <img class="song-thumb" src="${s.thumbnail}" alt="${escapeHtml(s.title)}" loading="lazy">
          <button class="fav-btn ${isFav ? "faved" : ""}" data-song-id="${s.song_id}"
            onclick="window.toggleFavorite(event, '${s.song_id}', '${escapeAttr(s.title)}', '${escapeAttr(s.artist_name)}', '${s.thumbnail}')"
            title="${isFav ? "取消收藏" : "收藏到我的最愛"}">${isFav ? "⭐" : "☆"}</button>
          ${s.language_label ? `<div class="lang-badge">${s.language_label}</div>` : ""}
          <div class="cached-badge">⚡ 快取秒播</div>
          <span class="song-duration">${s.duration ? formatTime(s.duration) : ""}</span>
        </div>
        <div class="song-info">
          <div class="song-title" title="${escapeHtml(s.title)}">${escapeHtml(s.title)}</div>
          <div class="song-artist">${escapeHtml(s.artist_name || s.artist || "")}</div>
          <div class="play-count reason-line">💡 ${escapeHtml(s.reason || "")}</div>
          <div class="song-actions">
            <button class="btn btn-primary" onclick="window.addSong('${s.song_id}', '${escapeAttr(s.title)}', '${escapeAttr(s.artist_name)}', '${s.thumbnail}', false)">🎤 點歌</button>
            <button class="btn btn-pink" onclick="window.addSong('${s.song_id}', '${escapeAttr(s.title)}', '${escapeAttr(s.artist_name)}', '${s.thumbnail}', true)">⚡ 插播</button>
          </div>
        </div>
      </div>`;
  }

  function switchLibrary(which) {
    libTabs.forEach(t => t.classList.toggle("active", t.dataset.lib === which));
    if (which === "rankings") loadRankings();
    else if (which === "browse") loadBrowse();
    else if (which === "find") loadFind();
    else if (which === "artists") loadArtists();
    else if (which === "numbers") loadNumbers();
    else if (which === "new") loadNewAndRecommend();
    else if (which === "favorites") loadFavorites();
    else if (which === "history") loadHistory();
    else if (which === "trends") loadTrends();
    else if (which === "recordings") loadRecordings();
    else if (which === "cache") loadCacheManager();
    else if (which === "batch") loadBatch();
    else loadCachedRecommendations();
  }

  libTabs.forEach(tab => {
    tab.addEventListener("click", () => switchLibrary(tab.dataset.lib));
  });

  // extraHtml：擺在標題與歌卡之間的整列區塊（分類瀏覽的語言/歌手篩選列用），
  // 沒有結果時也要留著，不然按了篩選就看不到篩選列，等於卡死。
  function renderSearchResults(songs, sectionTitle = "", extraHtml = "", emptyText = "未找到相符歌曲") {
    let headerHtml = sectionTitle ? `<div style="grid-column: 1/-1; font-size: 16px; font-weight: 700; color: var(--accent-cyan); margin-bottom: 8px;">${sectionTitle}</div>` : "";

    if (!songs || songs.length === 0) {
      searchResults.innerHTML = headerHtml + extraHtml +
        `<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--text-muted);">${emptyText}</div>`;
      return;
    }

    const cardsHtml = songs.map(s => {
      const isCachedBadge = s.is_cached ? `<div class="cached-badge">⚡ 快取秒播</div>` : "";
      const rankBadge = s.rank
        ? `<div class="rank-badge ${s.rank <= 3 ? "" : "rank-other"}">${s.rank <= 3 ? ["🥇","🥈","🥉"][s.rank - 1] : "#" + s.rank}</div>`
        : "";
      const playCount = s.reason
        ? `<div class="play-count reason-line">💡 ${s.reason}</div>`
        : (s.plays
          ? `<div class="play-count">🎤 已點唱 ${s.plays} 次${s.last_played ? " ・ 最近 " + s.last_played.slice(0, 10) : ""}</div>`
          : (s.sung_at
            ? `<div class="play-count">🕘 唱於 ${formatSungAt(s.sung_at)}</div>`
            : ""));
      // 分類瀏覽/新歌榜的歌卡上標語言別與「NEW」，一眼分辨是什麼歌
      const langBadge = s.language_label ? `<div class="lang-badge">${s.language_label}</div>` : "";
      // 歌號。印在每一張歌卡上是這個功能能不能用起來的全部關鍵 ——
      // 沒有人會去記一個只出現在「歌號點歌」那一頁的號碼。
      const numberText = window.NumberSearch
        ? window.NumberSearch.numberLabel(s.number) : "";
      // 印在歌名底下那一行而不是縮圖角落：縮圖四個角已經被
      // 快取秒播／語言別／NEW／收藏星星佔滿了，而歌號要跟**歌名**擺在一起
      // 才記得起來（商用點歌機的紙本歌本就是這樣排的）。
      const numberBadge = numberText
        ? `<span class="number-badge" title="歌號：下次直接打這組數字（它永遠不會變成別首歌）">🔢 ${numberText}</span>`
        : "";
      const newBadge = s.is_new ? `<div class="new-badge">NEW</div>` : "";
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
            ${langBadge}
            ${newBadge}
            ${isCachedBadge}
            <span class="song-duration">${s.duration_string || (s.duration ? formatTime(s.duration) : '')}</span>
          </div>
          <div class="song-info">
            <div class="song-title" title="${s.title}">${s.title}</div>
            <div class="song-artist">${s.uploader || s.artist || 'YouTube'}${numberBadge}</div>
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

    searchResults.innerHTML = headerHtml + extraHtml + cardsHtml;
  }

  // Global Add Song Action
  window.addSong = async (id, title, artist, thumbnail, priority) => {
    try {
      const res = await window.api.addToQueue(
        { id, title, artist, thumbnail, requested_by: nickname }, priority);

      // 點歌額度滿了。這是整個系統唯一一個對使用者說「不行」的地方，所以
      // 講的是完整的一句話（誰、現在幾首、什麼時候可以再點），而且用通知
      // 而不是 alert —— alert 要按確定才消失，被擋一次就打斷一次點歌。
      if (res && res.status === "rejected") {
        // 歡唱時間結束的優先講：「今晚結束了」蓋過「你排太多首」——
        // 講後者會讓人以為刪掉一首就能再點（然後他刪了，再點，再被擋一次）。
        showNotification(res.reason === "room_time_up"
          ? window.RoomView.roomTimeUpNote(res.room)
          : window.QuotaView.quotaRejectionNote(res.quota), 8000);
        return;
      }

      // Brief feedback toast
      // 輪唱開著時要講出「排到哪裡」—— 不講的話使用者看到的是
      // 「我點的歌沒有出現在最後面」，那看起來就跟壞掉一樣（然後他會再點一次）。
      const placed = priority ? "" : window.RotationView.rotationPlacementNote(
        res && res.placement, nickname);
      // 額度快用完時順便講一聲（剩很多的時候不講，見 quotaAddNote）
      const left = window.QuotaView.quotaAddNote(res && res.quota);
      // 歡唱時間快到了也順便講一聲。這句話講在點歌的當下有用，
      // 講在歌被停下來的那一刻就只是事後諸葛。
      const timeLeft = window.RoomView.roomAddNote(res && res.room);
      const parts = [placed, left, timeLeft].filter(Boolean).join("・");
      showNotification(priority
        ? (left ? `⚡ 已成功插播到下一首！${left}` : "⚡ 已成功插播到下一首！")
        : (parts ? `🎤 已加入：${parts}` : "🎤 已加入點歌佇列！"));
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
    // 舞台端每 400ms 回報一次，這是點歌台唯一知道的播放位置 ——
    // 設 A/B 點與進度條跳轉都以它為準
    lastKnownTime = cur;
    lastKnownDuration = msg.duration || 0;
    timeCurrent.textContent = formatTime(cur);
    timeDuration.textContent = formatTime(dur);
    const pct = Math.min(100, (cur / dur) * 100);
    progressBar.style.width = `${pct}%`;
    renderLoopRangeMark();
  });

  // --- 佇列拖曳排序 ---
  // 拖曳進行中不能讓 STATE_UPDATE 重畫佇列（DOM 一換，抓在手上的節點就沒了），
  // 先把最新狀態存起來，放手後補畫。
  let queueDragging = false;
  let pendingQueueState = null;

  function queueItems() {
    return Array.from(queueList.querySelectorAll(".queue-item"));
  }

  function startQueueDrag(e) {
    const handle = e.currentTarget;
    const item = handle.closest(".queue-item");
    if (!item) return;
    e.preventDefault();
    const fromIdx = queueItems().indexOf(item);
    if (fromIdx < 0) return;
    queueDragging = true;
    item.classList.add("dragging");
    try { handle.setPointerCapture(e.pointerId); } catch (err) { }

    const onMove = (ev) => {
      ev.preventDefault();
      // 手機上佇列可能比可視範圍長，拖到邊緣時自動捲動
      const listRect = queueList.getBoundingClientRect();
      if (ev.clientY < listRect.top + 44) queueList.scrollTop -= 10;
      else if (ev.clientY > listRect.bottom - 44) queueList.scrollTop += 10;

      const el = document.elementFromPoint(ev.clientX, ev.clientY);
      const over = el && el.closest ? el.closest(".queue-item") : null;
      if (!over || over === item || over.parentElement !== queueList) return;
      const rect = over.getBoundingClientRect();
      const before = ev.clientY < rect.top + rect.height / 2;
      queueList.insertBefore(item, before ? over : over.nextSibling);
    };

    const finish = async (commit) => {
      handle.removeEventListener("pointermove", onMove);
      handle.removeEventListener("pointerup", onUp);
      handle.removeEventListener("pointercancel", onCancel);
      item.classList.remove("dragging");
      queueDragging = false;
      const toIdx = queueItems().indexOf(item);
      if (commit && toIdx >= 0 && toIdx !== fromIdx) {
        try {
          await window.api.reorderQueue(fromIdx, toIdx);
        } catch (err) {
          showNotification("排序同步失敗，請再試一次");
        }
      }
      // 拖曳期間擋下的更新補畫回來（成功的話伺服器也會再廣播一次最新順序）
      if (pendingQueueState) {
        const s = pendingQueueState;
        pendingQueueState = null;
        renderQueue(s);
      }
    };
    const onUp = () => finish(true);
    const onCancel = () => finish(false);
    handle.addEventListener("pointermove", onMove);
    handle.addEventListener("pointerup", onUp);
    handle.addEventListener("pointercancel", onCancel);
  }

  function attachQueueDragHandlers() {
    queueList.querySelectorAll(".queue-drag-handle").forEach(h => {
      h.addEventListener("pointerdown", startQueueDrag);
    });
  }

  /**
   * 公平輪唱的那一條：開關狀態、現在輪到誰、沒人取暱稱的提醒。
   *
   * 關著的時候整條收起來（連同「第 N 輪」小標）—— 先到先唱的包廂不需要
   * 一直看到一組用不到的名詞。
   */
  function renderRotation(state) {
    if (!rotationToggle) return;
    const enabled = !!state.rotation_enabled;
    const summary = state.rotation || {};
    rotationToggle.textContent = window.RotationView.rotationToggleLabel(enabled);
    rotationToggle.classList.toggle("on", enabled);
    rotationBar.style.display = enabled ? "block" : "none";
    if (!enabled) return;
    rotationLineEl.textContent = window.RotationView.rotationLine(summary);
    const hint = window.RotationView.rotationNameHint(summary);
    rotationHintEl.textContent = hint;
    rotationHintEl.style.display = hint ? "block" : "none";
  }

  if (rotationToggle) {
    rotationToggle.addEventListener("click", async () => {
      const next = !rotationToggle.classList.contains("on");
      try {
        // 走共享控制參數：一支手機打開，包廂裡每一台都會收到同一份規則
        await window.api.updateControl({ rotation_enabled: next });
        showNotification(window.RotationView.rotationToggleNote(next));
      } catch (e) {
        alert("切換輪唱失敗: " + e.message);
      }
    });
  }

  /**
   * 每人待唱上限那一條：−／數字／＋ 與底下的用量。
   *
   * 上限是 0（不限）時只剩按鈕，用量那一條整條收起來 —— 沒有規則的包廂
   * 不需要一直看到一排 x/y。
   */
  const QUOTA_MAX = 20;
  // 按「不限」時要回到哪個數字。3 是包廂的常識值（一輪三首，佇列還讀得完），
  // 使用者調過之後就記住他的選擇：切回不限再切回來不該把他的設定忘掉。
  let lastQuotaLimit = 3;

  function currentQuotaLimit() {
    return Math.max(0, Math.floor(Number(quotaLabelBtn && quotaLabelBtn.dataset.limit) || 0));
  }

  function renderQuota(state) {
    if (!quotaLabelBtn) return;
    const limit = Math.max(0, Math.floor(Number(state.pending_limit) || 0));
    quotaLabelBtn.dataset.limit = String(limit);
    quotaLabelBtn.textContent = window.QuotaView.quotaLimitLabel(limit);
    quotaLabelBtn.classList.toggle("on", limit > 0);
    if (limit > 0) lastQuotaLimit = limit;
    const line = window.QuotaView.quotaLine(state.quota || {});
    quotaBar.style.display = line ? "block" : "none";
    quotaLineEl.textContent = line;
  }

  function renderAutofill(state) {
    if (!autofillLineEl) return;
    autofillLineEl.textContent = window.AutofillView.autofillLine(state.autofill);
  }

  if (randomPickBtn) {
    randomPickBtn.addEventListener("click", async () => {
      randomPickBtn.disabled = true;
      try {
        const res = await window.api.randomPick(nickname);
        showNotification(window.AutofillView.randomPickNote(res), 6000);
      } catch (e) {
        // 被擋下來的理由要照實講：額度滿了跟曲庫沒歌，下一步完全不同
        // （一個是等一首唱完，一個是先去點一首讓它進曲庫）。
        if (e.status === 404) {
          showNotification(window.AutofillView.emptyLibraryNote(), 8000);
        } else if (e.detail && e.detail.error === "room_time_up") {
          showNotification(window.RoomView.roomTimeUpNote(e.detail.room), 8000);
        } else if (e.detail && e.detail.error === "pending_limit_reached") {
          showNotification(window.QuotaView.quotaRejectionNote(e.detail.quota), 8000);
        } else {
          alert("隨機點歌失敗: " + e.message);
        }
      } finally {
        randomPickBtn.disabled = false;
      }
    });
  }

  async function setQuotaLimit(next) {
    const limit = Math.max(0, Math.min(QUOTA_MAX, Math.floor(next)));
    try {
      // 共享控制參數：一支手機調，包廂裡每一台都收到同一份規則
      // （只有「大家都知道」的規則才不會變成吵架的來源）
      await window.api.updateControl({ pending_limit: limit });
      showNotification(window.QuotaView.quotaChangeNote(limit), limit > 0 ? 5000 : 2500);
    } catch (e) {
      alert("調整點歌額度失敗: " + e.message);
    }
  }

  if (quotaLabelBtn) {
    // 按標籤＝在「不限」與上次那個數字之間切換（最常用的兩種狀態）
    quotaLabelBtn.addEventListener("click", () => {
      const limit = currentQuotaLimit();
      setQuotaLimit(limit > 0 ? 0 : lastQuotaLimit);
    });
  }
  if (quotaDownBtn) {
    quotaDownBtn.addEventListener("click", () => setQuotaLimit(currentQuotaLimit() - 1));
  }
  if (quotaUpBtn) {
    quotaUpBtn.addEventListener("click", () => setQuotaLimit(currentQuotaLimit() + 1));
  }

  // --- 包廂計時（歡唱時間）---
  // 伺服器每五秒才推一次狀態（而且只在有事發生時推），所以畫面上那個每秒跳的
  // 數字是本地算出來的：記下「收到快照的時刻」，之後每秒用 room-view 重算一次。
  // 為了一個時鐘而每秒廣播一次整份狀態，是拿包廂的網路換一個本來就算得出來的數字。
  let lastRoom = null;
  let lastRoomAt = 0;

  function roomSince() {
    return lastRoomAt ? (Date.now() - lastRoomAt) / 1000 : 0;
  }

  function paintRoom() {
    if (!roomTimerBox) return;
    const room = lastRoom;
    // 沒開計時就整組不出現：沒有在算時間的包廂不需要看到一個 00:00，
    // 更不需要一顆隨時可能被按到的「開始計時」。
    const on = !!(room && room.enabled);
    roomTimerBox.style.display = on ? "inline-flex" : "none";
    if (!on) {
      roomBar.style.display = "none";
      return;
    }
    const since = roomSince();
    roomClockBtn.textContent = window.RoomView.roomHeaderLabel(room, since);
    // 狀態也寫進 class，讓最後十分鐘與時間到在暗的包廂裡看得出差別
    const left = window.RoomView.roomRemainingSeconds(room, since);
    roomClockBtn.classList.toggle("room-expired", !!room.expired);
    roomClockBtn.classList.toggle(
      "room-soon", !room.expired && room.active
        && left <= window.RoomView.ROOM_STAGE_SHOW_SECONDS);
    const extendMinutes = Math.max(0, Math.floor(Number(room.extend_minutes) || 0));
    roomExtendBtn.textContent = extendMinutes > 0 ? `＋${extendMinutes} 分` : "＋時間";
    // 還沒開始計時的時候，暫停與續時都沒有意義（續時會變成「開始」，
    // 那是一顆會做出乎意料的事的按鈕）
    roomExtendBtn.style.display = room.active ? "inline-flex" : "none";
    roomPauseBtn.style.display = room.active && !room.expired ? "inline-flex" : "none";
    roomPauseBtn.textContent = room.running ? "⏸️" : "▶️";
    roomPauseBtn.title = room.running ? "暫停計時（中場休息）。播放不受影響" : "繼續倒數";

    const line = window.RoomView.roomStatusLine(room, since);
    roomBar.style.display = line ? "block" : "none";
    roomLineEl.textContent = line;
  }

  function renderRoom(state) {
    if (!state || !state.room) return;
    lastRoom = state.room;
    lastRoomAt = Date.now();
    paintRoom();
  }

  // 每秒重畫一次倒數。刻意不用 requestAnimationFrame：這個數字一秒只變一次，
  // 而點歌台常常開著擺在一旁（手機會一直亮著螢幕跑動畫）。
  setInterval(paintRoom, 1000);

  async function roomAction(fn, note) {
    try {
      const res = await fn();
      if (res && res.room) {
        lastRoom = res.room;
        lastRoomAt = Date.now();
        paintRoom();
      }
      if (note) showNotification(note(res && res.room), 5000);
    } catch (e) {
      alert("包廂計時操作失敗: " + e.message);
    }
  }

  if (roomClockBtn) {
    roomClockBtn.addEventListener("click", () => {
      const room = lastRoom || {};
      if (!room.active) {
        roomAction(() => window.api.startRoomTimer(), (r) =>
          `⏱️ 開始計時：${window.RoomView.roomDuration((r && r.total_seconds) || 0)}。`
          + window.RoomView.roomExpiryPolicyNote(r));
        return;
      }
      // 結束計時是會改變包廂規則的操作，而這顆鍵就在最上排（很容易被誤按），
      // 所以要問一次。問句裡要講出「不會停歌」—— 不然沒有人敢按。
      if (!confirm("結束計時？倒數會消失，之後不會再因為時間到而停止播放。"
                   + "（佇列與已經唱過的統計都不受影響）")) return;
      roomAction(() => window.api.stopRoomTimer(), () => "⏱️ 已結束計時，不再倒數");
    });
  }

  if (roomExtendBtn) {
    roomExtendBtn.addEventListener("click", () => {
      roomAction(() => window.api.extendRoomTimer(), (r) =>
        `⏱️ 已續時，還剩 ${window.RoomView.roomDuration(
          window.RoomView.roomRemainingSeconds(r))}`);
    });
  }

  if (roomPauseBtn) {
    roomPauseBtn.addEventListener("click", () => {
      const running = !!(lastRoom && lastRoom.running);
      roomAction(
        () => (running ? window.api.pauseRoomTimer() : window.api.resumeRoomTimer()),
        () => (running ? "⏸️ 已暫停計時（播放不受影響）" : "▶️ 繼續倒數"));
    });
  }

  // 提醒（剩十分鐘、剩三分鐘、時間到）。停留久一點 —— 這幾句話要看懂才有用，
  // 而且它們一個晚上只會出現三次。
  window.api.on("ROOM_ALERT", (msg) => {
    const data = (msg && msg.data) || {};
    if (data.room) {
      lastRoom = data.room;
      lastRoomAt = Date.now();
      paintRoom();
    }
    const note = window.RoomView.roomAlertNote(data, data.room);
    if (note) showNotification(note, 9000);
  });

  if (rotationResetBtn) {
    rotationResetBtn.addEventListener("click", async () => {
      if (!confirm("把「誰今晚唱過幾首」歸零？\n（已經排好的佇列不會變動，只影響接下來新點的歌）")) return;
      try {
        await window.api.resetRotation();
        showNotification("🔁 輪序已重新開始");
      } catch (e) {
        alert("重設輪序失敗: " + e.message);
      }
    });
  }

  function renderQueue(state) {
    if (queueDragging) {
      pendingQueueState = state;
      return;
    }
    renderRotation(state);
    renderQuota(state);
    renderRoom(state);
    renderAutofill(state);
    const cur = state.current_song;
    if (cur) {
      nowPlayingTitle.textContent = cur.title;
      // 機器接的歌要標出來：不標的話會有人以為是誰偷點的，
      // 而且它隨時會讓位給真正點的歌 —— 看得到標記才預期得到那件事。
      const badge = window.AutofillView.autoBadge(cur);
      nowPlayingArtist.textContent = badge
        ? `${cur.artist || "YouTube Music"} ・ ${badge}`
        : (cur.artist || "YouTube Music");
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
      // 自動接歌開著的話要先講「等一下機器會自己接」—— 否則那首自己冒出來的歌
      // 看起來像是機器壞了（或像是誰偷點的）。
      const note = window.AutofillView.emptyQueueNote(state.autofill);
      queueList.innerHTML = `<div style="text-align: center; color: var(--text-muted); padding: 30px 10px; font-size: 13px;">${note}</div>`;
      return;
    }

    const rounds = (state.rotation && state.rotation.rounds) || {};
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

      // 多人包廂：顯示這首是誰點的
      const requester = item.requested_by
        ? ` <span class="queue-requester">👤 ${escapeHtml(item.requested_by)}</span>`
        : "";

      // 輪唱開著才標輪次：沒開的時候「第 N 輪」只是雜訊（順序就是先到先唱）
      const roundText = state.rotation_enabled
        ? window.RotationView.rotationBadge(rounds[item.queue_id]) : "";
      const roundBadge = roundText ? ` <span class="queue-round">${roundText}</span>` : "";
      // 插播不受輪序影響，標出來才看得懂它為什麼在最前面
      const priorityBadge = item.priority
        ? ` <span class="queue-round" style="color: var(--accent-yellow); background: rgba(255, 222, 89, 0.12);">⚡ 插播</span>`
        : "";

      // 歌號。佇列上標出來是這個功能學得起來的第二個地方：
      // 剛剛點的那一首就在眼前，號碼跟它擺在一起，下次就喊得出來。
      // 還在跑流水線的歌還沒有號碼（備好才發），那時候不佔這個位置。
      const numberText = window.NumberSearch
        ? window.NumberSearch.numberLabel(item.number) : "";
      const numberBadge = numberText
        ? ` <span class="queue-number" title="歌號：下次直接打這組數字">🔢 ${numberText}</span>`
        : "";

      // 一首歌沒得排，兩首以上才給拖曳把手
      const dragHandle = queue.length > 1
        ? `<div class="queue-drag-handle" title="按住拖曳調整順序">⠿</div>`
        : "";

      return `
        <div class="queue-item">
          ${dragHandle}
          <img class="queue-item-thumb" src="${item.thumbnail}">
          <div class="queue-item-info">
            <div class="queue-item-title" title="${item.title}">${item.title}</div>
            <div class="queue-item-status">${statusIndicator}${numberBadge}${requester}${roundBadge}${priorityBadge}</div>
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

    attachQueueDragHandlers();
  }

  function updateDeckControls(state) {
    isPlaying = state.is_playing;
    playPauseBtn.textContent = isPlaying ? "⏸ 暫停" : "▶ 播放";

    // 換歌就重抓曲式分析（副歌位置、段落清單都是跟著歌走的）
    const songId = state.current_song ? state.current_song.song_id : null;
    if (songId !== currentSongId) {
      currentSongId = songId;
      lastKnownTime = 0;
      lastKnownDuration = 0;
      onCurrentSongChanged();
    }

    // A-B 練唱區間：任何一台裝置（含手機）改過都要同步回來
    loopState = {
      enabled: !!state.loop_enabled,
      start: state.loop_start === undefined ? null : state.loop_start,
      end: state.loop_end === undefined ? null : state.loop_end,
    };
    renderLoopUI();

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

    // 升降 Key：手機端與桌機端可能同時開著，任何一邊改（含男調/女調）都要同步回來
    if (state.pitch_shift !== undefined && state.pitch_shift !== currentKeyShift) {
      currentKeyShift = state.pitch_shift;
      renderKeyUI();
    }

    // 麥克風效果：手機端與桌機端可能同時開著，任何一邊改都要同步回來
    syncSlider(micVolumeSlider, micVolumeText, state.mic_volume, true);
    syncSlider(micReverbSlider, micReverbText, state.mic_reverb, true);
    syncSlider(micEchoSlider, micEchoText, state.mic_echo, true);
    syncSlider(micEchoRepeatSlider, micEchoRepeatText, state.mic_echo_repeat, true);
    syncSlider(micToneSlider, micToneText, state.mic_tone, true);

    // 和聲：任何一台裝置（含手機）改過都要同步回來
    syncSlider(harmonyLevelSlider, harmonyLevelText, state.harmony_level, true);
    if (state.harmony_enabled !== undefined || state.harmony_style !== undefined) {
      if (state.harmony_enabled !== undefined) harmonyEnabled = !!state.harmony_enabled;
      if (state.harmony_style !== undefined) harmonyStyle = state.harmony_style;
      renderHarmonyUI();
    }

    // 對唱模式：舞台端第二支麥克風開不起來時會把 duet_enabled 改回 false，
    // 這裡同步回來，按鈕才不會停在「已開啟」而實際上沒在對唱
    if (state.duet_enabled !== undefined && !!state.duet_enabled !== duetEnabled) {
      duetEnabled = !!state.duet_enabled;
      renderDuetUI();
    }
    // 暱稱：不覆蓋正在打字的欄位（別人改名字時把你打一半的內容抽走最惱人）
    if (state.duet_name_a !== undefined && document.activeElement !== duetNameA) {
      duetNameA.value = state.duet_name_a || "";
    }
    if (state.duet_name_b !== undefined && document.activeElement !== duetNameB) {
      duetNameB.value = state.duet_name_b || "";
    }

    if (state.sing_mode !== undefined && state.sing_mode !== singMode) {
      updateSingModeUI(state.sing_mode);
    }
    if (state.mic_echo_time_ms !== undefined &&
        parseInt(micEchoTimeSlider.value, 10) !== state.mic_echo_time_ms) {
      micEchoTimeSlider.value = state.mic_echo_time_ms;
      micEchoTimeText.textContent = `${state.mic_echo_time_ms} ms`;
    }

    // 效果風格按鈕：參數剛好等於某個風格就點亮那顆，手動微調過就全部熄掉
    if (state.mic_reverb !== undefined) {
      updateMicPresetHighlight({
        mic_reverb: state.mic_reverb,
        mic_echo: state.mic_echo,
        mic_echo_repeat: state.mic_echo_repeat,
        mic_echo_time_ms: state.mic_echo_time_ms,
      });
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
  // 男調/女調一鍵切換：商用點歌機的固定偏移慣例 —— 女歌男唱降 4 個 Key、男歌女唱升 4 個 Key。
  const KEY_PRESET_MALE = -4;
  const KEY_PRESET_FEMALE = 4;

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

  keyOrigBtn.addEventListener("click", () => { currentKeyShift = 0; updateKeyShift(); });
  keyMaleBtn.addEventListener("click", () => { currentKeyShift = KEY_PRESET_MALE; updateKeyShift(); });
  keyFemaleBtn.addEventListener("click", () => { currentKeyShift = KEY_PRESET_FEMALE; updateKeyShift(); });

  function renderKeyUI() {
    keyValueText.textContent = (currentKeyShift > 0 ? `+${currentKeyShift}` : `${currentKeyShift}`);
    keyOrigBtn.classList.toggle("active", currentKeyShift === 0);
    keyMaleBtn.classList.toggle("active", currentKeyShift === KEY_PRESET_MALE);
    keyFemaleBtn.classList.toggle("active", currentKeyShift === KEY_PRESET_FEMALE);
  }

  function updateKeyShift() {
    renderKeyUI();
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

  // --- 和聲（雙聲部）---
  // 開關、聲部與音量都是共享控制參數：點歌台按下去，舞台端立刻套用，
  // 其他手機看到的也是同一組狀態（同一個包廂只有一套和聲設定才合理）。
  const harmonyToggleBtn = document.getElementById("harmonyToggleBtn");
  const harmonyLevelSlider = document.getElementById("harmonyLevelSlider");
  const harmonyLevelText = document.getElementById("harmonyLevelText");
  const harmonyStyleBtns = document.querySelectorAll(".harmony-style-btn");

  let harmonyEnabled = false;
  let harmonyStyle = "third";

  function renderHarmonyUI() {
    harmonyToggleBtn.textContent = harmonyEnabled ? "已開啟" : "關閉中";
    harmonyToggleBtn.classList.toggle("btn-primary", harmonyEnabled);
    harmonyToggleBtn.classList.toggle("btn-secondary", !harmonyEnabled);
    harmonyStyleBtns.forEach((btn) => {
      btn.classList.toggle("active", harmonyEnabled && btn.dataset.harmony === harmonyStyle);
    });
  }

  harmonyToggleBtn.addEventListener("click", () => {
    harmonyEnabled = !harmonyEnabled;
    renderHarmonyUI();
    window.api.updateControl({ harmony_enabled: harmonyEnabled });
  });

  harmonyStyleBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      harmonyStyle = btn.dataset.harmony;
      // 直接按聲部就等於「我要和聲」——還要再按一次開關才有聲音的話沒人找得到
      const patch = { harmony_style: harmonyStyle };
      if (!harmonyEnabled) {
        harmonyEnabled = true;
        patch.harmony_enabled = true;
      }
      renderHarmonyUI();
      window.api.updateControl(patch);
    });
  });

  bindPercentSlider(harmonyLevelSlider, harmonyLevelText, "harmony_level");
  renderHarmonyUI();

  // --- 對唱模式（兩支麥克風分別評分）---
  // 開關與兩位演唱者的暱稱是共享控制參數；「第二支麥克風接在哪」不在這裡 ——
  // 那是舞台端那台機器的硬體接法（見 player.html 的音訊裝置面板）。
  const duetToggleBtn = document.getElementById("duetToggleBtn");
  const duetNameA = document.getElementById("duetNameA");
  const duetNameB = document.getElementById("duetNameB");

  let duetEnabled = false;

  function renderDuetUI() {
    duetToggleBtn.textContent = duetEnabled ? "已開啟" : "關閉中";
    duetToggleBtn.classList.toggle("btn-primary", duetEnabled);
    duetToggleBtn.classList.toggle("btn-secondary", !duetEnabled);
  }

  duetToggleBtn.addEventListener("click", () => {
    duetEnabled = !duetEnabled;
    renderDuetUI();
    // 舞台端第二支麥克風開不起來時會把這個參數改回 false 並在舞台上說明原因，
    // 所以這裡不用先問「有沒有第二支麥克風」——按下去就知道。
    window.api.updateControl({ duet_enabled: duetEnabled });
  });

  /**
   * 暱稱用 change（離開欄位／按 Enter）而不是 input 送出。
   *
   * 每打一個字就廣播一次的話，包廂裡每台裝置都會在你打字的過程中
   * 一直重畫計分板，而且舞台端的浮字會跟著半成品的名字跳。
   */
  [[duetNameA, "duet_name_a"], [duetNameB, "duet_name_b"]].forEach(([input, key]) => {
    if (!input) return;
    input.addEventListener("change", () => {
      window.api.updateControl({ [key]: input.value.trim().slice(0, 12) });
    });
  });

  renderDuetUI();

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

  // --- 麥克風效果風格預設 ---
  // 商用點歌機的「包廂 / 劇場 / 演唱會」一鍵風格：只動殘響與回音四個參數。
  // 柔化（防嘯叫）是場地相依的設定，不跟著風格走；乾聲照舊是獨立的止血鍵。
  const MIC_PRESETS = {
    room:    { mic_reverb: 0.25, mic_echo: 0.15, mic_echo_repeat: 0.40, mic_echo_time_ms: 280 },
    theater: { mic_reverb: 0.45, mic_echo: 0.25, mic_echo_repeat: 0.35, mic_echo_time_ms: 220 },
    concert: { mic_reverb: 0.60, mic_echo: 0.40, mic_echo_repeat: 0.55, mic_echo_time_ms: 380 },
  };
  const micPresetBtns = document.querySelectorAll(".mic-preset-btn");

  function updateMicPresetHighlight(vals) {
    micPresetBtns.forEach(btn => {
      const p = MIC_PRESETS[btn.dataset.preset];
      const match = p &&
        Math.abs(vals.mic_reverb - p.mic_reverb) < 0.001 &&
        Math.abs(vals.mic_echo - p.mic_echo) < 0.001 &&
        Math.abs(vals.mic_echo_repeat - p.mic_echo_repeat) < 0.001 &&
        Math.round(vals.mic_echo_time_ms) === p.mic_echo_time_ms;
      btn.classList.toggle("active", !!match);
    });
  }

  function applyMicPreset(name) {
    const p = MIC_PRESETS[name];
    if (!p) return;
    micReverbSlider.value = p.mic_reverb; pct(micReverbText, p.mic_reverb);
    micEchoSlider.value = p.mic_echo; pct(micEchoText, p.mic_echo);
    micEchoRepeatSlider.value = p.mic_echo_repeat; pct(micEchoRepeatText, p.mic_echo_repeat);
    micEchoTimeSlider.value = p.mic_echo_time_ms;
    micEchoTimeText.textContent = `${p.mic_echo_time_ms} ms`;
    updateMicPresetHighlight(p);
    window.api.updateControl({ ...p });
    const labels = { room: "🚪 包廂", theater: "🎭 劇場", concert: "🏟️ 演唱會" };
    showNotification(`🎚️ 已套用 ${labels[name]} 效果風格`);
  }

  micPresetBtns.forEach(btn => {
    btn.addEventListener("click", () => applyMicPreset(btn.dataset.preset));
  });

  // 一鍵乾聲：現場破音或嘯叫時最快的止血按鈕
  dryVoiceBtn.addEventListener("click", () => {
    micReverbSlider.value = 0; pct(micReverbText, 0);
    micEchoSlider.value = 0; pct(micEchoText, 0);
    // 「乾聲」是「只剩我自己的聲音」，和聲也算效果，一起關掉才符合這個字面意思
    harmonyEnabled = false;
    renderHarmonyUI();
    window.api.updateControl({ mic_reverb: 0, mic_echo: 0, harmony_enabled: false });
    showNotification("🎙️ 已切換為乾聲（殘響、回音與和聲關閉）");
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

  // --- 練唱模式：A-B 區段循環 + 段落跳轉 ---
  // 伺服器不持有播放位置（媒體在舞台端），所以這裡以 TIME_UPDATE 回報的位置為準，
  // A-B 點則存在共享狀態裡，包廂裡每一台裝置看到的圈選範圍都一樣。

  function renderLoopRangeMark() {
    if (!loopRangeMark) return;
    if (!lastKnownDuration || loopState.start === null || loopState.end === null) {
      loopRangeMark.style.display = "none";
      return;
    }
    const left = Math.max(0, Math.min(100, (loopState.start / lastKnownDuration) * 100));
    const right = Math.max(0, Math.min(100, (loopState.end / lastKnownDuration) * 100));
    loopRangeMark.style.display = "block";
    loopRangeMark.style.left = `${left}%`;
    loopRangeMark.style.width = `${Math.max(0.6, right - left)}%`;
  }

  function renderLoopUI() {
    if (!loopToggleBtn) return;
    loopAText.textContent = loopState.start === null ? "--:--" : formatTime(loopState.start);
    loopBText.textContent = loopState.end === null ? "--:--" : formatTime(loopState.end);
    const ready = loopState.start !== null && loopState.end !== null;
    loopToggleBtn.disabled = !ready;
    loopToggleBtn.style.opacity = ready ? "1" : "0.5";
    loopToggleBtn.textContent = loopState.enabled ? "⏹ 停止循環" : "🔁 開始循環";
    loopToggleBtn.classList.toggle("looping", loopState.enabled);
    if (loopHint && loopState.enabled) {
      loopHint.textContent = `🔁 循環中：${formatTime(loopState.start)} – ${formatTime(loopState.end)}，唱到終點會自動跳回起點。`;
    } else if (loopHint) {
      loopHint.innerHTML = "播放中按 <b>A</b> 標起點、按 <b>B</b> 標終點，就會在這一段反覆練唱；也可以直接按「副歌重唱」。";
    }
    renderLoopRangeMark();
  }

  function setLoopPoint(which) {
    if (!currentSongId) {
      showNotification("⚠️ 目前沒有正在演唱的歌曲");
      return;
    }
    const pos = Math.max(0, lastKnownTime);
    const patch = which === "A" ? { loop_start: pos } : { loop_end: pos };
    // 兩個點都齊了就直接開始循環：商用點歌機按完 B 就跳回 A，不必再按第三顆鍵
    const other = which === "A" ? loopState.end : loopState.start;
    if (other !== null) patch.loop_enabled = true;
    window.api.updateControl(patch);
    showNotification(`${which === "A" ? "🅰️" : "🅱️"} ${which} 點設在 ${formatTime(pos)}`);
  }

  if (loopSetABtn) loopSetABtn.addEventListener("click", () => setLoopPoint("A"));
  if (loopSetBBtn) loopSetBBtn.addEventListener("click", () => setLoopPoint("B"));

  if (loopToggleBtn) {
    loopToggleBtn.addEventListener("click", () => {
      if (loopState.start === null || loopState.end === null) return;
      const next = !loopState.enabled;
      window.api.updateControl({ loop_enabled: next });
      // 開始循環時直接跳到起點，馬上就能練
      if (next) window.api.seek(Math.min(loopState.start, loopState.end));
    });
  }

  if (loopClearBtn) {
    loopClearBtn.addEventListener("click", () => {
      window.api.updateControl({ loop_enabled: false, loop_start: null, loop_end: null });
      showNotification("已清除 A-B 練唱區間");
    });
  }

  // 一鍵副歌：伺服器分析歌詞的重複結構找出副歌，直接圈起來循環
  if (loopChorusBtn) {
    loopChorusBtn.addEventListener("click", async () => {
      const data = await ensureSections();
      const chorus = data && data.chorus;
      if (!chorus) {
        showNotification("😕 這首歌找不到重複的副歌段落，請手動設 A-B 點");
        return;
      }
      await window.api.updateControl({
        loop_start: chorus.start, loop_end: chorus.end, loop_enabled: true
      });
      await window.api.seek(chorus.start);
      showNotification(`🔁 副歌循環 ${formatTime(chorus.start)} – ${formatTime(chorus.end)}（全曲重複 ${chorus.repeats} 次）`);
    });
  }

  // 進度條點一下就跳過去（歌太長時想直接練後半段）
  if (progressTrack) {
    progressTrack.addEventListener("click", (e) => {
      if (!currentSongId || !lastKnownDuration) return;
      const rect = progressTrack.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
      const target = ratio * lastKnownDuration;
      window.api.seek(target);
      showNotification(`⏩ 跳到 ${formatTime(target)}`);
    });
  }

  // 段落清單：點標籤跳到該段，點 🔁 直接循環該段
  if (sectionList) {
    sectionList.addEventListener("click", (e) => {
      const btn = e.target.closest("button");
      if (!btn) return;
      const start = parseFloat(btn.dataset.start);
      if (Number.isNaN(start)) return;
      if (btn.classList.contains("section-loop")) {
        const end = parseFloat(btn.dataset.end);
        window.api.updateControl({ loop_start: start, loop_end: end, loop_enabled: true });
        showNotification(`🔁 循環「${btn.dataset.label}」`);
      } else {
        showNotification(`⏩ 跳到「${btn.dataset.label}」`);
      }
      window.api.seek(start);
    });
  }

  function renderSections(data) {
    if (!sectionList) return;
    const sections = (data && data.sections) || [];
    if (sections.length === 0) {
      sectionList.innerHTML = `<span style="font-size: 12px; color: var(--text-muted);">這首歌還沒有段落資訊（歌詞處理中，或沒抓到歌詞）</span>`;
      return;
    }
    sectionList.innerHTML = sections.map(s => {
      const label = escapeHtml(s.label);
      const attrs = `data-start="${s.start}" data-end="${s.end}" data-label="${escapeHtml(s.label)}"`;
      return `
        <div class="section-chip ${s.kind === "chorus" ? "is-chorus" : ""}">
          <button class="section-jump" ${attrs} title="${escapeHtml(s.preview || s.label)}">
            ${label}<span class="section-time">${formatTime(s.start)}</span>
          </button>
          <button class="section-loop" ${attrs} title="循環練唱這一段">🔁</button>
        </div>`;
    }).join("");
  }

  // 曲式分析是純計算、每首歌結果固定，同一首只跟伺服器要一次
  async function ensureSections() {
    if (!currentSongId) return null;
    if (sectionCache.songId === currentSongId) return sectionCache.data;
    try {
      const data = await window.api.getSections(currentSongId);
      sectionCache = { songId: currentSongId, data };
      return data;
    } catch (e) {
      return null;
    }
  }

  async function onCurrentSongChanged() {
    if (!currentSongId) {
      if (sectionList) sectionList.innerHTML = "";
      sectionCache = { songId: null, data: null };
      return;
    }
    renderSections(await ensureSections());
  }

  lyricOffsetSlider.addEventListener("dblclick", () => {
    lyricOffsetSlider.value = 0;
    updateLyricOffsetLabel(0);
    window.api.updateControl({ lyric_offset_ms: 0 });
  });

  // --- 系統設定頁 ---
  // 表單是照後端回傳的欄位規格（型別、範圍、選項）長出來的，
  // 後端加一個設定欄位，這裡只要補一行標題文字就會自動多一列。
  const settingsBtn = document.getElementById("settingsBtn");
  const settingsModal = document.getElementById("settingsModal");
  const closeSettingsBtn = document.getElementById("closeSettingsBtn");
  const settingsBody = document.getElementById("settingsBody");
  const settingsRuntimeHint = document.getElementById("settingsRuntimeHint");
  const applyDefaultsBtn = document.getElementById("applyDefaultsBtn");
  const resetSettingsBtn = document.getElementById("resetSettingsBtn");

  const SETTINGS_LABELS = {
    default_music_volume: { label: "音樂音量", hint: "伴奏", percent: true },
    default_mic_volume: { label: "麥克風音量", percent: true },
    default_vocal_volume: { label: "導唱人聲", hint: "0 = 純伴奏", percent: true },
    default_pitch_shift: { label: "升降 Key", unit: " 半音" },
    default_mic_reverb: { label: "殘響", hint: "空間感", percent: true },
    default_mic_echo: { label: "回音音量", percent: true },
    default_mic_echo_repeat: { label: "回音重複", percent: true },
    default_mic_echo_time_ms: { label: "回音間隔", unit: " ms" },
    default_mic_tone: { label: "高頻柔化", hint: "防尖銳", percent: true },
    default_harmony_enabled: { label: "開機就開和聲", hint: "預設關" },
    default_harmony_style: {
      label: "和聲聲部",
      choiceLabels: {
        third: "上三度", low_third: "下三度", fifth: "上五度",
        octave: "低八度", duet: "雙聲部",
      },
    },
    default_harmony_level: { label: "和聲音量", percent: true },
    default_duet_enabled: { label: "開機就開對唱", hint: "預設關" },
    duet_crosstalk_margin_db: { label: "串音判定門檻", unit: " dB", step: 1 },
    default_sing_mode: { label: "演唱模式", choiceLabels: { solo: "🎧 單人", party: "🔊 多人" } },
    default_show_pitch: { label: "顯示音準導唱線" },
    default_rotation_enabled: { label: "開機就開輪唱", hint: "預設關（先到先唱）" },
    default_pending_limit: { label: "每人待唱上限", unit: " 首", step: 1,
                             hint: "0 = 不限。算的是「同時排幾首」，不是今晚總共" },
    loudness_normalize: { label: "啟用自動音量平衡", hint: "各首歌一樣大聲" },
    loudness_target_lufs: { label: "目標響度", unit: " LUFS", step: 0.5 },
    guide_duck_enabled: { label: "導唱自動淡出", hint: "唱穩了自動變小聲" },
    guide_duck_depth: { label: "淡出深度", hint: "最多壓多少", percent: true },
    mic_agc_enabled: { label: "麥克風自動增益", hint: "換人唱免調音量" },
    mic_agc_target_db: { label: "目標收音電平", unit: " dBFS", step: 1 },
    cache_limit_gb: { label: "快取上限", unit: " GB", hint: "0 = 不限制", step: 1 },
    cache_auto_cleanup: { label: "自動清理最舊的歌", hint: "超過上限時" },
    batch_enabled: { label: "啟用排程預處理" },
    batch_start_hour: { label: "開工時間", unit: " 時", step: 1 },
    batch_end_hour: { label: "收工時間", unit: " 時", step: 1, hint: "與開工同一時間 = 全天候" },
    batch_pause_while_singing: { label: "有人唱歌時暫停", hint: "建議開著" },
    whisper_model: { label: "歌詞辨識模型", hint: "Whisper" },
    demucs_model: { label: "人聲分離模型", hint: "Demucs" },
    ambient_bg_mode: {
      label: "情境背景",
      choiceLabels: { auto: "沒有 MV 時", always: "一律使用", off: "關閉" },
    },
    ambient_bg_theme: {
      label: "背景主題",
      choiceLabels: {
        auto: "自動（依歌曲）", aurora: "極光", starfield: "星空",
        neon: "霓虹", ocean: "海洋", ember: "燭火",
      },
    },
    ambient_bg_brightness: { label: "背景亮度上限", hint: "字幕看不清就調低", percent: true },
    recording_enabled: { label: "錄唱回放", hint: "把每一次演唱錄起來" },
    recording_max_count: { label: "最多保存幾首", unit: " 首", step: 1 },
    recording_max_mb: { label: "錄音配額", unit: " MB", step: 16 },
    recording_min_sing_seconds: { label: "唱不到幾秒就不留", unit: " 秒", step: 1 },
    recording_mp3_enabled: { label: "錄音轉 MP3", hint: "車機／舊播放器只認 MP3；按下去才轉，轉好的留著當快取" },
    recording_mp3_bitrate: { label: "MP3 位元率", unit: " kbps", step: 32, hint: "192 以上聽不出差別，只是檔案變大" },
    recording_share_enabled: { label: "允許分享錄音", hint: "產生有時效的連結／QR" },
    recording_share_ttl_hours: { label: "連結有效時間", unit: " 小時", step: 1 },
    recording_share_max_downloads: { label: "下載幾次就失效", unit: " 次", step: 1, hint: "0 = 不限" },
    recording_session_gap_hours: { label: "相隔幾小時算換一場", unit: " 小時", step: 1,
                                   hint: "整晚打包與輪序共用：跨午夜的一晚要算同一場" },
    room_timer_enabled: { label: "啟用包廂計時", hint: "預設關（家裡唱歌沒人在算時間）" },
    room_timer_minutes: { label: "一場多久", unit: " 分鐘", step: 30 },
    room_timer_autostart: { label: "第一首歌自動開錶", hint: "免得三小時後才想起來沒按" },
    room_timer_warn_minutes: { label: "第一次提醒", unit: " 分鐘前", step: 1, hint: "0 = 不提醒" },
    room_timer_last_call_minutes: { label: "最後召集", unit: " 分鐘前", step: 1, hint: "0 = 不提醒" },
    room_timer_expire_action: {
      label: "時間到怎麼辦",
      choiceLabels: { finish_song: "唱完這一首再停", notify_only: "只提醒，不停歌" },
    },
    room_timer_extend_minutes: { label: "續時一次加", unit: " 分鐘", step: 5 },
    marquee_enabled: { label: "啟用舞台訊息", hint: "櫃檯把字打到包廂螢幕上" },
    marquee_seconds: { label: "每則停留", unit: " 秒", step: 1, hint: "多則訊息輪播的一輪" },
    marquee_ttl_minutes: { label: "幾分鐘後消失", unit: " 分鐘", step: 1,
                           hint: "要久一點的用「📌 釘住」送" },
    marquee_card_when_idle: { label: "沒在播歌時用大字卡",
                              hint: "播歌中一律降級成上緣那一條，不受此選項影響" },
    service_call_enabled: { label: "啟用服務鈴", hint: "包廂把一件事丟到櫃檯（跑馬燈的反方向）" },
    service_call_stale_minutes: { label: "開多久算過期", unit: " 分鐘", step: 5,
                                  hint: "標成過期而不是刪掉 —— 默默刪掉客人會以為送出去了" },
    service_call_chime: { label: "有新的單時提示一聲",
                          hint: "櫃檯與點歌台是同一台時可以關掉（自己按的鈴自己響很吵）" },
    autofill_enabled: { label: "啟用自動接歌", hint: "沒人點歌時，機器自己從曲庫接一首" },
    autofill_source: {
      label: "照什麼挑",
      choiceLabels: { mixed: "混著挑", favorites: "只挑我的最愛",
                      popular: "只挑常點的歌", fresh: "只挑還沒唱過的" },
    },
    autofill_idle_seconds: { label: "空多久才接", unit: " 秒", step: 5,
                             hint: "0 = 佇列一空就接。太短會跟正在找歌的人搶" },
    autofill_stop_after: { label: "連著接幾首就停", unit: " 首", step: 1,
                           hint: "有人點一首就重新計數" },
    intro_card_enabled: { label: "顯示導唱片頭卡" },
    intro_card_seconds: { label: "片頭卡秒數", unit: " 秒", step: 0.5 },
    settlement_enabled: { label: "顯示唱畢結算畫面" },
    settlement_seconds: { label: "結算畫面秒數", unit: " 秒", step: 0.5 },
  };

  const SETTINGS_GROUPS = [
    {
      title: "🎚️ 開機預設調音",
      hint: "伺服器重開後的初始值。改完可按下方「套用預設到現在」立刻生效。",
      keys: ["default_music_volume", "default_mic_volume", "default_vocal_volume",
             "default_pitch_shift", "default_mic_reverb", "default_mic_echo",
             "default_mic_echo_repeat", "default_mic_echo_time_ms", "default_mic_tone",
             "default_harmony_enabled", "default_harmony_style", "default_harmony_level",
             "default_sing_mode", "default_show_pitch"],
    },
    {
      title: "🔁 公平輪唱",
      hint: "一個人連點五首時，其他人不必等完那五首：新點的歌會照「這是誰的第幾首」" +
            "插進佇列，讓大家輪流唱。已經排好的順序不會被重排，插播也不受影響。" +
            "身分認的是暱稱（右上角設定）—— 沒取暱稱的所有人算同一位，" +
            "所以一整間都沒取名時，行為就等於先到先唱。" +
            "「今晚唱過幾首」隔一段時間沒人唱就自動歸零，那個時數與整晚打包共用" +
            "（下面的「相隔幾小時算換一場」）。",
      keys: ["default_rotation_enabled"],
    },
    {
      title: "🎫 每人待唱上限（點歌額度）",
      hint: "輪唱管的是順序，這一條管的是量：一個人同時最多能有幾首歌在等。" +
            "0 = 不限（預設）。算的是「待唱」而不是「今晚總共唱幾首」—— " +
            "所以排滿了只要等自己其中一首唱完就又能點，不會有人被鎖在今晚之外。" +
            "調低（或中途才打開）時已經排好的歌一首都不會被刪，只是在降回上限" +
            "以下之前點不了新的。插播不受上限限制（那是現場按下去的決定），" +
            "但照樣算進待唱數。身分同樣認暱稱：沒取暱稱的所有人共用一份額度，" +
            "取個暱稱才有屬於自己的。佇列上方可以隨時用 −／＋ 調整。",
      keys: ["default_pending_limit"],
    },
    {
      title: "🎤🎤 對唱模式",
      hint: "兩支麥克風分別評分，唱完比出勝負。串音判定門檻＝兩支麥克風的電平差超過幾 dB " +
            "就只算大聲的那一位（另一位這時收到的多半是隔壁那支漏過來的聲音）。" +
            "房間小、喇叭大聲就調高；調太高會讓唱得比較收的那一位一直沒分數 —— " +
            "結算畫面會顯示被判成串音的時間比例，照它調。",
      keys: ["default_duet_enabled", "duet_crosstalk_margin_db"],
    },
    {
      title: "🔊 自動音量平衡 (EBU R128)",
      hint: "每首歌在處理時量一次整合響度，播放時自動補到同一個目標，" +
            "不用再為了下一首手動轉音量。-14 LUFS 是串流平台通用值。",
      keys: ["loudness_normalize", "loudness_target_lufs"],
    },
    {
      title: "🎚️ 導唱自動淡出",
      hint: "唱得穩的時候導唱人聲自動退到背景，走音或忘詞時立刻回來扶一把（慢慢退、立刻回）。" +
            "導唱音量本來就轉到 0（純伴奏）時這一段不作用。",
      keys: ["guide_duck_enabled", "guide_duck_depth"],
    },
    {
      title: "🎤 麥克風自動增益",
      hint: "換人唱不用重調麥克風音量：機器把每個人的收音電平拉到同一個目標（要降立刻降、要升慢慢升）。" +
            "安靜的時候絕不加大 —— 否則底噪會被一起放大。舞台端按 S 可以看即時電平表。",
      keys: ["mic_agc_enabled", "mic_agc_target_db"],
    },
    {
      title: "🗂️ 快取",
      hint: "超過上限時從最舊的歌開始刪，演唱中與佇列裡的歌絕對不刪。",
      keys: ["cache_limit_gb", "cache_auto_cleanup"],
    },
    {
      title: "🌙 排程預處理",
      hint: "半夜把整張播放清單先跑成伴奏＋字幕，隔天點下去就是秒播。" +
            "跨午夜的時段（例如 23 → 6）也可以設；有人在唱歌時它會自己讓開，" +
            "每處理完一首就重新確認一次現場狀況。清單在「🌙 排程預處理」分頁貼上。",
      keys: ["batch_enabled", "batch_start_hour", "batch_end_hour",
             "batch_pause_while_singing"],
    },
    {
      title: "🤖 AI 模型",
      hint: "模型在伺服器啟動時載入，改完要重開伺服器才會生效。模型越大越準也越慢。",
      keys: ["whisper_model", "demucs_model"],
    },
    {
      title: "🌌 情境背景",
      hint: "沒抓到 MV 的歌（或抓到的其實是一張靜態圖）不會是黑畫面，" +
            "改放會跟著音樂動的情境視覺，底圖是這首歌的封面。" +
            "「自動」主題會依歌曲固定挑一個 —— 同一首歌每次都是同一個背景。" +
            "背景的亮度變化有限速，不會閃；覺得跟字幕搶就把亮度上限調低。",
      keys: ["ambient_bg_mode", "ambient_bg_theme", "ambient_bg_brightness"],
    },
    {
      title: "🎙️ 錄唱回放",
      hint: "把每一次演唱錄下來（伴奏＋麥克風的混音），唱完在「🎙️ 錄唱回放」分頁聽回來。" +
            "預設關閉 —— 打開等於包廂裡的聲音會被存進伺服器，這件事該由人決定。" +
            "錄音跟歌曲快取共用磁碟，所以有兩道上限；超過時從最舊、沒有標記保留（📌）" +
            "的那一筆開始刪。唱太短的（前奏就被切歌、沒人開口）不留，免得把想留的擠掉。",
      keys: ["recording_enabled", "recording_max_count", "recording_max_mb",
             "recording_min_sing_seconds"],
    },
    {
      title: "🎧 錄音轉 MP3",
      hint: "錄音是瀏覽器錄的 webm／mp4，在包廂裡播沒問題，但車機的 USB、" +
            "長輩的舊手機、傳過去給對方直接點開 —— 那些地方多半只認 MP3。" +
            "打開之後每一列會多一顆「🎧 MP3」，按下去才轉（不是錄完就轉，" +
            "那會跟正在放歌的 CPU 搶資源），轉好的留著當快取，第二次是秒回。" +
            "這份快取是可以丟的東西：它有自己的上限（錄音配額的 1/4，額外佔用），" +
            "滿了從最久沒用的那一份開始刪，永遠不會擠掉任何一次演唱。" +
            "需要機器上有 ffmpeg（含 libmp3lame），沒有的話清單上會直接說。",
      keys: ["recording_mp3_enabled", "recording_mp3_bitrate"],
    },
    {
      title: "🔗 錄音分享",
      hint: "唱完那一句「傳給我」：在錄唱回放的每一列按「🔗 分享」產生連結與 QR，" +
            "掃了就能聽、能下載。連結不需要登入就打得開，所以時效是唯一的安全邊界 —— " +
            "預設 24 小時、最長 30 天，沒有「永不過期」。送錯人可以隨時撤銷。" +
            "「下載幾次就失效」只算真的按下載，不算播放（拖進度條會多發好幾個請求）。" +
            "連結指向的是這台機器的網址，所以預設只在同一個網路裡打得開；" +
            "要讓人帶回家聽，請設 KARATUBE_PUBLIC_HOST 指到對外位址。",
      keys: ["recording_share_enabled", "recording_share_ttl_hours",
             "recording_share_max_downloads"],
    },
    {
      title: "📦 整晚打包",
      hint: "收場時的那句「今天晚上的通通給我一份」：錄唱回放最上面那一條可以把" +
            "一整場包成一個 zip（也可以只要某一位唱的）。一場不是照日曆日期算的 —— " +
            "包廂的一場常常是「九點唱到凌晨兩點半」，照日期切會把它剖成兩半。" +
            "這裡調的是「相隔幾小時算換了一場」：調小會把中間休息很久的一晚切成兩場，" +
            "調大則會把下午那一輪跟晚上那一輪算成同一場。",
      keys: ["recording_session_gap_hours"],
    },
    {
      title: "⏱️ 包廂計時（歡唱時間）",
      hint: "商用點歌機的「歡唱時間到」。難的不是倒數，是時間到之後怎麼收場 —— " +
            "所以這裡**沒有**「立刻停掉正在唱的那一首」這個選項：" +
            "被停在副歌那一句的人不會覺得時間到了，只會覺得機器把他關掉。" +
            "預設是讓正在唱的那一首唱完再停，超時的上限因此就是一首歌的長度。" +
            "停下來的只有播放，佇列一首都不會被刪，續時之後接著唱。" +
            "提醒預設兩次（剩 10 分鐘、剩 3 分鐘），每一次都會講出「時間到會怎樣」" +
            "與怎麼續時 —— 不講的話，最後一首的點歌者會以為自己被偷走一首歌。" +
            "倒數、續時與暫停在右上角隨時可以操作。",
      keys: ["room_timer_enabled", "room_timer_minutes", "room_timer_autostart",
             "room_timer_warn_minutes", "room_timer_last_call_minutes",
             "room_timer_expire_action", "room_timer_extend_minutes"],
    },
    {
      title: "📺 舞台訊息（跑馬燈）",
      hint: "商用點歌機的「櫃檯把字打到包廂螢幕上」：餐點到了、生日祝福。" +
            "功能本身三行就寫得完，難的是那段字會蓋掉畫面上的什麼 —— " +
            "舞台那面螢幕上已經沒有空地了，而唯一不能搶的就是歌詞（台上那個人" +
            "正在看它唱歌）。所以訊息只走畫面最上緣，而且是把 HUD **推開**、" +
            "不是疊上去；大字卡只在沒有人唱歌的時候出現，播歌中一律降級成那一條。" +
            "每一則都會自己消失：「您的餐點到了」在四十分鐘之後才出現，" +
            "是比沒有訊息更糟的錯誤資訊。要留久一點的用「📌 釘住」送（最長四小時）。" +
            "訊息在最上排的「📺 舞台訊息」隨時可以送出與撤掉。",
      keys: ["marquee_enabled", "marquee_seconds", "marquee_ttl_minutes",
             "marquee_card_when_idle"],
    },
    {
      title: "🔔 服務鈴（包廂呼叫櫃檯）",
      hint: "舞台訊息的反方向：包廂把一件事丟到櫃檯（送餐、加冰塊、清潔、" +
            "麥克風沒聲音、結帳）。功能本身也是三行就寫得完，難的是**按下去之後**" +
            "—— 一顆沒有回音的鈴會被按第五次，因為按的人沒有辦法分辨" +
            "「櫃檯看到了、正在弄」跟「這顆鍵根本沒作用」。所以：" +
            "同時只有一張未結案的單（再按是**併進去**，櫃檯看到的是一列與" +
            "「按了 3 次」，不是五列一樣的東西）；併單**不重設等待時間**" +
            "（等最久的那一桌不該因為按最多次而排到最後面）；" +
            "狀態分「已送出 / 櫃檯收到了 / 完成 / 取消」四種並且一直留在畫面上" +
            "（跳一下就消失的「已送出」等於沒有回應）；客人可以自己取消，" +
            "而且取消與完成在紀錄上分得開（否則「今晚有幾單沒服務到」永遠問不出來）。" +
            "服務鈴的字**一個都不會上舞台** —— 台上那個人沒有點那份冰塊。" +
            "在最上排的「🔔 服務鈴」按。",
      keys: ["service_call_enabled", "service_call_stale_minutes", "service_call_chime"],
    },
    {
      title: "🎧 自動接歌",
      hint: "商用點歌機沒有「安靜」這個狀態：一首唱完、佇列空了，機器會自己接上下一首。" +
            "真正被那段安靜傷到的是氣氛 —— 一首唱完之後那三十秒沒有聲音，" +
            "所有人會同時低頭滑手機。功能本身聽起來只是「隨機挑一首播」，" +
            "難的全部在**機器什麼時候該閉嘴**：接的歌只從已經備好的曲庫挑" +
            "（絕不趁大家在聊天的時候去下載新歌把 CPU 吃光）；空了要先等一下下" +
            "才接（切歌之後那幾秒鐘多半有人正在找下一首）；有人點歌時，" +
            "機器接的那一首**開播 45 秒內就讓位、超過就唱完再換**" +
            "（已經唱到一半的人被切掉，比點歌的人多等兩分鐘難堪）；" +
            "而且它會自己停下來 —— 連著接完設定的首數就安靜，" +
            "因為「沒有人點歌」最常見的原因是沒有人在了。" +
            "機器接的歌不算任何人的一首：不進點唱排行（否則排行會變成它自己的回音）、" +
            "不進已唱歷史、不佔輪序與額度，也不會把包廂的錶打開。",
      keys: ["autofill_enabled", "autofill_source", "autofill_idle_seconds",
             "autofill_stop_after"],
    },
    {
      title: "🖥️ 舞台演出",
      hint: "片頭卡與結算畫面的開關與停留時間，改完舞台端立刻套用。",
      keys: ["intro_card_enabled", "intro_card_seconds", "settlement_enabled",
             "settlement_seconds"],
    },
  ];

  let settingsSpec = null;
  let settingsValues = null;
  // 最後一次本機操作設定頁的時間，用來擋掉自己造成的廣播重畫
  let lastSettingsInteraction = 0;

  function formatSettingValue(key, value) {
    const meta = SETTINGS_LABELS[key] || {};
    if (meta.percent) return `${Math.round(value * 100)}%`;
    const shown = Number.isInteger(value) ? value : Math.round(value * 100) / 100;
    return `${shown}${meta.unit || ""}`;
  }

  function settingRowHtml(key) {
    const spec = settingsSpec[key];
    if (!spec) return "";
    const meta = SETTINGS_LABELS[key] || {};
    const value = settingsValues[key];
    const label = `<label>${meta.label || key}${meta.hint ? `<small>${meta.hint}</small>` : ""}</label>`;

    if (spec.type === "bool") {
      return `<div class="mixer-row">${label}
        <button class="btn btn-secondary setting-toggle ${value ? "on" : ""}"
                data-key="${key}" style="margin-left: auto;">${value ? "開啟" : "關閉"}</button>
      </div>`;
    }
    if (spec.type === "choice") {
      const options = spec.choices.map(c => {
        const text = (meta.choiceLabels && meta.choiceLabels[c]) || c;
        return `<option value="${c}"${c === value ? " selected" : ""}>${text}</option>`;
      }).join("");
      return `<div class="mixer-row">${label}
        <select class="setting-select" data-key="${key}">${options}</select>
      </div>`;
    }
    // int / float 都用滑桿：手機上滑桿比數字輸入框好按太多
    const step = meta.step || (spec.type === "int" ? 1 : 0.05);
    return `<div class="mixer-row">${label}
      <input type="range" class="setting-range" data-key="${key}"
             min="${spec.min}" max="${spec.max}" step="${step}" value="${value}">
      <span class="slider-value" data-value-for="${key}">${formatSettingValue(key, value)}</span>
    </div>`;
  }

  function renderSettings() {
    if (!settingsSpec || !settingsValues) return;
    settingsBody.innerHTML = SETTINGS_GROUPS.map(group => `
      <div class="mixer-section">
        <div class="mixer-section-title">${group.title}</div>
        ${group.keys.map(settingRowHtml).join("")}
        ${group.hint ? `<p class="mixer-hint">${group.hint}</p>` : ""}
      </div>`).join("");

    settingsBody.querySelectorAll(".setting-range").forEach(el => {
      el.addEventListener("input", (e) => {
        const key = e.target.dataset.key;
        const v = parseFloat(e.target.value);
        lastSettingsInteraction = performance.now();
        settingsValues[key] = v;
        const out = settingsBody.querySelector(`[data-value-for="${key}"]`);
        if (out) out.textContent = formatSettingValue(key, v);
        queueSettingSave(key, v);
      });
    });
    settingsBody.querySelectorAll(".setting-select").forEach(el => {
      el.addEventListener("change", (e) => {
        const key = e.target.dataset.key;
        lastSettingsInteraction = performance.now();
        settingsValues[key] = e.target.value;
        saveSetting(key, e.target.value);
      });
    });
    settingsBody.querySelectorAll(".setting-toggle").forEach(el => {
      el.addEventListener("click", (e) => {
        const key = e.currentTarget.dataset.key;
        lastSettingsInteraction = performance.now();
        const next = !settingsValues[key];
        settingsValues[key] = next;
        e.currentTarget.classList.toggle("on", next);
        e.currentTarget.textContent = next ? "開啟" : "關閉";
        saveSetting(key, next);
      });
    });
  }

  // 滑桿拖曳時不要每一格都打一次 API，放手後 350ms 內沒再動才送
  const settingSaveTimers = {};
  function queueSettingSave(key, value) {
    clearTimeout(settingSaveTimers[key]);
    settingSaveTimers[key] = setTimeout(() => saveSetting(key, value, true), 350);
  }

  async function saveSetting(key, value, quiet = false) {
    try {
      const res = await window.api.updateSettings({ [key]: value });
      settingsValues = res.settings || settingsValues;
      if (!quiet) {
        const meta = SETTINGS_LABELS[key] || {};
        showNotification(`⚙️ 已更新「${meta.label || key}」`);
      }
    } catch (e) {
      alert("設定儲存失敗: " + e.message);
    }
  }

  async function openSettings() {
    settingsModal.classList.add("open");
    try {
      const res = await window.api.getSettings();
      settingsSpec = res.spec || {};
      settingsValues = res.settings || {};
      renderSettings();
      const rt = res.runtime || {};
      // 版本號要看得到：回報問題時「你跑的是哪一版」是第一個要問的事，
      // 而包廂那台機器可能是三個月前 clone 的。
      const version = rt.version ? `　｜　KaraTube v${rt.version}` : "";
      settingsRuntimeHint.textContent =
        `目前運行中：Whisper ${rt.active_whisper_model} ・ Demucs ${rt.active_demucs_model} ・ 運算裝置 ${rt.device}${version}`;
    } catch (e) {
      settingsBody.innerHTML = `<div style="text-align: center; padding: 30px; color: #ff007f;">設定讀取失敗</div>`;
    }
  }

  if (settingsBtn) settingsBtn.addEventListener("click", openSettings);
  if (closeSettingsBtn) closeSettingsBtn.addEventListener("click", () => settingsModal.classList.remove("open"));
  if (settingsModal) {
    settingsModal.addEventListener("click", (e) => {
      if (e.target === settingsModal) settingsModal.classList.remove("open");
    });
  }

  if (applyDefaultsBtn) {
    applyDefaultsBtn.addEventListener("click", async () => {
      await window.api.applyDefaultSettings();
      showNotification("⤵️ 已把預設調音參數套用到目前狀態");
    });
  }

  if (resetSettingsBtn) {
    resetSettingsBtn.addEventListener("click", async () => {
      if (!confirm("確定把所有系統設定恢復成原廠值嗎？")) return;
      const res = await window.api.resetSettings();
      settingsValues = res.settings || settingsValues;
      renderSettings();
      showNotification("♻️ 已恢復原廠設定");
    });
  }

  // 別台裝置改了設定，這裡開著設定頁的話要跟著更新。
  // 但自己剛動過的話不重畫 —— 伺服器會把我們自己的儲存廣播回來，
  // 這時重畫會把手上正在拖的滑桿整個換掉。
  window.api.on("SETTINGS_UPDATE", (msg) => {
    if (!msg.data) return;
    settingsValues = msg.data;
    const busy = performance.now() - lastSettingsInteraction < 2000;
    if (!busy && settingsModal.classList.contains("open") && settingsSpec) renderSettings();
  });

  // --- 舞台訊息（跑馬燈）---
  // 櫃檯把一句話打到包廂螢幕上。顯示的規則全在 marquee-view.js（舞台端載的是
  // 同一支），這裡只負責送出、列出來、撤掉。

  const marqueeBtn = document.getElementById("marqueeBtn");
  const marqueeModal = document.getElementById("marqueeModal");
  const closeMarqueeBtn = document.getElementById("closeMarqueeBtn");
  const marqueeInput = document.getElementById("marqueeInput");
  const marqueeSendBtn = document.getElementById("marqueeSendBtn");
  const marqueeQuickBox = document.getElementById("marqueeQuick");
  const marqueeUrgentBox = document.getElementById("marqueeUrgent");
  const marqueePinnedBox = document.getElementById("marqueePinned");
  const marqueeListBox = document.getElementById("marqueeList");
  const marqueeClearBtn = document.getElementById("marqueeClearBtn");
  const marqueeCountBadge = document.getElementById("marqueeCountBadge");
  const marqueeCharHint = document.getElementById("marqueeCharHint");

  // 常用句。包廂裡真正會用到的就是這幾句，而「要打字」正是這個功能最大的阻力 ——
  // 餐點送到門口的那十秒鐘，沒有人想在手機上打字。
  const MARQUEE_QUICK = [
    "您的餐點到了，請開門",
    "🎂 生日快樂！",
    "麥克風請傳給下一位",
    "請到櫃檯結帳",
    "冷氣已為您調整",
  ];

  let marqueeSnapshot = null;
  let marqueeSnapshotAt = 0;

  function marqueeSince() {
    return marqueeSnapshotAt ? (Date.now() - marqueeSnapshotAt) / 1000 : 0;
  }

  function marqueeIsOpen() {
    return !!(marqueeModal && marqueeModal.classList.contains("open"));
  }

  function paintMarqueeBadge() {
    if (!marqueeBtn) return;
    const enabled = !marqueeSnapshot || marqueeSnapshot.enabled !== false;
    // 關著的時候不是把按鈕藏起來，是讓它講出怎麼打開 —— 藏起來的話，
    // 使用者只會覺得「我記得有這個功能」然後找不到。
    marqueeBtn.classList.toggle("is-off", !enabled);
    const count = window.MarqueeView.marqueeActive(marqueeSnapshot, marqueeSince()).length;
    if (!marqueeCountBadge) return;
    marqueeCountBadge.textContent = count > 0 ? String(count) : "";
    marqueeCountBadge.style.display = count > 0 ? "inline-block" : "none";
  }

  function renderMarqueeList() {
    if (!marqueeListBox) return;
    const since = marqueeSince();
    const active = window.MarqueeView.marqueeActive(marqueeSnapshot, since);
    if (active.length === 0) {
      marqueeListBox.innerHTML =
        '<div class="marquee-empty">螢幕上目前沒有訊息。</div>';
      return;
    }
    marqueeListBox.innerHTML = active.map((msg) => `
      <div class="marquee-row${msg.urgent ? " is-urgent" : ""}">
        <span class="marquee-row-text"></span>
        <button class="btn btn-secondary marquee-drop" data-id="${msg.id}"
                title="從螢幕上撤掉這一則">撤掉</button>
      </div>`).join("");
    // 訊息是包廂裡任何一支手機打進來的，所以文字一律用 textContent 放進去，
    // 不跟著上面那串 HTML 一起拼（拼進去的話，一則帶標籤的訊息就能改掉這一頁）。
    marqueeListBox.querySelectorAll(".marquee-row-text").forEach((el, idx) => {
      el.textContent = window.MarqueeView.marqueeListLine(active[idx], marqueeSnapshot, since);
    });
    marqueeListBox.querySelectorAll(".marquee-drop").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const res = await window.api.deleteMarquee(btn.dataset.id);
        applyMarqueeState(res && res.marquee);
      });
    });
  }

  function applyMarqueeState(state) {
    if (!state) return;
    marqueeSnapshot = state;
    marqueeSnapshotAt = Date.now();
    paintMarqueeBadge();
    if (marqueeIsOpen()) renderMarqueeList();
  }

  function paintMarqueeHint() {
    if (!marqueeCharHint) return;
    const max = (marqueeSnapshot && marqueeSnapshot.max_chars) || 40;
    const used = (marqueeInput && marqueeInput.value.trim().length) || 0;
    const ttl = (marqueeSnapshot && marqueeSnapshot.default_ttl_minutes) || 0;
    const life = marqueePinnedBox && marqueePinnedBox.checked
      ? "釘住的會留著直到撤掉（最長 4 小時）"
      : (ttl > 0 ? `${ttl} 分鐘後自己消失` : "過一會兒自己消失");
    marqueeCharHint.textContent = `${used}/${max} 字・${life}`;
  }

  async function sendMarquee() {
    const text = marqueeInput ? marqueeInput.value.trim() : "";
    if (!text) {
      showNotification(window.MarqueeView.marqueeRejectNote("empty",
        { max_chars: (marqueeSnapshot && marqueeSnapshot.max_chars) || 40 }), 5000);
      return;
    }
    const res = await window.api.sendMarquee({
      text,
      sender: nickname,
      urgent: !!(marqueeUrgentBox && marqueeUrgentBox.checked),
      pinned: !!(marqueePinnedBox && marqueePinnedBox.checked),
    });
    if (res && res.status === "rejected") {
      // 規則擋下來不是錯誤，用說明的語氣講，而且每一句「不行」後面都有下一步
      showNotification(window.MarqueeView.marqueeRejectNote(res.reason, res.detail || {}), 6000);
      applyMarqueeState(res.marquee);
      return;
    }
    if (marqueeInput) marqueeInput.value = "";
    if (marqueeUrgentBox) marqueeUrgentBox.checked = false;
    applyMarqueeState(res && res.marquee);
    paintMarqueeHint();
    showNotification(window.MarqueeView.marqueeSentNote(res && res.message, isPlaying), 5000);
  }

  async function openMarquee() {
    if (marqueeQuickBox && !marqueeQuickBox.dataset.ready) {
      marqueeQuickBox.dataset.ready = "1";
      MARQUEE_QUICK.forEach((phrase) => {
        const btn = document.createElement("button");
        btn.className = "btn btn-secondary marquee-quick-btn";
        btn.textContent = phrase;
        btn.addEventListener("click", () => {
          if (!marqueeInput) return;
          marqueeInput.value = phrase;
          marqueeInput.focus();
          paintMarqueeHint();
        });
        marqueeQuickBox.appendChild(btn);
      });
    }
    marqueeModal.classList.add("open");
    try {
      applyMarqueeState(await window.api.getMarquee());
    } catch (e) {
      renderMarqueeList();
    }
    paintMarqueeHint();
    if (marqueeInput) marqueeInput.focus();
  }

  if (marqueeBtn) marqueeBtn.addEventListener("click", openMarquee);
  if (closeMarqueeBtn) {
    closeMarqueeBtn.addEventListener("click", () => marqueeModal.classList.remove("open"));
  }
  if (marqueeModal) {
    marqueeModal.addEventListener("click", (e) => {
      if (e.target === marqueeModal) marqueeModal.classList.remove("open");
    });
  }
  if (marqueeSendBtn) marqueeSendBtn.addEventListener("click", sendMarquee);
  if (marqueeInput) {
    marqueeInput.addEventListener("input", paintMarqueeHint);
    marqueeInput.addEventListener("keydown", (e) => {
      // Enter 直接送出：這個功能的使用場景是「餐點在門口」，多一次滑鼠移動都嫌久
      if (e.key === "Enter") { e.preventDefault(); sendMarquee(); }
    });
  }
  if (marqueePinnedBox) marqueePinnedBox.addEventListener("change", paintMarqueeHint);
  if (marqueeClearBtn) {
    marqueeClearBtn.addEventListener("click", async () => {
      const res = await window.api.clearMarquee(true);
      applyMarqueeState(res && res.marquee);
    });
  }

  window.api.on("MARQUEE_UPDATE", (msg) => applyMarqueeState(msg && msg.data));

  // 設定頁把功能關掉（或打開）時，按鈕的樣子要當場跟著變 —— 不然那顆鍵會
  // 一直看起來可以按，按下去才發現送不出去。
  window.api.on("SETTINGS_UPDATE", (msg) => {
    const data = (msg && msg.data) || {};
    if (data.marquee_enabled === undefined || !marqueeSnapshot) return;
    marqueeSnapshot = { ...marqueeSnapshot, enabled: !!data.marquee_enabled };
    paintMarqueeBadge();
  });

  // 清單上的「剩 8 分鐘」要自己走。五秒一次就夠（沒有秒數在跳），
  // 而且只有在面板開著的時候才重畫。
  setInterval(() => {
    paintMarqueeBadge();
    if (marqueeIsOpen()) renderMarqueeList();
  }, 5000);

  // 開機先問一次：WebSocket 只推「有變動」的那一刻，中途才打開的點歌台
  // 不問就會以為螢幕上什麼都沒有。
  window.api.getMarquee()
    .then((data) => applyMarqueeState(data))
    .catch(() => { /* 拿不到就當成沒有訊息，下一次 MARQUEE_UPDATE 會補上 */ });

  // --- 服務鈴（包廂呼叫櫃檯）---
  // 跑馬燈的反方向。說法全在 service-view.js，這裡只負責按、顯示、結案。
  // 這一段的重點不是那顆鍵（那是最簡單的部分），是**等待中那一張單一直在
  // 畫面上**：客人分不出「櫃檯在弄」跟「這顆鍵沒作用」的時候，他會再按一次。

  const serviceBtn = document.getElementById("serviceBtn");
  const serviceModal = document.getElementById("serviceModal");
  const closeServiceBtn = document.getElementById("closeServiceBtn");
  const serviceItemsBox = document.getElementById("serviceItems");
  const serviceNoteInput = document.getElementById("serviceNoteInput");
  const serviceRingBtn = document.getElementById("serviceRingBtn");
  const serviceCurrentBox = document.getElementById("serviceCurrent");
  const serviceCurrentLine = document.getElementById("serviceCurrentLine");
  const serviceAckBtn = document.getElementById("serviceAckBtn");
  const serviceDoneBtn = document.getElementById("serviceDoneBtn");
  const serviceCancelBtn = document.getElementById("serviceCancelBtn");
  const serviceReplyInput = document.getElementById("serviceReplyInput");
  const serviceHistoryBox = document.getElementById("serviceHistory");
  const serviceClearBtn = document.getElementById("serviceClearBtn");
  const serviceCountBadge = document.getElementById("serviceCountBadge");

  let serviceSnapshot = null;
  let serviceSnapshotAt = 0;
  let servicePicked = new Set();
  // 上一張看到的單是哪一張。用來決定「這是新的一張嗎」——
  // 響鈴聲要只在第一次響（同一張單響三聲只會讓人把音量關掉）。
  let serviceLastCallId = "";

  function serviceSince() {
    return serviceSnapshotAt ? (Date.now() - serviceSnapshotAt) / 1000 : 0;
  }

  function serviceIsModalOpen() {
    return !!(serviceModal && serviceModal.classList.contains("open"));
  }

  function serviceSpec() {
    return (serviceSnapshot && Array.isArray(serviceSnapshot.items))
      ? serviceSnapshot.items : [];
  }

  /**
   * 最上排那顆鍵。
   *
   * 有單等待時它會亮 —— 這是整個功能唯一一個「不必打開任何視窗就看得到」的
   * 訊號，而櫃檯那一端多半沒有打開視窗。
   */
  function paintServiceBell() {
    if (!serviceBtn) return;
    const enabled = !serviceSnapshot || serviceSnapshot.enabled !== false;
    serviceBtn.classList.toggle("is-off", !enabled);
    const call = serviceSnapshot && serviceSnapshot.call;
    const waiting = window.ServiceView.serviceIsOpen(call);
    serviceBtn.classList.toggle("is-ringing", waiting);
    if (!serviceCountBadge) return;
    serviceCountBadge.textContent = waiting ? "1" : "";
    serviceCountBadge.style.display = waiting ? "inline-block" : "none";
  }

  /** 等待中那一張單的區塊（含等久了變色）。 */
  function renderServiceCurrent() {
    if (!serviceCurrentBox) return;
    const call = serviceSnapshot && serviceSnapshot.call;
    if (!window.ServiceView.serviceIsOpen(call)) {
      serviceCurrentBox.style.display = "none";
      return;
    }
    const since = serviceSince();
    serviceCurrentBox.style.display = "";
    const urgency = window.ServiceView.serviceUrgency(call, since);
    serviceCurrentBox.classList.toggle("is-warn", urgency === "warn");
    serviceCurrentBox.classList.toggle("is-late", urgency === "late");
    if (serviceCurrentLine) {
      // textContent 而不是 innerHTML：備註是使用者打進去的字。
      serviceCurrentLine.textContent =
        window.ServiceView.serviceDeskLine(call, serviceSpec(), since) +
        "\n" + window.ServiceView.serviceStatusLabel(call);
    }
    // 已經收過的單不必再按一次「櫃檯收到」，但那顆鍵不藏起來（藏起來會讓
    // 版面在按下去的瞬間跳動），只是停用。
    if (serviceAckBtn) serviceAckBtn.disabled = String(call.status) === "ACKED";
  }

  function renderServiceHistory() {
    if (!serviceHistoryBox) return;
    const rows = (serviceSnapshot && Array.isArray(serviceSnapshot.history))
      ? serviceSnapshot.history : [];
    if (rows.length === 0) {
      serviceHistoryBox.innerHTML =
        '<div class="marquee-empty">今晚還沒有叫過櫃檯。</div>';
      return;
    }
    serviceHistoryBox.innerHTML = rows.map(() =>
      '<div class="marquee-row"><span class="marquee-row-text"></span></div>').join("");
    serviceHistoryBox.querySelectorAll(".marquee-row-text").forEach((el, idx) => {
      el.textContent = window.ServiceView.serviceHistoryLine(rows[idx], serviceSpec());
    });
  }

  /** 品項按鍵。清單由伺服器給 —— 兩端必須是同一組詞。 */
  function renderServiceItems() {
    if (!serviceItemsBox) return;
    const spec = serviceSpec();
    const signature = spec.map((s) => s.key).join(",");
    if (serviceItemsBox.dataset.signature !== signature) {
      serviceItemsBox.dataset.signature = signature;
      serviceItemsBox.innerHTML = spec.map((s) => `
        <button class="btn btn-secondary service-item-btn" data-key="${s.key}"></button>
      `).join("");
      serviceItemsBox.querySelectorAll(".service-item-btn").forEach((btn, idx) => {
        btn.textContent = `${spec[idx].emoji || ""} ${spec[idx].label || ""}`.trim();
        btn.addEventListener("click", () => {
          const key = btn.dataset.key;
          if (servicePicked.has(key)) servicePicked.delete(key);
          else servicePicked.add(key);
          renderServiceItems();
        });
      });
    }
    serviceItemsBox.querySelectorAll(".service-item-btn").forEach((btn) => {
      btn.classList.toggle("is-on", servicePicked.has(btn.dataset.key));
    });
  }

  function applyServiceState(state, opts = {}) {
    if (!state) return;
    serviceSnapshot = state;
    serviceSnapshotAt = Date.now();
    const call = state.call;
    const callId = (call && call.id) || "";
    // 新的一張單才響。併單不響（伺服器那邊也已經把 chime 關掉了），
    // 自己按的也不響 —— 自己按的鈴自己響一聲只是吵。
    if (opts.chime && callId && callId !== serviceLastCallId && !serviceIsModalOpen()) {
      showNotification(`🔔 包廂叫櫃檯：${window.ServiceView.serviceItemsLabel(call, serviceSpec())}`,
                       8000);
    }
    serviceLastCallId = callId;
    paintServiceBell();
    if (serviceIsModalOpen()) {
      renderServiceItems();
      renderServiceCurrent();
      renderServiceHistory();
    }
  }

  async function ringService() {
    const items = Array.from(servicePicked);
    if (items.length === 0) {
      showNotification(window.ServiceView.serviceRejectNote("empty"), 5000);
      return;
    }
    const res = await window.api.ringService({
      items,
      note: serviceNoteInput ? serviceNoteInput.value.trim() : "",
      by: nickname,
    });
    if (res && res.status === "rejected") {
      showNotification(window.ServiceView.serviceRejectNote(res.reason, res.detail || {}), 6000);
      applyServiceState(res.service);
      return;
    }
    // 送出之後清掉選項與備註：下一次按的多半是別件事，而留著上一次的勾選，
    // 按下去會送出一張自己沒有要的單。
    servicePicked = new Set();
    if (serviceNoteInput) serviceNoteInput.value = "";
    applyServiceState(res && res.service);
    renderServiceItems();
    showNotification(window.ServiceView.serviceSentNote(res && res.call, serviceSpec()), 6000);
  }

  async function serviceAction(fn) {
    const call = serviceSnapshot && serviceSnapshot.call;
    if (!window.ServiceView.serviceIsOpen(call)) {
      showNotification(window.ServiceView.serviceRejectNote("none_open"), 5000);
      return;
    }
    const res = await fn(call.id);
    if (res && res.status === "rejected") {
      showNotification(window.ServiceView.serviceRejectNote(res.reason, res.detail || {}), 6000);
      applyServiceState(res.service);
      return;
    }
    applyServiceState(res && res.service);
  }

  async function openServiceModal() {
    serviceModal.classList.add("open");
    try {
      applyServiceState(await window.api.getServiceCalls());
    } catch (e) { /* 拿不到就先畫舊的，下一次 SERVICE_UPDATE 會補上 */ }
    renderServiceItems();
    renderServiceCurrent();
    renderServiceHistory();
  }

  if (serviceBtn) serviceBtn.addEventListener("click", openServiceModal);
  if (closeServiceBtn) {
    closeServiceBtn.addEventListener("click", () => serviceModal.classList.remove("open"));
  }
  if (serviceModal) {
    serviceModal.addEventListener("click", (e) => {
      if (e.target === serviceModal) serviceModal.classList.remove("open");
    });
  }
  if (serviceRingBtn) serviceRingBtn.addEventListener("click", ringService);
  if (serviceNoteInput) {
    serviceNoteInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); ringService(); }
    });
  }
  if (serviceAckBtn) {
    serviceAckBtn.addEventListener("click", () =>
      serviceAction((id) => window.api.ackServiceCall(id, nickname)));
  }
  if (serviceDoneBtn) {
    serviceDoneBtn.addEventListener("click", () => serviceAction(async (id) => {
      const reply = serviceReplyInput ? serviceReplyInput.value.trim() : "";
      const res = await window.api.resolveServiceCall(id, reply, nickname);
      if (serviceReplyInput) serviceReplyInput.value = "";
      return res;
    }));
  }
  if (serviceCancelBtn) {
    serviceCancelBtn.addEventListener("click", () =>
      serviceAction((id) => window.api.cancelServiceCall(id, nickname)));
  }
  if (serviceClearBtn) {
    serviceClearBtn.addEventListener("click", async () => {
      const res = await window.api.clearServiceHistory();
      applyServiceState(res && res.service);
    });
  }

  window.api.on("SERVICE_UPDATE", (msg) =>
    applyServiceState(msg && msg.data, { chime: !!(msg && msg.chime) }));

  // 設定頁把功能關掉（或打開）時，按鈕的樣子要當場跟著變。
  window.api.on("SETTINGS_UPDATE", (msg) => {
    const data = (msg && msg.data) || {};
    if (data.service_call_enabled === undefined || !serviceSnapshot) return;
    serviceSnapshot = { ...serviceSnapshot, enabled: !!data.service_call_enabled };
    paintServiceBell();
  });

  // 「已等 3 分鐘」要自己走。五秒一次就夠（沒有秒數在跳）。
  setInterval(() => {
    paintServiceBell();
    if (serviceIsModalOpen()) renderServiceCurrent();
  }, 5000);

  // 開機先問一次：中途才打開的點歌台不問的話，會以為現在沒有人在等。
  window.api.getServiceCalls()
    .then((data) => applyServiceState(data))
    .catch(() => { /* 拿不到就當成沒有單，下一次 SERVICE_UPDATE 會補上 */ });

  // --- 櫃檯管理鎖 ---
  //
  // 三件事：右上角那顆鎖頭、輸入密碼的面板，以及**被擋下來時自動把密碼問完、
  // 再把剛剛那個動作接著做完**（api.js 的 staffFetch 會回頭呼叫這裡的 onStaffLocked）。
  //
  // 鎖頭顯示的是「這台裝置」的狀態：解鎖綁 token，櫃檯的平板解開了，
  // 包廂裡的手機照樣是鎖著的 —— 畫面不能說它已經解鎖（見 staff-lock.js 檔頭）。

  const SL = window.StaffLockView;
  const staffLockBtn = document.getElementById("staffLockBtn");
  const staffLockText = document.getElementById("staffLockText");
  const staffLockModal = document.getElementById("staffLockModal");
  const staffLockScope = document.getElementById("staffLockScope");
  const staffLockRecovery = document.getElementById("staffLockRecovery");
  const staffUnlockPane = document.getElementById("staffLockUnlock");
  const staffManagePane = document.getElementById("staffLockManage");
  const staffPinDisplay = document.getElementById("staffPinDisplay");
  const staffPinPad = document.getElementById("staffPinPad");
  const staffUnlockNote = document.getElementById("staffUnlockNote");
  const staffManageNote = document.getElementById("staffManageNote");
  const staffNewPin = document.getElementById("staffNewPin");
  const staffAutoLock = document.getElementById("staffAutoLock");

  let staffState = { enabled: false, locked: false };
  let staffPin = "";
  // 被擋下來的那個動作在等：解鎖成功就 resolve(true)，關掉面板就 resolve(false)
  let staffPendingResolve = null;

  function staffHasToken() {
    return !!(window.api.staffToken && window.api.staffToken());
  }

  function paintStaffLock() {
    if (!SL || !staffLockBtn) return;
    const badge = SL.lockBadge(staffState, staffHasToken());
    staffLockBtn.style.display = badge.show ? "" : "none";
    staffLockBtn.title = badge.title;
    staffLockBtn.dataset.tone = badge.tone;
    staffLockBtn.firstChild.nodeValue = `${badge.icon} `;
    if (staffLockText) staffLockText.textContent = badge.text;
  }

  function renderStaffPin() {
    if (staffPinDisplay && SL) staffPinDisplay.textContent = SL.staffPinMask(staffPin);
  }

  function staffNote(el, message) {
    if (!el) return;
    el.textContent = message ? message.text || "" : "";
    el.dataset.tone = message ? message.tone || "" : "";
  }

  function paintStaffModal() {
    if (!staffLockModal || !SL) return;
    const managing = !staffState.enabled || (staffHasToken() && !staffState.locked);
    staffUnlockPane.style.display = managing ? "none" : "";
    staffManagePane.style.display = managing ? "" : "none";
    staffLockScope.textContent = SL.scopeSummary(staffState);
    staffLockRecovery.textContent = staffState.enabled ? SL.recoveryHint(staffState) : "";
    staffLockRecovery.style.display = staffState.enabled ? "" : "none";
    if (staffNewPin) staffNewPin.placeholder = staffState.enabled ? "換一組新密碼" : "4–8 位數字";
    if (staffAutoLock && document.activeElement !== staffAutoLock) {
      staffAutoLock.value = staffState.auto_lock_minutes || 15;
    }
    if (managing && staffState.enabled) {
      staffNote(staffManageNote, { tone: "ok", text: SL.autoLockText(staffState.unlock_seconds_left) });
    }
    renderStaffPin();
  }

  function applyStaffLock(state) {
    if (!state) return;
    // 伺服器說鎖上了（有人按了上鎖、或閒置到自動上鎖）：手上那把鑰匙同時作廢，
    // 留著只會讓下一個動作跳出一個「已解鎖」卻被擋的畫面。
    if (state.locked && window.api.setStaffToken) window.api.setStaffToken("");
    staffState = state;
    paintStaffLock();
    if (staffLockModal && staffLockModal.classList.contains("open")) paintStaffModal();
  }

  function openStaffLockModal() {
    staffPin = "";
    staffNote(staffUnlockNote, null);
    staffNote(staffManageNote, null);
    if (staffNewPin) staffNewPin.value = "";
    paintStaffModal();
    staffLockModal.classList.add("open");
  }

  function closeStaffLockModal(unlocked = false) {
    staffLockModal.classList.remove("open");
    staffPin = "";
    if (staffPendingResolve) {
      const resolve = staffPendingResolve;
      staffPendingResolve = null;
      resolve(!!unlocked);
    }
  }

  async function refreshStaffLock() {
    try {
      const data = await window.api.getStaffLock();
      applyStaffLock(data && data.lock);
    } catch (e) { /* 問不到就照現況畫，下一次 STAFF_LOCK 廣播會補上 */ }
  }

  async function submitStaffPin() {
    if (!SL.staffPinComplete(staffPin)) {
      staffNote(staffUnlockNote, { tone: "error", text: "密碼至少 4 位數" });
      return false;
    }
    const result = await window.api.unlockStaff(staffPin);
    staffNote(staffUnlockNote, SL.unlockMessage(result));
    staffPin = "";
    renderStaffPin();
    if (result && (result.status === "success" || result.status === "not_enabled")) {
      applyStaffLock(result.lock || staffState);
      closeStaffLockModal(true);
      return true;
    }
    await refreshStaffLock();
    paintStaffModal();
    return false;
  }

  if (staffPinPad) {
    // 數字鍵盤：包廂的燈是暗的，而櫃檯常常是站著單手操作
    staffPinPad.innerHTML = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "⌫", "0", "✓"]
      .map(key => `<button class="btn btn-secondary staff-pin-key" data-key="${key}">${key}</button>`)
      .join("");
    staffPinPad.addEventListener("click", async (e) => {
      const btn = e.target.closest(".staff-pin-key");
      if (!btn) return;
      const key = btn.dataset.key;
      if (key === "⌫") staffPin = SL.staffPinErase(staffPin);
      else if (key === "✓") { await submitStaffPin(); return; }
      else staffPin = SL.staffPinPress(staffPin, key);
      staffNote(staffUnlockNote, null);
      renderStaffPin();
    });
  }

  if (staffLockBtn) staffLockBtn.addEventListener("click", openStaffLockModal);
  const closeStaffLockBtn = document.getElementById("closeStaffLockBtn");
  if (closeStaffLockBtn) closeStaffLockBtn.addEventListener("click", () => closeStaffLockModal(false));
  if (staffLockModal) {
    staffLockModal.addEventListener("click", (e) => {
      if (e.target === staffLockModal) closeStaffLockModal(false);
    });
  }
  const staffUnlockBtn = document.getElementById("staffUnlockBtn");
  if (staffUnlockBtn) staffUnlockBtn.addEventListener("click", () => submitStaffPin());

  const staffSetPinBtn = document.getElementById("staffSetPinBtn");
  if (staffSetPinBtn) {
    staffSetPinBtn.addEventListener("click", async () => {
      const pin = SL.staffPinDigits(staffNewPin.value);
      if (!SL.staffPinComplete(pin)) {
        staffNote(staffManageNote, { tone: "error", text: "密碼要 4–8 位數字" });
        return;
      }
      try {
        const res = await window.api.setStaffPin(pin);
        staffNewPin.value = "";
        applyStaffLock(res.lock);
        // 設完立刻回到上鎖：設密碼的人已經知道密碼，重打一次是三秒鐘的事
        showNotification("櫃檯密碼已設定，機器層級的動作現在需要解鎖", 4000);
        paintStaffModal();
      } catch (err) {
        staffNote(staffManageNote, { tone: "error", text: err.message });
      }
    });
  }

  const staffAutoLockBtn = document.getElementById("staffAutoLockBtn");
  if (staffAutoLockBtn) {
    staffAutoLockBtn.addEventListener("click", async () => {
      try {
        const res = await window.api.setStaffAutoLock(Number(staffAutoLock.value));
        applyStaffLock(res.lock);
        staffNote(staffManageNote, { tone: "ok", text: `閒置 ${res.auto_lock_minutes} 分鐘後自動上鎖` });
      } catch (err) {
        staffNote(staffManageNote, { tone: "error", text: err.message });
      }
    });
  }

  const staffLockNowBtn = document.getElementById("staffLockNowBtn");
  if (staffLockNowBtn) {
    staffLockNowBtn.addEventListener("click", async () => {
      const res = await window.api.lockStaff();
      applyStaffLock(res.lock);
      closeStaffLockModal(false);
      showNotification("已上鎖", 2000);
    });
  }

  const staffDisableBtn = document.getElementById("staffDisableBtn");
  if (staffDisableBtn) {
    staffDisableBtn.addEventListener("click", async () => {
      if (!confirm("停用櫃檯管理鎖？之後每一支連進來的手機都可以刪曲庫、改設定。")) return;
      try {
        const res = await window.api.disableStaffLock();
        applyStaffLock(res.lock);
        closeStaffLockModal(false);
        showNotification("櫃檯管理鎖已停用（回到家用模式）", 3500);
      } catch (err) {
        staffNote(staffManageNote, { tone: "error", text: err.message });
      }
    });
  }

  // 被鎖擋下來時：把密碼面板叫出來，解開就回 true（api.js 會把那個動作重送一次）。
  window.api.onStaffLocked = (body) => new Promise((resolve) => {
    // 連著被擋兩次（設定頁拉了兩格滑桿）：前一個動作先放掉，
    // 不然它會永遠停在那裡等一個不會來的答案。
    if (staffPendingResolve) {
      const previous = staffPendingResolve;
      staffPendingResolve = null;
      previous(false);
    }
    applyStaffLock(body && body.lock);
    openStaffLockModal();
    staffNote(staffUnlockNote, { tone: "", text: SL.blockedMessage(body) });
    staffPendingResolve = resolve;
  });

  // 密碼沒解開（按了關閉、或打錯放棄）：那個動作沒有發生，要講出來 ——
  // 靜靜地沒反應會讓人以為機器壞了，然後再按五次。
  window.api.onStaffBlocked = (body) => showNotification(SL.blockedMessage(body), 4000);

  window.api.on("STAFF_LOCK", (msg) => applyStaffLock(msg && msg.data));
  // 解鎖中的那一顆鎖頭要自己走（「閒置 3 分鐘後自動上鎖」）。
  setInterval(() => {
    if (!staffState.enabled || !staffHasToken()) return;
    refreshStaffLock();
  }, 30000);
  refreshStaffLock();

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
    // 對唱模式送來的是兩位的成績（形狀不一樣：a / b / winner），要分開講
    if (r.duet && r.a && r.b) {
      const name = (side) => side.singer || "麥克風";
      const line = `${name(r.a)} ${r.a.score} 分（${r.a.grade || "-"}）` +
                   ` vs ${name(r.b)} ${r.b.score} 分（${r.b.grade || "-"}）`;
      const verdict = r.winner === "tie"
        ? `🤝 平手（差 ${r.margin} 分）`
        : `🏆 ${name(r.winner === "b" ? r.b : r.a)} 勝出（+${r.margin}）`;
      // 段落對決的主場段落。分段接唱的歌可能兩位都沒有對決段落（欄位是空的），
      // 那就不提 —— 硬湊一句「主場：無」比不講還糟
      const spots = ["a", "b"]
        .filter((k) => r[k].duel_section)
        .map((k) => `${name(r[k])} ${r[k].duel_section}`)
        .join("／");
      const spotPart = spots ? ` ・ ⭐ 主場 ${spots}` : "";
      showNotification(`🎤🎤 ${r.title || "對唱結束"}：${line} ・ ${verdict}${spotPart}`);
      return;
    }
    if (!r.title && !r.score) return;
    const bestPart = r.is_new_best ? " ・ 🎉 刷新個人最佳！" : "";
    // 段落評分：表現太平均時後端兩個欄位都是空的，就不畫蛇添足
    const sectionPart = r.best_section && r.worst_section
      ? ` ・ 💯 ${r.best_section} / 📈 ${r.worst_section}`
      : "";
    showNotification(
      `🏁 ${r.title || "演唱結束"}：${r.score} 分（${r.grade || "-"}）${sectionPart}${bestPart}`);
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

  function escapeHtml(str) {
    if (!str) return "";
    return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  /**
   * 右上角的提示。`ms` 可以拉長 —— 「你的額度滿了，等第 1 位那首唱完」這種
   * 要看懂才有用的句子，2.5 秒剛好夠讀到一半然後消失。
   * 寬度設上限並允許換行，否則長句會在手機上被推出畫面右邊。
   */
  function showNotification(msg, ms = 2500) {
    const div = document.createElement("div");
    div.style.cssText = `
      position: fixed; top: 20px; right: 20px; z-index: 9999;
      max-width: min(420px, calc(100vw - 40px));
      background: linear-gradient(135deg, var(--accent-cyan), #0077b6);
      color: #000; font-weight: 700; padding: 12px 20px; line-height: 1.5;
      border-radius: 12px; box-shadow: 0 4px 20px rgba(0,240,255,0.4);
      animation: fadeIn 0.3s ease;
    `;
    div.textContent = msg;
    document.body.appendChild(div);
    setTimeout(() => div.remove(), Math.max(1000, Number(ms) || 2500));
  }
});
