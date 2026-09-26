"""包廂名冊與房號（backend/services/rooms.py）"""
import json

import pytest

from backend.services import rooms


# --- 房號收斂 ---

def test_normalize_id_lowercases_and_trims():
    assert rooms.normalize_id("  Room-1 ") == "room-1"
    assert rooms.normalize_id("101") == "101"


def test_normalize_id_rejects_instead_of_repairing():
    """砍掉非法字元會讓使用者打的房號變成另一間（見模組說明）。"""
    for bad in ["房間A", "ro om", "room/1", "", None, "-lead", "x" * 25, "Room_1"]:
        assert rooms.normalize_id(bad) == ""


def test_slug_from_name():
    assert rooms.slug_from_name("101 包廂") == "101"
    assert rooms.slug_from_name("Party Room") == "party-room"
    # 整個名字都是中文 → 退回 room-N（音譯出來沒有人念得出來）
    assert rooms.slug_from_name("小包廂").startswith("room-")


def test_slug_from_name_avoids_collisions():
    assert rooms.slug_from_name("VIP", taken=["vip"]) == "vip-2"
    assert rooms.slug_from_name("VIP", taken=["vip", "vip-2"]) == "vip-3"
    assert rooms.slug_from_name("包廂", taken=["room-2"]) == "room-3"


def test_clean_name_falls_back_to_id():
    assert rooms.clean_name("  VIP  大包 ", "x") == "VIP 大包"
    assert rooms.clean_name("", "room-3") == "room-3"
    assert len(rooms.clean_name("名" * 50, "x")) == rooms.MAX_NAME_LEN


# --- 狀態檔位置（升級不掉資料）---

def test_default_room_keeps_legacy_paths(tmp_path):
    """決定二：default 沿用 cache/ 原路徑，升級的機器一格都不掉。"""
    assert rooms.state_path(tmp_path, "default", "room_timer.json") == \
        tmp_path / "room_timer.json"
    assert rooms.room_dir(tmp_path, "default") is None


def test_other_rooms_get_their_own_folder(tmp_path):
    assert rooms.state_path(tmp_path, "vip", "room_timer.json") == \
        tmp_path / "rooms" / "vip" / "room_timer.json"


# --- 名冊 ---

def test_roster_always_has_default(tmp_path):
    roster = rooms.RoomRoster(tmp_path / "rooms.json")
    assert roster.ids() == ["default"]
    assert roster.get("default")["name"] == rooms.DEFAULT_ROOM_NAME


def test_roster_persists(tmp_path):
    path = tmp_path / "rooms.json"
    rooms.RoomRoster(path).add("101 包廂")
    again = rooms.RoomRoster(path)
    assert again.ids() == ["default", "101"]
    assert again.get("101")["name"] == "101 包廂"


def test_roster_rejects_duplicate_id(tmp_path):
    roster = rooms.RoomRoster(tmp_path / "rooms.json")
    roster.add("VIP", room_id="vip")
    with pytest.raises(rooms.RoomError) as exc:
        roster.add("另一間", room_id="vip")
    assert exc.value.reason == "room_exists"


def test_roster_rejects_bad_id(tmp_path):
    roster = rooms.RoomRoster(tmp_path / "rooms.json")
    with pytest.raises(rooms.RoomError) as exc:
        roster.add("x", room_id="包廂 A")
    assert exc.value.reason == "bad_room_id"


def test_default_room_cannot_be_removed(tmp_path):
    roster = rooms.RoomRoster(tmp_path / "rooms.json")
    with pytest.raises(rooms.RoomError) as exc:
        roster.remove("default")
    assert exc.value.reason == "default_room_locked"


def test_default_room_can_be_renamed(tmp_path):
    roster = rooms.RoomRoster(tmp_path / "rooms.json")
    assert roster.rename("default", "大包")["name"] == "大包"


def test_roster_limit(tmp_path):
    roster = rooms.RoomRoster(tmp_path / "rooms.json")
    for i in range(rooms.MAX_ROOMS - 1):
        roster.add(f"r{i}", room_id=f"r{i}")
    with pytest.raises(rooms.RoomError) as exc:
        roster.add("one-more", room_id="one-more")
    assert exc.value.reason == "too_many_rooms"


def test_broken_roster_falls_back_to_single_room(tmp_path):
    """壞檔不是致命傷：退回「只有一間 default」，那正好是升級前的世界。"""
    path = tmp_path / "rooms.json"
    path.write_text("{ 這不是 json", encoding="utf-8")
    roster = rooms.RoomRoster(path)
    assert roster.ids() == ["default"]


def test_roster_drops_unusable_rows(tmp_path):
    path = tmp_path / "rooms.json"
    path.write_text(json.dumps({"rooms": [
        {"id": "ok", "name": "好的"},
        {"id": "BAD ID", "name": "壞的"},
        {"id": "ok", "name": "重複"},
        "不是 dict",
    ]}), encoding="utf-8")
    roster = rooms.RoomRoster(path)
    # default 補在最前面：它是「不帶 ?room= 的那一間」，清單上就該排第一
    assert roster.ids() == ["default", "ok"]


# --- Registry ---

def test_registry_builds_one_bundle_per_room(tmp_path):
    made = []

    def factory(room_id):
        made.append(room_id)
        return {"id": room_id}

    roster = rooms.RoomRoster(tmp_path / "rooms.json")
    roster.add("VIP", room_id="vip")
    reg = rooms.RoomRegistry(roster, factory)
    assert made == ["default", "vip"]
    assert reg.get("vip") == {"id": "vip"}
    # 認不得的房號回 None（呼叫端負責 404，絕不退回 default）
    assert reg.get("nope") is None
    assert reg.get("包廂") is None


def test_registry_create_and_remove(tmp_path):
    reg = rooms.RoomRegistry(rooms.RoomRoster(tmp_path / "rooms.json"),
                             lambda rid: {"id": rid})
    row = reg.create("202")
    assert row["id"] == "202"
    assert reg.get("202") is not None
    reg.remove("202")
    assert reg.get("202") is None
