/**
 * 服務鈴的說法 (Service View)
 *
 * 單子本身在伺服器（backend/services/service_calls.py）。這一支回答的是
 * 畫面那三個問題：
 *
 *   現在這張單是什麼狀況？ （「等待中」跟「櫃檯收到了」一定要看得出差別）
 *   等多久了？             （這個數字要一直在畫面上，不是跳一下就消失的提示）
 *   這一列該不該變紅？     （櫃檯要一眼認出等最久的那一桌，不是最吵的那一桌）
 *
 * 時間的算法跟舞台訊息、包廂計時同一套：**不比對絕對時刻**。伺服器在快照裡
 * 已經算好 `waited_seconds`，畫面只要加上「收到快照之後過了幾秒」。
 * 手機的時鐘跟伺服器常常差好幾分鐘，比對絕對時刻的話，客人手上會看到
 * 「等待 -3 分鐘」這種數字。
 *
 * 客人那一端與櫃檯那一端載的是同一支：同一張單在兩個畫面上必須是同一個說法
 * —— 客人看到「等了 12 分鐘」而櫃檯看到「剛剛」的話，接下來那段對話
 * 不會有好結果。
 *
 * 純資料邏輯：不碰 DOM、不發網路請求。
 */

// 等超過幾分鐘，櫃檯那一列開始變色。5 分鐘是「一杯冰塊該到了」的尺度。
const SERVICE_WARN_MINUTES = 5;
// 等超過幾分鐘算「等很久了」。10 分鐘之後客人通常已經按第二次了。
const SERVICE_LATE_MINUTES = 10;

// 還開著的兩種狀態（跟後端 OPEN_STATUSES 對齊）。
const SERVICE_OPEN_STATUSES = ["WAITING", "ACKED"];

/** 秒數，取不到就 0。畫面上的每一個數字都從這裡出去，所以不回 NaN。 */
function serviceSeconds(value) {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? n : 0;
}

/**
 * 這張單等了幾秒（到「現在」為止）。
 *
 * `since` 是「收到這份快照之後過了幾秒」—— 不傳的話那個數字會停在收到的
 * 那一刻不動，而一個停住的等待時間看起來就像系統當掉了（那正是客人
 * 再按一次的理由）。
 */
function serviceWaitedSeconds(call, since = 0) {
  if (!call) return 0;
  return serviceSeconds(call.waited_seconds) + serviceSeconds(since);
}

/** 把秒數講成人話。刻意不顯示秒 —— 「等了 3 分鐘」比「等了 187 秒」好讀。 */
function serviceWaitLabel(seconds) {
  const total = Math.floor(serviceSeconds(seconds));
  if (total < 60) return "剛剛";
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes} 分鐘`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} 小時 ${rest} 分` : `${hours} 小時`;
}

/** 這張單還開著嗎。 */
function serviceIsOpen(call) {
  return !!call && SERVICE_OPEN_STATUSES.includes(String(call.status || ""));
}

/**
 * 這一列該不該變色。
 *
 * 「櫃檯收到了」之後降一級是刻意的：讓客人再按一次的從來不是等待本身，
 * 是**沒有回音**。已經有人應聲的單，等同樣久沒有那麼緊張 ——
 * 而把它跟沒人理的那一張塗成一樣的紅色，等於讓櫃檯分不出哪一張真的沒人管。
 */
function serviceUrgency(call, since = 0) {
  if (!serviceIsOpen(call)) return "closed";
  const minutes = serviceWaitedSeconds(call, since) / 60;
  const acked = String(call.status) === "ACKED";
  if (minutes >= SERVICE_LATE_MINUTES) return acked ? "warn" : "late";
  if (minutes >= SERVICE_WARN_MINUTES) return acked ? "calm" : "warn";
  return "calm";
}

/**
 * 狀態那幾個字。
 *
 * 「已送出」與「櫃檯收到了」一定要是兩句不同的話（見 service_calls.py 決定三）
 * —— 講成同一句的話，客人沒有辦法分辨「有人在弄」跟「這顆鍵沒作用」，
 * 而任何人在那個處境下都會再按一次。
 */
function serviceStatusLabel(call) {
  const status = String((call && call.status) || "");
  if (status === "WAITING") return "🔔 已送出，等櫃檯回應";
  if (status === "ACKED") return "👀 櫃檯收到了，正在處理";
  if (status === "DONE") return "✅ 已完成";
  if (status === "CANCELLED") return "↩️ 包廂自己取消";
  if (status === "EXPIRED") return "⌛ 太久沒有人處理（已自動關閉）";
  return "—";
}

/** 品項那幾個字（帶 emoji）。`spec` 是快照裡的 `items`。 */
function serviceItemsLabel(call, spec = []) {
  const keys = (call && Array.isArray(call.items)) ? call.items : [];
  if (keys.length === 0) return "";
  const byKey = new Map((Array.isArray(spec) ? spec : []).map((s) => [s.key, s]));
  return keys.map((key) => {
    const found = byKey.get(key);
    if (found) return `${found.emoji || ""}${found.label || key}`.trim();
    // 認不得的品項照樣列出來（伺服器換了一版、這支頁面是舊的）：
    // 少列一項會讓櫃檯少送一樣東西，而那比列出一個怪字串糟得多。
    return String(key);
  }).join("＋");
}

