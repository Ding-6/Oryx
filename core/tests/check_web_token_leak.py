"""浏览器验证：前端扩展**拿不到令牌**，而页面本身照常能用。

这个漏洞是怎么坐实的
------------------
审计前端扩展时，发现「扩展拿不到令牌」这条承诺**不成立**：

    扩展 import { ORYX_TOKEN } from '/web/modules/core.js'
    → 拿到完整令牌（真实浏览器里验证过）

顺着查下去漏洞更大 —— 令牌明文在 core.js 里，而 `/web/**` 不需要令牌
就能读，CORS 又放开任意 localhost:PORT 来源 ⇒ **同机任意网页**
都能读到令牌，再带令牌调任意 API（含 `/api/chat`）。

修法：令牌改走 HttpOnly cookie（JS 读不到），服务端再查 Origin。

这个脚本验证三件事
-----------------
1. 扩展**拿不到令牌**（动态 import 试到底，拿到的是明确的失败）
2. 扩展**拿不到 cookie 的值**（HttpOnly 对 JS 不可见）
3. 页面本身**照常能用**（同源 fetch 带 cookie 能调通 API）

只用真实浏览器 —— 这个漏洞的本质是「浏览器把令牌交给了同页脚本」，
用 httpx 模拟是模拟不出来的。
"""
from __future__ import annotations

import asyncio

from _webcheck import describe_busy, ports_free
# 统一收工：杀 Chrome 的**进程树** + 清它的 profile。
# 只 terminate 父进程的话，子进程会活下来锁着 profile，
# 下一次跑 Chrome 就起不来（实测等 160 秒后报「Chrome 没起来」）。
from _webdrive import kill_tree, stop_chrome
import json
import os
import socket
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

CORE = Path(__file__).resolve().parent.parent
PORT = 8807
TOKEN = "browser-token-check"

# 一个「老实」的扩展：它只是想把令牌读出来。
LEAKY_JS = """
export default {
  setup(ctx) {
    const el = document.createElement('div');
    el.setAttribute('data-uiext', 'probe');
    el.setAttribute('data-token-got', '(还没试)');
    el.setAttribute('data-cookie-got', '(还没试)');
    el.setAttribute('data-api-status', '(还没试)');
    ctx.mount.append(el);

    // 1) 动态 import core.js，看看里面有没有令牌导出。
    //    （用动态 import 而不是静态 —— 静态 import 缺导出会在**模块加载
    //    阶段**就抛错，整个扩展挂不上，后面两项就没法验了。）
    (async () => {
      let tok;
      try {
        const m = await import('/web/modules/core.js');
        const keys = Object.keys(m).filter(k => /TOKEN/i.test(k));
        tok = keys.length
          ? ('拿到令牌导出：' + keys.join(','))
          : '模块里没有令牌导出';
      } catch (e) {
        tok = '动态 import 失败：' + (e && e.message ? e.message : String(e));
      }
      el.setAttribute('data-token-got', tok);

      // 2) HttpOnly cookie 对 JS 不可见 —— document.cookie 里没有它
      el.setAttribute('data-cookie-got', document.cookie.includes('oryx_token')
        ? '看得到 cookie（不该发生）' : '看不到 cookie 值（HttpOnly 生效）');

      // 3) 页面本身照常能调后端 —— 同源请求浏览器自动带 cookie
      try {
        const r = await fetch('/api/ping');
        el.setAttribute('data-api-status', String(r.status));
      } catch (e) {
        el.setAttribute('data-api-status', '失败：' + e);
      }
    })();
  }
};
"""


def _port_free(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        try:
            s.connect(("127.0.0.1", port))
            return False
        except OSError:
            return True


def check(label: str, ok: bool, detail: str = "") -> bool:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))
    return ok


