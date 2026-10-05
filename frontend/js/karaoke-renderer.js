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
    // 歌詞上面那一行拼音（1.35）。rows 是逐行逐字的陣列，規則在 ruby-layout.js。
    // 預設關著：沒有拼音的包廂，畫面跟這一版之前完全一樣。
    this.rubyRows = null;
    this.rubyEnabled = false;
  }

  setLyrics(lyrics) {
    this.lyrics = lyrics || [];
    this.currentLineIdx = -1;
    this.renderInitialLayout();
  }

  /**
   * 換一首歌的拼音。**一定要在 setLyrics 之後呼叫** ——
   * 長度比對（ruby-layout 的 normalizeDoc）比的是這一份歌詞的行數，
   * 順序反了會拿上一首的行數去比對這一首的拼音。
   */
  setRuby(doc) {
    const layout = (typeof window !== "undefined" && window.RubyLayout) || null;
    this.rubyRows = layout ? layout.normalizeDoc(doc, this.lyrics.length) : null;
    this.refreshRuby();
  }

  /** 顯示／隱藏拼音。設定頁一改就會走到這裡，所以要當場重畫兩行。 */
  setRubyEnabled(enabled) {
    const next = !!enabled;
    if (next === this.rubyEnabled) return;
    this.rubyEnabled = next;
    this.refreshRuby();
  }

  /**
   * 把現在畫面上的兩行重畫一次。
   *
   * 不能只是把拼音藏起來：拼音佔的是歌詞上方的一條空間（`with-ruby` 的
   * padding），開關它會改變歌詞的垂直位置 —— 只藏字不改版面的話，
   * 關掉之後歌詞還是停在原本被推下去的位置，而且沒有人看得出為什麼。
   */
  refreshRuby() {
    if (!this.container) return;
    const on = this.rubyEnabled && Array.isArray(this.rubyRows);
    [this.domLineA, this.domLineB].forEach(el => {
      if (!el) return;
      el.classList.toggle("with-ruby", on);
      const idx = parseInt(el.dataset.rowIdx, 10);
      if (Number.isFinite(idx) && idx >= 0 && this.lyrics[idx]) {
        this.populateLine(el, this.lyrics[idx]);
      }
    });
  }

  /** 這一行要標的拼音（取不到或長度對不上回 null，整行不標）。 */
  rubyForLine(lineIdx, charCount) {
    if (!this.rubyEnabled || !this.rubyRows) return null;
    const layout = (typeof window !== "undefined" && window.RubyLayout) || null;
    if (!layout) return null;
    return layout.rubyForLine(this.rubyRows, lineIdx, charCount);
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
      domElement.dataset.rowIdx = "-1";
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

    // 這一行的拼音。歌詞與拼音是同一個索引同一個長度，對不上就整行不標
    // （對不上代表那份拼音算的是另一份歌詞，而它的症狀是「每個字的拼音
    //   都標在隔壁那個字上面」—— 畫面看起來完全正常）。
    //
    // 查的是**陣列位置**而不是 `line_idx`：拼音是照 lyrics.json 的順序算出來的，
    // 而 `line_idx` 是歌詞自己帶的欄位 —— 兩者在流水線產出的檔案裡一致，
    // 但手改過或舊格式的歌詞對不上時，整行拼音會搬到別的句子上面去。
    const rowIdx = this.lyrics.indexOf(lineData);
    domElement.dataset.rowIdx = rowIdx;
    const ruby = this.rubyForLine(rowIdx, words.length);
    domElement.classList.toggle("with-ruby", this.rubyEnabled && Array.isArray(this.rubyRows));

    // Double-layer structure: .char-bg (crisp white with stroke) + .char-fill (glowing cyan overlay)
    // 拼音照抄同一套雙層結構：它必須跟著那個字一起變色，不然畫面上會有兩套
    // 進度在跑（字在走、拼音不動），而眼睛會跟著亮的那個走。
    const charsHtml = words.map((w, i) => {
      const ch = KaraokeRenderer.escapeChar(w.char);
      const syl = ruby ? KaraokeRenderer.escapeChar(ruby[i] || "") : "";
      const rubyHtml = syl ? `
          <span class="ktv-ruby">
            <span class="ruby-bg">${syl}</span>
            <span class="ruby-fill" style="width: 0%;">${syl}</span>
          </span>` : "";
      return `
        <span class="ktv-char" data-start="${w.start}" data-end="${w.end}">${rubyHtml}
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

    this.sizeRuby(domElement);
  }

  /**
   * 量好每一格的寬度，決定那一格的拼音用幾 px（規則在 ruby-layout.js）。
   *
   * 讀寬度與寫字級**分成兩個迴圈**：交錯著讀寫的話，每寫一次就逼瀏覽器
   * 重排一次（一行二十個字就是二十次），而這段程式碼跑在換行的那一幀上。
   */
  sizeRuby(domElement) {
    const layout = (typeof window !== "undefined" && window.RubyLayout) || null;
    if (!layout || !domElement) return;
    const rubies = domElement.querySelectorAll(".ktv-ruby");
    if (!rubies.length) return;

    const widths = [];
    rubies.forEach(el => {
      const host = el.parentElement;
      // offsetWidth 在還沒進畫面時是 0（舞台剛開、分頁在背景）。
      // 那時候退回字級上限而不是整個不標：下一次換行會再量一次，
      // 而「這一行完全沒有拼音」比「第一行的拼音稍微大了一點」難查得多。
      widths.push(host ? host.offsetWidth : 0);
    });
    rubies.forEach((el, i) => {
      const bg = el.querySelector(".ruby-bg");
      const text = bg ? bg.textContent : "";
      const px = widths[i] > 0 ? layout.rubyFontSize(text, widths[i]) : layout.RUBY_MAX_PX;
      if (px > 0) {
        el.style.fontSize = `${px}px`;
        el.style.display = "";
      } else {
        // 縮到下限還放不下：這一格不標。看不清楚的拼音只多了一個會吸引
        // 目光的雜訊，而目光被吸走的那一秒正是要唱的那一秒。
        el.style.display = "none";
      }
    });
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

      const width = `${pct.toFixed(1)}%`;
      fillEl.style.width = width;
      // 拼音跟著同一個進度走。分開算的話兩套進度會差半個字（拼音的盒子
      // 比字寬），而使用者會開始懷疑哪一套才是真的，然後兩套都不信。
      const rubyFill = ch.querySelector(".ruby-fill");
      if (rubyFill) rubyFill.style.width = width;
    });
  }
}

window.KaraokeRenderer = KaraokeRenderer;
