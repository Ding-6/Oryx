"""浏览器验证的共用驱动 —— 起服务、装扩展、开 Chrome、读结果。

为什么抽出来
------------
这几个验证脚本以前各写各的：起服务的代码抄一遍、找 Chrome 的抄一遍、
端口检查抄一遍（还抄漏了 —— 有的只查服务端口，不查 CDP 端口）。

抄来抄去的结果就是**漏**。这个模块把公共部分收在一处，
每个脚本只写自己关心的两件事：**装什么**、**查什么**。

端口的坑
--------
上一个测试的进程没退干净 → 端口还占着 → 这次起服务失败（端口冲突）
但**不报错** —— 浏览器连的是上次的旧服务，测出来的结论全是假的。

CDP 端口更隐蔽：Chrome 进程 terminate 后端口要过一会儿才释放，
下一个脚本用同一个 CDP 端口时可能连上**上一个** Chrome。

所以两个端口都要查（见 `_webcheck`）。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from _webcheck import port_in_use

CORE = Path(__file__).resolve().parents[1]

# 自动分配 CDP 端口的起点。
#
# 为什么不用固定端口：**同一个脚本里连跑两次探针**是常事
# （先点停用、再点启用）。上一个 Chrome 的进程组退干净要一会儿，
# 端口没释放时新 Chrome 起不来 —— 而 `json/new` 会打到**上一个**
# Chrome 上，evaluate 在旧标签页里执行，看起来一切正常、结果却是错的。
#
# 所以：每次探针都挑一个**当前空闲**的端口。
# 分配器的起点 —— 必须**避开各脚本的固定 CDP 端口**。
#
# 固定端口有：9333/9334/9341/9343/9345/9347/9349/9351…
# 分配区间要是跟它们重叠，一个脚本的第 N 个探针就会抢走
# 另一个脚本的固定端口 —— 那个脚本一跑就「端口被占用」被跳过
# （实测 ext_deps 的第 3 个探针抢了 9349，正好是 ext_toggle 的）。
_CDP_NEXT = 9400


def alloc_cdp_port(preferred: int | None = None) -> int:
    """挑一个当前没人用的 CDP 端口。"""
    global _CDP_NEXT
    candidates: list[int] = []
    if preferred:
        candidates.append(preferred)
    candidates.extend(range(_CDP_NEXT, _CDP_NEXT + 60))
    for c in candidates:
        if c and not port_in_use(c):
            _CDP_NEXT = c + 1
            return c
    raise RuntimeError("找不到空闲的 CDP 端口")


def _wait_port_free(port: int, timeout: float = 30.0) -> None:
    """等端口释放 —— 不然下一个探针会撞上。

    30 秒：Chrome 的子进程退出很慢（实测残留 20 个 msedge，
    一些还在退出过程中），端口短时间内还占着。
    """
    """等端口释放 —— 不然下一个探针会撞上。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not port_in_use(port):
            return
        time.sleep(0.3)


def setup_browser_temp(root: Path | None = None) -> dict[str, str]:
    """把浏览器用的临时目录指到数据根目录（D 盘）。

    为什么需要
    ----------
    Edge/Chrome 会在**系统 temp** 里创建自己的临时目录：
    ``HeadlessEdge*``、``msedge_url_fetcher_*``（**每个 140MB**）、
    ``Diagnostics``、``DiagOutputDir``…

    一次浏览器验证跑下来能攒**十几个 GB** —— 实测 C 盘的 temp 到过
    **14.5GB**，可用空间从 54GB 掉到 36GB。

    项目本来就有这套重定向（`oryx/paths.py` 的 `redirect_env`），
    但**测试脚本起 Chrome 时没走它** —— 于是全落在 C 盘。

    在跑测试**之前**调一次：它改的是 `os.environ`，
    **子进程（Chrome）会继承** ✓

    （本进程自己的 `tempfile` 缓存在第一次调用 `gettempdir()` 时就固定了，
    改不动 —— 但那没关系，我们要管的是 Chrome。）
    """
    from oryx.paths import redirect_env

    return redirect_env(root)


