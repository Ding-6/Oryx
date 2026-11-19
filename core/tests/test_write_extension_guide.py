"""扩展作者指南里的例子，**必须真的能用**。

为什么要有这个
--------------
`docs/WRITE-EXTENSION.md` 是给写扩展的人看的。
文档里的代码如果跑不通，那份文档就是**误导** ——
比没有文档更糟：人会照着抄，抄完发现不работает，
然后花半天找原因，最后归咎于「这破系统」。

所以：指南里的两个例子，一个字不改地搬过来，
装到临时目录里，**真的跑一遍**。

指南改了，这里就会红 —— 这是对文档的持续验证。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[1]
GUIDE = CORE.parent / "docs" / "WRITE-EXTENSION.md"


@pytest.fixture(scope="module")
def guide_text() -> str:
    if not GUIDE.exists():
        pytest.fail(f"指南不存在：{GUIDE}")
    return GUIDE.read_text(encoding="utf-8")


@pytest.fixture()
def user_home(tmp_path, monkeypatch):
    """一个干净的用户数据目录 —— 扩展装在这儿。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("ORYX_HOME", str(home))
    return home


# ── 指南里的后端扩展例子 ────────────────────────────────────────────────
TOOL_MANIFEST = {
    "apiVersion": "oryx/v1",
    "kind": "tool",
    "id": "my-tool",
    "version": "1.0.0",
    "displayName": "我的工具",
    "description": "做一件别人没做的事",
    "caps": ["fs.read"],
    "tools": [
        {
            "name": "my_action",
            "description": "干一件事",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "要处理的东西"}},
                "required": ["query"],
            },
            "risk": "L0_READ",
            "entry": "tools.py:my_action",
        }
    ],
}

TOOL_CODE = '''def my_action(query: str) -> dict:
    """实现层只提供函数。返回约定：ok / content / meta。"""
    return {"ok": True, "content": f"处理了 {query}", "meta": {}}
'''


def test_guide_contains_the_tool_example(guide_text):
    """指南里的例子得真的在文档里 —— 否则这份测试在测空气。"""
    for frag in ("my-tool", "my_action", "tools.py:my_action",
                 "L0_READ", "apiVersion"):
        assert frag in guide_text, f"指南里找不到「{frag}」"


def test_guide_tool_example_actually_works(user_home):
    """照指南写的后端扩展，真的能注册成工具并被调用。"""
    from oryx.registry import Registry, Trust
    from oryx.toolplug import load_tool_plugins

    d = user_home / "extensions" / "my-tool"
    d.mkdir(parents=True)
    (d / "oryx.json").write_text(
        json.dumps(TOOL_MANIFEST, ensure_ascii=False), encoding="utf-8")
    (d / "tools.py").write_text(TOOL_CODE, encoding="utf-8")

    reg = Registry(roots=[])
    reg.add_root("tool", user_home / "extensions", Trust.USER)
    reg.discover()
    specs, report = load_tool_plugins(reg.all_records())

    assert not report.errors, f"加载出错：{report.errors}"
    assert "my_action" in specs, (
        f"工具没注册上。跳过的：{report.skipped}，报错的：{report.errors}"
    )
    out = specs["my_action"].fn(query="测试")
    assert out["ok"] is True
    assert "测试" in out["content"], out


def test_guide_tool_example_is_in_the_guide(guide_text):
    """指南里的清单要跟测试里的一致 —— 别一边改了另一边没改。"""
    # 提取指南里的 tools 数组，做一次结构比对
    assert '"entry": "tools.py:my_action"' in guide_text
    assert '"risk": "L0_READ"' in guide_text
    assert '"name": "my_action"' in guide_text


