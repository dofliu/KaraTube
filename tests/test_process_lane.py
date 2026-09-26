"""處理車道：一台機器一顆 CPU，排隊而且要輪流（backend/services/process_lane.py）"""
import asyncio

import pytest

from backend.services import process_lane
from backend.services.process_lane import (CLASS_BATCH, CLASS_ROOM, CLASS_URGENT,
                                           ProcessLane, order_tickets)


# --- 純排序（輪流真的有輪到）---

def _t(room, seq, klass=CLASS_ROOM):
    return {"room": room, "seq": seq, "klass": klass}


def test_never_served_room_goes_before_a_room_that_already_ran():
    """輪流的全部內容：跑過一首的房間要讓沒跑過的先。"""
    waiting = [_t("a", 1), _t("b", 2)]
    order = order_tickets(waiting, served={"a": 5})
    assert [t["room"] for t in order] == ["b", "a"]


def test_ten_songs_from_one_room_do_not_block_another_room():
    """A 貼十首播放清單，B 點的第一首不必等完那十首。"""
    waiting = [_t("a", i) for i in range(1, 11)] + [_t("b", 11)]
    order = order_tickets(waiting, served={"a": 100})
    assert order[0]["room"] == "b"


def test_least_recently_served_wins_among_rooms():
    waiting = [_t("a", 3), _t("b", 2), _t("c", 1)]
    order = order_tickets(waiting, served={"a": 9, "b": 4, "c": 7})
    assert [t["room"] for t in order] == ["b", "c", "a"]


def test_arrival_order_breaks_ties_within_a_room():
    waiting = [_t("a", 4), _t("a", 2), _t("a", 3)]
    assert [t["seq"] for t in order_tickets(waiting, {})] == [2, 3, 4]


def test_idle_room_jumps_the_queue():
    """決定二：台上沒歌在唱的包廂先 —— 它的等待是一片安靜。"""
    waiting = [_t("a", 1), _t("b", 2, CLASS_URGENT)]
    assert order_tickets(waiting, {})[0]["room"] == "b"


def test_batch_is_always_last():
    """決定三：半夜的批次任務等一小時沒有人知道，客人等三分鐘所有人都在看。"""
    waiting = [_t("_batch", 1, CLASS_BATCH), _t("a", 99)]
    assert [t["room"] for t in order_tickets(waiting, {"a": 50})] == ["a", "_batch"]


# --- 真的跑起來 ---
# pytest 沒裝 asyncio 外掛（CI 的依賴刻意留最小），協程用 asyncio.run 自己跑。


def _run(coro):
    return asyncio.run(coro)


def test_only_one_runs_at_a_time():
    lane = ProcessLane(max_concurrent=1)
    peak = {"now": 0, "max": 0}

    async def work():
        peak["now"] += 1
        peak["max"] = max(peak["max"], peak["now"])
        await asyncio.sleep(0.01)
        peak["now"] -= 1
        return True

    async def main():
        await asyncio.gather(*[lane.run(f"r{i}", work) for i in range(5)])

    _run(main())
    assert peak["max"] == 1


def test_rooms_take_turns_instead_of_first_come_first_served():
    """A 先排三首、B 後排一首 —— B 不必排在第四個。"""
    lane = ProcessLane(max_concurrent=1)
    order = []

    def work(tag):
        async def run():
            order.append(tag)
            await asyncio.sleep(0)
        return run

    async def main():
        # 先讓車道被一件事佔住，這樣下面四件都會進入等待區
        gate = asyncio.Event()

        async def blocker():
            await gate.wait()

        first = asyncio.create_task(lane.run("blocker", blocker))
        await asyncio.sleep(0)
        rest = [asyncio.create_task(lane.run("a", work("a1"))),
                asyncio.create_task(lane.run("a", work("a2"))),
                asyncio.create_task(lane.run("a", work("a3"))),
                asyncio.create_task(lane.run("b", work("b1")))]
        await asyncio.sleep(0)
        gate.set()
        await first
        await asyncio.gather(*rest)

    _run(main())
    assert order[0] == "a1"
    assert order[1] == "b1", f"B 應該排在 A 的第二首前面，實際順序 {order}"


def test_waiting_position_is_reported():
    """決定四：等待要看得見 —— 佇列上那一行不能只停在 0%。"""
    lane = ProcessLane(max_concurrent=1)
    seen = []

    async def main():
        gate = asyncio.Event()

        async def blocker():
            await gate.wait()

        first = asyncio.create_task(lane.run("blocker", blocker))
        await asyncio.sleep(0)
        tasks = [asyncio.create_task(
            lane.run(f"r{i}", lambda: asyncio.sleep(0),
                     on_wait=lambda ahead, total, i=i: seen.append((i, ahead))))
            for i in range(3)]
        await asyncio.sleep(0)
        gate.set()
        await first
        await asyncio.gather(*tasks)

    _run(main())
    assert seen, "等待中的票至少要收到一次位置"
    assert min(a for _, a in seen) == 0


def test_slot_is_released_even_when_the_pipeline_raises():
    """流水線炸掉不能把整條車道卡死 —— 那會讓整台機器再也處理不了任何一首歌。"""
    lane = ProcessLane(max_concurrent=1)

    async def boom():
        raise RuntimeError("pipeline failed")

    async def fine():
        return "ok"

    async def main():
        with pytest.raises(RuntimeError):
            await lane.run("a", boom)
        return await lane.run("b", fine)

    assert _run(main()) == "ok"
    assert lane.snapshot()["running_count"] == 0


def test_snapshot_shape():
    lane = ProcessLane(max_concurrent=1)
    snap = lane.snapshot()
    assert snap == {"running": [], "running_count": 0, "capacity": 1,
                    "waiting": 0, "waiting_rooms": []}


def test_urgent_is_decided_at_selection_time_not_at_arrival():
    """一間包廂可能在排隊的三分鐘裡唱完了手上那一首，那一刻它才變成急件。"""
    idle = {"a": False}
    lane = ProcessLane(max_concurrent=1)
    order = []

    def work(tag):
        async def run():
            order.append(tag)
            await asyncio.sleep(0)
        return run

    async def main():
        gate = asyncio.Event()

        async def blocker():
            await gate.wait()

        first = asyncio.create_task(lane.run("blocker", blocker))
        await asyncio.sleep(0)
        tasks = [
            asyncio.create_task(lane.run("b", work("b"))),
            asyncio.create_task(lane.run("a", work("a"), urgent=lambda: idle["a"])),
        ]
        await asyncio.sleep(0)
        # 排隊途中 A 那間的歌唱完了 → 台上沒歌 → 變成急件
        idle["a"] = True
        gate.set()
        await first
        await asyncio.gather(*tasks)

    _run(main())
    assert order[0] == "a", f"急件應該插到前面，實際順序 {order}"


def test_class_constants_are_ordered():
    assert CLASS_URGENT < CLASS_ROOM < CLASS_BATCH
    assert process_lane.sort_key({"room": "a", "seq": 1, "klass": CLASS_ROOM}, {}) \
        > process_lane.sort_key({"room": "b", "seq": 9, "klass": CLASS_URGENT}, {})
