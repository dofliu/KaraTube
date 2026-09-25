/**
 * 本機曲庫匯入那一頁的說法（純邏輯，沒有 DOM、不發請求）。
 *
 * 抽出來是因為這一頁的錯誤幾乎都不會拋例外，只會讓畫面**安靜地騙人**：
 * 把「已經在曲庫裡」的歌顯示成可以匯入（使用者按下去，等了十分鐘，
 * 得到一首本來就有的歌）、或是掃到 0 個檔案時只印一句「沒有檔案」
 * （使用者完全不知道那個資料夾在哪、該放什麼進去）。
 *
 * 所以這裡的重點全部在**下一步是什麼**：每一種狀態都要講得出使用者現在
 * 該做什麼，而不只是報告機器看到了什麼。
 */

// 檔案狀態 → 徽章。tone 沿用專案既有的四種語意（ok/warn/bad/muted）。
const FILE_STATE = {
  new: { label: "可匯入", cls: "ok" },
  imported: { label: "已在曲庫", cls: "muted" },
  queued: { label: "處理中", cls: "warn" },
  failed: { label: "上次失敗", cls: "bad" },
};

// 只有這兩種狀態勾得起來。「已在曲庫」再匯入一次只是白跑一趟十分鐘的流水線；
// 「處理中」再排一次會在同一批裡出現兩筆同樣的歌。
const SELECTABLE = new Set(["new", "failed"]);

function fileStateMeta(state) {
  return FILE_STATE[state] || FILE_STATE.new;
}

function canSelect(file) {
  return !!file && SELECTABLE.has(file.state);
}

/** 預設勾哪些：所有沒匯入過的。失敗過的**不**預設勾 —— 它上次失敗是有原因的，
 *  使用者應該先看一眼那個原因（多半是檔案壞了，再跑一次還是會失敗）。 */
function defaultSelection(files) {
  return (Array.isArray(files) ? files : [])
    .filter(f => f && f.state === "new")
    .map(f => f.path);
}

function formatSize(bytes) {
  const n = Number(bytes) || 0;
  if (n >= 1024 * 1024 * 1024) return `${(n / (1024 * 1024 * 1024)).toFixed(1)} GB`;
  if (n >= 1024 * 1024) return `${Math.round(n / (1024 * 1024))} MB`;
  return `${Math.max(1, Math.round(n / 1024))} KB`;
}

/**
 * 一列右邊那串小字。
 *
 * 「有沒有 .lrc」一定要出現：它是這條路上最重要的歌詞來源，而使用者只有在
 * **匯入之前**才有機會補放那個檔案（匯入完成後要改就得重新處理一次）。
 */
function fileDetail(file) {
  const f = file || {};
  const parts = [f.kind === "audio" ? "🎵 純音檔（會用情境背景）" : "🎬 影片", formatSize(f.size_bytes)];
  parts.push(f.has_lrc ? "📄 已附 .lrc" : "📄 無 .lrc（會上網找）");
  if (f.note) parts.push(f.note);
  return parts.filter(Boolean).join(" ・ ");
}

/**
 * 「曲庫裡好像已經有同名的歌」的提醒。
 *
 * 只提醒、不擋：同名的歌真的存在（不同歌手、不同版本），而使用者手上這個
 * 檔案很可能正是他想拿來取代那份爛畫質的原因。
 */
function duplicateWarning(file) {
  if (!file || !file.duplicate_of || file.state !== "new") return "";
  return "⚠️ 曲庫裡已經有同名的歌，確定要再加一首嗎？";
}

/** 分頁標題列那一行摘要。 */
function summaryText(scan) {
  const c = (scan && scan.counts) || {};
  if (!c.total) return "匯入資料夾是空的";
  const bits = [`${c.total} 個檔案`];
  if (c.new) bits.push(`🆕 可匯入 ${c.new}`);
  if (c.queued) bits.push(`⚙️ 處理中 ${c.queued}`);
  if (c.failed) bits.push(`⚠️ 失敗 ${c.failed}`);
  if (c.imported) bits.push(`✅ 已在曲庫 ${c.imported}`);
  return bits.join(" ・ ");
}

