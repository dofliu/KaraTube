"""
哪些動作要櫃檯解鎖 (Access Policy)

這是櫃檯管理鎖（`staff_lock.py`）的清單那一半：一條一條列出每個會改變狀態的
API 屬於「唱歌的事」還是「機器的事」。

**為什麼是白名單兩張表，而不是「預設鎖起來、例外放行」？**

因為兩種漏掉的代價不對稱。漏掉一條該鎖的，結果是那條路在店裡沒受保護
（跟這個功能加進來之前一樣）；漏掉一條不該鎖的，結果是**包廂裡沒有人能唱歌**
—— 客人按下切歌，畫面跳出一個要密碼的鎖頭，而櫃檯在別的樓層。

所以兩張表都要列滿，而 `tests/test_access_policy.py` 會把 app 上每一條會改變
狀態的路由抓出來比對：新加的路由沒有歸類就紅燈。這條測試才是這個模組的主體
—— 三個月後加一條 `POST /api/xxx` 的人不會記得有這個檔案，但他會看到紅燈。

**判準**（歸類的時候照這個順序問）：

1. 這是客人正在唱歌的動作嗎（點歌、切歌、調音、評分、收藏、查歌、
   自己那一次錄音）？→ 一律 OPEN。不管它多「危險」。
2. 這個動作會影響到**下一組客人**，或者做完就回不去了（刪曲庫、清跨場次的
   統計、改機台設定、排程、清空所有錄音）？→ PROTECTED。
3. 這本來就是**櫃檯的工具**（包廂計時、舞台訊息、換一批客人時重排輪序）？
   → PROTECTED。

**唯讀的一律不鎖。** GET 不在任何一張表上：看得到不會弄壞任何東西，
而鎖住清單只會讓「我想看看曲庫有多大」也要找櫃檯。
（`test_access_policy.py` 有一條測試釘住「保護清單裡沒有 GET」。）

**WebSocket 不在這裡管。** `/ws` 收的四種訊息（TIME_UPDATE、CONTROL、
SOUND_EFFECT、SCORE_EVENT、SONG_ENDED）全部屬於第 1 類，沒有一條是被鎖的動作，
所以沒有「從 WebSocket 繞過去」這個洞。哪天要從 WS 收機器層級的指令，
那條指令要自己走一次這裡的判準。
"""
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# (方法, 路徑樣板, 這個動作叫什麼)
# 第三欄是擋下來的時候要對使用者說的那句話裡的主詞 ——「這個動作需要櫃檯解鎖」
# 比不上「刪除曲庫歌曲需要櫃檯解鎖」：後者讓客人知道自己碰到的是機器的事。
PROTECTED_ROUTES: Tuple[Tuple[str, str, str], ...] = (
    # --- 曲庫（影響下一組客人）---
    ("DELETE", "/api/cache/{song_id}", "刪除曲庫歌曲"),
    ("POST", "/api/cache/{song_id}/reprocess", "重新處理歌曲"),

    # --- 排程預處理（整台機器的 CPU）---
    ("POST", "/api/batch", "新增排程處理清單"),
    ("DELETE", "/api/batch", "清空排程紀錄"),
    ("POST", "/api/batch/force", "立刻開始排程處理"),
    ("POST", "/api/batch/{job_id}/retry", "重試排程項目"),
    ("POST", "/api/batch/{job_id}/cancel", "取消排程項目"),
    ("DELETE", "/api/batch/{job_id}", "刪除排程項目"),

    # --- 跨場次的資料 ---
    ("DELETE", "/api/rankings", "清空點唱排行"),
    ("DELETE", "/api/history", "清空已唱歷史"),
    ("DELETE", "/api/recordings", "清空所有錄音"),
    ("DELETE", "/api/recordings/mp3", "清空 MP3 轉檔快取"),
    ("POST", "/api/recordings/mp3/recheck", "重新偵測 MP3 轉檔能力"),

    # --- 機台設定 ---
    ("POST", "/api/settings", "修改系統設定"),
    ("DELETE", "/api/settings", "恢復原廠設定"),
    ("POST", "/api/settings/apply-defaults", "套用預設調音"),

    # --- 櫃檯本來就在用的工具 ---
    ("POST", "/api/room/start", "開始包廂計時"),
    ("POST", "/api/room/extend", "包廂續時"),
    ("POST", "/api/room/pause", "暫停包廂計時"),
    ("POST", "/api/room/resume", "繼續包廂計時"),
    ("POST", "/api/room/stop", "結束包廂計時"),
    ("POST", "/api/marquee", "發布舞台訊息"),
    ("DELETE", "/api/marquee", "清除舞台訊息"),
    ("DELETE", "/api/marquee/{message_id}", "刪除舞台訊息"),
    # 「重新排」是換一批客人的動作：它會把每個人「今晚唱過幾首」歸零，
    # 也就是把還沒輪到的那幾位排回隊伍後面。做這件事的人是櫃檯。
    ("POST", "/api/rotation/reset", "重設輪唱順序"),
)

