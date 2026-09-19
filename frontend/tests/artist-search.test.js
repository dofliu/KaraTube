/**
 * 歌星查歌的說法測試。
 *
 * 查得到哪些歌星由伺服器決定（tests/test_artist_index.py 釘著），這一支釘的是
 * 畫面上那幾句話 —— 歌星查歌有兩個地方不講清楚就會被當成壞掉：
 * 按英文查到中文名字（要說「也寫作 Jay Chou」），以及按到只剩一位時畫面
 * 自己跳去歌單（要說「只剩這一位，直接翻開」）。
 */
const test = require("node:test");
const assert = require("node:assert/strict");

const {
  artistLabel, queryLabel, resultSummary, songHeading, emptyHint,
  isSelected, toggleArtist,
} = require("../js/artist-search.js");

// --- 歌星卡片 ---

test("別名要寫出來：按英文查到中文名字的人要看得出是同一位", () => {
  const info = artistLabel({ name: "周杰倫", aliases: ["Jay Chou"], count: 8 });
  assert.equal(info.label, "周杰倫");
  assert.match(info.note, /Jay Chou/);
  assert.equal(info.count, 8);
});

test("只有一種寫法時不多說廢話", () => {
  assert.equal(artistLabel({ name: "五月天", aliases: [], count: 3 }).note, "");
});

test("名字空了也要有東西可顯示", () => {
  assert.equal(artistLabel({}).label, "未知歌星");
  assert.equal(artistLabel(null).count, 0);
});

// --- 查詢條件 ---

test("按了什麼、選了誰，兩個都要一直看得到", () => {
  assert.equal(queryLabel("ㄓㄐㄌ", null), "🔤 ㄓ ㄐ ㄌ");
  assert.match(queryLabel("ㄓㄐㄌ", { name: "周杰倫" }), /周杰倫/);
  assert.equal(queryLabel("", null), "全部歌星");
});

test("統計講的是曲庫裡有幾位，不只是查到幾位", () => {
  const text = resultSummary({ library_artists: 20, artist_total: 3, song_total: 40 });
  assert.match(text, /曲庫 20 位歌星/);
  assert.match(text, /符合 3 位/);
});

test("選定歌星之後，統計改講他有幾首", () => {
  const text = resultSummary({
    library_artists: 20, artist_total: 1, song_total: 8,
    selected: { id: "周杰倫", name: "周杰倫" },
  });
  assert.match(text, /周杰倫 8 首/);
});

test("曲庫空的時候不要報「符合 0 位」這種廢話", () => {
  assert.match(resultSummary({ library_artists: 0 }), /還沒有/);
});

// --- 歌單標題 ---

test("自動翻開歌單時要交代為什麼畫面跳了", () => {
  const head = songHeading({ selected: { name: "五月天" }, auto_selected: true });
  assert.match(head, /五月天/);
  assert.match(head, /只剩這一位/);
});

test("手動選的歌星不必多那句話", () => {
  const head = songHeading({ selected: { name: "五月天" }, auto_selected: false });
  assert.equal(head, "🎤 五月天");
});

test("還沒選人時告訴使用者下一步可以做什麼", () => {
  assert.match(songHeading({ artist_total: 5 }), /挑一位歌星|縮小範圍/);
});

// --- 查不到 ---

test("曲庫是空的就說曲庫是空的，不要怪使用者按錯", () => {
  assert.match(emptyHint({ library_artists: 0 }), /還沒有認得出歌星/);
});

test("伺服器沒裝注音字典要講出來，不然使用者會一直按", () => {
  const hint = emptyHint({ library_artists: 9, query: "ㄓㄐ", bopomofo_available: false });
  assert.match(hint, /pypinyin/);
});

test("注音查不到時要說清楚注音按的是歌星的名字，不是歌名", () => {
  const hint = emptyHint({ library_artists: 9, query: "ㄅㄆㄇ", bopomofo_available: true });
  assert.match(hint, /歌星名字/);
  assert.match(hint, /ㄅ ㄆ ㄇ/);
});

test("打字查不到時提醒這裡只查已經備好的曲庫", () => {
  assert.match(emptyHint({ library_artists: 9, query: "周杰倫" }), /只查已經備好的歌/);
});

// --- 選取 ---

test("比對用 id 不用顯示名（顯示名會因為合併而改變）", () => {
  assert.ok(isSelected({ id: "周杰倫", name: "周杰倫" }, { id: "周杰倫", name: "周杰倫" }));
  assert.ok(!isSelected({ id: "Jay Chou" }, { id: "周杰倫" }));
  assert.ok(!isSelected(null, { id: "周杰倫" }));
});

test("再按一次同一位＝取消選取，回到整份歌星清單", () => {
  assert.equal(toggleArtist("周杰倫", "周杰倫"), "");
  assert.equal(toggleArtist("周杰倫", "五月天"), "五月天");
  assert.equal(toggleArtist("", "五月天"), "五月天");
});
