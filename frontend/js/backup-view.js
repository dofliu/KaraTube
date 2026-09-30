/**
 * 備份與還原分頁的顯示邏輯（純資料，沒有 DOM）。
 *
 * 這一頁的錯法跟其他每一頁都不一樣：其他頁顯示錯了是看到錯的東西，這一頁
 * 顯示錯了會讓人**按下一個他以為是別的意思的按鈕**，而那顆按鈕會把資料蓋掉。
 *
 * 所以這裡的三支主角都不是「排版」而是「判斷」：
 *   - summarizePlan：這份備份會裝什麼（按下載之前）
 *   - restoreVerdict：這個檔案能不能還原、按下去會變成什麼樣（按還原之前）
 *   - reportSummary：開機那一刻發生了什麼（人回來之後）
 *
 * 後端已經判過一次（backend/services/backup.py），這裡不重判，只把後端給的
 * 那幾個數字翻成一句人看得懂的話 —— 兩邊各判一次的話，兩邊會在某一版之後
 * 開始講不一樣的事，而使用者只看得到這一邊。
 */

/** 千分位。備份頁上的數字常常是四位數（一千多筆成績），逗號讓它一眼看得出量級。 */
function groupDigits(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "";
  return Math.round(n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/** 檔案大小。備份是幾百 KB 到幾 MB，所以只需要三個單位。 */
function formatBytes(bytes) {
  // null 是「不知道」而不是 0 —— Number(null) 會變成 0，而畫成「0 B」
  // 會讓人以為備份檔是空的。
  if (bytes === null || bytes === undefined || bytes === "") return "—";
  const n = Number(bytes);
  if (!Number.isFinite(n) || n < 0) return "—";
  if (n < 1024) return `${Math.round(n)} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * 「幾筆」的那一段。
 *
 * `entries` 是 null 時講「—」而不是「0 筆」：後端刻意用 null 表示「數不出來」，
 * 而在這一頁上「0 筆」會讓人以為備份是空的，然後去做一件完全不必要的事。
 */
function formatEntries(entries, unit) {
  if (entries === null || entries === undefined) return "—";
  return `${groupDigits(entries)} ${unit || "筆"}`;
}

/**
 * 這份備份會裝什麼（`GET /api/backup` 的 groups）。
 *
 * 空的那幾份也留在清單上，只是標成 present:false —— 拿掉的話，「我的成績
 * 有沒有被備份」這個問題在畫面上沒有答案，而沒有答案的人會假設答案是「有」。
 */
function summarizePlan(plan) {
  const groups = (plan && Array.isArray(plan.groups)) ? plan.groups : [];
  let present = 0;
  let total = 0;
  const rows = [];
  groups.forEach((group) => {
    (group.files || []).forEach((file) => {
      total += 1;
      if (file.present) present += 1;
      rows.push({
        ...file,
        group: group.group,
        groupLabel: group.label,
        sizeText: file.present ? formatBytes(file.bytes) : "—",
        entriesText: file.present ? formatEntries(file.entries, file.unit) : "尚未有資料",
      });
    });
  });
  return {
    groups,
    rows,
    present,
    total,
    sizeText: formatBytes(plan && plan.total_bytes),
    // 一份備份至少要有一樣東西。全空的機器做備份不是錯，但值得講一句，
    // 免得有人拿著一個空檔案以為自己已經備份好了。
    empty: present === 0,
  };
}

/**
 * 「按下還原會發生什麼」（`POST /api/restore/inspect` 的報告）。
 *
 * 分成三級而不是兩級：
 *   blocked —— 後端說不行（壞檔、格式太新、空備份）。按鈕要關掉。
 *   caution —— 可以還原，但有東西會被**清掉**，或者有一批歌不在這台機器上。
 *   ready   —— 沒有意外。
 *
 * caution 存在的理由是「還原是整份取代」：備份裡沒有的那幾份會被刪掉，
 * 而那件事一定要在按下去之前講，不能等報告出來才講。
 */
function restoreVerdict(report) {
  if (!report || typeof report !== "object") {
    return { level: "blocked", canApply: false, headline: "還沒有選擇備份檔",
             notes: [], clears: [], files: [] };
  }
  const problems = Array.isArray(report.problems) ? report.problems : [];
  if (report.ok !== true || problems.length) {
    return {
      level: "blocked",
      canApply: false,
      headline: problems[0] || "這份備份不能還原",
      notes: problems.slice(1),
      clears: [],
      files: [],
    };
  }

  const files = (report.files || []).map((row) => ({
    ...row,
    fromText: formatEntries(row.entries, row.unit),
    toText: formatEntries(row.current_entries, row.unit),
    // 「1,204 筆 → 3 筆」比「會被覆蓋」有用得多：前者讓人自己看出選錯檔案。
    changeText: `現在 ${formatEntries(row.current_entries, row.unit)}`
      + ` → 還原後 ${formatEntries(row.entries, row.unit)}`,
  }));

  // 只有「現在真的有東西」的那幾份才算會被清掉。備份裡沒有、這台機器也沒有，
  // 是一件沒有發生的事，不該出現在警告上（警告一多就沒有人讀）。
  const clears = (report.missing || []).filter(
    (row) => Number(row.current_entries) > 0);

  const songs = report.songs || {};
  const notes = [];
  clears.forEach((row) => {
    notes.push(`「${row.label}」這台機器現在有 ${formatEntries(row.current_entries, "筆")}，`
      + "但這份備份裡沒有 —— 還原是整份取代，它會被清掉。");
  });
  if (songs.known && songs.missing > 0) {
    notes.push(`備份的曲庫清單上有 ${groupDigits(songs.in_backup)} 首歌，`
      + `這台機器上有 ${groupDigits(songs.matched)} 首 —— `
      + `其餘 ${groupDigits(songs.missing)} 首的歌號會先變成「不在曲庫裡」，`
      + "重新處理之後就會拿回原本的號碼。");
  }
  if (Array.isArray(report.unknown) && report.unknown.length) {
    notes.push(`備份裡有 ${report.unknown.length} 個這台機器不認得的檔案，會直接略過。`);
  }

  return {
    level: (clears.length || (songs.known && songs.missing > 0)) ? "caution" : "ready",
    canApply: true,
    headline: `這份備份來自 ${report.created_at || "未知時間"}`
      + (report.app_version ? `（KaraTube ${report.app_version}）` : ""),
    notes,
    clears,
    files,
    songs,
  };
}

/**
 * 開機那一刻發生了什麼（`restore_report.json`）。
 *
 * 這一支的存在本身就是設計的一部分：還原是在沒有人看著的時候做的，
 * 按下確認的人看到的只是「重新啟動後生效」—— 真正的結果要等他回來。
 */
function reportSummary(report) {
  if (!report || typeof report !== "object") return null;
  const restored = report.restored || [];
  const cleared = report.cleared || [];
  const failed = report.failed || [];
  if (report.status === "aborted") {
    return {
      tone: "error",
      title: "上次開機時沒有還原",
      detail: report.reason || "還原前的現況備份寫不出來，為了不讓現有資料回不去，這次還原沒有進行。",
      lines: [],
      at: report.applied_at || "",
    };
  }
  const lines = [];
  if (restored.length) lines.push(`還原了 ${restored.length} 份：` + restored.map(r => r.label).join("、"));
  if (cleared.length) lines.push(`清掉了 ${cleared.length} 份（備份裡沒有）：` + cleared.map(r => r.label).join("、"));
  failed.forEach((row) => lines.push(`⚠️ ${row.label || row.name}：${row.error || "失敗"}`));
  if (report.safety_copy) {
    lines.push(`還原前的現況已經存成 ${report.safety_copy}，要退回去就還原它。`);
  }
  return {
    tone: failed.length ? "warn" : "ok",
    title: failed.length ? "上次還原只完成了一部分" : "上次還原已完成",
    detail: report.source_created_at
      ? `來源備份建立於 ${report.source_created_at}`
        + (report.source_version ? `（KaraTube ${report.source_version}）` : "")
      : "",
    lines,
    at: report.applied_at || "",
  };
}

/**
 * 待套用的還原要講的那一句。
 *
 * 重點是「還沒有發生」與「取消得掉」—— 看到「已排定還原」而不知道可以取消的人，
 * 會以為自己已經按下了一個回不去的按鈕。
 */
function pendingSummary(pending) {
  if (!pending || typeof pending !== "object") return null;
  const files = pending.files || [];
  const clears = (pending.clears || []).filter((row) => Number(row.current_entries) > 0);
  return {
    stagedAt: pending.staged_at || "",
    createdAt: pending.created_at || "",
    appVersion: pending.app_version || "",
    fileCount: files.length,
    clearCount: clears.length,
    labels: files.map((row) => row.label).filter(Boolean),
    message: `已排定還原 ${files.length} 份資料`
      + (clears.length ? `，並清掉 ${clears.length} 份備份裡沒有的資料` : "")
      + "。重新啟動 KaraTube 之後生效 —— 在那之前隨時可以取消。",
  };
}

if (typeof window !== "undefined") {
  window.BackupView = {
    formatBytes, formatEntries, groupDigits,
    summarizePlan, restoreVerdict, reportSummary, pendingSummary,
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    formatBytes, formatEntries, groupDigits,
    summarizePlan, restoreVerdict, reportSummary, pendingSummary,
  };
}
