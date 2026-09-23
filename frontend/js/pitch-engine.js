// Real-time Pitch Visualizer & Scoring Engine (JOYSOUND / DAM Style)
//
// 依賴 section-scorer.js（段落評分與等級門檻），player.html 的 <script> 順序要在它之後。
class PitchEngine {
  /**
   * @param {HTMLCanvasElement|null} canvasElement
   *   音準導唱線畫在哪裡。對唱模式的第二位演唱者用 null ——
   *   他吃同一套評分邏輯但不自己畫圖（軌跡由主引擎一起畫在同一張畫布上）。
   * @param {object} options
   *   label 演唱者標籤（對唱模式的「PERFECT!」浮字要標明是誰的，否則兩人搶著跳字沒人看得懂）
   */
  constructor(canvasElement, scoreValueEl, comboValueEl, options = {}) {
    this.canvas = canvasElement || null;
    this.ctx = this.canvas ? this.canvas.getContext('2d') : null;
    this.scoreValueEl = scoreValueEl;
    this.comboValueEl = comboValueEl;
    this.label = options.label ? String(options.label) : "";

    this.pitchData = { notes: [], points: [] };
    this.score = 0;
    this.combo = 0;
    this.maxCombo = 0;

    this.analyser = null;
    this.audioBuffer = new Float32Array(2048);
    // 最近一幀麥克風原始訊號的 RMS（0~1）。麥克風自動增益與音量表都吃這個值，
    // 不各自再抓一次波形 —— getFloatTimeDomainData 每幀多抓一次是白花的成本。
    this.lastRms = 0;

    this.userPitchHistory = []; // [ { time, midi } ]
    this.lastHitTime = 0;
    this.lastUserMidi = 0;

    // 唱畢結算用的統計：有導唱音符的幀數（機會）、唱準的幀數（命中）、
    // 幾乎全準的幀數（Perfect）、有唱出聲音的幀數
    this.noteFrames = 0;
    this.hitFrames = 0;
    this.perfectFrames = 0;
    this.sangFrames = 0;

    // 段落評分：同一份逐幀判定另外依曲式分段累計，結算才能指出哪一段要練
    this.sectionScorer = new SectionScorer();

    this.resizeCanvas();
    window.addEventListener('resize', () => this.resizeCanvas());
  }

  resizeCanvas() {
    if (this.canvas && this.ctx) {
      this.canvas.width = this.canvas.offsetWidth * window.devicePixelRatio;
      this.canvas.height = this.canvas.offsetHeight * window.devicePixelRatio;
      this.ctx.scale(window.devicePixelRatio, window.devicePixelRatio);
    }
  }

  setPitchData(data) {
    this.pitchData = data || { notes: [], points: [] };
    this.resetScoring();
  }

  /**
   * 載入這首歌的曲式段落（`/api/songs/{id}/sections` 的 sections）。
   *
   * 段落是額外的分析資料，抓不到（還在處理、這首沒歌詞）就傳空的，
   * 段落評分安靜地停用，總分與音準率照常運作。
   */
  setSections(sections) {
    this.sectionScorer.setSections(sections);
  }

  /** 歸零整首歌的評分統計（換歌與重唱都要呼叫，成績單才不會累計到上一輪）。 */
  resetScoring() {
    this.score = 0;
    this.combo = 0;
    this.maxCombo = 0;
    this.userPitchHistory = [];
    this.lastUserMidi = 0;
    this.noteFrames = 0;
    this.hitFrames = 0;
    this.perfectFrames = 0;
    this.sangFrames = 0;
    this.sectionScorer.reset();
    this.updateScoreDisplay();
  }

  /**
   * 只清掉使用者音高軌跡，分數與統計保留。
   *
   * 跳轉（進度條、段落跳轉、A-B 循環跳回 A 點）之後舊軌跡的時間都落在新位置的未來，
   * 留著會在導唱線上畫出一條不存在的鬼影；但那些幀是真的唱過的，分數不能沒收。
   */
  clearTrail() {
    this.userPitchHistory = [];
    this.lastUserMidi = 0;
  }

