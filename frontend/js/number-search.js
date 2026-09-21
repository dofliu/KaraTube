/**
 * 歌號點歌的說法與狀態（純邏輯，沒有 DOM）。
 *
 * 伺服器負責「這組號碼是誰的」（backend/services/song_numbers.py），
 * 這一支負責**查不到的時候要說什麼** —— 而這個功能查不到的原因有四種，
 * 使用者的下一步完全不同：
 *
 *   1. 這首歌已經不在曲庫了（號碼是對的，歌被刪了）→ 去搜尋框重新下載一份，
 *      而且回來之後還是同一組號碼。
 *   2. 沒有發過這組號碼（多半是記錯一碼）→ 去查別的號碼或改用注音查歌。
 *   3. 打的不是號碼（長度不夠、不是數字）→ 繼續打完。
 *   4. 號碼簿讀不出來（機器的問題）→ 使用者做什麼都沒有用，該叫店員。
 *
 * 全部揉成一句「查無此歌號」的話，第 1 種會被當成第 2 種：使用者會反覆確認
 * 自己有沒有記錯號碼，而號碼從頭到尾都是對的。
 */

// 歌號的長度。六位數是商用點歌機的慣例，也是這裡的起跳長度（100001）；
// 但上限刻意放寬到 7 —— 曲庫要九十萬首才會用到第七碼，真的用到的那一天，
// 鍵盤不該把那一碼擋在外面。
const NUMBER_MIN_DIGITS = 6;
const NUMBER_MAX_DIGITS = 7;

/** 只留數字。使用者可能用實體鍵盤打字，貼上來的東西什麼都有。 */
function digitsOnly(text) {
  return String(text == null ? "" : text).replace(/\D/g, "");
}

/** 按一個數字鍵。已經打滿上限就原地不動（而不是把前面的字擠掉）。 */
function pressDigit(query, digit) {
  const d = digitsOnly(digit);
  if (d.length !== 1) return digitsOnly(query);
  const next = digitsOnly(query) + d;
  return next.length > NUMBER_MAX_DIGITS ? digitsOnly(query) : next;
}

/**
 * 退一格。空的時候按退格不該變成 undefined。
 *
 * 名字刻意叫 eraseDigit 而不是 backspace：這幾支前端模組是用 `<script src>`
 * 載進**同一個全域作用域**的，而 library-search.js 已經有一支 backspace()
 * （處理注音字元）。同名的話後載入的這一份會默默覆蓋它，注音查歌的退格
 * 就會變成「只留數字」—— 也就是按一下整串注音全部消失。
 * （frontend/tests/script-scope.test.js 守著這件事。）
 */
function eraseDigit(query) {
  return digitsOnly(query).slice(0, -1);
}

/** 打完了嗎（可以送出查號了）。 */
function isComplete(query) {
  return digitsOnly(query).length >= NUMBER_MIN_DIGITS;
}

/**
 * 號碼的顯示字串：打過的數字 + 還沒打的空格。
 *
 * 固定六格是刻意的 —— 包廂的燈是暗的，使用者需要一眼看出「還差幾碼」，
 * 而不是自己數已經按了幾下。
 */
function queryDisplay(query) {
  const digits = digitsOnly(query);
  const slots = Math.max(NUMBER_MIN_DIGITS, digits.length);
  const out = [];
  for (let i = 0; i < slots; i++) out.push(digits[i] || "＿");
  return out.join(" ");
}

/**
 * 這個數字鍵接下去，曲庫裡還有歌嗎？
 *
 * 注意回傳值**只拿來標亮，不拿來變灰**。注音鍵盤把落空的鍵變灰是對的
 * （沒有那個注音開頭的歌，按了就是白按），數字鍵盤照抄卻會壞掉：
 * 候選清單只列得出「現在唱得到的歌」，所以一組**已下架**號碼的數字
 * 會整串變灰 —— 使用者於是連打都打不完，永遠看不到
 * 「這組號碼是稻香，它已經不在曲庫裡了，重新下載回來還是這組號碼」。
 * 而那句話正是「號碼永不回收」這件事唯一會被看見的地方。
 *
 * 所以這副鍵盤十顆鍵永遠都能按；這支只回答「按下去會不會落在現有的歌上」，
 * 用來把那幾顆標亮（正面提示），而不是把其他顆鎖起來。
 */
