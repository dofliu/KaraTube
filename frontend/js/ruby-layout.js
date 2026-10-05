/**
 * 歌詞拼音標注的版面規則 (Ruby layout)
 *
 * 後端那一半在 `backend/services/ruby.py`（標什麼、標不標得準）。
 * 這一支只管**放得下放不下**，而且刻意做成純函數 —— 版面的錯要在 CI 被抓到，
 * 不是等平板進了包廂才發現第二行歌詞被拼音蓋住。
 *
 * ──────────────────────────────────────────────────────────────────
 * 為什麼拼音的字級是算出來的，不是固定值
 *
 * 一個全形漢字在舞台上佔 46px，而它的拼音可能是 "è"（1 個字母）也可能是
 * "zhuāng"（6 個）—— 差六倍。固定字級的結果是：短的那幾個浮在半空中，
 * 長的那幾個跟左右鄰居黏成一條看不懂的字母流，而「黏在一起」比沒有拼音更糟，
 * 因為使用者會停下來解讀它 —— 而他停下來的那一秒正是要唱的那一秒。
 *
 * 所以字級由音節長度決定：長的縮小、短的維持上限。縮到下限還放不下的，
 * 那一格就不標（回 0）——「看不清楚的拼音」跟「沒有拼音」比起來只多了一個
 * 會吸引目光的雜訊。
 *
 * ──────────────────────────────────────────────────────────────────
 * 長度對不上就整行不標
 *
 * 拼音陣列與逐字陣列必須同長同序。對不上代表那份拼音算的是**另一份歌詞**
 * （重算歌詞之後的殘留檔），而它的症狀是「每個字的拼音都標在隔壁那個字上面」
 * —— 畫面看起來完全正常，只有看得懂的人才發現全錯。
 * 那種錯沒有補救方式，所以寧可整行空白。
 *
 * ──────────────────────────────────────────────────────────────────
 * auto 是什麼意思
 *
 * 「要不要顯示拼音」的答案已經寫在另一個設定裡：舞台的介面語言就是
 * 「這塊螢幕現在是給誰看的」。所以 auto = 舞台不是中文介面時才顯示。
 * 做成獨立的開關也可以，但那等於要求櫃檯記得切兩次 ——
 * 而第二次沒切的那間包廂，功能等於不存在。
 */
(function (root, factory) {
  const mod = factory();
  if (typeof module === "object" && module.exports) module.exports = mod;
  else root.RubyLayout = mod;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // 拼音的字級上下限（px）。上限是「不要搶走歌詞的視覺重量」——
  // 拼音比歌詞醒目的話，看得懂中文的人也會開始讀拼音，然後慢半拍。
  const RUBY_MAX_PX = 18;
  const RUBY_MIN_PX = 11;

  // 粗體拉丁字母的平均寬高比。量的是實際字型下 "zhuang" 這種全小寫音節，
  // 寧可估寬一點（估窄的代價是黏在一起，估寬的代價只是小一點點）。
  const GLYPH_ASPECT = 0.62;

  // 一個字的格子能借用多少空間給拼音。1.0 = 只用自己那一格。
  // 稍微超出一點是可以的（左右各借一點點空白），但不能借到看起來像是
  // 標在隔壁那個字上面 —— 那正是這個功能最怕的那種錯。
  const SLOT_SLACK = 1.12;

  const MODES = ["off", "auto", "on"];
  const DEFAULT_MODE = "auto";

  /** 顯示模式夾成合法值。認不得就退回預設，不丟例外。 */
  function coerceMode(value) {
    const text = String(value == null ? "" : value).trim().toLowerCase();
    return MODES.indexOf(text) >= 0 ? text : DEFAULT_MODE;
  }

  /**
   * 這塊螢幕現在要不要顯示拼音。
   *
   * `hasRuby` 是「這首歌算得出拼音嗎」（日語歌、英文歌算不出來）——
   * 算不出來時連 on 都不顯示，因為沒有東西可以顯示。
   */
  function rubyVisible(mode, stageLocale, hasRuby) {
    if (!hasRuby) return false;
    const m = coerceMode(mode);
    if (m === "off") return false;
    if (m === "on") return true;
    // auto：中文介面的包廂不需要這一行（看得懂的人讀拼音只會慢半拍）。
    return !/^zh\b|^zh-/i.test(String(stageLocale || ""));
  }

  /**
   * 這一格的拼音該用幾 px。放不下（縮到下限還是超出）回 0 = 不標。
   *
   * `slotPx` 是這個字在畫面上的寬度（含字距），舞台端量一次就好 ——
   * 每個字各量一次的話，一行 20 個字就是 20 次 layout 讀取，
   * 而這段程式碼是在換行的那一幀跑的。
   */
  function rubyFontSize(syllable, slotPx) {
    const text = String(syllable || "");
    if (!text) return 0;
    const slot = Number(slotPx);
    if (!(slot > 0)) return 0;
    const avail = slot * SLOT_SLACK;
    const ideal = avail / (text.length * GLYPH_ASPECT);
    if (ideal >= RUBY_MAX_PX) return RUBY_MAX_PX;
    if (ideal < RUBY_MIN_PX) return 0;
    // 取整數 px：半格的字級在不同瀏覽器上會 round 到不同邊，
    // 而「同一首歌在點歌台與舞台上長得不一樣」是查不出原因的那種回報。
    return Math.floor(ideal);
  }

  /**
   * 整份拼音文件 → 可以用的逐行陣列。認不得的一律回 null（= 這首歌沒有拼音）。
   */
  function normalizeDoc(doc, lineCount) {
    if (!doc || typeof doc !== "object") return null;
    if (!doc.available) return null;
    const rows = doc.lines;
    if (!Array.isArray(rows)) return null;
    if (Number.isFinite(lineCount) && rows.length !== lineCount) return null;
    return rows;
  }

  /**
   * 第 `lineIdx` 行、共 `charCount` 個字的拼音。
   * 取不到或長度對不上一律回 null —— 寧可整行不標（見檔頭）。
   */
  function rubyForLine(rows, lineIdx, charCount) {
    if (!Array.isArray(rows)) return null;
    const row = rows[lineIdx];
    if (!Array.isArray(row)) return null;
    if (row.length !== charCount) return null;
    // 一整行都是空字串（純英文的那幾句）就當作沒有：回 null 讓呼叫端
    // 連空的 DOM 節點都不要建。
    let any = false;
    for (let i = 0; i < row.length; i++) {
      if (row[i]) { any = true; break; }
    }
    return any ? row : null;
  }

  return {
    RUBY_MAX_PX, RUBY_MIN_PX, GLYPH_ASPECT, SLOT_SLACK,
    RUBY_MODES: MODES, DEFAULT_RUBY_MODE: DEFAULT_MODE,
    coerceMode, rubyVisible, rubyFontSize, normalizeDoc, rubyForLine,
  };
});
