"""前端质量门禁的测试。

要锁住的：

1. **规则文件本身可靠** —— YAML 能解析、正则能编译。
2. **检查器能发现真问题** —— 往临时目录里塞一段有问题的代码，必须报出来。
3. **上下文感知生效** —— 变量定义、var() fallback、已有替代样式时不该报。
4. **自举承诺** —— Oryx 自己的界面**没有 block 级问题**。
   这是最重要的一条：用没验证过的工具去验证别人，等于没验证。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from oryx.ui_audit import RULE_FILE, audit, load_rules

CORE = Path(__file__).resolve().parents[1]
WEB = CORE / "web"


# ── 规则文件 ──────────────────────────────────────────────────────────
def test_rule_file_parses():
    rules = load_rules()
    assert len(rules) >= 20, "规则集太空了"
    for rule in rules:
        assert rule.get("id"), "规则缺 id"
        assert rule.get("severity") in ("block", "warn", "info"), \
            f"{rule.get('id')} 的 severity 不合法"
        assert rule.get("scope") in ("css", "html", "js", "all"), \
            f"{rule.get('id')} 的 scope 不合法"


def test_all_patterns_compile():
    """每条规则的 pattern 都得能编译成正则 —— 写错的规则等于没有。"""
    for rule in load_rules():
        pattern = rule.get("pattern")
        if not pattern or pattern.startswith("__"):
            continue
        try:
            re.compile(pattern)
        except re.error as exc:
            pytest.fail(f"{rule['id']} 的正则编译失败：{exc}")


def test_rule_ids_unique():
    ids = [r["id"] for r in load_rules()]
    assert len(ids) == len(set(ids)), "规则 id 有重复"


# ── 检查器能发现问题 ──────────────────────────────────────────────────
def _write(tmp_path: Path, css: str = "", html: str = "", js: str = "") -> Path:
    web = tmp_path / "web"
    (web / "styles").mkdir(parents=True, exist_ok=True)
    (web / "modules").mkdir(parents=True, exist_ok=True)
    if css:
        (web / "styles" / "main.css").write_text(css, encoding="utf-8")
    if html:
        (web / "index.html").write_text(html, encoding="utf-8")
    if js:
        (web / "modules" / "a.js").write_text(js, encoding="utf-8")
    return web


def test_finds_transition_all(tmp_path):
    web = _write(tmp_path, css=".x { transition: all .2s; }")
    result = audit(web)
    assert any(f.rule_id == "MOTION-001" for f in result.findings)


def test_finds_outline_none(tmp_path):
    web = _write(tmp_path, css=".x:focus { outline: none; }")
    result = audit(web)
    assert any(f.rule_id == "A11Y-001" for f in result.findings)


def test_finds_missing_alt(tmp_path):
    web = _write(tmp_path, html='<img src="a.png">')
    result = audit(web)
    assert any(f.rule_id == "A11Y-005" for f in result.findings)


def test_clean_code_has_no_findings(tmp_path):
    """干净的代码不该被报出来 —— 误报率高的门禁没人会用。"""
    web = _write(
        tmp_path,
        css=".x { color: var(--ink); transition: color .2s ease-out; }",
        html='<img src="a.png" alt="图" width="10" height="10">'
             '<button aria-label="关">x</button>',
        js="const el = document.querySelector('#x'); el.textContent = 'ok';",
    )
    result = audit(web)
    assert result.findings == [], f"干净代码被报了：{result.findings[:3]}"


# ── 上下文感知 ────────────────────────────────────────────────────────
def test_var_definition_not_flagged(tmp_path):
    """CSS 变量定义处的字面值不是「硬编码」—— 变量的值本来就得写。"""
    web = _write(tmp_path, css=":root { --accent: #4F46E5; }")
    result = audit(web)
    assert not any(f.rule_id == "TOKEN-001" for f in result.findings)


def test_var_fallback_not_flagged(tmp_path):
    """var(--x, #fff) 的 fallback 值是合理的 —— 变量没定义时的兜底。"""
    web = _write(tmp_path, css=".x { background: var(--danger-soft, #fdecec); }")
    result = audit(web)
    assert not any(f.rule_id == "TOKEN-001" for f in result.findings)


def test_requires_absent_suppresses(tmp_path):
    """文件里已有替代方案时，不该再报「缺少替代」。"""
    web = _write(
        tmp_path,
        css=":focus-visible { outline: 2px solid var(--accent); }\n"
            ".x:focus { outline: none; }",
    )
    result = audit(web)
    assert not any(f.rule_id == "A11Y-001" for f in result.findings)


def test_structural_rule_not_duplicated(tmp_path):
    """结构性规则（count）不该在主循环里再匹配一次。"""
    web = _write(tmp_path, html="<h1>标题</h1>")
    result = audit(web)
    assert not any(f.rule_id == "SEM-001" for f in result.findings), \
        "「有唯一 h1」不该在有 h1 时报出来"


def test_heading_order_detected(tmp_path):
    web = _write(tmp_path, html="<h1>a</h1><h3>b</h3>")
    result = audit(web)
    assert any(f.rule_id == "SEM-002" for f in result.findings)


# ── 自举：Oryx 自己的界面必须干净 ──────────────────────────────────────
def test_own_interface_has_no_block_issues():
    """**自举承诺**：Oryx 自己的界面不能有 block 级问题。

    这条最重要 —— 用没验证过的工具去验证别人，等于没验证。
    """
    result = audit(WEB)
    blocks = result.by_severity("block")
    assert not blocks, "自家界面有阻断级问题：\n" + "\n".join(
        f"  {f.file}:{f.line} [{f.rule_id}] {f.description}" for f in blocks
    )


def test_audit_covers_our_own_files():
    """要真的扫到了东西 —— 扫描范围不能是空的。"""
    result = audit(WEB)
    assert result.scanned_files >= 10, f"只扫到 {result.scanned_files} 个文件"
    assert result.rules_total >= 20
