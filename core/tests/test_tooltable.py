"""统一工具表的测试。

要锁住的核心：**扩展永远不能覆盖内置或 MCP 的工具**。

这不是洁癖 —— 扩展工具与内置同名时，旧的实现是
`self.registry[name] = spec` 直接覆盖，一个扩展能把 `fs_read`
换成返回任意内容的实现，而模型看到的工具清单毫无异常。

另外锁住：
1. 三种来源各归其位（内置 / 扩展 / MCP）
2. 声明校验能找出 schema 不完整的工具
3. 统一视图能回答「现在到底有哪些工具、谁提供的」
"""
from __future__ import annotations

import json
from pathlib import Path

from oryx.policy import Risk
from oryx.tooltable import Source, ToolDecl, ToolTable


class _Spec:
    """最小 ToolSpec 替身。"""

    def __init__(self, name, description="d", parameters=None, risk=Risk.L0_READ,
                 mutates=False):
        self.name = name
        self.description = description
        self.parameters = parameters if parameters is not None else {
            "type": "object", "properties": {}, "required": [],
        }
        self.risk = risk
        self.mutates = mutates


# ── 来源归位 ──────────────────────────────────────────────────────────
def test_sources_are_distinguished():
    table = ToolTable()
    table.add_builtin(_Spec("fs_read"))
    table.add_extension(_Spec("my_tool"), ext_id="ext1")
    table.add_mcp(_Spec("mcp__srv__do"), server="srv")
    assert table.get("fs_read").source == Source.BUILTIN
    assert table.get("my_tool").source == Source.EXTENSION
    assert table.get("mcp__srv__do").source == Source.MCP
    assert table.counts() == {"builtin": 1, "extension": 1, "mcp": 1}


def test_extension_detail_recorded():
    table = ToolTable()
    table.add_extension(_Spec("my_tool"), ext_id="ext1", trust="user")
    decl = table.get("my_tool")
    assert decl.source_detail == "ext1"
    assert decl.trust == "user"


# ── 冲突检测（本文件最重要的一组）────────────────────────────────────
def test_extension_cannot_override_builtin():
    """扩展不能覆盖内置工具 —— 内置的赢，冲突要记下来。"""
    table = ToolTable()
    table.add_builtin(_Spec("fs_read", description="内置的"))
    ok = table.add_extension(_Spec("fs_read", description="扩展的"), ext_id="evil")
    assert ok is False, "扩展覆盖内置必须被拒"
    assert table.get("fs_read").description == "内置的"
    assert table.conflicts, "冲突必须记下来"
    assert "fs_read" in table.conflicts[0].name


def test_extension_cannot_override_mcp():
    table = ToolTable()
    table.add_mcp(_Spec("mcp__srv__do"), server="srv")
    assert table.add_extension(_Spec("mcp__srv__do"), ext_id="evil") is False
    assert table.get("mcp__srv__do").source == Source.MCP


def test_same_priority_duplicate_is_reported():
    """同级重名也要报出来 —— 哪怕保留的是先来的那个。"""
    table = ToolTable()
    table.add_builtin(_Spec("dup"))
    assert table.add_builtin(_Spec("dup")) is False
    assert table.conflicts


def test_no_conflict_for_distinct_names():
    table = ToolTable()
    table.add_builtin(_Spec("a"))
    table.add_extension(_Spec("b"), ext_id="x")
    table.add_mcp(_Spec("mcp__s__c"), server="s")
    assert table.conflicts == []


# ── 声明校验 ──────────────────────────────────────────────────────────
def test_validate_flags_missing_schema():
    table = ToolTable()
    table.add_builtin(_Spec("bad", parameters={}))
    problems = table.validate()
    assert any("bad" in p for p in problems)


def test_validate_flags_wrong_type():
    table = ToolTable()
    table.add_builtin(_Spec("bad", parameters={"type": "array", "properties": {}}))
    assert any("bad" in p for p in table.validate())


def test_validate_flags_missing_description():
    table = ToolTable()
    table.add_builtin(_Spec("bad", description="   "))
    assert any("bad" in p for p in table.validate())


def test_validate_passes_good_spec():
    table = ToolTable()
    table.add_builtin(_Spec("good"))
    assert table.validate() == []


# ── 视图 ──────────────────────────────────────────────────────────────
def test_to_dict_has_all_fields():
    table = ToolTable()
    table.add_builtin(_Spec("fs_read", risk=Risk.L1_WRITE, mutates=True))
    d = table.to_dict()
    assert d["total"] == 1
    assert d["counts"] == {"builtin": 1}
    assert d["tools"][0]["name"] == "fs_read"
    assert d["tools"][0]["risk_label"]
    assert d["tools"][0]["mutates"] is True


def test_summary_mentions_conflicts():
    table = ToolTable()
    table.add_builtin(_Spec("fs_read"))
    table.add_extension(_Spec("fs_read"), ext_id="evil")
    text = table.summary()
    assert "冲突" in text
    assert "fs_read" in text


# ── 与真实运行时的集成 ────────────────────────────────────────────────
def test_runtime_tool_table_covers_all_tools():
    """运行时的统一表要覆盖 registry 里的全部工具。"""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from oryx.config import DEFAULT_CONFIG, Config
    from oryx.events import EventBus
    from oryx.loop import AgentRuntime
    from oryx.store import Store
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp)]
    data["forbidden_paths"] = []
    rt = AgentRuntime(Config(data), Store(tmp / "o.db"), EventBus())
    assert rt.tool_table.counts().get("builtin", 0) == len(rt.registry) - len(
        rt.tool_plugins
    )
    assert rt.tool_table.get("fs_read") is not None
    assert rt.tool_table.get("shell") is not None


def test_runtime_builtin_declarations_are_valid():
    """内置工具的声明必须全部合规 —— 不合规说明有人写漏了 schema。"""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from oryx.config import DEFAULT_CONFIG, Config
    from oryx.events import EventBus
    from oryx.loop import AgentRuntime
    from oryx.store import Store
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp)]
    data["forbidden_paths"] = []
    rt = AgentRuntime(Config(data), Store(tmp / "o.db"), EventBus())
    assert rt.tool_table.problems == [], \
        "内置工具有声明问题：\n" + "\n".join(rt.tool_table.problems[:10])
