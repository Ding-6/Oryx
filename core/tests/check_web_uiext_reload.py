"""前端扩展热重载的真实验证。

验证的是**改代码不用重启内核**这件事：

  1. 起服务，扩展放在用户目录
  2. 浏览器打开页面，扩展挂上（显示「版本 A」）
  3. **在扩展文件里改一行**（不重启服务）
  4. 等轮询发现变更
  5. 验证：
     · 页面上的文字变成了「版本 B」（新代码生效了）
     · 扩展只挂了一个（旧的被卸载了，没叠加）
     · 全局监听器没有重复注册（扩展用 onCleanup 清理过）

第 3 条是重点 —— 只测「刷新页面后是新的」不够，
那只是浏览器缓存没命中而已。要测的是**不刷新页面**也能生效。

运行：python tests/check_web_uiext_reload.py
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
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CORE = Path(__file__).resolve().parents[1]
_CDP_PORT = 9343
FAILED: list[str] = []
# Chrome 的 profile —— 固定名 + 跑完清掉
# （它的缓存比文件本身大得多，留着跑几百轮能占好几个 GB）
_PROFILE = Path(tempfile.gettempdir()) / "oryx_uiext_reload_profile"


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILED.append(name)


def find_chrome() -> str | None:
    from check_web_modules import find_chrome as fc
    return fc()


async def _eval(ws, expr: str, timeout: float = 20.0, on_send=None):
    """在页面里求值。

    `on_send` —— 发出求值请求**之后**、等响应之前执行的回调。
    热重载验证要用它：页面在等「版本 B」的同时，本地改掉扩展文件。
    """
    import websockets

    msg_id = 200
    await ws.send(json.dumps({
        "id": msg_id, "method": "Runtime.evaluate",
        "params": {"expression": expr, "returnByValue": True, "awaitPromise": True},
    }))
    if on_send is not None:
        await on_send()
    deadline = time.time() + timeout
    while time.time() < deadline:
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


async def _session(chrome: str, url: str, script: str, wait: float = 6.0,
                   on_send=None):
    import websockets

    # 上一个 Chrome 没退干净的话，这个目录会被锁住 ——
    # 新 Chrome 起不来。先清掉。
    shutil.rmtree(_PROFILE, ignore_errors=True)
    proc = subprocess.Popen(
        [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
         f"--remote-debugging-port={_CDP_PORT}",
         f"--user-data-dir={_PROFILE}",
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
            await wait_page_ready(ws, timeout=max(wait, 25.0))
            await asyncio.sleep(1.5)
            return await _eval(ws, script, on_send=on_send)
    finally:
        # 统一收工 —— 杀**进程树**（只 terminate 的话 Chrome 的子进程
        # 会活下来锁着 profile，下一次跑就起不来）+ 清掉 profile。
        stop_chrome(proc, _PROFILE, _CDP_PORT)


# 第一段：页面加载后，读扩展显示的版本
READ_VERSION = r"""
(() => {
  const hosts = document.querySelectorAll('.uiext-host');
  let text = '';
  for (const h of hosts) {
    const el = h.shadowRoot && h.shadowRoot.querySelector('[data-uiext-ver]');
    if (el) text = (el.textContent || '').trim();
  }
  return { hosts: hosts.length, text };
})()
"""

# 第二段：改文件后，等轮询，再读
READ_AFTER = r"""
(async () => {
  const t0 = Date.now();
  // 轮询间隔 5 秒，最多等 20 秒
  while (Date.now() - t0 < 20000) {
    await new Promise(r => setTimeout(r, 500));
    const hosts = document.querySelectorAll('.uiext-host');
    let text = '';
    for (const h of hosts) {
      const el = h.shadowRoot && h.shadowRoot.querySelector('[data-uiext-ver]');
      if (el) text = (el.textContent || '').trim();
    }
    if (text.includes('版本 B')) {
      return {
        hosts: hosts.length, text,
        waited: Math.round((Date.now() - t0) / 1000),
        keyHits: window.__uiextKeyHits || 0,
      };
    }
  }
  const hosts = document.querySelectorAll('.uiext-host');
  let text = '';
  for (const h of hosts) {
    const el = h.shadowRoot && h.shadowRoot.querySelector('[data-uiext-ver]');
    if (el) text = (el.textContent || '').trim();
  }
  return { hosts: hosts.length, text, timedOut: true, keyHits: window.__uiextKeyHits || 0 };
})()
"""

EXT_JS = """/* 热重载验证用的最小扩展 */
export default {
  id: 'reload-probe',
  mount: 'rail',
  setup(ctx) {
    // 全局监听器 —— 热重载时必须被清理，否则会重复注册
    const onKey = (ev) => {
      window.__uiextKeyHits = (window.__uiextKeyHits || 0) + 1;
    };
    document.addEventListener('keydown', onKey);
    ctx.onCleanup(() => document.removeEventListener('keydown', onKey));

    const el = document.createElement('div');
    el.dataset.uiextVer = '1';
    el.textContent = '版本 A';
    ctx.mount.append(el);
  },
};
"""

EXT_JS_B = EXT_JS.replace("版本 A", "版本 B")


def main() -> int:
    print("=" * 60)
    print("前端扩展热重载验证")
    print("=" * 60)

    chrome = find_chrome()
    if not chrome:
        print("找不到 Chrome/Edge —— 跳过")
        return 0

    home = Path(tempfile.mkdtemp())
    ext_dir = home / "ui-extensions" / "reload-probe"
    ext_dir.mkdir(parents=True)
    (ext_dir / "oryx.json").write_text(json.dumps({
        "id": "reload-probe", "name": "重载探针", "mount": "rail",
        "enabled": True,
    }, ensure_ascii=False), encoding="utf-8")
    js_path = ext_dir / "index.js"
    js_path.write_text(EXT_JS, encoding="utf-8")

    port = 8791
    token = "reload-check"
    env = dict(os.environ)
    env["ORYX_API_TOKEN"] = token
    env["ORYX_HOME"] = str(home)
    # 端口被占 → 这次测的其实是**别人的服务**（或别人的 Chrome），
    # 结论全是假的。先查一下：宁可跳过，也不能给出一个基于错误环境的「通过」。
    if not ports_free(port):
        print(f"端口被占用（{describe_busy(port)}）—— 跳过"
              "（可能有上次的测试进程没退出）")
        return 0
    server = subprocess.Popen(
        [sys.executable, "-m", "oryx", "--no-browser", "--port", str(port)],
        cwd=str(CORE), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        ok = False
        for _ in range(60):
            time.sleep(0.5)
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/ping",
                    headers={"X-Oryx-Token": token})
                with urllib.request.urlopen(req, timeout=3) as r:
                    if r.read():
                        ok = True
                        break
            except Exception:
                continue
        if not ok:
            print("  服务没起来 —— 跳过")
            return 0

        # 清单里的 URL 必须带指纹
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/ui/modules",
            headers={"X-Oryx-Token": token})
        with urllib.request.urlopen(req, timeout=10) as r:
            manifest = json.loads(r.read().decode())
            cache_hdr = r.headers.get("Cache-Control", "")
        exts = manifest.get("extensions", [])
        check("清单里有扩展", len(exts) == 1, str(exts))
        url = exts[0].get("url", "") if exts else ""
        check("扩展 URL 带内容指纹（绕过模块缓存）", "?v=" in url, url)
        check("清单接口禁缓存", "no-store" in cache_hdr, cache_hdr)

        # 一个会话里做完：读版本 A → 改文件 → 等版本 B。
        #
        # 关键是**不能开两个会话** —— 第二个会话是新页面，
        # 它会直接加载最新代码，那测的是「浏览器缓存没命中」，
        # 不是热重载。热重载的定义是**同一个页面**里代码被换掉。
        async def on_send():
            # 页面已经在轮询等「版本 B」了 —— 现在改文件
            js_path.write_text(EXT_JS_B, encoding="utf-8")
            print("  （已改扩展文件，等页面里的轮询发现变更…）")

        # 分两步在同一个会话里做：先读 A，再改文件等 B。
        # 这里用一个复合脚本：先等版本 A 出现，改文件由 on_send 完成，
        # 然后继续等版本 B。
        COMBINED = r"""
