/**
 * 今晚擂台的呈現（frontend/js/contest-view.js）
 *
 * 這裡守的是**用字**：榜上那個數字叫「擂台分」而不是「分數」，
 * 以及每一種「你還沒上榜」的狀態都必須說得出下一步。
 */
const test = require("node:test");
const assert = require("node:assert");

const {
  medal, boardRows, waitingRows, unnamedHint, describeBoard, describeVerdict,
} = require("../js/contest-view.js");

function row(name, points, extra = {}) {
  return {
    name, points, rank: extra.rank, songs: extra.songs || 3, takes: extra.takes || 3,
    qualified: true,
    top: extra.top || [
      { title: "稻香", points: points }, { title: "晴天", points: points },
      { title: "青花瓷", points: points },
    ],
  };
}

// --- 名次與獎牌 ---

test("前三名有獎牌，之後是井字號", () => {
  assert.strictEqual(medal(1), "🥇");
  assert.strictEqual(medal(3), "🥉");
  assert.strictEqual(medal(4), "#4");
});

test("看不懂的名次不生出獎牌", () => {
  for (const bad of [0, -1, null, undefined, "冠軍", NaN]) {
    assert.strictEqual(medal(bad), "");
  }
});

// --- 榜上的列 ---

test("榜上一列帶出那三首歌，第一名標記得出來", () => {
  const rows = boardRows({ rank_songs: 3, ranked: [row("小明", 88, { rank: 1 })] });
  assert.strictEqual(rows[0].medal, "🥇");
  assert.strictEqual(rows[0].isLeader, true);
  assert.match(rows[0].subtitle, /稻香（88）/);
  assert.strictEqual(rows[0].topTitles.length, 3);
});

test("空的榜不會爆掉", () => {
  assert.deepStrictEqual(boardRows(null), []);
  assert.deepStrictEqual(waitingRows(undefined), []);
  assert.strictEqual(unnamedHint(null), "");
});

// --- 還沒上榜的那幾位 ---

test("候補列講的是「再唱幾首」而不是「你不夠格」", () => {
  const rows = waitingRows({ waiting: [{ name: "阿華", songs: 2, need: 1, points: 70 }] });
  assert.match(rows[0].text, /再唱 1 首/);
});

test("need 缺值時至少講 1 首（不會出現「再唱 0 首」）", () => {
  assert.match(waitingRows({ waiting: [{ name: "阿華" }] })[0].text, /再唱 1 首/);
});

// --- 未具名提示 ---

test("有未具名的演唱就說出下一步", () => {
  const hint = unnamedHint({ unnamed_takes: 4 });
  assert.match(hint, /4 首/);
  assert.match(hint, /暱稱/);
});

test("沒有未具名的演唱就整行不顯示", () => {
  assert.strictEqual(unnamedHint({ unnamed_takes: 0 }), "");
});

// --- 整張榜的一句話 ---

test("有歌王就報歌王，而且用「擂台分」三個字", () => {
  const s = describeBoard({ rank_songs: 3, ranked: [row("小明", 91, { rank: 1 })] });
  assert.strictEqual(s.kind, "leader");
  assert.match(s.headline, /小明/);
  assert.match(s.detail, /擂台分 91/);
});

test("還沒開榜時點名最接近的那一位", () => {
  const s = describeBoard({
    rank_songs: 3,
    ranked: [],
    waiting: [{ name: "阿華", need: 2, points: 60 }, { name: "小美", need: 1, points: 50 }],
  });
  assert.strictEqual(s.kind, "waiting");
  assert.match(s.detail, /小美 再唱 1 首/);
});

test("一個人都沒唱與「唱過但都沒掛名字」講的不是同一句話", () => {
  const blank = describeBoard({ rank_songs: 3, ranked: [], waiting: [] });
  const anon = describeBoard({ rank_songs: 3, ranked: [], waiting: [], unnamed_takes: 3 });
  assert.strictEqual(blank.kind, "empty");
  assert.strictEqual(anon.kind, "empty");
  assert.notStrictEqual(blank.detail, anon.detail);
  assert.match(anon.detail, /暱稱/);
});

// --- 結算畫面那一句話 ---

test("沒有導唱音符的歌在結算畫面上完全不提擂台", () => {
  assert.strictEqual(describeVerdict({ accepted: false, reason: "no_pitch_data" }).kind, "none");
  assert.strictEqual(describeVerdict(null).kind, "none");
  assert.strictEqual(describeVerdict({}).kind, "none");
});

test("沒掛名字時講的是下一步，不是「你被排除了」", () => {
  const s = describeVerdict({ accepted: false, reason: "no_name", rank_songs: 3 });
  assert.strictEqual(s.kind, "no_name");
  assert.match(s.detail, /暱稱/);
});

test("還沒上榜時報進度", () => {
  const s = describeVerdict({
    accepted: true, qualified: false, name: "小明", points: 84,
    songs: 2, need: 1, rank_songs: 3,
  });
  assert.strictEqual(s.kind, "waiting");
  assert.match(s.headline, /再唱 1 首/);
  assert.match(s.detail, /2\/3 首/);
});

test("搶下第一與本來就是第一分成兩種", () => {
  const took = describeVerdict({
    accepted: true, qualified: true, name: "小明", points: 92,
    rank: 1, previous_rank: 2, is_leader: true, took_lead: true, rank_songs: 3,
  });
  assert.strictEqual(took.kind, "lead");
  assert.match(took.headline, /搶下今晚歌王/);

  const held = describeVerdict({
    accepted: true, qualified: true, name: "小明", points: 92,
    rank: 1, previous_rank: 1, is_leader: true, took_lead: false, rank_songs: 3,
    leader: { name: "小明", points: 92 },
  });
  assert.strictEqual(held.kind, "rank");
  assert.match(held.headline, /第 1 名/);
});

test("報名次時順便講差冠軍多少分", () => {
  const s = describeVerdict({
    accepted: true, qualified: true, name: "阿華", points: 80,
    rank: 3, previous_rank: 3, is_leader: false, rank_songs: 3,
    leader: { name: "小明", points: 92 },
  });
  assert.match(s.headline, /🥉/);
  assert.match(s.detail, /小明 還差 12 分/);
});

test("名次前進時講出前進幾名", () => {
  const s = describeVerdict({
    accepted: true, qualified: true, name: "阿華", points: 85,
    rank: 2, previous_rank: 5, is_leader: false, rank_songs: 3,
    leader: { name: "小明", points: 92 },
  });
  assert.match(s.headline, /前進 3 名/);
});

test("第一次上榜（之前沒有名次）不會講「前進 NaN 名」", () => {
  const s = describeVerdict({
    accepted: true, qualified: true, name: "阿華", points: 85,
    rank: 2, previous_rank: null, is_leader: false, rank_songs: 3,
    leader: { name: "小明", points: 92 },
  });
  assert.strictEqual(s.kind, "rank");
  assert.doesNotMatch(s.headline, /前進/);
});

test("落後為負時不會講出負數（冠軍自己看自己）", () => {
  const s = describeVerdict({
    accepted: true, qualified: true, name: "小明", points: 92,
    rank: 2, previous_rank: 2, is_leader: false, rank_songs: 3,
    leader: { name: "小明", points: 90 },
  });
  assert.doesNotMatch(s.detail, /-/);
});
