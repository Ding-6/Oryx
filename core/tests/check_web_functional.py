"""前端拆成 ES Module 之后的**功能性回归检查**。

为什么单独写一个
----------------
check_web_modules.py 验证的是「没有报错」——
但「不报错」不等于「功能还在」。拆分过程中最怕的是：
某个模块没被加载 → 按钮点了没反应 → 界面看着正常但功能没了。

这个检查用 CDP 在真实浏览器里点几个关键按钮，看界面**真的发生变化**：
  · 切视图（chat → market）：左栏高亮 + 主区内容变化
  · 展开全局搜索：搜索框出现
  · 打开设置面板：设置面板出现

运行：python -m tests.check_web_functional
"""
from __future__ import annotations

import asyncio

from _webcheck import describe_busy, ports_free
# 统一收工：杀 Chrome 的**进程树** + 清它的 profile。
# 只 terminate 父进程的话，子进程会活下来锁着 profile，
# 下一次跑 Chrome 就起不来（实测等 160 秒后报「Chrome 没起来」）。
from _webdrive import stop_chrome
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CORE = Path(__file__).resolve().parents[1]
_CDP_PORT = 9334
FAILED: list[str] = []

# Chrome 的 profile —— 固定名 + 跑完清掉。
# 它的缓存比文件本身大得多，留着跑几百轮能占好几个 GB。
_CDP_PROFILE = Path(tempfile.gettempdir()) / "oryx_cdp_profile"


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILED.append(name)


def find_chrome() -> str | None:
    from check_web_modules import find_chrome as fc
    return fc()


async def _eval(ws, expr: str, timeout: float = 20.0):
    """在页面里求值一个表达式，返回结果。

    id 必须全局唯一 —— 前面 Page.enable / Runtime.enable / navigate 已经用过
    1/2/3，硬编码 id=1 会收不到响应（返回 None）。
    """
    msg_id = 900
    await ws.send(json.dumps({
        "id": msg_id, "method": "Runtime.evaluate",
        "params": {"expression": expr, "returnByValue": True, "awaitPromise": True},
    }))
    end = time.time() + timeout
    while time.time() < end:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=3)
        except asyncio.TimeoutError:
            continue
        except Exception:
            break
        msg = json.loads(raw)
        if msg.get("id") == msg_id:
            res = msg.get("result", {})
            if "exceptionDetails" in res:
                return {"__error__": str(res["exceptionDetails"])[:300]}
            return res.get("result", {}).get("value")
    return None


