/**
 * 情境背景 — 渲染端 (Ambient Stage Renderer)
 *
 * 決策全在 ambient-visuals.js（那一支是純資料邏輯、有測試守著）。
 * 這裡只做三件跟瀏覽器綁在一起、測不了也不值得測的事：
 *
 *   1. 把 AudioContext 的頻譜量出來餵給 AmbientDirector。
 *   2. 每隔一秒多把 <video> 縮成 32x16 取一次樣，算出「跟上一張差多少」，
 *      讓 StillnessProbe 判斷抓到的 MV 是不是其實只是一張圖。
 *   3. 照主題把畫面畫出來。
 *
 * ## 成本控制
 *
 * 舞台端同一條 requestAnimationFrame 上還有字幕逐字走字、音高偵測與評分，
 * 那些掉幀使用者會立刻發現，背景掉幀不會。所以背景是**最低優先**：
 *   * 最多 30fps（背景是慢動作的東西，60fps 只是把電費變兩倍）。
 *   * 畫布解析度只有實際顯示的一半上下，柔邊由 CSS 的 blur 補 ——
 *     GPU 合成的 blur 比在 canvas 裡疊十層漸層便宜太多。
 *   * 分頁看不見（document.hidden）就完全不畫。
 *   * 每幀量自己花多少毫秒，超過預算就自動降級（QualityGovernor）。
 */

// 背景最多每 33ms 畫一次
const AMBIENT_FRAME_MS = 33;
// 靜態 MV 取樣間隔。太密沒有意義（靜止圖再怎麼取樣還是一樣），
// 太稀則要很久才判定得出來 —— 1.2 秒配 4 筆取樣約 5 秒定案，
// 正好落在片頭卡還亮著的時間內，切換不會被看到。
const PROBE_INTERVAL_MS = 1200;
const PROBE_W = 32;
const PROBE_H = 18;

// 畫質等級 → 畫布縮放與粒子數
const QUALITY_PROFILE = {
  high: { scale: 0.55, particles: 90 },
  medium: { scale: 0.45, particles: 55 },
  low: { scale: 0.35, particles: 28 },
};
const MAX_CANVAS_W = 960;

function rgba(color, alpha) {
  return `rgba(${color[0]}, ${color[1]}, ${color[2]}, ${alpha})`;
}

class AmbientStage {
  /**
   * @param {HTMLCanvasElement} canvas 情境背景畫布
   * @param {object} options { artEl: 模糊縮圖底圖的 DOM, videoEl: 背景 MV }
   */
  constructor(canvas, options = {}) {
    this.canvas = canvas;
    this.ctx = canvas ? canvas.getContext("2d") : null;
    this.artEl = options.artEl || null;
    this.videoEl = options.videoEl || null;
    this.director = new AmbientDirector(options.director || {});

    this.analyser = null;
    this.freqBytes = null;
    this.sampleRate = 48000;

    this.lastFrameMs = 0;
    this.lastProbeMs = 0;
    this.lastProbeData = null;
    this.probeCanvas = null;
    this.probeCtx = null;

    this.particles = [];
    this.particleKey = "";
    this.cssW = 0;
    this.cssH = 0;
    this.layer = null;

    this.applyLayer(true);
  }

  configure(options) {
    this.director.configure(options);
    // 亮度／主題是設定頁改的，改完當下就要看得到（不能等下一首）
    this.particleKey = "";
    this.applyLayer();
    return this;
  }

  /** 音訊分析節點。接的是伴奏＋導唱那條匯流排，不含麥克風 —— 見 audio-effects.js。 */
  setAnalyser(analyser, sampleRate) {
    this.analyser = analyser || null;
    if (analyser) {
      this.freqBytes = new Uint8Array(analyser.frequencyBinCount);
      if (sampleRate) this.sampleRate = sampleRate;
    }
    return this;
  }

  startSong(song) {
    this.director.startSong(song);
    this.lastProbeData = null;
    this.lastProbeMs = 0;
    this.setArt(song && song.thumbnail);
    this.applyLayer();
    return this;
  }

