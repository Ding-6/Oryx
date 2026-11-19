"""控制台巡检 —— 逐页走一遍，把 JS 报错全抓出来。

为什么要有这个
--------------
pytest 测的是**后端**；浏览器验证脚本测的是**某几个具体功能**。
但「某个页面一打开就报 `xxx is not defined`」这类错误，
两个都盖不住 —— 它只在用户点那个页面时才发生。

这个脚本把每个视图页都打开一遍，收集 `pageerror`（未捕获异常）
和 `console.error`，最后统一报出来。

跑法：
    python tests/check_web_console.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE))
sys.path.insert(0, str(CORE / "tests"))

VIEWS = [
    ("新建对话", "() => document.querySelector('.navitem.newchat').click()"),
    ("工作台", "() => document.querySelector('.navitem[data-view=\"work\"]').click()"),
    ("技能广场", "() => document.querySelector('.navitem[data-view=\"market\"]').click()"),
    ("自动任务", "() => document.querySelector('.navitem[data-view=\"automations\"]').click()"),
    ("工作流", "() => document.querySelector('.navitem[data-view=\"workflows\"]').click()"),
    ("管家团队", "() => document.querySelector('.navitem[data-view=\"butlers\"]').click()"),
    ("人设管理", "() => document.querySelector('.navitem[data-view=\"persona\"]').click()"),
    ("设置", "() => document.querySelector('#setBtn').click()"),
    ("用量面板", "() => document.querySelector('#dashBtn').click()"),
    # 本地资料是浮层，走子项
    ("本地资料", "() => document.querySelector('.navitem.localhead').click()"),
]


def main() -> int:
    from playwright.sync_api import sync_playwright

    from _webdrive import start_server, stop_server, wait_ready
    from check_web_modules import find_chrome

    find_chrome()
    home = Path("D:/OryxData/tmp/shot-home")
    home.mkdir(parents=True, exist_ok=True)
    srv = start_server(8782, home, "console-token")
    try:
        wait_ready(8782, timeout=40)
        time.sleep(2)
        errors: list[tuple[str, str, str]] = []
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
            ctx = browser.new_context(
                viewport={"width": 1440, "height": 900}, locale="zh-CN"
            )
            page = ctx.new_page()

            current = ["启动"]

            def on_error(err):
                errors.append((current[0], "未捕获异常", str(err)))

            def on_console(msg):
                if msg.type == "error":
                    errors.append((current[0], "console.error", msg.text))

            page.on("pageerror", on_error)
            page.on("console", on_console)

            page.goto("http://127.0.0.1:8782/", wait_until="domcontentloaded")
            page.wait_for_timeout(3500)

            for label, js in VIEWS:
                current[0] = label
                try:
                    page.evaluate(js)
                except Exception as exc:  # 页面不存在也不该算 JS 错误
                    errors.append((label, "入口", str(exc)))
                page.wait_for_timeout(1500)

            # 再走一遍暗色 —— 主题切换常常藏着自己的 bug
            page.evaluate("() => document.documentElement.setAttribute('data-theme','dark')")
            page.wait_for_timeout(800)
            for label, js in VIEWS:
                current[0] = label + "（暗色）"
                try:
                    page.evaluate(js)
                except Exception:
                    pass
                page.wait_for_timeout(900)

            browser.close()

        if errors:
            print(f"✗ 抓到 {len(errors)} 条错误：\n")
            seen = set()
            for page_name, kind, text in errors:
                key = (kind, text[:160])
                if key in seen:
                    continue
                seen.add(key)
                where = ""
                for line in text.splitlines():
                    if "http" in line or "at " in line:
                        where = line.strip()
                        break
                print(f"  [{page_name}] {kind}: {text.splitlines()[0][:150]}")
                if where:
                    print(f"      {where[:150]}")
            return 1
        # 套件认两种结论：输出里有「全部通过」，或者有 [PASS] 行且无 FAIL。
        print("全部通过 —— 各视图页无 JS 报错")
        return 0
    finally:
        stop_server(srv)


if __name__ == "__main__":
    raise SystemExit(main())
