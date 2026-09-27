/**
 * 今晚擂台的呈現 (Contest / Tonight's Champion View)
 *
 * 後端 `backend/services/contest.py` 算出誰是今晚的歌王，這裡只負責把那份
 * 資料變成畫面上的字。抽成獨立模組的理由跟 trend-view.js 一樣：同一份結論
 * 要出現在兩個地方 —— 舞台的唱畢結算畫面（「你現在是今晚歌王」）與點歌台的
 * 擂台分頁（整張榜）。兩邊各寫一次的話，同一份資料會講出兩種說法。
 *
 * 用字上只有一條規則：**榜上那個數字叫「擂台分」，不叫「分數」**。
 * 結算畫面上那個四位數是累加器（唱得越長分越高），榜上這個 0~100 是命中率
 * （見 contest.py 決定一）。兩個都叫分數的話，使用者會問「我唱了 3,480 分，
 * 為什麼榜上寫 78」—— 而那個問題沒有任何一句話回答得漂亮。
 *
 * 純資料邏輯：不碰 DOM、不發網路請求，所以能用 node --test 直接跑。
 */

const MEDALS = ["🥇", "🥈", "🥉"];

/** 名次 → 獎牌或 `#4`。前三名才有獎牌（第四名的獎牌只會讓前三名不值錢）。 */
function medal(rank) {
  const n = Number(rank);
  if (!Number.isFinite(n) || n < 1) return "";
  return n <= MEDALS.length ? MEDALS[n - 1] : `#${n}`;
}

/**
 * 榜上一列的文字。回傳 `{ rank, medal, name, points, subtitle, isLeader }`。
 *
 * 副標寫的是「代表分是哪三首撐起來的」而不是「總共唱了幾首」——
 * 看榜的人下一個問題一定是「那三首是什麼」，而唱了幾首在名字旁邊已經有了。
 */
function boardRows(contest) {
  const ranked = (contest && contest.ranked) || [];
  const need = Number((contest && contest.rank_songs) || 3);
  return ranked.map(r => ({
    rank: Number(r.rank) || 0,
    medal: medal(r.rank),
    name: String(r.name || ""),
    points: Number(r.points) || 0,
    songs: Number(r.songs) || 0,
    takes: Number(r.takes) || 0,
    isLeader: Number(r.rank) === 1,
    topTitles: (r.top || []).slice(0, need).map(t => String(t.title || "")),
    subtitle: (r.top || []).slice(0, need)
      .map(t => `${String(t.title || "")}（${Number(t.points) || 0}）`).join("、"),
  }));
}

/**
 * 還沒上榜的那幾位。`need` 是「再唱幾首不同的歌」。
 *
 * 這一區刻意存在：一個只列得出前三名的榜，對第四個人來說跟壞掉沒有兩樣
 * —— 他唱了兩首，畫面上完全沒有他，而他不知道是因為門檻還是因為沒取暱稱。
 */
function waitingRows(contest) {
  const waiting = (contest && contest.waiting) || [];
  return waiting.map(r => ({
    name: String(r.name || ""),
    songs: Number(r.songs) || 0,
    need: Number(r.need) || 0,
    points: Number(r.points) || 0,
    text: `再唱 ${Math.max(1, Number(r.need) || 1)} 首不同的歌就進榜`,
  }));
}

/**
 * 「今晚還有幾首沒掛名字」那一行。沒有未具名的演唱就回空字串（整行不顯示）。
 *
 * 一定要說出**下一步**（取個暱稱），不然看到的人只會覺得機器漏算了他那幾首。
 */
function unnamedHint(contest) {
  const n = Number((contest && contest.unnamed_takes) || 0);
  if (n <= 0) return "";
  return `今晚有 ${n} 首沒掛名字的演唱沒能進榜 —— 在頁首設個暱稱，下一首就算得到你身上`;
}

/**
 * 整張榜的一句話總結。`kind` 決定畫面要畫什麼：
 *   * `empty` —— 這一場還沒有人唱。
 *   * `waiting` —— 有人唱了但還沒有人達到門檻（講「再 N 首就開榜」）。
 *   * `leader` —— 有歌王了。
 */
