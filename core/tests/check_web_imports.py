"""前端模块的 import 断链检查。

**为什么要有这个**
----------------
ESM 的导入在**链接期**就解析 —— 从某个模块导一个**不存在的名字**，
浏览器报的是 `SyntaxError`，而且**整个模块都不会加载**。

实测踩过：把 `setDefaultPolicy` 从 policy.js 搬到 core.js 之后，
topbar-ui.js 忘了改导入 —— 结果是**整个顶栏模块没加载**，
交互模式、人设、回答风格、计算模式、默认权限**全都不绑定**，
界面看着正常，一点就废。

这种错没有任何报错会冒到界面上（模块直接不执行），
靠肉眼看是看不出来的 —— 所以得有个脚本盯着。

跑法::

    python core/tests/check_web_imports.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

MODULES_DIR = Path(__file__).resolve().parents[1] / "web" / "modules"

# export function|class|const|let|var 名字
_DECL = re.compile(
    r"export\s+(?:async\s+)?(?:function|class|const|let|var)\s+([A-Za-z_$][\w$]*)"
)
# export { a, b as c }
_BRACE = re.compile(r"export\s*\{([^}]*)\}")
# import { ... } from './x.js'
_IMPORT = re.compile(
    r"import\s*\{([^}]*)\}\s*from\s*['\"]\./([\w.-]+)\.js['\"]"
)


def collect_exports() -> dict[str, set[str]]:
    """每个模块导出了哪些名字。"""
    out: dict[str, set[str]] = {}
    for f in MODULES_DIR.glob("*.js"):
        src = f.read_text("utf-8")
        names: set[str] = set()
        for m in _DECL.finditer(src):
            names.add(m.group(1))
        for m in _BRACE.finditer(src):
            for part in m.group(1).split(","):
                part = part.strip()
                if not part:
                    continue
                if " as " in part:
                    part = part.split(" as ")[-1].strip()
                names.add(part)
        out[f.stem] = names
    return out


def main() -> int:
    exports = collect_exports()
    problems: list[str] = []
    for f in sorted(MODULES_DIR.glob("*.js")):
        src = f.read_text("utf-8")
        for m in _IMPORT.finditer(src):
            wanted = [
                x.strip().split(" as ")[0].strip()
                for x in m.group(1).split(",")
                if x.strip()
            ]
            other = m.group(2)
            have = exports.get(other)
            if have is None:
                problems.append(f"{f.name} 导入了不存在的模块 {other}.js")
                continue
            for w in wanted:
                if w not in have:
                    problems.append(
                        f"{f.name} 从 {other}.js 导入的 `{w}` 不存在"
                        "（会让**整个模块**加载失败，而且不报错到界面上）"
                    )
    if problems:
        print("❌ 前端 import 断链：")
        for p in problems:
            print("   ", p)
        return 1
    print(f"✅ {len(exports)} 个模块的 import 全部对得上")
    return 0


if __name__ == "__main__":
    sys.exit(main())
