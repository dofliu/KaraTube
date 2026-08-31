# KaraTube 🎤 - YouTube 隨選即唱 KTV 伴唱系統

[![CI](https://github.com/dofliu/KaraTube/actions/workflows/ci.yml/badge.svg)](https://github.com/dofliu/KaraTube/actions/workflows/ci.yml)

**KaraTube** 是一個隨時指定任何 YouTube 或 YouTube Music 歌曲，即可全自動轉換為專業 KTV 伴唱畫面的現代化卡拉OK系統。

📖 文件：[使用說明書](docs/USER_GUIDE.md)｜[功能路線圖](docs/ROADMAP.md)｜[開發進度日誌](docs/PROGRESS_LOG.md)

---

## 🌟 核心特色

1. **⚡ YouTube 隨選隨唱 (On-Demand Processing)**
   - 支援輸入歌曲關鍵字或直接貼上 YouTube / YouTube Music 網址。
   - 後端全自動流水線：下載 1080p 背景 MV $\rightarrow$ AI 分離伴奏與人聲 $\rightarrow$ 逐字時間軸歌詞對齊 $\rightarrow$ 抽取音高導唱線。
   - 智慧快取機制：點過的歌曲第二次秒播，免重複等待。

2. **🎵 AI 高品質人聲伴奏分離 (Demucs v4 / BS-RoFormer)**
   - 使用深度學習模型分離出純伴奏（Instrumental）與人聲（Vocals）。
   - 前端支援「**原唱/伴奏無段切換**」，隨時可在 0% 純伴奏到 100% 導唱原聲之間自由拉桿調節。

3. **✨ 經典雙行 KTV 逐字平滑變色字幕 (Dual-Line Sweeping Karaoke)**
   - 經典 KTV 雙行交替排版（一行正在唱，一行預備）。
   - 3、2、1 倒數預備光點提示。
   - 毫秒級字元漸變變色動畫（支援中文、英文、日文、韓文）。
   - **歌詞對齊採 LRC 主導、聲學校正**：抓多個 LRC 候選各自對齊，
     以 (scale, offset) 仿射變換修正片頭裁切與變速上傳，再把每行行首吸附到
     人聲軌真實的起唱點，行內逐字依累積人聲能量分配（不是均分）。
   - **播放端補償音訊輸出延遲**（`AudioContext.outputLatency`），
     並提供 ±1 秒即時微調：舞台端按 `←` `→`（Shift 微調 10ms、`0` 歸零），
     或從點歌台／手機的「🎬 字幕同步」滑桿調整，兩端即時同步。

4. **🎯 即時音準導唱線與評分系統 (JOYSOUND / DAM Style Scoring)**
   - 頂部音準線即時顯示旋律音符。
   - 麥克風音高實時捕捉（Autocorrelation / YIN 演算法），繪製玩家音高游標軌跡。
   - 即時 Combo 累積、音準判定（PERFECT / GREAT / GOOD）與歡呼慶祝特效。

5. **🎛️ 專業 KTV 調音與氣氛音效 (Web Audio DSP)**
   - 升降 Key 調節（-6 ~ +6 Semitones）。
   - 音樂（伴奏）音量與麥克風音量獨立調節，互不影響。
   - 麥克風效果四個獨立參數：**殘響**（空間感）、**回音音量**、
     **回音重複次數**、**回音間隔**（80~600ms）。回授量與輸出音量走不同的
     增益節點，所以「小聲但多次」跟「大聲但一次」都調得出來，回音也可以真正歸零。
   - 一鍵「乾聲」：現場破音或嘯叫時立刻關掉所有效果。
   - 罐頭氣氛音效：👏 熱烈掌聲、🎉 歡呼口哨、👎 倒喝采。

6. **🏆 熱門點唱排行 (Play Rankings)**
   - 每首歌實際上台演唱時才計數（排進佇列又被移除的不算）。
   - 點歌台「🏆 點唱排行」分頁顯示名次、累計次數與最近演唱日期，一鍵再點一次。
   - 統計存在 `cache/play_stats.json`，重開機不會歸零。

7. **⭐ 我的最愛 (Favorites)**
   - 每張歌卡右上角的星星一鍵收藏／取消收藏常唱歌曲。
   - 點歌台「⭐ 我的最愛」分頁集中管理，最新收藏排最前面，一鍵再點歌。
   - 收藏存在 `cache/favorites.json`，重開機不會消失。

8. **🕘 已唱歷史 (Sung History)**
   - 一次一筆的演唱時間序列：同一首唱三次就有三筆，顯示「唱於 今天 21:34」。
   - 點歌台「🕘 已唱歷史」分頁一鍵再點今天唱過的歌，並統計今天唱了幾首。
   - 歷史存在 `cache/song_history.json`（保留最近 500 筆），重開機不會消失。

9. **🏁 唱畢總評分結算畫面 (Score Settlement)**
   - 整首唱完舞台自動亮出成績單：總分滾動動畫、SSS~C 等級、音準率、最大 COMBO。
   - 顯示「本曲個人最佳」與「擊敗全場 XX% 的演唱」，破紀錄會標示 🎉 刷新個人最佳。
   - 結算顯示 9 秒（點擊畫面可跳過）後自動進下一首；沒人開口唱的純播放不會出結算。
   - 評分歷史與各曲個人最佳存在 `cache/score_history.json`；點歌台與手機端同步收到結算通知。
   - 音準評分與畫面渲染分離：隱藏音準線時照樣計分，結算才公平。

10. **📱 雙螢幕輸出與手機掃碼點歌**
   - **螢幕 1（點歌台 / 手機控制端）**：搜尋歌曲、管理排隊清單、調音、音效、
     音樂音量、字幕同步、音準線顯示切換、點唱排行。
   - **螢幕 2（舞台大螢幕）**：全螢幕高畫質 MV、雙行卡拉OK字幕、音準導唱線、即時評分。
   - 手機掃描 QR Code 即可連入局域網無線點歌。

---

## 🚀 快速啟動

### 1. 安裝環境需求
確保已安裝：
- **Python 3.10+**
- **FFmpeg**（系統需能直接呼叫 `ffmpeg`）
- **NVIDIA GPU**（推薦，可大幅加速 AI 分離與 Whisper 識別）

### 2. 安裝依賴
```bash
pip install -r requirements.txt
```

### 3. 一鍵啟動
```bash
python run.py
```

啟動後瀏覽器會自動開啟：
- 點歌控制台：`http://localhost:8080`
- 舞台演唱螢幕：`http://localhost:8080/player.html`
- 局域網手機點歌：在點歌台點擊「📱 手機點歌 QR」直接掃碼連線。

---

## 📂 專案目錄結構

```
KaraTube/
├── backend/
│   ├── main.py                  # FastAPI 主服務器 & WebSocket Hub
│   ├── config.py                # 系統設定 (路徑、模型、GPU配置)
│   ├── pipeline/
│   │   ├── downloader.py        # yt-dlp 影音下載器
│   │   ├── separator.py         # AI 人聲伴奏分離模組 (Demucs)
│   │   ├── lyrics_aligner.py    # LRC 取得、時間軸仿射校正、逐字時間分配
│   │   ├── vocal_activity.py    # 人聲能量包絡 / 發聲區段 / 起唱點偵測
│   │   ├── pitch_extractor.py   # 音高軌跡抽取 (F0 / MIDI)
│   │   └── song_processor.py    # 非同步協調流水線
│   └── services/
│       ├── storage.py           # 本地快取與資料庫
│       ├── play_stats.py        # 點唱次數統計（熱門排行）
│       ├── favorites.py         # 我的最愛（收藏清單）
│       ├── song_history.py      # 已唱歷史（演唱時間序列）
│       ├── score_history.py     # 評分歷史與個人最佳（唱畢結算）
│       ├── search_service.py    # YouTube 即時搜尋
│       └── queue_manager.py     # 點歌佇列與狀態廣播
│
├── frontend/
│   ├── index.html               # 點歌控制台 & 手機遙控端
│   ├── player.html              # KTV 舞台演唱全螢幕
│   ├── css/
│   │   ├── style.css            # 霓虹 KTV 主題樣式
│   │   └── karaoke.css          # KTV 雙行走字特效、音準條樣式
│   └── js/
│       ├── api.js               # REST & WebSocket 客戶端
│       ├── controller.js        # 點歌台邏輯
│       ├── player.js            # 舞台播放器核心
│       ├── karaoke-renderer.js  # 毫秒級 KTV 變色字幕渲染引擎
│       ├── pitch-engine.js      # 音準線與麥克風即時評分系統
│       └── audio-effects.js     # Web Audio 混音、升降 Key、殘響 DSP
│
├── tests/                       # 後端單元與 API 測試（pytest）
├── docs/                        # 使用說明書、路線圖、進度日誌
├── .github/workflows/ci.yml    # CI：後端測試 + 前端語法檢查
├── cache/                       # 自動生成的歌曲快取目錄
├── requirements.txt             # 完整執行依賴（含 AI 模型）
├── requirements-dev.txt         # 測試 / CI 用最小依賴
├── rebuild_lyrics.py            # 重算快取歌曲的歌詞時間軸（改對齊邏輯後用）
├── run.py                       # 一鍵啟動腳本
└── README.md
```

---

## 🧪 測試與 CI

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -v
```

重的 AI 模型（Demucs / Whisper / librosa）都是延遲載入，
所以整個 FastAPI app 可以在不裝 torch 的環境直接 import 起來測。
GitHub Actions 會在每個 PR 自動跑：後端 `compileall` + `pytest`、
前端 `node --check` 全 JS 語法檢查。

---

## 🎬 字幕同步

歌詞對得準不準由兩層決定，兩層都可以單獨檢查：

**後端對齊**（`backend/pipeline/lyrics_aligner.py`）
每首歌處理完會在 `cache/songs/<id>/alignment.json` 留下對齊診斷：

```json
{ "source": "lrc", "scale": 0.996, "offset": 0.4, "score": 0.577, "recall": 0.967, "lines": 47 }
```

- `source` — `lrc`（採用線上歌詞時間軸）或 `whisper`（找不到可信歌詞，改聽人聲轉錄）
- `scale` / `offset` — 相對原始錄音估出來的變速與偏移
- `score` — 行首落在真實起唱點的程度，**低於 0.28 會自動改走 Whisper 路徑**
- `recall` — 有多少比例的人聲被歌詞行覆蓋到，可用來抓「LRC 少了一段副歌」

改過對齊邏輯後，用下面的指令重算已快取的歌：

```bash
python rebuild_lyrics.py
```

只重算單一首、或只檢查現況不動檔案：

```bash
python rebuild_lyrics.py bu7nU9Mhpyo
```

```bash
python rebuild_lyrics.py --check
```

**播放端延遲**
`AudioContext.outputLatency` 會自動補償（Windows WASAPI 常見 40~200ms），
但藍牙喇叭、外接混音器、HDMI 電視的延遲量測不到，需要手動補：

- 舞台螢幕：`←` `→` 每次 50ms，`Shift + ← →` 每次 10ms，`0` 歸零
- 點歌台 / 手機：底部「🎬 字幕同步」滑桿

調整值會存在舞台端瀏覽器的 localStorage，下次開機自動沿用。

---

## ⌨️ 舞台螢幕快捷鍵

| 按鍵 | 功能 |
|------|------|
| `←` `→` | 字幕同步 ±50ms |
| `Shift` + `←` `→` | 字幕同步 ±10ms |
| `0` | 字幕同步歸零（只留自動延遲補償） |
| `P` | 顯示 / 隱藏音準導唱線（隱藏後 MV 畫面完整露出） |
| `S` | 開啟音訊裝置設定（麥克風 / 喇叭輸出 / 演唱模式） |
| `M` | 單人 ⇄ 多人模式切換（嘯叫時的緊急開關） |

字幕同步與音準線設定會存在舞台端瀏覽器，並即時同步到點歌台與手機端，
兩邊改都會反映到對方。

---

## 🎛️ 點歌台介面配置

底部控制列只放演唱中會一直動的東西，其餘收進「🎛️ 調音台」面板，
窄螢幕與手機會自動換行成兩欄。

| 位置 | 內容 |
|------|------|
| 控制列（常駐） | 現正播放 + 進度、重唱／播放／切歌、原唱↔伴奏、🎵 音樂音量、🎙️ 麥克風音量、🎛️ 調音台、氣氛音效 |
| 調音台面板 | 升降 Key、殘響、回音音量、回音重複、回音間隔、乾聲、字幕同步、音準導唱線開關 |

### 麥克風有回音／嘯叫時

1. **先把 🎙️ 麥克風音量拉到 0** —— 立刻切斷監聽。音準評分不受影響，
   因為 `micAnalyser` 是直接從 `micSource` 接出去的，不經過 `micGain`。
2. 打開調音台按「乾聲」，把殘響與回音歸零，再慢慢往上加。
3. 若是筆電內建麥克風配內建喇叭，`echoCancellation` 是關閉的
   （開啟會破壞歌聲與音準偵測），聲學回授無法靠軟體解決 ——
   請改用耳機監聽或外接混音器。

---

## 🎧 演唱模式與回授（嘯叫）

筆電是回授最糟的幾何：麥克風與喇叭裝在同一個機殼裡，除了空氣傳導還有
**機殼結構傳導**，這條路徑靠拉開距離解決不了。自激通常落在 2~5kHz，
所以聽起來是「尖銳」而不是低頻嗡嗡。

### 單人模式（預設）

人聲**完全不進喇叭**——`monitorGain` 設為 0，位置在殘響與回音送出之前，
所以連效果也一起靜音。回授迴路被物理性切斷，不可能嘯叫。

音準評分照常運作：`micAnalyser` 是直接接在 `micSource` 上，不經過監聽路徑。

### 多人模式

人聲外放，**必須搭配外接喇叭**。外接喇叭直接消掉機殼傳導那條路徑，
距離每加倍再少 6dB。但真正決定成敗的是**近距離收音**——
嘴巴離麥克風 3cm vs 50cm，訊噪比差 24dB，這是最大的一筆。
理想配置是外接喇叭 + 指向性手持麥克風，喇叭擺在麥克風前方朝外。

### 軟體能做的（`_buildMicChain`）

軟體只能把回授門檻往上推幾個 dB，幾何才是主因。這條鏈處理三種實際問題：

| 節點 | 作用 |
|------|------|
| `micHighpass` 110Hz | 砍掉近接效應與桌面震動傳來的低頻 |
| `micDeEss` 高棚 5.5kHz | 回授自激與齒音都集中在這帶，可調 0 ~ -12dB |
| `micLimiter` -14dB / 12:1 | 迴路增益短暫超過 1 時壓住，不讓它長大成嘯叫 |

殘響的脈衝響應也改成高頻先衰減（模擬空氣吸收）。原本是純白噪，
尾巴的高頻跟起始一樣多，人聲卷積上去會有沙沙的金屬感。

### 音訊裝置選擇

舞台螢幕按 `S`（或右上角 ⚙️）開啟。裝置 ID 綁定該台機器，
所以只能在舞台端選，不能從手機遙控。輸出裝置切換需要 Chrome 110+
（`AudioContext.setSinkId`），其他瀏覽器請從作業系統的音效設定切換。

### 現場嘯叫的處理順序

1. 舞台按 `M` 切回單人模式，人聲立刻離開喇叭。
2. 或把點歌台的 🎙️ 麥克風音量拉到 0。
3. 調音台按「乾聲」關掉殘響與回音，再慢慢往上加。
4. 「🔆 柔化」往右拉，壓掉尖銳的那一帶。