# 明確**不鎖**的（列出來是為了讓新路由必須二選一，而不是預設落進某一邊）。
# 第三欄是理由 —— 哪天有人想把某一條移到上面那張表，先讀這一句。
OPEN_ROUTES: Tuple[Tuple[str, str, str], ...] = (
    # --- 點歌與播放：客人付錢就是為了做這些 ---
    ("POST", "/api/queue/add", "點歌"),
    ("POST", "/api/queue/reorder", "調整佇列順序"),
    ("POST", "/api/queue/skip", "切歌"),
    ("POST", "/api/queue/restart", "重唱"),
    ("DELETE", "/api/queue/{queue_id}", "從佇列刪歌"),
    ("POST", "/api/queue/{queue_id}/retry", "失敗的歌重試"),
    ("POST", "/api/control", "調音量與效果"),
    ("POST", "/api/seek", "跳轉播放位置"),
    ("POST", "/api/sound-effect", "罐頭音效"),
    ("POST", "/api/autofill/random", "隨機點歌"),
    # --- 成績與收藏：都是這一場的人自己的東西 ---
    ("POST", "/api/scores", "上傳演唱成績"),
    ("POST", "/api/scores/duet", "上傳對唱成績"),
    ("POST", "/api/favorites/toggle", "收藏歌曲"),
    ("DELETE", "/api/favorites/{song_id}", "取消收藏"),
    # --- 錄音：自己那一次 ---
    # 單筆刪除刻意不鎖：唱壞的那一次是本人最想立刻刪掉的東西，
    # 而要他去找櫃檯才刪得掉，只會讓他下次不敢開錄音。
    # 「清空全部」才鎖（那一顆按下去連別人的都沒了）。
    ("POST", "/api/recordings", "上傳錄音"),
    ("DELETE", "/api/recordings/{rec_id}", "刪除自己的錄音"),
    ("POST", "/api/recordings/{rec_id}/pin", "標記保留錄音"),
    ("POST", "/api/recordings/{rec_id}/share", "產生分享連結"),
    ("DELETE", "/api/share/{token}", "撤銷分享連結"),
    # --- 鎖自己：解鎖要 PIN、上鎖不需要任何東西（見 staff_lock.py 第 3 點），
    #     所以這幾條一定不能被自己擋住，否則鎖上之後連解鎖的門都敲不了。 ---
    ("POST", "/api/staff-lock/unlock", "解鎖"),
    ("POST", "/api/staff-lock/lock", "上鎖"),
    ("POST", "/api/staff-lock/pin", "設定櫃檯密碼"),
    ("POST", "/api/staff-lock/disable", "停用櫃檯管理鎖"),
    ("POST", "/api/staff-lock/auto-lock", "調整自動上鎖時間"),
)

# 唯讀的方法一律不鎖（見模組說明）。
READ_ONLY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _segments(path: str) -> List[str]:
    return [s for s in str(path or "").split("/") if s]


def _matches(template: str, path: str) -> bool:
    """`/api/cache/{song_id}` 對得上 `/api/cache/abc123` 嗎？"""
    tpl = _segments(template)
    got = _segments(path)
    if len(tpl) != len(got):
        return False
    for t, g in zip(tpl, got, strict=True):
        if t.startswith("{") and t.endswith("}"):
            if not g:
                return False
            continue
        if t != g:
            return False
    return True


def _find(table: Sequence[Tuple[str, str, str]], method: str,
          path: str) -> Optional[Tuple[str, str, str]]:
    want = str(method or "").upper()
    for row in table:
        if row[0] == want and _matches(row[1], path):
            return row
    return None


def requires_unlock(method: str, path: str) -> bool:
    """這一個請求需要櫃檯解鎖嗎？認不得的一律 False（見模組說明的不對稱）。"""
    if str(method or "").upper() in READ_ONLY_METHODS:
        return False
    return _find(PROTECTED_ROUTES, method, path) is not None


def action_label(method: str, path: str) -> str:
    """擋下來的時候，那句話裡的主詞。認不得就給一個中性的說法。"""
    row = _find(PROTECTED_ROUTES, method, path)
    return row[2] if row else "這個動作"


def describe(method: str, path: str) -> Dict[str, str]:
    """403 的內容。訊息裡一定要有「怎麼解」，不然客人只會一直按那顆按鈕。"""
    label = action_label(method, path)
    return {
        "code": "staff_locked",
        "action": label,
        "detail": f"{label}需要櫃檯解鎖（點右上角 🔒 輸入櫃檯密碼）",
    }


def classify(method: str, path: str) -> str:
    """`protected` / `open` / `unclassified`。給守門測試用。"""
    if _find(PROTECTED_ROUTES, method, path):
        return "protected"
    if _find(OPEN_ROUTES, method, path):
        return "open"
    return "unclassified"


def unclassified_routes(routes: Iterable[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """
    這些 (方法, 路徑樣板) 裡有哪些還沒歸類。

    給 `tests/test_access_policy.py` 用：它把 FastAPI app 上每一條會改變狀態的
    路由丟進來，有漏的就紅燈，順便把那條路由的名字印在錯誤訊息裡。
    """
    out: List[Tuple[str, str]] = []
    for method, path in routes:
        if str(method or "").upper() in READ_ONLY_METHODS:
            continue
        if classify(method, path) == "unclassified":
            out.append((str(method).upper(), path))
    return out
