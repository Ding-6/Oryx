r"""安装版验收：跑在 D:\Oryx 的真后端 + 真前端上。

为什么要单独一份：仓库里的验收跑的是**源码**（`d:\项目\Oryx\core`），
而用户双击的是**装好的那份**（`D:\Oryx`）。两边的 core/web 与 exe 是
分别同步的 —— 同步漏了的话，仓库里全绿、装好的却还是旧版。

这一份直接连已经起在 8765 的安装版后端，页面也取 `D:\Oryx\core\web\index.html`，
所以验的就是用户真正会用到的那份。

前提：安装版后端已经在 8765 上跑着（本脚本不自己起）。

运行：python -m tests.verify_ui_installed
"""
from __future__ import annotations

import json
import re
import shutil
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

BACKEND = "http://127.0.0.1:8765"
INSTALLED_WEB = Path(r"D:\Oryx\core\web\index.html")
PROBE_PORT = 8807
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


def get_token() -> str:
    with urllib.request.urlopen(f"{BACKEND}/", timeout=20) as resp:
        html = resp.read().decode("utf-8")
    m = re.search(r'const ORYX_TOKEN = "([^"]*)"', html)
    return m.group(1) if m else ""


def api(method: str, path: str, body=None):
    tok = get_token()
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BACKEND + path, data=data, method=method)
    req.add_header("X-Oryx-Token", tok)
    req.add_header("X-Oryx-Request", "1")
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


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
  try {
    await sleep(1500);
    const taskId = window.__ACC_TASK__;
    await openSession(taskId);
    await sleep(1200);

    const userMsgs = qa('#messages .msg.user');
    R.userMsgCount = userMsgs.length;
    const rbtns = qa('#messages .msg.user .rewindbar [data-rewind]');
    R.rewindBtnCount = rbtns.length;
    R.rewindBtnText = rbtns.length ? rbtns[0].textContent.trim() : '';
    R.cpLoaded = (S.checkpoints || []).length;
    R.cpMsgCounts = (S.checkpoints || []).map(c => c.msg_count);

    /* 真点「回退到这里」 */
    const second = userMsgs[1];
    const target = second ? second.querySelector('[data-rewind]') : null;
    R.clickedSeq = target ? target.dataset.rewind : '';
    if (target) { target.click(); await sleep(700); }
    R.modalOpen = q('#mask').classList.contains('on');
    const scopes = qa('#modal [data-scope]');
    R.scopeIds = scopes.map(b => b.dataset.scope);
    const convBtn = scopes.find(b => b.dataset.scope === 'Conversation');
    if (convBtn) { convBtn.click(); await sleep(2500); }

    try {
      const r = await fetch('/api/sessions/' + encodeURIComponent(taskId));
      const d = await r.json();
      R.msgCountAfter = (d.messages || []).length;
    } catch (e) { R.fetchErr = String(e); }
    R.uiUserMsgsAfter = qa('#messages .msg.user').length;

    /* 改名 */
    await loadSessions();
    await sleep(800);
    const row = qa('#sessList .sess').find(el => el.dataset.id === taskId);
    const ren = row ? row.querySelector('[data-ren]') : null;
    R.hasRenameInRow = !!ren;
    if (ren) {
      ren.click();
      await sleep(700);
      const inp = q('#rnTitle');
      if (inp) {
        inp.value = '安装版改名';
        const ok = q('#rnOk');
        if (ok) { ok.click(); await sleep(1800); }
      }
    }
    await loadSessions();
    await sleep(600);
    const row2 = qa('#sessList .sess').find(el => el.dataset.id === taskId);
    R.listTitleAfter = row2 ? row2.querySelector('.tx').textContent.trim() : '(找不到)';

    /* 工具清单里有没有新工具 */
    try {
      const caps = await (await fetch('/api/capabilities')).json();
      const names = (caps.groups || []).flatMap(g => (g.tools || []).map(t => t.name));
      R.hasFsMove = names.includes('fs_move');
      R.hasFsGlob = names.includes('fs_glob');
      R.hasFsGrep = names.includes('fs_grep');
      R.totalTools = caps.total;
    } catch (e) { R.capsErr = String(e); }

    R.done = true;
    await post(R);
  } catch (e) {
    R.error = String((e && e.message) || e);
    R.stack = String((e && e.stack) || '').slice(0, 400);
    R.done = true;
    await post(R);
  }
})();
</script>
"""


def build_page(src: str, token: str, task_id: str) -> str:
    inject = f"<script>window.__ACC_TASK__ = {json.dumps(task_id)};</script>\n"
    return src.replace("__ORYX_TOKEN__", token).replace("</body>", inject + PROBE + "</body>")


class ProbeHandler(BaseHTTPRequestHandler):
    page_html = ""
    token = ""
    result: dict = {}

    def log_message(self, *a):
        pass

    def _proxy(self, method: str) -> None:
        url = f"{BACKEND}{self.path}"
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
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
            if data.get("done"):
                ProbeHandler.result["done"] = True
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._proxy("POST")

    def do_PUT(self) -> None:
        self._proxy("PUT")


def run_chrome(chrome: str, token: str, task_id: str) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="oryx_installed_acc_"))
    ProbeHandler.result = {}
    ProbeHandler.token = token
    ProbeHandler.page_html = build_page(INSTALLED_WEB.read_text("utf-8"), token, task_id)
    srv = ThreadingHTTPServer(("127.0.0.1", PROBE_PORT), ProbeHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    proc = subprocess.Popen(
        [
            chrome, "--headless=new", "--disable-gpu", "--no-first-run",
            "--no-default-browser-check",
            f"--user-data-dir={tmp / 'profile'}",
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


def seed():
    """在安装版的数据目录里造一个带两个检查点的会话。"""
    from oryx.store import Store

    store = Store(Path(r"D:\OryxData\data\oryx.db"))
    task = store.create_task("安装版验收会话", "craft", "test")
    store.create_checkpoint(task, 0, "第一轮")
    store.add_message(task, "user", "第一轮提问")
    store.add_message(task, "assistant", "第一轮回答")
    store.create_checkpoint(task, 0, "第二轮")
    store.add_message(task, "user", "第二轮提问")
    store.add_message(task, "assistant", "第二轮回答")
    return task, store


def main() -> int:
    chrome = find_chrome()
    if not chrome:
        print("找不到 Chrome/Edge，跳过")
        return 0
    if not INSTALLED_WEB.exists():
        print(f"安装版页面不存在：{INSTALLED_WEB}")
        return 1

    try:
        token = get_token()
    except Exception as exc:  # noqa: BLE001
        print(f"安装版后端连不上（{BACKEND}）：{exc}")
        print("先起安装版：python -m oryx --no-browser --port 8765（D:\\Oryx\\core 下）")
        return 1
    if not token:
        print("拿不到令牌")
        return 1
    print(f"安装版后端: {BACKEND}  浏览器: {chrome}")
    print(f"安装版页面: {INSTALLED_WEB}")

    task, store = seed()
    print(f"造好的会话: {task}")

    try:
        r = run_chrome(chrome, token, task)
        print("\n════ 安装版界面（真点一次） ════")
        if r.get("error"):
            check("界面验收脚本跑完了", False, r["error"])
        else:
            check("会话里有两条提问", r.get("userMsgCount") == 2, str(r.get("userMsgCount")))
            check("每条提问下都有「回退到这里」按钮", r.get("rewindBtnCount") == 2,
                  str(r.get("rewindBtnCount")))
            check("检查点被拉下来了", (r.get("cpLoaded") or 0) == 2, str(r.get("cpLoaded")))
            check("点了弹出范围选择", r.get("modalOpen") is True)
            check("范围有三种", len(r.get("scopeIds") or []) == 3, str(r.get("scopeIds")))
            check("后端真的删掉了后续消息", r.get("msgCountAfter") == 2,
                  f"剩 {r.get('msgCountAfter')} 条")
            check("界面上的提问条数也跟着变了", r.get("uiUserMsgsAfter") == 1,
                  str(r.get("uiUserMsgsAfter")))
            check("左栏有改名入口", r.get("hasRenameInRow") is True)
            check("改名真的生效了", r.get("listTitleAfter") == "安装版改名",
                  repr(r.get("listTitleAfter")))
            check("fs_move 已注册", r.get("hasFsMove") is True)
            check("fs_glob 已注册", r.get("hasFsGlob") is True)
            check("fs_grep 已注册", r.get("hasFsGrep") is True)
            print(f"  INFO  安装版工具总数：{r.get('totalTools')}")
            if r.get("stack"):
                print(f"  INFO  探针异常栈：{r['stack']}")
    finally:
        try:
            store.delete_session(task)
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
