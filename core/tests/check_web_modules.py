"""前端 ES Module 的**真实加载检查**（Chrome DevTools Protocol 驱动）。

为什么需要这个检查
------------------
前端从「单文件内联脚本」拆成 ES Module 之后，原来那个
`new Function(src)` 式的语法检查**不再够用**：

  · 单文件时，语法错会带行列号抛出来 —— 一个文件一个错，好定位；
  · 拆成模块后，真正的风险从「语法错」变成了「**接线错**」：
      - import 的符号在源模块里没 export（运行时报 SyntaxError）
      - 模块之间形成循环依赖、读到 TDZ（运行时报 ReferenceError）
      - 某个模块没被加载 / 加载顺序不对（运行时 undefined）

这些都是**运行期**错误，`new Function` 编译不出来 —— 必须真的让浏览器
加载一遍。

为什么用 CDP 而不是 --dump-dom
-----------------------------
Oryx 的界面会开一条 **EventSource 长连接**（SSE 收 Agent 事件）。
`chrome --headless --dump-dom` 会等页面「网络静止」，长连接让它**永远
等不到** —— 表现为命令挂起直到超时，而不是拿到错误信息。
`--screenshot` 同样会挂。

CDP（Chrome DevTools Protocol）不依赖页面静止：直接订阅
`Runtime.consoleAPICalled` / `Runtime.exceptionThrown`，几秒后主动断开。
项目后端本来就依赖 websockets，所以不引入新依赖。

做法
----
起一个真实的 uvicorn 服务 → 用 CDP 打开首页 → 采集 console 错误与
未捕获异常 → 判定「页面加载后无运行期错误」。

运行：python -m tests.check_web_modules
"""
from __future__ import annotations

import asyncio

from _webcheck import describe_busy, ports_free
from _webdrive import stop_chrome
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CORE = Path(__file__).resolve().parents[1]
MODULES = CORE / "web" / "modules"
INDEX = CORE / "web" / "index.html"

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

FAILED: list[str] = []

# CDP 调试端口（run_cdp_check 里启动 Chrome 时确定）
_CDP_PORT = 9333


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILED.append(name)


def find_chrome() -> str | None:
    for p in CHROME_CANDIDATES:
        if Path(p).exists():
            return p
    return shutil.which("chrome") or shutil.which("msedge")


def module_files() -> list[Path]:
    return sorted(MODULES.glob("*.js"))


# ── 静态检查：import / export 一致性 ─────────────────────────────────
def check_import_export() -> None:
    """每个 import 的符号，源模块必须真的 export 了。"""
    exports: dict[str, set[str]] = {}
    for f in module_files():
        src = f.read_text("utf-8")
        names: set[str] = set()
        for m in re.finditer(r"^export\s+(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", src, re.M):
            names.add(m.group(1))
        for m in re.finditer(r"^export\s+(?:const|let|var)\s+([A-Za-z_$][\w$]*)", src, re.M):
            names.add(m.group(1))
        for m in re.finditer(r"^export\s*\{([^}]*)\}", src, re.M):
            for part in m.group(1).split(","):
                part = part.strip().split(" as ")[0].strip()
                if part:
                    names.add(part)
        exports[f.stem] = names

    bad: list[str] = []
    for f in module_files():
        src = f.read_text("utf-8")
        for m in re.finditer(r"import\s*\{([^}]*)\}\s*from\s*'\./([\w-]+)\.js'", src):
            target = m.group(2)
            if target not in exports:
                bad.append(f"{f.name}: 导入的模块 {target}.js 不存在")
                continue
            for part in m.group(1).split(","):
                name = part.strip().split(" as ")[0].strip()
                if not name:
                    continue
                if name not in exports[target]:
                    bad.append(f"{f.name}: 导入 {name}，但 {target}.js 没有 export 它")
    check("import 的符号都能在源模块找到 export", not bad, "; ".join(bad[:5]))


