# KaraTube — 一鍵部署映像
#
# 預設是 CPU 版（誰都跑得起來，處理一首歌大約 5~15 分鐘）。
# 有 NVIDIA GPU 的話改用 CUDA 底層映像重建，處理時間會掉到 1~3 分鐘：
#
#   docker build --build-arg BASE_IMAGE=nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04 \
#                --build-arg PYTHON_APT=1 -t karatube:gpu .
#   docker run --gpus all -p 8080:8080 -v karatube-cache:/data karatube:gpu
#
# 設計重點：
#   * 依賴分兩層裝（requirements.txt 先 COPY），改程式碼不用重裝 torch —— 那一層要幾分鐘。
#   * 快取目錄指到 /data 並宣告成 volume：重建映像不會把整個曲庫洗掉。
#   * 跑非 root：這台機器要接進包廂的區網，不需要 root 的東西就不要給。

ARG BASE_IMAGE=python:3.11-slim
FROM ${BASE_IMAGE}

# CUDA 底層映像沒有 python，用這個 build arg 讓它自己裝一份（python:slim 則跳過）
ARG PYTHON_APT=0

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    KARATUBE_IN_CONTAINER=1 \
    KARATUBE_CACHE_DIR=/data \
    KARATUBE_HOST=0.0.0.0 \
    KARATUBE_PORT=8080

# ffmpeg 是硬需求（下載、轉檔、響度量測都要）；
# curl 給 HEALTHCHECK 用；git 是某些模型下載流程會用到的。
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg curl git ca-certificates \
    && if [ "$PYTHON_APT" = "1" ]; then \
         apt-get install -y --no-install-recommends python3 python3-pip python3-venv && \
         ln -sf /usr/bin/python3 /usr/local/bin/python && \
         ln -sf /usr/bin/pip3 /usr/local/bin/pip; \
       fi \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依賴層：只在 requirements.txt 變動時重跑
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 應用層
COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY run.py rebuild_lyrics.py README.md LICENSE ./

# CI 打包時塞 git short SHA，用來分辨同版本號的不同建置
ARG KARATUBE_BUILD=""
ENV KARATUBE_BUILD=${KARATUBE_BUILD}

# 曲庫快取與設定檔都在這裡，務必掛成 volume。
# ~/.cache 一定要先在映像裡建好並設好擁有者：Docker 掛 named volume 時會沿用
# 映像裡那個目錄的擁有者，沒先建的話 volume 會是 root 的，
# 非 root 的行程就下載不了 Whisper / Demucs 權重（而且錯誤訊息很難懂）。
RUN mkdir -p /data && useradd --create-home --uid 10001 karatube \
    && mkdir -p /home/karatube/.cache \
    && chown -R karatube:karatube /app /data /home/karatube
VOLUME ["/data"]
USER karatube

EXPOSE 8080

# 只打 /api/health：那支端點不碰模型也不碰磁碟，
# 拿它當健康檢查才不會在處理歌曲吃滿 CPU 時被誤判成掛掉。
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8080/api/health || exit 1

CMD ["python", "run.py"]
