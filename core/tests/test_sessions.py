"""会话列表、能力清单、工具检索。"""

from __future__ import annotations

import socket
import sys
import threading
import time

import pytest

from oryx import loop as loop_mod
from oryx.config import DEFAULT_CONFIG, Config
from oryx.events import EventBus
from oryx.loop import AgentRuntime
from oryx.providers import ProviderReply
from oryx.store import Store


class QuietProvider:
    def chat(self, system, messages, tools, on_text=None, max_tokens=4096):
        if on_text:
            on_text("好的")
        return ProviderReply(text="好的", tool_calls=[], stop_reason="end_turn", usage={})


def make_runtime(tmp_path):
    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    data["forbidden_paths"] = []
    data["workspace"] = str(tmp_path)
    config = Config(data)
    store = Store(tmp_path / "oryx.db")
    runtime = AgentRuntime(config, store, EventBus())
    loop_mod.build_provider = lambda cfg: QuietProvider()  # type: ignore[assignment]
    return runtime, store


# ---------------------------------------------------------------- 会话


def test_sessions_empty(tmp_path):
    _, store = make_runtime(tmp_path)
    assert store.list_sessions() == []


def test_session_appears_after_run(tmp_path):
    runtime, store = make_runtime(tmp_path)
    runtime.run("帮我整理一下文件", interaction="craft")

    sessions = store.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["title"].startswith("帮我整理")
    assert sessions[0]["msg_count"] >= 1


def test_session_title_truncated(tmp_path):
    runtime, store = make_runtime(tmp_path)
    long_goal = "这是一个非常非常非常非常非常非常非常非常长的请求内容用于测试标题是否会被正确截断而不是无限延长"
    assert len(long_goal) > 40
    runtime.run(long_goal, interaction="craft")
    title = store.list_sessions()[0]["title"]
    assert len(title) == 41
    assert title.endswith("…")


def test_session_detail(tmp_path):
    runtime, store = make_runtime(tmp_path)
    runtime.run("你好", interaction="craft")
    task_id = store.list_sessions()[0]["id"]

    detail = store.session_detail(task_id)
    assert detail["task"]["id"] == task_id
    assert isinstance(detail["messages"], list)
    assert isinstance(detail["steps"], list)
    assert isinstance(detail["todos"], list)
    assert detail["usage"]["calls"] == 0


def test_session_detail_missing(tmp_path):
    _, store = make_runtime(tmp_path)
    assert store.session_detail("nope") is None


def test_session_delete_cascades(tmp_path):
    runtime, store = make_runtime(tmp_path)
    runtime.run("你好", interaction="craft")
    task_id = store.list_sessions()[0]["id"]
    store.add_todo(task_id, "待办一")

    assert store.delete_session(task_id) is True
    assert store.list_sessions() == []
    assert store.messages_for_task(task_id) == []
    assert store.steps_for_task(task_id) == []
    assert store.list_todos(task_id) == []


def test_delete_missing_session(tmp_path):
    _, store = make_runtime(tmp_path)
    assert store.delete_session("nope") is False


# ---------------------------------------------------------------- 能力


def test_registry_has_tool_search(tmp_path):
    runtime, _ = make_runtime(tmp_path)
    assert "tool_search" in runtime.registry
    spec = runtime.registry["tool_search"]
    assert int(spec.risk) == 0


def test_tool_search_matches_by_keyword(tmp_path):
    runtime, _ = make_runtime(tmp_path)
    search = runtime.registry["tool_search"].fn
    result = search(query="截图", limit=10)
    names = [t["name"] for t in result["content"]]
    assert "browser_screenshot" in names


def test_tool_search_matches_chinese_description(tmp_path):
    runtime, _ = make_runtime(tmp_path)
    search = runtime.registry["tool_search"].fn
    names = [t["name"] for t in search(query="表格", limit=20)["content"]]
    assert any(n.startswith("xlsx") for n in names)


def test_tool_search_empty_query_lists_some(tmp_path):
    runtime, _ = make_runtime(tmp_path)
    result = runtime.registry["tool_search"].fn(query="", limit=5)
    assert len(result["content"]) == 5


def test_tool_search_returns_schema(tmp_path):
    runtime, _ = make_runtime(tmp_path)
    # 用工具名本身去查，必须能召回到它（语义融合会重排，所以不锁死第一位）
    items = runtime.registry["tool_search"].fn(query="fs_read", limit=5)["content"]
    item = next(i for i in items if i["name"] == "fs_read")
    assert "parameters" in item
    assert item["risk_label"]


def test_capability_groups_cover_registry(tmp_path):
    runtime, _ = make_runtime(tmp_path)
    groups: dict[str, int] = {}
    for name in runtime.registry:
        group = "mcp" if name.startswith("mcp__") else name.split("_")[0]
        groups[group] = groups.get(group, 0) + 1
    assert sum(groups.values()) == len(runtime.registry)
    assert "browser" in groups


# ---------------------------------------------------------------- 能力矩阵


def test_provider_capabilities_defaults():
    from oryx.manage import provider_capabilities

    anthropic = provider_capabilities({"type": "anthropic"})
    assert anthropic["supports_vision"] is True
    assert anthropic["context_window"] == 200_000

    openai = provider_capabilities({"type": "openai"})
    assert openai["supports_tools"] is True
    assert openai["context_window"] == 128_000


def test_provider_capabilities_override():
    from oryx.manage import provider_capabilities

    custom = provider_capabilities(
        {"type": "openai", "context_window": 1_000_000, "supports_vision": True}
    )
    assert custom["context_window"] == 1_000_000
    assert custom["supports_vision"] is True
    assert custom["supports_tools"] is True  # 未覆盖的仍用默认