  setAnalyser(analyser) {
    this.analyser = analyser;
  }

  /** 對唱模式的演唱者暱稱（點歌台可以隨時改，浮字要跟著改）。 */
  setLabel(label) {
    this.label = label ? String(label) : "";
  }

  // Real-time Autocorrelation Pitch Detector
  /**
   * 只量麥克風原始訊號的 RMS，不做音高偵測。
   *
   * 自動增益與音量表在「還沒開始唱」的時候也要能動（設定面板要讓人先試音），
   * 但那時候跑完整的自相關音高偵測是白花的 —— 它是整條迴圈裡最貴的一步。
   */
  measureRms() {
    if (!this.analyser) {
      this.lastRms = 0;
      return 0;
    }
    this.analyser.getFloatTimeDomainData(this.audioBuffer);
    let sum = 0;
    for (let i = 0; i < this.audioBuffer.length; i++) {
      sum += this.audioBuffer[i] * this.audioBuffer[i];
    }
    // 記在門檻判斷之前：自動增益要看的是「原始電平有多小」，
    // 只有它才知道 0.010 與 0.001 是「唱得太小聲」還是「根本沒人」。
    this.lastRms = Math.sqrt(sum / this.audioBuffer.length);
    return this.lastRms;
  }

  /**
   * @param {boolean} reuseRms
   *   true = 不重抓波形，直接用 measureRms() 剛才填好的緩衝區。
   *   對唱模式要先量兩支麥克風的電平才能決定這一幀算誰的，
   *   量完再叫 tick() 的話，同一幀就會抓兩次波形 —— 自相關是整條迴圈裡最貴的一步，
   *   多抓一次純粹是浪費（而且兩次抓到的樣本還不一樣，除錯時會很困惑）。
   */
  detectUserPitch(currentTime, reuseRms = false) {
    if (!this.analyser) return 0;

    const rms = reuseRms ? this.lastRms : this.measureRms();
    if (rms < 0.015) return 0; // Below noise floor

    // Autocorrelation algorithm
    const sampleRate = this.analyser.context.sampleRate;
    const SIZE = this.audioBuffer.length;
    let r1 = 0, r2 = SIZE - 1;
    const thres = 0.2;

    for (let i = 0; i < SIZE / 2; i++) {
      if (Math.abs(this.audioBuffer[i]) < thres) { r1 = i; break; }
    }
    for (let i = 1; i < SIZE / 2; i++) {
      if (Math.abs(this.audioBuffer[SIZE - i]) < thres) { r2 = SIZE - i; break; }
    }

    const buf = this.audioBuffer.slice(r1, r2);
    const bufSize = buf.length;
    const c = new Array(bufSize).fill(0);

    for (let i = 0; i < bufSize; i++) {
      for (let j = 0; j < bufSize - i; j++) {
        c[i] = c[i] + buf[j] * buf[j + i];
      }
    }

    let d = 0;
    while (c[d] > c[d + 1]) d++;
    let maxval = -1, maxpos = -1;
    for (let i = d; i < bufSize; i++) {
      if (c[i] > maxval) {
        maxval = c[i];
        maxpos = i;
      }
    }

    let T0 = maxpos;
    if (maxval > 0.01 && T0 > 0) {
      const f0 = sampleRate / T0;
      if (f0 >= 65 && f0 <= 1200) { // Human singing range C2 ~ D6
        const midi = 69 + 12 * Math.log2(f0 / 440);
        return midi;
      }
    }
    return 0;
  }

