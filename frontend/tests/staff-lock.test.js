/**
 * 櫃檯管理鎖的說法（node --test）
 *
 * 守的是「畫面不能騙人」：鎖頭要照**這台裝置**的狀態畫（櫃檯解鎖了，
 * 包廂裡的手機還是鎖著的），被擋下來要說得出是哪一個動作、怎麼過去，
 * 以及四種解不開的原因要分開講 —— 揉成一句「解鎖失敗」的話，
 * 罰站中的人會繼續猜（每猜一次罰更久），而密碼檔壞掉的人會一直確認
 * 自己有沒有記錯，他的密碼從頭到尾都是對的。
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const SL = require("../js/staff-lock.js");

test("PIN 只收數字，最多八碼", () => {
  assert.equal(SL.staffPinDigits("12a4"), "124");
  assert.equal(SL.staffPinDigits("123456789"), "12345678");
  assert.equal(SL.staffPinDigits(null), "");
});

test("按鍵與退格", () => {
  assert.equal(SL.staffPinPress("123", "4"), "1234");
  assert.equal(SL.staffPinPress("12345678", "9"), "12345678");  // 滿了原地不動
  assert.equal(SL.staffPinPress("12", "ab"), "12");
  assert.equal(SL.staffPinErase("1234"), "123");
  assert.equal(SL.staffPinErase(""), "");                        // 空的按退格不該壞掉
});

test("四碼才送得出去", () => {
  assert.equal(SL.staffPinComplete("123"), false);
  assert.equal(SL.staffPinComplete("1234"), true);
});

test("密碼顯示成 ●○ 而不是數字", () => {
  assert.equal(SL.staffPinMask(""), "○ ○ ○ ○");
  assert.equal(SL.staffPinMask("12"), "● ● ○ ○");
  assert.equal(SL.staffPinMask("123456"), "● ● ● ● ● ●");
  assert.ok(!SL.staffPinMask("1234").includes("1"));
});

test("沒設密碼時鎖頭整顆不出現", () => {
  const badge = SL.lockBadge({ enabled: false }, false);
  assert.equal(badge.show, false);
});

test("鎖頭畫的是這台裝置的狀態，不是整台機器的", () => {
  // 櫃檯的平板解開了（伺服器 locked=false），但這支手機手上沒有 token：
  // 它按下去照樣會被擋，所以畫面不能說「已解鎖」。
  const phone = SL.lockBadge({ enabled: true, locked: false }, false);
  assert.equal(phone.icon, "🔒");
  const counter = SL.lockBadge({ enabled: true, locked: false, unlock_seconds_left: 600 }, true);
  assert.equal(counter.icon, "🔓");
  assert.match(counter.title, /自動上鎖/);
});

test("密碼檔壞掉的鎖頭跟一般上鎖長得不一樣", () => {
  const badge = SL.lockBadge({ enabled: true, locked: true, broken: true }, false);
  assert.equal(badge.tone, "error");
  assert.match(badge.title, /救援|重設/);
});

test("自動上鎖倒數：不到一分鐘講秒", () => {
  assert.match(SL.autoLockText(45), /45 秒/);
  assert.match(SL.autoLockText(600), /10 分鐘/);
  assert.match(SL.autoLockText(0), /自動上鎖/);
  assert.match(SL.autoLockText(-5), /自動上鎖/);
});

test("被擋下來要說出哪一個動作，以及怎麼過去", () => {
  const msg = SL.blockedMessage({ action: "刪除曲庫歌曲", code: "staff_locked" });
  assert.match(msg, /刪除曲庫歌曲/);
  assert.match(msg, /櫃檯密碼/);          // 沒有下一步的錯誤訊息等於沒有訊息
  assert.match(SL.blockedMessage(null), /這個動作/);
});

test("解不開的四種原因分開講", () => {
  assert.equal(SL.unlockMessage({ status: "success" }).tone, "ok");
  assert.match(SL.unlockMessage({ status: "cooldown", retry_after: 15 }).text, /15 秒/);
  assert.match(SL.unlockMessage({ status: "denied", attempts_left: 2 }).text, /再錯 2 次/);
  assert.match(SL.unlockMessage({ status: "denied", broken: true }).text, /KARATUBE_STAFF_PIN/);
  assert.match(SL.unlockMessage({ status: "not_enabled" }).text, /沒有設/);
});

test("忘記密碼的救援說明兩條路都講得出來", () => {
  const withEnv = SL.recoveryHint({ env_pin_set: true, env_var: "KARATUBE_STAFF_PIN",
                                    file: "cache/staff_lock.json" });
  assert.match(withEnv, /已經設了救援密碼/);
  const without = SL.recoveryHint({ env_pin_set: false });
  assert.match(without, /KARATUBE_STAFF_PIN/);
  assert.match(without, /staff_lock\.json/);
  assert.match(without, /碰得到伺服器/);   // 這把鎖真正的安全邊界
});

test("摘要同時講「鎖了什麼」與「沒鎖什麼」", () => {
  const on = SL.scopeSummary({ enabled: true });
  assert.match(on, /刪曲庫/);
  // 只講鎖了什麼的話，店家會以為客人連歌都點不了而不敢開
  assert.match(on, /不受影響/);
  assert.match(on, /點歌/);
  assert.match(SL.scopeSummary({ enabled: false }), /沒有啟用/);
});

test("PIN 長度跟後端釘在一起", () => {
  const py = fs.readFileSync(path.join(__dirname, "..", "..", "backend", "services",
                                       "staff_lock.py"), "utf8");
  const min = Number(/MIN_PIN_DIGITS = (\d+)/.exec(py)[1]);
  const max = Number(/MAX_PIN_DIGITS = (\d+)/.exec(py)[1]);
  assert.equal(SL.STAFF_PIN_MIN_DIGITS, min);
  assert.equal(SL.STAFF_PIN_MAX_DIGITS, max);
});

test("沒有一個說法會吐出 undefined", () => {
  for (const state of [undefined, {}, { enabled: true }, { enabled: true, locked: true }]) {
    assert.ok(!String(SL.scopeSummary(state)).includes("undefined"));
    assert.ok(!String(SL.recoveryHint(state)).includes("undefined"));
    assert.ok(!String(SL.lockBadge(state, false).title).includes("undefined"));
  }
  assert.ok(!SL.unlockMessage({}).text.includes("undefined"));
});
