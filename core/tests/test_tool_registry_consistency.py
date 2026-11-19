"""工具注册表与「会改动什么」的清单必须一致。

这两个表漂移过一次，后果是**工具在撒谎**：
清单漏了 browser_open / spawn_agent / team_create，
于是这些工具跑完会带上 ``mutated: false`` —— 声称「我没动过任何东西」，
而实际上它改了浏览器状态、写了 SQLite、派了子 Agent。

只读自证是安全机制的一部分（用户靠它判断「这个操作改了什么」），
说谎比不说更糟。所以这里锁死一致性。
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from oryx.config import DEFAULT_CONFIG, Config
from oryx.events import EventBus
from oryx.loop import AgentRuntime
from oryx.store import Store
from oryx.tools import MUTATING_TOOLS


@pytest.fixture(scope="module")
def runtime() -> AgentRuntime:
    tmp = Path(tempfile.mkdtemp())
    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp)]
    data["forbidden_paths"] = []
    return AgentRuntime(Config(data), Store(tmp / "oryx.db"), EventBus())


def test_no_dead_entries_in_mutating_list(runtime: AgentRuntime):
    """清单里不能有根本不存在的工具 —— 那是残留。

    允许的例外：task_create / task_update 是运行时注入的
    （见 AgentRuntime._task_tools），不在静态注册表里。
    """
    runtime_injected = {"task_create", "task_update", "checkpoint_rewind"}
    dead = MUTATING_TOOLS - set(runtime.registry) - runtime_injected
    assert not dead, f"清单里有不存在的工具：{sorted(dead)}"


def test_all_mutating_tools_are_registered_or_injected(runtime: AgentRuntime):
    """反向：注册了会改状态的工具，就必须进清单。"""
    runtime_injected = {"task_create", "task_update", "checkpoint_rewind"}
    registered = set(runtime.registry) | runtime_injected
    missing = registered - MUTATING_TOOLS
    # 这些是真只读，允许不在清单里
    readonly_ok = {
        "system_info", "fs_list", "fs_read", "fs_glob", "fs_grep", "search_files",
        "xlsx_read", "index_search", "index_status", "ocr_image",
        "list_apps", "list_agents", "list_providers", "memory_search",
        "remote_status", "sys_power_status", "sys_display_settings",
        "sys_mouse_settings", "sys_input_methods", "term_status",
        "tool_search", "use_skill", "workflow_status", "workspace_info",
        "browser_extract", "browser_links", "browser_snapshot",
        # 等元素出现 —— 只读，不改任何状态
        # （不像 browser_open 会切页面）
        "browser_wait",
        # 联网搜索 —— 只读（不写文件、不改状态）
        # 搜到的内容是**外部来源**，但工具本身不改动任何东西
        "web_search",
        "doc_read", "automation_list", "automation_runs", "team_list",
        "team_inbox", "prompt_optimize", "shell_sandbox",
        "task_list",  # 只读清单
        "workflow_draft",  # 只读：只生成编排草案，不执行、不落库
    }
    unaccounted = missing - readonly_ok
    assert not unaccounted, (
        f"这些工具会改动状态但不在 MUTATING_TOOLS 里 —— "
        f"它们的结果会谎称「未改动」：{sorted(unaccounted)}"
    )


def test_browser_open_is_marked_mutating(runtime: AgentRuntime):
    """browser_open 会切换浏览器当前页面 —— 必须算改动。

    tools.py 自己的注释就写了这件事，但清单里漏了它。
    """
    assert "browser_open" in MUTATING_TOOLS
    assert "browser_close" in MUTATING_TOOLS


def test_team_tools_are_marked_mutating():
    """团队相关工具会写 SQLite。"""
    for name in ("team_create", "team_delete", "team_add_member", "team_send_message"):
        assert name in MUTATING_TOOLS, f"{name} 会写库，必须标记为改动"


def test_spawn_agent_is_marked_mutating():
    """派子 Agent 会真的产生副作用（子 Agent 可能写文件）。"""
    assert "spawn_agent" in MUTATING_TOOLS
