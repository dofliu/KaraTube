/**
 * 錄唱回放：錄音器 (Take Recorder)
 *
 * 把舞台的混音（伴奏＋導唱＋麥克風＋效果）錄成一個檔案。判斷規則
 * （要不要留、用什麼容器）在 take-rules.js，這一支只處理 MediaRecorder 的
 * 狀態機與那些只有在真的瀏覽器裡才會遇到的事：
 *
 *   * **長度要自己量**。MediaRecorder 吐出來的 webm 標頭裡沒有 Duration
 *     （它是串流容器，錄的時候還不知道會錄多久），`audio.duration` 讀出來是
 *     `Infinity`。清單上的長度因此不能靠檔案，要在這裡用時鐘量並跟著上傳。
 *   * **停止是非同步的**。`stop()` 之後還會再來一次 `ondataavailable`，
 *     最後一塊通常是整首歌的最後一兩秒。在 `onstop` 之前就把 Blob 組起來，
 *     結尾會少一截 —— 而那一截往往是尾音。
 *   * **要有上限**。舞台開著沒人管（睡著、忘了關）時，錄音會一直長大，
 *     所以超過上限自己停，而不是等記憶體爆掉。
 */

// 單次錄音的長度上限。最長的歌也不會超過 15 分鐘；超過通常是「沒有人在管」，
// 那時候停下來比繼續錄好 —— 停下來只是少錄，爆掉是整頁當掉。
const MAX_TAKE_MS = 15 * 60 * 1000;

// 每隔多久收一塊資料。給 timeslice 的理由不是效能，而是「中途出事時
// 手上已經有大部分的錄音」：不給的話整首歌只在 stop 時吐一塊，
// 錄到一半分頁被系統回收就什麼都沒有。
const TAKE_TIMESLICE_MS = 2000;

class TakeRecorder {
  constructor(engine) {
    this.engine = engine;
    this.recorder = null;
    this.chunks = [];
    this.mime = "";
    this.startedAt = 0;
    this.pausedAt = 0;
    this.pausedMs = 0;
    this.durationMs = 0;
    this.autoStopTimer = null;
    this.lastError = "";
  }

  /** 這台瀏覽器錄不錄得出東西。錄不出來就整個功能安靜地不出現。 */
  get supported() {
    if (typeof MediaRecorder === "undefined") return false;
    return !!window.TakeRules.pickTakeMime(
      (m) => MediaRecorder.isTypeSupported(m), window.TakeRules.TAKE_MIME_CANDIDATES);
  }

  /** 正在錄（沒有暫停）。 */
  get active() {
    return !!this.recorder && this.recorder.state === "recording";
  }

  /** 這一次還沒結束（可能正在錄，也可能暫停中）。 */
  get running() {
    return !!this.recorder && this.recorder.state !== "inactive";
  }

  /**
   * 開始錄。這一次已經在進行中就只是接回去（暫停中會 resume）——
   * 暫停再繼續、seek 都不該把一次演唱切成兩個檔案。
   * 回傳有沒有真的在錄。
   */
  start() {
    if (this.running) {
      this.resume();
      return true;
    }
    if (!this.supported) return false;
    const stream = this.engine && this.engine.getRecordingStream
      ? this.engine.getRecordingStream() : null;
    if (!stream) return false;

    this.mime = window.TakeRules.pickTakeMime(
      (m) => MediaRecorder.isTypeSupported(m), window.TakeRules.TAKE_MIME_CANDIDATES);
    this.chunks = [];
    this.lastError = "";
    try {
      this.recorder = new MediaRecorder(stream, {
        mimeType: this.mime,
        audioBitsPerSecond: window.TakeRules.TAKE_BITS_PER_SECOND,
      });
    } catch (e) {
      // 容器支援但參數不合（某些 Safari 版本不吃 audioBitsPerSecond）：
      // 退一步用預設參數，錄得到比錄得漂亮重要
      try {
        this.recorder = new MediaRecorder(stream);
        this.mime = this.recorder.mimeType || this.mime;
      } catch (e2) {
        this.lastError = String(e2 && e2.message ? e2.message : e2);
        this.recorder = null;
        return false;
      }
    }

    this.recorder.ondataavailable = (ev) => {
      if (ev.data && ev.data.size > 0) this.chunks.push(ev.data);
    };
    this.recorder.onerror = (ev) => {
      this.lastError = String((ev && ev.error && ev.error.message) || "錄音中斷");
      console.warn("[TakeRecorder] 錄音發生錯誤：", this.lastError);
    };

    this.startedAt = performance.now();
    this.pausedAt = 0;
    this.pausedMs = 0;
    this.durationMs = 0;
    this.recorder.start(TAKE_TIMESLICE_MS);
    this.autoStopTimer = setTimeout(() => {
      console.warn("[TakeRecorder] 超過單次錄音上限，自動停止");
      this.stop().catch(() => {});
    }, MAX_TAKE_MS);
    return true;
  }