def check_no_cycles() -> None:
    """模块依赖图必须无环。

    为什么：无环才能保证 ES Module 的求值顺序确定，不会读到未初始化的
    绑定（TDZ）。这里同时查**有向**与**无向**两种：
      · 有向环 = 直接互相 import，必然 TDZ；
      · 无向环 = 求值顺序不确定，间接 TDZ 风险。
    """
    deps: dict[str, set[str]] = {}
    for f in module_files():
        src = f.read_text("utf-8")
        deps[f.stem] = {m.group(1) for m in
                        re.finditer(r"from\s*'\./([\w-]+)\.js'", src)}

    def cycles_of(graph: dict[str, set[str]]) -> list[str]:
        color: dict[str, int] = {}
        out: list[str] = []

        def dfs(u: str, path: list[str]) -> None:
            color[u] = 1
            path.append(u)
            for v in sorted(graph.get(u, ())):
                if color.get(v) == 1:
                    out.append(" → ".join(path[path.index(v):] + [v]))
                elif color.get(v, 0) == 0:
                    dfs(v, path)
            path.pop()
            color[u] = 2

        for m in sorted(graph):
            if color.get(m, 0) == 0:
                dfs(m, [])
        return out

    directed = cycles_of(deps)
    undirected: dict[str, set[str]] = {}
    for a, bs in deps.items():
        s = set(bs)
        for b in bs:
            s |= deps.get(b, set())
        undirected[a] = s
    und = cycles_of(undirected)

    check("模块依赖图无有向环（无互相 import）", not directed, "; ".join(directed[:3]))
    check("模块依赖图无向无环（求值顺序确定）", not und, "; ".join(und[:3]))


def check_every_module_imported() -> None:
    """每个模块都要被某个地方加载，否则它就是死文件。"""
    html = INDEX.read_text("utf-8")
    referenced = set(re.findall(r"modules/([\w-]+)\.js", html))
    imported: set[str] = set()
    for f in module_files():
        src = f.read_text("utf-8")
        imported |= set(re.findall(r"from\s*'\./([\w-]+)\.js'", src))
        imported |= set(re.findall(r"import\s*'\./([\w-]+)\.js'", src))
    missing = {f.stem for f in module_files()} - referenced - imported
    check("没有孤立模块（每个都被入口或 import 引用）", not missing, str(sorted(missing)))


def check_entry_exists() -> None:
    """index.html 的入口脚本要指向真实存在的模块。"""
    html = INDEX.read_text("utf-8")
    m = re.search(r'<script[^>]*type=["\']module["\'][^>]*src=["\']([^"\']+)["\']', html)
    check("index.html 用 <script type=\"module\"> 引入入口", bool(m))
    if not m:
        return
    src = m.group(1)
    rel = src.split("/web/", 1)[-1]
    check("入口指向的文件存在", (CORE / "web" / rel).exists(), src)


# ── CDP 真实加载检查 ────────────────────────────────────────────────
async def _cdp_probe(port: int, url: str, wait: float) -> dict:
    """用 CDP 打开页面并采集运行期错误。"""
    import websockets

    base = f"http://127.0.0.1:{port}"
    async with websockets.connect(f"{base}/devtools/browser",
                                   open_timeout=15) as browser_ws:
        # 新建一个 target
        async with websockets.connect(f"{base}/devtools/page",
                                   open_timeout=15) as page_ws:
            return {}