  /**
   * 每一幀都要呼叫的評分心跳：偵測歌聲音高、對照導唱音符計分。
   * 與畫面渲染分離 —— 音準線隱藏時評分照常進行，唱畢結算才公平。
   *
   * 回傳這一幀的判定 `{ hasNote, sang, hit, perfect }`：
   * 段落評分與導唱自動 ducking 都吃這份判定，不各自再偵測一次音高
   * （偵測是整條迴圈裡最貴的一步，而且兩邊算出不同結果的話會非常難查）。
   *
   * @param {object} options
   *   credit   false = 這一幀不計分（對唱模式判定為串音時：另一位演唱者的聲音
   *            從這支麥克風漏進來，記進來就等於幫他加分）。音高照樣偵測 ——
   *            串音判定本身就需要知道這支麥克風收到的音高是什麼。
   *   reuseRms true = 電平已經由外面量過了，不要重抓一次波形。
   *   sectionTime 段落統計要用的時間（預設同 currentTime）。
   *
   * **為什麼有兩個時間**：導唱音符（pitch.json）是從人聲軌抽的，活在**音訊**
   * 時間軸上；段落邊界是從歌詞（lyrics.json）算出來的，活在**歌詞**時間軸上。
   * 這兩條在「這首歌的字幕偏移」不為零時會差那個偏移量，拿同一個時間去查
   * 就會有一邊是錯的：音符用錯時間＝評分整個歪掉（而且畫面上看不出來），
   * 段落用錯時間＝「哪一段唱得最好」指到隔壁那一段。
   */
  tick(currentTime, options = {}) {
    const sectionTime = options.sectionTime === undefined
      ? currentTime : options.sectionTime;
    const credit = options.credit === undefined ? true : !!options.credit;
    const userMidi = this.detectUserPitch(currentTime, !!options.reuseRms);
    this.lastUserMidi = credit ? userMidi : 0;

    // 有導唱音符的時刻才算「機會」，前奏間奏不列入音準率分母
    const activeNote = this.pitchData.notes
      ? this.pitchData.notes.find(n => currentTime >= n.start && currentTime <= n.end)
      : null;
    if (activeNote && credit) this.noteFrames++;

    let outcome = { hit: false, perfect: false };
    if (userMidi > 0 && credit) {
      this.sangFrames++;
      this.userPitchHistory.push({ time: currentTime, midi: userMidi });
      if (this.userPitchHistory.length > 120) this.userPitchHistory.shift();
      outcome = this.evaluateSingingScore(currentTime, userMidi, activeNote);
    }

    const frame = {
      hasNote: !!activeNote,
      sang: userMidi > 0 && credit,
      // 這一幀有沒有算進成績（false = 判定為串音）。徽章與統計要分得清
      // 「沒人唱」與「有唱但不算他的」—— 兩者在現場是完全不同的問題。
      credited: credit,
      hit: outcome.hit,
      perfect: outcome.perfect,
      // 麥克風原始電平：自動增益用它決定要加多少（前饋，量的是增益節點之前的訊號）
      rms: this.lastRms,
      // 現在這個導唱音符是什麼音、從哪裡開始（和聲用它算音階上的度數；
      // noteStart 同時是「換音符了沒」的識別碼 —— 同一個音符不重算移調量）
      noteMidi: activeNote ? activeNote.midi : 0,
      noteStart: activeNote ? activeNote.start : null,
      // 這支麥克風這一幀收到的音高（0 = 沒偵測到）。
      // 不管有沒有計分都給實際偵測值 —— 對唱的串音判定要靠「兩支麥克風的音高
      // 一不一樣」，把不計分的那邊歸零就等於拿掉了判斷的依據。
      userMidi,
    };

    // 同一幀的判定再依曲式分段累計一次，唱畢才知道哪一段唱得好、哪一段要練
    // （判定為串音的幀不進段落統計，否則段落命中率的分母會混進別人唱的時間）
    if (credit) this.sectionScorer.count(sectionTime, frame);

    return frame;
  }

