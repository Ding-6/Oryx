"""「注册表是唯一发现入口」的测试。

背景
----
上一轮修好了 `requires` 依赖解析，但审计发现注册表这个「唯一发现入口」
**实际上有三条旁路**：

1. **专家 / Agent 各自扫目录、从不问注册表**
   → 界面上点「停用」，注册表里确实没了，
   但 ExpertLibrary / AgentLibrary 照常加载 —— 停用**完全无效**。

2. **用户目录的技能注册表看得见、SkillLibrary 看不见**
   → 装在 `app_dir()/skills` 的技能，设置页里显示着，
   但模型 `use_skill` 不到 —— 装了等于没装。

3. **插件技能只挂进 SkillLibrary、不进注册表**
   → 它的触发词、契约、`requires` 依赖全都不生效，
   依赖解析也看不见它。

要锁住的：

- 停用对**所有类型**都生效（技能 / 专家 / Agent / 工具扩展）
- 停用名单按**清单 id** 匹配（不只是目录名）
- 用户目录的技能**能用**
- 插件技能**进注册表**、依赖被解析
- 信任级别按**根目录**给（仓库技能不该被误标成「用户信任」）
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from oryx.agents import AgentLibrary
from oryx.experts import ExpertLibrary
from oryx.registry import Registry, Trust
from oryx.skills import SkillLibrary


def _skill(base: Path, name: str, *, desc: str = "测试技能") -> Path:
    d = base / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\n\n正文\n", encoding="utf-8")
    return d


def _expert(base: Path, name: str) -> Path:
    d = base / name
    d.mkdir(parents=True)
    (d / "EXPERT.md").write_text(
        f"---\nname: 专家{name}\ndescription: 测试\n---\n\n正文\n", encoding="utf-8")
    return d


def _agent(base: Path, name: str) -> Path:
    d = base / name
    d.mkdir(parents=True)
    (d / "AGENT.md").write_text(
        f"---\nname: Agent{name}\ndescription: 测试\n---\n\n正文\n", encoding="utf-8")
    return d


# ── 缺陷 1：停用对专家 / Agent 无效 ──────────────────────────────────
def test_disabled_expert_not_loaded(tmp_path):
    """停用的专家**不加载** —— 以前照常加载，停用是装饰品。"""
    ex = _expert(tmp_path, "my-expert")
    reg = Registry(roots=[("expert", tmp_path)], trust_map={"expert": Trust.BUILTIN})
    reg.set_disabled({"my-expert"})
    reg.discover()

    lib = ExpertLibrary(tmp_path, registry=reg)
    assert "my-expert" not in lib.experts, "停用的专家不该被加载"
    assert lib.get("my-expert") is None


def test_disabled_agent_not_loaded(tmp_path):
    """停用的 Agent **不加载** —— 以前照样能被派活。"""
    _agent(tmp_path, "my-agent")
    reg = Registry(roots=[("agent", tmp_path)], trust_map={"agent": Trust.BUILTIN})
    reg.set_disabled({"my-agent"})
    reg.discover()

    lib = AgentLibrary(tmp_path, registry=reg)
    assert "my-agent" not in lib.agents


def test_enabled_expert_still_loaded(tmp_path):
    """没停用的照常加载 —— 不能把功能改没了。"""
    _expert(tmp_path, "keep-me")
    reg = Registry(roots=[("expert", tmp_path)], trust_map={"expert": Trust.BUILTIN})
    reg.discover()

    lib = ExpertLibrary(tmp_path, registry=reg)
    assert "keep-me" in lib.experts


def test_expert_library_without_registry_behaves_as_before(tmp_path):
    """没有注册表时行为与改造前完全一致 —— 老调用方不该受影响。"""
    _expert(tmp_path, "plain")
    lib = ExpertLibrary(tmp_path)
    assert "plain" in lib.experts


def test_agent_library_without_registry_behaves_as_before(tmp_path):
    _agent(tmp_path, "plain")
    lib = AgentLibrary(tmp_path)
    assert "plain" in lib.agents


# ── 缺陷 1 续：停用名单按清单 id 匹配 ────────────────────────────────
def test_disabled_by_manifest_id_works(tmp_path):
    """清单 id ≠ 目录名时，按清单 id 停用**也要生效**。

    界面上显示的、写进 `disabled_extensions` 的都是**清单 id**，
    而注册表以前比的是**目录名** —— 两者不同时停用完全失效。
    """
    d = tmp_path / "dir-name"
    d.mkdir()
    (d / "oryx.json").write_text(json.dumps({
        "id": "real-id", "kind": "tool", "version": "1.0.0", "tools": [],
    }, ensure_ascii=False), encoding="utf-8")

    reg = Registry()
    reg.add_root("tool", tmp_path, Trust.USER)
    reg.set_disabled({"real-id"})
    reg.discover()
    assert reg.get("tool", "real-id") is None, "按清单 id 停用应该生效"


def test_disabled_by_directory_name_still_works(tmp_path):
    """老配置里存的是目录名 —— 那个写法也得继续有效。"""
    d = tmp_path / "dir-name"
    d.mkdir()
    (d / "oryx.json").write_text(json.dumps({
        "id": "real-id", "kind": "tool", "version": "1.0.0", "tools": [],
    }, ensure_ascii=False), encoding="utf-8")

    reg = Registry()
    reg.add_root("tool", tmp_path, Trust.USER)
    reg.set_disabled({"dir-name"})
    reg.discover()
    assert reg.get("tool", "real-id") is None


# ── 缺陷 2：用户目录的技能加载不了 ───────────────────────────────────
def test_user_dir_skill_is_loaded(tmp_path):
    """装在用户目录的技能**要能用** —— 以前注册表看得见、这里看不见。"""
    home = tmp_path / "home"
    _skill(home / "skills", "user-skill")

    reg = Registry(roots=[("skill", home / "skills")],
                   trust_map={"skill": Trust.USER})
    reg.discover()
    assert reg.get("skill", "user-skill") is not None

    lib = SkillLibrary(home / "skills", registry=reg)
    assert "user-skill" in lib.skills, "用户目录的技能应该能加载"
    assert "user-skill" in lib.catalog()


def test_skill_library_scans_registry_roots(tmp_path):
    """目录由注册表说了算 —— 技能库不必知道用户目录的存在。"""
    repo = tmp_path / "repo"
    user = tmp_path / "user"
    _skill(repo, "repo-skill")
    _skill(user, "user-skill")

    reg = Registry(roots=[("skill", user), ("skill", repo)],
                   trust_map={"skill": Trust.USER})
    reg.discover()

    # 技能库只给了 repo 目录，但它应该跟着注册表把 user 也扫了
    lib = SkillLibrary(repo, registry=reg)
    assert "repo-skill" in lib.skills
    assert "user-skill" in lib.skills


def test_disabled_skill_not_loaded(tmp_path):
    _skill(tmp_path, "gone")
    reg = Registry(roots=[("skill", tmp_path)], trust_map={"skill": Trust.USER})
    reg.set_disabled({"gone"})
    reg.discover()

    lib = SkillLibrary(tmp_path, registry=reg)
    assert "gone" not in lib.skills


# ── 缺陷 3：插件技能不进注册表 ───────────────────────────────────────
def test_plugin_skill_enters_registry(tmp_path):
    """插件技能要**进注册表** —— 否则它的触发词、契约、依赖全都不生效。"""
    from oryx.plugins import discover, load_plugin

    plugin_dir = tmp_path / "my-plugin"
    (plugin_dir / ".codebuddy-plugin").mkdir(parents=True)
    (plugin_dir / ".codebuddy-plugin" / "plugin.json").write_text(json.dumps({
        "name": "my-plugin", "version": "1.0.0", "skills": "skills",
    }), encoding="utf-8")
    skill_dir = plugin_dir / "skills" / "plugin-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: plugin-skill\ndescription: 插件技能\n---\n\n正文\n", encoding="utf-8")
    (skill_dir / "oryx.json").write_text(json.dumps({
        "id": "plugin-skill", "kind": "skill", "version": "1.0.0",
        "triggers": ["插件触发"], "description": "插件技能",
    }, ensure_ascii=False), encoding="utf-8")

    plugin = load_plugin(plugin_dir / ".codebuddy-plugin" / "plugin.json")
    assert plugin is not None and plugin.skills_dir is not None

    reg = Registry(roots=[("skill", plugin.skills_dir)],
                   trust_map={"skill": Trust.PROJECT})
    reg.discover()
    rec = reg.get("skill", "plugin-skill")
    assert rec is not None, "插件技能应该在注册表里"
    assert "插件触发" in rec.triggers, "插件技能的触发词应该生效"


def test_plugin_skill_dependency_resolved(tmp_path):
    """插件技能的 `requires` 也要被解析。"""
    from oryx.plugins import load_plugin

    plugin_dir = tmp_path / "my-plugin"
    (plugin_dir / ".codebuddy-plugin").mkdir(parents=True)
    (plugin_dir / ".codebuddy-plugin" / "plugin.json").write_text(json.dumps({
        "name": "my-plugin", "version": "1.0.0", "skills": "skills",
    }), encoding="utf-8")
    skill_dir = plugin_dir / "skills" / "plugin-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: plugin-skill\ndescription: 插件技能\n---\n\n正文\n", encoding="utf-8")
    (skill_dir / "oryx.json").write_text(json.dumps({
        "id": "plugin-skill", "kind": "skill", "version": "1.0.0",
        "requires": {"skills": ["missing-dep"]},
    }, ensure_ascii=False), encoding="utf-8")

    plugin = load_plugin(plugin_dir / ".codebuddy-plugin" / "plugin.json")
    reg = Registry(roots=[("skill", plugin.skills_dir)],
                   trust_map={"skill": Trust.PROJECT})
    reg.discover()
    rec = reg.get("skill", "plugin-skill")
    assert rec.blocked_reason, "插件技能的依赖缺失也该被拦住"


# ── 信任级别按根目录给 ───────────────────────────────────────────────
def test_repo_skill_not_marked_as_user_trust(tmp_path):
    """仓库技能不该被标成「用户信任」。

    `trust_map` 是**按 kind**给的 —— 同一个 kind 挂两个根时，
    两个根会拿到同一个级别。信任看的是「东西从哪来」。
    """
    repo = tmp_path / "repo"
    user = tmp_path / "user"
    _skill(repo, "repo-skill")
    _skill(user, "user-skill")

    reg = Registry()
    reg.add_root("skill", user, Trust.USER)
    reg.add_root("skill", repo, Trust.BUILTIN)
    reg.discover()

    assert reg.get("skill", "user-skill").trust is Trust.USER
    assert reg.get("skill", "repo-skill").trust is Trust.BUILTIN


def test_user_dir_skill_can_declare_elevated_caps(tmp_path):
    """用户目录的技能该能声明高危能力（用户自己放的）。"""
    user = tmp_path / "user"
    d = user / "my-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: x\n---\n\n正文\n", encoding="utf-8")
    (d / "oryx.json").write_text(json.dumps({
        "id": "my-skill", "kind": "skill", "version": "1.0.0",
        "caps": ["proc.spawn"],
    }, ensure_ascii=False), encoding="utf-8")

    reg = Registry()
    reg.add_root("skill", user, Trust.USER)
    reg.discover()
    rec = reg.get("skill", "my-skill")
    assert rec.has_cap("proc.spawn"), "用户目录的技能不该被裁剪能力"


# ── 注册表的新查询能力 ───────────────────────────────────────────────
def test_find_by_id(tmp_path):
    _skill(tmp_path, "only-one")
    reg = Registry(roots=[("skill", tmp_path)], trust_map={"skill": Trust.USER})
    reg.discover()
    rec = reg.find_by_id("only-one")
    assert rec is not None
    assert rec.kind == "skill"


def test_roots_of_kind(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    reg = Registry()
    reg.add_root("skill", a, Trust.USER)
    reg.add_root("skill", b, Trust.USER)
    assert set(reg.roots_of_kind("skill")) == {a, b}
    assert reg.roots_of_kind("expert") == []


def test_is_disabled_query(tmp_path):
    reg = Registry()
    reg.set_disabled({"x"})
    assert reg.is_disabled("x") is True
    assert reg.is_disabled("y") is False


# ── 域层：技能清单要走内核那份权威库 ─────────────────────────────────
def test_skill_domain_uses_remote_authoritative_list(tmp_path, monkeypatch):
    """域层不该自己建 SkillLibrary —— 那会绕过注册表。

    server 侧自己建库的话，停用名单、依赖阻塞全都不生效：
    界面上关掉的技能照样列在技能编辑页里。
    """
    from oryx.domains import skill as skill_domain

    root = tmp_path / "skills"
    root.mkdir()
    monkeypatch.setattr(skill_domain, "skills_root", lambda: root)

    class FakeAgent:
        def call(self, method, timeout=60.0, **params):
            if method == "catalog.skills_detail":
                # 内核说：只有一个技能（另一个被停用了）
                return {"skills": [
                    {"name": "kept", "description": "留着", "path": str(root)},
                ]}
            return {}

    agent = FakeAgent()
    items = skill_domain.list_skills(agent)["skills"]
    assert [x["name"] for x in items] == ["kept"], "域层要以内核那份清单为准"


def test_skill_domain_falls_back_when_kernel_unreachable(tmp_path, monkeypatch):
    """内核问不到时退回本地读盘 —— 不能整页空白。"""
    from oryx.domains import skill as skill_domain

    root = tmp_path / "skills"
    (root / "demo").mkdir(parents=True)
    (root / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: 说明\n---\n\n内容\n", encoding="utf-8")
    monkeypatch.setattr(skill_domain, "skills_root", lambda: root)

    class DeadAgent:
        def call(self, method, timeout=60.0, **params):
            raise RuntimeError("内核未就绪")

    items = skill_domain.list_skills(DeadAgent())["skills"]
    assert any(x["name"] == "demo" for x in items), "内核挂了也要能列出技能"
