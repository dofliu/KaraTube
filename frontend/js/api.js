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

  async getScores(limit = 50) {
    const res = await fetch(`${this.baseUrl}/api/scores?limit=${limit}`);
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
