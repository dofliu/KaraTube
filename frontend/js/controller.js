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
    else if (which === "new") loadNewAndRecommend();
    else if (which === "favorites") loadFavorites();
    else if (which === "history") loadHistory();
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

    searchResults.innerHTML = headerHtml + extraHtml + cardsHtml;
  }

  // Global Add Song Action
  window.addSong = async (id, title, artist, thumbnail, priority) => {
    try {
      await window.api.addToQueue({ id, title, artist, thumbnail, requested_by: nickname }, priority);
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

  function renderQueue(state) {
    if (queueDragging) {
      pendingQueueState = state;
      return;
    }
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

      // 多人包廂：顯示這首是誰點的
      const requester = item.requested_by
        ? ` <span class="queue-requester">👤 ${escapeHtml(item.requested_by)}</span>`
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
            <div class="queue-item-status">${statusIndicator}${requester}</div>
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
    default_sing_mode: { label: "演唱模式", choiceLabels: { solo: "🎧 單人", party: "🔊 多人" } },
    default_show_pitch: { label: "顯示音準導唱線" },
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
