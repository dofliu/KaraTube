"""錄音分享連結的單元測試。

分享連結是這套系統裡唯一「不需要任何身分就打得開」的東西，所以測的重點
不是「產得出來」，而是**什麼時候該打不開**：

  * 時間到了要真的失效（不是畫面上不顯示而已）；
  * 撤銷之後要立刻打不開（送錯人才是按撤銷的理由，慢一秒都不行）；
  * 下載次數上限只算真的下載，不算播放時的那幾個請求；
  * 同一筆錄音重複按分享不該讓已經掃過的 QR 失效；
  * 索引不能無限長大（失效的連結要自己收掉）。
"""
from datetime import datetime, timedelta

import pytest

from backend.services.share_links import (
    MAX_TTL_HOURS,
    MIN_TTL_HOURS,
    TOMBSTONE_DAYS,
    ShareLinkStore,
    clamp_max_downloads,
    clamp_ttl_hours,
)


@pytest.fixture()
def store(tmp_path):
    return ShareLinkStore(tmp_path / "recording_shares.json")


# --- 參數夾制 ---

def test_ttl_is_clamped_and_has_no_forever_option():
    assert clamp_ttl_hours(24) == 24
    assert clamp_ttl_hours(0) == 24            # 0 不是「永不過期」，是「用預設」
    assert clamp_ttl_hours(-5) == 24
    assert clamp_ttl_hours("abc") == 24
    assert clamp_ttl_hours(0.5) == MIN_TTL_HOURS
    assert clamp_ttl_hours(99999) == MAX_TTL_HOURS   # 最長 30 天，刻意沒有無限


def test_download_cap_is_clamped():
    assert clamp_max_downloads(3) == 3
    assert clamp_max_downloads(0) == 0         # 0 = 不限（時效還在）
    assert clamp_max_downloads(-1) == 0
    assert clamp_max_downloads("x") == 0
    assert clamp_max_downloads(10_000) == 999


# --- 基本流程 ---

def test_create_then_resolve(store):
    share = store.create("20260913-213045-a1b2c3", ttl_hours=24)
    assert share["status"] == "active"
    assert share["expires_in_seconds"] > 23 * 3600

    found, reason = store.resolve(share["token"])
    assert reason == "ok"
    assert found["recording_id"] == "20260913-213045-a1b2c3"


def test_tokens_are_unguessable_and_unique(store):
    tokens = {store.create(f"rec-{i}", reuse=False)["token"] for i in range(20)}
    assert len(tokens) == 20
    assert all(len(t) >= 16 for t in tokens)


@pytest.mark.parametrize("bad", ["", "../../etc/passwd", "short", "has space",
                                 "a/b", "x" * 100, None])
def test_malformed_tokens_are_rejected_before_lookup(store, bad):
    entry, reason = store.resolve(bad)
    assert entry is None and reason == "not_found"


# --- 失效的四種原因 ---

def test_expired_link_stops_working(store):
    share = store.create("rec-1", ttl_hours=1)
    # 把到期時間往回撥，模擬「一小時後再點」
    store._entries[0]["expires_at"] = (datetime.now() - timedelta(minutes=1)).isoformat()

    entry, reason = store.resolve(share["token"])
    assert entry is None
    # 過期與「不存在」要分得出來：前者叫人重發一個，後者叫人檢查網址
    assert reason == "expired"


def test_revoked_link_stops_working_immediately(store):
    share = store.create("rec-1")
    assert store.revoke(share["token"]) is True

    entry, reason = store.resolve(share["token"])
    assert entry is None and reason == "revoked"
    # 撤銷第二次不是錯誤（使用者會連按）
    assert store.revoke(share["token"]) is True


def test_download_cap_counts_downloads_not_plays(store):
    share = store.create("rec-1", max_downloads=2)
    token = share["token"]

    # 播放頁被打開幾次都不該扣額度 —— 播一首歌不只一個請求
    for _ in range(5):
        store.note_view(token)
    assert store.resolve(token)[1] == "ok"
    assert store.resolve(token)[0]["downloads_left"] == 2

    store.note_download(token)
    assert store.resolve(token)[0]["downloads_left"] == 1
    store.note_download(token)

    entry, reason = store.resolve(token)
    assert entry is None and reason == "exhausted"


