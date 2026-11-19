"""读取前端源码的辅助函数。

背景
----
前端原来是**一个** `web/index.html`（内联 CSS + 内联 JS）。
第三轮把它拆成了：

    web/index.html          只留壳（HTML 结构 + 入口标签）
    web/styles/main.css     样式
    web/modules/*.js        ES Module（37 → 22 个）
    web/_source/app.js      拆分基准快照

所以「读前端源码」这件事不能再简单地 `read_text(index.html)` ——
那样只能看到壳，断言全都会失效。

这里提供两个视角：

  * `page_source()` —— index.html + 全部模块 + 全部样式，**合并视图**。
    适合查「某个函数 / 选择器 / 接口路径还在不在」这类断言。
  * `module_source(name)` —— 单个模块的源码。适合定位「是哪个模块的问题」。

注意：合并视图**不代表运行时的作用域** —— 模块之间的引用关系
要看 `web/modules/` 下的 import/export。但对「某段代码是否还存在」
这类断言，合并视图够用，而且比逐个模块查更省事。
"""
from __future__ import annotations

from pathlib import Path

WEB_DIR = Path(__file__).resolve().parents[1] / "web"
INDEX = WEB_DIR / "index.html"
MODULES_DIR = WEB_DIR / "modules"
STYLES_DIR = WEB_DIR / "styles"
SOURCE_DIR = WEB_DIR / "_source"


def page_source(include_source_snapshot: bool = False) -> str:
    """返回 index.html + 所有模块 + 所有样式的合并文本。"""
    parts: list[str] = [INDEX.read_text("utf-8")]
    for f in sorted(MODULES_DIR.glob("*.js")):
        parts.append(f.read_text("utf-8"))
    for f in sorted(STYLES_DIR.glob("*.css")):
        parts.append(f.read_text("utf-8"))
    if include_source_snapshot:
        src = SOURCE_DIR / "app.js"
        if src.exists():
            parts.append(src.read_text("utf-8"))
    return "\n".join(parts)


def module_source(name: str) -> str:
    """返回单个模块的源码（name 不带 .js）。"""
    return (MODULES_DIR / f"{name}.js").read_text("utf-8")


def module_names() -> list[str]:
    """所有模块名（不含 .js）。"""
    return sorted(f.stem for f in MODULES_DIR.glob("*.js"))
