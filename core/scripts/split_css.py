"""把 `web/styles/main.css` 按段落拆成多个文件。

为什么拆
--------
`main.css` 2749 行 / 116KB —— 改一处要找半天，
而且「左栏」「右栏」「输入框」这些**明显不同的区域**混在一个文件里。

怎么拆才安全
------------
**按原顺序切，顺序一点不变。**

CSS 的层叠靠**加载顺序** —— 后面的规则覆盖前面的。
如果拆完顺序变了（或者某个选择器的优先级依赖了它前面/后面的规则），
视觉就会悄悄变样，而这种回归极难查。

所以：
* 只在**段落标记**处切，不在规则中间切
* 拆分后仍按原顺序 `<link>`，层叠顺序完全不变
* 拆完**逐像素对比截图**确认没变

用法
----
    python scripts/split_css.py            # 拆
    python scripts/split_css.py --check    # 只检查是否已拆
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"
STYLES = WEB / "styles"
MAIN = STYLES / "main.css"

# (起始行, 文件名, 说明) —— 行号是 1-based，含起始行
SPLITS = [
    (1, "tokens.css", "设计 token：颜色、字号、圆角、阴影（明暗两套）"),
    (176, "rail.css", "左栏：logo、搜索、导航、会话列表、底部账号区"),
    (420, "center.css", "主区：顶栏、消息流、输入框、附件、模式提示"),
    (967, "side.css", "右栏：七个页签、执行步骤、任务、团队、总览"),
    (1302, "panels.css", "面板：团队名册、工作流阶段、统一卡片、产物"),
    (1862, "motion.css", "动效、窄屏自适应、背景图"),
    (2005, "overlays.css", "管家团队、弹层、多 Agent 团队、本地资料"),
    (2548, "composer.css", "知识库与图库、输入框芯片行、发送键"),
]

HEADER = """/* {desc}
   —— 从 `main.css` 按原顺序切出。层叠顺序与拆分前完全一致。 */

"""


def do_split() -> int:
    if not MAIN.exists():
        print(f"{MAIN} 不存在 —— 已经拆过了？")
        return 1
    lines = MAIN.read_text("utf-8").splitlines(keepends=True)
    total = len(lines)
    parts = []
    for i, (start, name, desc) in enumerate(SPLITS):
        end = SPLITS[i + 1][0] - 1 if i + 1 < len(SPLITS) else total + 1
        body = "".join(lines[start - 1:end])
        (STYLES / name).write_text(HEADER.format(desc=desc) + body, encoding="utf-8")
        parts.append(name)
        print(f"  {name:18} {end - start + 1:5} 行  {desc}")

    # index.html：把一个 link 换成多个，**顺序不变**
    idx = WEB / "index.html"
    html = idx.read_text("utf-8")
    links = "\n".join(
        f'<link rel="stylesheet" href="/web/styles/{n}">' for n in parts)
    old = '<link rel="stylesheet" href="/web/styles/main.css">'
    if old not in html:
        print("index.html 里找不到 main.css 的 link —— 可能已经拆过了")
    else:
        html = html.replace(old, links, 1)
        idx.write_text(html, encoding="utf-8")
        print(f"  index.html: 换成 {len(parts)} 个 link（顺序不变）")

    MAIN.unlink()
    print(f"\nmain.css 已删除（拆成 {len(parts)} 个文件，共 {total} 行）")
    return 0


def check() -> int:
    missing = [n for _, n, _ in SPLITS if not (STYLES / n).exists()]
    if MAIN.exists() and not missing:
        print("还没拆")
        return 1
    if missing:
        print(f"缺这些文件：{missing}")
        return 1
    idx = (WEB / "index.html").read_text("utf-8")
    bad = [n for _, n, _ in SPLITS if f'/web/styles/{n}' not in idx]
    if bad:
        print(f"index.html 里没引这些：{bad}")
        return 1
    if "/web/styles/main.css" in idx:
        print("index.html 里还引着 main.css")
        return 1
    print(f"已拆成 {len(SPLITS)} 个文件，index.html 引用完整")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    return check() if args.check else do_split()


if __name__ == "__main__":
    sys.exit(main())
