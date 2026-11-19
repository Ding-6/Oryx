"""工具扩展与 profile 的测试。

要锁住的四件事：

1. **信任边界** —— 仓库自带（project）的扩展**不能执行代码**。
   这是整个设计的关键：clone 一个仓库不该等于授权它的代码在你机器上跑。
2. **声明与实现分离** —— 清单里必须有风险等级；没写就按 L2 处理。
3. **profile 只收窄、不放宽** —— profile 不能用来绕过信任检查。
4. **加载要留痕** —— 加载了什么、跳过了什么、为什么，都能问出来。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from oryx.policy import Risk
from oryx.profiles import ProfileSet
from oryx.registry import Registry, Trust
from oryx.toolplug import ToolPluginError, load_tool_plugins

CORE = Path(__file__).resolve().parents[1]


# ── 工具 ──────────────────────────────────────────────────────────────
def _make_ext(base: Path, name: str, *, risk: str | None = "L0_READ",
              entry: str = "tools.py:run", code: str | None = None) -> Path:
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    tool: dict = {
        "name": f"{name}_tool",
        "description": "测试工具",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "entry": entry,
    }
    if risk is not None:
        tool["risk"] = risk
    (d / "oryx.json").write_text(
        json.dumps({"id": name, "kind": "tool", "version": "1.0.0", "tools": [tool]}),
        encoding="utf-8",
    )
    (d / "tools.py").write_text(
        code if code is not None else
        "def run(**kw):\n    return {'ok': True, 'content': 'ok', 'meta': {}}\n",
        encoding="utf-8",
    )
    return d


# ── 信任边界 ──────────────────────────────────────────────────────────
def test_project_trust_cannot_execute_code(tmp_path):
    """仓库自带的扩展不能执行代码 —— clone 不等于授权。"""
    _make_ext(tmp_path, "risky")
    reg = Registry()
    reg.add_root("tool", tmp_path, Trust.PROJECT)
    reg.discover()
    specs, report = load_tool_plugins(reg.of_kind("tool"))
    assert specs == {}, "不可信来源的工具不该被注册"
    assert report.loaded == []
    assert any("不允许执行" in why for _, why in report.skipped), \
        f"跳过原因要说清楚：{report.skipped}"


def test_user_trust_can_execute_code(tmp_path):
    """用户自己放的扩展可以执行。"""
    _make_ext(tmp_path, "mine")
    reg = Registry()
    reg.add_root("tool", tmp_path, Trust.USER)
    reg.discover()
    specs, report = load_tool_plugins(reg.of_kind("tool"))
    assert "mine_tool" in specs
    assert report.loaded and report.loaded[0].extension_id == "mine"


def test_builtin_trust_can_execute_code(tmp_path):
    _make_ext(tmp_path, "builtin")
    reg = Registry()
    reg.add_root("tool", tmp_path, Trust.BUILTIN)
    reg.discover()
    specs, _ = load_tool_plugins(reg.of_kind("tool"))
    assert "builtin_tool" in specs


def test_trust_is_per_root(tmp_path):
    """同一个 kind 的不同根可以有不同信任级别。"""
    trusted = tmp_path / "trusted"
    untrusted = tmp_path / "untrusted"
    _make_ext(trusted, "a")
    _make_ext(untrusted, "b")
    reg = Registry()
    reg.add_root("tool", trusted, Trust.USER)
    reg.add_root("tool", untrusted, Trust.PROJECT)
    reg.discover()
    specs, report = load_tool_plugins(reg.of_kind("tool"))
    assert "a_tool" in specs
    assert "b_tool" not in specs


# ── 声明与实现分离 ────────────────────────────────────────────────────
def test_missing_risk_defaults_to_l2(tmp_path):
    """清单里没写风险等级 → 按 L2（破坏性）处理。

    宁可严一点：未知工具当成破坏性操作，它就会被审批流程拦一下。
    """
    _make_ext(tmp_path, "norisk", risk=None)
    reg = Registry()
    reg.add_root("tool", tmp_path, Trust.USER)
    reg.discover()
    specs, report = load_tool_plugins(reg.of_kind("tool"))
    assert "norisk_tool" in specs
    assert specs["norisk_tool"].risk == Risk.L2_DESTRUCTIVE
    assert report.loaded[0].declared_risk == "L2_DESTRUCTIVE"


def test_unparsable_risk_defaults_to_l2(tmp_path):
    _make_ext(tmp_path, "weird", risk="超级危险")
    reg = Registry()
    reg.add_root("tool", tmp_path, Trust.USER)
    reg.discover()
    specs, _ = load_tool_plugins(reg.of_kind("tool"))
    assert specs["weird_tool"].risk == Risk.L2_DESTRUCTIVE


def test_declared_risk_is_kept(tmp_path):
    _make_ext(tmp_path, "safe", risk="L0_READ")
    reg = Registry()
    reg.add_root("tool", tmp_path, Trust.USER)
    reg.discover()
    specs, report = load_tool_plugins(reg.of_kind("tool"))
    assert specs["safe_tool"].risk == Risk.L0_READ
    assert report.loaded[0].declared_risk == "L0_READ"


def test_metadata_only_extension_is_skipped(tmp_path):
    """只声明元数据、没有 entry 的 —— 不注册为可调用工具。"""
    d = tmp_path / "metaonly"
    d.mkdir(parents=True)
    (d / "oryx.json").write_text(json.dumps({
        "id": "metaonly", "kind": "tool",
        "tools": [{"name": "x", "description": "只有元数据"}],
    }), encoding="utf-8")
    reg = Registry()
    reg.add_root("tool", tmp_path, Trust.USER)
    reg.discover()
    specs, report = load_tool_plugins(reg.of_kind("tool"))
    assert specs == {}
    assert report.skipped


def test_broken_extension_reports_error(tmp_path):
    """扩展代码坏了要说清楚，不能静默跳过 —— 静默会让人以为工具可用。"""
    _make_ext(tmp_path, "broken", code="this is not valid python !!!")
    reg = Registry()
    reg.add_root("tool", tmp_path, Trust.USER)
    reg.discover()
    specs, report = load_tool_plugins(reg.of_kind("tool"))
    assert specs == {}
    assert report.errors, "加载失败要有错误记录"


def test_missing_entry_file_reports_error(tmp_path):
    d = tmp_path / "nofile"
    d.mkdir(parents=True)
    (d / "oryx.json").write_text(json.dumps({
        "id": "nofile", "kind": "tool",
        "tools": [{"name": "x", "entry": "missing.py:run"}],
    }), encoding="utf-8")
    reg = Registry()
    reg.add_root("tool", tmp_path, Trust.USER)
    reg.discover()
    specs, report = load_tool_plugins(reg.of_kind("tool"))
    assert specs == {}
    assert report.errors


def test_duplicate_tool_name_is_reported(tmp_path):
    """同名工具不能静默覆盖 —— 要报出来。"""
    _make_ext(tmp_path, "a")
    _make_ext(tmp_path, "b")
    for name in ("a", "b"):
        p = tmp_path / name / "oryx.json"
        data = json.loads(p.read_text("utf-8"))
        data["tools"][0]["name"] = "same_tool"
        p.write_text(json.dumps(data), encoding="utf-8")
    reg = Registry()
    reg.add_root("tool", tmp_path, Trust.USER)
    reg.discover()
    specs, report = load_tool_plugins(reg.of_kind("tool"))
    assert len(specs) == 1, "同名只能留一个"
    assert any("重名" in e for e in report.errors), f"重名要报出来：{report.errors}"


def test_load_report_lists_sources(tmp_path):
    """加载报告要能回答「现在到底加载了谁的代码」。"""
    _make_ext(tmp_path, "x")
    reg = Registry()
    reg.add_root("tool", tmp_path, Trust.USER)
    reg.discover()
    _, report = load_tool_plugins(reg.of_kind("tool"))
    assert report.sources and report.sources[0].endswith("tools.py")
    assert "x_tool" in report.summary()


# ── profile ───────────────────────────────────────────────────────────
def test_profile_resolves_includes():
    ps = ProfileSet.from_dict({
        "profiles": {
            "base": {"extensions": ["a"]},
            "full": {"includes": ["base"], "extensions": ["b"]},
        },
    })
    assert ps.resolve("full") == ("a", "b")


def test_profile_cyclic_include_terminates():
    """循环 include 不能无限递归。"""
    ps = ProfileSet.from_dict({
        "profiles": {
            "a": {"includes": ["b"], "extensions": ["x"]},
            "b": {"includes": ["a"], "extensions": ["y"]},
        },
    })
    resolved = ps.resolve("a")
    assert "x" in resolved and "y" in resolved


def test_unknown_profile_allows_everything():
    """profile 写错了名字，不该让扩展全部消失 —— profile 是收窄工具。"""
    ps = ProfileSet.from_dict({"profiles": {"p": {"extensions": ["a"]}}})
    assert ps.allows("不存在的profile", "whatever") is True
    assert ps.allows("", "whatever") is True


def test_profile_filters_registry(tmp_path):
    """profile 排除的扩展不进注册表。"""
    for name in ("keep", "drop"):
        d = tmp_path / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: d\n---\n正文\n", encoding="utf-8"
        )
    ps = ProfileSet.from_dict({"profiles": {"p": {"extensions": ["keep"]}}})
    reg = Registry(roots=[("skill", tmp_path)])
    reg.set_profiles(ps, "p")
    reg.discover()
    assert reg.get("skill", "keep") is not None
    assert reg.get("skill", "drop") is None


def test_profile_does_not_bypass_trust(tmp_path):
    """profile 不能用来绕过信任检查 —— 那是两件独立的事。"""
    _make_ext(tmp_path, "risky")
    ps = ProfileSet.from_dict({"profiles": {"p": {"extensions": ["risky"]}}})
    reg = Registry()
    reg.add_root("tool", tmp_path, Trust.PROJECT)
    reg.set_profiles(ps, "p")
    reg.discover()
    specs, report = load_tool_plugins(reg.of_kind("tool"))
    assert specs == {}, "profile 里点名了也不该绕过信任检查"
    assert report.skipped


# ── 与真实扩展的集成 ──────────────────────────────────────────────────
def test_shipped_extension_is_discovered():
    """仓库里自带的扩展样例应该能被找到（但因信任级别不会被执行）。"""
    ext_dir = CORE.parent / "extensions"
    if not ext_dir.exists():
        pytest.skip("仓库里没有 extensions 目录")
    reg = Registry()
    reg.add_root("tool", ext_dir, Trust.PROJECT)
    reg.discover()
    assert reg.of_kind("tool"), "扩展样例没被发现"
