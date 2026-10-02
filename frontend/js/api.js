// KaraTube API & WebSocket Client
//
// 多包廂：這一頁屬於哪一間包廂，從網址的 `?room=` 讀（沒有就是 default）。
// 房號**只從網址來**，不從 localStorage 記 —— 記起來的話，門口那張 QR 掃進來
// 的手機會跳回上一次開的那一間，而那正是「唱到別人的歌」的樣子。
// 切房間走的是換網址（見 switchRoom），所以那件事在網址列上看得見、
// 上一頁回得去、也複製得出去給別人。
const ROOM_PARAM = "room";
const DEFAULT_ROOM_ID = "default";

// 櫃檯那一頁（desk=1）會收到**別間包廂**的這幾種訊息。清單跟後端的
// DESK_FORWARD_TYPES 是同一份（backend/main.py）—— 多列一種的代價是
// 櫃檯那一頁被十間包廂的播放時間淹掉，少列一種是櫃檯看不到有人在按鈴。
const DESK_CROSS_ROOM_TYPES = new Set(["SERVICE_UPDATE", "ROOM_ALERT", "MARQUEE_UPDATE"]);

/** 網址上的房號。認不得的形狀（跟後端同一套規則）一律當成 default。 */
function roomFromLocation(search) {
  const raw = new URLSearchParams(search || "").get(ROOM_PARAM) || "";
  const id = String(raw).trim().toLowerCase();
  return /^[a-z0-9][a-z0-9-]{0,23}$/.test(id) ? id : DEFAULT_ROOM_ID;
}

