"""检查各视图页的卡片网格：**同一行的卡片要一样高**。

为什么要有这个
------------
卡片高度不一这件事，**肉眼看截图很容易判错**：
同一行不等高会被看成「没对齐」，但跨行不一样高是正常的
（每行有自己的内容，行高本来就不同）。

所以这个工具**按行分组**报：
哪一行的卡片高度不一致，一眼就能看出来。

用法：python scripts/check_grid.py
退出码：0 = 每行都等高；1 = 有行不等高
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE))
sys.path.insert(0, str(CORE / "tests"))

from playwright.sync_api import sync_playwright  # noqa: E402

from _webdrive import start_server, stop_server, wait_ready  # noqa: E402

PORT = 8791
VIEWS = ["work", "market", "automations", "workflows", "butlers", "persona"]


def main() -> int:
    from check_web_modules import find_chrome
    find_chrome()
    problems: list[str] = []
    home = Path("D:/OryxData/tmp/diag-home")
    home.mkdir(parents=True, exist_ok=True)
    srv = start_server(PORT, home, "diag-token")
    port = getattr(srv, "oryx_port", PORT)
    try:
        if not wait_ready(port, timeout=45):
            print("服务没起来")
            return 1
        with sync_playwright() as p:
            b = None
            for ch in ("chrome", "msedge", None):
                try:
                    b = p.chromium.launch(headless=True, channel=ch,
                                         args=["--no-sandbox", "--disable-gpu"])
                    break
                except Exception:
                    continue
            ctx = b.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
            pg = ctx.new_page()
            pg.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
            pg.wait_for_timeout(3500)
            for v in VIEWS:
                pg.evaluate(
                    """(v) => document.querySelector('.navitem[data-view="' + v + '"]').click()""",
                    v,
                )
                pg.wait_for_timeout(1600)
                out = pg.evaluate(
                    """() => {
                    const res = [];
                    for (const grid of document.querySelectorAll('#page .grid, #page .ucgrid')) {
                      const cs = getComputedStyle(grid);
                      const kids = [...grid.children].map(e => ({
                        cls: e.className.split(' ')[0],
                        h: Math.round(e.getBoundingClientRect().height),
                      }));
                      res.push({
                        gridCls: grid.className,
                        display: cs.display,
                        alignItems: cs.alignItems,
                        cols: cs.gridTemplateColumns.split(' ').length,
                        // **不要去重** —— 去重后就分不出哪张卡属于哪一行了
                        heights: kids.map(k => k.h),
                        n: kids.length,
                      });
                    }
                    return res;
                }"""
                )
                print(f"── {v} ──")
                for g in out:
                    # 按行分组：grid 里每行的卡片高度应该一致
                    cols = g["cols"] or 1
                    rows = [g["heights"][i:i + cols] for i in range(0, len(g["heights"]), cols)]
                    print(f"   {g['gridCls']}  {g['n']} 张 / {cols} 列")
                    bad = False
                    for i, row in enumerate(rows):
                        same = len(set(row)) == 1
                        if not same:
                            bad = True
                        print(f"     第 {i+1} 行: {row}  {'等高 ✓' if same else '**不等高**'}")
                    if bad:
                        problems.append(v)
            b.close()
    finally:
        stop_server(srv)
    if problems:
        print(f"\n这些页面的卡片**同行不等高**：{', '.join(problems)}")
        return 1
    print("\n全部通过 —— 每个网格的每一行卡片都等高")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
