/**
 * 整晚打包的顯示邏輯 (Night Export View)
 *
 * 後端把錄音切成一場一場（相隔超過幾小時就算換一場，見
 * backend/services/night_export.py），這一支負責把那幾筆資料翻成畫面上
 * 那一行字：這是哪一場、有幾首、多大、誰唱的。不碰 DOM、不發請求。
 *
 * 抽出來測的理由是這些字**只有在真的收場的時候**才會被看到，而那時候
 * 使用者要做的是一個判斷：「這一場是不是我剛剛唱的那一場？」寫錯日期、
 * 把跨午夜的那一場寫成兩場、或把 186 MB 寫成 186 B，都會讓他按錯或不敢按。
 */

// 檔案大小與時間的寫法沿用錄唱回放那一套 —— 同一份錄音的大小在清單上與
// 打包列上寫成兩種樣子是最沒必要的分岔。瀏覽器裡 take-rules.js 先載入
// （見 index.html 的順序），node 測試走 require。
const NIGHT_TAKE_RULES = (typeof window !== "undefined" && window.TakeRules)
  ? window.TakeRules
  : (typeof require === "function" ? require("./take-rules.js") : null);

// 幾個小時以內結束的那一場算「今晚」。8 小時的理由：一場唱到凌晨兩點半、
// 隔天中午才回來看清單的人，看到「今晚」會以為是**今天**晚上要唱的那一場。
const TONIGHT_WINDOW_MS = 8 * 3600 * 1000;

function parseWhen(text) {
  if (!text) return null;
  // 伺服器給的是本地時間的 ISO 字串（沒有時區後綴），瀏覽器照本地時間解讀 ——
  // 包廂那台機器跟手機在同一個房間裡，不必也不該扯到 UTC。
  const d = new Date(String(text));
  return Number.isNaN(d.getTime()) ? null : d;
}

function hhmm(date) {
  if (!date) return "--:--";
  return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

/**
 * 一場的標題：`今晚 21:05 – 隔天 02:30`。
 *
 * 「隔天」那兩個字是這個功能的重點之一：包廂的一場常常是跨午夜的，
 * 而畫面上只寫 `21:05 – 02:30` 會被讀成「倒過來了」或「打錯了」。
 */
function sessionLabel(session, now) {
  const s = session || {};
  const start = parseWhen(s.started_at);
  const end = parseWhen(s.ended_at);
  if (!start) return "這一場";

  const ref = now instanceof Date ? now : new Date();
  const fresh = end ? (ref.getTime() - end.getTime()) <= TONIGHT_WINDOW_MS : false;
  const day = `${start.getMonth() + 1}/${start.getDate()}`;
  const head = fresh ? "今晚" : day;

  if (!end) return `${head} ${hhmm(start)}`;
  const crossed = end.getDate() !== start.getDate() ||
    end.getMonth() !== start.getMonth() || end.getFullYear() !== start.getFullYear();
  return `${head} ${hhmm(start)} – ${crossed ? "隔天 " : ""}${hhmm(end)}`;
}

/** 一場的副標：`23 首・3 人・186.4 MB`。 */
function sessionSummary(session) {
  const s = session || {};
  const bits = [`${Number(s.count) || 0} 首`];
  const singers = Array.isArray(s.singers) ? s.singers.filter(Boolean) : [];
  if (singers.length) bits.push(`${singers.length} 人`);
  if (NIGHT_TAKE_RULES) bits.push(NIGHT_TAKE_RULES.formatTakeSize(s.bytes));
  return bits.join("・");
}

/**
 * 下載前要先講的那句話。
 *
 * 兩件事一定要先說，不然使用者會以為壞掉：**檔案很大**（一場可能三百 MB，
 * 在手機上按下去是會佔滿空間的），以及**進度條不會動**（邊打包邊送就算不出
 * 總長度，瀏覽器只會顯示已下載多少，沒有百分比）。
 */
function downloadHint(session) {
  const s = session || {};
  const size = NIGHT_TAKE_RULES ? NIGHT_TAKE_RULES.formatTakeSize(s.bytes) : "";
  return `整包大約 ${size}，邊打包邊下載（瀏覽器不會顯示百分比，不是卡住了）`;
}

/**
 * 「只要某個人的」選單。放的是這一場真的有唱的人 ——
 * 一桌八個人，不是每個人都想把另外七個人的版本一起帶走。
 */
function singerChoices(session) {
  const s = session || {};
  const singers = Array.isArray(s.singers) ? s.singers.filter(Boolean) : [];
  return [{ value: "", label: `全部（${Number(s.count) || 0} 首）` }]
    .concat(singers.map((name) => ({ value: name, label: name })));
}

/**
 * 有人正在打包時要顯示的那一行。同時只做一份是刻意的（一包幾百 MB，
 * 而那條網路正是舞台端串影片在走的），所以畫面要講得出「在忙什麼」。
 */
function busyNote(busy) {
  return busy ? "正在打包另一份，等它下載完再按（同時只做一份，免得舞台卡住）" : "";
}

/** 沒有任何場次時的那句話。 */
function emptyNote() {
  return "還沒有錄音可以打包 —— 唱完一首（唱夠久）就會出現在這裡。";
}

if (typeof window !== "undefined") {
  window.NightExport = {
    sessionLabel, sessionSummary, downloadHint, singerChoices, busyNote, emptyNote,
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    sessionLabel, sessionSummary, downloadHint, singerChoices, busyNote, emptyNote,
  };
}
