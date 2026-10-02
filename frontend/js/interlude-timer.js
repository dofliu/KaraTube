/**
 * 間奏倒數 (Interlude Countdown)
 *
 * 商用點歌機（錢櫃那台、金嗓、音圓、JOYSOUND）在**沒有歌詞的那一段**都會講話：
 * 螢幕上出現「間奏 ♪ 18」，數到剩幾秒時把畫面交還給預備光點。
 * 這台機器在這一版之前，那一段是**一片沉默** —— 畫面上留著下一句歌詞不動，
 * 而包廂裡的人分不出三件事：
 *
 *   1. 前奏還沒結束（再等 20 秒）
 *   2. 這首歌的字幕壞了（該按重算）
 *   3. 機器卡住了（該切歌）
 *
 * 三者在螢幕上長得一模一樣，而**猜錯的代價是切掉一首沒有壞的歌**。
 * 所以這個功能的主體不是那個數字，是「機器在這一段裡也要說得出自己在做什麼」。
 *
 * 這一支只做純邏輯（不碰 DOM、不碰 Web Audio、不碰時鐘）：
 * 給一份歌詞與歌曲長度，算出哪幾段是空檔；給一個時間點，回答「現在該顯示什麼」。
 * 舞台端與點歌台共用同一份判斷 —— 兩邊各算一次的話，手機上那顆「跳過前奏」
 * 遲早會跟電視上的倒數對不起來，而那種錯在包廂裡完全查不出原因。
 */

// --- 一段空檔要多長才算「間奏」---
//
// 樂句之間本來就有空檔（4~8 秒很常見），那段時間人正在看畫面上已經排好的下一句。
// 門檻設低的話整首歌都在閃倒數，而**會閃的東西會一直把餘光拉過去**，
// 正好是唱歌的人需要拿去看歌詞的那一點餘光。10 秒是商用機的慣用值，
// 也是「換氣」與「可以先去倒杯水」之間的分界。
const INTERLUDE_MIN_GAP_SECONDS = 10.0;
const INTERLUDE_MIN_GAP_FLOOR = 5.0;
const INTERLUDE_MIN_GAP_CEIL = 30.0;

// 倒數在剩這麼多秒時收掉，把畫面交還給每一句自己的預備光點（三顆點）。
//
// 這是整支裡最容易被忽略、但在包廂裡最明顯的一個決定：**畫面上永遠只能有一套
// 倒數在跑**。數到 0 才交棒的話，使用者會先看到「3、2、1」的數字，
// 接著又看到三顆點再數一次 3、2、1 —— 兩套倒數對不起來的時候
// （它們本來就不是同一個基準：數字是整數、光點是連續的），
// 他會開始懷疑哪一套才是真的，然後兩套都不信。
//
// 這個值必須跟 karaoke-renderer.js 的預備光點視窗一致，
// frontend/tests/interlude-timer.test.js 有一條測試把兩邊釘在一起。
const INTERLUDE_LEAD_IN_SECONDS = 3.0;

// 倒數至少要在畫面上待這麼久才值得出現。
//
// 沒有這道門檻的話，一段剛好跨過門檻的空檔會讓倒數**閃一下就不見**
// （出現 1.2 秒就交棒給光點），而「閃一下的東西」比沒有更糟：
// 看到的人會回頭找它去哪了，沒看到的人會以為自己眼花。
const INTERLUDE_MIN_VISIBLE_SECONDS = 4.0;

// 「跳過」至少要省下這麼多秒才給按。
//
// 按下去只前進 0.6 秒的話，使用者的結論是「這顆鍵壞了」—— 而且他會再按五次。
// 寧可不給按：一顆不在的按鈕沒有人會抱怨，一顆按了沒反應的按鈕會變成客訴。
const INTERLUDE_SKIP_MIN_GAIN_SECONDS = 3.0;

// 畫面上的字。三種空檔要分得開 —— 「間奏」與「尾奏」看起來像同一件事，
// 但對台上那個人的意思相反：一個是「等一下還要唱」，另一個是「唱完了」。
const INTERLUDE_LABELS = {
  intro: "前奏",
  interlude: "間奏",
  outro: "尾奏",
};