def close_browser(cdp_port: int, timeout: float = 8.0) -> None:
    """通过 CDP 让 Chrome **自己退出**。

    为什么比杀进程树干净
    ------------------
    Windows 上 Chrome/Edge 的**启动器进程会先退出**（把参数转交给真正的进程），
    所以 `subprocess.Popen` 拿到的 PID 很快就失效了 ——
    `taskkill /T` 找不到进程树，真正的浏览器进程成了**孤儿**，
    一直活到会话结束（实测一轮跑完残留 5 个 msedge）。

    发 `Browser.close` 让 Chrome 自己收摊：所有子进程一起走，端口干净释放。
    """
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{cdp_port}/json/version", timeout=3
        ) as r:
            info = json.loads(r.read().decode())
        ws_url = info.get("webSocketDebuggerUrl")
        if not ws_url:
            return
    except Exception:
        return

    async def _close() -> None:
        import websockets
        try:
            async with websockets.connect(
                ws_url, max_size=1024 * 1024, open_timeout=5
            ) as ws:
                await ws.send(json.dumps({
                    "id": 1, "method": "Browser.close",
                }))
                try:
                    await asyncio.wait_for(ws.recv(), timeout=3)
                except asyncio.TimeoutError:
                    pass
        except Exception:
            pass

    try:
        asyncio.run(_close())
    except Exception:
        pass


def kill_tree(proc: subprocess.Popen) -> None:
    """杀掉 Chrome **及其全部子进程**。

    为什么不能只 `terminate()`
    ------------------------
    Chrome 会起一串子进程（zygote、renderer…）。`terminate()` 只杀父进程，
    子进程会活下来 —— 而它们**锁着 profile 目录**。

    后果是连锁的：

    1. `shutil.rmtree(profile)` 删不掉（文件被锁，ignore_errors 静默跳过）
    2. 下一次跑，profile 还是上次的（还被锁着）
    3. 新 Chrome 启动极慢（实测 12 秒 → 29 秒），有时干脆起不来
    4. 探针一路等到超时 —— 整个脚本卡住

    Windows 上用 `taskkill /T` 杀整棵进程树；其它平台退回 terminate。
    """
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True, timeout=30, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    if proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def stop_chrome(proc: subprocess.Popen | None, profile: Path | None = None,
                cdp_port: int | None = None) -> None:
    """收工：让 Chrome 优雅退出 + 杀进程树 + 清掉它的 profile。

    **所有脚本共用这一个** —— 以前每个脚本各写一份自己的清理，
    有的只 `terminate()`（杀不掉子进程）、有的不清 profile，
    结果就是：Chrome 的子进程活下来锁着 profile，
    下一次跑 Chrome 起不来（实测等 160 秒后报「Chrome 没起来」）。

    `cdp_port` 给了就先走 CDP 的 `Browser.close` ——
    Windows 上浏览器**启动器进程会先退出**，`taskkill /T` 找不到进程树，
    真正的浏览器进程就成了孤儿（实测一轮残留 20 个，越积越多 → 后面的脚本超时）。
    让浏览器自己退，所有子进程一起走。
    """
    if cdp_port is not None:
        close_browser(cdp_port)
    if proc is not None:
        kill_tree(proc)
    if profile is not None:
        cleanup_profile(profile)


def sweep_profiles(keep: int | None = None) -> None:
    """清掉历史遗留的 Chrome profile 目录。

    只清**当前的**那个 —— 以前扫全部（按端口隔离时留下的），
    每个删不掉的要重试 3 秒，开跑前白等好几秒（实测 ext_deps 54s → 140s）。

    残留的 `.stale-*` 目录也顺手清一下（它们基本都能删掉）。
    """
    base = Path(tempfile.gettempdir())
    keep_name = f"oryx_web_profile_{keep}" if keep is not None else None
    for d in base.glob("oryx_web_profile*"):
        if not d.is_dir() or d.name == keep_name:
            continue
        # 批量目录**只试一次** —— 删不掉就改名脱钩。
        # （每个都重试 3 秒的话，攒了十几个残留就能白等半分钟）
        shutil.rmtree(d, ignore_errors=True)
        if d.exists():
            try:
                d.rename(d.with_name(f"{d.name}.stale-{int(time.time())}"))
            except OSError:
                pass


