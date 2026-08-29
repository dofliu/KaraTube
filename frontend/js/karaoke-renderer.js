// High Precision KTV Dual-Line Karaoke Subtitles Renderer (Double-Layer Glowing Font Engine)
class KaraokeRenderer {
  // 歌詞裡的空白是樂句頓點，HTML 會把它塌掉，必須換成 nbsp 才看得出斷句
  static escapeChar(ch) {
    if (ch === " " || ch === "　") return "&nbsp;";
    return ch.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  constructor(containerElement) {
    this.container = containerElement;
    this.lyrics = [];
    this.currentLineIdx = -1;
    this.domLineA = null; // Top line
    this.domLineB = null; // Bottom line
  }

  setLyrics(lyrics) {
    this.lyrics = lyrics || [];
    this.currentLineIdx = -1;
    this.renderInitialLayout();
  }

  renderInitialLayout() {
    this.container.innerHTML = `
      <div class="ktv-line line-top" id="ktvLineA"></div>
      <div class="ktv-line line-bottom" id="ktvLineB"></div>
    `;
    this.domLineA = document.getElementById("ktvLineA");
    this.domLineB = document.getElementById("ktvLineB");

    if (this.lyrics.length > 0) {
      this.populateLine(this.domLineA, this.lyrics[0]);
    }
    if (this.lyrics.length > 1) {
      this.populateLine(this.domLineB, this.lyrics[1]);
    }
  }

  populateLine(domElement, lineData) {
    if (!domElement) return;
    if (!lineData) {
      domElement.innerHTML = "";
      domElement.dataset.lineIdx = "-1";
      return;
    }

    domElement.dataset.lineIdx = lineData.line_idx;
    domElement.dataset.start = lineData.start;
    domElement.dataset.end = lineData.end;

    // Normalize words
    const words = (lineData.words && lineData.words.length > 0) ? lineData.words :
      lineData.text.split('').map((ch, idx, arr) => {
        const dur = (lineData.end - lineData.start) / Math.max(1, arr.length);
        return {
          char: ch,
          start: lineData.start + idx * dur,
          end: lineData.start + (idx + 1) * dur
        };
      });

    // Double-layer structure: .char-bg (crisp white with stroke) + .char-fill (glowing cyan overlay)
    const charsHtml = words.map(w => {
      const ch = KaraokeRenderer.escapeChar(w.char);
      return `
        <span class="ktv-char" data-start="${w.start}" data-end="${w.end}">
          <span class="char-bg">${ch}</span>
          <span class="char-fill" style="width: 0%;">${ch}</span>
        </span>
      `;
    }).join("");

    domElement.innerHTML = `
      <div class="ktv-countdown" id="countdown_${lineData.line_idx}" style="display: none;">
        <div class="countdown-dot dot-3"></div>
        <div class="countdown-dot dot-2"></div>
        <div class="countdown-dot dot-1"></div>
      </div>
      <div class="ktv-text-wrapper">${charsHtml}</div>
    `;
  }

  update(currentTime) {
    if (!this.lyrics || this.lyrics.length === 0) return;

    // Find active singing line
    let activeIdx = -1;
    for (let i = 0; i < this.lyrics.length; i++) {
      const line = this.lyrics[i];
      // Active if playing or within 3 seconds countdown
      if (currentTime >= line.start - 3.0 && currentTime <= line.end + 0.5) {
        activeIdx = i;
        break;
      }
    }

    // If between lines, find next upcoming line
    if (activeIdx === -1) {
      for (let i = 0; i < this.lyrics.length; i++) {
        if (this.lyrics[i].start > currentTime) {
          activeIdx = i;
          break;
        }
      }
      if (activeIdx === -1 && this.lyrics.length > 0) {
        activeIdx = this.lyrics.length - 1;
      }
    }

    if (activeIdx !== -1 && activeIdx !== this.currentLineIdx) {
      this.currentLineIdx = activeIdx;
      this.prepareUpcomingLines(activeIdx);
    }

    // Update character fill animation for both lines
    this.updateLineSweep(this.domLineA, currentTime);
    this.updateLineSweep(this.domLineB, currentTime);
  }

  prepareUpcomingLines(activeIdx) {
    const isEven = activeIdx % 2 === 0;
    const currentLine = this.lyrics[activeIdx];
    const nextLine = this.lyrics[activeIdx + 1] || null;

    if (isEven) {
      // Line A (Top) = Current, Line B (Bottom) = Next
      if (this.domLineA.dataset.lineIdx != activeIdx) {
        this.populateLine(this.domLineA, currentLine);
      }
      if (nextLine && this.domLineB.dataset.lineIdx != (activeIdx + 1)) {
        this.populateLine(this.domLineB, nextLine);
      }
    } else {
      // Line B (Bottom) = Current, Line A (Top) = Next
      if (this.domLineB.dataset.lineIdx != activeIdx) {
        this.populateLine(this.domLineB, currentLine);
      }
      if (nextLine && this.domLineA.dataset.lineIdx != (activeIdx + 1)) {
        this.populateLine(this.domLineA, nextLine);
      }
    }
  }

  updateLineSweep(domElement, currentTime) {
    if (!domElement || !domElement.dataset.lineIdx || domElement.dataset.lineIdx === "-1") return;

    const lineStart = parseFloat(domElement.dataset.start);
    const lineEnd = parseFloat(domElement.dataset.end);

    // 1. Countdown Lights (3, 2, 1)
    const countdownEl = domElement.querySelector(".ktv-countdown");
    if (countdownEl) {
      const timeUntilStart = lineStart - currentTime;
      if (timeUntilStart > 0 && timeUntilStart <= 3.0) {
        countdownEl.style.display = "inline-flex";
        const dot3 = countdownEl.querySelector(".dot-3");
        const dot2 = countdownEl.querySelector(".dot-2");
        const dot1 = countdownEl.querySelector(".dot-1");

        if (dot3) dot3.classList.toggle("active", timeUntilStart <= 3.0);
        if (dot2) dot2.classList.toggle("active", timeUntilStart <= 2.0);
        if (dot1) dot1.classList.toggle("active", timeUntilStart <= 1.0);
      } else {
        countdownEl.style.display = "none";
      }
    }

    // 2. Character-by-character color fill sweep
    const chars = domElement.querySelectorAll(".ktv-char");
    chars.forEach(ch => {
      const cStart = parseFloat(ch.dataset.start);
      const cEnd = parseFloat(ch.dataset.end);
      const fillEl = ch.querySelector(".char-fill");
      if (!fillEl) return;

      let pct = 0;
      if (currentTime >= cEnd) {
        pct = 100;
      } else if (currentTime > cStart && currentTime < cEnd) {
        pct = ((currentTime - cStart) / (cEnd - cStart)) * 100;
      }

      fillEl.style.width = `${pct.toFixed(1)}%`;
    });
  }
}

window.KaraokeRenderer = KaraokeRenderer;
