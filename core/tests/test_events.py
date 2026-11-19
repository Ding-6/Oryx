"""事件总线：AG-UI 事件族与断点续传。"""

from __future__ import annotations

import asyncio

from oryx import events as ev
from oryx.events import EventBus


def test_publish_assigns_monotonic_seq_and_trace():
    bus = EventBus()
    a = bus.publish("X", n=1)
    b = bus.publish("X", n=2)

    assert a["seq"] == 1 and b["seq"] == 2, "seq 必须从 1 单调递增"
    assert a["trace_id"] == b["trace_id"] != "", "同一进程内 trace_id 恒定"
    assert a["type"] == "X" and a["n"] == 1


def test_agui_shortcuts_emit_standard_families():
    bus = EventBus()
    assert bus.run_started("t1")["type"] == ev.RUN_STARTED
    assert bus.text_delta("t1", "m1", "你")["type"] == ev.TEXT_MESSAGE_CONTENT
    assert bus.text_delta("t1", "m1", "好")["delta"] == "好"
    assert bus.tool_start("t1", "call1", "fs_read")["tool"] == "fs_read"
    assert bus.tool_args("t1", "call1", {"path": "a"})["args"] == {"path": "a"}
    assert bus.tool_result("t1", "call1", "fs_read", "ok", ok=True)["ok"] is True
    assert bus.run_error("t1", "boom")["error"] == "boom"


def test_reserved_keys_cannot_be_overwritten_by_payload():
    """业务字段哪怕叫 seq 也不能盖掉事件序号。

    seq 是前端单调校验的唯一依据，被盖掉后后续事件全被当重复丢掉，界面直接卡死。
    """
    bus = EventBus()
    a = bus.publish("step.created", seq=1, trace_id="fake", type="fake", ts=0)
    b = bus.publish("X")

    assert a["seq"] == 1 and a["type"] == "step.created"
    assert a["trace_id"] == bus.trace_id != "fake"
    assert b["seq"] == 2, "事件序号必须继续单调递增"
    assert ev.RESERVED_KEYS == frozenset({"seq", "trace_id", "type", "ts"})


def test_step_created_carries_step_seq_not_event_seq():
    """主循环的 step.created 不能再用 seq 这个名字传步骤序号。"""
    from oryx import loop as loop_mod
    from oryx.config import DEFAULT_CONFIG, Config
    from oryx.providers import ToolCall
    from oryx.store import Store
    from pathlib import Path
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    data = dict(DEFAULT_CONFIG)
    data.update(allowed_roots=[str(tmp)], forbidden_paths=[], workspace=str(tmp), interaction="craft")
    bus = EventBus()
    events: list[dict] = []
    bus.add_sync_sink(events.append)
    runtime = loop_mod.AgentRuntime(Config(data), Store(tmp / "oryx.db"), bus)
    runtime._active_task_id = "task-1"
    runtime._active_interaction = "craft"

    runtime._handle_tool_call("task-1", 7, ToolCall(id="c1", name="fs_list", arguments={"path": str(tmp)}))

    created = next(e for e in events if e["type"] == "step.created")
    assert created["step_seq"] == 7, "步骤序号改名成 step_seq"
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs), "所有事件序号必须严格递增不重复"


def test_replay_since_returns_only_newer_events():
    bus = EventBus()
    for i in range(5):
        bus.publish("X", i=i)

    replayed = bus.replay_since(2)

    assert [e["seq"] for e in replayed] == [3, 4, 5], "只补发 seq > since 的事件"
    assert [e["i"] for e in replayed] == [2, 3, 4]


def test_replay_since_zero_on_empty_bus_is_empty():
    assert EventBus().replay_since(0) == []


def test_replay_since_rejects_negative():
    bus = EventBus()
    bus.publish("X")
    assert bus.replay_since(-1) == [], "负数表示首连，不走补发"


def test_gap_detected_when_buffer_rolled_over():
    bus = EventBus(buffer_limit=4)
    for i in range(10):
        bus.publish("X", i=i)

    assert bus.has_gap_since(1) is True, "缓冲已滚过 since，必须报空洞"
    assert bus.replay_since(1) == [], "有空洞时宁可不补，也不乱序"

    assert bus.has_gap_since(9) is False
    assert [e["seq"] for e in bus.replay_since(9)] == [10]


def test_no_gap_when_since_is_latest():
    bus = EventBus()
    for i in range(3):
        bus.publish("X", i=i)
    assert bus.has_gap_since(bus.current_seq) is False


def test_sync_sink_receives_seq():
    bus = EventBus()
    seen: list[dict] = []
    bus.add_sync_sink(seen.append)
    bus.publish("X")
    bus.publish("Y")

    assert [e["seq"] for e in seen] == [1, 2]


def test_async_subscriber_gets_events_in_order():
    async def scenario():
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        q = bus.subscribe()
        bus.publish("A")
        bus.publish("B")
        await asyncio.sleep(0)  # 让 call_soon_threadsafe 落地
        return [q.get_nowait()["type"] for _ in range(q.qsize())]

    assert asyncio.run(scenario()) == ["A", "B"]


def test_unsubscribe_stops_delivery():
    async def scenario():
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        q = bus.subscribe()
        bus.publish("A")
        await asyncio.sleep(0)
        bus.unsubscribe(q)
        bus.publish("B")
        await asyncio.sleep(0)
        return q.qsize()

    assert asyncio.run(scenario()) == 1