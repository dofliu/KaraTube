/**
 * 分享頁顯示邏輯的單元測試（node --test）
 *
 * 這一頁是給**客人**看的：唱完的人把連結傳給朋友，朋友在手機上點開。
 * 所以這裡測的都是「講錯話」的情形 —— 還有 -3 小時、已過期卻寫著還有 0 分鐘、
 * 沒評分卻寫著 0 分（會被讀成唱得很爛）。這些錯誤在後台看不到，
 * 只有真的把分享頁打開才會發現，而那時候連結已經傳出去了。
 */
const test = require("node:test");
const assert = require("node:assert/strict");

const {
  shareTokenFromLocation, expiryPhrase, expiryIsUrgent,
  shareMetaLine, shareScoreLine, shareErrorMessage,
} = require("../js/share-view.js");

// --- 從網址找 token ---

test("正規的 /share/<token> 網址", () => {
  const token = "abcdefghijklmnop12345";
  assert.equal(shareTokenFromLocation({ pathname: `/share/${token}` }), token);
  // 有些通訊軟體會在後面接參數或斜線
  assert.equal(shareTokenFromLocation({ pathname: `/share/${token}/` }), token);
  assert.equal(shareTokenFromLocation({ pathname: `/share/${token}`, search: "?from=line" }), token);
});

test("?t= 的退路（有些軟體會把路徑最後一段吃掉）", () => {
  const token = "abcdefghijklmnop12345";
  assert.equal(shareTokenFromLocation({ pathname: "/share/", search: `?t=${token}` }), token);
});

test("形狀不對的 token 一律當成沒有，不必多打一次一定會失敗的請求", () => {
  for (const loc of [
    { pathname: "/share/" },
    { pathname: "/share/short" },
    { pathname: "/share/../../etc/passwd" },
    { pathname: "/share/has space here 123456" },
    { pathname: "/" },
    {},
  ]) {
    assert.equal(shareTokenFromLocation(loc), "", JSON.stringify(loc));
  }
});

test("壞掉的 percent-encoding 不會讓整頁炸掉", () => {
  assert.equal(shareTokenFromLocation({ pathname: "/share/%E0%A4%A" }), "");
});

// --- 還能聽多久 ---

test("倒數用人話講，而且永遠不會出現負數", () => {
  assert.equal(expiryPhrase(23 * 3600), "還有 23 小時就過期");
  assert.equal(expiryPhrase(90 * 60), "還有 1 小時就過期");
  assert.equal(expiryPhrase(45 * 60), "還有 45 分鐘就過期");
  assert.equal(expiryPhrase(20), "剩不到 1 分鐘就過期");
  assert.equal(expiryPhrase(0), "這個連結已經過期");
  // 負數是最容易漏的：伺服器回的秒數與手機的時鐘不會完全一致
  assert.equal(expiryPhrase(-500), "這個連結已經過期");
  assert.equal(expiryPhrase(undefined), "這個連結已經過期");
});

test("超過兩天改用天數（「還有 71 小時」沒有人在心算）", () => {
  assert.equal(expiryPhrase(72 * 3600), "還有 3 天就過期");
});

test("剩不到一小時才算緊急；已經過期的不算緊急（那是另一句話）", () => {
  assert.equal(expiryIsUrgent(59 * 60), true);
  assert.equal(expiryIsUrgent(2 * 3600), false);
  assert.equal(expiryIsUrgent(0), false);
  assert.equal(expiryIsUrgent(-10), false);
});

// --- 卡片上的字 ---

test("副標題把有的欄位串起來，沒有的不留下空位", () => {
  const line = shareMetaLine({
    singer: "阿明", artist: "周杰倫", duration_ms: 204_000,
    created_at: "2026-09-13T21:30:45",
  });
  assert.equal(line, "阿明・周杰倫・3:24・2026-09-13 21:30");

  // 匿名（沒設暱稱）唱的那一次不該出現「・・」這種空位
  assert.equal(shareMetaLine({ artist: "周杰倫" }), "周杰倫");
  assert.equal(shareMetaLine({}), "");
  assert.equal(shareMetaLine(null), "");
});

test("對唱的那一次要講出來", () => {
  assert.match(shareMetaLine({ singer: "阿明", mode: "duet" }), /對唱/);
});

test("沒有分數就整行不出現 —— 「0 分」會被讀成唱得很爛", () => {
  assert.equal(shareScoreLine({ score: 0, grade: "" }), "");
  assert.equal(shareScoreLine({}), "");
  assert.equal(shareScoreLine({ score: 92_500, grade: "S", accuracy: 0.83 }),
               "92,500 分・S・音準 83%");
  // 有分數但沒開音準統計時，只講分數
  assert.equal(shareScoreLine({ score: 88_000 }), "88,000 分");
});

// --- 打不開的時候 ---

test("伺服器講得比前端清楚，detail 優先", () => {
  assert.equal(shareErrorMessage(410, "這個分享連結已經過期了，請原點歌的人重新分享"),
               "這個分享連結已經過期了，請原點歌的人重新分享");
});

test("沒有 detail 時照狀態碼講，而且分得出「網址打錯」與「已失效」", () => {
  assert.match(shareErrorMessage(404, ""), /不存在/);
  assert.match(shareErrorMessage(410, ""), /失效/);
  assert.match(shareErrorMessage(403, ""), /沒有開放/);
});

test("連不到伺服器時要講出網路 —— 這是分享連結最常見的失敗", () => {
  // 人已經離開那個網路了。講「載入失敗」會讓他一直重整手機
  assert.match(shareErrorMessage(0, ""), /網路/);
});