def cleanup_profile(profile: Path) -> None:
    """删掉 Chrome 的 profile 目录。

    删不掉就**再试几次** —— 刚 kill 完进程树时，文件锁可能还没释放
    （Windows 上进程退出与文件解锁之间有延迟）。

    实在删不掉就**改名脱钩**：名字带上后缀后，这个目录就跟端口无关了，
    下一个探针不会撞上它。
    """
    for _ in range(3):
        shutil.rmtree(profile, ignore_errors=True)
        if not profile.exists():
            return
        time.sleep(0.5)
    try:
        profile.rename(profile.with_name(f"{profile.name}.stale-{int(time.time())}"))
    except OSError:
        pass


class Result:
    """一个验证脚本的结果收集器。"""

    def __init__(self) -> None:
        self.failed: list[str] = []

    def check(self, name: str, cond: bool, detail: str = "") -> bool:
        ok = bool(cond)
        print(f"  {'PASS' if ok else 'FAIL'}  {name}"
              + ("" if ok else f"  {detail}"))
        if not ok:
            self.failed.append(name)
        return ok

    def summary(self) -> int:
        if self.failed:
            print(f"\n失败 {len(self.failed)} 项：{self.failed}")
            return 1
        print("\n============================================================")
        print("全部通过")
        return 0


def find_chrome() -> str | None:
    from check_web_modules import find_chrome as fc
    return fc()


def wait_ready(port: int, timeout: float = 40.0) -> bool:
    """等服务起来。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/ping", timeout=3
            ) as r:
                if r.read():
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def install_ui_ext(home: Path, ext_id: str, manifest: dict,
                    js: str) -> None:
    """在用户目录里装一个界面扩展。"""
    d = home / "ui-extensions" / ext_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "oryx.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (d / "index.js").write_text(js, encoding="utf-8")


async def wait_page_ready(ws, timeout: float = 25.0) -> bool:
    """等页面**真的加载完** —— 轮询 `document.readyState`。

    为什么不用固定 sleep
    ------------------
    固定 sleep 是**猜**要多久。机器负载高时（批量跑时前一个脚本的
    Chrome 还在退出）猜不够 —— 页面还没渲染完探针就查元素，
    落在空表上 → 断言失败。

    表现为「**单独跑通过、批量跑失败**」，而且每次挂的脚本还不一样 ——
    最难查的那种。轮询 readyState 是「等到好」。

    `readyState=complete` 不等于模块都加载完，所以调用方之后
    通常还要留一点余量。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        await asyncio.sleep(0.3)
        await ws.send(json.dumps({
            "id": 290, "method": "Runtime.evaluate",
            "params": {"expression": "document.readyState",
                       "returnByValue": True},
        }))
        if await _read_eval(ws, 290, timeout=3) == "complete":
            return True
    return False


