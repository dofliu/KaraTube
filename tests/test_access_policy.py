"""
「哪些動作要櫃檯解鎖」的守門測試。

這一支的主體是 `test_every_mutating_route_is_classified`：它把 FastAPI app 上
**每一條會改變狀態的路由**抓出來，要求都在 `access_policy` 的兩張表之一。
三個月後加一條 `POST /api/xxx` 的人不會記得有那個檔案，但他會看到這條紅燈，
然後被迫回答一個問題：這是客人在唱歌的動作，還是機器的事？

其餘幾條釘住不能退讓的部分：唱歌的路一條都不鎖、GET 一律不鎖、
解鎖自己的那扇門不能被鎖住。
"""
import pytest

from backend.main import app
from backend.services.access_policy import (OPEN_ROUTES, PROTECTED_ROUTES, READ_ONLY_METHODS,
                                            action_label, classify, describe,
                                            requires_unlock, unclassified_routes)


def app_mutating_routes():
    """app 上每一條 (方法, 路徑樣板)，唯讀的除外。"""
    out = []
    for route in app.routes:
        methods = getattr(route, "methods", None)
        if not methods:
            continue
        for method in sorted(methods):
            if method in READ_ONLY_METHODS:
                continue
            out.append((method, route.path))
    return out


def test_every_mutating_route_is_classified():
    missing = unclassified_routes(app_mutating_routes())
    assert not missing, (
        "這幾條路由還沒歸類到 backend/services/access_policy.py：\n  "
        + "\n  ".join(f"{m} {p}" for m, p in missing)
        + "\n\n請照模組說明的判準二選一放進 PROTECTED_ROUTES 或 OPEN_ROUTES。"
    )


def test_tables_do_not_overlap():
    protected = {(m, p) for m, p, _ in PROTECTED_ROUTES}
    openish = {(m, p) for m, p, _ in OPEN_ROUTES}
    assert not (protected & openish)


def test_tables_have_no_duplicates():
    for table in (PROTECTED_ROUTES, OPEN_ROUTES):
        keys = [(m, p) for m, p, _ in table]
        assert len(keys) == len(set(keys))


def test_no_read_only_method_is_protected():
    # 看得到不會弄壞任何東西，而鎖住清單只會讓「我想看看曲庫有多大」也要找櫃檯
    assert not [row for row in PROTECTED_ROUTES if row[0] in READ_ONLY_METHODS]
    assert requires_unlock("GET", "/api/settings") is False
    assert requires_unlock("GET", "/api/cache/abc123") is False


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/queue/add"),
    ("POST", "/api/queue/skip"),
    ("POST", "/api/queue/restart"),
    ("POST", "/api/queue/reorder"),
    ("DELETE", "/api/queue/xyz"),
    ("POST", "/api/control"),
    ("POST", "/api/seek"),
    ("POST", "/api/sound-effect"),
    ("POST", "/api/scores"),
    ("POST", "/api/scores/duet"),
    ("POST", "/api/favorites/toggle"),
    ("POST", "/api/recordings"),
    ("DELETE", "/api/recordings/rec-1"),
    # 字幕對不上是包廂裡當場要修的東西，鎖起來等於「字幕歪了要先找櫃檯」
    ("POST", "/api/songs/abc123/lyric-offset"),
    ("DELETE", "/api/songs/abc123/lyric-offset"),
])
def test_singing_is_never_locked(method, path):
    """鎖錯一條的代價不是多按一次密碼，是整個晚上沒有人能唱歌。"""
    assert requires_unlock(method, path) is False


@pytest.mark.parametrize("method,path", [
    ("DELETE", "/api/cache/abc123"),
    ("POST", "/api/cache/abc123/reprocess"),
    ("POST", "/api/settings"),
    ("DELETE", "/api/settings"),
    ("DELETE", "/api/rankings"),
    ("DELETE", "/api/history"),
    ("DELETE", "/api/recordings"),
    ("POST", "/api/room/start"),
    ("POST", "/api/marquee"),
    ("POST", "/api/batch"),
    ("POST", "/api/rotation/reset"),
    ("POST", "/api/cache/abc123/rebuild-lyrics"),
])
def test_machine_level_actions_are_locked(method, path):
    assert requires_unlock(method, path) is True


def test_the_lock_never_locks_its_own_door():
    """鎖上之後連敲門的那扇門都鎖住的話，就沒有人解得開了。"""
    for path in ("/api/staff-lock/unlock", "/api/staff-lock/lock",
                 "/api/staff-lock/pin", "/api/staff-lock/disable",
                 "/api/staff-lock/auto-lock"):
        assert requires_unlock("POST", path) is False
        assert classify("POST", path) == "open"


def test_path_params_match_one_segment_only():
    assert requires_unlock("DELETE", "/api/cache/abc") is True
    assert requires_unlock("DELETE", "/api/cache") is False
    assert requires_unlock("DELETE", "/api/cache/abc/extra") is False


def test_unknown_routes_are_not_locked():
    # 認不得的一律放行：漏鎖等於回到功能加進來之前，錯鎖等於沒有人能唱歌
    assert requires_unlock("POST", "/api/something-new") is False
    assert classify("POST", "/api/something-new") == "unclassified"


def test_describe_tells_the_user_how_to_get_past_it():
    info = describe("DELETE", "/api/cache/abc123")
    assert info["code"] == "staff_locked"
    assert info["action"] == "刪除曲庫歌曲"
    assert "櫃檯" in info["detail"]
    # 認不得的路由也要給得出一句話（中介層永遠不該噴例外）
    assert action_label("POST", "/api/nope") == "這個動作"


def test_every_row_has_a_human_label():
    for method, path, label in PROTECTED_ROUTES + OPEN_ROUTES:
        assert method.isupper() and path.startswith("/api/") and label.strip()