  clearSong() {
    this.director.clearSong();
    this.lastProbeData = null;
    this.setArt("");
    this.applyLayer();
    return this;
  }

  videoFailed() {
    this.director.videoFailed();
    this.applyLayer();
    return this;
  }

  /**
   * 模糊縮圖底圖。
   *
   * 沒有 MV 的歌還是有封面圖（YouTube 的縮圖），把它放大模糊當底，
   * 情境背景就跟這首歌有關係，而不是一段通用的螢幕保護程式。
   * 抓不到（離線、圖被刪）就靜靜地不放 —— 破圖比沒圖難看得多。
   */
  setArt(url) {
    if (!this.artEl) return;
    if (!url) {
      this.artEl.style.backgroundImage = "";
      this.artEl.classList.remove("on");
      return;
    }
    const img = new Image();
    img.onload = () => {
      this.artEl.style.backgroundImage = `url("${url}")`;
      if (this.layer === "ambient") this.artEl.classList.add("on");
    };
    img.onerror = () => {
      this.artEl.style.backgroundImage = "";
      this.artEl.classList.remove("on");
    };
    img.src = url;
  }

  /** 把「現在該給誰畫面」寫進 DOM。淡入淡出交給 CSS transition。 */
  applyLayer(force) {
    const layer = this.director.layer;
    if (!force && layer === this.layer) return layer;
    this.layer = layer;
    if (this.canvas) this.canvas.classList.toggle("on", layer === "ambient");
    if (this.artEl) {
      this.artEl.classList.toggle("on",
        layer === "ambient" && !!this.artEl.style.backgroundImage);
    }
    if (this.videoEl) this.videoEl.classList.toggle("ambient-hidden", layer !== "video");
    return layer;
  }

  /**
   * 取樣一格 MV 畫面，回傳與上一張的平均亮度差（0~255）。
   * 沒東西可取樣（暫停中、還沒解碼、跨來源）就回 null。
   *
   * 「暫停中不取樣」這一條不是小事：暫停的畫面永遠跟上一張一模一樣，
   * 差值恆為 0，四筆取樣就會宣布「這是一張圖」—— 使用者只是按了暫停，
   * 回來就發現 MV 不見了。
   */
  sampleVideoDiff() {
    const v = this.videoEl;
    if (!v || v.paused || v.readyState < 2 || !(v.videoWidth > 0)) return null;
    if (!this.probeCanvas) {
      this.probeCanvas = document.createElement("canvas");
      this.probeCanvas.width = PROBE_W;
      this.probeCanvas.height = PROBE_H;
      this.probeCtx = this.probeCanvas.getContext("2d", { willReadFrequently: true });
    }
    let data;
    try {
      this.probeCtx.drawImage(v, 0, 0, PROBE_W, PROBE_H);
      data = this.probeCtx.getImageData(0, 0, PROBE_W, PROBE_H).data;
    } catch (e) {
      // 跨來源污染（理論上不會：影片跟頁面同源）或解碼器不給畫面
      this.director.probe.giveUp();
      return null;
    }
    const luma = new Float32Array(PROBE_W * PROBE_H);
    for (let i = 0, p = 0; i < luma.length; i++, p += 4) {
      luma[i] = 0.299 * data[p] + 0.587 * data[p + 1] + 0.114 * data[p + 2];
    }
    const prev = this.lastProbeData;
    this.lastProbeData = luma;
    if (!prev) return null;
    let sum = 0;
    for (let i = 0; i < luma.length; i++) sum += Math.abs(luma[i] - prev[i]);
    return sum / luma.length;
  }

  /** 量這一幀的音樂能量。沒有分析節點（還沒解鎖音訊）就當安靜。 */
  readSpectrum() {
    if (!this.analyser || !this.freqBytes) return { bass: 0, mid: 0, treble: 0, energy: 0 };
    this.analyser.getByteFrequencyData(this.freqBytes);
    return analyseAmbientSpectrum(this.freqBytes, this.sampleRate);
  }