def test_unlimited_downloads_reports_none_not_zero(store):
    share = store.create("rec-1", max_downloads=0)
    store.note_download(share["token"])
    # None 是「不限」，0 會被畫面讀成「不能再下載了」
    assert store.resolve(share["token"])[0]["downloads_left"] is None


# --- 重複分享 ---

def test_sharing_twice_reuses_the_live_link(store):
    first = store.create("rec-1")
    second = store.create("rec-1")
    # 已經掃進別人手機的 QR 必須繼續有效
    assert first["token"] == second["token"]


def test_new_link_can_be_forced(store):
    first = store.create("rec-1")
    second = store.create("rec-1", reuse=False)
    assert first["token"] != second["token"]
    # 沒有明確撤銷的話舊的還活著（撤銷是呼叫端的決定）
    assert store.resolve(first["token"])[1] == "ok"


def test_reuse_skips_dead_links(store):
    first = store.create("rec-1")
    store.revoke(first["token"])
    second = store.create("rec-1")
    assert second["token"] != first["token"]
    assert second["status"] == "active"


# --- 與錄音的連動 ---

def test_revoke_for_recording_kills_every_link(store):
    a = store.create("rec-1", reuse=False)
    b = store.create("rec-1", reuse=False)
    other = store.create("rec-2")

    assert store.revoke_for_recording("rec-1") == 2
    assert store.resolve(a["token"])[1] == "revoked"
    assert store.resolve(b["token"])[1] == "revoked"
    assert store.resolve(other["token"])[1] == "ok"       # 別人的不受影響


def test_prune_marks_links_whose_recording_is_gone(store):
    alive = store.create("rec-alive")
    evicted = store.create("rec-evicted")

    # 配額把 rec-evicted 擠掉了（沒有人按刪除，所以只能靠對帳發現）
    assert store.prune(["rec-alive"]) == 1
    assert store.resolve(alive["token"])[1] == "ok"
    # 「錄音不在了」要跟「連結不存在」分開講：後者會被理解成網址打錯，
    # 於是掃過 QR 的人會一直重掃
    assert store.resolve(evicted["token"])[1] == "gone"


def test_dead_links_are_kept_a_while_then_swept(store):
    share = store.create("rec-1")
    store.revoke(share["token"])

    # 剛撤銷的要留著，點下去才看得到「已撤銷」而不是「找不到頁面」
    store.prune(["rec-1"])
    assert store.resolve(share["token"])[1] == "revoked"

    old = (datetime.now() - timedelta(days=TOMBSTONE_DAYS + 1)).isoformat()
    store._entries[0]["revoked_at"] = old
    store._entries[0]["expires_at"] = old
    store._entries[0]["created_at"] = old
    store.prune(["rec-1"])
    assert store.resolve(share["token"])[1] == "not_found"


# --- 持久化 ---

def test_links_survive_a_restart(tmp_path):
    path = tmp_path / "recording_shares.json"
    share = ShareLinkStore(path).create("rec-1", ttl_hours=48)

    # 重開伺服器不該讓已經發出去的連結全部死掉
    reopened = ShareLinkStore(path)
    assert reopened.resolve(share["token"])[1] == "ok"


def test_broken_index_does_not_take_the_server_down(tmp_path):
    path = tmp_path / "recording_shares.json"
    path.write_text("{ not json at all", encoding="utf-8")
    store = ShareLinkStore(path)
    assert store.list_all() == []
    assert store.create("rec-1")["status"] == "active"


def test_index_entries_without_a_token_are_dropped(tmp_path):
    path = tmp_path / "recording_shares.json"
    path.write_text('{"shares": [{"recording_id": "rec-1"}, {"token": "bad/one"}]}',
                    encoding="utf-8")
    assert ShareLinkStore(path).list_all() == []


def test_active_count_only_counts_usable_links(store):
    live = store.create("rec-1")
    dead = store.create("rec-2")
    store.revoke(dead["token"])
    assert store.active_count() == 1
    assert store.resolve(live["token"])[1] == "ok"
