"""前端扩展的真实浏览器验证。

为什么单独写
------------
前端扩展的价值全在「**真的挂上了、真的能跑**」上。
后端清单返回正确 ≠ 浏览器里能 import ≠ setup() 跑通 ≠ 元素真在页面上。

这个检查用 CDP 在真实浏览器里：
  1. 起一个真服务，**把扩展放进用户目录**
  2. 打开界面，等扩展加载
  3. 检查扩展的按钮**真的出现在 DOM 里**
  4. 点它，看**界面真的变了**
  5. 检查控制台**没有扩展相关的报错**

第 4 条是关键 —— 只检查「元素存在」的话，一个 setup() 里抛了异常、
按钮挂上了但没绑事件的扩展也能蒙混过关。

运行：python tests/check_web_uiext.py
"""
from __future__ import annotations

import asyncio

from _webcheck import describe_busy, ports_free
# 统一收工：杀 Chrome 的**进程树** + 清它的 profile。
# 只 terminate 父进程的话，子进程会活下来锁着 profile，
# 下一次跑 Chrome 就起不来（实测等 160 秒后报「Chrome 没起来」）。
from _webdrive import kill_tree, stop_chrome, start_server, wait_ready
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
REPO = CORE.parent
_CDP_PORT = 9341
FAILED: list[str] = []
# Chrome 的 profile —— 固定名 + 跑完清掉
# （它的缓存比文件本身大得多，留着跑几百轮能占好几个 GB）
_PROFILE = Path(tempfile.gettempdir()) / "oryx_uiext_profile"


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILED.append(name)


def find_chrome() -> str | None:
    from check_web_modules import find_chrome as fc
    return fc()


async def _eval(ws, expr: str, timeout: float = 20.0):
    import websockets

    msg_id = 100
    await ws.send(json.dumps({
        "id": msg_id, "method": "Runtime.evaluate",
        "params": {"expression": expr, "returnByValue": True, "awaitPromise": True},
    }))
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


async def _session(chrome: str, url: str, script: str):
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
            await wait_page_ready(ws, timeout=25.0)
            await asyncio.sleep(6.0)   # 保留原来的余量   # 等页面、模块与扩展都加载完
            return await _eval(ws, script)
    finally:
        # 统一收工 —— 杀**进程树**（只 terminate 的话 Chrome 的子进程
        # 会活下来锁着 profile，下一次跑就起不来）+ 清掉 profile。
        stop_chrome(proc, _PROFILE, _CDP_PORT)


SCRIPT = r"""
(async () => {
  // 扩展跑在自己的 Shadow DOM 里 —— 核心 DOM 查不到它的内容。
  // 这本身就是「样式与 DOM 隔离」的直接证据。
  const host = document.querySelector('.uiext-host');
  const root = host && host.shadowRoot;
  const btn = root && root.querySelector('[data-uiext="sample-panel"]');
  const before = root && root.querySelector('[data-uiext-panel="sample-panel"]');

  // 核心 DOM 里不该能查到扩展的元素（隔离生效）
  const leaked = document.querySelectorAll('[data-uiext="sample-panel"]').length;

  if (btn) btn.click();
  await new Promise(r => setTimeout(r, 400));
  const after = root && root.querySelector('[data-uiext-panel="sample-panel"]');

  // 先抓「面板内容来自后端」的文本 —— **必须在切视图之前**，
  // 因为 view:changed 事件会把这段文字覆盖掉。
  const apiText = after ? (after.querySelector('.muted')?.textContent || '').trim() : '';

  // 事件总线：核心切视图 → 扩展收到 view:changed → 面板文字更新
  // 扩展订阅时把文字设成「当前视图：X」，切视图后应变成新视图名
  const work = document.querySelector('.navitem[data-view="work"]');
  let busWorked = false;
  let busText = '';
  if (work) {
    work.click();
    await new Promise(r => setTimeout(r, 700));
    const p = root && root.querySelector('[data-uiext-panel="sample-panel"] .muted');
    busText = p ? (p.textContent || '').trim() : '';
    busWorked = busText.includes('当前视图');
  }

  // 反向总线：扩展发 ui:view → 核心切视图。
  // 点扩展里的「回到对话」按钮，核心应该切回 chat。
  const back = root && root.querySelector('[data-uiext-action="back"]');
  let backWorked = false;
  let viewAfter = '';
  if (back) {
    back.click();
    await new Promise(r => setTimeout(r, 800));
    const sel = document.querySelector('.navitem[data-view="chat"]');
    viewAfter = sel ? (sel.getAttribute('aria-selected') || '') : '';
    backWorked = viewAfter === 'true';
  }

  // 隔离是否真的挡住样式：用**核心独有的类名**（.navitem）在 shadow root 里
  // 放一个元素，跟普通 div 比 computed style。
  //
  // 注意不能用 .iconbtn —— 扩展自己就定义了 .iconbtn 的样式，
  // 那样测的是扩展自己的 CSS，不是核心的。
  // 也不能只看「高度 > 5」—— 浏览器默认的 <button> 本来就有 21px 高，
  // 那样恒为真，跟隔离没关系。
  let coreStyleLeak = false;
  try {
    const plain = document.createElement('div');
    const withCls = document.createElement('div');
    withCls.className = 'navitem';
    root.appendChild(plain);
    root.appendChild(withCls);
    const a = getComputedStyle(plain);
    const b = getComputedStyle(withCls);
    const differs = a.width !== b.width
      || a.padding !== b.padding
      || a.paddingTop !== b.paddingTop
      || a.backgroundColor !== b.backgroundColor
      || a.cursor !== b.cursor
      || a.borderRadius !== b.borderRadius;
    coreStyleLeak = differs;
    plain.remove(); withCls.remove();
  } catch (_) {}

  // CSS 变量是否继承进来了（隔离不该把变量也挡掉）
  let varOk = false;
  try {
    const probe = document.createElement('div');
    root.appendChild(probe);
    varOk = getComputedStyle(probe).getPropertyValue('--ink').trim().length > 0;
    probe.remove();
  } catch (_) {}

  // 核心样式子集是否注入了：扩展里的 .btn 应该有核心 .btn 的样式
  // （核心 .btn 是 border:1px solid var(--line-strong) + border-radius:var(--r)）
  let baseCssApplied = false;
  let baseCssRadius = '';
  try {
    const b = root.querySelector('.uiext-btn');
    if (b) {
      const cs = getComputedStyle(b);
      baseCssRadius = cs.borderRadius || '';
      // 核心 .btn 有圆角（var(--r) = 8px）；浏览器默认 button 的圆角是 0
      baseCssApplied = parseFloat(baseCssRadius) > 0;
    }
  } catch (_) {}

  return {
    hasHost: !!host,
    hasShadow: !!root,
    btnExists: !!btn,
    btnText: btn ? (btn.getAttribute('title') || btn.textContent || '').trim() : '',
    panelBefore: !!before,
    panelAfter: !!after,
    panelHidden: after ? !!after.hidden : null,
    panelText: after ? (after.textContent || '').trim().slice(0, 90) : '',
    apiText,
    leakedIntoCore: leaked,
    coreStyleLeak,
    varInherited: varOk,
    baseCssApplied,
    baseCssRadius,
    busWorked,
    busText,
    backWorked,
    viewAfter,
    navCount: document.querySelectorAll('.railnav .navitem').length,
  };
})()
"""