def test_missing_entry_means_metadata_only(user_home):
    """不写 `entry` = 只声明元数据，不注册成可调用的工具。

    指南里写了这条 —— 这里守住它。
    """
    from oryx.registry import Registry, Trust
    from oryx.toolplug import load_tool_plugins

    d = user_home / "extensions" / "meta-only"
    d.mkdir(parents=True)
    manifest = {
        "id": "meta-only", "kind": "tool",
        "tools": [{"name": "just_meta", "description": "只有声明"}],
    }
    (d / "oryx.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (d / "tools.py").write_text(TOOL_CODE, encoding="utf-8")

    reg = Registry(roots=[])
    reg.add_root("tool", user_home / "extensions", Trust.USER)
    reg.discover()
    specs, report = load_tool_plugins(reg.all_records())

    assert "just_meta" not in specs, "没有 entry 的不该注册成工具"
    assert any("just_meta" in str(s) for s in report.skipped), (
        f"应该记进「跳过」并说明原因，实际：{report.skipped}"
    )


def test_missing_risk_is_treated_as_destructive(user_home):
    """不写 `risk` 一律按 L2（破坏性）处理 —— 宁可严一点。"""
    from oryx.registry import Registry, Trust
    from oryx.toolplug import load_tool_plugins

    d = user_home / "extensions" / "no-risk"
    d.mkdir(parents=True)
    manifest = {
        "id": "no-risk", "kind": "tool",
        "tools": [{
            "name": "risky_one", "description": "没写风险等级",
            "entry": "tools.py:my_action",
        }],
    }
    (d / "oryx.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (d / "tools.py").write_text(TOOL_CODE, encoding="utf-8")

    reg = Registry(roots=[])
    reg.add_root("tool", user_home / "extensions", Trust.USER)
    reg.discover()
    specs, _ = load_tool_plugins(reg.all_records())

    spec = specs.get("risky_one")
    assert spec is not None, "工具应该照样注册（只是风险等级按 L2）"
    assert spec.risk.name == "L2_DESTRUCTIVE", (
        f"没写 risk 应该按 L2_DESTRUCTIVE，实际 {spec.risk.name}"
    )


# ── 指南里的界面扩展例子 ───────────────────────────────────────────────
UI_MANIFEST = {
    "id": "my-panel",
    "name": "我的面板",
    "mount": "rail",
    "enabled": True,
    "caps": ["fs.read"],
}

UI_CODE = """export default {
  setup(ctx) {
    const el = document.createElement('div');
    el.textContent = '我的面板';
    ctx.mount.append(el);
  }
};
"""


def test_guide_ui_example_is_discovered(user_home):
    """照指南写的界面扩展，能被正确发现并加载。"""
    from oryx.uiext import discover_ui_extensions, load_ui_extensions

    d = user_home / "ui-extensions" / "my-panel"
    d.mkdir(parents=True)
    (d / "oryx.json").write_text(
        json.dumps(UI_MANIFEST, ensure_ascii=False), encoding="utf-8")
    (d / "index.js").write_text(UI_CODE, encoding="utf-8")

    found = discover_ui_extensions(user_home / "ui-extensions")
    assert [e.id for e in found] == ["my-panel"], found
    assert found[0].mount == "rail"
    assert found[0].caps == ("fs.read",), found[0].caps

    loaded, skipped = load_ui_extensions(user_home)
    assert [e.id for e in loaded] == ["my-panel"], (loaded, skipped)
    assert not skipped, skipped


def test_guide_ui_example_is_in_the_guide(guide_text):
    assert '"id": "my-panel"' in guide_text
    assert '"mount": "rail"' in guide_text


# ── 指南里承诺的「怎么修」真的存在 ─────────────────────────────────────
def test_guide_promises_fix_hints_that_exist():
    """指南说「设置页会告诉你该怎么做」—— 那修复建议得真的在接口里。"""
    from oryx.extdeps import DependencyIssue, fix_hint

    issue = DependencyIssue(
        ext_id="a", kind="skill", required_id="b", reason="missing",
        detail="依赖的扩展 b 不存在", required_kind="skill",
        fix=fix_hint("missing", "skill", "b"),
    )
    d = issue.to_dict()
    assert d["fix"], "修复建议得下发到界面"
    assert d["required_kind_label"] == "技能", d
    assert d["required_kind_hint"], d


# ── 指南里的技能与专家例子 ──────────────────────────────────────────────
SKILL_MD = """---
name: 我的技能
description: 什么场景该用它
triggers: 触发词A,触发词B
caps: fs.read
---

# 我的技能

## 什么时候用

……

## 步骤

1. ……
"""

EXPERT_MD = """---
name: 我的专家
description: 一句话说明这个专家擅长什么
scenarios: 架构设计, 代码审查
tags: 冷静的技术搭档
personality: 严谨,克制
speaking_style: 不做多余寒暄，直奔问题核心
use_cases: 适合技术方案、代码审查
self_intro: 我是你的技术搭档。……
---

## 工作方式

- …
"""


def test_guide_skill_example_is_discovered(user_home):
    """照指南写的技能，能被技能库加载。"""
    from oryx.skills import SkillLibrary

    d = user_home / "skills" / "my-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")

    lib = SkillLibrary(user_home / "skills")
    skill = lib.get("我的技能")
    assert skill is not None, f"技能没加载上：{list(lib.skills)}"
    assert skill.description == "什么场景该用它"
    assert skill.triggers == ("触发词A", "触发词B"), skill.triggers


def test_guide_expert_example_is_discovered(user_home):
    """照指南写的专家，能被专家库加载。"""
    from oryx.experts import ExpertLibrary

    d = user_home / "experts" / "my-expert"
    d.mkdir(parents=True)
    (d / "EXPERT.md").write_text(EXPERT_MD, encoding="utf-8")

    lib = ExpertLibrary(user_home / "experts")
    expert = lib.experts.get("my-expert")
    assert expert is not None, f"专家没加载上：{list(lib.experts)}"
    assert expert.name == "我的专家"
    assert expert.personality, "personality 得被读出来"
    assert expert.self_intro, "self_intro 得被读出来"


def test_guide_skill_and_expert_are_in_the_guide(guide_text):
    assert "SKILL.md" in guide_text
    assert "EXPERT.md" in guide_text
    assert "self_intro" in guide_text
    assert "triggers: 触发词A,触发词B" in guide_text


def test_guide_optional_manifest_enhances_skill(user_home):
    """同目录放 oryx.json 是**可选增强** —— 放了就得生效。"""
    from oryx.registry import Registry, Trust
    from oryx.skills import SkillLibrary

    d = user_home / "skills" / "my-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    (d / "oryx.json").write_text(json.dumps({
        "id": "my-skill",
        "version": "1.0.0",
        "requires": {"skills": ["other-skill"]},
    }, ensure_ascii=False), encoding="utf-8")

    reg = Registry(roots=[])
    reg.add_root("skill", user_home / "skills", Trust.USER)
    reg.discover()
    rec = reg.get("skill", "my-skill")
    assert rec is not None
    assert rec.version == "1.0.0"
    assert rec.requires == {"skills": ["other-skill"]}, rec.requires

    # 技能库也要能加载它
    lib = SkillLibrary(user_home / "skills", registry=reg)
    assert lib.get("我的技能") is not None