async def _read_eval(ws, msg_id: int, timeout: float = 5.0):
    """在 ws 上等某个 id 的 evaluate 结果，返回它的 value。

    ws 上会混着别的消息（Page.enable 的响应、console 事件…），
    所以要按 id 挑，挑不出就继续等。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        try:
            msg = json.loads(raw)
        except Exception:
            continue
        if msg.get("id") != msg_id:
            continue
        res = msg.get("result", {}) or {}
        if "exceptionDetails" in res:
            return None
        return (res.get("result", {}) or {}).get("value")
    return None


async def _probe(cdp_port: int, url: str, expression: str,
                 wait: float = 8.0) -> dict:
    """开 Chrome、访问页面、执行一段表达式、拿结果。"""
    import websockets

    chrome = find_chrome()
    if not chrome:
        return {"__error__": "找不到 Chrome"}

    # Chrome 的 profile 目录 —— **按 CDP 端口隔离**。
    #
    # 为什么隔离：连续探针共用一个 profile 时，上一个 Chrome 没退干净
    # 就会锁着它 —— 下一个 Chrome 在被锁的 profile 上启动，
    # 实测第一个探针 31 秒、第二个干脆「Chrome 没起来」（等了 160 秒）。
    #
    # 为什么不会堆积：开跑前把**历史残留**一起清掉（见 sweep_profiles）。
    # profile 名**带 CDP 端口** —— 每个探针用自己那份。
    #
    # 共用一个是不行的：上一个 Chrome 没退干净（子进程还锁着文件），
    # 下一个就在坏 profile 上启动 —— 实测断言大面积失败。
    profile = Path(tempfile.gettempdir()) / f"oryx_web_profile_{cdp_port}"
    # **不在这里 sweep** —— 上一个探针的 Chrome 可能还在退出，
    # 它的 profile 正在被使用；扫删会把它删掉 → 那个 Chrome 出错、
    # 端口不释放 → 连锁影响后面的脚本（实测 ext_deps 失败 4 项）。
    # 残留改在**脚本之间**清（那时 Chrome 都退干净了）。
    cleanup_profile(profile)
    profile.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [
            chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
            f"--remote-debugging-port={cdp_port}",
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
                    f"http://127.0.0.1:{cdp_port}/json/version", timeout=3
                ) as r:
                    if r.read():
                        break
            except Exception:
                continue
        else:
            return {"__error__": "Chrome 没起来"}

        req = urllib.request.Request(
            f"http://127.0.0.1:{cdp_port}/json/new?about:blank", method="PUT")
        target = json.loads(
            urllib.request.urlopen(req, timeout=10).read().decode())

        async with websockets.connect(
            target["webSocketDebuggerUrl"], max_size=16 * 1024 * 1024,
            # **必须给 open_timeout** —— 不给的话，连接不上时会一直挂着：
            # 批量跑时上一个 Chrome 还没退干净，json/new 拿到的
            # 可能是已经死掉的 target —— 不超时就是无限等待，整个批次卡死。
            open_timeout=15,
        ) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Page.enable"}))
            await ws.send(json.dumps({"id": 2, "method": "Runtime.enable"}))
            await ws.send(json.dumps({
                "id": 3, "method": "Page.navigate",
                "params": {"url": url},
            }))
            # 等页面**真的加载完**（共用逻辑见 wait_page_ready）
            await wait_page_ready(ws, timeout=max(wait, 25.0))
            # readyState=complete 不等于模块都加载完 —— 再留一点余量
            await asyncio.sleep(1.5)

            # 先注入共用工具函数（单独一次 evaluate ——
            # 拼进表达式里会撞上 ASI：函数声明后面紧跟 `(` 会被当成调用）
            await ws.send(json.dumps({
                "id": 299, "method": "Runtime.evaluate",
                "params": {"expression": OPEN_EXTENSIONS_TAB},
            }))

            await ws.send(json.dumps({
                "id": 300, "method": "Runtime.evaluate",
                "params": {
                    "expression": expression,
                    "returnByValue": True,
                    # async 表达式返回的是 Promise —— 不加这个，
                    # returnByValue 只能序列化成一个空对象，拿不到结果。
                    "awaitPromise": True,
                },
            }))
            logs: list[str] = []
            seen: list[str] = []
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=25)
                except asyncio.TimeoutError:
                    # 超时时把看到的东西带出去 —— 光说「超时」没法定位。
                    return {"__error__": "CDP 超时，没等到 evaluate 的结果",
                            "__seen__": seen[-20:], "__logs__": logs[-10:]}
                msg = json.loads(raw)
                seen.append(str(msg.get("method") or msg.get("id")))
                # 页面弹了 confirm()/alert() —— **必须自动接受**。
                #
                # 不处理的话：headless Chrome 不会自动关对话框，
                # 正在执行的 evaluate 会被挂起 —— 表现是「CDP 超时」，
                # 看起来像探针坏了，其实是页面在等一个没人点的确认框。
                if msg.get("method") == "Page.javascriptDialogOpening":
                    await ws.send(json.dumps({
                        "id": 900 + len(seen),
                        "method": "Page.handleJavaScriptDialog",
                        "params": {"accept": True},
                    }))
                    continue
                if msg.get("method") in ("Runtime.consoleAPICalled",
                                         "Runtime.exceptionThrown"):
                    try:
                        args = msg["params"].get("args") or []
                        text = " ".join(
                            str(a.get("value", a.get("description", "")))
                            for a in args)
                        if text:
                            logs.append(text)
                    except Exception:
                        pass
                if msg.get("id") == 299:
                    continue
                if msg.get("id") == 300:
                    value = (msg.get("result", {}).get("result", {})
                             .get("value"))
                    # 表达式可能返回**任意**类型 —— 不只是 dict。
                    # 以前这里无条件 `data["__logs__"] = logs`，
                    # 表达式返回字符串/数字时会 TypeError，整个脚本崩掉。
                    if isinstance(value, dict):
                        value["__logs__"] = logs
                        return value
                    return {"__value__": value, "__logs__": logs}
    finally:
        # 先让 Chrome **自己退出**（子进程会一起走、端口干净释放），
        # 再杀进程树兜底。
        close_browser(cdp_port)
        kill_tree(proc)
        # profile 也清掉 —— 它比文件本身大得多（缓存），
        # 留着跑几百轮能占好几个 GB。删不掉就重试。
        cleanup_profile(profile)
        # 等端口真的释放 —— 否则下一个探针会撞上同一个 Chrome，
        # 测出来的结论是假的（见 alloc_cdp_port 的注释）。
        _wait_port_free(cdp_port)


OPEN_EXTENSIONS_TAB = """
/* 打开设置页的「扩展」标签。

   注意：**不要无条件点 #setBtn** ——
   那个按钮是「切换」设置面板，已经开着时再点一次会**关掉**它，
   后面的 `[data-sec="extensions"]` 就找不到了。
   所以先看面板在不在，不在才点开。
*/
window.__openExtensionsTab = async function () {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  let sec = document.querySelector('#page [data-sec="extensions"]');
  if (!sec) {
    const btn = document.querySelector('#setBtn');
    if (btn) btn.click();
    await sleep(1200);
    sec = document.querySelector('#page [data-sec="extensions"]');
  }
  if (sec) { sec.click(); await sleep(1600); }
  return !!sec;
};
"""


def run_probe(port: int, cdp_port: int | None = None, expression: str = "",
              wait: float = 8.0) -> dict:
    """在浏览器里跑一段表达式，返回结果。

    `cdp_port` 给了就用它（但会先确认它空着）；不给就自动挑一个。
    """
    chosen = alloc_cdp_port(cdp_port)
    return asyncio.run(
        _probe(chosen, f"http://127.0.0.1:{port}/", expression, wait))


def start_server(port: int, home: Path, token: str):
    """起一个 Oryx 服务（独立配置目录，不碰用户配置）。"""
    env = dict(os.environ)
    env["ORYX_API_TOKEN"] = token
    env["ORYX_HOME"] = str(home)
    return subprocess.Popen(
        [sys.executable, "-m", "oryx", "--no-browser", "--port", str(port)],
        cwd=str(CORE), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def stop_server(server) -> None:
    """停掉服务 —— **要杀整棵进程树**。

    `python -m oryx` 会起子进程（agentd 内核）。
    只 `terminate()` 父进程的话，agentd 变成孤儿进程，
    **继续占着服务端口** —— 下一个脚本的端口检查就失败（被跳过）。

    所以这里跟 Chrome 一样用 `kill_tree`。
    """
    kill_tree(server)


def open_settings(page: str) -> str:
    """一段 JS：打开设置页的某个标签，返回页面状态。

    设置页的标签是靠 hash 或点击切的 —— 各脚本自己决定，
    这里只提供一个统一的「进设置页」动作。
    """
    return f"""
    (async () => {{
      const click = (sel) => {{
        const el = document.querySelector(sel);
        if (el) el.click();
        return !!el;
      }};
      click('[data-sec="extensions"]');
      await new Promise(r => setTimeout(r, 1200));
      return true;
    }})()
    """
