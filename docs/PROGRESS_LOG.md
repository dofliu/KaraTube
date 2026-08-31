# KaraTube 開發進度日誌

每次迭代完成後在最上方新增一筆，記錄做了什麼、為什麼、下一步。
功能對照總表見 [ROADMAP.md](ROADMAP.md)。

---

## 2026-08-31（第 2 輪）— 快取管理 UI + 錯誤歌曲重新處理

**目標**：發布前的運維必備 —— 機台磁碟總會滿、下載總會斷線。
給店家/家用管理者一個看得到磁碟用量、刪得掉歌、救得回失敗處理的入口。

### 新功能：🗂️ 快取管理分頁
- `SongStorage` 新增 `get_song_size()` / `list_cache_entries()` / `cache_stats()`：
  逐首歌統計磁碟用量、以四個必要檔案（metadata / instrumental / vocals / lyrics）判斷完整性、
  `shutil.disk_usage` 回報磁碟剩餘空間。
- `list_cache_entries()` 連 **沒有 metadata 的壞資料夾也列出來** ——
  點歌清單把它們藏起來是對的，但它們照樣佔磁碟，管理介面必須看得到才刪得掉。
- 新 API：`GET /api/cache`（總覽）、`DELETE /api/cache/{song_id}`（刪除）、
  `POST /api/cache/{song_id}/reprocess`（砍掉快取重跑整條流水線，保留原 metadata 的顯示資訊）。
- 刪除與重新處理都有守門：`QueueManager.is_song_in_use()` ——
  演唱中或還在佇列裡的歌回 409，不然舞台會直接斷片。
- 點歌台新增「🗂️ 快取管理」分頁：總用量 + 磁碟剩餘、每首歌一列
  （磁碟用量、完整/不完整徽章、缺檔清單 tooltip）、點歌/重新處理/刪除按鈕（含確認對話框）。

### 新功能：🔁 佇列失敗重試
- `QueueManager.retry_item()`：只對狀態 ERROR 的佇列項生效，重置進度重跑 `_process_queue_item`。
- 新 API：`POST /api/queue/{queue_id}/retry`。
- 佇列裡「● 處理失敗」的歌多一顆 🔁 —— 網路斷線類的暫時性錯誤重跑一次通常就過，
  不用刪掉重新搜尋。

### 測試（70 條，全綠，+11）
- `test_storage.py` +4：單曲磁碟用量、壞資料夾列舉與缺檔清單、快取總覽統計。
- `test_queue_manager.py` +3：失敗重試成功上台（FlakyProcessor 第一次爆第二次過）、
  重試只對 ERROR 生效、`is_song_in_use` 的三種狀態。
- `test_api.py` +5：cache 總覽形狀、刪除與 404、使用中 409 守門、reprocess/retry 的 404。

### 文件
- README（核心特色 10 + 重新編號）、USER_GUIDE（快取管理章節、佇列重試、五分頁）、
  ROADMAP 打勾兩項（系統類全數只剩設定頁與 Docker）、STATUS.yaml。

### 下一步（建議下輪迭代）
1. 導唱片頭卡（「演唱者：XXX」）＋男調/女調一鍵切換（演唱體驗）。
2. 多人包廂暱稱：佇列顯示「誰點的」（手機端已可連入，只差身分）。
3. Python lint（ruff）納入 CI、pipeline 純函數單元測試。

---

## 2026-08-31 — 已唱歷史分頁 + 唱畢總評分結算畫面

**目標**：補上商用 KTV 兩塊核心體驗 —— 「剛剛唱過什麼、再唱一次」的已唱歷史，
以及整首唱完的總評分結算（含個人最佳與擊敗比例），一次做掉 ROADMAP 三項。

### 新功能：🕘 已唱歷史分頁
- 新增 `backend/services/song_history.py`：一次一筆的演唱時間序列（同一首唱三次就有三筆），
  執行緒安全、JSON 持久化（`cache/song_history.json`），保留最近 500 筆。
- `QueueManager.play_next()` 在歌曲真正上台時記錄（與點唱排行同一時機，排進佇列又被刪的不算）。
- 新 API：`GET /api/history`（含 `today_count` 今天唱幾首）、`DELETE /api/history`。
- 點歌台新增「🕘 已唱歷史」分頁：最新在前、標示「唱於 今天 21:34」、一鍵再點。

### 新功能：🏁 唱畢總評分結算畫面
- 新增 `backend/services/score_history.py`：評分歷史 + 每曲個人最佳 +
  擊敗比例（這次分數贏過過往多少比例的演唱），持久化 `cache/score_history.json`。
- 新 API：`POST /api/scores`（記錄成績，回傳個人最佳/是否破紀錄/擊敗比例，並廣播 `SCORE_FINAL`）、
  `GET /api/scores`、`GET /api/scores/{song_id}/best`。
