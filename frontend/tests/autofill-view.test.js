/**
 * 自動接歌的說法測試。
 *
 * 規則本身在伺服器且已經有測試釘著（tests/test_autofill.py），這一支釘的是
 * **那幾句話有沒有講清楚**。一台會自己放歌的機器，唯一能讓人放心的地方就是
 * 「可預期」：它現在在做什麼、還會接幾首、我點歌的話它會怎麼樣。
 */
const test = require("node:test");
const assert = require("node:assert/strict");

const {
  isAutoSong, autoBadge, autofillLine, emptyQueueNote, randomPickNote, emptyLibraryNote,
} = require("../js/autofill-view.js");

const OFF = { enabled: false };
const ON = { enabled: true, source: "mixed", idle_seconds: 20, stop_after: 3,
             streak: 0, playing: false, stopped: false };

// --- 「這首是機器接的」 ---

test("機器接的歌要標出來，人點的不標", () => {
  assert.equal(isAutoSong({ auto: true }), true);
  assert.equal(isAutoSong({ requested_by: "小明" }), false);
  assert.equal(isAutoSong(null), false);
  assert.match(autoBadge({ auto: true }), /自動接歌/);
  assert.equal(autoBadge({ auto: false }), "");
});

// --- 佇列上方那一行 ---

test("關著的時候講的是 🎲 那顆鍵解的問題，不是推銷自動接歌", () => {
  const line = autofillLine(OFF);
  assert.ok(line.length > 0);            // 空白會讓旁邊那顆 🎲 看起來像裝飾
  assert.doesNotMatch(line, /自動接歌開著/);
});

test("開著但還沒接：講清楚什麼時候接、照什麼挑、最多接幾首", () => {
  const line = autofillLine(ON);
  assert.match(line, /20 秒/);
  assert.match(line, /混著挑/);
  assert.match(line, /最多連著接 3 首/);
});

test("0 秒是合法設定，講法要跟著變（不能印出「沒人點歌 0 秒後」）", () => {
  const line = autofillLine({ ...ON, idle_seconds: 0 });
  assert.match(line, /佇列一空/);
  assert.doesNotMatch(line, /0 秒/);
});

test("正在放機器接的歌時，最重要的是「有人點歌就讓開」", () => {
  const line = autofillLine({ ...ON, playing: true, streak: 2,
                              last: { reason: "⭐ 我的最愛" } });
  assert.match(line, /有人點歌就讓開/);   // 不講的話，想點歌的人會先去按切歌
  assert.match(line, /我的最愛/);         // 它為什麼挑這首
  assert.match(line, /2\/3/);             // 還會接幾首
});

test("接滿之後要講出「有人點一首就會再接」，不然看起來像壞掉", () => {
  const line = autofillLine({ ...ON, streak: 3, stopped: true });
  assert.match(line, /安靜/);
  assert.match(line, /有人點一首/);
});

test("壞掉的狀態不該讓那一行變成 NaN 或 undefined", () => {
  for (const state of [undefined, {}, { enabled: true }, { enabled: true, stop_after: "壞" }]) {
    const line = autofillLine(state);
    assert.doesNotMatch(line, /NaN|undefined/);
  }
});

// --- 佇列空了的時候 ---

test("自動接歌開著時，空佇列要先講「等一下機器會自己接」", () => {
  const note = emptyQueueNote(ON);
  assert.match(note, /機器會自己接/);
  assert.match(note, /點歌隨時可以把它換掉/);
});

test("關著（或已經接滿停下來）時就是原本那句話", () => {
  assert.match(emptyQueueNote(OFF), /快搜尋並點播/);
  assert.match(emptyQueueNote({ ...ON, stopped: true }), /快搜尋並點播/);
});

// --- 🎲 來一首 ---

test("🎲 的通知要講出歌名與「為什麼是這首」", () => {
  const note = randomPickNote({ item: { title: "稻香" }, reason: "🌱 還沒唱過" });
  assert.match(note, /稻香/);
  assert.match(note, /還沒唱過/);
});

test("🎲 拿到殘缺的回應也要講得出一句話", () => {
  assert.doesNotMatch(randomPickNote({}), /undefined/);
  assert.doesNotMatch(randomPickNote(null), /undefined/);
});

test("曲庫是空的要講出下一步，不能只說「不行」", () => {
  assert.match(emptyLibraryNote(), /先搜尋點一首/);
});
