# KaraTube 安裝與部署說明

給「要把這台機器真的架起來給人唱」的人看的。
日常操作（怎麼點歌、怎麼調音）請看 [使用說明書](USER_GUIDE.md)。

- [1. 先決定跑在哪](#1-先決定跑在哪)
- [2. Docker 一鍵部署（建議）](#2-docker-一鍵部署建議)
- [3. 直接裝在主機上](#3-直接裝在主機上)
- [4. 環境變數](#4-環境變數)
- [5. 讓手機連得進來](#5-讓手機連得進來)
- [6. 資料與備份](#6-資料與備份)
- [7. 升級與版本](#7-升級與版本)
- [8. 部署常見問題](#8-部署常見問題)

---

## 1. 先決定跑在哪

KaraTube 是「一台伺服器 + 兩個瀏覽器畫面」的架構：伺服器負責下載與 AI 處理，
舞台大螢幕與點歌台（含手機）都只是連進來的網頁。所以伺服器放哪決定了整體體驗。

| 情境 | 建議 |
|---|---|
| 家裡包廂、一台電腦接電視 | 直接裝在那台電腦上（[第 3 節](#3-直接裝在主機上)），舞台開全螢幕 |
| 家用伺服器 / NAS / 小主機常駐 | Docker（[第 2 節](#2-docker-一鍵部署建議)），電視接一台便宜的機器開網頁就好 |
| 有 NVIDIA 顯卡 | 一定要用得到：處理一首歌從 5~15 分鐘掉到 1~3 分鐘 |

**硬體最低要求**

- CPU：4 核以上（純 CPU 也能跑，只是處理新歌慢）
- 記憶體：8 GB（Demucs 分離時的尖峰約 4~6 GB）
- 磁碟：每首歌約 60~120 MB（1080p MV + 音軌）。100 首約 10 GB。
- GPU（選配）：NVIDIA、6 GB VRAM 以上，需安裝驅動與 NVIDIA Container Toolkit

**軟體需求**

- Docker 部署：Docker Engine 24+ 與 Docker Compose v2
- 直接安裝：Python 3.10+、**FFmpeg**（必要，`ffmpeg -version` 要叫得出來）

---

## 2. Docker 一鍵部署（建議）

```bash
git clone https://github.com/dofliu/KaraTube.git
cd KaraTube

# 手機要能掃 QR code 連進來的話，先告訴它這台機器在區網裡的 IP
echo "KARATUBE_PUBLIC_HOST=192.168.1.50" > .env

docker compose up -d
docker compose logs -f          # 看啟動與處理進度
```

啟動後：

- 點歌台　`http://<這台機器的IP>:8080`
- 舞台　　`http://<這台機器的IP>:8080/player.html`

> 第一次建映像會裝 PyTorch，視網路要 5~15 分鐘、映像約 4~6 GB。
> 之後改程式碼重建只會重跑最後一層（依賴層有 layer cache）。

### GPU 版

需要先裝好 NVIDIA 驅動與 [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)。
把 `docker-compose.yml` 裡的 `deploy.resources` 區塊取消註解，並改用 CUDA 底層映像：

```yaml
    build:
      context: .
      args:
        BASE_IMAGE: nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04
        PYTHON_APT: "1"
```

然後 `docker compose up -d --build`。進去確認有吃到 GPU：

```bash
curl -s http://localhost:8080/api/info | grep device      # 應該是 "cuda"
```

### 常用指令

```bash
docker compose up -d --build     # 更新程式碼後重建並重啟
docker compose restart           # 只重啟（改了設定頁的模型選擇時要做）
docker compose logs -f --tail=100
docker compose down              # 停止（曲庫留在 volume 裡）
docker compose down -v           # 停止並「刪光曲庫與所有紀錄」（小心）
```

---

## 3. 直接裝在主機上

```bash
git clone https://github.com/dofliu/KaraTube.git
cd KaraTube

python -m venv .venv
source .venv/bin/activate         # Windows：.venv\Scripts\activate

pip install -r requirements.txt
python run.py
```

`run.py` 會印出點歌台與舞台的網址，並自動開瀏覽器（不想開就設
`KARATUBE_OPEN_BROWSER=0`）。

**FFmpeg** 要能從命令列直接叫到：

- Windows：下載 FFmpeg 後把 `bin` 加進 PATH
- macOS：`brew install ffmpeg`
- Ubuntu/Debian：`sudo apt install ffmpeg`

### 開機自動啟動（Linux systemd）

```ini
# /etc/systemd/system/karatube.service
[Unit]
Description=KaraTube KTV Server
After=network-online.target

[Service]
Type=simple
User=karatube
WorkingDirectory=/opt/KaraTube
Environment=KARATUBE_OPEN_BROWSER=0
Environment=KARATUBE_PUBLIC_HOST=192.168.1.50
ExecStart=/opt/KaraTube/.venv/bin/python run.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now karatube
sudo journalctl -u karatube -f
```

---

## 4. 環境變數

全部都是選填，沒設就用預設值。

| 變數 | 預設 | 說明 |
|---|---|---|
| `KARATUBE_HOST` | `0.0.0.0` | 伺服器監聽位址 |
| `KARATUBE_PORT` | `8080` | 伺服器監聽 port |
| `KARATUBE_PUBLIC_HOST` | 自動偵測 | **對外公告**的位址（QR code 與「手機點歌」網址用）。容器或反向代理後面務必設定 |
| `KARATUBE_PUBLIC_PORT` | 同 `KARATUBE_PORT` | 對外公告的 port。走反向代理時填 80 / 443（會自動省略不寫進網址） |
| `KARATUBE_CACHE_DIR` | `./cache` | 曲庫與所有紀錄的位置。容器裡是 `/data` |
| `KARATUBE_OPEN_BROWSER` | 非容器才開 | `0` 不自動開瀏覽器，`1` 強制開 |
| `WHISPER_MODEL` | `small` | 歌詞辨識模型（也可在設定頁改，改完要重啟） |
| `DEMUCS_MODEL` | `htdemucs` | 人聲分離模型（同上） |
| `KARATUBE_BUILD` | 空 | 建置識別（CI 打包時塞 git short SHA），會出現在 `/api/version` |

### 反向代理（Nginx）

WebSocket 是必要的（狀態同步、字幕、評分全靠它），代理設定一定要帶 upgrade 標頭：

```nginx
server {
    listen 80;
    server_name karatube.local;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 3600s;      # 舞台端會長時間掛著，別讓代理砍連線
        client_max_body_size 0;
    }
}
```

搭配 `KARATUBE_PUBLIC_HOST=karatube.local` 與 `KARATUBE_PUBLIC_PORT=80`。

> ⚠️ 麥克風需要 **安全來源**：瀏覽器只在 `https://` 或 `http://localhost` 下給
> `getUserMedia`。舞台端如果不是跑在本機（而是從別台電腦連進來），
> 就必須架 HTTPS，否則音準評分拿不到麥克風。

---

## 5. 讓手機連得進來

1. 手機與伺服器要在**同一個區網**（同一個 Wi-Fi，且 AP 沒開「訪客隔離」）。
2. 伺服器防火牆放行 8080：
   - Windows：`netsh advfirewall firewall add rule name="KaraTube" dir=in action=allow protocol=TCP localport=8080`
   - Ubuntu：`sudo ufw allow 8080/tcp`
3. 在點歌台按「📱 手機點歌 QR」掃碼。
4. **QR code 掃出來連不上**，多半是伺服器自動偵測到的 IP 不對（有多張網卡、或跑在容器裡）
   —— 設 `KARATUBE_PUBLIC_HOST` 指定正確的區網 IP。

---

## 6. 資料與備份

所有狀態都在快取目錄（預設 `./cache`，容器裡 `/data`）：

| 檔案 / 目錄 | 內容 | 重要性 |
|---|---|---|
| `songs/` | 每首歌的 MV、音軌、歌詞、音高、metadata | 大（重抓要重跑流水線） |
| `settings.json` | 系統設定 | 小，建議備份 |
| `favorites.json` | 我的最愛 | 小，建議備份 |
| `play_stats.json` | 點唱排行 | 小，建議備份 |
| `song_history.json` | 已唱歷史 | 小 |
| `score_history.json` | 評分紀錄與個人最佳 | 小，建議備份 |
| `temp/` | 處理中的暫存 | 可隨時刪 |

只備份那幾個 JSON 就能保住「這台機器的個性」（設定、收藏、紀錄）；
`songs/` 很大，但重點過一次就會自動長回來。

```bash
# Docker volume 備份
docker run --rm -v karatube-cache:/data -v "$PWD":/backup alpine \
    tar -czf /backup/karatube-data.tar.gz -C /data .
```

磁碟吃緊時到點歌台「🗂️ 快取管理」手動刪，或在 **⚙️ 系統設定 → 快取**
設上限並開啟自動清理。

---

## 7. 升級與版本

執行中的版本可以直接查（回報問題時請附上）：

```bash
curl -s http://localhost:8080/api/version
# {"name":"KaraTube","version":"1.0.0","codename":"First Light"}
```

點歌台的 **⚙️ 系統設定** 頁尾也會顯示版本。

```bash
# 直接安裝的升級
git pull
pip install -r requirements.txt      # 依賴有變動時
# 重啟服務

# Docker 的升級
git pull
docker compose up -d --build
```

升級不會動到快取目錄，曲庫與紀錄都會留著。變更內容看 [CHANGELOG.md](../CHANGELOG.md)。

### 發布（維護者）

版本號的唯一來源是 `backend/version.py`。發布流程：

1. 更新 `backend/version.py` 的 `__version__`。
2. 在 `CHANGELOG.md` 最上面新增一筆 `## [x.y.z] - YYYY-MM-DD`。
3. `git tag -a vx.y.z -m "KaraTube x.y.z" && git push origin vx.y.z`

`.github/workflows/release.yml` 會先跑完整 CI、檢查「標籤 = version.py = CHANGELOG
最上面那筆」，通過才打包 `.tar.gz` / `.zip` 與 SHA256 校驗檔並建立 GitHub Release。
三者對不上就直接失敗 —— 發出去的東西版本號對不上是最難追的問題。

---

## 8. 部署常見問題

**Q：處理歌曲一直失敗，log 出現 `ffmpeg not found`。**
FFmpeg 沒裝或不在 PATH。Docker 映像已內建，直接安裝的話請看[第 3 節](#3-直接裝在主機上)。

**Q：舞台端沒有聲音，或按了播放沒反應。**
瀏覽器的自動播放限制。舞台畫面點一下（會有「點擊開始」提示）即可解鎖音訊。

**Q：舞台端拿不到麥克風。**
`getUserMedia` 只在 `https://` 或 `http://localhost` 下可用。
舞台端請直接跑在伺服器本機開 `http://localhost:8080/player.html`，
或替它架 HTTPS（見[第 4 節](#4-環境變數)）。

**Q：容器裡的 QR code 掃了連不上。**
容器自動偵測到的是 bridge 網段（172.17.x.x）。設 `KARATUBE_PUBLIC_HOST` 為
這台主機在區網裡的 IP。

**Q：`docker compose up` 卡在 `pip install` 很久。**
在裝 PyTorch（幾 GB）。這是一次性的，之後有 layer cache。

**Q：GPU 沒有被用到（`/api/info` 顯示 `cpu`）。**
確認：主機 `nvidia-smi` 正常、裝了 NVIDIA Container Toolkit、
compose 的 `deploy.resources` 區塊已取消註解，且映像是用 CUDA 底層重建的。

**Q：磁碟滿了。**
**⚙️ 系統設定 → 快取** 設上限並開啟自動清理（演唱中與佇列裡的歌絕對不刪），
或到「🗂️ 快取管理」手動挑著刪。

**Q：想換成別的 port。**
`KARATUBE_PORT=9000`（Docker 則改 compose 的 `ports:` 對應，
並把 `KARATUBE_PUBLIC_PORT` 設成對外那個 port）。
