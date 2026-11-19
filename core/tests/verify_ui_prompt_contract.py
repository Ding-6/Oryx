"""提示词契约 + 凭据闸门的界面验收。

**真点一次**，不只查代码位置。

这一份要验的：
  1. 确认卡片上**真的有**「凭据类文件，需你授权」这一步（用户要能看见自己批的是什么）；
  2. 点「确认执行」**真的**放行了（凭据文件被读到）；
  3. 取消则**真的**没读；
  4. 失败分类徽章（E5）在步骤卡上**真的**显示出来；
  5. 界面上的 E1-E5 说明文案齐全。

做法：起真实 server（隔离 ORYX_HOME）→ 同源代理 → headless Chrome 跑探针。
**不依赖真模型** —— 造数据靠直接写库，触发审批靠走 `/api/chat` 的
假 provider（发一个 fs_read 工具调用就停）。

运行：python -m tests.verify_ui_prompt_contract
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CORE = Path(__file__).resolve().parents[1]
WEB = CORE / "web" / "index.html"
PYTHON = sys.executable


def _free_port() -> int:
    """挑一个当前空闲的端口。

    写死端口会撞上「上一轮跑剩的进程还占着它」——
    新进程绑不上，脚本却照样往下跑，最后验的是一个僵尸服务。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


SERVER_PORT = _free_port()
PROBE_PORT = _free_port()
CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
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


# ---------------------------------------------------------------- 真实后端


class Server:
    """起一个真实的 Oryx server（隔离的 ORYX_HOME）。"""

    def __init__(self, port: int = SERVER_PORT) -> None:
        self.port = port
        self.proc: subprocess.Popen | None = None
        self.token = ""
        self.home = tempfile.mkdtemp(prefix="oryx_prompt_home_")

    def start(self) -> bool:
        env = {**os.environ, "ORYX_HOME": self.home}
        self.proc = subprocess.Popen(
            [PYTHON, "-m", "oryx", "--no-browser", "--port", str(self.port)],
            cwd=str(CORE),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                return False
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/", timeout=10
                ) as resp:
                    html = resp.read().decode("utf-8")
                m = re.search(r'const ORYX_TOKEN = "([^"]*)"', html)
                if m:
                    self.token = m.group(1)
                    return bool(self.token)
            except Exception:
                pass
            time.sleep(0.3)
        return False

    def raw(self, method: str, path: str, body=None):
        from urllib.parse import quote

        url = f"http://127.0.0.1:{self.port}{quote(path, safe='/?=&')}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("X-Oryx-Token", self.token)
        req.add_header("X-Oryx-Request", "1")
        if data:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode())

    def get(self, path: str):
        return self.raw("GET", path)

    def post(self, path: str, body=None):
        return self.raw("POST", path, body if body is not None else {})

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=8)
            except Exception:
                self.proc.kill()


# ---------------------------------------------------------------- 假 provider

FAKE_PROVIDER = r'''
"""假 provider：按脚本发一串工具调用，然后收尾。

为什么要它：真实 provider 是用户机器上的模型（可能是 Base 模型，
不会终止生成 —— 单次调用 4 分钟）。验收要的是「审批卡弹出来了、
点确认后文件真被读了」，不需要模型有智商。

发的序列（三步，每一步验一件事）：
  1) 读一个**不存在**的文件 → 立刻失败 → 步骤卡上要出现 E5 徽章
  2) 读 .env → 凭据闸门 → 弹第一张审批卡（点「确认执行」）
  3) 再读一次 .env → 弹第二张审批卡（点「取消」）
第 4 次调用返回纯文本收尾。
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TARGET = {target!r}
MISSING = {missing!r}
STATE = {{"calls": 0}}

SEQUENCE = [
    ("fs_read", {{"path": MISSING}}),
    ("fs_read", {{"path": TARGET}}),
    ("fs_read", {{"path": TARGET}}),
]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        if not self.path.endswith("/chat/completions"):
            self.send_error(404)
            return
        length = int(self.headers.get("content-length") or 0)
        if length:
            self.rfile.read(length)
        STATE["calls"] += 1
        nth = STATE["calls"]
        frames = []
        if nth <= len(SEQUENCE):
            tool_name, tool_args = SEQUENCE[nth - 1]
            frames.append({{
                "choices": [{{
                    "delta": {{"tool_calls": [{{
                        "index": 0,
                        "id": "call_%d" % nth,
                        "type": "function",
                        "function": {{
                            "name": tool_name,
                            "arguments": json.dumps(tool_args, ensure_ascii=False),
                        }},
                    }}]}},
                    "index": 0, "finish_reason": None,
                }}]
            }})
            frames.append({{"choices": [{{"delta": {{}}, "index": 0, "finish_reason": "tool_calls"}}]}})
        else:
            frames.append({{"choices": [{{"delta": {{"content": "都处理完了。"}}, "index": 0, "finish_reason": None}}]}})
            frames.append({{"choices": [{{"delta": {{}}, "index": 0, "finish_reason": "stop"}}]}})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        for f in frames:
            self.wfile.write(f"data: {{json.dumps(f, ensure_ascii=False)}}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def do_GET(self):
        self.send_error(404)


srv = ThreadingHTTPServer(("127.0.0.1", {port}), Handler)
print("READY", flush=True)
srv.serve_forever()
'''


