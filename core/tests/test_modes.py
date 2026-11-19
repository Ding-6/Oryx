"""交互模式、任务清单、回答风格回归。

重点验证：模式闸门是代码强制的，不是靠提示词自觉。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from oryx import loop as loop_mod
from oryx.config import DEFAULT_CONFIG, Config
from oryx.events import EventBus
from oryx.loop import AgentRuntime
from oryx.providers import ProviderReply, ToolCall
from oryx.store import Store


class CapturingProvider:
    def __init__(self, script):
        self.script = list(script)
        self.index = 0
        self.systems: list[str] = []
        self.tools: list[list[str]] = []

    def chat(self, system, messages, tools, on_text=None, max_tokens=4096):
        self.systems.append(system)
        self.tools.append([t["name"] for t in tools])
        if self.index < len(self.script):
            name, args = self.script[self.index]
            self.index += 1
            if name is None:
                return ProviderReply(text="完成", tool_calls=[], stop_reason="end_turn")
            return ProviderReply(
                text="",
                tool_calls=[ToolCall(id=f"c{self.index}", name=name, arguments=args)],
                stop_reason="tool_use",
            )
        return ProviderReply(text="完成", tool_calls=[], stop_reason="end_turn")


def make_runtime(tmp_path: Path, script):
    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    data["forbidden_paths"] = []
    config = Config(data)
    store = Store(tmp_path / "oryx.db")
    bus = EventBus()
    runtime = AgentRuntime(config, store, bus)
    provider = CapturingProvider(script)
    loop_mod.build_provider = lambda cfg: provider  # type: ignore[assignment]
    events: list[dict] = []
    bus.add_sync_sink(events.append)
    return runtime, store, bus, provider, events


def test_ask_mode_blocks_write(tmp_path):
    target = tmp_path / "should_not_exist.txt"
    runtime, store, bus, provider, events = make_runtime(
        tmp_path, [("fs_write", {"path": str(target), "content": "x"}), (None, {})]
    )
    runtime.run("写个文件", interaction="ask")

    assert not target.exists(), "问答模式绝不能写文件"
    denied = [e for e in events if e["type"] == "step.denied"]
    assert denied and "ask" in denied[0]["reason"]


def test_ask_mode_allows_read(tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    runtime, store, bus, provider, events = make_runtime(
        tmp_path, [("fs_list", {"path": str(tmp_path)}), (None, {})]
    )
    runtime.run("看看目录", interaction="ask")
    assert any(e["type"] == "step.done" for e in events)
    assert not any(e["type"] == "step.denied" for e in events)


def test_plan_mode_still_blocks_write(tmp_path):
    target = tmp_path / "plan.txt"
    runtime, store, bus, provider, events = make_runtime(
        tmp_path, [("fs_write", {"path": str(target), "content": "x"}), (None, {})]
    )
    runtime.run("给个方案", interaction="plan")
    assert not target.exists()


def test_craft_mode_allows_write(tmp_path):
    target = tmp_path / "craft.txt"
    runtime, store, bus, provider, events = make_runtime(
        tmp_path, [("fs_write", {"path": str(target), "content": "hello"}), (None, {})]
    )
    runtime.run("写个文件", interaction="craft")
    assert target.exists()
    assert target.read_text(encoding="utf-8") == "hello"


def test_task_tools_only_in_craft_mode(tmp_path):
    runtime, store, bus, provider, events = make_runtime(
        tmp_path, [(None, {})]
    )
    runtime.run("随便聊聊", interaction="ask")
    assert "task_create" not in provider.tools[0]

    runtime2, store2, bus2, provider2, _ = make_runtime(tmp_path / "b", [(None, {})])
    runtime2.run("做点事", interaction="craft")
    assert "task_create" in provider2.tools[0]


def test_todo_lifecycle(tmp_path):
    runtime, store, bus, provider, events = make_runtime(
        tmp_path,
        [
            ("task_create", {"title": "读取文件"}),
            ("task_list", {}),
            (None, {}),
        ],
    )
    runtime.run("分步做", interaction="craft")

    tasks = store.recent_tasks(1)
    todos = store.list_todos(tasks[0]["id"])
    assert len(todos) == 1
    assert todos[0]["title"] == "读取文件"
    assert todos[0]["status"] == "pending"
    assert any(e["type"] == "todos.updated" for e in events)


def test_todo_status_update(tmp_path):
    runtime, store, bus, provider, events = make_runtime(tmp_path, [(None, {})])
    tasks = store.recent_tasks(1)
    runtime.run("x", interaction="craft")
    task_id = store.recent_tasks(1)[0]["id"]

    todo = store.add_todo(task_id, "第一步")
    updated = store.update_todo(task_id, todo["id"], "done")
    assert updated and updated["status"] == "done"

    bad = store.update_todo(task_id, "不存在", "done")
    assert bad is None


def test_system_prompt_carries_mode_and_style(tmp_path):
    runtime, store, bus, provider, events = make_runtime(tmp_path, [(None, {})])
    runtime.run("你好", interaction="ask", style="efficient")

    system = provider.systems[0]
    assert "问答模式" in system
    assert "极简直给" in system  # efficient 风格的特征句


def test_craft_prompt_mentions_task_tools(tmp_path):
    runtime, store, bus, provider, events = make_runtime(tmp_path, [(None, {})])
    runtime.run("你好", interaction="craft", style="professional")
    assert "task_create" in provider.systems[0]
