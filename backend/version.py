"""
版本號（唯一真相來源）

發布之後「使用者手上跑的到底是哪一版」是最常被問、也最難回答的問題：
包廂那台機器可能是三個月前 clone 的，回報的問題早就修掉了。
所以版本號要能從執行中的系統直接讀出來（`GET /api/version`、設定頁頁尾），
而不是只寫在 README 裡。

規則：
  * 這裡是唯一的來源。FastAPI 的 `version=`、`/api/version`、發布流程都讀這一個常數，
    改版只要動這一行。
  * 遵循語意化版本 MAJOR.MINOR.PATCH。CHANGELOG.md 最上面那一筆必須是這個版本
    （有測試在守，忘了更新 CHANGELOG 會被 CI 擋下來）。
  * `KARATUBE_BUILD` 環境變數可選填（CI 打包時塞 git short SHA），
    用來分辨「同一個版本號但不同 commit」的自建映像。
"""
import os

__version__ = "1.8.0"

# 版本代號：發布公告與 release 標題用，純粹是給人記的名字
VERSION_CODENAME = "Second Listen"


def build_id() -> str:
    """打包時記下的建置識別（通常是 git short SHA）。沒設就回空字串。"""
    return os.getenv("KARATUBE_BUILD", "").strip()


def version_info() -> dict:
    """`GET /api/version` 的內容。刻意只放「識別這份程式」需要的東西。"""
    info = {
        "name": "KaraTube",
        "version": __version__,
        "codename": VERSION_CODENAME,
    }
    build = build_id()
    if build:
        info["build"] = build
    return info