async def _browser_probe(port: int) -> dict:
    import websockets

    from check_web_modules import find_chrome

    chrome = find_chrome()
    # 固定名 + 跑完清掉 —— Chrome 的缓存比文件本身大得多，
    # 留着跑几百轮能占好几个 GB。
    profile = Path(tempfile.gettempdir()) / "oryx_leak_profile"
    shutil.rmtree(profile, ignore_errors=True)
    proc = subprocess.Popen(
        [
            chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
            f"--remote-debugging-port={port + 100}",
            f"--user-data-dir={profile}",
            "--remote-allow-origins=*", "about:blank",
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(60):
            time.sleep(0.5)
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port + 100}/json/version", timeout=3
                ) as r:
                    if r.read():
                        break
            except Exception:
                continue
        else:
            # Chrome 没起来 —— **立刻返回**，别一路走到下一个超时
            # （否则白等几十秒，最后只报一句「没有结论」）
            return {"__error__": "Chrome 没起来（可能端口被占或系统负载高）"}

        req = urllib.request.Request(
            f"http://127.0.0.1:{port + 100}/json/new?about:blank", method="PUT"
        )
        target = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())

        async with websockets.connect(
            target["webSocketDebuggerUrl"], max_size=16 * 1024 * 1024,
            open_timeout=15,
        ) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Page.enable"}))
            await ws.send(json.dumps({"id": 2, "method": "Runtime.enable"}))
            await ws.send(json.dumps({
                "id": 3, "method": "Page.navigate",
                "params": {"url": f"http://127.0.0.1:{port}/"},
            }))
            # 等页面**真的加载完** —— 固定 sleep 是猜，负载高时猜不够。
            # （单独跑通过、批量跑失败，而且每次挂的脚本还不一样）
            from _webdrive import wait_page_ready
            await wait_page_ready(ws, timeout=25.0)
            await asyncio.sleep(7)   # 保留原来的余量

            expr = """(()=>{
                // 扩展的 DOM 在自己的 shadow root 里 ——
                // document.querySelector 不穿透 shadow DOM，得逐个 host 进去找。
                let el = null;
                for (const host of document.querySelectorAll('.uiext-host')) {
                    const root = host.shadowRoot;
                    el = (root && root.querySelector('[data-uiext="probe"]')) || el;
                }
                if (!el) return {mounted: false,
                    hostCount: document.querySelectorAll('.uiext-host').length};
                return {
                    mounted: true,
                    token: el.getAttribute('data-token-got') || '',
                    cookie: el.getAttribute('data-cookie-got') || '',
                    api: el.getAttribute('data-api-status') || '',
                };
            })()"""
            await ws.send(json.dumps({
                "id": 300, "method": "Runtime.evaluate",
                "params": {"expression": expr, "returnByValue": True},
            }))
            logs: list[str] = []
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=25)
                msg = json.loads(raw)
                # 收集 console 输出 —— 扩展加载失败的原因只打在 console 里
                if msg.get("method") in ("Runtime.consoleAPICalled",
                                         "Runtime.exceptionThrown"):
                    try:
                        args = msg["params"].get("args") or []
                        text = " ".join(
                            str(a.get("value", a.get("description", "")))
                            for a in args
                        )
                        if not text:
                            text = str(msg["params"].get("exceptionDetails", ""))
                        logs.append(text)
                    except Exception:
                        pass
                if msg.get("id") == 300:
                    data = (msg.get("result", {}).get("result", {})
                            .get("value") or {})
                    data["logs"] = logs
                    return data
    finally:
        # 统一收工 —— 杀**进程树**（只 terminate 的话 Chrome 的子进程
        # 会活下来锁着 profile，下一次跑就起不来）+ 清掉 profile。
        stop_chrome(proc, profile, port + 100)


def main() -> int:
    if not _port_free(PORT):
        print(f"端口 {PORT} 已被占用 —— 跳过（可能有上次的测试进程没退出）")
        return 0

    home = Path(tempfile.mkdtemp())
    ext = home / "ui-extensions" / "probe"
    ext.mkdir(parents=True)
    (ext / "oryx.json").write_text(json.dumps({
        "id": "probe", "name": "令牌探针", "mount": "rail", "enabled": True,
    }, ensure_ascii=False), encoding="utf-8")
    (ext / "index.js").write_text(LEAKY_JS, encoding="utf-8")

    env = dict(os.environ)
    env["ORYX_API_TOKEN"] = TOKEN
    env["ORYX_HOME"] = str(home)
    # 端口被占 → 这次测的其实是**别人的服务**（或别人的 Chrome），
    # 结论全是假的。先查一下：宁可跳过，也不能给出一个基于错误环境的「通过」。
    if not ports_free(PORT):
        print(f"端口被占用（{describe_busy(PORT)}）—— 跳过"
              "（可能有上次的测试进程没退出）")
        return 0
    server = subprocess.Popen(
        [sys.executable, "-m", "oryx", "--no-browser", "--port", str(PORT)],
        cwd=str(CORE), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    try:
        for _ in range(80):
            time.sleep(0.5)
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{PORT}/api/ping", timeout=3
                ) as r:
                    if r.read():
                        break
            except Exception:
                continue
        else:
            print("服务没起来")
            return 1

        data = asyncio.run(_browser_probe(PORT))
        print("浏览器实测：")
        if data.get("__error__"):
            print(f"  [FAIL] 浏览器探针失败：{data['__error__']}")
            return 1
        if not data.get("mounted"):
            print(f"  [FAIL] 扩展没挂上（host 数={data.get('hostCount')}）")
            for line in data.get("logs", [])[:12]:
                print(f"         console: {line[:160]}")
            return 1

        token = str(data.get("token", ""))
        cookie = str(data.get("cookie", ""))
        api = str(data.get("api", ""))

        ok = True
        ok &= check(
            "扩展拿不到令牌",
            TOKEN not in token and ("没有令牌导出" in token or "失败" in token),
            token[:90],
        )
        ok &= check(
            "扩展看不到 cookie 的值（HttpOnly）",
            "看不到" in cookie,
            cookie[:60],
        )
        ok &= check("页面本身能调后端（同源 + cookie）", api == "200", f"HTTP {api}")
        print()
        print("=" * 62)
        if ok:
            print("全部通过")
            return 0
        print("有断言未通过")
        return 1
    finally:
        # 杀**进程树** —— agentd 是子进程，只 terminate 父进程的话
        # 它会变成孤儿继续占着端口，下一个脚本就被跳过。
        kill_tree(server)


if __name__ == "__main__":
    sys.exit(main())