  /**
   * 唱畢結算：把整首歌的統計濃縮成一張成績單。
   * 音準率 = 命中幀 / 有導唱音符的幀；等級門檻與段落評分共用（section-scorer.js），
   * 整首總評與單段評語的手感才會一致。
   */
  getFinalResult() {
    const accuracy = this.noteFrames > 0 ? this.hitFrames / this.noteFrames : 0;
    const bySection = this.sectionScorer.summary();
    return {
      score: this.score,
      accuracy: Math.round(accuracy * 1000) / 1000,
      max_combo: this.maxCombo,
      perfect_frames: this.perfectFrames,
      grade: gradeForAccuracy(accuracy),
      // 唱不到一秒（約 60 幀偵測到聲音）視同沒唱，不出結算畫面
      sang: this.sangFrames >= 60 && this.noteFrames > 0,
      // 段落評分：整首的每段命中率，以及值得點名的最佳／待加強段落（可能是 null）
      sections: bySection.sections,
      best_section: bySection.best,
      worst_section: bySection.worst
    };
  }

  /**
   * @param {Array<{engine: PitchEngine, color: string}>} extraTrails
   *   另外要畫在同一張畫布上的音高軌跡。對唱模式用它把第二位演唱者的軌跡
   *   疊在同一條導唱線上（兩個人各自一種顏色）—— 分成兩張畫布的話，
   *   同一個音符在兩張圖上的位置對不起來，反而看不出誰唱得比較準。
   */
  updateAndRender(currentTime, extraTrails = []) {
    if (!this.canvas || !this.ctx) return;
    const width = this.canvas.offsetWidth;
    const height = this.canvas.offsetHeight;
    this.ctx.clearRect(0, 0, width, height);

    // Grid lines
    this.ctx.strokeStyle = "rgba(255, 255, 255, 0.06)";
    this.ctx.lineWidth = 1;
    for (let y = 0; y < height; y += 18) {
      this.ctx.beginPath();
      this.ctx.moveTo(0, y);
      this.ctx.lineTo(width, y);
      this.ctx.stroke();
    }

    // Time window to display: from (currentTime - 1.5s) to (currentTime + 4.5s)
    const windowStart = currentTime - 1.5;
    const windowDuration = 6.0;
    const currentX = (1.5 / windowDuration) * width; // Playhead vertical line position

    // Playhead line
    this.ctx.strokeStyle = "rgba(0, 240, 255, 0.5)";
    this.ctx.lineWidth = 2;
    this.ctx.setLineDash([4, 4]);
    this.ctx.beginPath();
    this.ctx.moveTo(currentX, 0);
    this.ctx.lineTo(currentX, height);
    this.ctx.stroke();
    this.ctx.setLineDash([]);

    // Note pitch range: Midi 45 (A2) to Midi 80 (Ab5)
    const minMidi = 45;
    const maxMidi = 80;
    const midiToY = (midi) => {
      const clamped = Math.max(minMidi, Math.min(maxMidi, midi));
      return height - ((clamped - minMidi) / (maxMidi - minMidi)) * (height - 20) - 10;
    };

    // Draw Reference Note Blocks (Golden Melody Bars)
    if (this.pitchData.notes) {
      this.pitchData.notes.forEach(note => {
        if (note.end >= windowStart && note.start <= windowStart + windowDuration) {
          const x1 = ((note.start - windowStart) / windowDuration) * width;
          const x2 = ((note.end - windowStart) / windowDuration) * width;
          const y = midiToY(note.midi);
          const w = Math.max(x2 - x1, 4);
          const h = 8;

          // Note bar glow & rounded rect
          this.ctx.fillStyle = "rgba(255, 222, 89, 0.75)";
          this.ctx.shadowColor = "#ffde59";
          this.ctx.shadowBlur = 8;
          this.ctx.beginPath();
          this.ctx.roundRect(x1, y - h / 2, w, h, 4);
          this.ctx.fill();
          this.ctx.shadowBlur = 0;
        }
      });
    }

    // 音高偵測與計分在 tick() 完成，這裡只負責畫出最新結果
    // 對唱模式的第二位先畫（顏色較暗），主唱的軌跡疊在上面
    for (const extra of extraTrails) {
      if (!extra || !extra.engine) continue;
      this._drawTrail(extra.engine.userPitchHistory, extra.color || "#7cf6a0",
                      windowStart, windowDuration, width, midiToY);
      this._drawCursor(extra.engine.lastUserMidi, extra.color || "#7cf6a0", currentX, midiToY);
    }

    this._drawTrail(this.userPitchHistory, "#ff007f",
                    windowStart, windowDuration, width, midiToY);
    this._drawCursor(this.lastUserMidi, "#00f0ff", currentX, midiToY);
  }

