// Real-time Pitch Visualizer & Scoring Engine (JOYSOUND / DAM Style)
class PitchEngine {
  constructor(canvasElement, scoreValueEl, comboValueEl) {
    this.canvas = canvasElement;
    this.ctx = canvasElement.getContext('2d');
    this.scoreValueEl = scoreValueEl;
    this.comboValueEl = comboValueEl;

    this.pitchData = { notes: [], points: [] };
    this.score = 0;
    this.combo = 0;
    this.maxCombo = 0;

    this.analyser = null;
    this.audioBuffer = new Float32Array(2048);

    this.userPitchHistory = []; // [ { time, midi } ]
    this.lastHitTime = 0;
    this.lastUserMidi = 0;

    // 唱畢結算用的統計：有導唱音符的幀數（機會）、唱準的幀數（命中）、
    // 幾乎全準的幀數（Perfect）、有唱出聲音的幀數
    this.noteFrames = 0;
    this.hitFrames = 0;
    this.perfectFrames = 0;
    this.sangFrames = 0;

    this.resizeCanvas();
    window.addEventListener('resize', () => this.resizeCanvas());
  }

  resizeCanvas() {
    if (this.canvas) {
      this.canvas.width = this.canvas.offsetWidth * window.devicePixelRatio;
      this.canvas.height = this.canvas.offsetHeight * window.devicePixelRatio;
      this.ctx.scale(window.devicePixelRatio, window.devicePixelRatio);
    }
  }

  setPitchData(data) {
    this.pitchData = data || { notes: [], points: [] };
    this.resetScoring();
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

  // Real-time Autocorrelation Pitch Detector
  detectUserPitch(currentTime) {
    if (!this.analyser) return 0;

    this.analyser.getFloatTimeDomainData(this.audioBuffer);

    // Compute RMS amplitude to ensure user is actually singing, not background silence
    let rms = 0;
    for (let i = 0; i < this.audioBuffer.length; i++) {
      rms += this.audioBuffer[i] * this.audioBuffer[i];
    }
    rms = Math.sqrt(rms / this.audioBuffer.length);
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
   */
  tick(currentTime) {
    const userMidi = this.detectUserPitch(currentTime);
    this.lastUserMidi = userMidi;

    // 有導唱音符的時刻才算「機會」，前奏間奏不列入音準率分母
    const activeNote = this.pitchData.notes
      ? this.pitchData.notes.find(n => currentTime >= n.start && currentTime <= n.end)
      : null;
    if (activeNote) this.noteFrames++;

    if (userMidi > 0) {
      this.sangFrames++;
      this.userPitchHistory.push({ time: currentTime, midi: userMidi });
      if (this.userPitchHistory.length > 120) this.userPitchHistory.shift();
      this.evaluateSingingScore(currentTime, userMidi, activeNote);
    }
  }

  /**
   * 唱畢結算：把整首歌的統計濃縮成一張成績單。
   * 音準率 = 命中幀 / 有導唱音符的幀；等級門檻參考商用機的手感
   * （逐幀命中其實很嚴格，門檻不能照直覺的 90/80 分切）。
   */
  getFinalResult() {
    const accuracy = this.noteFrames > 0 ? this.hitFrames / this.noteFrames : 0;
    let grade;
    if (accuracy >= 0.75) grade = "SSS";
    else if (accuracy >= 0.6) grade = "SS";
    else if (accuracy >= 0.45) grade = "S";
    else if (accuracy >= 0.3) grade = "A";
    else if (accuracy >= 0.15) grade = "B";
    else grade = "C";
    return {
      score: this.score,
      accuracy: Math.round(accuracy * 1000) / 1000,
      max_combo: this.maxCombo,
      perfect_frames: this.perfectFrames,
      grade,
      // 唱不到一秒（約 60 幀偵測到聲音）視同沒唱，不出結算畫面
      sang: this.sangFrames >= 60 && this.noteFrames > 0
    };
  }

  updateAndRender(currentTime) {
    if (!this.canvas) return;
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
    const userMidi = this.lastUserMidi;

    // Draw User Pitch Trail
    if (this.userPitchHistory.length > 1) {
      this.ctx.beginPath();
      this.ctx.strokeStyle = "#ff007f";
      this.ctx.shadowColor = "#ff007f";
      this.ctx.shadowBlur = 12;
      this.ctx.lineWidth = 3;

      let started = false;
      for (const pt of this.userPitchHistory) {
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

    // Draw Current User Pitch Cursor
    if (userMidi > 0) {
      const cursorY = midiToY(userMidi);
      this.ctx.fillStyle = "#00f0ff";
      this.ctx.shadowColor = "#00f0ff";
      this.ctx.shadowBlur = 16;
      this.ctx.beginPath();
      this.ctx.arc(currentX, cursorY, 6, 0, Math.PI * 2);
      this.ctx.fill();
      this.ctx.shadowBlur = 0;
    }
  }

  evaluateSingingScore(currentTime, userMidi, activeNote) {
    if (activeNote) {
      const diff = Math.abs(userMidi - activeNote.midi);
      if (diff <= 1.5) {
        // Hit!
        this.score += 15;
        this.combo += 1;
        this.maxCombo = Math.max(this.maxCombo, this.combo);
        this.hitFrames++;
        if (diff < 0.6) this.perfectFrames++;
        this.updateScoreDisplay();

        if (currentTime - this.lastHitTime > 1.2 && this.combo > 5) {
          this.lastHitTime = currentTime;
          this.spawnToastFX(diff < 0.6 ? "PERFECT! 🔥" : "GREAT! ✨");
        }
      }
    } else {
      // Missed active melody
      if (this.combo > 0 && Math.random() < 0.05) {
        this.combo = 0;
        this.updateScoreDisplay();
      }
    }
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
    toast.textContent = text;
    toast.style.left = `${30 + Math.random() * 40}%`;
    toast.style.top = `${25 + Math.random() * 20}%`;

    stage.appendChild(toast);
    setTimeout(() => toast.remove(), 1500);
  }
}

window.PitchEngine = PitchEngine;