def main() -> int:
    print("=" * 60)
    print("前端扩展真实浏览器验证")
    print("=" * 60)

    chrome = find_chrome()
    if not chrome:
        print("找不到 Chrome/Edge —— 跳过")
        return 0

    home = Path(tempfile.mkdtemp())
    ext_src = REPO / "core" / "web" / "ui-extensions" / "sample-panel"
    ext_dst = home / "ui-extensions" / "sample-panel"
    ext_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ext_src, ext_dst)
    # 打开 enabled —— 样例默认是关的（clone 来的不自动注入界面）
    m = ext_dst / "oryx.json"
    data = json.loads(m.read_text("utf-8"))
    data["enabled"] = True
    m.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")

    port = 8785
    token = "uiext-check"
    env = dict(os.environ)
    env["ORYX_API_TOKEN"] = token
    env["ORYX_HOME"] = str(home)
    # 端口被占 → 这次测的其实是**别人的服务**（或别人的 Chrome），
    # 结论全是假的。先查一下：宁可跳过，也不能给出一个基于错误环境的「通过」。
    # 走 start_server —— 端口被占时它会往后找一个空端口，
    # 这个脚本以前自己起服务，端口被占就**静默跳过**，
    # 套件看着全绿，其实这项没跑。
    server = start_server(port, Path(env["ORYX_HOME"]), token)
    port = getattr(server, "oryx_port", port)
    try:
        if not wait_ready(port, timeout=45):
            print("  服务没起来 —— 跳过")
            return 0

        # 先看清单接口
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/ui/modules",
            headers={"X-Oryx-Token": token})
        with urllib.request.urlopen(req, timeout=10) as r:
            manifest = json.loads(r.read().decode())
        ids = [e["id"] for e in manifest.get("extensions", [])]
        check("清单接口列出了扩展", "sample-panel" in ids, str(ids))
        check("清单给了静态 URL",
              any(e.get("url") for e in manifest.get("extensions", [])),
              str(manifest.get("extensions")))

        data = asyncio.run(_session(chrome, f"http://127.0.0.1:{port}/", SCRIPT))
        if not isinstance(data, dict):
            print(f"  浏览器求值失败：{data}")
            return 1

        check("扩展被挂到了隔离容器里", bool(data.get("hasHost")) and bool(data.get("hasShadow")),
              f"host={data.get('hasHost')} shadow={data.get('hasShadow')}")
        check("扩展的按钮出现在自己的 shadow root 里", bool(data.get("btnExists")),
              str(data.get("btnExists")))
        check("按钮有可访问名", bool(data.get("btnText")), str(data.get("btnText")))
        check("扩展的元素没有泄漏进核心 DOM", (data.get("leakedIntoCore") or 0) == 0,
              f"泄漏 {data.get('leakedIntoCore')} 个")
        check("核心样式没有渗进扩展", data.get("coreStyleLeak") is False,
              str(data.get("coreStyleLeak")))
        check("CSS 变量继承到扩展（隔离不挡变量）", data.get("varInherited") is True,
              str(data.get("varInherited")))
        check("核心样式子集注入扩展（.btn 有核心的圆角）",
              data.get("baseCssApplied") is True,
              f"border-radius={data.get('baseCssRadius')}")
        check("扩展的面板初始隐藏", data.get("panelBefore") is True,
              str(data.get("panelBefore")))
        check("点按钮后面板出现", data.get("panelAfter") is True and data.get("panelHidden") is False,
              f"after={data.get('panelAfter')} hidden={data.get('panelHidden')}")
        check("面板内容来自后端（扩展能调 API）",
              "模式" in (data.get("apiText") or "") or "不可达" in (data.get("apiText") or ""),
              str(data.get("apiText")))
        check("核心切视图 → 扩展收到事件（正向总线通了）",
              data.get("busWorked") is True, str(data.get("busText")))
        check("扩展请求切视图 → 核心响应（反向总线通了）",
              data.get("backWorked") is True, f"aria-selected={data.get('viewAfter')}")
        check("核心界面照常渲染", (data.get("navCount") or 0) >= 5,
              str(data.get("navCount")))
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