/** 夾成合法的「多長才算間奏」。認不得的值一律回預設，不讓壞設定關掉整個功能。 */
function interludeClampMinGap(value) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds)) return INTERLUDE_MIN_GAP_SECONDS;
  return Math.max(INTERLUDE_MIN_GAP_FLOOR, Math.min(INTERLUDE_MIN_GAP_CEIL, seconds));
}

/**
 * 一首歌的空檔表。
 *
 * 刻意在換歌時算一次就好（不是每一幀算一次）：歌詞不會中途變，
 * 而這條迴圈每秒要跑 60 次。
 */
class InterludePlan {
  constructor(gaps = []) {
    this.gaps = gaps;
  }

  /**
   * 從歌詞行與歌曲長度算出空檔表。
   *
   * `lyrics` 是 lyrics.json 的那份陣列（每行 `{start, end}`，秒）。
   * `duration` 是伴奏的長度；拿不到（還沒 loadedmetadata、或 Infinity）就是 0，
   * 這時候**只是沒有尾奏**，前奏與間奏照樣算得出來。
   */
  static fromLyrics(lyrics, duration = 0, options = {}) {
    const minGap = interludeClampMinGap(
      options.minGap === undefined ? INTERLUDE_MIN_GAP_SECONDS : options.minGap);

    // 壞 LRC 什麼都可能送進來：字串時間、end < start、兩行互相重疊、沒排序。
    // 這裡全部正規化掉，而不是相信輸入 —— 一個負的空檔會變成一段「剩 -4 秒」
    // 的倒數，而那是畫面上最像「機器壞了」的東西。
    const lines = [];
    for (const line of (lyrics || [])) {
      if (!line) continue;
      const start = Number(line.start);
      const end = Number(line.end);
      if (!Number.isFinite(start) || start < 0) continue;
      lines.push({ start, end: Number.isFinite(end) && end > start ? end : start });
    }
    lines.sort((a, b) => a.start - b.start);

    const gaps = [];
    if (lines.length === 0) {
      // 整首沒有歌詞的歌**不是一段長間奏**：它是一首沒有歌詞的歌，
      // 而那件事畫面上另有說法（字幕區會講）。在這裡宣布「間奏 03:42」
      // 只會把一個已知的狀況講成另一個看起來更壞的狀況。
      return new InterludePlan(gaps);
    }

    if (lines[0].start >= minGap) {
      gaps.push(interludeMakeGap("intro", 0, lines[0].start));
    }

    // 「上一句唱到哪裡」用**跑過的最大值**而不是前一行的 end：
    // 重疊的行（合唱、翻譯行）會讓前一行的 end 比再前一行早，
    // 照前一行算會憑空長出一段根本不存在的間奏。
    let sungUntil = lines[0].end;
    for (let i = 1; i < lines.length; i++) {
      const line = lines[i];
      if (line.start - sungUntil >= minGap) {
        gaps.push(interludeMakeGap("interlude", sungUntil, line.start));
      }
      if (line.end > sungUntil) sungUntil = line.end;
    }

    const total = Number(duration);
    if (Number.isFinite(total) && total > 0 && total - sungUntil >= minGap) {
      gaps.push(interludeMakeGap("outro", sungUntil, total));
    }

    return new InterludePlan(gaps);
  }

  /** 這個時間點落在哪一段空檔裡（沒有就是 null）。 */
  gapAt(time) {
    const t = Number(time);
    if (!Number.isFinite(t)) return null;
    for (const gap of this.gaps) {
      if (t >= gap.start && t < gap.end) return gap;
    }
    return null;
  }

