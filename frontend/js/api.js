// KaraTube API & WebSocket Client
class KaraTubeAPI {
  constructor() {
    this.baseUrl = window.location.origin;
    this.wsUrl = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws`;
    this.socket = null;
    this.listeners = new Map();
    this.reconnectTimer = null;
  }

  // --- REST Endpoints ---
  async getServerInfo() {
    const res = await fetch(`${this.baseUrl}/api/info`);
    return await res.json();
  }

  async search(query) {
    const res = await fetch(`${this.baseUrl}/api/search?q=${encodeURIComponent(query)}`);
    return await res.json();
  }

  async getCachedSongs() {
    const res = await fetch(`${this.baseUrl}/api/cached-songs`);
    return await res.json();
  }

  async getRankings(limit = 24) {
    const res = await fetch(`${this.baseUrl}/api/rankings?limit=${limit}`);
    return await res.json();
  }

  async resetRankings() {
    const res = await fetch(`${this.baseUrl}/api/rankings`, { method: 'DELETE' });
    return await res.json();
  }

  async getFavorites() {
    const res = await fetch(`${this.baseUrl}/api/favorites`);
    return await res.json();
  }

  async toggleFavorite(songData) {
    const res = await fetch(`${this.baseUrl}/api/favorites/toggle`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(songData)
    });
    return await res.json();
  }

  async getHistory(limit = 50) {
    const res = await fetch(`${this.baseUrl}/api/history?limit=${limit}`);
    return await res.json();
  }

  async clearHistory() {
    const res = await fetch(`${this.baseUrl}/api/history`, { method: 'DELETE' });
    return await res.json();
  }

  async submitScore(resultData) {
    const res = await fetch(`${this.baseUrl}/api/scores`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(resultData)
    });
    return await res.json();
  }

  /**
   * 對唱模式的唱畢結算：兩位演唱者的成績一起送。
   *
   * 刻意不是「呼叫兩次 submitScore」—— 兩筆分開送的話，
   * 中間斷線就會只記到一半（歷史上留下一場只有一個人的對唱），
   * 而且會廣播兩次結算通知，包廂裡每支手機都跳兩則。
   */
  async submitDuetScore(resultData) {
    const res = await fetch(`${this.baseUrl}/api/scores/duet`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(resultData)
    });
    return await res.json();
  }

  async getScores(limit = 50) {
    const res = await fetch(`${this.baseUrl}/api/scores?limit=${limit}`);
    return await res.json();
  }

  /**
   * 跨場次段落趨勢：這位演唱者在這首歌一向強在哪一段。
   * `singer` 空字串＝單人演唱的紀錄（對唱才有名字）。
   */
  async getSongTrend(songId, singer = "") {
    const params = singer ? `?singer=${encodeURIComponent(singer)}` : "";
    const res = await fetch(`${this.baseUrl}/api/scores/${songId}/trend${params}`);
    return await res.json();
  }

  // --- 錄唱回放 ---

  /**
   * 上傳一次演唱的錄音。
   *
   * 音檔是 **raw body**、metadata 走 query string —— 不用 multipart 是為了
   * 後端不必多裝一個 `python-multipart`，而這裡要送的就只有「一個檔案 + 幾個欄位」。
   * Content-Type 直接寫錄音的 mime，後端照它決定副檔名（webm / m4a）。
   */
  async uploadRecording(blob, meta) {
    const params = new URLSearchParams();
    Object.entries(meta || {}).forEach(([key, value]) => {
      if (key === "mime" || value === undefined || value === null) return;
      params.set(key, String(value));
    });
    const res = await fetch(`${this.baseUrl}/api/recordings?${params.toString()}`, {
      method: 'POST',
      headers: { 'Content-Type': (meta && meta.mime) || blob.type || 'audio/webm' },
      body: blob
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `錄音上傳失敗 (${res.status})`);
    }
    return await res.json();
  }

  async getRecordings(limit = 100) {
    const res = await fetch(`${this.baseUrl}/api/recordings?limit=${limit}`);
    return await res.json();
  }

  recordingAudioUrl(recId, download = false, format = "") {
    const q = [];
    if (download) q.push("download=1");
    if (format) q.push(`format=${encodeURIComponent(format)}`);
    return `${this.baseUrl}/api/recordings/${recId}/audio${q.length ? `?${q.join("&")}` : ""}`;
  }

  /**
   * 有哪幾場可以整晚打包。一場 = 連續唱的那一段（相隔太久就算換一場），
   * 所以跨午夜的那一晚是一場，不是兩場。
   */
  async getRecordingSessions() {
    const res = await fetch(`${this.baseUrl}/api/recordings/sessions`);
    if (!res.ok) throw new Error(`場次讀取失敗 (${res.status})`);
    return await res.json();
  }

  /**
   * 一整場的 zip 網址。`singer` 只要那個人的那幾首。
   *
   * 刻意只回網址而不是自己 fetch：一包可能三百 MB，用 fetch 拿回來會先
   * 整包住進瀏覽器的記憶體，手機上直接當掉。交給瀏覽器的下載管理員處理。
   */
  sessionZipUrl(key, singer = "") {
    const q = singer ? `?singer=${encodeURIComponent(singer)}` : "";
    return `${this.baseUrl}/api/recordings/sessions/${encodeURIComponent(key)}/zip${q}`;
  }

  /** 把轉好的 MP3 全部丟掉。錄音一個都不會動（MP3 隨時可以重轉）。 */
  async clearMp3Cache() {
    const res = await fetch(`${this.baseUrl}/api/recordings/mp3`, { method: 'DELETE' });
    return await res.json();
  }

  /** 重新偵測 ffmpeg（裝好之後不用重開伺服器）。 */
  async recheckMp3Support() {
    const res = await fetch(`${this.baseUrl}/api/recordings/mp3/recheck`, { method: 'POST' });
    return await res.json();
  }

  async pinRecording(recId, pinned) {
    const res = await fetch(`${this.baseUrl}/api/recordings/${recId}/pin`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(pinned === undefined ? {} : { pinned })
    });
    return await res.json();
  }

  async deleteRecording(recId) {
    const res = await fetch(`${this.baseUrl}/api/recordings/${recId}`, { method: 'DELETE' });
    return await res.json();
  }

  async clearRecordings(includePinned = false) {
    const res = await fetch(
      `${this.baseUrl}/api/recordings${includePinned ? "?include_pinned=1" : ""}`,
      { method: 'DELETE' });
    return await res.json();
  }

  /**
   * 產一個分享連結（有時效）。已經有還有效的就沿用同一個，
   * `newLink=true` 才換新的（順便撤銷舊的）—— 每按一次就讓上一個 QR 失效，
   * 對已經掃過的人來說是莫名其妙的壞掉。
   */
  async shareRecording(recId, { ttlHours, maxDownloads, newLink = false } = {}) {
    const body = { new: newLink };
    if (ttlHours !== undefined) body.ttl_hours = ttlHours;
    if (maxDownloads !== undefined) body.max_downloads = maxDownloads;
    const res = await fetch(`${this.baseUrl}/api/recordings/${recId}/share`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `分享連結產生失敗 (${res.status})`);
    }
    return await res.json();
  }

  async getRecordingShares(recId) {
    const res = await fetch(`${this.baseUrl}/api/recordings/${recId}/shares`);
    return await res.json();
  }

  async revokeShare(token) {
    const res = await fetch(`${this.baseUrl}/api/share/${encodeURIComponent(token)}`,
                            { method: 'DELETE' });
    if (!res.ok) throw new Error(`撤銷失敗 (${res.status})`);
    return await res.json();
  }

  async getScoreTrends(limit = 20) {
    const res = await fetch(`${this.baseUrl}/api/scores/trends?limit=${limit}`);
    return await res.json();
  }

  // --- 曲庫分類瀏覽 / 新歌榜 / 推薦歌單 ---
  async getLibraryFacets() {
    const res = await fetch(`${this.baseUrl}/api/library`);
    return await res.json();
  }

  async getLibrarySongs({ language = "", artist = "", sort = "recent", limit = 120 } = {}) {
    const params = new URLSearchParams({ sort, limit });
    if (language) params.set("language", language);
    if (artist) params.set("artist", artist);
    const res = await fetch(`${this.baseUrl}/api/library/songs?${params}`);
    return await res.json();
  }

  async getNewSongs(limit = 24) {
    const res = await fetch(`${this.baseUrl}/api/library/new?limit=${limit}`);
    return await res.json();
  }

  async getRecommendations(limit = 12) {
    const res = await fetch(`${this.baseUrl}/api/library/recommend?limit=${limit}`);
    return await res.json();
  }

  async getCacheInfo() {
    const res = await fetch(`${this.baseUrl}/api/cache`);
    return await res.json();
  }

  async deleteCachedSong(songId) {
    const res = await fetch(`${this.baseUrl}/api/cache/${songId}`, { method: 'DELETE' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
  }

  async reprocessSong(songId) {
    const res = await fetch(`${this.baseUrl}/api/cache/${songId}/reprocess`, { method: 'POST' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
  }

  async retryQueueItem(queueId) {
    const res = await fetch(`${this.baseUrl}/api/queue/${queueId}/retry`, { method: 'POST' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
  }

  // --- 排程預處理（半夜把整批歌先跑成伴奏＋字幕）---
  async getBatchState() {
    const res = await fetch(`${this.baseUrl}/api/batch`);
    return await res.json();
  }

  async createBatchJob({ sources, name = "", startNow = false, requestedBy = "" }) {
    const res = await fetch(`${this.baseUrl}/api/batch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sources, name, start_now: startNow, requested_by: requestedBy })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
  }

  async setBatchForce(force) {
    const res = await fetch(`${this.baseUrl}/api/batch/force`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ force })
    });
    return await res.json();
  }

  async batchJobAction(jobId, action) {
    const isDelete = action === "delete";
    const url = isDelete
      ? `${this.baseUrl}/api/batch/${jobId}`
      : `${this.baseUrl}/api/batch/${jobId}/${action}`;
    const res = await fetch(url, { method: isDelete ? 'DELETE' : 'POST' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
  }

  async clearFinishedBatchJobs() {
    const res = await fetch(`${this.baseUrl}/api/batch`, { method: 'DELETE' });
    return await res.json();
  }

  // --- 系統設定 ---
  async getSettings() {
    const res = await fetch(`${this.baseUrl}/api/settings`);
    return await res.json();
  }

  async updateSettings(patch) {
    const res = await fetch(`${this.baseUrl}/api/settings`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch)
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
  }

  async resetSettings() {
    const res = await fetch(`${this.baseUrl}/api/settings`, { method: 'DELETE' });
    return await res.json();
  }

  async applyDefaultSettings() {
    const res = await fetch(`${this.baseUrl}/api/settings/apply-defaults`, { method: 'POST' });
    return await res.json();
  }

  // 自動音量平衡：這首歌該套多少增益（伺服器已依目前設定算好）
  async getLoudness(songId) {
    const res = await fetch(`${this.baseUrl}/api/songs/${songId}/loudness`);
    if (!res.ok) return { gain_db: 0, enabled: false, measured: false };
    return await res.json();
  }

  async getQueue() {
    const res = await fetch(`${this.baseUrl}/api/queue`);
    return await res.json();
  }

  async addToQueue(songData, priority = false) {
    const res = await fetch(`${this.baseUrl}/api/queue/add`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...songData, priority })
    });
    const body = await res.json().catch(() => ({}));
    // 409 = 點歌額度滿了。刻意**不丟例外**：這不是錯誤，是規則生效了，
    // 而規則生效要用「說明」的語氣講（見 quota-view.js），不是紅字的失敗訊息。
    // 丟例外的話呼叫端只拿得到 e.message，那一串結論（誰、幾首、何時可以再點）
    // 就得再從字串裡剖回來。
    if (res.status === 409) {
      const detail = (body && body.detail) || {};
      return { status: 'rejected', reason: detail.error || 'rejected',
               quota: detail.quota || null };
    }
    return body;
  }

  async reorderQueue(fromIdx, toIdx) {
    const res = await fetch(`${this.baseUrl}/api/queue/reorder`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ from_idx: fromIdx, to_idx: toIdx })
    });
    return await res.json();
  }

  async removeQueueItem(queueId) {
    const res = await fetch(`${this.baseUrl}/api/queue/${queueId}`, { method: 'DELETE' });
    return await res.json();
  }

  async skipSong() {
    const res = await fetch(`${this.baseUrl}/api/queue/skip`, { method: 'POST' });
    return await res.json();
  }

  async restartSong() {
    const res = await fetch(`${this.baseUrl}/api/queue/restart`, { method: 'POST' });
    return await res.json();
  }

  // 跳到指定秒數：進度條拖曳、段落跳轉、回到 A 點
  async seek(position) {
    const res = await fetch(`${this.baseUrl}/api/seek`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ position })
    });
    return await res.json();
  }

  // 公平輪唱：輪序歸零（換一批客人時用）。開關本身走 updateControl，
  // 因為它是共享狀態 —— 一支手機打開，包廂裡每一台都要看到規則變了。
  async resetRotation() {
    const res = await fetch(`${this.baseUrl}/api/rotation/reset`, { method: 'POST' });
    return await res.json();
  }

  async updateControl(controlData) {
    const res = await fetch(`${this.baseUrl}/api/control`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(controlData)
    });
    return await res.json();
  }

  async triggerSoundEffect(effectName) {
    const res = await fetch(`${this.baseUrl}/api/sound-effect`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ effect: effectName })
    });
    return await res.json();
  }

  async getLyrics(songId) {
    const res = await fetch(`${this.baseUrl}/api/songs/${songId}/lyrics`);
    const data = await res.json();
    return data.lyrics;
  }

  async getPitch(songId) {
    const res = await fetch(`${this.baseUrl}/api/songs/${songId}/pitch`);
    const data = await res.json();
    return data.pitch;
  }

  // 練唱模式用的曲式分析：副歌位置與段落清單
  async getSections(songId) {
    const res = await fetch(`${this.baseUrl}/api/songs/${songId}/sections`);
    if (!res.ok) return { chorus: null, sections: [] };
    return await res.json();
  }

  // --- WebSocket Connection ---
  initWebSocket() {
    if (this.socket && (this.socket.readyState === WebSocket.OPEN || this.socket.readyState === WebSocket.CONNECTING)) {
      return;
    }

    try {
      this.socket = new WebSocket(this.wsUrl);

      this.socket.onopen = () => {
        console.log("[KaraTube WS] Connected");
        this.emit("ws_connected", true);
        if (this.reconnectTimer) {
          clearInterval(this.reconnectTimer);
          this.reconnectTimer = null;
        }
      };

      this.socket.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          this.emit(msg.type, msg);
        } catch (e) {
          console.error("WS Parse error:", e);
        }
      };

      this.socket.onclose = () => {
        console.warn("[KaraTube WS] Closed. Reconnecting in 2s...");
        this.emit("ws_connected", false);
        if (!this.reconnectTimer) {
          this.reconnectTimer = setInterval(() => this.initWebSocket(), 2000);
        }
      };

      this.socket.onerror = (err) => {
        console.error("[KaraTube WS] Error:", err);
      };
    } catch (err) {
      console.error("[KaraTube WS] Init Error:", err);
    }
  }

  send(type, payload = {}) {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ type, ...payload }));
    }
  }

  on(event, callback) {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, []);
    }
    this.listeners.get(event).push(callback);
  }

  off(event, callback) {
    if (this.listeners.has(event)) {
      const filtered = this.listeners.get(event).filter(cb => cb !== callback);
      this.listeners.set(event, filtered);
    }
  }

  emit(event, data) {
    if (this.listeners.has(event)) {
      this.listeners.get(event).forEach(cb => {
        try { cb(data); } catch (e) { console.error("Event handler error:", e); }
      });
    }
  }
}

// Global API instance
window.api = new KaraTubeAPI();