  /**
   * 暫停。歌暫停時包廂在講話 —— 那不是這次演唱的一部分，
   * 錄進去既沒有用（回放時多出一段對話）也不該錄（沒有人以為自己在被錄音）。
   */
  pause() {
    if (!this.active) return;
    try {
      this.recorder.pause();
      this.pausedAt = performance.now();
    } catch (e) { /* 不支援 pause 的瀏覽器就繼續錄，總比中斷好 */ }
  }

  resume() {
    if (!this.recorder || this.recorder.state !== "paused") return;
    try {
      this.recorder.resume();
      if (this.pausedAt) this.pausedMs += performance.now() - this.pausedAt;
      this.pausedAt = 0;
    } catch (e) { /* 同上 */ }
  }

  /**
   * 停止並把整段錄音組成一個 Blob。沒在錄就回傳 null。
   *
   * 一定要等 `onstop`：`stop()` 之後還有最後一塊資料在路上，
   * 早一步組起來會少掉歌的結尾。
   */
  stop() {
    if (!this.recorder) return Promise.resolve(null);
    const recorder = this.recorder;
    this.recorder = null;
    if (this.autoStopTimer) {
      clearTimeout(this.autoStopTimer);
      this.autoStopTimer = null;
    }
    // 暫停的那段時間不算進長度：錄音裡本來就沒有那一段，
    // 算進去的話清單上的長度會比實際播出來的長（而且暫停越久差越多）
    const pausedNow = this.pausedAt ? performance.now() - this.pausedAt : 0;
    this.durationMs = Math.max(
      0, Math.round(performance.now() - this.startedAt - this.pausedMs - pausedNow));

    if (recorder.state === "inactive") {
      return Promise.resolve(this._blob());
    }
    return new Promise((resolve) => {
      recorder.onstop = () => resolve(this._blob());
      try {
        recorder.stop();
      } catch (e) {
        // stop 在極少數情況會丟（串流已經死了）：把手上有的先交出去
        resolve(this._blob());
      }
    });
  }

  /** 丟掉這一次（切歌、重唱）。不留 Blob，也不上傳。 */
  cancel() {
    if (this.autoStopTimer) {
      clearTimeout(this.autoStopTimer);
      this.autoStopTimer = null;
    }
    const recorder = this.recorder;
    this.recorder = null;
    this.chunks = [];
    if (recorder && recorder.state !== "inactive") {
      try { recorder.stop(); } catch (e) { /* 已經停了 */ }
    }
  }

  _blob() {
    const chunks = this.chunks;
    this.chunks = [];
    if (!chunks.length) return null;
    const blob = new Blob(chunks, { type: this.mime || "audio/webm" });
    return { blob, mime: this.mime || blob.type, durationMs: this.durationMs };
  }
}

if (typeof window !== "undefined") {
  window.TakeRecorder = TakeRecorder;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { TakeRecorder, MAX_TAKE_MS, TAKE_TIMESLICE_MS };
}
