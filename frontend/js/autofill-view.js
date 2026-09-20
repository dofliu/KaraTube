/**
 * 自動接歌的說法 (Autofill View)
 *
 * 規則本身在伺服器（backend/services/autofill.py）：什麼時候接、接什麼、
 * 接了之後算誰的。這一支只負責**把規則講成人話**。
 *
 * 為什麼這幾句話值得抽成一支有測試的模組：這是系統裡唯一一個「機器自己
 * 發出聲音」的功能。一台會自己放歌的機器，只要使用者預期不到它下一步要做
 * 什麼，它就從貼心變成失控 —— 而唯一能讓人放心的是那句話講不講得清楚：
 *
 *   現在這首是誰點的（還是機器接的）
 *   它為什麼挑這首
 *   它還會接幾首、什麼時候會安靜下來
 *   有人點歌的話它會怎麼樣（讓位，還是唱完）
 *
 * 少講第三件，使用者會去關掉整個功能（因為他不知道它什麼時候會停）；
 * 少講第四件，他會以為自己點的歌被機器插隊了。
 *
 * 純資料邏輯：不碰 DOM、不發網路請求。
 */

// 挑歌來源在畫面上的叫法。跟設定頁那張表是同一組字 ——
// 同一個選項在兩個地方有兩個名字，使用者會以為那是兩件事。
const AUTOFILL_SOURCE_LABELS = {
  mixed: "混著挑",
  favorites: "只挑我的最愛",
  popular: "只挑常點的歌",
  fresh: "只挑還沒唱過的",
};

/** 正在播的那一首是機器接的嗎。 */
function isAutoSong(song) {
  return !!(song && song.auto);
}

/** 「現在這首是機器接的」那顆小標。不是機器接的就回空字串。 */
function autoBadge(song) {
  return isAutoSong(song) ? "🎧 自動接歌" : "";
}

/**
 * 佇列上方那一行：自動接歌現在的狀態。
 *
 * 關著的時候刻意**不是空白**：那一排還有一顆「🎲 來一首」，一行字都沒有的話
 * 那顆鍵看起來像是沒有作用的裝飾。關著時講的是 🎲 解的那個問題
 * （想唱歌但想不到唱什麼），不是推銷自動接歌。
 */
function autofillLine(state) {
  const s = state || {};
  if (!s.enabled) return "想不到要唱什麼？讓機器從曲庫裡挑一首";

  const idle = Math.max(0, Math.floor(Number(s.idle_seconds) || 0));
  const stopAfter = Math.max(1, Math.floor(Number(s.stop_after) || 1));
  const streak = Math.max(0, Math.floor(Number(s.streak) || 0));
  const source = AUTOFILL_SOURCE_LABELS[s.source] || AUTOFILL_SOURCE_LABELS.mixed;
  const when = idle > 0 ? `沒人點歌 ${idle} 秒後` : "佇列一空就";

  if (s.playing) {
    const why = (s.last && s.last.reason) ? `（${s.last.reason}）` : "";
    // 「有人點歌就讓開」是這裡最重要的一句：不講的話，想點歌的人會先去按切歌
    return `🎧 這首是機器接的${why}・有人點歌就讓開・已接 ${streak}/${stopAfter} 首`;
  }
  if (s.stopped) {
    return `🎧 自動接歌已連著接了 ${stopAfter} 首，先安靜下來・有人點一首就會再接`;
  }
  return `🎧 自動接歌開著・${when}${source}・最多連著接 ${stopAfter} 首（已接 ${streak}）`;
}

/**
 * 佇列空了的時候，中間那塊要說什麼。
 *
 * 自動接歌開著時要先講「等一下機器會自己接」—— 否則使用者會以為機器壞了
 * 或以為是誰偷點的歌；關著就是原本那句「快搜尋並點播想唱的歌吧」。
 */
function emptyQueueNote(state) {
  const s = state || {};
  if (!s.enabled || s.stopped) return "點歌佇列為空<br>快搜尋並點播想唱的歌吧！";
  const idle = Math.max(0, Math.floor(Number(s.idle_seconds) || 0));
  const wait = idle > 0 ? `${idle} 秒沒有人點歌` : "佇列一空";
  return `點歌佇列為空<br>${wait}，機器會自己接一首（點歌隨時可以把它換掉）`;
}

/**
 * 🎲 點完之後的通知。
 *
 * 一定要講出「為什麼是這首」：按下一顆骰子卻拿到一首自己沒想到的歌時，
 * 「⭐ 我的最愛」跟「🌱 還沒唱過」是兩種完全不同的心情，而沒有理由的話
 * 使用者只會覺得機器在亂給。
 */
function randomPickNote(result) {
  const r = result || {};
  const item = r.item || {};
  const title = item.title || "一首歌";
  const reason = r.reason ? `（${r.reason}）` : "";
  return `🎲 幫你點了：${title}${reason}`;
}

/** 曲庫還沒有歌可以挑時的說法。要講出下一步，不然它就只是一句「不行」。 */
function emptyLibraryNote() {
  return "曲庫裡還沒有備好的歌。先搜尋點一首，處理好之後就會留在曲庫裡，"
    + "下次按 🎲 就抽得到了。";
}

if (typeof window !== "undefined") {
  window.AutofillView = {
    AUTOFILL_SOURCE_LABELS,
    isAutoSong, autoBadge, autofillLine, emptyQueueNote, randomPickNote, emptyLibraryNote,
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    AUTOFILL_SOURCE_LABELS,
    isAutoSong, autoBadge, autofillLine, emptyQueueNote, randomPickNote, emptyLibraryNote,
  };
}
