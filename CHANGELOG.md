# 更新日誌 (Changelog)

本檔記錄每個發布版本的變更。格式參考 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.1.0/)，
版本號遵循 [語意化版本](https://semver.org/lang/zh-TW/)。

逐次迭代的開發細節（為什麼這樣做、踩到什麼）記在 [docs/PROGRESS_LOG.md](docs/PROGRESS_LOG.md)。

> 版本號的唯一真相來源是 `backend/version.py`。發布流程會檢查 git 標籤、
> `backend/version.py` 與本檔最上面那一筆三者一致，對不上就不給發。

## [1.1.0] - 2026-09-07

### 新增

- **麥克風自動增益 (Mic AGC)**：換人唱不用重調麥克風音量 —— 機器自己把每個人的
  收音電平拉到同一個目標（預設 -18 dBFS）。前饋量測（量增益節點之前的原始訊號）、
  安靜時絕不加大（噪音閘門 -45 dBFS）、要降立刻降（0.25s）要升慢慢升（3s）、
  削峰保護（預估輸出過 -6 dBFS 就用 0.06s 壓下來）、加成上限 +9 dB
  且多人模式收到 +6 dB。舞台 🎤 徽章、音訊面板的即時收音電平表，
  以及唱畢結算的「拿近一點／拿遠一點」建議。
- 系統設定頁新增 **麥克風自動增益**（開關、目標收音電平）。
- 舞台音訊面板（按 `S`）新增 **📶 收音電平表**：太小聲偏左灰、正常青色、快削峰轉粉紅。
  麥克風沒插好、選錯裝置、靜音鍵沒開，在這裡三秒就看得出來。

### 變更

- 麥克風處理鏈多一段 `micAgcGain`，位置在 de-esser 之後、limiter **之前**
  （自動增益判斷失誤時還有 limiter 擋著），與使用者的麥克風音量各走一個節點。
- `PitchEngine` 多開放 `measureRms()` 與逐幀的 `frame.rms`：自動增益與電平表
  沿用評分心跳已經抓過的波形，不再重複抓一次。

### 測試

- 新增 `frontend/tests/mic-agc.test.js`（24 條）：安全規則（靜音不加大、不對稱曲線、
  削峰保護不是「不准再降」的地板）、遲滯收斂、幀率無關性、壞資料防護。
- 後端 232 條、前端 67 條全綠。

## [1.0.0] - 2026-09-07

第一個正式發布版。商用 KTV 點歌機的功能對照清單（見 `docs/ROADMAP.md`）全數完成。

### 新增（本次發布週期）

- **導唱音量自動 ducking**：導唱人聲跟著唱的人走 —— 唱穩了自動退到背景，
  走音或忘詞時立刻回來。不對稱曲線（慢退 1.6s / 快回 0.22s）＋ Schmitt 遲滯門檻
  ＋ 3 秒暖機 ＋ 深度上限 0.95（永遠留安全網）。舞台徽章與結算「導唱獨立度」。
- **Docker 一鍵部署**：`Dockerfile` + `docker-compose.yml`，快取與模型權重各自掛 volume，
  非 root 執行，`/api/health` 健康檢查；GPU 版只要換底層映像。
- **版本號與發布流程**：`backend/version.py` 為唯一來源，`GET /api/version` 可查，
  設定頁頁尾顯示；打 `v*` 標籤自動驗證（跑完整 CI ＋ 比對版本號）並打包 release。
- **部署設定環境變數化**：`KARATUBE_HOST` / `KARATUBE_PORT` / `KARATUBE_PUBLIC_HOST` /
  `KARATUBE_PUBLIC_PORT` / `KARATUBE_CACHE_DIR`，容器與反向代理情境下
  QR code 與「手機點歌」網址才會印對。
- 新增 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) 安裝與部署說明。

### 這一版包含的完整功能

- YouTube / YouTube Music 隨選點歌（關鍵字或網址），處理過的歌永久快取秒播
- Demucs v4 AI 人聲伴奏分離，導唱人聲 0~100% 無段調節
- LRC 主導、聲學校正的逐字時間軸；雙行 KTV 變色字幕與倒數預備光點
- 即時音準導唱線與 JOYSOUND / DAM 風格評分（Combo、PERFECT / GREAT / GOOD）
- 唱畢結算：總分、SSS~C 等級、音準率、最大 Combo、個人最佳、擊敗比例、段落評分
- 練唱模式：A-B 區段循環、自動副歌偵測、段落跳轉
- 升降 Key（±6 半音）、男調/女調一鍵切換、殘響/回音四參數與三種效果風格
- EBU R128 自動音量平衡（ITU-R BS.1770-4 K 加權 + 雙重閘門）
- 曲庫分類瀏覽（語言別／歌手自動判定）、新歌榜、推薦歌單、熱門排行、我的最愛、已唱歷史
- 雙螢幕輸出（點歌台 + 舞台）、手機掃碼點歌、多人包廂暱稱
- 快取管理與錯誤歌曲重新處理、系統設定頁
- CI：後端 `compileall` + `ruff` + 231 條 pytest，前端 `node --check` + 43 條 `node --test`

[1.1.0]: https://github.com/dofliu/KaraTube/releases/tag/v1.1.0
[1.0.0]: https://github.com/dofliu/KaraTube/releases/tag/v1.0.0
