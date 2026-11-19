"""扩展依赖解析的测试。

要锁住的：

1. **声明了就要生效** —— `requires` 以前只解析不使用，
   扩展缺了依赖照样加载。现在缺依赖 = 不加载。
2. **传递闭包** —— A 依赖 B、B 依赖 C、C 缺失 → **B 与 A 都不加载**。
3. **环** —— A 要 B、B 要 A → 两个都不加载（不是无限递归）。
4. **停用 ≠ 没装** —— 两种原因要给用户不同的提示。
5. **版本约束** —— 版本不满足是不加载；对方没写版本号只警告。
6. **不被阻塞的扩展照常加载** —— 不能因为引入了依赖检查就弄坏既有扩展。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from oryx.extdeps import (
    compare_versions,
    parse_requirement,
    parse_requires,
    resolve,
    version_satisfies,
)
from oryx.registry import Registry, Trust


# ── 版本比较 ──────────────────────────────────────────────────────────
def test_compare_versions_orders():
    assert compare_versions("1.2.0", "1.1.0") == 1
    assert compare_versions("1.0.0", "1.0.1") == -1
    assert compare_versions("1.0", "1.0.0") == 0, "短的按 0 补齐"
    assert compare_versions("2.0", "10.0") == -1, "按数字比不是按字符串"


def test_version_satisfies_operators():
    assert version_satisfies("1.2.0", ">=", "1.1.0")
    assert version_satisfies("1.0.0", ">=", "1.1.0") is False
    assert version_satisfies("1.0.0", "<", "1.1.0")
    assert version_satisfies("1.1.0", "==", "1.1.0")
    assert version_satisfies("1.1.0", "!=", "1.1.0") is False
    assert version_satisfies("", "", "") is True, "没约束就是都满足"


def test_version_satisfies_without_actual_version_is_false():
    """对方没写版本号 —— 无法验证，按不满足处理（由调用方决定致命还是警告）。"""
    assert version_satisfies("", ">=", "1.0.0") is False


# ── 解析 ──────────────────────────────────────────────────────────────
def test_parse_requirement_plain():
    req = parse_requirement("web-research")
    assert req is not None
    assert req.ext_id == "web-research"
    assert req.op == ""


def test_parse_requirement_with_min_version():
    req = parse_requirement("web-research@>=1.1.0")
    assert req is not None
    assert req.ext_id == "web-research"
    assert req.op == ">="
    assert req.version == "1.1.0"


def test_parse_requirement_exact_version():
    req = parse_requirement("web-research@1.1.0")
    assert req is not None
    assert req.op == "=="
    assert req.version == "1.1.0"


def test_parse_requires_normalizes_plural():
    """清单里写单数还是复数都认 —— 写清单的人不会去查文档。"""
    out = parse_requires({"skills": ["a"], "experts": ["b"]})
    kinds = {k for k, _ in out}
    assert kinds == {"skill", "expert"}


def test_parse_requires_accepts_string_value():
    out = parse_requires({"skills": "a"})
    assert len(out) == 1
    assert out[0][1].ext_id == "a"


def test_parse_requires_ignores_bad_shape():
    """清单坏了不该让整个扩展加载失败。"""
    assert parse_requires(None) == []
    assert parse_requires("不是字典") == []
    assert parse_requires({"skills": 123}) == []


# ── 记录构造 ──────────────────────────────────────────────────────────
class Rec:
    """最小 ExtensionRecord 替身（只带解析要用的字段）。"""

    def __init__(self, kind: str, ext_id: str, requires=None, version: str = ""):
        self.kind = kind
        self.id = ext_id
        self.requires = requires or {}
        self.version = version


def test_missing_dependency_blocks():
    report = resolve([Rec("skill", "a", {"skills": ["missing"]})])
    assert report.is_blocked("skill", "a")
    assert "missing" in report.reason("skill", "a")


def test_satisfied_dependency_not_blocked():
    report = resolve([
        Rec("skill", "a", {"skills": ["b"]}),
        Rec("skill", "b"),
    ])
    assert not report.is_blocked("skill", "a")
    assert not report.blocked


def test_disabled_dependency_has_distinct_reason():
    """停用与没装要给不同的提示 —— 用户该知道是自己关掉的。"""
    report = resolve([Rec("skill", "a", {"skills": ["b"]})], disabled={"b"})
    reason = report.reason("skill", "a")
    assert "停用" in reason
    assert report.issues[0].reason == "disabled"


def test_version_mismatch_blocks():
    report = resolve([
        Rec("skill", "a", {"skills": ["b@>=2.0.0"]}),
        Rec("skill", "b", version="1.0.0"),
    ])
    assert report.is_blocked("skill", "a")
    assert "版本" in report.reason("skill", "a")


def test_version_match_passes():
    report = resolve([
        Rec("skill", "a", {"skills": ["b@>=1.0.0"]}),
        Rec("skill", "b", version="1.5.0"),
    ])
    assert not report.is_blocked("skill", "a")


def test_missing_version_is_warning_not_fatal():
    """对方没写版本号 —— 无法验证，但不该因此拦住它工作。"""
    report = resolve([
        Rec("skill", "a", {"skills": ["b@>=1.0.0"]}),
        Rec("skill", "b"),
    ])
    assert not report.is_blocked("skill", "a"), "版本未知不该致命"
    warn = [i for i in report.issues if i.reason == "version-unknown"]
    assert warn and not warn[0].fatal


def test_transitive_closure_blocks_upstream():
    """A 依赖 B、B 依赖 C、C 缺失 → B 与 A 都不加载。"""
    report = resolve([
        Rec("skill", "a", {"skills": ["b"]}),
        Rec("skill", "b", {"skills": ["c"]}),
        # C 不存在 —— B 缺依赖，B 被阻塞；A 依赖 B，A 也被阻塞
    ])
    assert report.is_blocked("skill", "b"), "B 缺 C，B 该被阻塞"
    assert report.is_blocked("skill", "a"), "A 依赖 B，B 不可用，A 也该被阻塞"


def test_cross_kind_dependency():
    """依赖可以跨类型 —— 技能依赖专家、工具扩展依赖技能。"""
    report = resolve([
        Rec("tool", "t", {"skills": ["s"]}),
        Rec("skill", "s"),
    ])
    assert not report.is_blocked("tool", "t")

    report2 = resolve([
        Rec("tool", "t", {"skills": ["s"]}),
    ])
    assert report2.is_blocked("tool", "t")


def test_any_kind_dependency():
    """`extensions` = 任意类型，按 id 在所有类型里找。"""
    report = resolve([
        Rec("tool", "t", {"extensions": ["s"]}),
        Rec("skill", "s"),
    ])
    assert not report.is_blocked("tool", "t")


def test_cycle_blocks_both():
    """A 要 B、B 要 A —— 两个都不加载。"""
    report = resolve([
        Rec("skill", "a", {"skills": ["b"]}),
        Rec("skill", "b", {"skills": ["a"]}),
    ])
    assert report.is_blocked("skill", "a")
    assert report.is_blocked("skill", "b")
    assert report.cycles, "环应该被记下来"


def test_self_cycle_blocks():
    report = resolve([Rec("skill", "a", {"skills": ["a"]})])
    assert report.is_blocked("skill", "a")


def test_long_chain_does_not_recurse_forever():
    """很长的依赖链不该撞递归上限（用迭代式 DFS 找环 + BFS 传播阻塞）。"""
    records = [Rec("skill", f"s{i}", {"skills": [f"s{i+1}"]}) for i in range(200)]
    records.append(Rec("skill", "s200"))
    report = resolve(records)
    assert not report.blocked, "链条完整时不该有任何阻塞"


def test_long_chain_with_missing_tail_blocks_all():
    """200 层链，末端缺失 —— 整条链都该被阻塞（传递闭包要一路推到底）。"""
    records = [Rec("skill", f"s{i}", {"skills": [f"s{i+1}"]}) for i in range(200)]
    report = resolve(records)
    assert report.is_blocked("skill", "s199"), "末端缺依赖"
    assert report.is_blocked("skill", "s0"), "阻塞要一路传到链首"


def test_extension_without_requires_is_never_blocked():
    """没声明依赖的扩展永远不受影响 —— 不能因为引入了依赖检查就弄坏既有扩展。"""
    report = resolve([Rec("skill", "plain")])
    assert not report.blocked


# ── 与注册表接起来 ────────────────────────────────────────────────────
def _ext(base: Path, name: str, *, requires=None, version="1.0.0", kind="skill") -> Path:
    d = base / name
    d.mkdir(parents=True)
    manifest = {
        "id": name, "kind": kind, "version": version,
        "displayName": name, "description": "测试扩展",
    }
    if requires:
        manifest["requires"] = requires
    (d / "oryx.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: 测试\n---\n\n正文\n", encoding="utf-8")
    return d


def test_registry_marks_blocked_reason(tmp_path):
    _ext(tmp_path, "a", requires={"skills": ["missing"]})
    reg = Registry()
    reg.add_root("skill", tmp_path, Trust.USER)
    reg.discover()
    rec = reg.get("skill", "a")
    assert rec is not None
    assert rec.blocked_reason, "缺依赖的扩展应该带上原因"
    assert "missing" in rec.blocked_reason


def test_registry_usable_of_kind_excludes_blocked(tmp_path):
    _ext(tmp_path, "ok")
    _ext(tmp_path, "bad", requires={"skills": ["missing"]})
    reg = Registry()
    reg.add_root("skill", tmp_path, Trust.USER)
    reg.discover()
    usable = {r.id for r in reg.usable_of_kind("skill")}
    assert usable == {"ok"}
    assert {r.id for r in reg.blocked_records()} == {"bad"}


def test_registry_dependency_report_exposed(tmp_path):
    _ext(tmp_path, "bad", requires={"skills": ["missing"]})
    reg = Registry()
    reg.add_root("skill", tmp_path, Trust.USER)
    reg.discover()
    report = reg.dep_report
    assert report is not None
    assert report.is_blocked("skill", "bad")
    d = report.to_dict()
    assert d["blocked"], "报告里应该有被阻塞的扩展"


def test_disabled_dependency_blocks_dependent(tmp_path):
    """依赖的扩展被停用 → 依赖它的也不该加载。"""
    _ext(tmp_path, "a", requires={"skills": ["b"]})
    _ext(tmp_path, "b")
    reg = Registry()
    reg.add_root("skill", tmp_path, Trust.USER)
    reg.set_disabled({"b"})
    reg.discover()
    rec = reg.get("skill", "a")
    assert rec is not None
    assert rec.blocked_reason
    assert "停用" in rec.blocked_reason


# ── 工具扩展：被阻塞的扩展不注册工具 ──────────────────────────────────
def test_blocked_extension_tools_not_loaded(tmp_path):
    """缺依赖的扩展 —— 它的工具**一个都不注册**。

    让它半跑比不跑更糟：模型会以为工具可用，调了才出错。
    """
    from oryx.toolplug import load_tool_plugins

    d = tmp_path / "ext"
    d.mkdir()
    (d / "oryx.json").write_text(json.dumps({
        "id": "ext", "kind": "tool", "version": "1.0.0",
        "requires": {"skills": ["missing"]},
        "tools": [{
            "name": "ext_tool", "description": "测试工具",
            "parameters": {"type": "object", "properties": {}},
            "risk": "L0_READ", "entry": "tools.py:run",
        }],
    }, ensure_ascii=False), encoding="utf-8")
    (d / "tools.py").write_text("def run(**kw):\n    return {}\n", encoding="utf-8")

    reg = Registry()
    reg.add_root("tool", tmp_path, Trust.USER)
    reg.discover()

    specs, report = load_tool_plugins(reg.of_kind("tool"))
    assert "ext_tool" not in specs, "被阻塞的扩展不该注册工具"
    assert any("ext_tool" in name for name, _ in report.skipped)
    assert any("依赖不满足" in why for _, why in report.skipped)


def test_ok_extension_tools_still_loaded(tmp_path):
    from oryx.toolplug import load_tool_plugins

    d = tmp_path / "ext"
    d.mkdir()
    (d / "oryx.json").write_text(json.dumps({
        "id": "ext", "kind": "tool", "version": "1.0.0",
        "tools": [{
            "name": "ext_tool", "description": "测试工具",
            "parameters": {"type": "object", "properties": {}},
            "risk": "L0_READ", "entry": "tools.py:run",
        }],
    }, ensure_ascii=False), encoding="utf-8")
    (d / "tools.py").write_text("def run(**kw):\n    return {}\n", encoding="utf-8")

    reg = Registry()
    reg.add_root("tool", tmp_path, Trust.USER)
    reg.discover()

    specs, _report = load_tool_plugins(reg.of_kind("tool"))
    assert "ext_tool" in specs, "没问题的扩展照常加载"


# ── 技能目录：被阻塞的技能不列出来 ────────────────────────────────────
def test_blocked_skill_not_in_catalog(tmp_path):
    """缺依赖的技能不该出现在目录里 —— 让模型去用它只会卡住。"""
    from oryx.skills import SkillLibrary

    _ext(tmp_path, "good")
    _ext(tmp_path, "bad", requires={"skills": ["missing"]})

    reg = Registry()
    reg.add_root("skill", tmp_path, Trust.USER)
    reg.discover()

    lib = SkillLibrary(tmp_path, registry=reg)
    catalog = lib.catalog()
    assert "good" in catalog
    assert "bad" not in catalog
    assert lib.usable("bad") is None
    assert lib.get("bad") is not None, "被阻塞的技能仍然存在 —— 用户要看得见"


def test_blocked_skill_excluded_from_match(tmp_path):
    from oryx.skills import SkillLibrary

    _ext(tmp_path, "bad", requires={"skills": ["missing"]})
    reg = Registry()
    reg.add_root("skill", tmp_path, Trust.USER)
    reg.discover()
    lib = SkillLibrary(tmp_path, registry=reg)
    assert lib.match("bad") == []


# ── 修复建议：用户看到「没满足」之后该做什么 ──────────────────────────
def test_missing_dependency_carries_fix_hint():
    """依赖没装上 → 得告诉用户**该做什么**，不只是说缺了。"""
    report = resolve([Rec("skill", "a", {"skills": ["missing"]})])
    issue = next(i for i in report.issues if i.reason == "missing")
    assert issue.required_kind == "skill", "得记住缺的是哪一类"
    assert issue.fix, "得给出修复建议"
    assert "missing" in issue.fix, issue.fix


def test_dependency_kind_is_labelled():
    """「依赖的扩展 X 不存在」没说 X 是什么 —— 技能？专家？界面扩展？

    用户分不清的话，根本不知道该去哪儿找它。
    """
    report = resolve([Rec("tool", "a", {"ui": ["panel-x"]})])
    issue = next(i for i in report.issues if i.reason == "missing")
    d = issue.to_dict()
    assert d["required_kind_label"] == "界面扩展", d
    assert d["required_kind_hint"], "还得说清这类东西是干什么的"


def test_disabled_dependency_suggests_enabling():
    """被停用 → 该建议「启用」，不是「装上」—— 它已经装着了。"""
    report = resolve([Rec("skill", "a", {"skills": ["b"]})], disabled={"b"})
    issue = next(i for i in report.issues if i.reason == "disabled")
    assert "启用" in issue.fix, issue.fix


def test_version_mismatch_suggests_upgrading():
    report = resolve([
        Rec("skill", "a", {"skills": ["b@>=2.0.0"]}),
        Rec("skill", "b", version="1.0.0"),
    ])
    issue = next(i for i in report.issues if i.reason == "version")
    assert "升级" in issue.fix, issue.fix
    assert "2.0.0" in issue.fix, "得说清要升到哪个版本"


def test_every_fatal_issue_has_a_fix():
    """任何一条致命问题都得有修复建议 —— 说不清怎么修的，等于没说。"""
    report = resolve([
        Rec("skill", "a", {"skills": ["missing"]}),
        Rec("skill", "b", {"skills": ["gone"]}),
    ])
    fatal = [i for i in report.issues if i.fatal]
    assert fatal
    for i in fatal:
        assert i.fix, f"{i.ext_id} 的问题没有修复建议：{i.detail}"


def test_fix_hint_never_empty():
    """兜底：任何 reason 都得能生成建议（新增 reason 时不会漏）。"""
    from oryx.extdeps import fix_hint
    for reason in ("missing", "disabled", "version", "version-unknown", "cycle"):
        assert fix_hint(reason, "skill", "x").strip(), reason
    # 未知 reason 也不能返回空 —— 宁可给个泛泛的
    assert fix_hint("something-new", "skill", "x").strip()


# ── 修复建议：用户看到「没满足」之后该做什么 ──────────────────────────
def test_missing_dependency_carries_fix_hint():
    """依赖没装上 → 得告诉用户**该做什么**，不只是说缺了。"""
    report = resolve([Rec("skill", "a", {"skills": ["missing"]})])
    issue = next(i for i in report.issues if i.reason == "missing")
    assert issue.required_kind == "skill", "得记住缺的是哪一类"
    assert issue.fix, "得给出修复建议"
    assert "missing" in issue.fix, issue.fix


def test_dependency_kind_is_labelled():
    """「依赖的扩展 X 不存在」没说 X 是什么 —— 技能？专家？界面扩展？

    用户分不清的话，根本不知道该去哪儿找它。
    """
    report = resolve([Rec("tool", "a", {"ui": ["panel-x"]})])
    issue = next(i for i in report.issues if i.reason == "missing")
    d = issue.to_dict()
    assert d["required_kind_label"] == "界面扩展", d
    assert d["required_kind_hint"], "还得说清这类东西是干什么的"


def test_disabled_dependency_suggests_enabling():
    """被停用 → 该建议「启用」，不是「装上」—— 它已经装着了。"""
    report = resolve([Rec("skill", "a", {"skills": ["b"]})], disabled={"b"})
    issue = next(i for i in report.issues if i.reason == "disabled")
    assert "启用" in issue.fix, issue.fix


def test_version_mismatch_suggests_upgrading():
    report = resolve([
        Rec("skill", "a", {"skills": ["b@>=2.0.0"]}),
        Rec("skill", "b", version="1.0.0"),
    ])
    issue = next(i for i in report.issues if i.reason == "version")
    assert "升级" in issue.fix, issue.fix
    assert "2.0.0" in issue.fix, "得说清要升到哪个版本"


def test_every_fatal_issue_has_a_fix():
    """任何一条致命问题都得有修复建议 —— 说不清怎么修的，等于没说。"""
    report = resolve([
        Rec("skill", "a", {"skills": ["missing"]}),
        Rec("skill", "b", {"skills": ["gone"]}),
    ])
    fatal = [i for i in report.issues if i.fatal]
    assert fatal
    for i in fatal:
        assert i.fix, f"{i.ext_id} 的问题没有修复建议：{i.detail}"


def test_fix_hint_never_empty():
    """兜底：任何 reason 都得能生成建议（新增 reason 时不会漏）。"""
    from oryx.extdeps import fix_hint
    for reason in ("missing", "disabled", "version", "version-unknown", "cycle"):
        assert fix_hint(reason, "skill", "x").strip(), reason
    # 未知 reason 也不能返回空 —— 宁可给个泛泛的
    assert fix_hint("something-new", "skill", "x").strip()
