/**
 * 備份與還原分頁的顯示邏輯測試（node --test，不需要瀏覽器）。
 *
 * 這一頁的錯法比其他頁貴：其他頁顯示錯了是看到錯的東西，這一頁顯示錯了
 * 會讓人按下一個他以為是別的意思的按鈕，而那顆按鈕會把資料蓋掉。
 * 所以測的幾乎都是「有沒有把該講的話講出來」。
 */
const test = require("node:test");
const assert = require("node:assert/strict");

const {
  formatBytes,
  formatEntries,
  groupDigits,
  summarizePlan,
  restoreVerdict,
  reportSummary,
  pendingSummary,
} = require("../js/backup-view.js");

test("數字帶千分位：一千多筆成績要一眼看得出量級", () => {
  assert.equal(groupDigits(1204), "1,204");
  assert.equal(groupDigits(999), "999");
  assert.equal(groupDigits("x"), "");
});

test("檔案大小只需要三個單位", () => {
  assert.equal(formatBytes(512), "512 B");
  assert.equal(formatBytes(2048), "2.0 KB");
  assert.equal(formatBytes(3 * 1024 * 1024), "3.0 MB");
  assert.equal(formatBytes(null), "—");
});

test("「數不出來」不能寫成 0 筆", () => {
  // 後端用 null 表示數不出來。畫成「0 筆」的話，使用者會以為備份是空的，
  // 然後去做一件完全不必要的事。
  assert.equal(formatEntries(null, "首"), "—");
  assert.equal(formatEntries(undefined, "首"), "—");
  assert.equal(formatEntries(0, "首"), "0 首");
  assert.equal(formatEntries(1204, "筆成績"), "1,204 筆成績");
});

function plan() {
  return {
    total_bytes: 4096,
    groups: [
      {
        group: "promise", label: "不可重建的承諾",
        files: [{ name: "song_numbers.json", label: "歌號簿", present: true,
                  bytes: 2048, entries: 312, unit: "首有號碼" }],
      },
      {
        group: "people", label: "人累積出來的",
        files: [{ name: "score_history.json", label: "成績與個人最佳",
                  present: false, bytes: 0, entries: null, unit: "筆成績" }],
      },
    ],
  };
}

test("清單摘要：空的那幾份也要留在畫面上", () => {
  // 拿掉的話，「我的成績有沒有被備份」在畫面上沒有答案，
  // 而沒有答案的人會假設答案是「有」。
  const out = summarizePlan(plan());
  assert.equal(out.total, 2);
  assert.equal(out.present, 1);
  assert.equal(out.rows[1].name, "score_history.json");
  assert.equal(out.rows[1].entriesText, "尚未有資料");
  assert.equal(out.rows[0].entriesText, "312 首有號碼");
  assert.equal(out.sizeText, "4.0 KB");
  assert.equal(out.empty, false);
});

test("一台什麼都還沒有的機器，備份是空的 —— 而那件事要講", () => {
  const blank = plan();
  blank.groups[0].files[0].present = false;
  assert.equal(summarizePlan(blank).empty, true);
  assert.equal(summarizePlan(null).empty, true);
});

function okReport(extra) {
  return Object.assign({
    ok: true,
    created_at: "2026-09-30T21:00:00",
    app_version: "1.29.0",
    problems: [],
    files: [{ name: "favorites.json", label: "我的最愛", entries: 48,
              current_entries: 3, unit: "首收藏" }],
    missing: [],
    unknown: [],
    songs: { known: true, in_backup: 312, matched: 312, missing: 0 },
  }, extra || {});
}

test("後端說不行就按不下去，而且講的是後端那一句", () => {
  const out = restoreVerdict({ ok: false, problems: ["這不是 KaraTube 的備份檔。"] });
  assert.equal(out.level, "blocked");
  assert.equal(out.canApply, false);
  assert.equal(out.headline, "這不是 KaraTube 的備份檔。");
});

test("沒有選檔案的時候也是 blocked，不是 ready", () => {
  assert.equal(restoreVerdict(null).canApply, false);
  assert.equal(restoreVerdict(undefined).level, "blocked");
});

test("一切正常時是 ready，而且每一份都講得出「現在 → 還原後」", () => {
  const out = restoreVerdict(okReport());
  assert.equal(out.level, "ready");
  assert.equal(out.canApply, true);
  assert.match(out.headline, /2026-09-30/);
  assert.match(out.headline, /1\.29\.0/);
  assert.equal(out.files[0].changeText, "現在 3 首收藏 → 還原後 48 首收藏");
  assert.deepEqual(out.notes, []);
});

