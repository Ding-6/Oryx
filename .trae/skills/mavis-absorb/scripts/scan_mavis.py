"""盘点参考项目 Marvis 的当前状态。

参考项目更新后跑这个脚本，重新盘点能力与安全模式，不要拿静态清单当唯一事实。

用法：
    D:\\Anaconda\\envs\\mcp-mw\\python.exe scripts\\scan_mavis.py
    D:\\Anaconda\\envs\\mcp-mw\\python.exe scripts\\scan_mavis.py --root D:\\其他路径\\Mavis-main

输出：JSON（结构化，便于对比）+ 一段人类可读摘要。
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

# 需要重点盯的安全模式：出现即定位行号
SECURITY_SIGNALS: dict[str, re.Pattern[str]] = {
    "密钥打码": re.compile(r"\[redacted\]|redact"),
    "符号链接防护": re.compile(r"follow_symlinks\s*=\s*False|O_NOFOLLOW|readlink|commonpath|isjunction"),
    "预览模式": re.compile(r"dry_run|preview|execute\s*:\s*bool\s*=\s*False"),
    "网络阻断": re.compile(r"network_allowed|_local_only|offline"),
    "独占写入": re.compile(r"open\([^)]*[\"']x[\"']"),
    "参数白名单": re.compile(r"fullmatch|PARAM_VALUE|allowed_chars"),
    "上限自述": re.compile(r"partial|limit_hits|truncated"),
}

# 只读类能力：名字命中即认为只读
READONLY_HINTS = (
    "inspect", "scan", "search", "list", "read", "info", "analyz",
    "audit", "summary", "preview", "doctor",
)


def _find_pkg_root(root: Path) -> Path:
    """找到 marvis 包目录。找不到就返回 root 本身。"""
    for candidate in (root / "marvis", root / "Mavis-main" / "marvis"):
        if candidate.is_dir():
            return candidate
    hits = [p for p in root.rglob("marvis") if p.is_dir() and (p / "cli.py").exists()]
    return hits[0] if hits else root


def _module_inventory(pkg: Path) -> list[dict]:
    out = []
    for path in sorted(pkg.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(pkg.parent).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            out.append({"file": rel, "lines": text.count("\n") + 1, "error": "syntax"})
            continue
        funcs = [
            n.name for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("_")
        ]
        classes = [
            n.name for n in ast.walk(tree)
            if isinstance(n, ast.ClassDef) and not n.name.startswith("_")
        ]
        out.append({
            "file": rel,
            "lines": text.count("\n") + 1,
            "functions": funcs[:40],
            "classes": classes[:20],
        })
    return out


def _security_signals(pkg: Path) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in sorted(pkg.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(pkg.parent).as_posix()
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for label, pattern in SECURITY_SIGNALS.items():
            for idx, line in enumerate(lines, 1):
                if pattern.search(line):
                    found.setdefault(label, []).append(f"{rel}:{idx}")
    return found


def _tool_registry(pkg: Path) -> dict:
    """MCP 工具层：统计注册的工具名。"""
    tools = pkg / "mcp" / "tools" / "__init__.py"
    if not tools.exists():
        return {"found": False}
    text = tools.read_text(encoding="utf-8", errors="replace")
    names = sorted(set(re.findall(r"^\s*\"([a-z][a-z0-9_]{2,})\":\s*\{", text, re.MULTILINE)))
    return {"found": True, "count": len(names), "names": names[:80]}


def _tests(pkg_parent: Path) -> dict:
    tdir = pkg_parent / "tests"
    if not tdir.is_dir():
        return {"found": False}
    files = []
    for path in sorted(tdir.glob("test_*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        cases = len(re.findall(r"^\s*def (test_\w+)", text, re.MULTILINE))
        files.append({"file": path.name, "cases": cases, "lines": text.count("\n") + 1})
    return {"found": True, "total_cases": sum(f["cases"] for f in files), "files": files}


def _version(pkg_parent: Path, pkg: Path | None = None) -> str:
    # 版本可能在包内 __init__.py，也可能在仓库根 pyproject.toml
    roots = [pkg, pkg_parent] if pkg else [pkg_parent]
    for base in roots:
        for name in ("__init__.py", "pyproject.toml"):
            path = base / name
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"__version__\s*=\s*[\"']([^\"']+)", text)
            if m:
                return m.group(1)
            m = re.search(r"^version\s*=\s*[\"']([^\"']+)", text, re.MULTILINE)
            if m:
                return m.group(1)
    return "unknown"


def scan(root: Path) -> dict:
    pkg = _find_pkg_root(root)
    parent = pkg.parent
    mods = _module_inventory(pkg)
    readonly = [
        m["file"] for m in mods
        if any(h in m["file"].lower() for h in READONLY_HINTS)
    ]
    return {
        "root": str(root),
        "package": str(pkg),
        "version": _version(parent, pkg),
        "module_count": len(mods),
        "total_lines": sum(m.get("lines", 0) for m in mods),
        "modules": mods,
        "readonly_candidates": readonly,
        "security_signals": _security_signals(pkg),
        "mcp_tools": _tool_registry(pkg),
        "tests": _tests(parent),
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="盘点 Marvis 参考项目")
    ap.add_argument("--root", default=r"D:\项目\Mavis-main\Mavis-main")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    args = ap.parse_args(argv)

    root = Path(args.root)
    if not root.exists():
        print(f"路径不存在: {root}", file=sys.stderr)
        return 2

    data = scan(root)

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    print(f"参考项目: {data['package']}")
    print(f"版本: {data['version']}")
    print(f"模块数: {data['module_count']}  总行数: {data['total_lines']}")
    print(f"测试: {data['tests'].get('total_cases', 0)} 个用例 / {len(data['tests'].get('files', []))} 个文件")
    print(f"MCP 工具: {data['mcp_tools'].get('count', 0)} 个")
    print()
    print("安全模式命中:")
    for label, hits in data["security_signals"].items():
        print(f"  {label}: {len(hits)} 处")
        for h in hits[:6]:
            print(f"    {h}")
    print()
    print("最大的 8 个模块:")
    for m in sorted(data["modules"], key=lambda x: x.get("lines", 0), reverse=True)[:8]:
        print(f"  {m['file']}: {m.get('lines', 0)} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
