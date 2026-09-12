/**
 * 全域作用域衝突檢查（node --test）
 *
 * 舞台與點歌台的 JS 是用 `<script src>` 一支一支載進**同一個全域作用域**的
 * （不是 ES module，因為要在沒有打包工具的情況下直接開檔案就能跑）。
 * 兩支檔案在最外層宣告同名的 `const`，後載入的那一支會在**解析階段**
 * 丟 `SyntaxError: Identifier 'X' has already been declared` 而整支不執行 ——
 * 然後用到它的那一支會跟著炸，而且是從那一行開始整支停掉。
 *
 * 這種錯誤三種現有的檢查全部測不到：
 *   * `node --check` 一次只看一支檔案，語法各自都是對的。
 *   * `node --test` 用 `require()` 各給一個模組作用域，名字不會相撞。
 *   * 後端測試根本不碰前端。
 *
 * 這一支就是補那個洞：照 HTML 實際載入的清單，把每支檔案最外層的宣告抓出來，
 * 檢查同一頁載入的檔案之間沒有重名。真的發生過一次（mic-agc.js 與
 * duet-scorer.js 都宣告了 SILENCE_DB，舞台端的 player.js 整支停在
 * `new DuetScorer()` 那一行），所以這裡守的是已經踩過的坑。
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const FRONTEND = path.join(__dirname, "..");

/** 一頁 HTML 實際載入的本地 JS（照載入順序）。CDN 或缺檔一律略過。 */
function scriptsOf(htmlName) {
  const html = fs.readFileSync(path.join(FRONTEND, htmlName), "utf8");
  const out = [];
  for (const m of html.matchAll(/<script\s+src="([^"]+)"/g)) {
    const src = m[1];
    if (!src.startsWith("/js/")) continue;
    const file = path.join(FRONTEND, src.slice(1));
    if (fs.existsSync(file)) out.push(file);
  }
  return out;
}

/**
 * 一支檔案在最外層宣告了什麼。
 *
 * 判斷方式是「行首沒有縮排」—— 這個專案的每一支前端 JS 都照這個排版，
 * 而且刻意寧可**多抓**也不要漏抓：誤判會讓這個測試叫一次（然後改名解決），
 * 漏判會讓舞台在包廂裡整個不動。
 *
 * 兩種宣告要分開看，因為後果完全不同：
 *   * `const` / `let` / `class` 重複宣告是 **SyntaxError**，整支不執行。
 *   * `function` 重複宣告是合法的，後載入的那一支**默默覆蓋**前面的。
 *     今天不會壞，但哪天有人改了其中一份（而另一份的測試照樣綠燈，
 *     因為 node --test 給每支模組獨立作用域），瀏覽器裡的行為就會跟測試不一樣。
 */
function topLevelDecls(file) {
  const src = fs.readFileSync(file, "utf8");
  const bindings = new Set();                 // const / let / var / class
  const functions = new Map();                // 函式名 -> 正規化後的實作
  for (const m of src.matchAll(/^(?:const|let|var|class)\s+([A-Za-z_$][\w$]*)/gm)) {
    bindings.add(m[1]);
  }
  // 函式實作抓到「行首單獨一個 }」為止（這個專案的排版都是這樣）
  for (const m of src.matchAll(/^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)[\s\S]*?\n\}/gm)) {
    functions.set(m[1], m[0].replace(/\s+/g, " ").trim());
  }
  return { bindings, functions };
}

for (const page of ["player.html", "index.html"]) {
  test(`${page}：同名的 const/class 會讓後載入的整支不執行`, () => {
    const files = scriptsOf(page);
    assert.ok(files.length >= 2, `${page} 應該載入多支 JS，實際 ${files.length} 支`);

    const owner = new Map();
    const clashes = [];
    for (const file of files) {
      for (const name of topLevelDecls(file).bindings) {
        const first = owner.get(name);
        if (first) clashes.push(`${name}（${path.basename(first)} 與 ${path.basename(file)}）`);
        else owner.set(name, file);
      }
    }
    assert.deepEqual(clashes, [],
      `${page} 有重複的最外層 const/class 宣告，後載入的那一支會整支不執行`);
  });

  test(`${page}：同名的共用小工具實作必須完全一樣`, () => {
    // clamp / approach / dbFromRms 這類幾行的工具在好幾支模組裡各寫一份，
    // 目前每一份都一字不差，所以誰覆蓋誰都無所謂。這個測試不是要求去重
    // （為了幾行工具硬做一支共用模組，反而綁死載入順序），而是釘住
    // 「一樣」這個前提：哪天有人只改其中一份，這裡就會叫。
    const owner = new Map();
    const diverged = [];
    for (const file of scriptsOf(page)) {
      for (const [name, impl] of topLevelDecls(file).functions) {
        const first = owner.get(name);
        if (first && first.impl !== impl) {
          diverged.push(`${name}（${path.basename(first.file)} 與 ${path.basename(file)} 的實作不同）`);
        } else if (!first) {
          owner.set(name, { file, impl });
        }
      }
    }
    assert.deepEqual(diverged, [],
      `${page} 有同名但實作不同的最外層函式：瀏覽器裡最後載入的那一份會贏，` +
      `但 node --test 給每支獨立作用域，所以單元測試不會發現`);
  });
}

test("兩頁共用的模組不會因為載入順序不同而衝突", () => {
  // 同一支檔案出現在兩頁是正常的（trend-view.js 就是），這裡確認的是
  // 「有共用」這件事本身 —— 沒有共用的話，同一份資料的兩種說法遲早會分岔。
  const shared = scriptsOf("player.html").filter((f) => scriptsOf("index.html").includes(f));
  assert.ok(shared.length >= 1, "舞台與點歌台應該至少共用一支純邏輯模組");
});
