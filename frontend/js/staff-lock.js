/**
 * 櫃檯管理鎖的說法與狀態（純邏輯，沒有 DOM）。
 *
 * 伺服器負責「這個動作要不要密碼」（backend/services/access_policy.py），
 * 這一支負責**畫面上要說什麼** —— 而這個功能說錯話的代價很具體：
 * 客人按下一顆按鈕、跳出一個他看不懂的錯誤，然後他會再按五次。
 *
 * 三個刻意的決定：
 *
 * 1. **鎖頭顯示的是「這台裝置」的狀態，不是整台機器的。** 解鎖是綁 token 的
 *    （櫃檯的平板拿到，包廂裡的手機沒有），所以伺服器說「現在是解鎖狀態」
 *    的時候，沒有 token 的那幾支手機照樣按不動。它們的鎖頭必須畫成鎖著 ——
 *    不然使用者會看著一個「已解鎖」的畫面，按下去卻被擋，而畫面沒有騙人的權利。
 *
 * 2. **被擋下來要說出「哪一個動作」與「怎麼過去」。** 伺服器回的 403 裡帶著
 *    動作的名字（「刪除曲庫歌曲」），照抄就好；重要的是後半句永遠是
 *    「點右上角 🔒 輸入櫃檯密碼」—— 沒有下一步的錯誤訊息等於沒有訊息。
 *
 * 3. **「忘記密碼」的說明只在需要的時候出現，但一定出現得來。** 那段話
 *    （環境變數 / 刪檔案）在正常情況下是雜訊，在半夜的店裡是唯一有用的資訊。
 */

// 跟 backend/services/staff_lock.py 的 MIN/MAX_PIN_DIGITS 對齊
// （frontend/tests/staff-lock.test.js 有一條測試把兩邊釘在一起）。
const STAFF_PIN_MIN_DIGITS = 4;
const STAFF_PIN_MAX_DIGITS = 8;

/** 只留數字。使用者可能用實體鍵盤打字，貼上來的東西什麼都有。 */
function staffPinDigits(text) {
  return String(text == null ? "" : text).replace(/\D/g, "").slice(0, STAFF_PIN_MAX_DIGITS);
}

/** 按一個數字鍵。已經打滿上限就原地不動（而不是把前面的字擠掉）。 */
function staffPinPress(pin, digit) {
  const d = String(digit == null ? "" : digit).replace(/\D/g, "");
  if (d.length !== 1) return staffPinDigits(pin);
  const next = staffPinDigits(pin) + d;
  return next.length > STAFF_PIN_MAX_DIGITS ? staffPinDigits(pin) : next;
}

/** 退一格。空的時候按退格不該變成 undefined。 */
function staffPinErase(pin) {
  return staffPinDigits(pin).slice(0, -1);
}

/** 打夠了嗎（可以送出了）。 */
function staffPinComplete(pin) {
  return staffPinDigits(pin).length >= STAFF_PIN_MIN_DIGITS;
}

/**
 * 密碼的顯示字串：打過的用 ●，還沒打的用 ○（最少畫四格）。
 *
 * 不顯示數字本身是刻意的 —— 櫃檯輸入密碼的時候，包廂裡有十個人看著那面
 * 大螢幕，而點歌台的畫面常常就鏡射在上面。
 */
function staffPinMask(pin) {
  const digits = staffPinDigits(pin);
  const slots = Math.max(STAFF_PIN_MIN_DIGITS, digits.length);
  const out = [];
  for (let i = 0; i < slots; i++) out.push(i < digits.length ? "●" : "○");
  return out.join(" ");
}

/**
 * 右上角那顆鎖頭要長什麼樣子。
 *
 * `hasToken` 是**這台裝置**有沒有解鎖用的 token（見檔頭第 1 點）：
 * 伺服器的 `locked` 是整台機器的狀態，兩個都要看才畫得對。
 */
function lockBadge(state, hasToken) {
  const s = state || {};
  if (!s.enabled) {
    return { show: false, icon: "🔓", text: "未設密碼", tone: "off",
             title: "櫃檯管理鎖沒有啟用：這台機器上每個人都改得了設定與曲庫" };
  }
  if (s.broken) {
    return { show: true, icon: "🔧", text: "密碼檔損毀", tone: "error",
             title: "密碼檔讀不出來，機器層級的動作維持上鎖。請用救援密碼或重設密碼檔" };
  }
  if (hasToken && !s.locked) {
    const left = Math.max(0, Number(s.unlock_seconds_left) || 0);
    return { show: true, icon: "🔓", text: "櫃檯已解鎖", tone: "open",
             title: `機器層級的動作現在可以做。${autoLockText(left)}` };
  }
  return { show: true, icon: "🔒", text: "櫃檯管理", tone: "locked",
           title: "刪曲庫、改設定、包廂計時等機器層級的動作需要櫃檯密碼。點歌與唱歌不受影響" };
}