  /**
   * 每一幀由 player.js 呼叫（含歌沒在播的待機狀態）。
   * @param {number} nowMs performance.now()
   * @param {boolean} playing 現在有沒有歌在播
   */
  frame(nowMs, playing) {
    if (!this.ctx) return;

    // 靜態 MV 取樣：跟畫圖的節流分開算，判定完就不再花這個成本
    if (this.director.needsProbe && nowMs - this.lastProbeMs >= PROBE_INTERVAL_MS) {
      this.lastProbeMs = nowMs;
      const diff = this.sampleVideoDiff();
      if (diff !== null) {
        // 觀察窗用牆上時鐘算，不用影片的 currentTime —— 短片配長歌會一直繞回 0
        this.director.probeFrame(diff, nowMs / 1000);
        this.applyLayer();
      }
    }

    if (this.layer !== "ambient") return;
    if (typeof document !== "undefined" && document.hidden) return;
    if (nowMs - this.lastFrameMs < AMBIENT_FRAME_MS) return;
    const dt = this.lastFrameMs ? (nowMs - this.lastFrameMs) / 1000 : 1 / 30;
    this.lastFrameMs = nowMs;

    const spectrum = playing ? this.readSpectrum() : { bass: 0, energy: 0 };
    const state = this.director.update(dt, {
      energy: spectrum.energy,
      bass: spectrum.bass,
      playing,
    });

    const t0 = performance.now();
    this.draw(state);
    this.director.reportFrameCost(performance.now() - t0);
  }

  resizeIfNeeded(quality) {
    const profile = QUALITY_PROFILE[quality] || QUALITY_PROFILE.medium;
    const cssW = this.canvas.clientWidth || 1280;
    const cssH = this.canvas.clientHeight || 720;
    const w = Math.max(320, Math.min(MAX_CANVAS_W, Math.round(cssW * profile.scale)));
    const h = Math.max(180, Math.round(w * (cssH / Math.max(1, cssW))));
    if (this.canvas.width !== w || this.canvas.height !== h) {
      this.canvas.width = w;
      this.canvas.height = h;
      this.particleKey = "";
    }
    this.cssW = w;
    this.cssH = h;
    return profile;
  }

  /** 粒子只在主題／尺寸／畫質變動時重建，每幀重建會在 GC 上看得出來。 */
  ensureParticles(theme, profile) {
    const n = Math.round(profile.particles * (theme.density || 1));
    const key = `${theme.id}:${this.cssW}x${this.cssH}:${n}`;
    if (key === this.particleKey) return;
    this.particleKey = key;
    this.particles = [];
    for (let i = 0; i < n; i++) {
      this.particles.push({
        x: Math.random(),
        y: Math.random(),
        r: 0.35 + Math.random() * 1.1,
        speed: 0.2 + Math.random() * 0.9,
        seed: Math.random() * Math.PI * 2,
      });
    }
  }