  /** 一條音高軌跡。抽出來是因為對唱模式要在同一張畫布上畫兩條（只差顏色）。 */
  _drawTrail(history, color, windowStart, windowDuration, width, midiToY) {
    if (!history || history.length < 2) return;
    this.ctx.beginPath();
    this.ctx.strokeStyle = color;
    this.ctx.shadowColor = color;
    this.ctx.shadowBlur = 12;
    this.ctx.lineWidth = 3;

    let started = false;
    for (const pt of history) {
      if (pt.time >= windowStart && pt.time <= windowStart + windowDuration) {
        const x = ((pt.time - windowStart) / windowDuration) * width;
        const y = midiToY(pt.midi);
        if (!started) {
          this.ctx.moveTo(x, y);
          started = true;
        } else {
          this.ctx.lineTo(x, y);
        }
      }
    }
    this.ctx.stroke();
    this.ctx.shadowBlur = 0;
  }

  /** 現在唱到的音高游標（播放頭上那顆點）。 */
  _drawCursor(midi, color, currentX, midiToY) {
    if (!(midi > 0)) return;
    this.ctx.fillStyle = color;
    this.ctx.shadowColor = color;
    this.ctx.shadowBlur = 16;
    this.ctx.beginPath();
    this.ctx.arc(currentX, midiToY(midi), 6, 0, Math.PI * 2);
    this.ctx.fill();
    this.ctx.shadowBlur = 0;
  }

  /** 回傳這一幀的判定 `{ hit, perfect }`，讓段落評分沿用同一個結果。 */
  evaluateSingingScore(currentTime, userMidi, activeNote) {
    if (activeNote) {
      const diff = Math.abs(userMidi - activeNote.midi);
      if (diff <= 1.5) {
        // Hit!
        const perfect = diff < 0.6;
        this.score += 15;
        this.combo += 1;
        this.maxCombo = Math.max(this.maxCombo, this.combo);
        this.hitFrames++;
        if (perfect) this.perfectFrames++;
        this.updateScoreDisplay();

        if (currentTime - this.lastHitTime > 1.2 && this.combo > 5) {
          this.lastHitTime = currentTime;
          this.spawnToastFX(perfect ? "PERFECT! 🔥" : "GREAT! ✨");
        }
        return { hit: true, perfect };
      }
    } else {
      // Missed active melody
      if (this.combo > 0 && Math.random() < 0.05) {
        this.combo = 0;
        this.updateScoreDisplay();
      }
    }
    return { hit: false, perfect: false };
  }

  updateScoreDisplay() {
    if (this.scoreValueEl) {
      this.scoreValueEl.textContent = this.score.toString().padStart(6, '0');
    }
    if (this.comboValueEl) {
      this.comboValueEl.textContent = this.combo > 2 ? `${this.combo} COMBO!` : "";
    }
  }

  spawnToastFX(text) {
    const stage = document.querySelector('.stage-wrapper');
    if (!stage) return;

    const toast = document.createElement('div');
    toast.className = 'floating-fx';
    // 對唱模式兩個人的浮字會同時跳出來，不標名字的話沒人知道那句 PERFECT 是誰的
    toast.textContent = this.label ? `${this.label} ${text}` : text;
    toast.style.left = `${30 + Math.random() * 40}%`;
    toast.style.top = `${25 + Math.random() * 20}%`;

    stage.appendChild(toast);
    setTimeout(() => toast.remove(), 1500);
  }
}

window.PitchEngine = PitchEngine;