function digitHasSongs(digit, nextDigits) {
  if (!nextDigits || !nextDigits.length) return false;
  return nextDigits.indexOf(String(digit)) >= 0;
}

/** 結果列的統計。講的是「曲庫裡有幾首」而不只是「符合幾首」。
 *  （名字不叫 resultSummary 的理由同 eraseDigit。） */
function numberSummary(res) {
  const data = res || {};
  const book = data.book || {};
  const libTotal = Math.max(0, Number(data.library_total) || 0);
  if (book.available === false) return "歌號暫停服務";
  if (libTotal === 0) return "曲庫是空的";
  const shown = (data.songs || []).length;
  const total = Math.max(0, Number(data.total) || 0);
  const head = `曲庫 ${libTotal} 首 ・ 符合 ${total} 首`;
  const tail = shown < total ? `（先列出 ${shown} 首）` : "";
  // 已下架的號碼數字要講出來：它會一直長，而那正是「號碼不回收」的成本。
  const retired = Math.max(0, Number(book.retired) || 0);
  return head + tail + (retired ? ` ・ 已下架 ${retired} 組號碼` : "");
}

/** 還沒開始打的時候說什麼。 */
function idleHint(res) {
  const book = (res || {}).book || {};
  if (book.available === false) {
    return "歌號簿讀不出來，歌號點歌暫時停用<br>"
      + "（其他查歌方式照常可用；請店家檢查 cache/song_numbers.json）";
  }
  const libTotal = Math.max(0, Number((res || {}).library_total) || 0);
  if (libTotal === 0) {
    return "曲庫是空的<br>用上面的搜尋框點一首歌，處理完就會拿到一組歌號";
  }
  return "打出歌號（六位數），或先按前面幾碼看看有哪些歌<br>"
    + "每首歌的號碼印在歌卡上，而且<b>永遠不會變成別首歌</b>";
}

/** 查號的結果要講的那一句話。`res` 是 /api/library/number/{n} 的回覆。 */
function lookupMessage(res) {
  const data = res || {};
  const input = digitsOnly(data.input);
  switch (data.status) {
    case "ready":
      return { tone: "ok", text: `找到了：${(data.song || {}).title || ""}` };
    case "gone": {
      const title = (data.record || {}).title || "那一首";
      // 「號碼沒錯，歌不在了」是這個功能唯一真正需要解釋的狀況：
      // 號碼永遠不回收，所以重新下載一份之後還是同一組號碼 ——
      // 講出這一句，使用者才不會以為自己記錯、去重記一組新的。
      return {
        tone: "gone",
        text: `${input} 是「${title}」，但它已經不在曲庫裡了。`
          + `用上面的搜尋框重新處理一次，回來還是這組號碼。`,
      };
    }
    case "unknown":
      return { tone: "miss", text: `沒有 ${input} 這組歌號（是不是記錯一碼？）` };
    case "invalid":
      return { tone: "miss", text: "歌號是六位數，從 100001 開始" };
    case "unavailable":
      return { tone: "error", text: "歌號簿讀不出來，歌號點歌暫時停用" };
    default:
      return { tone: "", text: "" };
  }
}

/** 歌卡上那一行「🔢 100237」。沒號碼（還在跑流水線）就不印，不要印成 0。 */
function numberLabel(number) {
  const digits = digitsOnly(number);
  return digits.length >= NUMBER_MIN_DIGITS ? digits : "";
}

if (typeof window !== "undefined") {
  window.NumberSearch = {
    NUMBER_MIN_DIGITS, NUMBER_MAX_DIGITS,
    digitsOnly, pressDigit, eraseDigit, isComplete, queryDisplay,
    digitHasSongs, numberSummary, idleHint, lookupMessage, numberLabel,
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    NUMBER_MIN_DIGITS, NUMBER_MAX_DIGITS,
    digitsOnly, pressDigit, eraseDigit, isComplete, queryDisplay,
    digitHasSongs, numberSummary, idleHint, lookupMessage, numberLabel,
  };
}
