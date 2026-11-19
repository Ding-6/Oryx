"""「停止」竞态的界面验收。

**真点一次**，不只查代码位置。

背景：前端的「发送」和「停止」是**两条独立的 HTTP 请求**，可以并发。
用户点「发送」后立刻点「停止」，停止请求完全可能先到：

  · 先到内核 → 内核还没登记任务，cancel() 认为「没有正在跑的任务」，丢掉
    → 症状：按了停止，任务照跑到底
  · 先到控制面 → 控制面还不知道有发起在路上
    → 症状同上

这条脚本验的是「点了停止，任务真的停了」：
  1. 点发送 → 立刻点停止（两条请求并发发出）
  2. 界面上出现「（已停止）」，任务状态是 cancelled
  3. 后端库里那个任务的状态也是 cancelled（不是 done）

做法：起真实 server（隔离 ORYX_HOME）→ 同源代理 → headless Chrome 跑探针。
**不依赖真模型** —— 用一个永远「不发工具调用、只吐字」的假 provider，
这样任务会一直跑下去，正好给「点停止」留出时间窗口。

运行：python -m tests.verify_ui_cancel_race
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


# ---------------------------------------------------------------- 假 provider

# 一直吐字、不发工具调用 —— 任务会一直跑下去，给「点停止」留出窗口。
# 每轮流式给一点，好让界面有东西显示（证明任务真的在跑）。
FAKE_PROVIDER = r'''
"""假 provider：每轮流式吐一点字，永不结束。

为什么要它：验收「点了停止，任务真的停了」。
如果模型秒回，任务在用户点停止之前就结束了，验的就不是停止功能。
所以这里让它一直说下去 —— 只有「停止」能把这一轮收掉。
"""
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {"calls": 0}


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
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        # 一次给 40 段，每段之间 sleep —— 整个响应要持续好几秒
        for i in range(40):
            frame = {"choices": [{"delta": {"content": " thinking %d " % i},
                                  "index": 0, "finish_reason": None}]}
            self.wfile.write(("data: " + json.dumps(frame) + "\n\n").encode())
            self.wfile.flush()
            time.sleep(0.25)
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def do_GET(self):
        self.send_error(404)


srv = ThreadingHTTPServer(("127.0.0.1", {port}), Handler)
print("READY", flush=True)
srv.serve_forever()
'''


def start_fake_provider() -> tuple[subprocess.Popen, str]:
    """起假 provider，返回 (进程, base_url)。

    端口**动态挑**：写死端口有个阴险的失败模式 ——
    上一次调试跑剩的假 provider 还占着那个端口，新进程绑定失败、
    stdout 里读不到 READY，于是脚本**照样返回**，只是 base_url 指向
    那个旧进程。旧进程的调用早就走完了，答回来是一段纯文本，
    验收脚本表现为「界面什么都没有」—— 根因离症状十万八千里。

    所以：让内核挑一个空闲端口；再核对 READY 确实来自**这个**进程
    （读不到就报错，别糊过去）。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    # 用 replace 不用 format —— 假 provider 的代码里有大量 { }（字典字面量），
    # str.format 会把它们当成占位符，直接 KeyError。
    code = FAKE_PROVIDER.replace("{port}", str(port))
    home = tempfile.mkdtemp(prefix="oryx_cancel_provider_")
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


def configure_provider(home: str, base_url: str) -> None:
    """把假 provider 写进隔离的 ORYX_HOME 配置。"""
    cfg_path = Path(home) / "config.json"
    cfg = {}
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text("utf-8"))
        except Exception:
            cfg = {}
    cfg["providers"] = {
        "fake": {
            "name": "fake",
            "base_url": base_url,
            "model": "fake-model",
            "api_key": "sk-fake",
            "kind": "openai",
        }
    }
    cfg["active_provider"] = "fake"
    cfg["local_provider"] = "fake"
    cfg["mode"] = "efficiency"
    cfg["allowed_roots"] = [home]
    cfg["workspace"] = home
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")


# ---------------------------------------------------------------- 真实后端


