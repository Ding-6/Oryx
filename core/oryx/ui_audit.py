"""前端质量检查 —— 用规则集扫自己的界面代码。

为什么要有
----------
「AI 生成的代码能跑但没工程感」在界面上的表现很具体：
`outline: none` 没给替代焦点样式、动效动的是 `width` 不是 `transform`、
颜色全是硬编码十六进制、中文正文 11px。

这些都能**静态检出** —— 不需要浏览器、不需要渲染。
调研时找到的现成规则集（taste-lint 149 条、web-interface-guidelines 104 条、
interface-proof 14 类、Frontend-Quality 7 类、hallmark、argus、impeccable）
全部是静态可判定的，先移植了一批高频、可自动判定的到 `rules/ui-quality.yaml`。

**规则文件格式与 `rules/dangerous.yaml` 同构**（id/category/description/pattern），
所以加载、解析、分级都能复用 policy 那套 —— 不另起炉灶。

分级
----
`block` / `warn` / `info` 三档，对应 ECC 的 hook profile 分级思路：
阻断项必须改，警告项多数该改，提示项是风格建议。

用法
----
    python -m oryx.ui_audit              # 扫 core/web/
    python -m oryx.ui_audit --json       # 机器可读
    python -m oryx.ui_audit --strict     # 有 block 就退出码非 0

**先跑自己**：这个检查器扫的就是自己的界面代码，
所以它自己必须先过 —— 否则就是「用没验证的工具去验证别人」。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CORE_DIR = Path(__file__).resolve().parents[1]
WEB_DIR = CORE_DIR / "web"
RULE_FILE = Path(__file__).resolve().parent / "rules" / "ui-quality.yaml"

SEVERITY_ORDER = {"block": 0, "warn": 1, "info": 2}


@dataclass
class Finding:
    """一条检查结果。"""

    rule_id: str
    category: str
    severity: str
    description: str
    file: str
    line: int
    snippet: str = ""
    hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.rule_id,
            "category": self.category,
            "severity": self.severity,
            "description": self.description,
            "file": self.file,
            "line": self.line,
            "snippet": self.snippet[:160],
            "hint": self.hint,
        }


@dataclass
class AuditResult:
    findings: list[Finding] = field(default_factory=list)
    scanned_files: int = 0
    rules_total: int = 0

    def by_severity(self, sev: str) -> list[Finding]:
        return [f for f in self.findings if f.severity == sev]

    def counts(self) -> dict[str, int]:
        return {s: len(self.by_severity(s)) for s in SEVERITY_ORDER}

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanned_files": self.scanned_files,
            "rules_total": self.rules_total,
            "counts": self.counts(),
            "findings": [f.to_dict() for f in self.findings],
        }


def load_rules(path: Path = RULE_FILE) -> list[dict[str, Any]]:
    """读规则文件。

    规则格式与 `rules/dangerous.yaml` 一致：`rules:` 列表，每项有
    id / category / description / pattern，加上本文件专用的
    severity / scope / hint / count / heading_order / case_insensitive。
    """
    import yaml

    data = yaml.safe_load(path.read_text("utf-8")) or {}
    rules = data.get("rules") or []
    out: list[dict[str, Any]] = []
    for rule in rules:
        if not isinstance(rule, dict) or not rule.get("id"):
            continue
        rule.setdefault("severity", "warn")
        rule.setdefault("scope", "all")
        rule.setdefault("category", "MISC")
        out.append(rule)
    return out


def _compile(rule: dict[str, Any]) -> re.Pattern[str] | None:
    pattern = rule.get("pattern")
    if not pattern or pattern.startswith("__"):
        return None
    flags = re.IGNORECASE if rule.get("case_insensitive") else 0
    try:
        return re.compile(pattern, flags)
    except re.error:
        return None


def _compile_opt(value: Any) -> re.Pattern[str] | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return re.compile(value)
    except re.error:
        return None


def _is_var_definition(line: str) -> bool:
    """这一行是不是 CSS 变量定义。

    `--accent: #3b82f6;` 里的十六进制是**定义**，不是「硬编码」——
    变量的值本来就得写字面值。把它报成硬编码，等于要求变量不能有值。

    判据：行内任意位置出现 `--名字:` 就是定义行。
    不要求在行首 —— `:root { --accent: #fff; --bg: #000; }` 这种一行多个的写法
    也要认出来。

    排除 `var(--x, ...)`：那里的 `--x` 是**引用**不是定义，
    但它后面跟的是逗号不是冒号，所以不会被这个判据命中。
    """
    return bool(re.search(r"(?<![\w-])--[\w-]+\s*:", line))


def _strip_comments(text: str) -> str:
    """把 CSS 注释替换成**等长空格** —— 行号与列号都保持不变。

    为什么
    ------
    注释里写的字面量**不是代码**。比如：

        /* 开关的滑块 —— 走变量，别硬编码 #fff */

    这行注释里有 `#fff`，审计按行匹配就把它当成硬编码颜色报了 ——
    明明已经改成了 `var(--bg-2)`，还因为**注释里提了一嘴**而红。

    替换成等长空格而不是删掉，是为了**行号不变** ——
    报错的行号要能对上源文件。
    """
    out = []
    i = 0
    n = len(text)
    while i < n:
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0:
                out.append(" " * (n - i))
                break
            out.append(" " * (end + 2 - i))
            i = end + 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _line_of(text: str, pos: int) -> str:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    if end < 0:
        end = len(text)
    return text[start:end]


def _context_ok(rule: dict[str, Any], text: str, file_text_cache: dict[str, bool]) -> bool:
    """上下文条件：不满足就**不算违规**。

    这是让规则「准」的关键。举例：
    `outline: none` 本身不是 bug —— 前提是**没有**别处的焦点样式。
    界面上已经有 `:focus-visible { outline: 2px solid var(--accent) }`，
    这条 outline:none（用 border 做焦点态）就**不是问题**。

    规则里写 `requires_absent: ":focus-visible"` 表示：
    文件里存在 :focus-visible 时，这条规则不报。
    """
    require_absent = rule.get("requires_absent")
    if not require_absent:
        return True
    key = f"absent:{require_absent}"
    if key not in file_text_cache:
        file_text_cache[key] = require_absent not in text
    return file_text_cache[key]


def _exclude_ok(rule: dict[str, Any], line: str) -> bool:
    """行内排除：这一行匹配 exclude 就不算违规。

    用途：同一行里既有违规模式又有正当理由时。
    比如 `font-size:11px` 用于角标是可接受的（辅助文字），
    但用于正文不行 —— 那种要靠 `max_font_size` 之类更细的规则。
    """
    ex = rule.get("exclude")
    if not ex:
        return True
    rx = _compile_opt(ex)
    if rx is None:
        return True
    return not rx.search(line)


def _files_for_scope(scope: str, web_dir: Path) -> list[Path]:
    if scope == "css":
        return sorted(web_dir.glob("styles/*.css"))
    if scope == "html":
        return sorted(web_dir.glob("*.html"))
    if scope == "js":
        return sorted(web_dir.glob("modules/*.js"))
    # all：三类都扫
    return (
        sorted(web_dir.glob("styles/*.css"))
        + sorted(web_dir.glob("*.html"))
        + sorted(web_dir.glob("modules/*.js"))
    )


def _scope_of(path: Path, web_dir: Path) -> str:
    rel = path.relative_to(web_dir).as_posix()
    if rel.endswith(".css"):
        return "css"
    if rel.endswith(".html"):
        return "html"
    return "js"


def audit(web_dir: Path = WEB_DIR, rules: list[dict[str, Any]] | None = None) -> AuditResult:
    """扫界面代码，返回结果。"""
    rules = rules if rules is not None else load_rules()
    result = AuditResult(rules_total=len(rules))

    # 按 scope 归类规则，避免同一文件重复编译
    compiled: dict[str, list[tuple[dict[str, Any], re.Pattern[str]]]] = {}
    for rule in rules:
        rx = _compile(rule)
        if rx is None:
            continue
        compiled.setdefault(rule["scope"], []).append((rule, rx))

    scanned: set[Path] = set()
    # CSS scope 的上下文用**所有样式文件的合并文本** ——
    # 规则的 `requires_absent` 问的是「有没有别处的焦点样式」，
    # 「别处」本来就可能写在**别的文件**里。
    # 以前所有样式在一个 main.css 里，只看当前文件恰好也对；
    # 样式拆成多个文件后只看当前文件就误报了
    # （panels.css 里的 .inp 用 outline:none + :focus 边框做焦点态，
    #   但 :focus-visible 写在 center.css 里 → 被误判成「没有替代焦点样式」）。
    css_context = ""
    if "css" in compiled:
        parts = []
        for p in _files_for_scope("css", web_dir):
            try:
                parts.append(p.read_text("utf-8"))
            except OSError:
                continue
        css_context = "\n".join(parts)

    for scope, items in compiled.items():
        for path in _files_for_scope(scope, web_dir):
            if not path.exists():
                continue
            scanned.add(path)
            try:
                text = path.read_text("utf-8")
            except OSError:
                continue
            rel = path.relative_to(web_dir).as_posix()
            # 匹配用**剥掉注释**的文本 —— 注释里的字面量不是代码。
            # 行号不变（_strip_comments 用等长空格替换），报错仍能对上源文件。
            scan_text = _strip_comments(text) if path.suffix == ".css" else text
            ctx_cache: dict[str, bool] = {}
            ctx_text = css_context if scope == "css" else text
            for rule, rx in items:
                # 结构性规则（count / heading_order）由下面的专门逻辑处理 ——
                # 它们的 pattern 只是「找候选」，不是「匹配即违规」。
                # 走主循环会产生重复误报（比如「有唯一 h1」在有 h1 时也报）。
                if rule.get("count") or rule.get("heading_order"):
                    continue
                # 上下文不满足 → 整条规则对这个文件不适用
                if not _context_ok(rule, ctx_text, ctx_cache):
                    continue
                for m in rx.finditer(scan_text):
                    line_text = _line_of(text, m.start())
                    # 变量定义处的字面值不是硬编码
                    if rule.get("ignore_var_defs") and _is_var_definition(line_text):
                        continue
                    # 行内排除
                    if not _exclude_ok(rule, line_text):
                        continue
                    line_no = text.count("\n", 0, m.start()) + 1
                    result.findings.append(Finding(
                        rule_id=rule["id"],
                        category=rule.get("category", "MISC"),
                        severity=rule.get("severity", "warn"),
                        description=rule.get("description", ""),
                        file=rel,
                        line=line_no,
                        snippet=line_text.strip(),
                        hint=rule.get("hint", ""),
                    ))
    result.scanned_files = len(scanned)

    # ── 计数型规则（count: 1 表示「恰好 1 个」）──────────────────
    for rule in rules:
        want = rule.get("count")
        if not want:
            continue
        scope = rule.get("scope", "all")
        hits = 0
        for path in _files_for_scope(scope, web_dir):
            if not path.exists():
                continue
            try:
                text = path.read_text("utf-8")
            except OSError:
                continue
            rx = _compile(rule)
            if rx is None:
                continue
            hits += len(rx.findall(text))
            if hits > want:
                rel = path.relative_to(web_dir).as_posix()
                result.findings.append(Finding(
                    rule_id=rule["id"], category=rule.get("category", "MISC"),
                    severity=rule.get("severity", "warn"),
                    description=f"{rule.get('description','')}（实际 {hits} 个）",
                    file=rel, line=1,
                ))
                break

    # ── 标题层级连续性 ────────────────────────────────────────────
    for rule in rules:
        if not rule.get("heading_order"):
            continue
        for path in _files_for_scope(rule.get("scope", "html"), web_dir):
            if not path.exists():
                continue
            try:
                text = path.read_text("utf-8")
            except OSError:
                continue
            levels = [int(m) for m in re.findall(r"<h([1-6])\b", text)]
            rel = path.relative_to(web_dir).as_posix()
            for i in range(1, len(levels)):
                if levels[i] - levels[i - 1] > 1:
                    result.findings.append(Finding(
                        rule_id=rule["id"], category=rule.get("category", "MISC"),
                        severity=rule.get("severity", "warn"),
                        description=f"{rule.get('description','')}（h{levels[i-1]} 后直接 h{levels[i]}）",
                        file=rel, line=1,
                    ))
                    break

    result.findings.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.file, f.line))
    return result


def format_report(result: AuditResult, findings: list[Finding] | None = None) -> str:
    shown = findings if findings is not None else result.findings
    lines = [
        "=" * 60,
        "前端质量检查",
        "=" * 60,
        f"扫描文件：{result.scanned_files}　规则：{result.rules_total}　"
        f"结果：{result.counts()}",
        "",
    ]
    if not shown:
        lines.append("✓ 没有发现问题")
        return "\n".join(lines)
    current = None
    for f in shown:
        if f.severity != current:
            current = f.severity
            label = {"block": "阻断", "warn": "警告", "info": "提示"}.get(current, current)
            lines.append(f"── {label} ──")
        lines.append(f"  {f.file}:{f.line}  [{f.rule_id}] {f.description}")
        if f.snippet:
            lines.append(f"      {f.snippet[:110]}")
        if f.hint:
            lines.append(f"      → {f.hint}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="前端质量检查")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--strict", action="store_true", help="有 block 级问题就退出码非 0")
    ap.add_argument("--web-dir", default=str(WEB_DIR), help="要扫的目录")
    ap.add_argument(
        "--min-severity", default="warn", choices=["block", "warn", "info"],
        help="只显示不低于该级别的问题（默认 warn —— "
             "info 级是风格建议，日常显示几百条会淹掉真问题）",
    )
    args = ap.parse_args(argv)

    result = audit(Path(args.web_dir))
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        limit = SEVERITY_ORDER[args.min_severity]
        shown = [f for f in result.findings if SEVERITY_ORDER.get(f.severity, 9) <= limit]
        print(format_report(result, shown))
    if args.strict and result.by_severity("block"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
