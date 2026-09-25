/**
 * 歌詞對齊品質的說法（純邏輯，沒有 DOM）。
 *
 * 後端每處理完一首歌就留一份診斷（`cache/songs/<id>/alignment.json`：
 * source / scale / offset / score / recall / lines / track），但那幾個數字是
 * 流水線內部用的，**對人沒有校準過**。直接把 `0.577` 印在畫面上，店員會讀成
 * 「58 分、不及格」，而它在這條流水線上其實是良好（信任門檻是 0.28）。
 *
 * 這一支負責把它翻譯成人看得懂的一句話。三個最容易畫錯的地方：
 *
 * 1. **沒有 alignment.json ≠ 0 分。** 這份診斷是後來才加的，功能上線前處理好的
 *    歌一律沒有。畫成紅色的 0 分等於冤枉一整批其實沒問題的歌，而使用者會去
 *    重算一堆本來就對的歌詞。缺檔就是「未知」。
 * 2. **`source: "whisper"` 時 score 恆為 0.0。** 那不是「對得很爛」，是
 *    「根本沒有 LRC 可以對，改用聽寫」—— 聽寫出來的字幕通常時間軸很準、
 *    但用字可能不對。要講的是這件事，不是分數。
 * 3. **`source: "placeholder"` 才是真的該紅。** 那首歌只有幾行佔位文字，
 *    等於沒有歌詞。
 */

// 後端 lyrics_aligner.MIN_TRUST_SCORE：低於這個分數會自動改走聽寫。
// 拿它當「勉強」與「有問題」的分界（frontend/tests/alignment-view.test.js 釘住兩邊一致）。
const ALIGN_MIN_TRUST_SCORE = 0.28;
// 「良好」的門檻。0.45 以上在實際曲庫裡對應的是「整首跟著唱都不會覺得怪」。
const ALIGN_GOOD_SCORE = 0.45;

/** 讀 alignment 裡的分數，缺欄位或不是數字回 null（不是 0）。 */
function alignScore(alignment) {
  const raw = alignment && alignment.score;
  const num = Number(raw);
  return Number.isFinite(num) ? num : null;
}

/**
 * 快取管理那一列的對齊徽章。回傳 `{ label, tone, title, rebuildHint }`。
 *
 * `tone` 只用專案既有的四種語意：ok（綠）/ warn（黃）/ bad（紅）/ muted（灰）。
 * 主體是**文字**，原始數字退到 title（滑鼠停留）裡 —— 數字沒有校準過，
 * 拿它當主角只會引來「這樣算好還是壞」。
 */
function alignmentBadge(alignment) {
  if (!alignment || typeof alignment !== "object") {
    return {
      label: "對齊未知",
      tone: "muted",
      title: "這首歌是在對齊診斷加進來之前處理的，按「重算歌詞」就會有分數",
      rebuildHint: "沒有診斷資料，重算一次就知道現在對得準不準",
    };
  }

  const source = String(alignment.source || "");
  const score = alignScore(alignment);
  const parts = [];
  if (alignment.track) parts.push(`對到：${alignment.track}`);
  if (score !== null) parts.push(`分數 ${score.toFixed(3)}（信任門檻 ${ALIGN_MIN_TRUST_SCORE}）`);
  if (Number.isFinite(Number(alignment.recall))) {
    parts.push(`人聲覆蓋率 ${(Number(alignment.recall) * 100).toFixed(0)}%`);
  }
  if (Number.isFinite(Number(alignment.scale)) && Number(alignment.scale) !== 1) {
    parts.push(`變速校正 ×${Number(alignment.scale).toFixed(3)}`);
  }
  if (Number.isFinite(Number(alignment.lines))) parts.push(`${alignment.lines} 行`);

  if (source === "placeholder") {
    return {
      label: "沒有歌詞",
      tone: "bad",
      title: ["這首歌沒有抓到任何可用的歌詞，畫面上只有佔位文字。", ...parts].join("　"),
      rebuildHint: "重算會再試一次線上歌詞來源",
    };
  }

  if (source === "whisper") {
    return {
      label: "聽寫字幕",
      tone: "warn",
      // score 在這條路徑上恆為 0，講分數是誤導
      title: ["找不到可信的歌詞檔，改用人聲聽寫。時間軸通常準，但用字可能不對。",
              ...parts.filter(p => !p.startsWith("分數"))].join("　"),
      rebuildHint: "重算會再找一次歌詞檔（找到就會換成正式歌詞）",
    };
  }

  // 本機匯入時，使用者自己擺在檔案旁邊的那一份 .lrc。
  // 分數照樣分級（手打的 LRC 一樣會對到不同版本而歪掉），但**下一步不一樣**：
  // 重算只會再讀同一份檔案，所以修法是去換掉那個 .lrc，不是按重算。
  // 不講這一句的話，使用者會對著同一首歌按五次重算，每次得到一樣的結果。
  if (source === "lrc_local") {
    const tone = score === null ? "muted"
      : (score >= ALIGN_GOOD_SCORE ? "ok" : (score >= ALIGN_MIN_TRUST_SCORE ? "warn" : "bad"));
    const head = tone === "ok"
      ? "用的是你放在檔案旁邊的 .lrc，對得很準。"
      : "用的是你放在檔案旁邊的 .lrc。時間軸看起來對不太上 —— " +
        "那份歌詞可能是別的版本的（改用另一份 .lrc 再重新處理一次即可）。";
    return {
      label: "自備歌詞",
      tone,
      title: [head, ...parts].join("　"),
      rebuildHint: "重算會再讀同一份 .lrc（要換歌詞的話先換掉那個檔案）",
    };
  }

  if (source === "lrc") {
    if (score === null) {
      return { label: "對齊未知", tone: "muted", title: parts.join("　"),
               rebuildHint: "診斷資料不完整，重算一次就會補上" };
    }
    if (score >= ALIGN_GOOD_SCORE) {
      return { label: "對齊良好", tone: "ok", title: parts.join("　"), rebuildHint: "" };
    }
    if (score >= ALIGN_MIN_TRUST_SCORE) {
      return {
        label: "對齊勉強", tone: "warn", title: parts.join("　"),
        rebuildHint: "分數偏低：可能抓到了不同版本的歌詞，重算會換一份試試",
      };
    }
    return {
      label: "對齊可疑", tone: "bad", title: parts.join("　"),
      rebuildHint: "分數低於信任門檻，很可能抓到同名的別首歌",
    };
  }

  return { label: "對齊未知", tone: "muted", title: parts.join("　"),
           rebuildHint: "沒有可判讀的來源，重算一次就知道" };
}