def run_cdp_check(chrome: str, url: str, wait: float = 6.0) -> dict:
    """启动 Chrome（带 remote debugging），用 CDP 采集错误。"""
    import urllib.request

    # ignore_cleanup_errors —— Chrome 的子进程可能还锁着 profile 文件，
    # 清理失败不该让整个脚本崩掉（子进程由下面的 stop_chrome 收掉）。
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        profile = Path(td) / "profile"
        profile.mkdir(parents=True, exist_ok=True)
        port = _CDP_PORT
        proc = subprocess.Popen(
            [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
             f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
             "--remote-allow-origins=*", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            # 等 DevTools 端口就绪
            ready = False
            for _ in range(60):
                time.sleep(0.5)
                try:
                    with urllib.request.urlopen(
                            f"http://127.0.0.1:{port}/json/version", timeout=3) as r:
                        if r.read():
                            ready = True
                            break
                except Exception:
                    continue
            if not ready:
                return {"errors": ["Chrome DevTools 端口没起来"], "ok": False}

            return asyncio.run(_cdp_collect("", url, wait))
        finally:
            # 统一收工 —— 杀**进程树**（只 terminate 的话 Chrome 的子进程
            # 会活下来锁着 profile，删目录时 PermissionError）。
            stop_chrome(proc, profile, port)


async def _cdp_collect(browser_ws_url: str, url: str, wait: float) -> dict:
    """连到 **页面级** WebSocket（不是 browser 级），订阅异常与 console。

    用 /json/new 建页最简单：拿到该页自己的 wsDebuggerUrl，
    直接连上去就是页面上下文，不需要处理 Target/attach 的会话协议。
    """
    import urllib.request

    import websockets

    errors: list[str] = []
    # 通过 HTTP 端点新建一个标签页，拿它的 wsDebuggerUrl
    req = urllib.request.Request(
        f"http://127.0.0.1:{_CDP_PORT}/json/new?about:blank",
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        target = json.loads(r.read().decode())
    page_ws_url = target.get("webSocketDebuggerUrl", "")
    if not page_ws_url:
        return {"errors": ["拿不到页面 WebSocket 地址"], "ok": False}

    async with websockets.connect(page_ws_url, max_size=16 * 1024 * 1024,
                                   open_timeout=15) as ws:
        msg_id = 0

        async def send(method: str, params: dict | None = None) -> None:
            nonlocal msg_id
            msg_id += 1
            await ws.send(json.dumps({"id": msg_id, "method": method,
                                      "params": params or {}}))

        await send("Runtime.enable")
        await send("Log.enable")
        await send("Page.enable")
        await send("Page.navigate", {"url": url})

        end = time.time() + wait
        while time.time() < end:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=2)
            except asyncio.TimeoutError:
                continue
            except Exception:
                break
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            method = msg.get("method", "")
            params = msg.get("params", {})
            if method == "Runtime.exceptionThrown":
                det = params.get("exceptionDetails", {})
                exc = det.get("exception") or {}
                text = exc.get("description") or det.get("text", "")
                errors.append(f"未捕获异常: {text[:400]}")
            elif method == "Runtime.consoleAPICalled" and params.get("type") == "error":
                args = params.get("args", [])
                text = " ".join(
                    str(a.get("value") if "value" in a else a.get("description", ""))
                    for a in args)
                errors.append(f"console.error: {text[:400]}")
            elif method == "Log.entryAdded":
                entry = params.get("entry", {})
                if entry.get("level") == "error":
                    errors.append(f"日志错误: {str(entry.get('text',''))[:400]}")
        return {"errors": errors, "ok": True}


def main() -> int:
    print("=" * 60)
    print("前端 ES Module 真实加载检查")
    print("=" * 60)

    mods = module_files()
    check("模块目录存在且有内容", bool(mods), str(MODULES))
    if not mods:
        return 1
    print(f"  模块数：{len(mods)}")

    print("\n── 静态检查 ──")
    check_import_export()
    check_no_cycles()
    check_every_module_imported()
    check_entry_exists()

    chrome = find_chrome()
    if not chrome:
        print("\n  跳过真实加载检查：找不到 Chrome/Edge")
        return 1 if FAILED else 0

    print("\n── 真实加载检查（起服务 + Chrome CDP）──")
    import uvicorn

    from oryx.server import app, API_TOKEN

    port = 8797
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
    try:
        result = run_cdp_check(chrome, url)
        errors = result.get("errors", [])
        if not result.get("ok"):
            check("Chrome 能加载页面并回传探测结果", False, "; ".join(errors[:3]))
        else:
            check("Chrome 能加载页面并回传探测结果", True)
            # 过滤掉与模块拆分无关的噪声（如 favicon 404）
            real = [e for e in errors
                    if "favicon" not in e.lower() and "net::ERR" not in e]
            check("页面加载后没有运行期错误", not real,
                  "; ".join(real[:5]))
    finally:
        srv.should_exit = True
        time.sleep(0.8)

    print("\n" + "=" * 60)
    if FAILED:
        print(f"失败 {len(FAILED)} 项：{FAILED}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