  /**
   * 現在畫面上該顯示什麼。沒有東西要顯示就回 null。
   *
   * `blockUntil`：在這個時間點之前什麼都不顯示。舞台端拿它來讓開**導唱片頭卡**
   *   —— 片頭卡亮著的那八秒是整首歌裡最多人同時看著同一塊畫面的時候
   *   （歌名、演唱者、點歌人、歌號都在上面），而一個倒數在它旁邊數秒數，
   *   只是在搶同一份注意力。所以前奏倒數等片頭卡收掉之後才接手。
   *
   * `loopEnabled` / `loopEnd`：A-B 練唱循環的終點。跳過去會越過 B 點的話
   *   不給跳 —— 跳完當場被循環拉回來，畫面上看起來就是「按了會彈回來」。
   */
  at(time, options = {}) {
    const t = Number(time);
    if (!Number.isFinite(t)) return null;
    const gap = this.gapAt(t);
    if (!gap) return null;

    const blockUntil = Number(options.blockUntil) || 0;
    const total = gap.end - gap.start;

    if (gap.kind === "outro") {
      // 尾奏**不倒數**。這是這支裡唯一一個「有空檔卻不給數字」的地方，
      // 理由是倒數的意思是「數到 0 就換你」—— 尾奏數到 0 之後沒有東西要唱，
      // 而一個深吸一口氣準備開口、結果發現歌已經結束的人，
      // 會覺得是自己漏掉了一段。
      //
      // 也刻意不給「跳過」：跳過尾奏等於提前結束這首歌，而那個動作
      // 已經有一顆自己的鍵（切歌），名字也誠實得多。
      return {
        kind: "outro",
        label: INTERLUDE_LABELS.outro,
        remaining: null,
        total,
        elapsed: t - gap.start,
        progress: total > 0 ? Math.max(0, Math.min(1, (t - gap.start) / total)) : 1,
        resumeAt: null,
        skippable: false,
      };
    }

    // 看得見的區間：前奏要讓開片頭卡，尾端要讓開預備光點。
    const visibleFrom = gap.kind === "intro" ? Math.max(gap.start, blockUntil) : gap.start;
    const visibleUntil = gap.end - INTERLUDE_LEAD_IN_SECONDS;
    // 這個判斷只看空檔的兩端、不看現在幾秒，所以同一段空檔從頭到尾的答案
    // 一定一致 —— 不然會出現「數到一半自己決定不顯示」的閃爍。
    if (visibleUntil - visibleFrom < INTERLUDE_MIN_VISIBLE_SECONDS) return null;
    if (t < visibleFrom || t >= visibleUntil) return null;

    // 倒數數到**第一個字**，不是數到這條帶子自己消失的時候。
    // 數到帶子消失的話，螢幕上那個數字的意思會變成「倒數還有多久結束」，
    // 而那是沒有人需要知道的事。
    const remaining = gap.end - t;
    const gain = remaining - INTERLUDE_LEAD_IN_SECONDS;

    let skippable = options.skipEnabled !== false && gain >= INTERLUDE_SKIP_MIN_GAIN_SECONDS;
    const resumeAt = gap.end - INTERLUDE_LEAD_IN_SECONDS;
    if (skippable && options.loopEnabled) {
      const loopEnd = Number(options.loopEnd);
      if (Number.isFinite(loopEnd) && resumeAt > loopEnd) skippable = false;
    }

    return {
      kind: gap.kind,
      label: INTERLUDE_LABELS[gap.kind],
      remaining,
      total,
      elapsed: t - gap.start,
      progress: total > 0 ? Math.max(0, Math.min(1, (t - gap.start) / total)) : 1,
      // 跳到**還剩三秒**的地方，不是跳到第一個字。
      // 跳到第一個字等於把人推上台：沒有吸氣的時間，第一句必漏 ——
      // 而且評分會照實算他漏了那一句。三秒剛好是預備光點亮起來的時候，
      // 所以跳完之後看到的畫面，跟一路等過來看到的完全一樣。
      resumeAt,
      skippable,
    };
  }
}

/** 一段空檔。start/end 都在**歌詞時間軸**上（跟 lyrics.json 同一個基準）。 */
function interludeMakeGap(kind, start, end) {
  return { kind, start, end, length: end - start };
}

if (typeof window !== "undefined") {
  window.InterludeTimer = {
    InterludePlan,
    interludeClampMinGap,
    INTERLUDE_LABELS,
    INTERLUDE_MIN_GAP_SECONDS,
    INTERLUDE_LEAD_IN_SECONDS,
    INTERLUDE_MIN_VISIBLE_SECONDS,
    INTERLUDE_SKIP_MIN_GAIN_SECONDS,
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    InterludePlan,
    interludeClampMinGap,
    INTERLUDE_LABELS,
    INTERLUDE_MIN_GAP_SECONDS,
    INTERLUDE_MIN_GAP_FLOOR,
    INTERLUDE_MIN_GAP_CEIL,
    INTERLUDE_LEAD_IN_SECONDS,
    INTERLUDE_MIN_VISIBLE_SECONDS,
    INTERLUDE_SKIP_MIN_GAIN_SECONDS,
  };
}
