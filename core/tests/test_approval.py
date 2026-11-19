"""安全回归：L2 动作在未确认时绝不执行；确认后才生效。"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from oryx import loop as loop_mod
from oryx.config import DEFAULT_CONFIG, Config
from oryx.events import EventBus
from oryx.loop import AgentRuntime
from oryx.providers import ProviderReply, ToolCall
from oryx.store import Store


class FakeProvider:
    """按预设脚本返回工具调用，最后一个动作是纯文本收尾。"""

    def __init__(self, script):
        self.script = list(script)
        self.index = 0

    def chat(self, system, messages, tools, on_text=None, max_tokens=4096):
        if self.index < len(self.script):
            name, args = self.script[self.index]
            self.index += 1
            if name is None:
                return ProviderReply(text="完成", tool_calls=[], stop_reason="end_turn")
            return ProviderReply(
                text="",
                tool_calls=[ToolCall(id=f"call_{self.index}", name=name, arguments=args)],
                stop_reason="tool_use",
            )
        return ProviderReply(text="完成", tool_calls=[], stop_reason="end_turn")


def make_runtime(tmp_path: Path, script, forbidden=None):
    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    data["forbidden_paths"] = forbidden or []
    config = Config(data)
    store = Store(tmp_path / "oryx.db")
    bus = EventBus()
    runtime = AgentRuntime(config, store, bus)
    fake = FakeProvider(script)
    loop_mod.build_provider = lambda cfg: fake  # type: ignore[assignment]
    return runtime, store, bus, config


def test_delete_without_approval_does_nothing(tmp_path, monkeypatch):
    victim = tmp_path / "important.txt"
    victim.write_text("不要删我", encoding="utf-8")

    runtime, store, bus, _ = make_runtime(
        tmp_path, [("fs_delete", {"path": str(victim)}), (None, {})]
    )
    events = []
    bus.add_sync_sink(events.append)

    # 不确认，超时时间调短，避免测试卡住
    monkeypatch.setattr(loop_mod, "APPROVAL_TIMEOUT", 3)
    runtime.run("删掉那个文件")

    assert victim.exists(), "未确认的删除操作绝不能执行"
    assert any(e["type"] == "step.awaiting_approval" for e in events)
    assert any(e["type"] == "step.denied" for e in events)


def test_delete_after_approval_executes(tmp_path, monkeypatch):
    victim = tmp_path / "trash.txt"
    victim.write_text("可以删", encoding="utf-8")

    runtime, store, bus, _ = make_runtime(
        tmp_path, [("fs_delete", {"path": str(victim)}), (None, {})]
    )

    def approver():
        deadline = time.time() + 5
        while time.time() < deadline:
            with runtime._lock:
                step_ids = list(runtime._approvals)
            if step_ids:
                runtime.resolve_approval(step_ids[0], True)
                return
            time.sleep(0.05)

    threading.Thread(target=approver, daemon=True).start()
    runtime.run("删掉那个文件")

    assert not victim.exists(), "确认后应当执行删除"


def test_forbidden_operation_is_rejected(tmp_path):
    secret = Path("C:/Windows/system.ini")
    runtime, store, bus, _ = make_runtime(
        tmp_path, [("fs_read", {"path": str(secret)}), (None, {})], forbidden=["C:\\Windows"]
    )
    events = []
    bus.add_sync_sink(events.append)

    runtime.run("读一下系统文件")

    assert any(e["type"] == "step.denied" for e in events)
    assert not any(e["type"] == "step.done" for e in events)


def test_audit_written_for_every_call(tmp_path, monkeypatch):
    target = tmp_path / "x.txt"
    target.write_text("x", encoding="utf-8")
    runtime, store, bus, _ = make_runtime(
        tmp_path, [("fs_list", {"path": str(tmp_path)}), (None, {})]
    )
    runtime.run("看看目录")
    tasks = store.recent_tasks(1)
    assert tasks
    audits = store.audit_for_task(tasks[0]["id"])
    assert len(audits) == 1
    assert audits[0]["tool"] == "fs_list"
