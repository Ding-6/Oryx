"""截一张当前界面的图，用来和目标设计对比。

用法：
    python tests/_shot.py [输出路径] [宽度] [高度]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE))
sys.path.insert(0, str(CORE / "tests"))

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("D:/tools/shot.png")
W = int(sys.argv[2]) if len(sys.argv) > 2 else 1080
H = int(sys.argv[3]) if len(sys.argv) > 3 else 567


def main() -> int:
    from playwright.sync_api import sync_playwright

    from _webdrive import start_server, stop_server, wait_ready
    from check_web_modules import find_chrome

    chrome = find_chrome()
    home = Path("D:/OryxData/tmp/shot-home")
    home.mkdir(parents=True, exist_ok=True)

    srv = start_server(8791, home, "shot-token")
    try:
        wait_ready(8791, timeout=40)
        time.sleep(1.5)
        with sync_playwright() as p:
            browser = None
            for channel in ("chrome", "msedge", None):
                try:
                    browser = p.chromium.launch(
                        headless=True, channel=channel,
                        args=["--no-sandbox", "--disable-gpu"],
                    )
                    break
                except Exception:
                    continue
            if browser is None:
                print("浏览器起不来")
                return 1
            ctx = browser.new_context(
                viewport={"width": W, "height": H},
                locale="zh-CN",
                device_scale_factor=2,
            )
            page = ctx.new_page()
            page.goto("http://127.0.0.1:8791/", wait_until="domcontentloaded")
            page.wait_for_timeout(3500)
            OUT.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(OUT))
            print(f"已截图 → {OUT}  ({W}x{H})")
            browser.close()
    finally:
        stop_server(srv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
