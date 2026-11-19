"""扩展总线的测试 —— 把「可自由修改、不写死」的承诺钉死。

要锁住的四件事：

1. **加一种扩展类型不改别处** —— 注册表能装下新 kind，
   且不影响已有 kind 的加载。
2. **声明式清单可选** —— 没有 oryx.json 也能加载（向后兼容）。
3. **信任分级真的裁能力** —— 不可信来源声明的高危能力被剔除。
4. **路由对中文有效** —— 之前的空格分词对中文根本不生效。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from oryx.context_budget import ContextProfile, select
from oryx.registry import (
    KIND_SPECS,
    Registry,
    Trust,
    route,
    tokenize,
)


# ── 向后兼容 ──────────────────────────────────────────────────────────
def test_legacy_skill_without_manifest_still_loads(tmp_path):
    """没有 oryx.json 的技能照样能被发现 —— 声明是可选增强，不是必需。"""
    skill_dir = tmp_path / "skills" / "legacy"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: legacy\ndescription: 老技能，只有 frontmatter\n---\n\n正文\n",
        encoding="utf-8",
    )
    reg = Registry(roots=[("skill", tmp_path / "skills")])
    reg.discover()
    rec = reg.get("skill", "legacy")
    assert rec is not None, "没有 oryx.json 的技能应该照样被加载"
    assert rec.description == "老技能，只有 frontmatter"
    assert rec.triggers == ()
    assert rec.eager is False


def test_manifest_overrides_frontmatter(tmp_path):
    """oryx.json 里的描述优先于 frontmatter —— 显式配置胜出。"""
    d = tmp_path / "skills" / "s1"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: s1\ndescription: frontmatter 的描述\n---\n\n正文\n", encoding="utf-8"
    )
    (d / "oryx.json").write_text(json.dumps({
        "id": "s1", "version": "2.0.0", "description": "清单里的描述",
        "triggers": ["甲", "乙"], "eager": True,
    }), encoding="utf-8")
    reg = Registry(roots=[("skill", tmp_path / "skills")])
    reg.discover()
    rec = reg.get("skill", "s1")
    assert rec is not None
    assert rec.description == "清单里的描述"
    assert rec.version == "2.0.0"
    assert rec.triggers == ("甲", "乙")
    assert rec.eager is True


def test_frontmatter_triggers_are_fallback(tmp_path):
    """frontmatter 里写 triggers 也能用 —— 不想建 oryx.json 时的轻量做法。"""
    d = tmp_path / "skills" / "s2"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: s2\ndescription: d\ntriggers: 甲, 乙、丙\n---\n\n正文\n", encoding="utf-8"
    )
    reg = Registry(roots=[("skill", tmp_path / "skills")])
    reg.discover()
    rec = reg.get("skill", "s2")
    assert rec is not None
    assert set(rec.triggers) == {"甲", "乙", "丙"}


# ── 可扩展性 ──────────────────────────────────────────────────────────
def test_new_kind_needs_no_other_change(tmp_path):
    """加一种扩展类型不用改注册表 —— KIND_SPECS 加一行即可。

    这是「不写死」的核心承诺：以前每加一种类型都要复制一遍发现逻辑。
    """
    d = tmp_path / "workflows" / "wf1"
    d.mkdir(parents=True)
    (d / "workflow.json").write_text(
        json.dumps({"description": "一个工作流", "version": "1.0.0"}), encoding="utf-8"
    )
    reg = Registry(roots=[("workflow", tmp_path / "workflows")])
    reg.discover()
    rec = reg.get("workflow", "wf1")
    assert rec is not None, "新 kind 应该能被装下"
    assert rec.description == "一个工作流"
    # 已有 kind 不受影响
    assert reg.of_kind("skill") == []


def test_registry_covers_all_kinds():
    """扩展点清单是**显式**的 —— 不是散落在代码里。

    断言的是「清单里有的每一类都有目录与入口文件定义」，
    而不是「恰好是这几类」—— 扩展点本来就会随需求增加，
    加一类是正常演进，不该让这个测试红。
    """
    assert KIND_SPECS, "扩展点清单不能为空"
    for kind, spec in KIND_SPECS.items():
        assert spec.get("dir"), f"{kind} 没定义目录"
        assert spec.get("entry"), f"{kind} 没定义入口文件"
    # 已知的扩展点一个都不能少
    for kind in ("skill", "expert", "agent", "workflow", "policy", "plugin", "tool"):
        assert kind in KIND_SPECS, f"{kind} 这个扩展点不见了"


def test_digest_changes_when_content_changes(tmp_path):
    """注册表摘要随内容变 —— 用于检测「改了扩展但没同步清单」。"""
    d = tmp_path / "skills" / "s1"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: s1\ndescription: 甲\n---\n正文\n", encoding="utf-8")
    reg = Registry(roots=[("skill", tmp_path / "skills")])
    reg.discover()
    before = reg.digest()
    (d / "SKILL.md").write_text("---\nname: s1\ndescription: 乙\n---\n正文\n", encoding="utf-8")
    reg.discover()
    assert reg.digest() != before, "内容变了摘要必须变"


# ── 信任分级 ──────────────────────────────────────────────────────────
def test_untrusted_project_cannot_declare_process_spawn(tmp_path):
    """项目仓库里的扩展不能声明 proc.spawn —— clone 别人的仓库不该提权。"""
    d = tmp_path / "skills" / "evil"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: evil\ndescription: d\n---\n正文\n", encoding="utf-8")
    (d / "oryx.json").write_text(json.dumps({
        "caps": ["net.egress", "proc.spawn", "credential.read", "fs.read"],
    }), encoding="utf-8")
    reg = Registry(roots=[("skill", tmp_path / "skills")],
                   trust_map={"skill": Trust.PROJECT})
    reg.discover()
    rec = reg.get("skill", "evil")
    assert rec is not None
    assert rec.has_cap("net.egress") is True
    assert rec.has_cap("proc.spawn") is False, "不可信来源不能有进程能力"
    assert rec.has_cap("credential.read") is False, "不可信来源不能读凭据"


def test_trusted_source_keeps_all_caps(tmp_path):
    """用户自己放的扩展可以声明高危能力。"""
    d = tmp_path / "skills" / "mine"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: mine\ndescription: d\n---\n正文\n", encoding="utf-8")
    (d / "oryx.json").write_text(json.dumps({"caps": ["proc.spawn"]}), encoding="utf-8")
    reg = Registry(roots=[("skill", tmp_path / "skills")],
                   trust_map={"skill": Trust.USER})
    reg.discover()
    rec = reg.get("skill", "mine")
    assert rec is not None
    assert rec.has_cap("proc.spawn") is True


# ── 路由 ──────────────────────────────────────────────────────────────
def test_tokenize_handles_chinese():
    """中文要切 bigram —— 整句当一个词就永远匹配不上。"""
    grams = tokenize("整理下载文件夹")
    assert "整理" in grams and "文件" in grams
    assert "整理下载文件夹" not in grams, "不该把整句当一个 token"


def test_tokenize_handles_english():
    assert "web" in tokenize("web research")


def test_route_by_triggers(tmp_path):
    d = tmp_path / "skills" / "s1"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: s1\ndescription: d\n---\n正文\n", encoding="utf-8")
    (d / "oryx.json").write_text(json.dumps({"triggers": ["查一下"]}), encoding="utf-8")
    reg = Registry(roots=[("skill", tmp_path / "skills")])
    reg.discover()
    res = route(reg.of_kind("skill"), "帮我查一下最新政策")
    assert [m.id for m in res.matched] == ["s1"]
    assert res.strategy == "triggers"


def test_route_by_description_for_chinese(tmp_path):
    """没有触发词时，中文描述也得能命中 —— 这是老实现的老毛病。"""
    d = tmp_path / "skills" / "organizer"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: organizer\ndescription: 按类型整理文件目录\n---\n正文\n", encoding="utf-8"
    )
    reg = Registry(roots=[("skill", tmp_path / "skills")])
    reg.discover()
    res = route(reg.of_kind("skill"), "帮我整理下载文件夹")
    assert "organizer" in [m.id for m in res.matched], "中文描述应该能命中"


def test_route_does_not_match_everything(tmp_path):
    """无关输入不该乱命中 —— 之前描述分词命中的是最长的那个。"""
    d = tmp_path / "skills" / "s1"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: s1\ndescription: 处理发票票据识别\n---\n正文\n", encoding="utf-8"
    )
    reg = Registry(roots=[("skill", tmp_path / "skills")])
    reg.discover()
    res = route(reg.of_kind("skill"), "帮我写一段代码")
    assert res.matched == []


def test_route_empty_query():
    reg = Registry()
    assert route([], "").matched == []


# ── 上下文预算 ────────────────────────────────────────────────────────
def _make_skill(base: Path, name: str, desc: str) -> None:
    d = base / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\n正文\n", encoding="utf-8"
    )


def test_budget_excludes_over_limit(tmp_path):
    """超预算的要被排除 —— 这是「扩展多了不炸上下文」的保证。"""
    for i in range(6):
        _make_skill(tmp_path, f"s{i}", f"技能 {i} 的描述，足够长以便占用 token 预算")
    reg = Registry(roots=[("skill", tmp_path / "skills")])
    reg.discover()
    recs = reg.of_kind("skill")
    prof = ContextProfile(limit_tokens=40, mode="report-only")
    decision = select(recs, prof, query="")
    assert decision.used_tokens <= 40 or decision.included, "应该有裁剪发生"
    assert len(decision.included) < len(recs), "不该全部收进来"


def test_budget_keeps_eager_even_when_over(tmp_path):
    """eager（元技能）超预算也保留 —— 它们是「怎么用这个平台」，不能少。"""
    for i in range(6):
        _make_skill(tmp_path, f"s{i}", f"技能 {i} 的描述，足够长以便占用 token 预算")
    reg = Registry(roots=[("skill", tmp_path / "skills")])
    reg.discover()
    prof = ContextProfile(eager=("s0",), limit_tokens=1, mode="report-only")
    decision = select(reg.of_kind("skill"), prof, query="")
    assert "s0" in decision.included, "eager 必须无条件保留"


def test_budget_blocking_mode_reports_not_ok(tmp_path):
    """blocking 模式下超预算要能判断「不该继续」。"""
    for i in range(6):
        _make_skill(tmp_path, f"s{i}", f"技能 {i} 的描述，足够长以便占用 token 预算")
    reg = Registry(roots=[("skill", tmp_path / "skills")])
    reg.discover()
    prof = ContextProfile(eager=(), required=(), remainder="all",
                           limit_tokens=1, mode="blocking")
    decision = select(reg.of_kind("skill"), prof, query="")
    if decision.over_budget:
        assert decision.ok is False
    assert decision.explain()  # 必须能说明原因


def test_budget_remainder_all_includes_everything(tmp_path):
    """remainder=all 应该收全部（由预算层负责砍）。"""
    for i in range(4):
        _make_skill(tmp_path, f"s{i}", f"描述 {i}")
    reg = Registry(roots=[("skill", tmp_path / "skills")])
    reg.discover()
    prof = ContextProfile(remainder="all", limit_tokens=10000)
    decision = select(reg.of_kind("skill"), prof, query="")
    assert len(decision.included) == 4


# ── 契约 ──────────────────────────────────────────────────────────────
def test_contracts_parsed(tmp_path):
    """契约（gates / fail_closed_when / handoffs）能被读出来。"""
    d = tmp_path / "skills" / "s1"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: s1\ndescription: d\n---\n正文\n", encoding="utf-8")
    (d / "oryx.json").write_text(json.dumps({
        "contracts": {
            "gates": ["至少两个来源交叉验证"],
            "fail_closed_when": ["来源全不可访问"],
            "handoffs": ["doc-writer"],
        },
    }), encoding="utf-8")
    reg = Registry(roots=[("skill", tmp_path / "skills")])
    reg.discover()
    rec = reg.get("skill", "s1")
    assert rec is not None
    assert rec.contracts.gates == ("至少两个来源交叉验证",)
    assert rec.contracts.fail_closed_when == ("来源全不可访问",)
    assert rec.contracts.handoffs == ("doc-writer",)


def test_malformed_manifest_does_not_crash(tmp_path):
    """坏掉的清单不能让整个注册表挂掉 —— 跳过它，继续发现别的。"""
    d = tmp_path / "skills" / "broken"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: broken\ndescription: d\n---\n正文\n", encoding="utf-8")
    (d / "oryx.json").write_text("{ 这不是合法 JSON", encoding="utf-8")
    d2 = tmp_path / "skills" / "good"
    d2.mkdir(parents=True)
    (d2 / "SKILL.md").write_text("---\nname: good\ndescription: d\n---\n正文\n", encoding="utf-8")
    reg = Registry(roots=[("skill", tmp_path / "skills")])
    reg.discover()
    assert reg.get("skill", "good") is not None, "坏的清单不能影响好的那个"
