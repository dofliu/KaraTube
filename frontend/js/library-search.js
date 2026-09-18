/**
 * 曲庫查歌的說法與狀態（純邏輯，沒有 DOM）。
 *
 * 伺服器負責「查得到什麼」（backend/services/song_index.py），這一支負責
 * 「查不到的時候要說什麼」—— 那是這個功能最容易壞掉的地方：
 * 使用者按了三個注音、畫面空白，他不會去想是不是字數條件還開著，
 * 他會認定曲庫裡沒有這首歌，然後回去用 YouTube 搜尋重下載一份已經有的歌。
 */

// 注音符號的 Unicode 區段（ㄅ U+3105 ~ ㄯ U+312F）
const BOPOMOFO_RE = /[ㄅ-ㄯ]/;

/** 這個字元可以當成查歌的一鍵嗎（注音符號或英數）。 */
function isKeyChar(ch) {
  if (!ch) return false;
  return BOPOMOFO_RE.test(ch) || /[A-Za-z0-9]/.test(ch);
}

/** 使用者按出來的那一串 → 逐鍵陣列。跟伺服器的 normalize_query 同一套規則。 */
function queryKeys(query) {
  const out = [];
  for (const ch of String(query || "")) {
    if (BOPOMOFO_RE.test(ch)) out.push(ch);
    else if (/[A-Za-z0-9]/.test(ch)) out.push(ch.toUpperCase());
  }
  return out;
}

/** 按一鍵。上限擋在 12 鍵：沒有歌名需要按到第 13 個字才查得出來。 */
function pressKey(query, key) {
  if (!isKeyChar(key)) return String(query || "");
  const next = String(query || "") + (BOPOMOFO_RE.test(key) ? key : key.toUpperCase());
  return queryKeys(next).length > 12 ? String(query || "") : next;
}

/** 退一格。空的時候按退格不該變成 undefined。 */
function backspace(query) {
  const chars = Array.from(String(query || ""));
  chars.pop();
  return chars.join("");
}

/**
 * 查詢條件的一句話。「現在在查什麼」要一直看得到 ——
 * 注音是一串符號，按了五顆之後沒有人記得自己按了什麼。
 */
function queryLabel(query, chars) {
  const keys = queryKeys(query);
  const parts = [];
  if (keys.length > 0) parts.push(`🔤 ${keys.join(" ")}`);
  else if (String(query || "").trim()) parts.push(`🔤 ${String(query).trim()}`);
  if (chars > 0) parts.push(`${chars} 個字`);
  return parts.length ? parts.join("　・　") : "整個曲庫";
}

/** 結果列的統計。講的是「曲庫裡有幾首」而不只是「找到幾首」。 */
function resultSummary(res) {
  const data = res || {};
  const total = Math.max(0, Number(data.total) || 0);
  const libTotal = Math.max(0, Number(data.library_total) || 0);
  const shown = (data.songs || []).length;
  if (libTotal === 0) return "曲庫是空的";
  const head = `曲庫 ${libTotal} 首 ・ 符合 ${total} 首`;
  return shown < total ? `${head}（先列出 ${shown} 首）` : head;
}

/**
 * 查不到的時候說什麼。
 *
 * 順序是「最可能的誤會先講」：
 * 1. 曲庫是空的 —— 這裡只查已經備好的歌，沒歌就是沒歌。
 * 2. 字數條件還開著 —— 按了注音又看到空白，多半是這個。
 * 3. 注音查詢關著（伺服器沒裝 pypinyin）卻按了注音。
 * 4. 真的查不到：提醒這裡只查曲庫，要新歌請用上面的搜尋框。
 */
function emptyHint(res) {
  const data = res || {};
  const libTotal = Math.max(0, Number(data.library_total) || 0);
  if (libTotal === 0) {
    return "曲庫是空的<br>用上面的搜尋框點一首歌，處理完就會出現在這裡";
  }
  const keys = queryKeys(data.query || "");
  const chars = Math.max(0, Number(data.chars) || 0);
  if (chars > 0 && keys.length > 0) {
    return `曲庫裡沒有「${keys.join(" ")}」開頭又剛好 ${chars} 個字的歌`
      + `<br>把字數條件取消再看一次？`;
  }
  if (chars > 0) {
    return `曲庫裡沒有 ${chars} 個字的歌名<br>換一個字數看看`;
  }
  if (keys.length > 0 && data.bopomofo_available === false) {
    return "這台伺服器沒有安裝注音字典（pypinyin），注音查歌暫時關閉"
      + "<br>可以改用字數或直接打歌名";
  }
  if (keys.length > 0) {
    return `曲庫裡查不到「${keys.join(" ")}」`
      + "<br>注音查的是<b>歌名每個字的第一個符號</b>（稻香＝ㄉㄒ）；"
      + "查新歌請用上面的搜尋框";
  }
  return "曲庫裡查不到這首<br>這裡只查已經備好的歌，查新歌請用上面的搜尋框";
}

/** 字數按鈕上的字。 */
function charBucketLabel(bucket) {
  const b = bucket || {};
  const n = Math.max(0, Number(b.chars) || 0);
  const count = Math.max(0, Number(b.count) || 0);
  return { label: `${n} 個字`, count };
}

/**
 * 注音鍵盤上哪些鍵按下去還查得到歌？
 *
 * 商用點歌機會把「按下去會沒有結果」的鍵變灰。這裡做不到那麼細
 * （要先知道曲庫每一首的每一個位置），但至少做得到第一鍵：
 * 目前這一串後面再接哪些鍵仍然有歌，是伺服器算好回傳的；沒有這份資料時
 * 一律當成可按 —— 寧可按下去查不到，也不要把查得到的鍵鎖起來。
 */
function keyEnabled(key, nextKeys) {
  if (!nextKeys || !nextKeys.length) return true;
  return nextKeys.indexOf(key) >= 0;
}

/**
 * 把伺服器回的「下一鍵」收斂成這個鍵盤上真的用得到的那些。
 *
 * 下一格是英文單字（「稻香 Rice Fields」按完 ㄉㄒ）或已經沒有下一格時，
 * 照字面套用會讓整個注音鍵盤一起變灰 —— 那個畫面看起來就是壞掉，
 * 而它其實什麼資訊都沒給。全部會變灰就一個都不變灰。
 */
function usableNextKeys(rows, nextKeys) {
  const flat = [];
  (rows || []).forEach(row => (row || []).forEach(k => flat.push(k)));
  const keep = (nextKeys || []).filter(k => flat.indexOf(k) >= 0);
  return keep.length ? keep : [];
}

if (typeof window !== "undefined") {
  window.LibrarySearch = {
    isKeyChar, queryKeys, pressKey, backspace,
    queryLabel, resultSummary, emptyHint, charBucketLabel, keyEnabled, usableNextKeys,
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    isKeyChar, queryKeys, pressKey, backspace,
    queryLabel, resultSummary, emptyHint, charBucketLabel, keyEnabled, usableNextKeys,
  };
}
