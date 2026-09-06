/**
 * 段落評分 (Per-section Scoring)
 *
 * 商用 KTV（JOYSOUND / DAM）的成績單不只給一個總分，還會告訴你
 * 「哪一段唱得最好、哪一段拖垮分數」—— 這才是能拿來練歌的資訊：
 * 總分 62 分只讓人沮喪，「副歌 2 只有 31%、主歌都在 70% 以上」直接指出要練哪裡。
 *
 * 這裡的分段沿用 `/api/songs/{id}/sections`（後端 chorus_detector 由歌詞算出的曲式），
 * 評分心跳每一幀把「這一幀落在哪一段、有沒有導唱音符、有沒有唱準」記進對應的段落，
 * 唱完再把每段的命中率濃縮成一列成績。
 *
 * 純資料邏輯：不碰 DOM、不碰 Web Audio、不發網路請求，所以能用 node --test 直接跑。
 * 瀏覽器端由 player.html 以 <script> 載入（必須在 pitch-engine.js 之前）。
 */

// 音準率換算等級。逐幀命中其實很嚴格，門檻不能照直覺的 90/80 分切
// （這份門檻同時給整首總評與單一段落用，兩邊的手感才會一致）。
const GRADE_THRESHOLDS = [
  [0.75, "SSS"],
  [0.60, "SS"],
  [0.45, "S"],
  [0.30, "A"],
  [0.15, "B"],
];
const LOWEST_GRADE = "C";

// 前奏/間奏/尾奏沒有歌詞也沒有導唱音符，列進成績單只會是一排 0%。
// 用「排除」而不是「白名單」：後端日後多切出 bridge 之類的段落會自動納入。
const UNSUNG_KINDS = ["intro", "interlude", "outro"];

// 一段至少要有這麼多幀導唱音符才值得評分（60fps 下約 0.5 秒）。
// 太短的段落一兩幀的運氣就能決定它是「最佳」或「最差」，那是雜訊不是資訊。
const MIN_SECTION_NOTE_FRAMES = 30;

// 最佳與最差的差距小於這個值就不點名任何段落 ——
// 整首均勻地唱到 55% 時說「副歌 2 是你最差的段落（54%）」是在製造焦慮，不是回饋。
const MIN_SECTION_SPREAD = 0.05;

function gradeForAccuracy(accuracy) {
  const value = Number(accuracy);
  if (!Number.isFinite(value)) return LOWEST_GRADE;
  for (const [threshold, grade] of GRADE_THRESHOLDS) {
    if (value >= threshold) return grade;
  }
  return LOWEST_GRADE;
}

function round3(value) {
  return Math.round(value * 1000) / 1000;
}

function toFiniteNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

/**
 * 把後端來的段落清單整理成可以二分搜尋的形式。
 *
 * 濾掉不唱的段落與時間軸壞掉的資料，依起點排序，並丟掉與前一段重疊的段落 ——
 * 重疊會讓二分搜尋的前提失效（同一個時間點對到兩段），寧可少評一段也不要對錯段。
 */
function normalizeSections(sections) {
  const cleaned = [];
  for (const raw of Array.isArray(sections) ? sections : []) {
    if (!raw || typeof raw !== "object") continue;
    const kind = String(raw.kind || "verse");
    if (UNSUNG_KINDS.includes(kind)) continue;
    const start = toFiniteNumber(raw.start);
    const end = toFiniteNumber(raw.end);
    if (start === null || end === null || end <= start) continue;
    cleaned.push({
      index: toFiniteNumber(raw.index) === null ? cleaned.length : Number(raw.index),
      kind,
      label: String(raw.label || kind),
      preview: String(raw.preview || ""),
      start,
      end,
    });
  }
  cleaned.sort((a, b) => a.start - b.start || a.end - b.end);

  const result = [];
  for (const section of cleaned) {
    const prev = result[result.length - 1];
    if (prev && section.start < prev.end) continue;
    result.push(section);
  }
  return result;
}

class SectionScorer {
  constructor(sections) {
    this.setSections(sections);
  }

  /** 換歌時呼叫：載入這首歌的段落並歸零統計。傳空的就等於停用段落評分。 */
  setSections(sections) {
    this.sections = normalizeSections(sections);
    this.reset();
  }