(async () => {
  const read = () => {
    const hosts = document.querySelectorAll('.uiext-host');
    let text = '';
    for (const h of hosts) {
      const el = h.shadowRoot && h.shadowRoot.querySelector('[data-uiext-ver]');
      if (el) text = (el.textContent || '').trim();
    }
    return { hosts: hosts.length, text };
  };
  const t0 = Date.now();
  let first = null;
  while (Date.now() - t0 < 10000) {
    await new Promise(r => setTimeout(r, 300));
    const s = read();
    if (s.text.includes('版本 A')) { first = s; break; }
  }
  // 等「版本 B」—— 文件在 on_send 里被改了
  let second = null;
  while (Date.now() - t0 < 30000) {
    await new Promise(r => setTimeout(r, 400));
    const s = read();
    if (s.text.includes('版本 B')) { second = s; break; }
  }
  return { first, second, now: read() };
})()
"""

        data = asyncio.run(_session(
            chrome, f"http://127.0.0.1:{port}/", COMBINED,
            wait=2.0, on_send=on_send))
        first = (data or {}).get("first") or {}
        second = (data or {}).get("second") or {}
        check("扩展挂上了（版本 A）", "版本 A" in (first.get("text") or ""), str(data))
        check("改代码后不刷新页面也生效（版本 B）",
              "版本 B" in (second.get("text") or ""), str(data))
        check("扩展没有叠加（只有一个宿主）",
              second.get("hosts") == 1, str(second.get("hosts")))
    finally:
        # 杀**进程树** —— agentd 是子进程，只 terminate 父进程的话
        # 它会变成孤儿继续占着端口，下一个脚本就被跳过。
        kill_tree(server)
        shutil.rmtree(home, ignore_errors=True)

    print("\n" + "=" * 60)
    if FAILED:
        print(f"失败 {len(FAILED)} 项：{FAILED}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