/** 「再過多久自動上鎖」。剩不到一分鐘就講秒，免得一直顯示「1 分鐘」。 */
function autoLockText(secondsLeft) {
  const left = Math.max(0, Math.floor(Number(secondsLeft) || 0));
  if (left <= 0) return "閒置一段時間後會自動上鎖";
  if (left < 60) return `閒置 ${left} 秒後自動上鎖`;
  return `閒置 ${Math.round(left / 60)} 分鐘後自動上鎖`;
}

/**
 * 被擋下來的時候那句話。`body` 是伺服器回的 403 內容。
 *
 * 動作名稱照抄伺服器的（那張表才知道「這條路叫什麼」），
 * 但後半句「怎麼過去」一定要補上。
 */
function blockedMessage(body) {
  const action = (body && body.action) || "這個動作";
  return `${action}需要櫃檯解鎖 —— 點右上角 🔒 輸入櫃檯密碼`;
}

/**
 * 解鎖結果要說什麼。四種情況分開講：
 * 成功、密碼錯（還剩幾次）、罰站中（等幾秒）、密碼檔壞掉（只有救援密碼有效）。
 *
 * 揉成一句「解鎖失敗」的話，罰站中的人會繼續猜密碼（每猜一次罰更久），
 * 而密碼檔壞掉的人會一直確認自己有沒有記錯 —— 他的密碼從頭到尾都是對的。
 */
function unlockMessage(result) {
  const r = result || {};
  if (r.status === "success") return { tone: "ok", text: "已解鎖" };
  if (r.status === "not_enabled") return { tone: "ok", text: "這台機器沒有設櫃檯密碼" };
  if (r.status === "cooldown") {
    const wait = Math.max(1, Number(r.retry_after) || 1);
    return { tone: "error", text: `密碼錯太多次，請等 ${wait} 秒再試` };
  }
  if (r.broken) {
    return { tone: "error",
             text: `密碼檔讀不出來，只有環境變數 ${r.env_var || "KARATUBE_STAFF_PIN"} 的救援密碼有效` };
  }
  const left = Number(r.attempts_left);
  if (Number.isFinite(left) && left > 0) {
    return { tone: "error", text: `密碼錯誤，再錯 ${left} 次就要罰站` };
  }
  return { tone: "error", text: r.message || "密碼錯誤" };
}

/**
 * 設定頁那段「忘記密碼怎麼辦」。
 *
 * 沒有救援手段的鎖等於把機器變成磚頭，而這台機器在店裡、半夜、客人在等。
 * 兩條路都需要實體碰得到伺服器 —— 那正是這把鎖真正的安全邊界，
 * 所以這段話不該藏起來，它本身就是設計的一部分。
 */
function recoveryHint(state) {
  const s = state || {};
  const envVar = s.env_var || "KARATUBE_STAFF_PIN";
  const file = s.file || "cache/staff_lock.json";
  const lead = s.env_pin_set
    ? `這台伺服器已經設了救援密碼（環境變數 ${envVar}），忘記密碼時用它解鎖。`
    : `忘記密碼時，在伺服器上設環境變數 ${envVar}=新密碼 後重開，用它解鎖。`;
  return `${lead}或是刪掉 ${file} 這個檔案（會一併清掉自動上鎖設定）。兩條都要碰得到伺服器本機 —— 這把鎖真正的安全邊界就是那台機器放在哪裡。`;
}

/**
 * 這把鎖現在保護著什麼（設定頁上那一行摘要）。
 *
 * 刻意同時講「鎖了什麼」與「沒鎖什麼」：只講前者的話，
 * 店家會以為包廂裡的客人連歌都點不了而不敢開。
 */
function scopeSummary(state) {
  const s = state || {};
  if (!s.enabled) {
    return "目前沒有啟用：掃到 QR 的每一支手機都可以刪曲庫、改設定、清排行。放在店裡請設一組密碼。";
  }
  return "已保護：刪曲庫／重新處理、系統設定、排程預處理、清空排行與歷史、清空所有錄音、包廂計時、舞台訊息、重設輪唱。不受影響：點歌、切歌、重唱、調音量與效果、評分、收藏、查歌、自己那一次錄音。";
}

if (typeof window !== "undefined") {
  window.StaffLockView = {
    STAFF_PIN_MIN_DIGITS, STAFF_PIN_MAX_DIGITS,
    staffPinDigits, staffPinPress, staffPinErase, staffPinComplete, staffPinMask,
    lockBadge, autoLockText, blockedMessage, unlockMessage, recoveryHint, scopeSummary,
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    STAFF_PIN_MIN_DIGITS, STAFF_PIN_MAX_DIGITS,
    staffPinDigits, staffPinPress, staffPinErase, staffPinComplete, staffPinMask,
    lockBadge, autoLockText, blockedMessage, unlockMessage, recoveryHint, scopeSummary,
  };
}