class KaraTubeAPI {
  constructor() {
    this.baseUrl = window.location.origin;
    this.wsUrl = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws`;
    this.socket = null;
    this.listeners = new Map();
    this.reconnectTimer = null;
    // 這一頁在哪一間包廂。所有 /api/ 請求與 WebSocket 都帶著它。
    this.roomId = roomFromLocation(window.location.search);
    // 櫃檯那一頁（?desk=1）：照樣屬於自己選的那一間，另外多收全店的服務鈴與計時提醒。
    this.isDesk = new URLSearchParams(window.location.search).get("desk") === "1";
    this.rooms = [];
  }

  /**
   * 每一個 /api/ 請求都自動帶上房號。
   *
   * 做成「一律加」而不是「維護一張分房端點清單」：漏掉一條的後果是那一條
   * 默默地操作到 default 那一間（在只有一間包廂的機器上完全看不出來，
   * 直到店裡裝了第二間）。不分房的端點多收一個查詢參數沒有任何影響。
   */
  withRoom(url) {
    try {
      const u = new URL(url, this.baseUrl);
      if (!u.pathname.startsWith("/api/")) return url;
      if (!u.searchParams.has(ROOM_PARAM)) u.searchParams.set(ROOM_PARAM, this.roomId);
      return u.toString();
    } catch (e) {
      return url;
    }
  }

  /** 專案裡所有的 fetch 都走這裡（`staffFetch` 也是），房號才不會漏掉。 */
  fetch(url, options) {
    return window.fetch(this.withRoom(url), options);
  }

  /** 切到另一間包廂 = 換網址。整頁重載，沒有任何「上一間的殘留狀態」。 */
  switchRoom(roomId) {
    const id = String(roomId || DEFAULT_ROOM_ID);
    const u = new URL(window.location.href);
    if (id === DEFAULT_ROOM_ID) u.searchParams.delete(ROOM_PARAM);
    else u.searchParams.set(ROOM_PARAM, id);
    window.location.href = u.toString();
  }

  // --- 包廂 ---
  async listRooms() {
    const res = await window.fetch(`${this.baseUrl}/api/rooms`);
    const data = await res.json();
    this.rooms = data.rooms || [];
    return data;
  }

  async roomsOverview() {
    const res = await window.fetch(`${this.baseUrl}/api/rooms/overview`);
    return await res.json();
  }

  async createRoom(name, id) {
    const res = await this.staffFetch(`${this.baseUrl}/api/rooms`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, id }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error((data.detail && data.detail.error) || `HTTP ${res.status}`);
    return data;
  }

  async renameRoom(id, name) {
    const res = await this.staffFetch(`${this.baseUrl}/api/rooms/${encodeURIComponent(id)}/rename`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error((data.detail && data.detail.error) || `HTTP ${res.status}`);
    return data;
  }

  async deleteRoom(id, force = false) {
    const res = await this.staffFetch(
      `${this.baseUrl}/api/rooms/${encodeURIComponent(id)}?force=${force ? "true" : "false"}`,
      { method: "DELETE" });
    const data = await res.json();
    if (!res.ok) {
      const err = new Error((data.detail && data.detail.error) || `HTTP ${res.status}`);
      err.detail = data.detail;
      throw err;
    }
    return data;
  }

  /** 櫃檯那一頁要的：全店還開著的服務鈴（等最久的排最前面）。 */
  async deskServiceCalls() {
    const res = await window.fetch(`${this.baseUrl}/api/service?all_rooms=true`);
    return await res.json();
  }

  // --- REST Endpoints ---
  async getServerInfo() {
    const res = await this.fetch(`${this.baseUrl}/api/info`);
    return await res.json();
  }

  async search(query) {
    const res = await this.fetch(`${this.baseUrl}/api/search?q=${encodeURIComponent(query)}`);
    return await res.json();
  }

  async getCachedSongs() {
    const res = await this.fetch(`${this.baseUrl}/api/cached-songs`);
    return await res.json();
  }

  async getRankings(limit = 24) {
    const res = await this.fetch(`${this.baseUrl}/api/rankings?limit=${limit}`);
    return await res.json();
  }

  async resetRankings() {
    const res = await this.staffFetch(`${this.baseUrl}/api/rankings`, { method: 'DELETE' });
    return await res.json();
  }

  async getFavorites() {
    const res = await this.fetch(`${this.baseUrl}/api/favorites`);
    return await res.json();
  }

  async toggleFavorite(songData) {
    const res = await this.fetch(`${this.baseUrl}/api/favorites/toggle`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(songData)
    });
    return await res.json();
  }

  async getHistory(limit = 50) {
    const res = await this.fetch(`${this.baseUrl}/api/history?limit=${limit}`);
    return await res.json();
  }

  async clearHistory() {
    const res = await this.staffFetch(`${this.baseUrl}/api/history`, { method: 'DELETE' });
    return await res.json();
  }

  async submitScore(resultData) {
    const res = await this.fetch(`${this.baseUrl}/api/scores`, {
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
    const res = await this.fetch(`${this.baseUrl}/api/scores/duet`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(resultData)
    });
    return await res.json();
  }

  /**
   * 音域：唱畢時把這一次的音高直方圖送回去（舞台端）。
   *
   * 刻意跟 `submitScore` 分開送：結算是使用者正在看的畫面，音域是背景累積。
   * 併在一起的話，直方圖有問題就會讓一張已經算好的成績單回 400。
   */
  async submitVocalRange(singer, bins) {
    const res = await this.fetch(`${this.baseUrl}/api/vocal-range`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ singer, bins })
    });
    return await res.json();
  }

  /** 某個人的音域檔案（暱稱走 query string —— 包廂暱稱是自由文字）。 */
  async getVocalRange(singer) {
    const res = await this.fetch(
      `${this.baseUrl}/api/vocal-range/profile?singer=${encodeURIComponent(singer || "")}`);
    return await res.json();
  }

  /** 這個人 + 這首歌 → 建議移調幾個 Key。 */
  async getKeyAdvice(singer, songId) {
    const res = await this.fetch(`${this.baseUrl}/api/vocal-range/advice`
      + `?singer=${encodeURIComponent(singer || "")}`
      + `&song_id=${encodeURIComponent(songId || "")}`);
    return await res.json();
  }

  /** 「重新認識我的聲音」：把這個人的音域檔案整份刪掉。 */
  async resetVocalRange(singer) {
    const res = await this.fetch(
      `${this.baseUrl}/api/vocal-range?singer=${encodeURIComponent(singer || "")}`,
      { method: 'DELETE' });
    return await res.json();
  }

  async getScores(limit = 50) {
    const res = await this.fetch(`${this.baseUrl}/api/scores?limit=${limit}`);
    return await res.json();
  }

  /**
   * 跨場次段落趨勢：這位演唱者在這首歌一向強在哪一段。
   * `singer` 空字串＝單人演唱的紀錄（對唱才有名字）。
   */
  async getSongTrend(songId, singer = "") {
    const params = singer ? `?singer=${encodeURIComponent(singer)}` : "";
    const res = await this.fetch(`${this.baseUrl}/api/scores/${songId}/trend${params}`);
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
    const res = await this.fetch(`${this.baseUrl}/api/recordings?${params.toString()}`, {
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
    const res = await this.fetch(`${this.baseUrl}/api/recordings?limit=${limit}`);
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
    const res = await this.fetch(`${this.baseUrl}/api/recordings/sessions`);
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
    const res = await this.staffFetch(`${this.baseUrl}/api/recordings/mp3`, { method: 'DELETE' });
    return await res.json();
  }

  /** 重新偵測 ffmpeg（裝好之後不用重開伺服器）。 */
  async recheckMp3Support() {
    const res = await this.staffFetch(`${this.baseUrl}/api/recordings/mp3/recheck`, { method: 'POST' });
    return await res.json();
  }

  async pinRecording(recId, pinned) {
    const res = await this.fetch(`${this.baseUrl}/api/recordings/${recId}/pin`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(pinned === undefined ? {} : { pinned })
    });
    return await res.json();
  }

  async deleteRecording(recId) {
    const res = await this.fetch(`${this.baseUrl}/api/recordings/${recId}`, { method: 'DELETE' });
    return await res.json();
  }

  async clearRecordings(includePinned = false) {
    const res = await this.staffFetch(
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
    const res = await this.fetch(`${this.baseUrl}/api/recordings/${recId}/share`, {
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
    const res = await this.fetch(`${this.baseUrl}/api/recordings/${recId}/shares`);
    return await res.json();
  }

  async revokeShare(token) {
    const res = await this.fetch(`${this.baseUrl}/api/share/${encodeURIComponent(token)}`,
                            { method: 'DELETE' });
    if (!res.ok) throw new Error(`撤銷失敗 (${res.status})`);
    return await res.json();
  }

  // --- 今晚擂台 ---

  /** 這一間這一場的歌王榜（房號由 withRoom 自動帶上）。 */
  async getContest() {
    const res = await this.fetch(`${this.baseUrl}/api/contest`);
    return await res.json();
  }

  /**
   * 開新的一場（換一批客人）。要櫃檯解鎖，所以走 staffFetch。
   *
   * 平常不必呼叫：隔了「一場的空檔」沒有人唱，榜自己就翻新了。
   */
  async resetContest() {
    const res = await this.staffFetch(`${this.baseUrl}/api/contest/reset`, { method: "POST" });
    if (!res.ok) throw new Error(`重設失敗 (${res.status})`);
    return await res.json();
  }

  async getScoreTrends(limit = 20) {
    const res = await this.fetch(`${this.baseUrl}/api/scores/trends?limit=${limit}`);
    return await res.json();
  }

  // --- 曲庫分類瀏覽 / 新歌榜 / 推薦歌單 ---
  async getLibraryFacets() {
    const res = await this.fetch(`${this.baseUrl}/api/library`);
    return await res.json();
  }

  async getLibrarySongs({ language = "", artist = "", sort = "recent", limit = 120 } = {}) {
    const params = new URLSearchParams({ sort, limit });
    if (language) params.set("language", language);
    if (artist) params.set("artist", artist);
    const res = await this.fetch(`${this.baseUrl}/api/library/songs?${params}`);
    return await res.json();
  }

  async getFindKeys() {
    const res = await this.fetch(`${this.baseUrl}/api/library/find/keys`);
    return await res.json();
  }

  async findInLibrary({ q = "", chars = 0, limit = 60 } = {}) {
    const params = new URLSearchParams({ limit });
    if (q) params.set("q", q);
    if (chars > 0) params.set("chars", chars);
    const res = await this.fetch(`${this.baseUrl}/api/library/find?${params}`);
    return await res.json();
  }

  // 歌號鍵盤：這幾碼的候選歌曲 + 「下一個數字按哪些還有歌」。
  // 候選與下一鍵一起回來是刻意的 —— 分兩支的話，鍵盤變灰與清單更新
  // 會落在不同的一幀，看起來像鍵盤慢半拍。
  async getSongNumbers({ prefix = "", limit = 40 } = {}) {
    const params = new URLSearchParams({ limit });
    if (prefix) params.set("prefix", prefix);
    const res = await this.fetch(`${this.baseUrl}/api/library/numbers?${params}`);
    return await res.json();
  }

  // 查一組歌號。打錯、已下架、號碼簿壞掉三種狀況在回覆裡是分開的
  // （見 backend/main.py 的 _number_lookup），畫面才講得出不同的下一步。
  async lookupSongNumber(number) {
    const res = await this.fetch(
      `${this.baseUrl}/api/library/number/${encodeURIComponent(number)}`);
    return await res.json();
  }

  async getArtistKeys() {
    const res = await this.fetch(`${this.baseUrl}/api/library/artists/keys`);
    return await res.json();
  }

  // 歌星清單與那位歌星的歌單一起回來：使用者要的是歌不是名字，
  // 分兩支 API 會讓「按到只剩一位就自動翻開歌單」多閃一次空畫面
  async findArtists({ q = "", artist = "", limit = 60 } = {}) {
    const params = new URLSearchParams({ limit });
    if (q) params.set("q", q);
    if (artist) params.set("artist", artist);
    const res = await this.fetch(`${this.baseUrl}/api/library/artists/find?${params}`);
    return await res.json();
  }

  async getNewSongs(limit = 24) {
    const res = await this.fetch(`${this.baseUrl}/api/library/new?limit=${limit}`);
    return await res.json();
  }

  async getRecommendations(limit = 12) {
    const res = await this.fetch(`${this.baseUrl}/api/library/recommend?limit=${limit}`);
    return await res.json();
  }

  async getCacheInfo() {
    const res = await this.fetch(`${this.baseUrl}/api/cache`);
    return await res.json();
  }

  async deleteCachedSong(songId) {
    const res = await this.staffFetch(`${this.baseUrl}/api/cache/${songId}`, { method: 'DELETE' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
  }

  // --- 字幕對齊 ---
  //
  // 「這首歌的偏移」走自己的路由（帶 song_id），不擠進 updateControl：
  // control 是「寄給現在」，而這個值要寄給**那一首歌** —— 每一次換歌都會開一個
  // 讓遲到的寫入落到新歌上的窗口，而那正是這個功能要修掉的 bug。

  async setSongLyricOffset(songId, offsetMs) {
    const res = await this.fetch(`${this.baseUrl}/api/songs/${songId}/lyric-offset`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ offset_ms: offsetMs })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
  }

  /**
   * 兩點校正的結果：偏移與速度**一起送**。
   *
   * 分兩次送會在伺服器上留下一個「新偏移配舊速度」的中間狀態，而那個狀態會
   * 被廣播出去 —— 包廂裡每一面螢幕都會看到字幕跳一下。
   */
  async setSongCalibration(songId, { offsetMs, rate } = {}) {
    const body = {};
    // 只放有帶到的欄位：後端把缺席當成「這個數字不要動」，
    // 送一個 undefined 進去會變成 null，那是另一件事。
    if (offsetMs !== undefined) body.offset_ms = offsetMs;
    if (rate !== undefined) body.rate = rate;
    const res = await this.fetch(`${this.baseUrl}/api/songs/${songId}/lyric-offset`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
  }

  async clearSongLyricOffset(songId) {
    const res = await this.fetch(`${this.baseUrl}/api/songs/${songId}/lyric-offset`,
                            { method: 'DELETE' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
  }

  async getLyricOffsets() {
    const res = await this.fetch(`${this.baseUrl}/api/lyric-offsets`);
    return await res.json();
  }

  /** 升級成本機基準的另一半：所有已校正的歌各減掉 delta。 */
  async rebaseLyricOffsets(deltaMs) {
    const res = await this.fetch(`${this.baseUrl}/api/lyric-offsets/rebase`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ delta_ms: deltaMs })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
  }

  /**
   * 只重算這首歌的歌詞時間軸（不重新下載、不跑人聲分離）。
   * 是機器層級的動作（會改寫曲庫檔案、吃掉整台機器唯一那個重算名額），
   * 所以走 staffFetch。
   */
  async rebuildLyrics(songId) {
    const res = await this.staffFetch(`${this.baseUrl}/api/cache/${songId}/rebuild-lyrics`,
                                      { method: 'POST' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
  }

  async reprocessSong(songId) {
    const res = await this.staffFetch(`${this.baseUrl}/api/cache/${songId}/reprocess`, { method: 'POST' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
  }

  async retryQueueItem(queueId) {
    const res = await this.fetch(`${this.baseUrl}/api/queue/${queueId}/retry`, { method: 'POST' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
  }

  // --- 本機曲庫匯入（把 cache/import/ 裡的檔案變成曲庫歌曲）---
  // 掃描是 GET（不鎖），真正建立處理任務走 staffFetch（機器層級的動作）。
  async getLocalImports() {
    const res = await this.fetch(`${this.baseUrl}/api/import`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
  }

  async importLocalFiles({ items, name = "", startNow = true, requestedBy = "" }) {
    const res = await this.staffFetch(`${this.baseUrl}/api/import`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items, name, start_now: startNow, requested_by: requestedBy })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
  }

  // --- 排程預處理（半夜把整批歌先跑成伴奏＋字幕）---
  async getBatchState() {
    const res = await this.fetch(`${this.baseUrl}/api/batch`);
    return await res.json();
  }

  async createBatchJob({ sources, name = "", startNow = false, requestedBy = "" }) {
    const res = await this.staffFetch(`${this.baseUrl}/api/batch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sources, name, start_now: startNow, requested_by: requestedBy })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
  }

  async setBatchForce(force) {
    const res = await this.staffFetch(`${this.baseUrl}/api/batch/force`, {
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
    const res = await this.staffFetch(url, { method: isDelete ? 'DELETE' : 'POST' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
  }

  async clearFinishedBatchJobs() {
    const res = await this.staffFetch(`${this.baseUrl}/api/batch`, { method: 'DELETE' });
    return await res.json();
  }

  // --- 系統設定 ---
  async getSettings() {
    const res = await this.fetch(`${this.baseUrl}/api/settings`);
    return await res.json();
  }

  async updateSettings(patch) {
    const res = await this.staffFetch(`${this.baseUrl}/api/settings`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch)
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
  }

  async resetSettings() {
    const res = await this.staffFetch(`${this.baseUrl}/api/settings`, { method: 'DELETE' });
    return await res.json();
  }

  async applyDefaultSettings() {
    const res = await this.staffFetch(`${this.baseUrl}/api/settings/apply-defaults`, { method: 'POST' });
    return await res.json();
  }

  // --- 備份與還原 ---
  //
  // 下載是 POST 而不是 GET，理由在 backend/services/access_policy.py：
  // 那份 zip 裡是全店所有人的資料，它不屬於「唯讀所以不鎖」那一類。
  // 檢查與套用是兩支，因為還原沒有 undo —— 先看「會發生什麼事」，再決定。

  async getBackupPlan() {
    const res = await this.fetch(`${this.baseUrl}/api/backup`);
    return await res.json();
  }

  /** 下載備份。回 Blob，交給呼叫端存檔（備份的用途是離開這台機器）。 */
  async downloadBackup() {
    const res = await this.staffFetch(`${this.baseUrl}/api/backup`, { method: 'POST' });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${res.status}`);
    }
    // 檔名解析跟錄音下載共用 take-rules.js 那一份（它處理得了 `filename*`），
    // 而不是在這裡再寫一個 —— 兩份實作會在某一版之後開始講不一樣的事，
    // 而 frontend/tests/script-scope.test.js 正是為了這件事存在的。
    const parse = (window.TakeRules && window.TakeRules.filenameFromDisposition)
      || (() => '');
    const name = parse(res.headers.get('content-disposition')) || 'karatube-backup.zip';
    return { blob: await res.blob(), filename: name };
  }

  async inspectBackup(file) {
    const res = await this.staffFetch(`${this.baseUrl}/api/restore/inspect`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/zip' },
      body: file,
    });
    const body = await res.json().catch(() => ({}));
    // 400 是「這個檔案不能用」，而那句話本身就是要給人看的 —— 包成報告的
    // 形狀回去，呼叫端才不必為「壞檔」與「可以還原但有警告」寫兩條路。
    if (!res.ok) return { ok: false, problems: [body.detail || `HTTP ${res.status}`] };
    return body;
  }

  async stageRestore(file) {
    const res = await this.staffFetch(`${this.baseUrl}/api/restore`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/zip' },
      body: file,
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
    return body;
  }

  async cancelRestore() {
    const res = await this.staffFetch(`${this.baseUrl}/api/restore`, { method: 'DELETE' });
    return await res.json();
  }

  async getRestoreStatus() {
    const res = await this.fetch(`${this.baseUrl}/api/restore/status`);
    return await res.json();
  }

  // 自動音量平衡：這首歌該套多少增益（伺服器已依目前設定算好）
  async getLoudness(songId) {
    const res = await this.fetch(`${this.baseUrl}/api/songs/${songId}/loudness`);
    if (!res.ok) return { gain_db: 0, enabled: false, measured: false };
    return await res.json();
  }

  async getQueue() {
    const res = await this.fetch(`${this.baseUrl}/api/queue`);
    return await res.json();
  }

  async addToQueue(songData, priority = false) {
    const res = await this.fetch(`${this.baseUrl}/api/queue/add`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...songData, priority })
    });
    const body = await res.json().catch(() => ({}));
    // 409 = 規則擋下來了（點歌額度滿了，或是歡唱時間已經結束）。
    // 刻意**不丟例外**：這不是錯誤，是規則生效了，而規則生效要用「說明」的
    // 語氣講（見 quota-view.js / room-view.js），不是紅字的失敗訊息。
    // 丟例外的話呼叫端只拿得到 e.message，那一串結論（誰、幾首、何時可以再點／
    // 怎麼續時）就得再從字串裡剖回來。
    if (res.status === 409) {
      const detail = (body && body.detail) || {};
      return { status: 'rejected', reason: detail.error || 'rejected',
               quota: detail.quota || null, room: detail.room || null };
    }
    return body;
  }

  // --- 櫃檯管理鎖 ---
  //
  // 機器層級的動作（刪曲庫、改設定、包廂計時…）要帶 `X-Staff-Token`。
  // token 存在 **sessionStorage**：關掉那一頁就沒了，而櫃檯的平板重新整理
  // 不會被踢出去。放 localStorage 的話，那支借給客人查歌的手機會一直帶著
  // 櫃檯的權限；伺服器端另有閒置自動上鎖，兩邊都失手才會留著一把鑰匙。

  staffToken() {
    try { return sessionStorage.getItem('karatube_staff_token') || ''; } catch (e) { return ''; }
  }

  setStaffToken(token) {
    try {
      if (token) sessionStorage.setItem('karatube_staff_token', token);
      else sessionStorage.removeItem('karatube_staff_token');
    } catch (e) { /* 無痕視窗寫不進去：那就每次動作重打一次密碼，功能照樣成立 */ }
  }

  staffHeaders(extra) {
    const headers = Object.assign({}, extra || {});
    const token = this.staffToken();
    if (token) headers['X-Staff-Token'] = token;
    return headers;
  }

  /**
   * 受櫃檯管理鎖保護的請求。
   *
   * 被擋下來時**先問密碼，解開之後把剛剛那個動作接著做完** —— 不自動重送的話，
   * 使用者解完鎖會回到一個什麼都沒發生的畫面，然後得自己想起剛剛按的是哪一顆。
   * （重送的是他本來就按下去的那個動作，不是機器自己決定要做的事。）
   */
  async staffFetch(url, options = {}) {
    const send = () => this.fetch(url, Object.assign({}, options, {
      headers: this.staffHeaders(options.headers),
    }));

    let res = await send();
    if (res.status !== 403) return res;

    const body = await res.clone().json().catch(() => null);
    if (!body || body.code !== 'staff_locked') return res;

    // 這台裝置手上那把鑰匙已經不管用了（過期、被上鎖、或伺服器重開過）
    this.setStaffToken('');
    if (typeof this.onStaffLocked === 'function') {
      let unlocked = false;
      try { unlocked = await this.onStaffLocked(body); } catch (e) { unlocked = false; }
      if (unlocked) {
        res = await send();
        if (res.status !== 403) return res;
      }
    }
    if (typeof this.onStaffBlocked === 'function') this.onStaffBlocked(body);
    return res;
  }

  async getStaffLock() {
    const res = await this.fetch(`${this.baseUrl}/api/staff-lock`);
    return await res.json();
  }

  /** 打密碼解鎖。回傳統一形狀給 staff-lock.js 的 unlockMessage() 說話。 */
  async unlockStaff(pin) {
    const res = await this.fetch(`${this.baseUrl}/api/staff-lock/unlock`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pin })
    });
    const body = await res.json().catch(() => ({}));
    if (res.status === 429) {
      return { status: 'cooldown', retry_after: Number(res.headers.get('Retry-After')) || 5,
               message: body.detail };
    }
    if (res.status === 403) {
      const lock = (await this.getStaffLock().catch(() => ({}))).lock || {};
      return { status: 'denied', message: body.detail, attempts_left: lock.attempts_left,
               broken: lock.broken, env_var: lock.env_var };
    }
    if (body.token) this.setStaffToken(body.token);
    return body;
  }

  /** 上鎖。刻意不需要任何憑據 —— 關門不需要鑰匙。 */
  async lockStaff() {
    const res = await this.fetch(`${this.baseUrl}/api/staff-lock/lock`, { method: 'POST' });
    this.setStaffToken('');
    return await res.json();
  }

  async setStaffPin(pin, currentPin = '') {
    const res = await this.fetch(`${this.baseUrl}/api/staff-lock/pin`, {
      method: 'POST',
      headers: this.staffHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ pin, current_pin: currentPin })
    });
    const body = await res.json().catch(() => ({}));
    // 設完密碼伺服器一律回到上鎖狀態，手上那把舊鑰匙跟著作廢
    this.setStaffToken('');
    if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
    return body;
  }

  async disableStaffLock(currentPin = '') {
    const res = await this.fetch(`${this.baseUrl}/api/staff-lock/disable`, {
      method: 'POST',
      headers: this.staffHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ current_pin: currentPin })
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
    this.setStaffToken('');
    return body;
  }

  async setStaffAutoLock(minutes, currentPin = '') {
    const res = await this.fetch(`${this.baseUrl}/api/staff-lock/auto-lock`, {
      method: 'POST',
      headers: this.staffHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ minutes, current_pin: currentPin })
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
    return body;
  }

  // --- 包廂計時（歡唱時間）---
  // 每一支都回傳最新的計時狀態，畫面不必再問一次；同一份資料也會跟著
  // STATE_UPDATE 廣播給包廂裡所有裝置（倒數要是同一個數字）。

  async getRoomTimer() {
    const res = await this.fetch(`${this.baseUrl}/api/room`);
    return await res.json();
  }

  async startRoomTimer(minutes) {
    return await this._roomAction('start', minutes);
  }

  async extendRoomTimer(minutes) {
    return await this._roomAction('extend', minutes);
  }

  async pauseRoomTimer() {
    return await this._roomAction('pause');
  }

  async resumeRoomTimer() {
    return await this._roomAction('resume');
  }

  async stopRoomTimer() {
    return await this._roomAction('stop');
  }

  async _roomAction(action, minutes) {
    const res = await this.staffFetch(`${this.baseUrl}/api/room/${action}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(minutes === undefined || minutes === null ? {} : { minutes })
    });
    return await res.json();
  }

  // --- 舞台訊息（跑馬燈）---
  // 每一支都回傳最新的整份狀態，畫面不必再問一次；同一份也會用
  // MARQUEE_UPDATE 廣播出去（點歌台的清單與舞台上跑的要是同一份）。

  async getMarquee() {
    const res = await this.fetch(`${this.baseUrl}/api/marquee`);
    return await res.json();
  }

  async sendMarquee({ text, sender = '', urgent = false, pinned = false,
                      seconds, ttlMinutes } = {}) {
    const payload = { text, sender, urgent, pinned };
    if (seconds !== undefined && seconds !== null) payload.seconds = seconds;
    if (ttlMinutes !== undefined && ttlMinutes !== null) payload.ttl_minutes = ttlMinutes;
    const res = await this.staffFetch(`${this.baseUrl}/api/marquee`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const body = await res.json().catch(() => ({}));
    // 409 = 規則擋下來了（滿了、空訊息、功能關著）。跟點歌那一支同樣
    // **不丟例外**：這不是錯誤，是規則生效了，而規則生效要用「說明」的語氣講
    // （見 marquee-view.js marqueeRejectNote）。靜靜地失敗最糟 —— 送出的人
    // 會以為螢幕上已經有字了，然後對著客人說「您看一下螢幕」。
    if (res.status === 409) {
      const detail = (body && body.detail) || {};
      return { status: 'rejected', reason: detail.error || 'rejected', detail,
               marquee: detail.marquee || null };
    }
    return body;
  }

  async deleteMarquee(messageId) {
    const res = await this.staffFetch(`${this.baseUrl}/api/marquee/${messageId}`, { method: 'DELETE' });
    return await res.json();
  }

  async clearMarquee(includePinned = true) {
    const res = await this.staffFetch(
      `${this.baseUrl}/api/marquee?include_pinned=${includePinned ? 'true' : 'false'}`,
      { method: 'DELETE' });
    return await res.json();
  }

  // --- 服務鈴（包廂呼叫櫃檯）---
  // 跑馬燈的反方向。每一支同樣回傳最新的整份狀態，並以 SERVICE_UPDATE 廣播 ——
  // 客人那支手機與櫃檯那一端看到的必須是同一張單、同一個等待時間。

  async getServiceCalls() {
    const res = await this.fetch(`${this.baseUrl}/api/service`);
    return await res.json();
  }

  async ringService({ items = [], note = '', by = '' } = {}) {
    const res = await this.fetch(`${this.baseUrl}/api/service`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items, note, by })
    });
    const body = await res.json().catch(() => ({}));
    // 409 = 規則擋下來了（沒選品項、功能關著）。跟舞台訊息一樣**不丟例外**：
    // 一顆按了之後畫面什麼都沒發生的服務鈴，下一步是有人推開包廂的門。
    if (res.status === 409) {
      const detail = (body && body.detail) || {};
      return { status: 'rejected', reason: detail.error || 'rejected', detail,
               service: detail.service || null };
    }
    return body;
  }

  async _serviceAction(action, payload) {
    // 「收到了」與「完成」是**櫃檯在回答**，受櫃檯管理鎖保護（見
    // backend/services/access_policy.py）；包廂自己按的「取消」不鎖，
    // 走 staffFetch 也不會多問一次密碼（沒被擋就不會問）。
    const res = await this.staffFetch(`${this.baseUrl}/api/service/${action}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload || {})
    });
    const body = await res.json().catch(() => ({}));
    if (res.status === 409) {
      const detail = (body && body.detail) || {};
      return { status: 'rejected', reason: detail.error || 'rejected', detail,
               service: detail.service || null };
    }
    return body;
  }

  async ackServiceCall(id, by = '') { return this._serviceAction('ack', { id, by }); }

  async resolveServiceCall(id, reply = '', by = '') {
    return this._serviceAction('resolve', { id, reply, by });
  }

  async cancelServiceCall(id, by = '') { return this._serviceAction('cancel', { id, by }); }

  async clearServiceHistory() {
    const res = await this.staffFetch(`${this.baseUrl}/api/service/history`,
                                      { method: 'DELETE' });
    return await res.json();
  }

  async reorderQueue(fromIdx, toIdx) {
    const res = await this.fetch(`${this.baseUrl}/api/queue/reorder`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ from_idx: fromIdx, to_idx: toIdx })
    });
    return await res.json();
  }

  async removeQueueItem(queueId) {
    const res = await this.fetch(`${this.baseUrl}/api/queue/${queueId}`, { method: 'DELETE' });
    return await res.json();
  }

  async skipSong() {
    const res = await this.fetch(`${this.baseUrl}/api/queue/skip`, { method: 'POST' });
    return await res.json();
  }

  async restartSong() {
    const res = await this.fetch(`${this.baseUrl}/api/queue/restart`, { method: 'POST' });
    return await res.json();
  }

  // 跳到指定秒數：進度條拖曳、段落跳轉、回到 A 點
  async seek(position) {
    const res = await this.fetch(`${this.baseUrl}/api/seek`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ position })
    });
    return await res.json();
  }

  // 跳過正在倒數的前奏／間奏。刻意不帶秒數：該跳到哪裡只有舞台端算得出來
  // （空檔表在歌詞時間軸上，seek 要用音訊時間軸，中間差著這首歌的偏移與速度）。
  async skipInterlude() {
    const res = await this.fetch(`${this.baseUrl}/api/skip-interlude`, { method: 'POST' });
    return await res.json();
  }

  // 公平輪唱：輪序歸零（換一批客人時用）。開關本身走 updateControl，
  // 因為它是共享狀態 —— 一支手機打開，包廂裡每一台都要看到規則變了。
  async resetRotation() {
    const res = await this.staffFetch(`${this.baseUrl}/api/rotation/reset`, { method: 'POST' });
    return await res.json();
  }

  // 自動接歌（沒有人點歌時，機器自己接一首）。開關在系統設定頁，
  // 這兩支是「現在的狀態」與「現在接的話會接哪一首」。
  async getAutofill() {
    const res = await this.fetch(`${this.baseUrl}/api/autofill`);
    return await res.json();
  }

  async previewAutofill() {
    const res = await this.fetch(`${this.baseUrl}/api/autofill/preview`);
    if (!res.ok) throw new Error('曲庫裡還沒有可以接的歌');
    return await res.json();
  }

  // 🎲 來一首：從已備好的曲庫隨機點一首。算人點的，所以跟手動點歌一樣
  // 會被額度與歡唱時間擋下來（409 帶著整份理由回來）。
  async randomPick(requestedBy = '') {
    const res = await this.fetch(`${this.baseUrl}/api/autofill/random`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ requested_by: requestedBy })
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error((data && data.detail && data.detail.error) || '隨機點歌失敗');
      err.status = res.status;
      err.detail = data && data.detail;
      throw err;
    }
    return data;
  }

  async updateControl(controlData) {
    const res = await this.fetch(`${this.baseUrl}/api/control`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(controlData)
    });
    return await res.json();
  }

  async triggerSoundEffect(effectName) {
    const res = await this.fetch(`${this.baseUrl}/api/sound-effect`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ effect: effectName })
    });
    return await res.json();
  }

  async getLyrics(songId) {
    const res = await this.fetch(`${this.baseUrl}/api/songs/${songId}/lyrics`);
    const data = await res.json();
    return data.lyrics;
  }

  async getPitch(songId) {
    const res = await this.fetch(`${this.baseUrl}/api/songs/${songId}/pitch`);
    const data = await res.json();
    return data.pitch;
  }

  // 練唱模式用的曲式分析：副歌位置與段落清單
  async getSections(songId) {
    const res = await this.fetch(`${this.baseUrl}/api/songs/${songId}/sections`);
    if (!res.ok) return { chorus: null, sections: [] };
    return await res.json();
  }

  // --- WebSocket Connection ---
  initWebSocket() {
    if (this.socket && (this.socket.readyState === WebSocket.OPEN || this.socket.readyState === WebSocket.CONNECTING)) {
      return;
    }

    try {
      // 房號跟著連線走。斷線重連也一樣 —— 重連時如果掉回 default，
      // 那台舞台會開始播別間包廂的歌（見 backend/services/rooms.py 決定三）。
      const params = new URLSearchParams({ room: this.roomId });
      if (this.isDesk) params.set("desk", "1");
      this.socket = new WebSocket(`${this.wsUrl}?${params.toString()}`);

      this.socket.onopen = () => {
        console.log(`[KaraTube WS] Connected (${this.roomId})`);
        this.emit("ws_connected", true);
        if (this.reconnectTimer) {
          clearInterval(this.reconnectTimer);
          this.reconnectTimer = null;
        }
      };

      this.socket.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          // 房號認不得：伺服器已經關掉這條連線。不重試（重試永遠不會成功），
          // 讓畫面講出「這個房號不存在」與下一步（重掃門口那張 QR）。
          if (msg.type === "ROOM_UNKNOWN") {
            this.roomUnknown = msg.data || {};
            this.stopReconnect();
            this.emit("ROOM_UNKNOWN", msg);
            return;
          }
          // 別間包廂的訊息一律丟掉。正常情況下伺服器不會送過來，
          // 但櫃檯那一頁（desk=1）**會**收到每一間的服務鈴與計時提醒 ——
          // 那幾種要讓它收到，其餘的（尤其 STATE_UPDATE）一個都不能畫上自己的畫面。
          if (msg.room && msg.room !== this.roomId && !DESK_CROSS_ROOM_TYPES.has(msg.type)) {
            return;
          }
          this.emit(msg.type, msg);
        } catch (e) {
          console.error("WS Parse error:", e);
        }
      };

      this.socket.onclose = () => {
        this.emit("ws_connected", false);
        if (this.roomUnknown) return;   // 房號不存在，重試永遠不會成功
        console.warn("[KaraTube WS] Closed. Reconnecting in 2s...");
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

  stopReconnect() {
    if (this.reconnectTimer) {
      clearInterval(this.reconnectTimer);
      this.reconnectTimer = null;
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
