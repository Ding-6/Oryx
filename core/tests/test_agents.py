"""子 Agent：白名单约束 + 文档管理 Agent 真的会写文档。"""

from __future__ import annotations

from pathlib import Path

from oryx import loop as loop_mod
from oryx.agents import AgentLibrary
from oryx.config import DEFAULT_CONFIG, Config
from oryx.events import EventBus
from oryx.loop import AgentRuntime
from oryx.providers import ProviderReply, ToolCall
from oryx.store import Store

AGENTS_ROOT = Path(__file__).resolve().parents[2] / "agents"


class ScriptedProvider:
    def __init__(self, script):
        self.script = list(script)
        self.index = 0
        self.systems: list[str] = []

    def chat(self, system, messages, tools, on_text=None, max_tokens=4096):
        self.systems.append(system)
        if self.index < len(self.script):
            name, args = self.script[self.index]
            self.index += 1
            if name is None:
                return ProviderReply(text="已记录", tool_calls=[], stop_reason="end_turn")
            return ProviderReply(
                text="",
                tool_calls=[ToolCall(id=f"c{self.index}", name=name, arguments=args)],
                stop_reason="tool_use",
            )
        return ProviderReply(text="已记录", tool_calls=[], stop_reason="end_turn")


def make_runtime(tmp_path: Path, script):
    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    data["forbidden_paths"] = []
    data["workspace"] = str(tmp_path)
    data["interaction"] = "craft"
    config = Config(data)
    store = Store(tmp_path / "oryx.db")
    bus = EventBus()
    runtime = AgentRuntime(config, store, bus)
    provider = ScriptedProvider(script)
    loop_mod.build_provider = lambda cfg: provider  # type: ignore[assignment]
    return runtime, store, provider, bus


def test_agent_library_loads_doc_manager():
    library = AgentLibrary(AGENTS_ROOT)
    assert "doc-manager" in library.agents
    spec = library.agents["doc-manager"]
    assert "doc_requirement_add" in spec.tools
    assert spec.max_turns >= 1


def test_subagent_writes_to_project_doc(tmp_path):
    runtime, store, provider, bus = make_runtime(
        tmp_path,
        [("doc_requirement_add", {"title": "支持批量导入插件", "detail": "一次拖入多个目录"}), (None, {})],
    )
    task_id = store.create_task("x", "efficiency", "test")
    runtime._active_task_id = task_id
    runtime._active_registry = dict(runtime.registry)

    result = runtime.run_subagent("doc-manager", "记录这条需求")
    assert result["ok"]

    text = runtime.project_doc.read()
    assert "支持批量导入插件" in text
    assert "REQ-001" in text


def test_subagent_cannot_use_tools_outside_whitelist(tmp_path):
    victim = tmp_path / "keep.txt"
    victim.write_text("别删我", encoding="utf-8")

    runtime, store, provider, bus = make_runtime(
        tmp_path, [("fs_delete", {"path": str(victim)}), (None, {})]
    )
    runtime._active_task_id = store.create_task("x", "efficiency", "test")
    runtime._active_registry = dict(runtime.registry)

    runtime.run_subagent("doc-manager", "删掉那个文件")

    assert victim.exists(), "子 Agent 不能调用白名单外的工具"


def test_unknown_agent_returns_error(tmp_path):
    runtime, store, provider, bus = make_runtime(tmp_path, [(None, {})])
    result = runtime.run_subagent("nope", "干活")
    assert result["ok"] is False
    assert "没有这个子 Agent" in result["content"]


def test_subagent_prompt_mentions_role(tmp_path):
    runtime, store, provider, bus = make_runtime(tmp_path, [(None, {})])
    runtime._active_task_id = store.create_task("x", "efficiency", "test")
    runtime._active_registry = dict(runtime.registry)
    runtime.run_subagent("doc-manager", "记录")
    assert "文档管理" in provider.systems[0]
    assert "只能使用被授权的工具" in provider.systems[0]


# ================================================================ 五大管家
def test_five_butlers_exist():
    """Oryx + 五大管家：软件、搜索、浏览器、文件、电脑。"""
    library = AgentLibrary(AGENTS_ROOT)
    butlers = {a.id: a for a in library.agents.values() if a.kind == "butler"}
    expected = {
        "app-agent": "软件管家",
        "search-agent": "搜索管家",
        "browser-agent": "浏览器管家",
        "file-agent": "文件管家",
        "computer-agent": "电脑管家",
    }
    for aid, name in expected.items():
        assert aid in butlers, f"缺少管家 {aid}"
        assert butlers[aid].name == name
        # 管家要有身份标识 —— 界面上得能一眼认出是哪个
        assert butlers[aid].icon, f"{aid} 没有图标"
        assert butlers[aid].subtitle, f"{aid} 没有职责短语"


def test_butlers_have_role_specific_tools():
    """每个管家的工具要跟它的职责对得上。

    管家空着手就等于没有 —— 白名单是硬边界，工具给错了就什么都干不了。
    """
    library = AgentLibrary(AGENTS_ROOT)
    agents = library.agents

    # 软件管家管应用
    assert "list_apps" in agents["app-agent"].tools
    # 搜索管家管检索
    assert "index_search" in agents["search-agent"].tools
    assert "search_files" in agents["search-agent"].tools
    # 浏览器管家管网页
    assert "browser_open" in agents["browser-agent"].tools
    assert "browser_click" in agents["browser-agent"].tools
    # 文件管家管文件
    assert "fs_read" in agents["file-agent"].tools
    assert "fs_write" in agents["file-agent"].tools
    # 电脑管家管系统
    assert "term_run" in agents["computer-agent"].tools
    assert "sys_power_status" in agents["computer-agent"].tools


def test_agent_library_reads_bom_files(tmp_path):
    """带 BOM 的 AGENT.md 也要能解析。

    用 utf-8 读出来开头是 U+FEFF，frontmatter 正则匹配不上 ——
    结果是整个 Agent 被当成没有 frontmatter 的纯正文，
    名字、工具白名单全丢，而且**不报任何错**。
    """
    root = tmp_path / "agents"
    d = root / "bom-agent"
    d.mkdir(parents=True)
    (d / "AGENT.md").write_text(
        "﻿---\nname: BOM 管家\ndescription: 带 BOM 的定义\n"
        "tools: fs_read\nkind: butler\nmax_turns: 4\n---\n正文",
        encoding="utf-8",
    )
    library = AgentLibrary(root)
    spec = library.get("bom-agent")
    assert spec is not None, "带 BOM 的文件要被认出来"
    assert spec.name == "BOM 管家"
    assert spec.tools == ["fs_read"]
    assert spec.kind == "butler"
    assert spec.max_turns == 4


def test_agent_catalog_exposes_identity():
    """目录里要带上名字、图标、职责、类型 —— 前端靠这些显示。"""
    library = AgentLibrary(AGENTS_ROOT)
    item = next(a for a in library.catalog() if a["id"] == "app-agent")
    assert item["name"] == "软件管家"
    assert item["icon"]
    assert item["subtitle"]
    assert item["kind"] == "butler"