- 舞台端（`player.js` + `player.html` + `karaoke.css`）：整首唱完自動亮出結算卡
  —— 總分滾動動畫、SSS~C 等級、音準率、最大 COMBO、個人最佳、擊敗比例；
  停留 9 秒（點擊可跳過）後才送 `SONG_ENDED` 進下一首；切歌會直接收掉結算不卡流程；
  沒人開口唱（偵測到的發聲少於約 1 秒）不出結算。
- 點歌台收到 `SCORE_FINAL` 顯示同步通知，包廂裡每支手機都看得到成績。

### 評分引擎重構（`pitch-engine.js`）
- 把「音高偵測 + 計分」從渲染函式抽成獨立的 `tick()`：
  隱藏音準線（P 鍵）時評分照樣進行，唱畢結算才公平（原本隱藏音準線=停止計分）。
- 新增結算統計：機會幀（有導唱音符）、命中幀、Perfect 幀、發聲幀；
  `getFinalResult()` 產出總分、音準率、等級（SSS≥75% 命中率，逐幀命中很嚴格所以門檻下修）、最大 COMBO。
- 修正 `setPitchData()` 沒重置 `maxCombo` 的舊 bug（上一首的 COMBO 會漏到下一首的結算）。

### 修正
- 結算畫面亮著時共享狀態仍是 `is_playing=true`，任何 STATE_UPDATE（如有人動滑桿）
  會把唱完的歌又拉回來重播 —— 已在舞台端加結算中的播放守門。

### 測試（59 條，全綠）
- 新增 `tests/test_song_history.py`（8 條）：記錄順序、今天計數、持久化、壞檔復原、上限裁切。
- 新增 `tests/test_score_history.py`（9 條）：個人最佳、擊敗比例、參數夾限、持久化、清空。
- `tests/test_api.py` 加 6 條：history / scores 端點的形狀與錯誤處理（含測試後還原本機 cache）。
- `tests/test_queue_manager.py` 驗證上台即記入已唱歷史。

### 文件
- README（核心特色 8、9 + 目錄結構）、USER_GUIDE（曲庫分頁、已唱歷史、唱畢結算章節）、
  ROADMAP 打勾三項、STATUS.yaml。

### 下一步（建議下輪迭代）
1. 快取管理 UI + 錯誤歌曲重新處理按鈕（發布前的運維必備）。
2. 導唱片頭卡（「演唱者：XXX」）＋男調/女調一鍵切換。
3. Python lint（ruff）納入 CI、pipeline 純函數單元測試。

---

## 2026-08-30 — 我的最愛 + CI 測試基礎建設

**目標**：朝可發布系統邁進的第一輪迭代 —— 補上商用 KTV 必備的「我的最愛」，並建立 CI 品質防線。

### 新功能：⭐ 我的最愛（收藏常唱歌曲）
- 新增 `backend/services/favorites.py`：執行緒安全、JSON 持久化（`cache/favorites.json`），與點唱統計相同模式。
- 新 API：`GET /api/favorites`、`POST /api/favorites/toggle`、`DELETE /api/favorites/{song_id}`。
- 點歌台新增「⭐ 我的最愛」分頁；每張歌卡右上角加收藏星星（☆/⭐ 就地切換，不重畫清單、不丟捲動位置）；在最愛分頁取消收藏即時從清單消失。

### 工程品質：CI + 測試
- 新增 GitHub Actions CI（`.github/workflows/ci.yml`）：
  - 後端：`compileall` 全源碼語法檢查 + `pytest`（Python 3.11）。
  - 前端：`node --check` 全 JS 語法檢查（Node 22）。
- 新增 `requirements-dev.txt`：CI 用最小依賴集（重的 AI 模型都是延遲載入，所以整個 FastAPI app 可直接 import 測試）。
- 新增 38 條測試（`tests/`）：
  - `test_play_stats.py` — 點唱統計：計數、排序、持久化、壞檔復原。
  - `test_favorites.py` — 收藏：新增/移除/切換、持久化、排序。
  - `test_storage.py` — 歌曲快取：metadata/歌詞/音高讀取、刪除。
  - `test_queue_manager.py` — 佇列：快取秒播、插播、切歌、歷史、控制參數夾限（用假流水線隔離下載/AI）。
  - `test_api.py` — FastAPI 端點整合測試（TestClient）。

### 文件
- 新增 `docs/USER_GUIDE.md`（完整使用說明書）、`docs/ROADMAP.md`（商用 KTV 功能對照清單）、本進度日誌。
- README 更新：我的最愛、開發/測試章節、文件連結。

### 下一步（建議下輪迭代）
1. 已唱歷史分頁（資料已在 `queue_manager.history`，只差 API 曝露與 UI）。
2. 整首唱完的總評分結算畫面（舞台端已有即時評分事件）。
3. 快取管理 UI + 錯誤歌曲重新處理。