  /** 歸零所有段落的統計（換歌、重唱都要）。 */
  reset() {
    this.stats = this.sections.map(() => ({
      noteFrames: 0,
      hitFrames: 0,
      perfectFrames: 0,
      sangFrames: 0,
    }));
  }

  get enabled() {
    return this.sections.length > 0;
  }

  /**
   * 二分搜出這個時間點落在哪一段，落在段落之間（間奏、被濾掉的段）回傳 -1。
   *
   * 不用「記住上一段往前掃」的做法：練唱模式的 A-B 循環與進度條跳轉會讓時間
   * 任意前後跳，線性游標在那些情境下會退化成每幀重掃整首。
   */
  sectionIndexAt(time) {
    const t = toFiniteNumber(time);
    if (t === null) return -1;
    let lo = 0;
    let hi = this.sections.length - 1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      const section = this.sections[mid];
      if (t < section.start) hi = mid - 1;
      else if (t > section.end) lo = mid + 1;
      else return mid;
    }
    return -1;
  }

  /**
   * 評分心跳：把這一幀的結果記進所在段落，回傳段落編號（不在任何段落回 -1）。
   *
   * `outcome` 用 pitch-engine 已經算好的判定：
   * `hasNote`（此刻有導唱音符 = 這一幀算分母）、`sang`（有唱出聲音）、
   * `hit`（唱在音準容差內）、`perfect`（幾乎完全準）。
   */
  count(time, outcome) {
    const idx = this.sectionIndexAt(time);
    if (idx < 0) return -1;
    const stat = this.stats[idx];
    const o = outcome || {};
    if (o.hasNote) stat.noteFrames++;
    if (o.sang) stat.sangFrames++;
    if (o.hit) stat.hitFrames++;
    if (o.perfect) stat.perfectFrames++;
    return idx;
  }

  /**
   * 唱畢結算用的段落成績。
   *
   * `sections` 是每一段的命中率（依歌曲順序），`best` / `worst` 是值得點名的
   * 最佳與待加強段落 —— 可評分的段落不到兩段、或全曲表現太平均時兩者都是 null，
   * 前端就只顯示長條圖不下結論。
   */
  summary(minNoteFrames = MIN_SECTION_NOTE_FRAMES) {
    const rows = this.sections.map((section, i) => {
      const stat = this.stats[i];
      const accuracy = stat.noteFrames > 0 ? stat.hitFrames / stat.noteFrames : 0;
      return {
        index: section.index,
        kind: section.kind,
        label: section.label,
        preview: section.preview,
        start: round3(section.start),
        end: round3(section.end),
        note_frames: stat.noteFrames,
        hit_frames: stat.hitFrames,
        perfect_frames: stat.perfectFrames,
        sang_frames: stat.sangFrames,
        accuracy: round3(accuracy),
        grade: gradeForAccuracy(accuracy),
        graded: stat.noteFrames >= minNoteFrames,
      };
    });

    const graded = rows.filter((row) => row.graded);
    let best = null;
    let worst = null;
    if (graded.length >= 2) {
      // 同分時偏好先唱到的那一段，讓結果穩定（不受排序演算法的實作影響）
      const ranked = graded.slice().sort((a, b) => b.accuracy - a.accuracy || a.index - b.index);
      const top = ranked[0];
      const bottom = ranked[ranked.length - 1];
      // 差距先四捨五入再比門檻：0.6 - 0.55 在浮點裡是 0.049999…，
      // 直接比會讓「剛好差 5 個百分點」這個邊界隨機失效
      if (round3(top.accuracy - bottom.accuracy) >= MIN_SECTION_SPREAD) {
        best = top;
        worst = bottom;
      }
    }

    return { sections: rows, graded_count: graded.length, best, worst };
  }
}

if (typeof window !== "undefined") {
  window.SectionScorer = SectionScorer;
  window.gradeForAccuracy = gradeForAccuracy;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    SectionScorer,
    gradeForAccuracy,
    normalizeSections,
    GRADE_THRESHOLDS,
    LOWEST_GRADE,
    UNSUNG_KINDS,
    MIN_SECTION_NOTE_FRAMES,
    MIN_SECTION_SPREAD,
  };
}