class Server:
    """起一个真实的 Oryx server（隔离的 ORYX_HOME）。"""

    def __init__(self, port: int = SERVER_PORT) -> None:
        self.port = port
        self.proc: subprocess.Popen | None = None
        self.token = ""
        self.home = tempfile.mkdtemp(prefix="oryx_cancel_home_")

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

    def get(self, path: str):
        url = f"http://127.0.0.1:{self.port}{path}"
        req = urllib.request.Request(url)
        req.add_header("X-Oryx-Token", self.token)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=8)
            except Exception:
                self.proc.kill()


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
    R.sendBtnBefore = !!q('#sendBtn');
    if (ta) {
      ta.value = '讲个长一点的故事';
      ta.dispatchEvent(new Event('input', { bubbles: true }));
      await sleep(300);
      // 点发送
      const btn = q('#sendBtn');
      if (btn) btn.click();
    }

    /* ── 2. 立刻点停止（两条请求并发） ──
       这是整个验收的核心：点完发送**不等**它返回，马上点停止。
       中止请求完全可能先到内核 —— 那时内核还没登记任务，
       以前那一下会被丢掉，任务照跑到底。 */
    await sleep(120);          // 只等界面切到「停止」形态
    const stopBtn = q('#sendBtn');
    R.stopBtnShape = !!(stopBtn && stopBtn.classList.contains('stopping'));
    R.busyBeforeStop = (typeof S !== 'undefined') ? S.busy : null;
    R.sendSeq = (typeof S !== 'undefined') ? S.sendSeq : -1;
    if (stopBtn) stopBtn.click();

    /* ── 3. 等界面出现「（已停止）」 ── */
    const stopped = await waitFor(() =>
      document.body.innerText.includes('（已停止）') ? true : null, 120, 250);
    R.stoppedTextShown = !!stopped;
    R.busyAfter = (typeof S !== 'undefined') ? S.busy : null;
    R.sendStateAfter = (typeof S !== 'undefined') && S.taskId ? S.taskId : '';
    R.diag = {
      lastSeq: typeof lastSeq !== 'undefined' ? lastSeq : -1,
      taskId: (typeof S !== 'undefined' && S.taskId) ? S.taskId : '',
      busy: (typeof S !== 'undefined') ? S.busy : null,
      esState: (typeof es !== 'undefined' && es) ? es.readyState : -1,
      msgs: q('#messages') ? q('#messages').innerText.slice(0, 300) : '',
    };

    /* ── 4. 停止之后界面不该还在转圈 ── */
    await sleep(1500);
    R.busyFinally = (typeof S !== 'undefined') ? S.busy : null;
    R.stoppingClassLeft = !!(q('#sendBtn') && q('#sendBtn').classList.contains('stopping'));
    R.textAfterStop = q('#messages') ? q('#messages').innerText.slice(0, 400) : '';

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
    js = PROBE
    start = js.find("<script>") + len("<script>")
    end = js.rfind("</script>")
    body = js[start:end]
    tmp = Path(tempfile.mkdtemp(prefix="oryx_probe_")) / "probe.js"
    tmp.write_text(body, "utf-8")
    node = shutil.which("node") or r"D:\Node.js\node.exe"
    if not Path(node).exists() and not shutil.which("node"):
        print("  （跳过：找不到 node）")
        return True
    proc = subprocess.run([node, "--check", str(tmp)], capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"  FAIL  探针 JS 语法错：{proc.stderr[:400]}")
        return False
    print("  PASS  探针 JS 语法正确")
    return True


def run_chrome(chrome: str, token: str) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="oryx_cancel_acc_"))
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


def latest_task_status(srv: Server) -> tuple[str, str]:
    """看库里最近动过的那个任务的状态与 id。

    界面上的「（已停止）」可能是前端自己画的 —— 必须回库里核对，
    确认内核真的把它标成了 cancelled（而不是前端骗人）。

    列表按 updated_at DESC 排（最近动过的在最前），所以第一条就是刚才那一轮。
    """
    try:
        payload = srv.get("/api/sessions")
    except Exception as exc:  # noqa: BLE001
        return "", f"读会话列表失败：{exc}"
    items = payload.get("sessions") or []
    if not items:
        return "", "库里一个会话都没有"
    latest = items[0]
    return str(latest.get("id") or ""), str(latest.get("status") or "")


def main() -> int:
    chrome = find_chrome()
    if not chrome:
        print("找不到 Chrome/Edge，跳过")
        return 0

    fake_proc, base_url = start_fake_provider()
    srv = Server()
    try:
        configure_provider(srv.home, base_url)
        if not srv.start():
            print("后端起不来，跳过")
            srv.stop()
            fake_proc.kill()
            return 1
        print(f"后端: http://127.0.0.1:{SERVER_PORT}  浏览器: {chrome}")

        print("\n════ 界面：点发送后立刻点停止 ════")
        if not check_probe_syntax():
            return 1
        r = run_chrome(chrome, srv.token)
        if r.get("error"):
            check("界面验收脚本跑完了", False, r["error"])
            return 1

        check("找到了输入框", r.get("foundComposer") is True)
        check("发送键存在", r.get("sendBtnBefore") is True)
        check("点发送后键变成「停止」形态", r.get("stopBtnShape") is True,
              f"busy={r.get('busyBeforeStop')} diag={r.get('diag')}")
        check("发出去了（序号 +1）", (r.get("sendSeq") or 0) >= 1,
              str(r.get("sendSeq")))
        check("界面出现「（已停止）」", r.get("stoppedTextShown") is True,
              str(r.get("textAfterStop")))
        check("停止后不忙了", r.get("busyFinally") is False,
              f"busy={r.get('busyFinally')}")
        check("停止键形态已复位", r.get("stoppingClassLeft") is False)

        # 回库里核对 —— 界面上的「已停止」可能是前端自己画的
        task_id, status = latest_task_status(srv)
        check("库里那个任务是 cancelled（不是 done）", status == "cancelled",
              f"task={task_id} status={status}")

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
