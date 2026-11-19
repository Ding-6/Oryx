"""实验：多行截断到底哪种写法在这版 Chrome 里生效。

判定方式用 `Range.getClientRects()` 的行数 ——
clamp 生效时只渲染出 N 行，不生效时文字照旧排下去。
（看 `scrollHeight` 不准 —— clamp 生效时 scrollHeight 仍是全文高度。）
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE))
sys.path.insert(0, str(CORE / "tests"))

LONG = "这是一段很长的描述文字用来测试多行截断是否生效" * 4

CANDIDATES = [
    ("现状 webkit-box", "display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;"),
    ("标准 line-clamp", "display:-webkit-box;-webkit-box-orient:vertical;line-clamp:2;"),
    ("两套都设", "display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;line-clamp:2;"),
    ("flex+overflow", "display:flex;flex-direction:column;overflow:hidden;max-height:42px;"),
]


def main() -> int:
    from playwright.sync_api import sync_playwright

    from _webdrive import start_server, stop_server, wait_ready

    home = Path("D:/OryxData/tmp/shot-home")
    home.mkdir(parents=True, exist_ok=True)
    srv = start_server(8785, home, "shot-token")
    try:
        wait_ready(8785, timeout=40)
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
            pg.goto("http://127.0.0.1:8785/", wait_until="domcontentloaded")
            pg.wait_for_timeout(3000)
            pg.evaluate(
                "() => document.querySelector('.navitem[data-view=\"market\"]').click()"
            )
            pg.wait_for_timeout(1800)

            print("Chrome:", pg.evaluate("() => navigator.userAgent"))

            for label, css in CANDIDATES:
                r = pg.evaluate(
                    """([css, text]) => {
                        const d = document.querySelector('#page .uc .udesc');
                        if (!d) return null;
                        d.style.cssText = css;
                        d.textContent = text;
                        // 取 Range 的行数 —— clamp 生效时只渲染 N 行
                        const range = document.createRange();
                        range.selectNodeContents(d);
                        const rects = [...range.getClientRects()];
                        const lines = new Set(rects.map(x => Math.round(x.top))).size;
                        const cs = getComputedStyle(d);
                        return {
                            lines: lines,
                            h: Math.round(d.getBoundingClientRect().height),
                            scrollH: d.scrollHeight,
                            display: cs.display,
                            clamp: cs.webkitLineClamp,
                            stdClamp: cs.lineClamp,
                        };
                    }""",
                    [css, LONG],
                )
                print(f"{label:16} → {json.dumps(r, ensure_ascii=False)}")
            b.close()
    finally:
        stop_server(srv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