function describeBoard(contest) {
  const c = contest || {};
  const ranked = c.ranked || [];
  const waiting = c.waiting || [];
  const need = Math.max(1, Number(c.rank_songs) || 3);
  if (ranked.length) {
    const top = ranked[0];
    return {
      kind: "leader",
      headline: `🥇 今晚歌王：${top.name}`,
      detail: `擂台分 ${Number(top.points) || 0}（最好的 ${need} 首平均命中率）`,
    };
  }
  if (waiting.length) {
    const closest = waiting.reduce(
      (best, r) => (Number(r.need) < Number(best.need) ? r : best), waiting[0]);
    return {
      kind: "waiting",
      headline: `擂台還沒開榜 —— 唱滿 ${need} 首不同的歌就上榜`,
      detail: `${closest.name} 再唱 ${Math.max(1, Number(closest.need) || 1)} 首就是第一位`,
    };
  }
  if (Number(c.unnamed_takes) > 0) {
    return {
      kind: "empty",
      headline: "擂台還沒有人上榜",
      detail: "今晚唱過的那幾首都沒掛名字 —— 設個暱稱就算得到你身上",
    };
  }
  return {
    kind: "empty",
    headline: "擂台還沒有人上榜",
    detail: `唱滿 ${need} 首不同的歌，就會出現在今晚的榜上`,
  };
}

/**
 * 唱畢結算畫面上那一句話。吃的是 `/api/scores` 回來的 `result.contest`。
 *
 * `kind`：
 *   * `none`     —— 什麼都不顯示（沒有導唱音符的歌，或舊版舞台端沒送資料）。
 *   * `no_name`  —— 這一次沒掛名字，講出下一步。
 *   * `waiting`  —— 有名字但還沒上榜，講「再 N 首」。
 *   * `lead`     —— **這一首**把他推上第一（值得在舞台上亮一下）。
 *   * `rank`     —— 已經在榜上，報名次。
 *
 * 「本來就是第一」跟「這一首搶下第一」分開：每首歌都亮「你是歌王」的話，
 * 那句話三首之後就變成背景雜訊，真的易主的那一刻反而沒有人注意到。
 */
function describeVerdict(verdict) {
  const v = verdict || {};
  const need = Math.max(1, Number(v.rank_songs) || 3);
  if (v.reason === "no_pitch_data" || (!v.accepted && !v.reason)) {
    return { kind: "none", headline: "", detail: "" };
  }
  if (v.reason === "no_name") {
    return {
      kind: "no_name",
      headline: "🏷️ 這一首沒能進今晚的擂台",
      detail: "在點歌台或手機上設個暱稱，下一首就算得到你身上",
    };
  }
  const name = String(v.name || "");
  const points = Number(v.points) || 0;
  if (!v.qualified) {
    const left = Math.max(1, Number(v.need) || 1);
    return {
      kind: "waiting",
      headline: `🏁 ${name} 再唱 ${left} 首不同的歌就上今晚的榜`,
      detail: `目前 ${Number(v.songs) || 0}/${need} 首，擂台分 ${points}`,
    };
  }
  if (v.took_lead) {
    return {
      kind: "lead",
      headline: `🥇 ${name} 搶下今晚歌王！`,
      detail: `擂台分 ${points}（最好的 ${need} 首平均命中率）`,
    };
  }
  const rank = Number(v.rank) || 0;
  const climbed = Number(v.previous_rank) > 0 && rank > 0 && rank < Number(v.previous_rank);
  const leader = v.leader || null;
  const chase = leader && !v.is_leader
    ? `　距離 ${leader.name} 還差 ${Math.max(0, (Number(leader.points) || 0) - points)} 分`
    : "";
  return {
    kind: "rank",
    headline: `${medal(rank)} 今晚擂台第 ${rank} 名${climbed ? "（前進 " + (Number(v.previous_rank) - rank) + " 名）" : ""}`,
    detail: `擂台分 ${points}${chase}`,
  };
}

if (typeof window !== "undefined") {
  window.ContestView = { medal, boardRows, waitingRows, unnamedHint, describeBoard, describeVerdict };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { medal, boardRows, waitingRows, unnamedHint, describeBoard, describeVerdict };
}
