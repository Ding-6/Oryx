"""防回归：模块里不能有**未定义的名字**。

为什么需要这个
--------------
2026-11-19 抓到两个真实的 500：

* `domains/index.py` 用了 `JSONResponse`（第 99、148 行）和
  `StreamingResponse`（第 224 行），但 `from fastapi.responses import`
  只导了 `FileResponse, Response` —— 两个名字**根本没导入**。
  后果：`GET /api/drives` 500，`GET /api/image/thumb` 500
  （图片网格里**每一张**缩略图都在 500）。
* 这类 bug 编译器不管、pytest 也不管 —— 除非正好有测试打到那条路由。

所以这里做一次**全模块静态检查**：任何被引用却没有定义的名字，直接报错。

为什么不用 pyflakes / ruff
-------------------------
这个项目**没有依赖清单**（阶段一的体检发现），加 linter 意味着先引入
一整套工具链。标准库的 `symtable` 够用了：它做的是真正的作用域分析，
能分辨「局部变量 / 参数 / 全局 / 内建」。零依赖，随项目走。

判定规则（刻意保守，宁可漏报不要误报）
--------------------------------------
收集**所有**作用域里绑定过的名字（模块级 + 各函数的局部 + 参数），
加上 Python 内建名。凡是被**读取**却不在这个集合里的名字，就是未定义。

这个口径会漏掉「A 函数里定义了 x，B 函数用了 x」这类跨函数误用 ——
那属于代码质量问题，不是未定义，交给 review 看。
本检查只抓「**谁都没定义过**」这种硬错。
"""

from __future__ import annotations

import builtins
import symtable
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
ORYX = CORE / "oryx"

BUILTINS = set(dir(builtins))

# Python 在导入模块时注入的模块级名字。
# symtable 看不到它们（它们不是源码里写的），但代码里可以直接用
# —— `__file__` 在 agentlink.py 里就用到了。
MODULE_DUNDERS = {
    "__name__", "__file__", "__doc__", "__package__", "__spec__",
    "__loader__", "__builtins__", "__debug__", "__path__", "__cached__",
}

# Python 隐式提供的名字。
# `__class__` 是零参数 `super()` 和 `self.__class__` 在类体里**隐式**建的 cell ——
# 源码里根本没写它，但 symtable 会把它标成自由变量（localfs/loop/mcp/providers 都有）。
IMPLICIT = {"__class__", "__qualname__"}


def _bound_in(table: symtable.SymbolTable) -> set[str]:
    """这个作用域**自己**绑定过的名字（import / 赋值 / 参数）。"""
    return {
        sym.get_name()
        for sym in table.get_symbols()
        if sym.is_assigned() or sym.is_imported() or sym.is_parameter()
    }


def find_undefined(source: str, filename: str) -> list[str]:
    """返回这个文件里所有未定义的名字。

    「可见的名字」= 内建 ∪ 模块级绑定 ∪ **所有外层作用域**绑定。
    最后一层是关键：嵌套函数引用外层函数的局部变量（闭包）是合法的，
    早先没算这一层，把 `self` / `rt` / `port` 全误报成未定义。
    """
    top = symtable.symtable(source, filename, "exec")
    problems: list[str] = []

    def walk(table: symtable.SymbolTable, visible: set[str]) -> None:
        here = _bound_in(table)
        scope_visible = visible | here
        for sym in table.get_symbols():
            name = sym.get_name()
            if not sym.is_referenced():
                continue
            if sym.is_assigned() or sym.is_imported() or sym.is_parameter():
                continue
            if (
                name in scope_visible
                or name in BUILTINS
                or name in MODULE_DUNDERS
                or name in IMPLICIT
            ):
                continue
            problems.append(name)
        for child in table.get_children():
            walk(child, scope_visible)

    walk(top, set())
    return sorted(set(problems))


def _read(py: Path) -> str:
    """读源码。

    用 `utf-8-sig` —— 本项目有 3 个文件带 UTF-8 BOM
    （loop.py / server.py / workflow.py）。
    Python 自己的分词器会**剥掉** BOM，所以导入没事；
    但 `symtable` 不剥，会报 "invalid non-printable character U+FEFF"。
    读文件时统一用 utf-8-sig，两边行为就对齐了。
    """
    return py.read_text(encoding="utf-8-sig")


def test_no_module_uses_undefined_names():
    """全量模块过一遍 —— 有未定义名就报出文件与行号线索。"""
    offenders: list[str] = []
    checked = 0
    for py in sorted(ORYX.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        try:
            source = _read(py)
        except (OSError, UnicodeDecodeError):
            continue
        try:
            symtable.symtable(source, str(py), "exec")
        except SyntaxError as exc:
            offenders.append(f"{py.relative_to(CORE)}: 语法错误 {exc}")
            continue
        checked += 1
        missing = find_undefined(source, str(py))
        if missing:
            rel = py.relative_to(CORE)
            offenders.append(f"{rel}: 未定义的名字 {missing}")

    assert not offenders, (
        "这些模块里有未定义的名字（运行到就会 NameError → 500）：\n  "
        + "\n  ".join(offenders)
        + f"\n\n（已检查 {checked} 个模块）"
    )
