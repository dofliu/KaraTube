/**
 * 錄唱回放判斷規則的前端單元測試（node --test）。
 *
 * 這一層錯掉的後果是「唱完一首，錄音沒了」—— 事後補不回來，
 * 而且使用者不會知道是被規則擋掉還是壞了。所以每一道門都要有測試釘著，
 * 尤其是那句「為什麼沒留」的說明。
 */
const test = require("node:test");
const assert = require("node:assert/strict");

const {
  TAKE_MIME_CANDIDATES,
  pickTakeMime,
  shouldKeepTake,
  formatTakeDuration,
  formatTakeSize,
  quotaSummary,
  quotaWarning,
  takeSubtitle,
} = require("../js/take-rules.js");

// --- 容器挑選 ---

test("Chrome：挑到第一個支援的 webm/opus", () => {
  const chrome = (m) => m.startsWith("audio/webm");
  assert.equal(pickTakeMime(chrome), "audio/webm;codecs=opus");
});

test("Safari：webm 都不支援時退到 mp4", () => {
  const safari = (m) => m === "audio/mp4";
  assert.equal(pickTakeMime(safari), "audio/mp4");
});

test("都不支援就回空字串（呼叫端據此整個不錄）", () => {
  assert.equal(pickTakeMime(() => false), "");
  assert.equal(pickTakeMime(undefined), "");
});

test("isTypeSupported 丟例外要當成不支援，不能整支炸掉", () => {
  const rude = (m) => {
    if (m.includes("opus")) throw new Error("舊瀏覽器");
    return m === "audio/webm";
  };
  assert.equal(pickTakeMime(rude), "audio/webm");
});

test("候選清單以 Opus 優先（同樣長度下檔案最小）", () => {
  assert.ok(TAKE_MIME_CANDIDATES[0].includes("opus"));
});

// --- 要不要留 ---

function take(extra = {}) {
  return Object.assign({
    enabled: true, supported: true, bytes: 1_000_000,
    voicedMs: 60_000, minSingMs: 10_000,
  }, extra);
}

test("正常唱完一首要留下來", () => {
  assert.equal(shouldKeepTake(take()).keep, true);
});

test("功能沒開、瀏覽器不支援、檔案是空的都不留", () => {
  assert.equal(shouldKeepTake(take({ enabled: false })).keep, false);
  assert.equal(shouldKeepTake(take({ supported: false })).keep, false);
  assert.equal(shouldKeepTake(take({ bytes: 0 })).keep, false);
});

test("沒有人真的開口唱就不留（麥克風擺在桌上、前奏就被切歌）", () => {
  const verdict = shouldKeepTake(take({ voicedMs: 3_000 }));
  assert.equal(verdict.keep, false);
  // 說明要講得出「唱了幾秒／要幾秒」，不然使用者只會覺得功能壞了
  assert.match(verdict.reason, /3 秒/);
  assert.match(verdict.reason, /10 秒/);
});

test("門檻設成 0 表示照單全收（除了空檔案）", () => {
  assert.equal(shouldKeepTake(take({ voicedMs: 0, minSingMs: 0 })).keep, true);
  assert.equal(shouldKeepTake(take({ voicedMs: 0, minSingMs: 0, bytes: 0 })).keep, false);
});

test("剛好等於門檻要留（邊界是「夠了」不是「差一點」）", () => {
  assert.equal(shouldKeepTake(take({ voicedMs: 10_000, minSingMs: 10_000 })).keep, true);
});

test("缺欄位或整包 undefined 都當成不留，不丟例外", () => {
  assert.equal(shouldKeepTake(undefined).keep, false);
  assert.equal(shouldKeepTake({}).keep, false);
});

// --- 格式化 ---

test("長度寫成 m:ss，超過一小時才出現小時位", () => {
  assert.equal(formatTakeDuration(204_000), "3:24");
  assert.equal(formatTakeDuration(9_000), "0:09");
  assert.equal(formatTakeDuration(3_723_000), "1:02:03");
  assert.equal(formatTakeDuration(0), "0:00");
  assert.equal(formatTakeDuration(undefined), "0:00");
});

test("大小：KB 不寫小數、MB 寫一位（配額快滿時差很多）", () => {
  assert.equal(formatTakeSize(512), "512 B");
  assert.equal(formatTakeSize(2048), "2 KB");
  assert.equal(formatTakeSize(3.5 * 1024 * 1024), "3.5 MB");
  assert.equal(formatTakeSize(-5), "0 B");
});

test("配額摘要：上限 0 時不講上限（「12 / 0 首」會被讀成超標）", () => {
  const summary = quotaSummary({ count: 12, max_count: 50,
                                 total_bytes: 300 * 1024 * 1024,
                                 max_bytes: 512 * 1024 * 1024, pinned_count: 2 });
  assert.match(summary, /12 \/ 50 首/);
  assert.match(summary, /300\.0 MB \/ 512\.0 MB/);
  assert.match(summary, /2 筆保留中/);

  const unlimited = quotaSummary({ count: 3, max_count: 0, total_bytes: 1024, max_bytes: 0 });
  assert.equal(unlimited.includes("/"), false);
});

// --- 配額提醒 ---

test("九成才提醒：滿了才講已經來不及（下一首就會刪掉一筆）", () => {
  const at = (count, max) => quotaWarning({ count, max_count: max });
  assert.equal(at(80, 100), "");         // 八成：還不必吵
  assert.notEqual(at(90, 100), "");      // 九成：開始提醒
  assert.notEqual(at(100, 100), "");
});

test("兩道上限取比較緊的那一道", () => {
  // 筆數還很空，但容量已經九成五 —— 一樣要提醒
  const warning = quotaWarning({
    count: 5, max_count: 100,
    total_bytes: 95 * 1024 * 1024, max_bytes: 100 * 1024 * 1024,
  });
  assert.notEqual(warning, "");
});

test("全部標記保留時，提醒要換成「新的存不進來」", () => {
  const warning = quotaWarning({ count: 50, max_count: 50, pinned_count: 50 });
  assert.match(warning, /存不進來/);
});

test("沒有上限就沒有提醒", () => {
  assert.equal(quotaWarning({ count: 9999, max_count: 0, max_bytes: 0 }), "");
  assert.equal(quotaWarning(undefined), "");
});

// --- 清單副標題 ---

test("副標題把唱的人、分數、長度、大小串成一行", () => {
  const line = takeSubtitle({ singer: "阿明", score: 88000, grade: "A",
                              duration_ms: 204_000, bytes: 2 * 1024 * 1024 });
  assert.equal(line, "阿明・88,000 分 A・3:24・2.0 MB");
});

test("沒有名字、沒有分數時不留下空欄位", () => {
  const line = takeSubtitle({ bytes: 1024, duration_ms: 0 });
  assert.equal(line, "1 KB");
});

test("對唱的錄音要標出來（同一個檔案裡有兩個人）", () => {
  const line = takeSubtitle({ singer: "A 麥 & B 麥", mode: "duet", bytes: 1024 });
  assert.match(line, /對唱/);
});