  draw(state) {
    const { theme, luma, energy, beatPulse, phase, quality } = state;
    const profile = this.resizeIfNeeded(quality);
    this.ensureParticles(theme, profile);
    // 柔邊的力道依動態種類而定（CSS 裡分開設）：漸層類糊一點比較好看，
    // 星點糊掉就只剩一片灰霧，那不是星空。
    if (this.canvas.dataset.motion !== theme.motion) this.canvas.dataset.motion = theme.motion;
    const ctx = this.ctx;
    const w = this.cssW;
    const h = this.cssH;

    // 底色：上暗下更暗。字幕在下半部，所以底部一律壓得比上面重。
    const base = ctx.createLinearGradient(0, 0, 0, h);
    base.addColorStop(0, rgba(theme.base, 1));
    base.addColorStop(1, `rgba(0, 0, 0, 1)`);
    ctx.globalCompositeOperation = "source-over";
    ctx.fillStyle = base;
    ctx.fillRect(0, 0, w, h);

    ctx.globalCompositeOperation = "lighter";
    switch (theme.motion) {
      case "stars": this.drawStars(theme, phase, energy, beatPulse); break;
      case "grid": this.drawGrid(theme, phase, energy, beatPulse); break;
      case "waves": this.drawWaves(theme, phase, energy, beatPulse); break;
      case "embers": this.drawEmbers(theme, phase, energy, beatPulse); break;
      default: this.drawCurtain(theme, phase, energy, beatPulse); break;
    }

    // 亮度限速器的結果在這裡生效：一層黑紗蓋掉 (1 - luma)。
    // 放在最後而不是調每一層的 alpha，是為了讓「整體亮度」真的只有一個出口 ——
    // 分散在各層的話，某個主題偷偷亮起來就沒人擋得住。
    ctx.globalCompositeOperation = "source-over";
    ctx.fillStyle = `rgba(0, 0, 0, ${(1 - luma).toFixed(3)})`;
    ctx.fillRect(0, 0, w, h);
  }

  // --- 各主題的畫法 ---