/**
 * 這一列能不能按「重算歌詞」。
 *
 * 重算只吃 `vocals.mp3`；缺人聲軌的壞資料夾只能走「重新處理」。
 * 回傳 `{ can, reason }` —— 不能按的時候要說得出為什麼，不然那顆灰掉的按鈕
 * 只會讓人一直點。
 */
function canRebuildLyrics(entry) {
  const e = entry || {};
  const missing = Array.isArray(e.missing_files) ? e.missing_files : [];
  if (missing.includes("vocals.mp3")) {
    return { can: false, reason: "缺人聲軌，只能用「重新處理」重跑整條流水線" };
  }
  if (e.complete === false && missing.length) {
    return { can: false, reason: `缺 ${missing.join("、")}，請用「重新處理」` };
  }
  return { can: true, reason: "" };
}

/**
 * 「重算歌詞」的確認文字。
 *
 * 要講滿四件事，因為這四件事使用者都問過：便宜在哪（不重新下載）、
 * 什麼時候看得到（下次播放）、會不會弄壞別的東西（跨場次趨勢歸零）、
 * 以及**那首歌手動校正過的偏移會被清掉**（它是為舊歌詞調的）。
 */
function rebuildConfirmText({ title = "", songId = "", offsetMs = 0, rate = 1,
                             alignment = null } = {}) {
  const name = title || songId || "這首歌";
  const badge = alignmentBadge(alignment);
  const lines = [
    `重算「${name}」的歌詞時間軸？`,
    "",
    `・目前狀態：${badge.label}`,
    "・只用已經存在的人聲軌重新對齊，不重新下載、不跑人聲分離（通常十幾秒）",
    "・找不到可信的歌詞時會改用聽寫，那一段會久一點",
    "・重算後要**下一次播放**才會套用（正在唱的那一首不能重算）",
    "・這首歌的跨場次段落趨勢會從頭累積（曲式可能變了）",
  ];
  if (offsetMs) {
    const sign = offsetMs > 0 ? "+" : "−";
    lines.push(`・會一併清掉這首歌手動校正的 ${sign}${Math.abs(offsetMs)} ms 字幕偏移` +
               "（那是為舊的那份歌詞調的）");
  }
  // 速度校正也是為舊的那份歌詞解出來的，而且**留著比清掉更糟**：
  // 新的時間軸是重新對出來的，舊的 1.024× 套上去會把一份剛對好的歌詞再弄歪一次。
  if (Number(rate) && Number(rate) !== 1) {
    lines.push(`・也會清掉兩點校正解出來的 ${Number(rate).toFixed(3)}× 速度` +
               "（新的時間軸是重對的，舊的速度套上去反而會歪）");
  }
  return lines.join("\n");
}

/** 重算完成後的那句話：新舊分數要一起講，否則沒有人知道有沒有變好。 */
function rebuildResultMessage(before, after, clearedOffsetMs = 0, clearedRate = 1) {
  const from = alignmentBadge(before);
  const to = alignmentBadge(after);
  const beforeScore = alignScore(before);
  const afterScore = alignScore(after);
  const detail = (beforeScore !== null && afterScore !== null)
    ? `（${beforeScore.toFixed(3)} → ${afterScore.toFixed(3)}）` : "";
  const cleared = [];
  if (clearedOffsetMs) cleared.push("字幕偏移");
  if (Number(clearedRate) && Number(clearedRate) !== 1) cleared.push("速度校正");
  const tail = cleared.length ? `，原本的手動${cleared.join("與")}已清掉` : "";
  return `🔄 歌詞重算完成：${from.label} → ${to.label}${detail}${tail}。下次播放這首歌就會套用。`;
}

if (typeof window !== "undefined") {
  window.AlignmentView = {
    ALIGN_MIN_TRUST_SCORE, ALIGN_GOOD_SCORE,
    alignScore, alignmentBadge, canRebuildLyrics, rebuildConfirmText, rebuildResultMessage,
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    ALIGN_MIN_TRUST_SCORE, ALIGN_GOOD_SCORE,
    alignScore, alignmentBadge, canRebuildLyrics, rebuildConfirmText, rebuildResultMessage,
  };
}
