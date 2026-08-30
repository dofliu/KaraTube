"""
測試共用設定。

CI 上不裝 torch / demucs / whisper（模組層級只需要 numpy、requests、yt-dlp），
所以整個 FastAPI app 都可以直接 import 起來測 —— 重的 AI 模型都是延遲載入。
"""
import sys
from pathlib import Path

# 讓 `import backend.*` 在任何工作目錄下都找得到
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