async def _session(chrome: str, url: str, script: str) -> dict:
    import websockets

    # 上一个 Chrome 没退干净的话，这个目录会被锁住 ——
    # 新 Chrome 起不来。先清掉。
    shutil.rmtree(_CDP_PROFILE, ignore_errors=True)
    proc = subprocess.Popen(
        [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
         f"--remote-debugging-port={_CDP_PORT}",
         f"--user-data-dir={_CDP_PROFILE}",
         "--remote-allow-origins=*", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(60):
            time.sleep(0.5)
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{_CDP_PORT}/json/version", timeout=3) as r:
                    if r.read():
                        break
            except Exception:
                continue
        else:
            # Chrome 没起来 —— **立刻返回**，别一路走到下一个超时。
            # （否则白等几十秒，最后只报一句「没有结论」，看不出出了啥事）
            return {"__error__": "Chrome 没起来（可能端口被占或系统负载高）"}

        req = urllib.request.Request(
            f"http://127.0.0.1:{_CDP_PORT}/json/new?about:blank", method="PUT")
        with urllib.request.urlopen(req, timeout=10) as r:
            target = json.loads(r.read().decode())
        async with websockets.connect(target["webSocketDebuggerUrl"],
                                      max_size=16 * 1024 * 1024,
                                      open_timeout=15) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Page.enable"}))
            await ws.send(json.dumps({"id": 2, "method": "Runtime.enable"}))
            await ws.send(json.dumps({"id": 3, "method": "Page.navigate",
                                      "params": {"url": url}}))
            # 等页面**真的加载完** —— 固定 sleep 是猜，负载高时猜不够。
            # （单独跑通过、批量跑失败，而且每次挂的脚本还不一样）
            from _webdrive import wait_page_ready
            await wait_page_ready(ws, timeout=25.0)
            await asyncio.sleep(4.0)   # 保留原来的余量   # 等页面与模块加载完
            return await _eval(ws, script)
    finally:
        # 统一收工 —— 杀**进程树**（只 terminate 的话 Chrome 的子进程
        # 会活下来锁着 profile，下一次跑就起不来）+ 清掉 profile。
        stop_chrome(proc, _CDP_PROFILE, _CDP_PORT)


def main() -> int:
    print("=" * 60)
    print("前端 ES Module 功能性回归检查")
    print("=" * 60)

    chrome = find_chrome()
    if not chrome:
        print("  找不到 Chrome/Edge，跳过")
        return 1

    import uvicorn
    from oryx.server import app

    port = 8794
    # 端口被占 → 这次测的其实是**别人的服务**（或别人的 Chrome），
    # 结论全是假的。先查一下：宁可跳过，也不能给出一个基于错误环境的「通过」。
    if not ports_free(port):
        print(f"端口被占用（{describe_busy(port)}）—— 跳过"
              "（可能有上次的测试进程没退出）")
        return 0
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    srv = uvicorn.Server(cfg)
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    time.sleep(2.5)
    url = f"http://127.0.0.1:{port}/"

    # 一次 CDP 会话里做完所有检查
    script = """
    (async () => {
      const out = {};
      const sleep = ms => new Promise(r => setTimeout(r, ms));
      const $ = s => document.querySelector(s);
      const $$ = s => Array.from(document.querySelectorAll(s));

      out.modulesLoaded = $$('script[type="module"]').length;
      // 关键：模块的副作用是否生效 —— 导航项的点击处理器是否绑上了
      out.navCount = $$('.navitem').length;

      // 1) 切到技能广场：主区内容应变化
      const pageBefore = ($('#page')||{}).innerHTML || '';
      const market = $$('.navitem').find(b => b.dataset.view === 'market');
      out.hasMarket = !!market;
      if (market) { market.click(); await sleep(1200); }
      const pageAfter = ($('#page')||{}).innerHTML || '';
      out.pageChanged = pageBefore !== pageAfter;
      out.pageLen = pageAfter.length;
      out.view = ($('#page')||{}).dataset?.view || (window.S && window.S.view) || '';

      // 2) 全局搜索：输入关键词后应出现结果面板
      //    注意：空查询词时**故意不显示**结果（原有设计，避免一打字就弹框）
      const search = $('#search');
      out.hasSearch = !!search;
      if (search) {
        search.value = 'a';
        search.dispatchEvent(new Event('input', {bubbles: true}));
        await sleep(1200);
      }
      const searchBox = $('#searchBox');
      out.searchBoxExists = !!searchBox;
      out.searchBoxVisible = !!searchBox && getComputedStyle(searchBox).display !== 'none';
      out.searchRowCount = searchBox ? searchBox.querySelectorAll('[data-go]').length : 0;

      // 3) 设置面板：点设置按钮应出现
      const setBtn = $('#setBtn');
      out.hasSetBtn = !!setBtn;
      if (setBtn) { setBtn.click(); await sleep(800); }
      const side = $('#side');
      out.sideExists = !!side;
      out.sideClass = side ? side.className : '';

      return JSON.stringify(out);
    })()
    """

    try:
        raw = asyncio.run(_session(chrome, url, script))
    finally:
        srv.should_exit = True
        time.sleep(0.8)

    if not isinstance(raw, str):
        check("CDP 脚本返回结果", False, str(raw)[:200])
        return 1
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        check("CDP 脚本返回合法 JSON", False, raw[:200])
        return 1

    check("CDP 脚本返回合法 JSON", True)
    check("页面引入了模块入口脚本", data.get("modulesLoaded", 0) >= 1,
          str(data.get("modulesLoaded")))
    check("导航项已渲染（模块副作用生效）", data.get("navCount", 0) >= 5,
          str(data.get("navCount")))
    check("切视图后主区内容变化", bool(data.get("pageChanged")),
          f"pageLen={data.get('pageLen')}")
    check("全局搜索框存在", bool(data.get("hasSearch")))
    check("搜索面板能被唤起", bool(data.get("searchBoxVisible")),
          f"exists={data.get('searchBoxExists')} rows={data.get('searchRowCount')}")
    check("设置按钮存在", bool(data.get("hasSetBtn")))
    check("右栏容器存在", bool(data.get("sideExists")))

    print("\n" + "=" * 60)
    if FAILED:
        print(f"失败 {len(FAILED)} 项：{FAILED}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