  /** 極光：三道緩慢擺動的光幕。 */
  drawCurtain(theme, phase, energy, beatPulse) {
    const ctx = this.ctx;
    const w = this.cssW;
    const h = this.cssH;
    const bands = theme.colors.length;
    for (let i = 0; i < bands; i++) {
      const color = theme.colors[i];
      const speed = 0.6 + i * 0.25;
      const cx = w * (0.5 + 0.30 * Math.sin(phase * Math.PI * 2 * speed + i * 1.7));
      const bw = w * (0.22 + 0.10 * Math.sin(phase * Math.PI * 2 * (speed + 0.4) + i));
      const grad = ctx.createLinearGradient(cx - bw, 0, cx + bw, 0);
      grad.addColorStop(0, rgba(color, 0));
      grad.addColorStop(0.5, rgba(color, 0.58 + energy * 0.22 + beatPulse * 0.06));
      grad.addColorStop(1, rgba(color, 0));
      ctx.fillStyle = grad;
      // 上方比下方亮：極光掛在天上，而且畫面下半要留給字幕
      ctx.globalAlpha = 0.85;
      ctx.beginPath();
      ctx.moveTo(cx - bw, 0);
      ctx.lineTo(cx + bw, 0);
      ctx.lineTo(cx + bw * 1.6, h * 0.78);
      ctx.lineTo(cx - bw * 1.6, h * 0.78);
      ctx.closePath();
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  }

  /** 星空：緩慢飄移的星點 + 兩團星雲。 */
  drawStars(theme, phase, energy, beatPulse) {
    const ctx = this.ctx;
    const w = this.cssW;
    const h = this.cssH;
    for (let i = 0; i < 2; i++) {
      const color = theme.colors[i === 0 ? 2 : 0];
      const cx = w * (0.3 + 0.4 * i);
      const cy = h * (0.35 + 0.15 * Math.sin(phase * Math.PI * 2 + i));
      const r = w * (0.28 + 0.05 * energy);
      const neb = ctx.createRadialGradient(cx, cy, 0, cx, cy, r);
      neb.addColorStop(0, rgba(color, 0.42));
      neb.addColorStop(1, rgba(color, 0));
      ctx.fillStyle = neb;
      ctx.fillRect(0, 0, w, h);
    }
    const star = theme.colors[1];
    for (const p of this.particles) {
      const y = (p.y + phase * p.speed * 0.25) % 1;
      const twinkle = 0.45 + 0.35 * Math.sin(phase * Math.PI * 2 * 3 + p.seed);
      ctx.fillStyle = rgba(star, twinkle * (0.5 + energy * 0.5));
      const r = p.r * (1 + beatPulse * 0.5);
      ctx.beginPath();
      ctx.arc(p.x * w, y * h, r, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  /** 霓虹：往地平線收束的透視格線。 */
  drawGrid(theme, phase, energy, beatPulse) {
    const ctx = this.ctx;
    const w = this.cssW;
    const h = this.cssH;
    const horizon = h * 0.58;
    const [pink, cyan, purple] = theme.colors;

    // 地平線上的光暈
    const glow = ctx.createRadialGradient(w / 2, horizon, 0, w / 2, horizon, w * 0.45);
    glow.addColorStop(0, rgba(purple, 0.48 + energy * 0.18));
    glow.addColorStop(1, rgba(purple, 0));
    ctx.fillStyle = glow;
    ctx.fillRect(0, 0, w, h);

    ctx.lineWidth = 1 + beatPulse * 1.2;
    // 橫線：間距隨距離拉開，整體往前捲動
    const rows = 14;
    for (let i = 0; i < rows; i++) {
      const k = ((i + phase * 4) % rows) / rows;
      const y = horizon + (h - horizon) * k * k;
      ctx.strokeStyle = rgba(cyan, 0.42 * (1 - k) + 0.08);
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(w, y);
      ctx.stroke();
    }
    // 直線：全部收到消失點
    const cols = 13;
    for (let i = 0; i <= cols; i++) {
      const x = (i / cols - 0.5) * w * 3 + w / 2;
      ctx.strokeStyle = rgba(pink, 0.24 + energy * 0.12);
      ctx.beginPath();
      ctx.moveTo(w / 2, horizon);
      ctx.lineTo(x, h);
      ctx.stroke();
    }
  }

  /** 海洋：幾層互相錯開的波形。 */
  drawWaves(theme, phase, energy, beatPulse) {
    const ctx = this.ctx;
    const w = this.cssW;
    const h = this.cssH;
    const layers = 4;
    for (let i = 0; i < layers; i++) {
      const color = theme.colors[i % theme.colors.length];
      const amp = h * (0.03 + 0.025 * i) * (1 + energy * 0.8 + beatPulse * 0.25);
      const yBase = h * (0.42 + i * 0.13);
      const speed = 0.8 + i * 0.35;
      ctx.beginPath();
      ctx.moveTo(0, h);
      for (let x = 0; x <= w; x += 8) {
        const k = x / w;
        const y = yBase + Math.sin(phase * Math.PI * 2 * speed + k * Math.PI * (2 + i)) * amp;
        ctx.lineTo(x, y);
      }
      ctx.lineTo(w, h);
      ctx.closePath();
      const grad = ctx.createLinearGradient(0, yBase - amp, 0, h);
      grad.addColorStop(0, rgba(color, 0.44));
      grad.addColorStop(1, rgba(color, 0.04));
      ctx.fillStyle = grad;
      ctx.fill();
    }
  }

  /** 燭火：往上飄的火星。 */
  drawEmbers(theme, phase, energy, beatPulse) {
    const ctx = this.ctx;
    const w = this.cssW;
    const h = this.cssH;
    const warm = ctx.createLinearGradient(0, h, 0, h * 0.3);
    warm.addColorStop(0, rgba(theme.colors[0], 0.50 + energy * 0.18));
    warm.addColorStop(1, rgba(theme.colors[0], 0));
    ctx.fillStyle = warm;
    ctx.fillRect(0, 0, w, h);

    const spark = theme.colors[1];
    for (const p of this.particles) {
      const y = 1 - ((p.y + phase * p.speed * 0.6) % 1);
      const x = p.x + Math.sin(phase * Math.PI * 2 * 2 + p.seed) * 0.02;
      const r = p.r * (1.2 + beatPulse * 0.8);
      const g = ctx.createRadialGradient(x * w, y * h, 0, x * w, y * h, r * 4);
      g.addColorStop(0, rgba(spark, 0.75));
      g.addColorStop(1, rgba(spark, 0));
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(x * w, y * h, r * 4, 0, Math.PI * 2);
      ctx.fill();
    }
  }
}

if (typeof window !== "undefined") {
  window.AmbientStage = AmbientStage;
}
