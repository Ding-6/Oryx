"""第三批 · D6：记忆分层 + 条件规则注入的验证。

核心断言：规则只在适用的文件类型上出现，且注入过一次就不再重复。

运行：python -m tests.verify_batch3_d6
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILED.append(name)


def fresh_oryx(home: str):
    os.environ["ORYX_HOME"] = home
    for mod in [m for m in list(sys.modules) if m.startswith("oryx")]:
        del sys.modules[mod]


def setup(rules: dict[str, str] | None = None, project_memory: str = "",
          user_memory: dict[str, str] | None = None):
    """建一个带规则的工作区，返回 (MemoryService, workspace, home)。"""
    from oryx.memory import MemoryService

    tmp = tempfile.mkdtemp(prefix="oryx_b3d6_")
    home = Path(tmp) / "home"
    ws = Path(tmp) / "ws"
    (home / "memory").mkdir(parents=True)
    ws.mkdir()

    for name, body in (user_memory or {}).items():
        (home / "memory" / f"{name}.md").write_text(body, encoding="utf-8")

    if project_memory:
        (ws / "ORYX.md").write_text(project_memory, encoding="utf-8")

    if rules:
        rdir = ws / ".oryx" / "rules"
        rdir.mkdir(parents=True)
        for name, body in rules.items():
            (rdir / f"{name}.md").write_text(body, encoding="utf-8")

    return MemoryService(store=None, home=home, workspace=ws), ws, home


# ---------------------------------------------------------------- glob 语义
def test_glob_matchbase() -> None:
    """matchBase：不含 / 的模式匹配任意目录。"""
    from oryx.memory import glob_match

    check("*.ts 命中深层目录", glob_match("src/utils/helper.ts", "*.ts"))
    check("*.ts 命中顶层", glob_match("main.ts", "*.ts"))
    check("*.ts 不命中 .tsx", not glob_match("a.tsx", "*.ts"))
    check("src/**/* 命中深层", glob_match("src/a/b.ts", "src/**/*"))
    check("大括号展开", glob_match("src/a.tsx", "src/**/*.{ts,tsx}"))
    check("逗号多模式", glob_match("tests/x.test.ts", "src/**/*.ts, tests/**/*.test.ts"))


# ---------------------------------------------------------------- 条件规则
def test_conditional_rule_trigger() -> None:
    from oryx.memory import ConditionalRule

    r = ConditionalRule("ts", "TS 规范", paths=["src/**/*.ts"])
    check("命中同类文件", r.triggers("src/a/b.ts"))
    check("不命中异类文件", not r.triggers("src/a/b.py"))

    r2 = ConditionalRule("all", "通用规范", always=True)
    check("always 规则恒命中", r2.triggers("anything.txt"))


def test_rules_loaded_from_frontmatter() -> None:
    svc, ws, _ = setup(rules={
        "ts": "---\nid: ts-rule\npaths:\n  - src/**/*.ts\n---\n用 4 空格缩进。\n",
        "always": "---\nid: always-rule\nalwaysApply: true\n---\n提交前跑测试。\n",
    })
    rules = svc.load_all()["rules"]
    check("读到 2 条规则", len(rules) == 2, str([r.id for r in rules]))
    by_id = {r.id: r for r in rules}
    check("条件规则识别 paths", "src/**/*.ts" in by_id["ts-rule"].paths,
          str(by_id["ts-rule"].paths))
    check("always 规则识别", by_id["always-rule"].always is True)
    check("条件规则不标 always", by_id["ts-rule"].always is False)


def test_inject_only_on_match() -> None:
    svc, ws, _ = setup(rules={"ts": "---\nid: ts-rule\npaths: ['src/**/*.ts']\n---\nTS 规范内容。\n"})
    check("碰 TS 文件时注入", "TS 规范内容" in svc.remember("src/a.ts"))
    check("碰 Python 文件不注入", svc.remember("src/a.py") == "")


def test_no_repeat_injection() -> None:
    """同一会话里同一份规范只注入一次。"""
    svc, ws, _ = setup(rules={"ts": "---\nid: ts-rule\npaths: ['src/**/*.ts']\n---\nTS 规范。\n"})
    first = svc.remember("src/a.ts")
    second = svc.remember("src/b.ts")
    check("第一次有内容", "TS 规范" in first, first)
    check("第二次不再注入", second == "", second)


def test_refresh_clears_injected() -> None:
    svc, ws, _ = setup(rules={"ts": "---\nid: ts-rule\npaths: ['src/**/*.ts']\n---\nTS 规范。\n"})
    svc.remember("src/a.ts")
    svc.refresh(ws)
    check("刷新后可以重新注入", "TS 规范" in svc.remember("src/a.ts"))


def test_always_rules_separate() -> None:
    svc, ws, _ = setup(rules={"al": "---\nid: always-rule\nalwaysApply: true\n---\n通用规范。\n"})
    out = svc.always_rules()
    check("always 规则进常驻块", "通用规范" in out, out)
    check("再次调用不再给", svc.always_rules() == "")


def test_rule_size_cap() -> None:
    from oryx.memory import MemoryService

    long_body = "X" * 9000
    svc, ws, _ = setup(rules={"big": "---\nid: big\npaths: ['*.txt']\n---\n" + long_body})
    out = svc.remember("a.txt")
    # 单条截到 MAX_RULE_CHARS（4000），加上标题那行
    check("超长规则被截断", len(out) < 4100, f"len={len(out)}")
    check("截断有说明", "截断" in out, out[-120:])
    check("截断后仍带原文开头", "XXXX" in out[:200], out[:80])


def test_total_inject_cap() -> None:
    rules = {}
    for i in range(10):
        rules[f"r{i}"] = f"---\nid: r{i}\npaths: ['*.txt']\n---\n" + ("Y" * 2000)
    svc, ws, _ = setup(rules=rules)
    out = svc.remember("a.txt")
    check("单次注入总量封顶", len(out) <= MemoryService_cap() + 200,
          f"len={len(out)}")


def MemoryService_cap() -> int:
    from oryx.memory import MemoryService

    return MemoryService.MAX_INJECT_CHARS


# ---------------------------------------------------------------- 分层载入
def test_static_block_layers() -> None:
    svc, ws, home = setup(
        project_memory="项目用 pnpm。",
        user_memory={"prefs": "我喜欢中文注释。"},
    )
    block = svc.static_block()
    check("用户偏好进了常驻块", "中文注释" in block, block)
    check("项目约定进了常驻块", "pnpm" in block, block)


def test_no_memory_no_block() -> None:
    svc, ws, _ = setup()
    check("没有记忆时为空串", svc.static_block() == "", svc.static_block())


def test_agents_md_fallback() -> None:
    """没有 ORYX.md 时兼容 AGENTS.md。"""
    from oryx.memory import MemoryService

    tmp = tempfile.mkdtemp(prefix="oryx_b3d6b_")
    ws = Path(tmp)
    (ws / "AGENTS.md").write_text("用 uv 管理依赖。", encoding="utf-8")
    svc = MemoryService(store=None, home=Path(tmp) / "h", workspace=ws)
    items = svc.load_all()["project"]
    check("AGENTS.md 被识别", items and "uv" in items[0]["text"], str(items))


def test_oryx_md_wins_over_agents() -> None:
    from oryx.memory import MemoryService

    tmp = tempfile.mkdtemp(prefix="oryx_b3d6c_")
    ws = Path(tmp)
    (ws / "ORYX.md").write_text("用 pnpm。", encoding="utf-8")
    (ws / "AGENTS.md").write_text("用 npm。", encoding="utf-8")
    svc = MemoryService(store=None, home=Path(tmp) / "h", workspace=ws)
    items = svc.load_all()["project"]
    check("ORYX.md 优先", len(items) == 1 and "pnpm" in items[0]["text"], str(items))


# ---------------------------------------------------------------- @import
def test_import_expansion() -> None:
    from oryx.memory import MemoryService

    tmp = tempfile.mkdtemp(prefix="oryx_b3d6d_")
    ws = Path(tmp) / "ws"
    (ws / "docs").mkdir(parents=True)
    (ws / "docs" / "style.md").write_text("缩进 4 空格。", encoding="utf-8")
    (ws / "ORYX.md").write_text(
        "规范见 @docs/style.md\n请遵守。", encoding="utf-8"
    )
    svc = MemoryService(store=None, home=Path(tmp) / "h", workspace=ws)
    items = svc.load_all()["project"]
    check("@import 被展开", items and "缩进 4 空格" in items[0]["text"], str(items))


def test_import_ignores_code_block() -> None:
    from oryx.memory import MemoryService

    tmp = tempfile.mkdtemp(prefix="oryx_b3d6e_")
    ws = Path(tmp) / "ws"
    ws.mkdir(parents=True)
    (ws / "ORYX.md").write_text(
        "包名是 `@scope/pkg`，别改。\n", encoding="utf-8"
    )
    svc = MemoryService(store=None, home=Path(tmp) / "h", workspace=ws)
    items = svc.load_all()["project"]
    check("代码内 @ 不当导入", items and "@scope/pkg" in items[0]["text"], str(items))


def test_import_missing_is_dropped() -> None:
    from oryx.memory import MemoryService

    tmp = tempfile.mkdtemp(prefix="oryx_b3d6f_")
    ws = Path(tmp) / "ws"
    ws.mkdir(parents=True)
    (ws / "ORYX.md").write_text("见 @nope/missing.md\n结束。", encoding="utf-8")
    svc = MemoryService(store=None, home=Path(tmp) / "h", workspace=ws)
    items = svc.load_all()["project"]
    check("缺失的 import 不报错", bool(items) and "结束" in items[0]["text"], str(items))


def main() -> int:
    tests = [
        ("D6-1 glob matchBase 语义", test_glob_matchbase),
        ("D6-2 条件规则触发判定", test_conditional_rule_trigger),
        ("D6-3 frontmatter 解析", test_rules_loaded_from_frontmatter),
        ("D6-4 只在匹配时注入", test_inject_only_on_match),
        ("D6-5 不重复注入", test_no_repeat_injection),
        ("D6-6 refresh 后可重注入", test_refresh_clears_injected),
        ("D6-7 always 规则独立", test_always_rules_separate),
        ("D6-8 超长规则截断", test_rule_size_cap),
        ("D6-9 单次注入封顶", test_total_inject_cap),
        ("D6-10 分层载入常驻块", test_static_block_layers),
        ("D6-11 无记忆返回空", test_no_memory_no_block),
        ("D6-12 AGENTS.md 兼容", test_agents_md_fallback),
        ("D6-13 ORYX.md 优先", test_oryx_md_wins_over_agents),
        ("D6-14 @import 展开", test_import_expansion),
        ("D6-15 代码块内 @ 不解析", test_import_ignores_code_block),
        ("D6-16 缺失 import 不报错", test_import_missing_is_dropped),
    ]
    for label, fn in tests:
        print(f"\n[{label}]")
        try:
            fn()
        except Exception as exc:
            import traceback

            traceback.print_exc()
            FAILED.append(label)
    print("\n" + "=" * 60)
    if FAILED:
        print(f"失败 {len(FAILED)} 项：{'、'.join(FAILED)}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())