test("會被清掉的資料一定要在按下去之前講", () => {
  // 還原是整份取代。不講的話，一份「只備份了最愛」的檔案會把成績清光，
  // 而按下去的人以為自己只是在還原最愛。
  const out = restoreVerdict(okReport({
    missing: [{ name: "score_history.json", label: "成績與個人最佳",
                current_entries: 1204 }],
  }));
  assert.equal(out.level, "caution");
  assert.equal(out.canApply, true);
  assert.equal(out.clears.length, 1);
  assert.match(out.notes[0], /成績與個人最佳/);
  assert.match(out.notes[0], /1,204/);
  assert.match(out.notes[0], /會被清掉/);
});

test("兩邊都沒有的那一份不算警告", () => {
  // 警告一多就沒有人讀，而「一件沒有發生的事」是最不值得占用那個位置的東西。
  const out = restoreVerdict(okReport({
    missing: [{ name: "play_stats.json", label: "點唱排行", current_entries: 0 },
              { name: "vocal_range.json", label: "音域檔案", current_entries: null }],
  }));
  assert.equal(out.level, "ready");
  assert.deepEqual(out.notes, []);
});

test("歌不在這台機器上要先講 —— 不然還原完會被當成失敗", () => {
  // 曲庫不進備份是刻意的（幾十 GB、而且可重建）。代價是還原完那幾百個歌號
  // 會先變成「不在曲庫裡」，而沒有這一句的人會以為還原沒有成功。
  const out = restoreVerdict(okReport({
    songs: { known: true, in_backup: 312, matched: 40, missing: 272 },
  }));
  assert.equal(out.level, "caution");
  assert.match(out.notes[0], /312/);
  assert.match(out.notes[0], /40/);
  assert.match(out.notes[0], /272/);
  assert.match(out.notes[0], /重新處理/);
});

test("曲庫清單讀不出來時不編數字出來", () => {
  const out = restoreVerdict(okReport({ songs: { known: false, missing: 0 } }));
  assert.equal(out.level, "ready");
  assert.deepEqual(out.notes, []);
});

test("不認得的檔案只是略過，講一句就好", () => {
  const out = restoreVerdict(okReport({ unknown: ["mystery.json"] }));
  assert.match(out.notes[0], /略過/);
  assert.equal(out.canApply, true);
});

test("開機報告：成功時說清楚換了什麼、以及怎麼退回去", () => {
  const out = reportSummary({
    status: "done",
    applied_at: "2026-10-01T09:00:00",
    source_created_at: "2026-09-30T21:00:00",
    source_version: "1.29.0",
    safety_copy: "pre-restore-20261001-090000.zip",
    restored: [{ name: "favorites.json", label: "我的最愛" }],
    cleared: [{ name: "play_stats.json", label: "點唱排行" }],
    failed: [],
  });
  assert.equal(out.tone, "ok");
  assert.match(out.lines[0], /我的最愛/);
  assert.match(out.lines[1], /點唱排行/);
  assert.match(out.lines[2], /pre-restore-20261001-090000\.zip/);
  assert.match(out.detail, /1\.29\.0/);
});

test("開機報告：有失敗就是 warn，而且指名是哪一份", () => {
  const out = reportSummary({
    status: "partial", restored: [], cleared: [],
    failed: [{ name: "settings.json", label: "系統設定", error: "permission denied" }],
  });
  assert.equal(out.tone, "warn");
  assert.match(out.lines[0], /系統設定/);
  assert.match(out.lines[0], /permission denied/);
});

test("開機報告：現況備份寫不出來時，講的是「沒有還原」而不是「還原失敗」", () => {
  // 差別很大：前者代表資料還在（而且重開機會再試一次），
  // 後者會讓人以為資料已經被動過了。
  const out = reportSummary({ status: "aborted", reason: "磁碟滿了" });
  assert.equal(out.tone, "error");
  assert.equal(out.title, "上次開機時沒有還原");
  assert.equal(out.detail, "磁碟滿了");
});

test("沒有報告就是 null，不要生一張空卡片出來", () => {
  assert.equal(reportSummary(null), null);
  assert.equal(reportSummary("nope"), null);
});

test("待套用：重點是「還沒有發生」與「取消得掉」", () => {
  const out = pendingSummary({
    staged_at: "2026-09-30T22:10:00",
    created_at: "2026-09-30T21:00:00",
    app_version: "1.29.0",
    files: [{ name: "favorites.json", label: "我的最愛" },
            { name: "song_numbers.json", label: "歌號簿" }],
    clears: [{ name: "play_stats.json", label: "點唱排行", current_entries: 12 }],
  });
  assert.equal(out.fileCount, 2);
  assert.equal(out.clearCount, 1);
  assert.deepEqual(out.labels, ["我的最愛", "歌號簿"]);
  assert.match(out.message, /重新啟動/);
  assert.match(out.message, /隨時可以取消/);
});

test("待套用：沒有東西會被清掉時就不要多講一句", () => {
  const out = pendingSummary({ files: [{ label: "我的最愛" }], clears: [] });
  assert.equal(out.clearCount, 0);
  assert.doesNotMatch(out.message, /清掉/);
  assert.equal(pendingSummary(null), null);
});