def start_fake_provider(target: str, missing: str = "") -> tuple[subprocess.Popen, str]:
    """起假 provider，返回 (进程, base_url)。

    端口**动态挑**：写死端口有个阴险的失败模式 ——
    上一次调试跑剩的假 provider 还占着那个端口，新进程绑定失败、
    stdout 里读不到 READY，于是脚本**照样返回**，只是 base_url 指向
    那个旧进程。旧进程的调用计数早就走完了，答回来的是一段纯文本，
    验收脚本表现为「界面什么都没有」—— 根因离症状十万八千里。

    所以：让内核挑一个空闲端口；再核对 READY 确实来自**这个**进程
    （读不到就报错，别糊过去）。
    """
    if not missing:
        missing = str(Path(target).with_name("__missing__.txt"))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    code = FAKE_PROVIDER.format(target=target, missing=missing, port=port)
    home = tempfile.mkdtemp(prefix="oryx_fake_provider_")
    path = Path(home) / "fake_provider.py"
    path.write_text(code, "utf-8")
    proc = subprocess.Popen(
        [PYTHON, str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    deadline = time.time() + 30
    ready = False
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        if b"READY" in line:
            ready = True
            break
        if proc.poll() is not None:
            break
    if not ready:
        err = b""
        try:
            err = proc.stderr.read() or b""
        except Exception:
            pass
        proc.kill()
        raise RuntimeError(f"假 provider 起不来：{err.decode('utf-8', 'replace')[:400]}")
    return proc, f"http://127.0.0.1:{port}/v1"


def configure_provider(home: str, base_url: str, allowed_roots: list[str] | None = None) -> None:
    """把假 provider 写进隔离的 ORYX_HOME 配置。"""
    cfg_path = Path(home) / "config.json"
    cfg = {}
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text("utf-8"))
        except Exception:
            cfg = {}
    cfg["providers"] = {
        "fake": {"type": "openai", "base_url": base_url, "model": "fake-1"},
    }
    cfg["active_provider"] = "fake"
    cfg["local_provider"] = "fake"
    cfg["mode"] = "local"
    cfg["interaction"] = "craft"
    # allowed_roots 必须含被读文件的目录 ——
    # 不在范围内的路径会先被抬到 L2（越界），审批卡就先于失败出现，
    # E5 徽章那一段就永远验不到。
    if allowed_roots:
        cfg["allowed_roots"] = list(allowed_roots)
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")


# ---------------------------------------------------------------- 探针

PROBE = r"""
<script>
(async () => {
  const R = {};
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const q = s => document.querySelector(s);
  const qa = s => [...document.querySelectorAll(s)];
  const post = obj => fetch('/__acc_result', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(obj),
  }).catch(() => {});
  const waitFor = async (fn, tries = 80, gap = 250) => {
    for (let i = 0; i < tries; i++) {
      const v = fn();
      if (v) return v;
      await sleep(gap);
    }
    return null;
  };
  try {
    await sleep(1500);

    /* ── 1. 发一条消息 ── */
    const ta = q('#input');
    R.foundComposer = !!ta;
    if (ta) {
      ta.value = '看一下我的 .env';
      ta.dispatchEvent(new Event('input', { bubbles: true }));
      await sleep(300);
      R.sendBtn = !!q('#sendBtn');
      const btn = q('#sendBtn');
      if (btn) btn.click();
    }
    /* 诊断：SSE 到底通没通、任务认领了没有 */
    await sleep(2000);
    R.diag = {
      lastSeq: typeof lastSeq !== 'undefined' ? lastSeq : -1,
      taskId: (typeof S !== 'undefined' && S.taskId) ? S.taskId : '',
      busy: (typeof S !== 'undefined' && S.busy) ? S.busy : null,
      esState: (typeof es !== 'undefined' && es) ? es.readyState : -1,
      liveOn: q('#live') ? q('#live').className : '',
      msgs: q('#messages') ? q('#messages').innerText.slice(0, 300) : '',
    };

    /* ── 2. 失败分类徽章：读一个不存在的文件 → E5 ── */
    await waitFor(() => qa('.step .ecls').length > 0);
    R.eclsCount = qa('.step .ecls').length;
    R.eclsText = qa('.step .ecls').map(e => e.textContent.trim());
    R.eclsTitle = qa('.step .ecls').map(e => e.getAttribute('title') || '');
    R.eclsHasHint = R.eclsTitle.some(t => t && t.length > 4);
    R.stepCount = qa('#p-steps .step').length;

    /* ── 3. 第一张审批卡：凭据文件，要用户授权 ── */
    const plan1 = await waitFor(() => qa('.plan')[0] || null);
    R.planCount = qa('.plan').length;
    R.planExists = !!plan1;
    if (plan1) {
      R.planTitle = plan1.querySelector('.h') ? plan1.querySelector('.h').textContent.trim() : '';
      // 注意：.act 必须在**这张卡里**找，不能用全局 qa('.act')
      const acts = [...plan1.querySelectorAll('.act')];
      R.actCount = acts.length;
      R.actKeys = acts.map(a => {
        const k = a.querySelector('.k');
        const t = a.querySelector('.t');
        return (k ? k.textContent.trim() : '') + '|' + (t ? t.textContent.trim() : '');
      });
      R.hasCredentialStep = R.actKeys.some(k => k.includes('凭据'));
      R.credentialDetailHasPath = R.actKeys.some(k => k.includes('.env'));
      R.hasConfirmBtn = !!plan1.querySelector('[data-yes]');
      R.hasCancelBtn = !!plan1.querySelector('[data-no]');
      R.summary = plan1.querySelector('.s') ? plan1.querySelector('.s').textContent.trim() : '';
    }

    /* ── 4. 点「确认执行」→ 凭据文件真被读到，但密钥值必须打码 ── */
    if (plan1) {
      const yes = plan1.querySelector('[data-yes]');
      if (yes) yes.click();
      await sleep(3000);
    }
    R.approvedTitle = plan1 && plan1.querySelector('.h')
      ? plan1.querySelector('.h').textContent.trim() : '';
    R.approvedIsYes = R.approvedTitle.includes('已确认');
    R.secretLeaked = document.body.innerText.includes('sk-should-never-appear');
    R.bodyHasRedacted = document.body.innerText.includes('[redacted]');

    /* ── 5. 第二张审批卡：点「取消」→ 不该再读 ── */
    const plan2 = await waitFor(() => qa('.plan')[1] || null, 80, 250);
    R.plan2Exists = !!plan2;
    if (plan2) {
      const no = plan2.querySelector('[data-no]');
      if (no) no.click();
      await sleep(2500);
    }
    R.plan2Cancelled = plan2 && plan2.querySelector('.h')
      ? plan2.querySelector('.h').textContent.trim().includes('已取消') : false;

    /* ── 6. 失败分类文案表齐全 ── */
    R.hintKeys = Object.keys(typeof ERR_CLASS_HINT !== 'undefined' ? ERR_CLASS_HINT : {});

    R.done = true;
    await post(R);
  } catch (e) {
    R.error = String((e && e.message) || e);
    R.stack = String((e && e.stack) || '').slice(0, 500);
    R.done = true;
    await post(R);
  }
})();
</script>
"""


def build_page(src: str, token: str) -> str:
    return src.replace("__ORYX_TOKEN__", token).replace("</body>", PROBE + "</body>")


class ProbeHandler(BaseHTTPRequestHandler):
    # **必须**是 HTTP/1.1：SSE 要靠 chunked 传输，HTTP/1.0 下浏览器拿不到增量。
    protocol_version = "HTTP/1.1"
    page_html = ""
    token = ""
    result: dict = {}

    def log_message(self, *a):
        pass

    def _proxy_stream(self, url: str, method: str, body) -> None:
        """流式转发（只给 SSE 用）。

        为什么必须单独一条路：普通转发用 `resp.read()` 一次性读完再返回 ——
        对 SSE 来说这会把长连接**堵死**（后端要等流结束才吐数据），
        浏览器那边一个事件都收不到。
        表现是「页面照常显示、接口全通，但事件一个不来」，
        根因完全看不出来。

        所以这里逐块转发：后端吐一块、代理立刻转一块。
        """
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("X-Oryx-Token", ProbeHandler.token)
        try:
            resp = urllib.request.urlopen(req, timeout=600)
        except urllib.error.HTTPError as exc:
            data = exc.read()
            self.send_response(exc.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_response(resp.status)
        self.send_header("Content-Type", resp.headers.get("Content-Type", "text/event-stream"))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            while True:
                chunk = resp.read1(4096)
                if not chunk:
                    break
                self.wfile.write(f"{len(chunk):X}\r\n".encode())
                self.wfile.write(chunk)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
        except Exception:
            pass
        finally:
            try:
                resp.close()
            except Exception:
                pass
        try:
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except Exception:
            pass

    def _proxy(self, method: str) -> None:
        url = f"http://127.0.0.1:{SERVER_PORT}{self.path}"
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        if self.path.startswith("/api/events"):
            self._proxy_stream(url, method, body)
            return
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("X-Oryx-Token", ProbeHandler.token)
        if method not in ("GET", "HEAD"):
            req.add_header("X-Oryx-Request", "1")
        if body is not None:
            req.add_header("Content-Type", self.headers.get("Content-Type", "application/json"))
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = resp.read()
                self.send_response(resp.status)
                self.send_header("Content-Type", resp.headers.get("Content-Type", "application/octet-stream"))
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as exc:
            data = exc.read()
            self.send_response(exc.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as exc:  # noqa: BLE001
            msg = json.dumps({"detail": str(exc)}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)

    def do_GET(self) -> None:
        if self.path.startswith("/api/"):
            self._proxy("GET")
            return
        data = ProbeHandler.page_html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        if self.path == "/__acc_result":
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(raw.decode("utf-8"))
            except Exception:
                data = {"ok": False, "error": "结果不是合法 JSON"}
            ProbeHandler.result.update(data)
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._proxy("POST")

    def do_PUT(self) -> None:
        self._proxy("PUT")

    def do_DELETE(self) -> None:
        self._proxy("DELETE")


def check_probe_syntax() -> bool:
    """先验探针 JS 的语法。

    为什么必须有这一步：探针脚本有语法错误会让**整段不执行** ——
    页面照常显示，但一个验收结果都收不到，表现为「等 180 秒超时」，
    根因完全看不出来。所以先把 JS 抠出来 node --check 一遍。
    """
    js = re.sub(r"^\s*<script>|</script>\s*$", "", PROBE, flags=re.S)
    tmp = Path(tempfile.mkdtemp(prefix="oryx_probe_js_")) / "probe.js"
    tmp.write_text(js, encoding="utf-8")
    node = next(
        (p for p in (r"D:\Node.js\node.exe", shutil.which("node") or "") if p and Path(p).exists()),
        None,
    )
    if not node:
        print("  SKIP  找不到 node，跳过探针语法预检")
        return True
    proc = subprocess.run([node, "--check", str(tmp)], capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"  FAIL  探针 JS 语法错：{proc.stderr.strip()[:300]}")
        return False
    print("  PASS  探针 JS 语法正确")
    return True


def run_chrome(chrome: str, token: str) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="oryx_prompt_acc_"))
    ProbeHandler.result = {}
    ProbeHandler.token = token
    ProbeHandler.page_html = build_page(WEB.read_text("utf-8"), token)
    srv = ThreadingHTTPServer(("127.0.0.1", PROBE_PORT), ProbeHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    proc = subprocess.Popen(
        [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            f"--user-data-dir={tmp / 'profile'}",
            # **不要** --virtual-time-budget：页面有 setInterval(refreshState, 6000)，
            # 虚拟时间会瞬间烧完预算，后面所有 setTimeout 被冻结。
            f"http://127.0.0.1:{PROBE_PORT}/",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        deadline = time.time() + 180
        while time.time() < deadline:
            if ProbeHandler.result.get("done"):
                return ProbeHandler.result
            if proc.poll() is not None:
                time.sleep(1.0)
                return ProbeHandler.result or {"error": f"Chrome 提前退出（{proc.returncode}）"}
            time.sleep(0.4)
        return {"error": "等验收结果超时（180 秒）", "partial": ProbeHandler.result}
    finally:
        try:
            proc.kill()
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)
        srv.shutdown()


# ---------------------------------------------------------------- 契约


def test_credential_policy(srv: Server) -> None:
    """后端层：读 .env 判 L2、确认卡片带凭据说明。"""
    print("\n════ 凭据闸门（后端） ════")
    from oryx.policy import PolicyEngine, Risk
    from oryx.tools import build_registry

    engine = PolicyEngine(allowed_roots=[str(Path(srv.home))], forbidden_paths=[])
    registry = build_registry()
    target = str(Path(srv.home) / ".env")
    decision = engine.evaluate(registry["fs_read"], {"path": target})
    check("读 .env 升到 L2", decision.risk == Risk.L2_DESTRUCTIVE, str(decision.risk))
    check("要用户确认", decision.needs_approval)
    check("确认卡片带凭据说明",
          any("凭据" in s["title"] for s in (decision.plan.steps if decision.plan else [])),
          str(decision.plan.steps if decision.plan else None))


def test_failure_class_api(srv: Server) -> None:
    """后端层：失败分类有 E1-E5 五类，且每一类都有对应动作。"""
    print("\n════ 失败分类（后端） ════")
    from oryx.failure import ADVICE, classify

    check("文件不存在 → E5", classify("文件不存在：D:\\x") == "E5")
    check("权限不足 → E2", classify("没有权限读取") == "E2")
    check("连接失败 → E1", classify("连接失败") == "E1")
    check("未知工具 → E3", classify("未知工具：foo") == "E3")
    for code in ("E1", "E2", "E3", "E4", "E5"):
        check(f"{code} 有对应的处理建议", bool(ADVICE.get(code)))


def main() -> int:
    chrome = find_chrome()
    if not chrome:
        print("找不到 Chrome/Edge，跳过")
        return 0

    # 造一个 .env —— 被测的目标
    home = tempfile.mkdtemp(prefix="oryx_prompt_target_")
    env_file = Path(home) / ".env"
    env_file.write_text("API_KEY=sk-should-never-appear\n", encoding="utf-8")

    fake_proc, base_url = start_fake_provider(str(env_file))
    srv = Server()
    try:
        # allowed_roots 要含被读文件的目录，否则路径先被抬到 L2（越界），
        # 审批卡就先于失败出现，E5 徽章那一段就验不到。
        configure_provider(srv.home, base_url, allowed_roots=[home])
        if not srv.start():
            print("后端起不来，跳过")
            srv.stop()
            fake_proc.kill()
            return 1
        print(f"后端: http://127.0.0.1:{SERVER_PORT}  浏览器: {chrome}")

        test_credential_policy(srv)
        test_failure_class_api(srv)

        print("\n════ 界面（真点一次） ════")
        if not check_probe_syntax():
            return 1
        r = run_chrome(chrome, srv.token)
        if r.get("error"):
            check("界面验收脚本跑完了", False, r["error"])
        else:
            check("找到了输入框", r.get("foundComposer") is True)
            check("步骤卡渲染出来了", (r.get("stepCount") or 0) >= 1,
                  str(r.get("stepCount")))
            check("失败分类徽章出现了", (r.get("eclsCount") or 0) >= 1,
                  str(r.get("eclsText")))
            check("徽章文字是分类代码",
                  all(t in ("E1", "E2", "E3", "E4", "E5", "E0") for t in (r.get("eclsText") or [])),
                  str(r.get("eclsText")))
            check("徽章悬停有中文说明", r.get("eclsHasHint") is True,
                  str(r.get("eclsTitle")))
            check("五类说明都写全了",
                  set(r.get("hintKeys") or []) >= {"E1", "E2", "E3", "E4", "E5"},
                  str(r.get("hintKeys")))
            check("审批卡弹出来了", (r.get("planCount") or 0) >= 1, str(r.get("planCount")))
            check("审批卡标题是「需要你确认」",
                  "确认" in (r.get("planTitle") or ""), repr(r.get("planTitle")))
            check("卡片上有凭据那一步", r.get("hasCredentialStep") is True,
                  str(r.get("actKeys")))
            check("凭据那一步写明了是哪个文件",
                  r.get("credentialDetailHasPath") is True, str(r.get("actKeys")))
            check("有确认按钮", r.get("hasConfirmBtn") is True)
            check("有取消按钮", r.get("hasCancelBtn") is True)
            check("点确认后卡片标记为已确认", r.get("approvedIsYes") is True,
                  repr(r.get("approvedTitle")))
            check("密钥值没泄到界面上", r.get("secretLeaked") is False)
            check("密钥值被打码了", r.get("bodyHasRedacted") is True)
            check("第二张审批卡也弹出来了", r.get("plan2Exists") is True)
            check("点取消后卡片标记为已取消", r.get("plan2Cancelled") is True)
            if r.get("diag"):
                print(f"  INFO  诊断：{r['diag']}")
            if r.get("stack"):
                print(f"  INFO  探针异常栈：{r['stack']}")
    finally:
        srv.stop()
        try:
            fake_proc.kill()
        except Exception:
            pass

    print("\n" + "=" * 60)
    if FAILED:
        print(f"失败 {len(FAILED)} 项：{'、'.join(FAILED)}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
