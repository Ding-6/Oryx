"""检查「两行截断 + 省略号」是不是真的生效。

判定：
1. 长描述要被打上 `is-clipped`（有省略号）
2. 短描述**不能**被打上（平白多个「…」比硬切更烦）
3. 容器高度不变（不撑破固定高度的卡片）

跑法：python scripts/check_clamp.py
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
SHORT = "短描述"


def main() -> int:
    from playwright.sync_api import sync_playwright

    from _webdrive import start_server, stop_server, wait_ready

    home = Path("D:/OryxData/tmp/shot-home")
    home.mkdir(parents=True, exist_ok=True)
    srv = start_server(8784, home, "shot-token")
    try:
        wait_ready(8784, timeout=40)
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
            pg.goto("http://127.0.0.1:8784/", wait_until="domcontentloaded")
            pg.wait_for_timeout(3000)
            pg.evaluate(
                "() => document.querySelector('.navitem[data-view=\"market\"]').click()"
            )
            pg.wait_for_timeout(2000)

            def probe(text):
                return pg.evaluate(
                    """(text) => {
                        const d = document.querySelector('#page .uc .udesc');
                        if (!d) return null;
                        d.textContent = text;
                        d.classList.remove('is-clipped');
                        // 手动跑一次判定（MutationObserver 是异步的）
                        const clipped = d.scrollHeight > d.clientHeight + 1;
                        d.classList.toggle('is-clipped', clipped);
                        const cs = getComputedStyle(d, '::after');
                        return {
                            clipped: clipped,
                            hasClass: d.classList.contains('is-clipped'),
                            afterContent: cs.content,
                            h: Math.round(d.getBoundingClientRect().height),
                            cardH: Math.round(d.closest('.uc').getBoundingClientRect().height),
                        };
                    }""",
                    text,
                )

            long_r = probe(LONG)
            short_r = probe(SHORT)
            print("长描述:", json.dumps(long_r, ensure_ascii=False))
            print("短描述:", json.dumps(short_r, ensure_ascii=False))

            ok = True
            if not (long_r and long_r["clipped"] and long_r["hasClass"]):
                print("✗ 长描述没有标记为截断")
                ok = False
            if short_r and short_r["clipped"]:
                print("✗ 短描述被误判为截断")
                ok = False
            if long_r and long_r["cardH"] != 187:
                print(f"✗ 卡片高度变了：{long_r['cardH']}（应为 187）")
                ok = False
            if ok:
                print("✓ 截断与省略号都正常")
            b.close()
    finally:
        stop_server(srv)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
