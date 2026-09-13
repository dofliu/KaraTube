"""
測試用的假 ffmpeg。

MP3 轉檔那條路上有三個角色：ffmpeg、檔案系統、以及我們自己的判斷。
要測的是第三個，但沒有第一個就整條跑不起來 —— 而 CI 的映像上有沒有 ffmpeg、
那個 ffmpeg 有沒有 libmp3lame，都不是測試該去賭的事。

所以這裡放一支假的：問它編碼器就說有 libmp3lame，叫它轉檔就往最後一個參數
（ffmpeg 的規矩：輸出永遠在最後）寫幾個 bytes。失敗、逾時、空輸出都可以
換一段 script 演出來。
"""
import os
from pathlib import Path

# 一支「正常」的 ffmpeg
GOOD_FFMPEG = """
for a in "$@"; do
  if [ "$a" = "-encoders" ]; then
    echo " A..... libmp3lame           libmp3lame MP3 (MPEG audio layer 3)"
    exit 0
  fi
done
for a in "$@"; do out="$a"; done
printf 'ID3fake-mp3-payload' > "$out"
exit 0
"""

# 假 ffmpeg 是 POSIX shell script。CI 與部署目標都是 Linux，
# 但 Windows 上開發的人跑測試時要能直接跳過而不是看到一堆紅字。
POSIX_ONLY = os.name != "nt"


def write_fake_ffmpeg(path: Path, body: str = GOOD_FFMPEG) -> Path:
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return path