/**
 * 客人那支手機上，那張單的一句話。
 *
 * 這一句要**一直在畫面上**直到單子結案，不是跳出來三秒就消失的提示 ——
 * 這個功能的價值有一半在這個持續可見的狀態，不在那顆鍵
 * （見 service_calls.py 決定三）。
 */
function serviceRoomLine(call, spec = [], since = 0) {
  if (!serviceIsOpen(call)) return "";
  const items = serviceItemsLabel(call, spec);
  const waited = serviceWaitLabel(serviceWaitedSeconds(call, since));
  const note = String((call && call.note) || "").trim();
  const tail = note ? `（${note}）` : "";
  return `${serviceStatusLabel(call)}・${items}${tail}・已等 ${waited}`;
}

/**
 * 櫃檯那一列的一句話。
 *
 * 按了幾次要寫出來（見 service_calls.py 決定四）：那是「這桌等急了」的
 * 真實訊號，而它之所以值得寫出來，正是因為它**沒有**變成第二張單。
 */
function serviceDeskLine(call, spec = [], since = 0) {
  if (!call) return "";
  const items = serviceItemsLabel(call, spec);
  const waited = serviceWaitLabel(serviceWaitedSeconds(call, since));
  const note = String(call.note || "").trim();
  const who = String(call.by || "").trim();
  const presses = Math.max(1, Math.floor(Number(call.presses) || 1));
  const bits = [items || "服務"];
  if (note) bits.push(note);
  bits.push(`已等 ${waited}`);
  if (presses > 1) bits.push(`按了 ${presses} 次`);
  if (who) bits.push(`來自 ${who}`);
  return bits.join("・");
}

/** 紀錄清單上的一列（結案的單）。 */
function serviceHistoryLine(call, spec = []) {
  if (!call) return "";
  const items = serviceItemsLabel(call, spec) || "服務";
  const reply = String(call.reply || "").trim();
  const tail = reply ? `・回覆：${reply}` : "";
  return `${serviceStatusLabel(call)}・${items}${tail}`;
}

/**
 * 按下去之後回的那一句。
 *
 * 併單與新單一定要是兩句不同的話（見 service_calls.py 決定一、決定四）：
 * 第二次按下去得到跟第一次一模一樣的「已送出」，那個人會以為第一次根本
 * 沒送出去 —— 於是他會按第三次，或者推開門走出去。
 */
function serviceSentNote(call, spec = []) {
  if (!call) return "";
  const items = serviceItemsLabel(call, spec);
  if (call.merged) {
    const presses = Math.max(2, Math.floor(Number(call.presses) || 2));
    return `🔔 已經併進剛剛那一張單（${items}），櫃檯看得到你按了 ${presses} 次。`;
  }
  return `🔔 已送出：${items}。櫃檯收到之後這裡會變成「櫃檯收到了」。`;
}

/**
 * 按不下去（或做不了）時說的話。
 *
 * 跟額度、計時、舞台訊息同一個規矩：講規則，而且每一句「不行」後面都要有
 * 下一步。這裡特別要緊 —— 一顆按了沒反應的服務鈴，下一步是有人推開包廂的門。
 */
function serviceRejectNote(reason, detail = {}) {
  if (reason === "empty") {
    return "🔔 還沒選要什麼（送餐、加冰塊、麥克風……），選一項再按。";
  }
  if (reason === "disabled") {
    return "🔔 服務鈴目前關著，到系統設定頁的「🔔 服務鈴」打開。";
  }
  if (reason === "none_open") {
    return "🔔 現在沒有等待中的單（可能剛被處理掉了）。";
  }
  if (reason === "stale") {
    // 畫面停在舊的那一張。照著按下去會把**新的**那一張標成完成，
    // 而那件事根本還沒做 —— 所以擋下來，並且明講畫面是舊的。
    return "🔔 這張單已經結案了，現在櫃檯上是新的一張。畫面已更新，請重看一次。";
  }
  return "🔔 這個動作沒有完成。";
}

if (typeof window !== "undefined") {
  window.ServiceView = {
    SERVICE_WARN_MINUTES, SERVICE_LATE_MINUTES, SERVICE_OPEN_STATUSES,
    serviceSeconds, serviceWaitedSeconds, serviceWaitLabel, serviceIsOpen,
    serviceUrgency, serviceStatusLabel, serviceItemsLabel, serviceRoomLine,
    serviceDeskLine, serviceHistoryLine, serviceSentNote, serviceRejectNote,
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    SERVICE_WARN_MINUTES, SERVICE_LATE_MINUTES, SERVICE_OPEN_STATUSES,
    serviceSeconds, serviceWaitedSeconds, serviceWaitLabel, serviceIsOpen,
    serviceUrgency, serviceStatusLabel, serviceItemsLabel, serviceRoomLine,
    serviceDeskLine, serviceHistoryLine, serviceSentNote, serviceRejectNote,
  };
}
