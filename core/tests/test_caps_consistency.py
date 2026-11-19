"""能力词表的一致性测试 —— 守住「只有一份来源」。

背景
----
能力（caps）有两处会出现：

* **后端词表** `registry.CAPABILITY_TIERS` —— 扩展能声明什么
* **界面侧映射** `registry.UI_CAP_RULES` —— 能力管哪些 API 路径

以前界面侧那份**硬编码在前端的 `uiext.js` 里**，于是两边各写一份：
改了后端前端不知道，界面上「这个能力到底管哪些接口」没人说得准。

现在后端是唯一来源（`/api/ui/modules` 的 `cap_rules` 随清单下发），
这些测试守住它不再漂回去。

要锁住的
--------
1. 界面侧映射里的能力，后端词表得认识
2. 后端词表里的每个能力，要么在界面侧有映射，要么**显式说明**为什么没有
3. 两边不能既映射又说明（那说明有人没想清楚）
4. 映射里的每个路径前缀，都得**对应真实的 API 端点**
   —— 否则能力表里写着一堆不存在的接口，看着专业、实际没用
5. 前端源码里**不许再硬编码**能力路径表
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[1]
WEB = CORE / "web" / "modules"


@pytest.fixture(scope="module")
def registry():
    from oryx import registry as reg
    return reg


@pytest.fixture(scope="module")
def routes() -> list[str]:
    """服务上真实存在的 API 路径。

    **用 `app.openapi()` 而不是遍历 `app.routes`** ——
    新版 FastAPI 的 `include_router` 把子路由收进一个 `_IncludedRouter`
    对象里，直接遍历 `app.routes` 看不到它们（`getattr(r, "path", "")`
    取到空串）。

    openapi 文档是「真实可用的端点」的权威来源 ✓
    """
    from oryx.server import app
    return sorted(p for p in (app.openapi().get("paths") or {}) if p.startswith("/api"))


# ── 1 & 2：词表覆盖 ────────────────────────────────────────────────────
def test_ui_cap_rules_are_known_capabilities(registry):
    """界面侧映射里的能力，后端词表得认识 —— 否则是凭空发明的词。"""
    unknown = sorted(set(registry.UI_CAP_RULES) - set(registry.CAPABILITY_TIERS))
    assert not unknown, f"界面侧用了后端词表里没有的能力：{unknown}"


def test_every_capability_is_accounted_for(registry):
    """后端词表里的每个能力，都要**有交代**。

    有交代 = 在界面侧有映射，或者在 `UI_CAP_NOTES` 里说明了为什么没有。
    没交代 = 有人加了新能力，忘了想清楚界面侧怎么办。
    """
    accounted = set(registry.UI_CAP_RULES) | set(registry.UI_CAP_NOTES)
    missing = sorted(set(registry.CAPABILITY_TIERS) - accounted)
    assert not missing, (
        f"这些能力没有交代界面侧怎么处理：{missing} —— "
        "要么加进 UI_CAP_RULES，要么在 UI_CAP_NOTES 里说明原因"
    )


def test_no_capability_is_both_mapped_and_explained(registry):
    """既映射又说明 = 有人没想清楚该走哪条路。"""
    both = sorted(set(registry.UI_CAP_RULES) & set(registry.UI_CAP_NOTES))
    assert not both, f"这些能力既在映射表里、又在说明表里：{both}"


def test_cap_notes_explain_why(registry):
    """说明必须真的说明了 —— 空字符串等于没说。"""
    empty = [k for k, v in registry.UI_CAP_NOTES.items() if not v.strip()]
    assert not empty, f"这些能力有说明但说明是空的：{empty}"


# ── 4：映射的路径要对应真实端点 ────────────────────────────────────────
def _path_matches(prefix: str, path: str) -> bool:
    """这个真实端点落在这个前缀的范围内吗？

    与前端 `capsNeededFor` 的匹配规则**必须一致** ——
    两边规则不一样的话，判定结果就会分叉。
    """
    if prefix.endswith("/"):
        return path.startswith(prefix)
    return path == prefix or path.startswith(prefix + "/")


def _caps_for(registry, method: str, path: str) -> list[str]:
    """这个调用需要哪些能力（按后端那张表算）。"""
    need = []
    for cap, spec in registry.UI_CAP_RULES.items():
        for rule in spec.get("rules", []):
            if method not in rule.get("methods", []):
                continue
            if any(_path_matches(p, path) for p in rule.get("paths", [])):
                if cap not in need:
                    need.append(cap)
                break
    return need


def test_ui_cap_paths_match_real_endpoints(registry, routes):
    """映射里的每个路径前缀，都得能匹配到真实的 API 端点。

    不然就是「能力表里写着一堆不存在的接口」——
    看着专业，实际永远匹配不上。
    """
    dead: list[str] = []
    for cap, spec in registry.UI_CAP_RULES.items():
        for rule in spec.get("rules", []):
            for prefix in rule.get("paths", []):
                if not any(_path_matches(prefix, r) for r in routes):
                    dead.append(f"{cap}: {prefix}")
    assert not dead, (
        "这些路径前缀匹配不到任何真实端点：\n  " + "\n  ".join(dead)
    )


def test_ui_cap_methods_are_known(registry):
    """方法名写对了没有 —— 写错的话那条规则永远不触发。"""
    allowed = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}
    bad: list[str] = []
    for cap, spec in registry.UI_CAP_RULES.items():
        for rule in spec.get("rules", []):
            for m in rule.get("methods", []):
                if m.upper() not in allowed:
                    bad.append(f"{cap}: {m}")
    assert not bad, f"这些方法名不认识：{bad}"


def test_ui_cap_rules_have_paths_and_methods(registry):
    """每条规则都要有 paths 与 methods —— 空规则等于没写。"""
    for cap, spec in registry.UI_CAP_RULES.items():
        rules = spec.get("rules")
        assert rules, f"{cap} 没有 rules"
        for i, rule in enumerate(rules):
            assert rule.get("paths"), f"{cap} 第 {i + 1} 条规则没有 paths"
            assert rule.get("methods"), f"{cap} 第 {i + 1} 条规则没有 methods"


# ── 4b：高危端点必须被能力覆盖 ─────────────────────────────────────────
#
# 「能力表里的路径都存在」只保证表写得对；
# 这一条保证**表没漏** —— 新加的敏感端点忘了归类，一样是漏。
#
# 底线清单刻意只列**真正敏感**的：
# 改设置、管自己的东西那些（换主题、改工作区）不算 ——
# 把它们也塞进来，能力表就变成一张谁都不敢维护的清单。
MUST_BE_GUARDED = [
    ("POST", "/api/chat"),                  # 让 AI 干活 → 能执行命令
    ("POST", "/api/tools/search"),
    ("POST", "/api/prompt/optimize"),
    ("POST", "/api/approve"),               # 审批闸门
    ("POST", "/api/interaction"),
    ("POST", "/api/apps/launch"),           # 启动本机应用
    ("POST", "/api/remote/start"),          # 把机器暴露出去
    ("POST", "/api/paste"),                 # 数据出站
    ("POST", "/api/install"),               # 从外头下载
    ("POST", "/api/extensions/install"),
    ("POST", "/api/ui-extensions/install"),
    ("POST", "/api/memory"),                # 写记忆
    ("GET", "/api/memory"),                 # 读记忆
    ("DELETE", "/api/memory/{memory_id}"),  # 删记忆
    ("POST", "/api/index/scan"),            # 扫盘
    ("POST", "/api/index/search"),
    ("GET", "/api/clipboard"),              # 读剪贴板（可能复制了密码）
    ("POST", "/api/sys/query"),            # 查系统设置
    ("GET", "/api/drives"),
]


def test_sensitive_endpoints_are_guarded(registry):
    """这些端点必须被至少一个能力覆盖 —— 漏了就等于没约束。"""
    missing = []
    for method, path in MUST_BE_GUARDED:
        need = _caps_for(registry, method, path)
        if not need:
            missing.append(f"{method} {path}")
    assert not missing, (
        "这些端点没被任何能力覆盖：\n  " + "\n  ".join(missing)
    )


def test_same_path_different_methods_are_separated(registry):
    """同一个路径的不同方法，不该落进同一个能力里。

    记忆就是典型：读是 fs.read、写是 fs.write、删是 fs.delete。
    混在一起的话，「写记忆」会顺带要求「会读记忆」——
    那不是约束，那是刁难。
    """
    read = set(_caps_for(registry, "GET", "/api/memory"))
    write = set(_caps_for(registry, "POST", "/api/memory"))
    delete = set(_caps_for(registry, "DELETE", "/api/memory/xyz"))

    assert "fs.read" in read, f"GET /api/memory 应该要 fs.read，实际 {read}"
    assert "fs.read" not in write, (
        f"POST /api/memory 不该要求 fs.read，实际 {write}"
    )
    assert "fs.write" in write, f"POST /api/memory 应该要 fs.write，实际 {write}"
    assert "fs.delete" in delete, (
        f"DELETE /api/memory/xyz 应该要 fs.delete，实际 {delete}"
    )
    assert "fs.write" not in delete, (
        f"DELETE /api/memory/xyz 不该要求 fs.write，实际 {delete}"
    )


# ── 5：前端不许再硬编码 ────────────────────────────────────────────────
def test_every_capability_has_a_doc(registry):
    """每个能力都得有人话说明 —— 写扩展的人要靠它查「这到底管什么」。"""
    missing = sorted(set(registry.CAPABILITY_TIERS) - set(registry.CAPABILITY_DOCS))
    assert not missing, f"这些能力没有说明：{missing}"
    empty = [k for k, v in registry.CAPABILITY_DOCS.items()
             if not v.get("summary", "").strip()]
    assert not empty, f"这些能力有说明但 summary 是空的：{empty}"


def test_capability_catalog_is_complete(registry):
    """词典的条目与词表一一对应 —— 不多不少。"""
    cat = registry.capability_catalog()
    assert set(cat) == set(registry.CAPABILITY_TIERS), (
        f"词典与词表对不上：多了 {sorted(set(cat) - set(registry.CAPABILITY_TIERS))}，"
        f"少了 {sorted(set(registry.CAPABILITY_TIERS) - set(cat))}"
    )
    for cap, item in cat.items():
        assert item.get("tier") in ("normal", "elevated"), \
            f"{cap} 的 tier 不认识：{item.get('tier')}"
        assert item.get("summary"), f"{cap} 没有 summary"
        # 谁能声明 —— 必须与 UNTRUSTED_ALLOWED 一致
        assert item.get("untrusted_allowed") == (
            cap in registry.UNTRUSTED_ALLOWED), \
            f"{cap} 的 untrusted_allowed 与 UNTRUSTED_ALLOWED 不一致"
        # 界面侧路径 —— 必须与 UI_CAP_RULES 一致
        expected: list[str] = []
        for rule in registry.UI_CAP_RULES.get(cap, {}).get("rules", []):
            for p in rule.get("paths", []):
                if p not in expected:
                    expected.append(p)
        assert item.get("ui_paths") == expected, f"{cap} 的 ui_paths 与表不一致"
        # 界面侧的说明：要么有路径，要么有「为什么不用声明」
        assert expected or item.get("ui_note"), \
            f"{cap} 既没有界面侧路径、也没说明为什么不用声明"


def test_frontend_does_not_hardcode_cap_rules():
    """前端源码里不许再出现硬编码的能力路径表。

    以前 `uiext.js` 里有一份 `const CAP_RULES = {...}` ——
    那是漂移的源头。现在表由后端随清单下发。
    """
    src = (WEB / "uiext.js").read_text(encoding="utf-8")
    # 去掉注释再找 —— 注释里提到 CAP_RULES 是正常的
    code = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    code = re.sub(r"//[^\n]*", "", code)
    assert not re.search(r"const\s+CAP_RULES\s*=", code), (
        "uiext.js 里又出现了硬编码的 CAP_RULES —— "
        "能力表的唯一来源应该在后端 registry.UI_CAP_RULES"
    )


def test_frontend_reads_cap_rules_from_manifest():
    """前端得真的从清单里取表，而不是取不到就退回到自己那份。"""
    src = (WEB / "uiext.js").read_text(encoding="utf-8")
    assert "cap_rules" in src, (
        "uiext.js 没有读清单里的 cap_rules —— 后端下发了表，前端却不用"
    )


# ── 6：清单真的下发了这张表 ────────────────────────────────────────────
def test_ui_modules_manifest_carries_cap_rules(registry):
    """`/api/ui/modules` 的返回里带着能力表。"""
    from oryx import domains

    import inspect
    # 路由已搬进 domains/ui.py —— 不再从 server 取
    src = inspect.getsource(domains.ui.modules)
    assert "cap_rules" in src, "/api/ui/modules 没有下发 cap_rules"
    assert "UI_CAP_RULES" in src, "/api/ui/modules 没有引用后端的能力表"
