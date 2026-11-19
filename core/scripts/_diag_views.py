"""诊断：各视图页的排版现状（一次性排查用）。

量三件事：
1. 多行截断（`-webkit-line-clamp`）到底生效没有
2. 每个页面顶部第一块的 y —— 用来对齐各页的内边距
3. `#page` 自身的 padding
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE))
sys.path.insert(0, str(CORE / "tests"))

VIEWS = ("work", "market", "automations", "workflows", "butlers", "persona")


def main() -> int:
    from playwright.sync_api import sync_playwright

    from _webdrive import start_server, stop_server, wait_ready

    home = Path("D:/OryxData/tmp/shot-home")
    home.mkdir(parents=True, exist_ok=True)
    srv = start_server(8786, home, "shot-token")
    try:
        wait_ready(8786, timeout=40)
        time.sleep(1.5)
        with sync_playwright() as p:
            b = None
            for ch in ("chrome", "msedge", None):
                try:
                    b = p.chromium.launch(
                        headless=True, channel=ch,
                        args=["--no-sandbox", "--disable-gpu"],
                    )
                    break
                except Exception:
                    continue
            ctx = b.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
            pg = ctx.new_page()
            pg.goto("http://127.0.0.1:8786/", wait_until="domcontentloaded")
            pg.wait_for_timeout(3000)

            # ── 1. 多行截断 ────────────────────────────────────────────
            pg.evaluate(
                "() => document.querySelector('.navitem[data-view=\"market\"]').click()"
            )
            pg.wait_for_timeout(1800)
            pg.evaluate(
                """() => {
                    const d = document.querySelector('#page .uc .udesc');
                    if (d) d.textContent = '这是一段很长的描述文字用来测试多行截断是否生效'.repeat(4);
                }"""
            )
            pg.wait_for_timeout(400)
            clamp = pg.evaluate(
                """() => {
                    const d = document.querySelector('#page .uc .udesc');
                    if (!d) return null;
                    const cs = getComputedStyle(d);
                    return {
                        h: Math.round(d.getBoundingClientRect().height),
                        scrollH: d.scrollHeight,
                        display: cs.display,
                        orient: cs.webkitBoxOrient,
                        webkitClamp: cs.webkitLineClamp,
                        stdClamp: cs.lineClamp,
                    };
                }"""
            )
            print("多行截断:", json.dumps(clamp, ensure_ascii=False))

            # ── 2. 各页顶部 ────────────────────────────────────────────
            tops = {}
            for v in VIEWS:
                pg.evaluate(
                    "(v) => document.querySelector('.navitem[data-view=\"' + v + '\"]').click()",
                    v,
                )
                pg.wait_for_timeout(1100)
                tops[v] = pg.evaluate(
                    """() => {
                        const p = document.querySelector('#page');
                        if (!p) return null;
                        const cs = getComputedStyle(p);
                        const kids = [...p.children].filter(
                            k => k.getBoundingClientRect().height > 0
                        );
                        const first = kids[0];
                        return {
                            padTop: cs.paddingTop,
                            padX: cs.paddingLeft,
                            firstTop: first ? Math.round(first.getBoundingClientRect().top) : null,
                            firstCls: first ? (first.className || first.tagName) : null,
                        };
                    }"""
                )
            print("各页顶部:", json.dumps(tops, ensure_ascii=False, indent=1))
            b.close()
    finally:
        stop_server(srv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
