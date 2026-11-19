"""MCP 管理：用户能看见、能启停、能知道启没启起来。

MCP 的**实现**一直都在（三作用域配置、stdio/http/sse 三传输、工具注册），
但用户**完全触达不到**：
  · summarize() 写好了却零引用；
  · 前端没有 MCP 页；
  · mcp_servers 是特权键，只能手改 config.json；
  · 更糟的是 MCP 只在内核**启动时**加载一次 —— 用户改了 enabled 也不生效。

这组测试锁住「可达 + 可控 + 立即生效」。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from oryx.config import DEFAULT_CONFIG, Config
from oryx.events import EventBus
from oryx.loop import AgentRuntime
from oryx.store import Store


def _runtime(tmp_path: Path, mcp_file: Path | None = None) -> AgentRuntime:
    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    data["forbidden_paths"] = []
    data["workspace"] = str(tmp_path)
    config = Config(data)
    store = Store(tmp_path / "oryx.db")
    return AgentRuntime(config, store, EventBus())


# ------------------------------------------------------------------ 清单


def test_status_lists_configured_servers(tmp_path: Path, monkeypatch):
    """配置里有的服务器要出现在清单里。"""
    import oryx.mcpconfig as mcpconfig_mod

    # 指到临时目录的 user 配置
    monkeypatch.setattr(
        mcpconfig_mod, "user_config_path", lambda: tmp_path / ".mcp.json"
    )
    (tmp_path / ".mcp.json").write_text(
        json.dumps({
            "mcpServers": {
                "demo": {"command": "echo", "args": ["hi"]},
            }
        }),
        encoding="utf-8",
    )

    runtime = _runtime(tmp_path)
    status = runtime.mcp_status()
    names = [s["name"] for s in status["servers"]]
    assert "demo" in names, f"清单里没有 demo：{names}"


def test_status_marks_enabled_and_running(tmp_path: Path, monkeypatch):
    """enabled 与 running 是两回事，要分开标出来。

    enabled = 配置里开了；running = 进程真的起来了。
    外部 MCP server 不会自动启动（安全约束），所以两者经常不一致 ——
    用户必须能看出「我开了，但它没起来」。
    """
    import oryx.mcpconfig as mcpconfig_mod

    monkeypatch.setattr(
        mcpconfig_mod, "user_config_path", lambda: tmp_path / ".mcp.json"
    )
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"demo": {"command": "echo"}}}),
        encoding="utf-8",
    )

    runtime = _runtime(tmp_path)
    runtime.config.data["mcp_servers"] = {"demo": {"enabled": True}}
    status = runtime.mcp_status()
    demo = next(s for s in status["servers"] if s["name"] == "demo")
    assert demo["enabled"] is True
    # 不会自动启动（安全约束）→ running 应为 False
    assert demo["running"] is False


def test_status_works_without_any_config(tmp_path: Path, monkeypatch):
    """没有 MCP 配置也不能报错 —— 清单就是空的。"""
    import oryx.mcpconfig as mcpconfig_mod

    monkeypatch.setattr(
        mcpconfig_mod, "user_config_path", lambda: tmp_path / "missing.json"
    )
    runtime = _runtime(tmp_path)
    status = runtime.mcp_status()
    assert status["servers"] == []


# ------------------------------------------------------------------ 启停


def test_toggle_saves_to_config(tmp_path: Path, monkeypatch):
    """启停要真的落到配置里 —— 否则重启后又回到原样。"""
    import oryx.mcpconfig as mcpconfig_mod

    monkeypatch.setattr(
        mcpconfig_mod, "user_config_path", lambda: tmp_path / ".mcp.json"
    )
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"demo": {"command": "echo"}}}),
        encoding="utf-8",
    )

    runtime = _runtime(tmp_path)
    # Config 没绑定路径时 save() 不落盘，所以给它一个路径
    runtime.config.path = tmp_path / "config.json"

    out = runtime.mcp_set_enabled("demo", True)
    assert out["ok"] is True
    assert runtime.config.data["mcp_servers"]["demo"]["enabled"] is True

    saved = json.loads((tmp_path / "config.json").read_text("utf-8"))
    assert saved["mcp_servers"]["demo"]["enabled"] is True


def test_toggle_rejects_empty_name(tmp_path: Path):
    runtime = _runtime(tmp_path)
    out = runtime.mcp_set_enabled("", True)
    assert out["ok"] is False


def test_reload_removes_tools_of_stopped_servers(tmp_path: Path):
    """停用的服务器，它的工具要从注册表里消失。

    不消失的话：用户以为停用了，模型照样能调它的工具 ——
    停用等于没停。
    """
    runtime = _runtime(tmp_path)

    from oryx.mcp import MCPTool

    # 造一个假的 MCP 客户端（工具要用真的 MCPTool —— 注册时断言了类型）
    class _FakeClient:
        server_info = {}

        def __init__(self):
            self.tools = [MCPTool(
                server="demo", name="ping", description="ping",
                input_schema={"type": "object", "properties": {}}, annotations={},
            )]

        def close(self):
            pass

    runtime.mcp_clients["demo"] = _FakeClient()
    runtime._register_mcp_tools()
    assert "mcp__demo__ping" in runtime.registry

    # 停用 → 重载 → 工具应该消失，client 也应该被关掉
    runtime.config.data["mcp_servers"] = {"demo": {"enabled": False}}
    runtime.reload_mcp()
    assert "mcp__demo__ping" not in runtime.registry, "停用后工具还在"
    assert "demo" not in runtime.mcp_clients, "停用后进程还开着"


def test_mcp_tools_are_named_with_server_prefix(tmp_path: Path):
    """MCP 工具名带服务器前缀 —— 否则两个服务器有同名工具会打架。"""
    from oryx.mcp import MCPTool

    tool = MCPTool(
        server="github", name="create_issue", description="",
        input_schema={}, annotations={},
    )
    assert tool.qualified_name == "mcp__github__create_issue"