/**
 * 畫面最上面那段話：現在該做什麼。
 *
 * 四種情況要講四句不同的話 —— 講同一句（「沒有可匯入的檔案」）的話，
 * 「我還沒放檔案」跟「我放的檔案機器不認得」在畫面上長得一模一樣，
 * 而這兩者的下一步完全不同。
 */
function guidanceText(scan) {
  const s = scan || {};
  const c = s.counts || {};
  const skipped = Array.isArray(s.skipped) ? s.skipped.length : 0;
  if (!s.exists) {
    return `匯入資料夾還沒建立：${s.root || "cache/import"}（機器沒有寫入權限？）`;
  }
  if (!c.total && skipped) {
    return `這個資料夾裡有 ${skipped} 個檔案，但沒有一個是支援的影音格式。` +
           "支援的格式列在資料夾裡的說明檔裡。";
  }
  if (!c.total) {
    return `把你自己的伴唱影片或音檔複製到 ${s.root || "cache/import"}，` +
           "再回來按「重新掃描」。建議檔名取成「歌手 - 歌名.mp4」，" +
           "同名的 .lrc 放旁邊就會直接拿來當歌詞。";
  }
  if (!c.new && c.imported === c.total) {
    return "這個資料夾裡的歌全部都已經在曲庫裡了 ——" +
           "原始檔案不會被刪掉，你可以自己清掉它們，也可以留著當備份。";
  }
  return "勾選要匯入的檔案，歌名與歌手可以先改（處理完之後歌號與查歌索引就" +
         "建在那串字上，改起來會貴很多）。匯入是複製，你的原始檔案不會被動到。";
}

/**
 * 被略過的檔案那一區的標題。
 *
 * 不能寫死「不是支援的影音格式」—— 被略過的理由不只一種（太小、讀不到），
 * 而使用者看到的是一個他確定放進去了的檔案配上一個錯的理由。
 * 標題只講數量，理由逐檔列在裡面。
 */
function skippedSummary(scan) {
  const list = (scan && scan.skipped) || [];
  if (!list.length) return "";
  return `有 ${list.length} 個檔案被略過（展開看原因）`;
}

/** 截斷提示。靜悄悄只列前 N 個會讓使用者以為剩下的檔案機器讀不到。 */
function truncationNote(scan) {
  const s = scan || {};
  if (!s.truncated) return "";
  return `⚠️ 檔案太多，這次只列出前 ${s.max_files || 0} 個。` +
         "先匯入這一批，處理完它們會變成「已在曲庫」，再掃描就會輪到後面的。";
}

/** 送出前的確認文字。要講滿「幾首、跑多久、跑完之後在哪裡找」。 */
function confirmText(count, startNow) {
  const when = startNow
    ? "馬上開始處理（有人唱歌時會自動讓開）"
    : "排進處理佇列，等排程時段（預設半夜 2 點）才開始";
  return [
    `匯入 ${count} 首歌？`,
    "",
    `・${when}`,
    "・每一首都要跑 AI 人聲分離與歌詞對齊，一首大約 1~15 分鐘（看有沒有 GPU）",
    "・進度在「🌙 排程預處理」分頁看得到，跑完就會出現在曲庫裡",
    "・你放在匯入資料夾裡的原始檔案不會被移動或刪除",
  ].join("\n");
}

/** 送出之後那句話。收不下來的檔案要一起講，不然使用者會以為全部都收了。 */
function resultMessage(res) {
  const r = res || {};
  const accepted = Number(r.accepted) || 0;
  const failed = Array.isArray(r.failed) ? r.failed.length : 0;
  const head = accepted
    ? `📁 已排入 ${accepted} 首，處理進度看「🌙 排程預處理」`
    : "沒有任何檔案被收下";
  return failed ? `${head}（${failed} 個檔案讀不到，已略過）` : head;
}

if (typeof window !== "undefined") {
  window.LocalImportView = {
    FILE_STATE, fileStateMeta, canSelect, defaultSelection, formatSize, fileDetail,
    duplicateWarning, summaryText, guidanceText, skippedSummary, truncationNote,
    confirmText, resultMessage,
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    FILE_STATE, SELECTABLE, fileStateMeta, canSelect, defaultSelection, formatSize, fileDetail,
    duplicateWarning, summaryText, guidanceText, skippedSummary, truncationNote,
    confirmText, resultMessage,
  };
}
