/**
 * 歌星查歌的說法與狀態（純邏輯，沒有 DOM）。
 *
 * 「查得到哪些歌星」由伺服器決定（backend/services/artist_index.py），這一支負責
 * **畫面上要講什麼**，而歌星查歌有兩個地方不講清楚就會被當成壞掉：
 *
 * 1. 使用者按 J C 查到的卡片上寫著「周杰倫」。不說一句「也寫作 Jay Chou」，
 *    他會以為自己查錯人了（明明按的是英文）。
 * 2. 按到只剩一位歌星時系統會自動翻開歌單 —— 畫面突然從「歌星」跳成「歌單」，
 *    沒有一句話說明就像是自己亂跳。
 *
 * 鍵盤的按鍵處理（按一鍵、退一格、逐鍵拆解）跟歌名查歌共用 library-search.js：
 * 兩個鍵盤的手感必須一模一樣，各寫一份遲早會走音。
 */
const LS = (typeof window !== "undefined" && window.LibrarySearch)
  ? window.LibrarySearch
  : (typeof require === "function" ? require("./library-search.js") : null);

/** 歌星卡片上的字：名字、別名說明、幾首歌。 */
function artistLabel(artist) {
  const a = artist || {};
  const name = String(a.name || "").trim() || "未知歌星";
  const aliases = (a.aliases || []).filter(x => String(x || "").trim());
  return {
    label: name,
    // 別名要寫出來：按英文查到中文名字的人，需要看到兩者是同一位
    note: aliases.length ? `也寫作 ${aliases.join("、")}` : "",
    count: Math.max(0, Number(a.count) || 0),
    unknown: a.unknown === true,
  };
}

/** 目前在查什麼。注音按了五顆之後沒有人記得自己按了什麼，所以一直顯示著。 */
function artistQueryLabel(query, selected) {
  const keys = LS ? LS.queryKeys(query) : [];
  const parts = [];
  if (keys.length > 0) parts.push(`🔤 ${keys.join(" ")}`);
  else if (String(query || "").trim()) parts.push(`🔤 ${String(query).trim()}`);
  const name = selected && selected.name ? String(selected.name) : "";
  if (name) parts.push(`🎤 ${name}`);
  return parts.length ? parts.join("　・　") : "全部歌星";
}

/** 結果列的統計。講的是「曲庫裡有幾位」而不只是「找到幾位」。 */
function artistResultSummary(res) {
  const data = res || {};
  const libArtists = Math.max(0, Number(data.library_artists) || 0);
  const matched = Math.max(0, Number(data.artist_total) || 0);
  const songs = Math.max(0, Number(data.song_total) || 0);
  if (libArtists === 0) return "曲庫裡還沒有認得出歌星的歌";
  const head = `曲庫 ${libArtists} 位歌星 ・ 符合 ${matched} 位`;
  const sel = data.selected && data.selected.name;
  return sel ? `${head} ・ ${data.selected.name} ${songs} 首` : `${head} ・ 共 ${songs} 首`;
}

/**
 * 歌單那一區的標題。
 *
 * 自動選起來的時候要說出「為什麼畫面跳了」——「只剩這一位，直接翻開」
 * 比單純寫著歌手名多一句交代，使用者才不覺得是自己按錯。
 */
function songHeading(res) {
  const data = res || {};
  const sel = data.selected;
  if (sel && sel.name) {
    const tail = data.auto_selected ? "（只剩這一位，直接翻開歌單）" : "";
    return `🎤 ${sel.name}${tail}`;
  }
  if (Number(data.artist_total) > 0) {
    return "🎤 挑一位歌星，或繼續按注音縮小範圍";
  }
  return "🎤 歌星查歌";
}

/**
 * 查不到的時候說什麼。順序是「最可能的誤會先講」：
 * 1. 曲庫裡根本還沒有歌。
 * 2. 注音查詢關著（伺服器沒裝 pypinyin）卻按了注音。
 * 3. 按了注音查不到：說清楚注音查的是**歌星名字**的首字（不是歌名）。
 * 4. 打字查不到：提醒這裡只查已經備好的曲庫。
 */
function artistEmptyHint(res) {
  const data = res || {};
  const libArtists = Math.max(0, Number(data.library_artists) || 0);
  if (libArtists === 0) {
    return "曲庫裡還沒有認得出歌星的歌<br>"
      + "用上面的搜尋框點一首歌，處理完就會出現在這裡";
  }
  const keys = LS ? LS.queryKeys(data.query || "") : [];
  if (keys.length > 0 && data.bopomofo_available === false) {
    return "這台伺服器沒有安裝注音字典（pypinyin），注音查歌星暫時關閉"
      + "<br>可以直接打歌星的名字";
  }
  if (keys.length > 0) {
    return `曲庫裡沒有「${keys.join(" ")}」開頭的歌星`
      + "<br>注音按的是<b>歌星名字每個字的第一個符號</b>（周杰倫＝ㄓㄐㄌ）；"
      + "英文名按首字母（Jay Chou＝J C）";
  }
  return "曲庫裡查不到這位歌星<br>這裡只查已經備好的歌，查新歌請用上面的搜尋框";
}

/**
 * 同一位歌星在曲庫裡常有好幾種寫法，伺服器已經併成一位並給了 id。
 * 比對一律用 id，不要用畫面上顯示的名字 —— 顯示名會因為合併而改變。
 */
function isSelected(artist, selected) {
  if (!artist || !selected) return false;
  return String(artist.id || "") === String(selected.id || "");
}

/** 再按一次同一位＝取消選取（回到整份歌星清單），跟字數篩選同一個手感。 */
function toggleArtist(currentId, artistId) {
  const now = String(currentId || "");
  const next = String(artistId || "");
  return now === next ? "" : next;
}

// 這一頁的 JS 是一支一支載進同一個全域作用域的，所以最外層的函式名不能跟
// library-search.js 撞（撞到的話瀏覽器裡後載入的那一份會默默蓋掉前面的，
// 而 node --test 給每支獨立作用域，單元測試還是綠的）。對外的名字維持不變。
const API = {
  artistLabel,
  queryLabel: artistQueryLabel,
  resultSummary: artistResultSummary,
  songHeading,
  emptyHint: artistEmptyHint,
  isSelected,
  toggleArtist,
};

if (typeof window !== "undefined") window.ArtistSearch = API;
if (typeof module !== "undefined" && module.exports) module.exports = API;
