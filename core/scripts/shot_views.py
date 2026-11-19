"""把七个视图页各截一张图 —— 用来逐页看视觉现状。

用法：
    python scripts/shot_views.py [输出目录] [宽度] [高度]

**为什么需要它**
--------------
一次只能看一屏。要评估「七个页面各自长什么样」，就得挨个切过去截 ——
人工在浏览器里点太慢，而且容易漏。

切换方式是**点左栏的 navitem**（走的是真实交互路径，
而不是直接调内部函数）—— 这样连事件绑定有问题都能暴露出来。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE))
sys.path.insert(0, str(CORE / "tests"))

OUT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("D:/tools/views")
W = int(sys.argv[2]) if len(sys.argv) > 2 else 1440
H = int(sys.argv[3]) if len(sys.argv) > 3 else 900
# 第四个参数传 "dark" 就切暗色主题再截
DARK = len(sys.argv) > 4 and sys.argv[4].lower() == "dark"

# (文件名, 入口方式) —— 入口是 ('view', 名) 或 ('btn', 元素 id)
# 设置与用量面板在左栏底部的按钮里，不在导航里 —— 所以单独走按钮。
VIEWS = [
    ("01-chat", ("view", "chat")),
    ("02-work", ("view", "work")),
    ("03-market", ("view", "market")),
    ("04-automations", ("view", "automations")),
    ("05-workflows", ("view", "workflows")),
    ("06-butlers", ("view", "butlers")),
    ("07-persona", ("view", "persona")),
    ("08-settings", ("btn", "#setBtn")),
    ("09-dashboard", ("btn", "#dashBtn")),
    # 右栏：空态 vs 有内容 —— 有内容才是真正要检查的排版
    ("10-side-filled", ("side", None)),
    # 技能广场的「工具」页签 —— 83 个工具，密集列表最容易乱
    ("11-market-tools", ("tab", "market:tools")),
    # 本地资料是浮层，不是页面
    ("12-local", ("js", """() => document.querySelector('.navitem.localhead').click()""")),
]


def main() -> int:
    from playwright.sync_api import sync_playwright

    from _webdrive import start_server, stop_server, wait_ready
    from check_web_modules import find_chrome

    find_chrome()
    home = Path("D:/OryxData/tmp/shot-home")
    home.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    srv = start_server(8790, home, "shot-token")
    try:
        wait_ready(8790, timeout=40)
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
            page.goto("http://127.0.0.1:8790/", wait_until="domcontentloaded")
            page.wait_for_timeout(3500)

            if DARK:
                # 走真实的切主题路径（改属性 + 持久化），
                # 而不是只改个 class —— 否则量到的是「半切换」的中间态。
                page.evaluate(
                    """() => {
                        document.documentElement.setAttribute('data-theme', 'dark');
                        try { localStorage.setItem('oryx-theme', 'dark'); } catch (_) {}
                    }"""
                )
                page.wait_for_timeout(900)

            for name, entry in VIEWS:
                kind, arg = entry
                if kind == "view":
                    ok = page.evaluate(
                        """(v) => {
                            const el = document.querySelector(
                                '.navitem[data-view="' + v + '"]'
                            );
                            if (!el) return false;
                            el.click();
                            return true;
                        }""",
                        arg,
                    )
                    if not ok:
                        print(f"{name}: 找不到入口 data-view={arg}")
                        continue
                elif kind == "btn":
                    ok = page.evaluate(
                        """(sel) => {
                            const el = document.querySelector(sel);
                            if (!el) return false;
                            el.click();
                            return true;
                        }""",
                        arg,
                    )
                    if not ok:
                        print(f"{name}: 找不到按钮 {arg}")
                        continue
                elif kind == "tab":
                    # 切到某个视图页后再点它里面的页签
                    page.evaluate(
                        """(v) => {
                            const el = document.querySelector(
                                '.navitem[data-view="' + v.split(':')[0] + '"]'
                            );
                            if (el) el.click();
                        }""",
                        arg,
                    )
                    page.wait_for_timeout(1200)
                    page.evaluate(
                        """(v) => {
                            const el = document.querySelector(
                                '[data-mtab="' + v.split(':')[1] + '"]'
                            );
                            if (el) el.click();
                        }""",
                        arg,
                    )
                elif kind == "js":
                    page.evaluate(arg)
                else:  # side —— 给右栏灌一点内容，看有内容时的排版
                    page.evaluate("""() => {
                        const el = document.querySelector('.navitem.newchat');
                        if (el) el.click();
                    }""")
                    page.wait_for_timeout(900)
                    page.evaluate(
                        """() => {
                            const p = document.querySelector('#p-steps');
                            if (!p) return;
                            p.innerHTML = `
                              <div class="step"><span class="n">1</span>
                                <div class="sx"><b>读取配置</b>
                                  <span class="sm">读 3 个文件 · 1.2s</span></div>
                                <span class="st done">完成</span></div>
                              <div class="step on"><span class="n">2</span>
                                <div class="sx"><b>调用浏览器</b>
                                  <span class="sm">打开 example.com</span></div>
                                <span class="st run">进行中</span></div>
                              <div class="step"><span class="n">3</span>
                                <div class="sx"><b>写入结果</b>
                                  <span class="sm">等待上一步</span></div>
                                <span class="st wait">等待</span></div>`;
                        }"""
                    )
                page.wait_for_timeout(1600)
                out = OUT_DIR / f"{name}.png"
                page.screenshot(path=str(out))
                print(f"已截 {name} → {out.name}")
            browser.close()
    finally:
        stop_server(srv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
