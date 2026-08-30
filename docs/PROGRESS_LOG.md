# KaraTube 開發進度日誌

每次迭代完成後在最上方新增一筆，記錄做了什麼、為什麼、下一步。
功能對照總表見 [ROADMAP.md](ROADMAP.md)。

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
