"""index.html 内联脚本的**真实语法检查**（交给 Chrome 的解析器编译一遍）。

为什么要有这个：括号配平检查只能抓"多一个括号"这类手误，语法错误抓不到。
上一轮就漏过一次 —— `${rest.length ?: ...}` 里把三元写成了 `?:`，
括号是配平的、引号是成对的、检查全绿，而浏览器里整段 2972 行脚本
因 SyntaxError 一行都不执行，页面只剩导航外壳。

做法：把 script 块源码嵌进一个临时 HTML，页面加载时用 `new Function(src)`
交给 JS 引擎编译 —— 这是引擎自己的解析器，语法错误会带行列号抛出来。
只编译不执行（new Function 不调用），所以没有副作用。
再用 `chrome --headless --dump-dom` 把页面里的结果标记读回来。

运行：python -m tests.check_web_syntax
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# 杀 Chrome 的**进程树** —— 只 terminate 父进程的话，
# 子进程会活下来锁着 profile 目录（删不掉，还会挡住下一个模块的 Chrome）。
from _webdrive import close_browser, kill_tree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CORE = Path(__file__).resolve().parents[1]
WEB = CORE / "web" / "index.html"
MODULES_DIR = CORE / "web" / "modules"
CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
# 页面把结果写进这两个标签，靠正则读回来
OK_MARK = "__ORYX_SYNTAX_OK__"
ERR_MARK = "__ORYX_SYNTAX_ERR__"
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILED.append(name)


def find_chrome() -> str | None:
    for p in CHROME_CANDIDATES:
        if Path(p).exists():
            return p
    return shutil.which("chrome") or shutil.which("msedge")


def extract_scripts(text: str) -> list[str]:
    return re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", text, re.S)


def build_probe_html(src: str) -> str:
    """做一个临时页面：把源码以 JSON 字符串内嵌，加载后交给引擎编译。

    用 `new Function(src)` 编译 —— 它只能编**普通脚本**，
    遇到 import/export 会报「Cannot use import statement outside a module」。

    所以 ES Module 的语法检查走另一条路：**先把 import/export 语句剥掉**，
    剩下的纯粹是 JS 语法，用 new Function 编译即可。
    剥掉 import/export 不会掩盖语法错误 —— 那些错误在表达式/语句部分，
    new Function 照样能抓到。
    （模块的**接线**正确性由 tests/check_web_modules.py 用真实浏览器验证。）
    """
    import json

    return (
        "<!doctype html><meta charset=utf-8><body>"
        f"<div id=ok>{OK_MARK}</div><div id=err></div><script>"
        "const SRC = " + json.dumps(src, ensure_ascii=False) + ";\n"
        "try { new Function(SRC); document.getElementById('ok').textContent += ':1';\n"
        "} catch (e) {\n"
        "  document.getElementById('err').textContent =\n"
        f"    '{ERR_MARK}' + String((e && e.message) || e);\n"
        "}\n"
        "</script>"
    )


def strip_module_syntax(src: str) -> str:
    """把 ES Module 的 import/export 语句剥掉，剩下的才是普通脚本语法。

    要剥的：
      · `import { a, b } from './x.js';` / `import './x.js';`
      · `export function f(...)`      → `function f(...)`
      · `export const x = ...`        → `const x = ...`
      · `export { a, b };`            → 空
    """
    out: list[str] = []
    for line in src.splitlines():
        s = line.strip()
        if s.startswith("import ") and s.endswith(";"):
            continue
        if s.startswith("export {"):
            continue
        line = re.sub(r"^(\s*)export\s+", r"\1", line)
        out.append(line)
    return "\n".join(out)


def compile_in_chrome(chrome: str, src: str) -> tuple[bool, str]:
    """在 Chrome 里编译一段 JS。

    每个模块用**自己的**临时目录 —— 共用一个的话，
    Chrome 的子进程还活着时会锁住 profile，
    下一个模块的 Chrome 起不来（「dump-dom 无输出」）。

    编译完**杀进程树**并删目录 —— 只等父进程的话，目录删不掉，
    跑几百轮 temp 里能堆出几百个 Chrome profile（实测占 6GB）。
    """
    tmp = Path(tempfile.mkdtemp(prefix="oryx_syntax_"))
    try:
        return _compile_in_chrome(chrome, src, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _compile_in_chrome(chrome: str, src: str, tmp: Path) -> tuple[bool, str]:
    """真正干活的那部分（临时目录由调用方管）。"""
    page = tmp / "probe.html"
    page.write_text(build_probe_html(src), "utf-8")

    proc = subprocess.Popen(
        [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--user-data-dir=" + str(tmp / "profile"),
            "--virtual-time-budget=5000",   # 给引擎留出编译时间
            "--dump-dom",
            page.as_uri(),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        stdout, _stderr = proc.communicate(timeout=120)
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        stdout = ""
    else:
        kill_tree(proc)   # 父进程退了，子进程还活着 —— 它们锁着 profile
    dom = stdout or ""
    # 只从结果 div 里读 —— dump-dom 会把 <script> 的源码原文也吐出来，
    # 直接全文搜标记会匹配到探针脚本自己那几行，报出假错误
    m = re.search(r'<div id="err">(.*?)</div>', dom, re.S)
    if m:
        err = m.group(1).strip()
        if err:
            return False, err
    m = re.search(r'<div id="ok">(.*?)</div>', dom, re.S)
    if m and OK_MARK + ":1" in m.group(1):
        return True, ""
    return False, "页面没返回编译结果（dump-dom 无输出或超时）"



# ── 用 CDP 检查语法 ────────────────────────────────────────────────────
#
# 不用 `chrome --dump-dom`：那依赖浏览器的命令行开关，
# 而这台机器上 find_chrome() 找到的是 Edge —— 它的 --dump-dom 输出为空
# （returncode 0，stdout 0 字节），于是所有模块都被误报成语法错误。
#
# CDP 稳（Edge / Chrome 都行），而且**一个** Chrome 查完所有模块。

def _pick_port() -> int:
    """挑一个没人用的端口。"""
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def compile_all_in_chrome(chrome: str,
                          modules: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """在浏览器里逐个编译模块，返回 [(模块名, 错误信息)] —— 只列有错的。"""
    import asyncio
    import json as _json
    import urllib.request

    import websockets

    async def _run() -> list[tuple[str, str]]:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            profile = Path(td) / "profile"
            profile.mkdir(parents=True, exist_ok=True)
            port = _pick_port()
            proc = subprocess.Popen(
                [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
                 f"--remote-debugging-port={port}",
                 f"--user-data-dir={profile}",
                 "--remote-allow-origins=*", "about:blank"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            try:
                for _ in range(60):
                    time.sleep(0.5)
                    try:
                        with urllib.request.urlopen(
                                f"http://127.0.0.1:{port}/json/version",
                                timeout=3) as r:
                            if r.read():
                                break
                    except Exception:
                        continue
                else:
                    return [("(浏览器)", "Chrome 没起来")]

                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/json/new?about:blank", method="PUT")
                target = _json.loads(
                    urllib.request.urlopen(req, timeout=10).read().decode())

                failed: list[tuple[str, str]] = []
                async with websockets.connect(
                    target["webSocketDebuggerUrl"], max_size=32 * 1024 * 1024,
                    open_timeout=15,
                ) as ws:
                    msg_id = 0

                    async def evaluate(expr: str) -> dict:
                        nonlocal msg_id
                        msg_id += 1
                        await ws.send(_json.dumps({
                            "id": msg_id, "method": "Runtime.evaluate",
                            "params": {"expression": expr, "returnByValue": True},
                        }))
                        while True:
                            raw = await asyncio.wait_for(ws.recv(), timeout=30)
                            msg = _json.loads(raw)
                            if msg.get("id") != msg_id:
                                continue
                            res = msg.get("result", {}) or {}
                            if "exceptionDetails" in res:
                                det = res["exceptionDetails"] or {}
                                text = (det.get("exception") or {}).get(
                                    "description") or det.get("text") or "语法错误"
                                return {"error": str(text)}
                            return {"value": (res.get("result", {}) or {}).get("value")}

                    for name, body in modules:
                        # new Function(src) —— 只编译不执行，
                        # 语法错误会作为异常抛出来。
                        payload = _json.dumps(body)
                        out = await evaluate(
                            "(() => { try {"
                            f" new Function({payload});"
                            " return {ok:true}; }"
                            " catch (e) { return {ok:false, msg: String(e && e.message || e)}; } })()"
                        )
                        val = out.get("value") or {}
                        if not out.get("error") and not val.get("ok"):
                            failed.append((name, str(val.get("msg", "语法错误"))[:200]))
                return failed
            finally:
                close_browser(port)
                kill_tree(proc)
                shutil.rmtree(profile, ignore_errors=True)

    return asyncio.run(_run())


def main() -> int:
    chrome = find_chrome()
    check("找得到 Chrome/Edge", bool(chrome), "装了浏览器但没在预期路径")
    if not chrome:
        return 1

    text = WEB.read_text("utf-8")
    blocks = extract_scripts(text)
    module_files = sorted(MODULES_DIR.glob("*.js"))
    check("提取到 script 块或模块文件",
          bool(blocks) or bool(module_files),
          f"内联 {len(blocks)} 个 / 模块 {len(module_files)} 个")
    if not blocks and not module_files:
        return 1

    # 逐个模块编译 —— 一次编译一个文件，出错能直接定位到是哪个模块。
    # 全部合并成一大块编译的话，报错行号对不上源文件，等于没有定位能力。
    failed_modules: list[tuple[str, str]] = []
    total_bytes = 0
    # 所有模块**共用**一个临时目录，跑完删掉 ——
    # 以前每个模块建一个自己的，跑完从不删，temp 里堆了八百个。
    to_check: list[tuple[str, str]] = []
    for f in module_files:
        src = f.read_text("utf-8")
        total_bytes += len(src)
        to_check.append((f.name, strip_module_syntax(src)))

    # 一个 Chrome 查完全部模块 —— 不用每个模块起一次
    failed_modules = compile_all_in_chrome(chrome, to_check)

    check(f"全部 {len(module_files)} 个模块语法合法",
          not failed_modules,
          "; ".join(f"{n}: {e[:90]}" for n, e in failed_modules[:3]))
    print(f"  （交给 Chrome 编译 {total_bytes} 字节的模块脚本）")

    if failed_modules:
        print("\n  引擎报错原文（人工据此定位）:")
        for name, err in failed_modules[:3]:
            print(f"    [{name}]")
            for line in str(err).splitlines()[:2]:
                print(f"      {line}")
        return 1

    # 内联脚本（如果有）也要编译
    if blocks:
        src = "\n".join(blocks)
        ok, err = compile_in_chrome(chrome, src)
        check("内联脚本语法合法", ok, err)
        if not ok:
            return 1

    # 沿用原来的可疑写法扫描（针对合并后的整体）
    src = "\n".join(f.read_text("utf-8") for f in module_files) + "\n".join(blocks)
    suspicious = [
        (i, ln) for i, ln in enumerate(src.splitlines(), 1)
        if re.search(r"[^\s?:]\?:", ln) and "?:" in ln
    ]
    if suspicious:
        print(f"\n  源码里有 {len(suspicious)} 处可疑写法（?: 前缺空格）:")
        for i, ln in suspicious[:8]:
            print(f"    {i}: {ln.strip()[:150]}")

    print("\n" + "=" * 60)
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())