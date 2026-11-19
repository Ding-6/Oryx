"""检查点回滚 + 会话重命名的界面验收。

**真点一次**，不只查代码位置 ——
前几批的教训：`.navitem.newchat` 的 click 绑定坏了几个批次都没被发现，
因为验收只查了它在导航第一位、是实心块。

这一份要验的：
  1. 用户消息下方**真的有**「回退到这里」按钮（不是只在代码里写了）；
  2. 点它**真的弹**出范围选择弹层；
  3. 选「只退对话」**真的**把后面的消息删掉了（后端 + 界面一起变）；
  4. 左栏会话有改名入口，点它**真的**能改名，改完列表跟着变；
  5. 检查点 seq 递增（回退范围正确性的地基）。

做法：起真实 server（隔离 ORYX_HOME）→ 同源代理 → headless Chrome 跑探针。
造数据靠直接写库 + 走 HTTP 接口，不依赖真模型。

运行：python -m tests.verify_ui_rewind
"""
from __future__ import annotations

import json
import os
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

CORE = Path(__file__).resolve().parents[1]
WEB = CORE / "web" / "index.html"
PYTHON = sys.executable
SERVER_PORT = 8805
PROBE_PORT = 8806
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
        self.home = tempfile.mkdtemp(prefix="oryx_rewind_home_")

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

    def put(self, path: str, body=None):
        return self.raw("PUT", path, body if body is not None else {})

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=8)
            except Exception:
                self.proc.kill()


# ---------------------------------------------------------------- 造数据


def seed_session(srv: Server) -> tuple[str, str]:
    """造一个有两轮对话、两个检查点的会话。

    直接走 HTTP 接口 —— /api/chat 会真调模型，没有模型就发不出去。
    所以这里直接写库：store 在同进程的 conv 库里，
    字段与真实运行时（loop.run）完全一致。

    **顺序要紧**：检查点打在**用户提问之前**（loop.run 里的顺序），
    所以 msg_count = 打点时库里已有的消息数 = 这条提问自己的序号。
    先加消息后打点会让 msg_count 偏移 1 —— 那就验不出「退对位置」了。

    消息序列：
        0: user 「第一轮提问」   ← cp1 打在这条之前（msg_count=0）
        1: assistant 「第一轮回答」
        2: user 「第二轮提问」   ← cp2 打在这条之前（msg_count=2）
        3: assistant 「第二轮回答」

    回退到 cp2（只退对话）应保留 0、1，删掉 2、3。

    返回 (task_id, checkpoint_id)。
    """
    from oryx.store import Store

    db = Path(srv.home) / "oryx.db"
    store = Store(db)
    task = store.create_task("第一轮提问", "craft", "test")
    # 第一轮：先打点，再写提问
    cp1 = store.create_checkpoint(task, 0, "第一轮")
    store.add_message(task, "user", "第一轮提问")
    store.add_message(task, "assistant", "第一轮回答")
    # 第二轮：先打点，再写提问
    cp2 = store.create_checkpoint(task, 0, "第二轮")
    store.add_message(task, "user", "第二轮提问")
    store.add_message(task, "assistant", "第二轮回答")
    return task, cp2


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

    /* ── 打开造好的会话 ── */
    const taskId = window.__ACC_TASK__;
    await openSession(taskId);
    await sleep(1200);

    /* ── 1. 用户消息下方有「回退到这里」按钮 ── */
    const userMsgs = qa('#messages .msg.user');
    R.userMsgCount = userMsgs.length;
    const rbtns = qa('#messages .msg.user .rewindbar [data-rewind]');
    R.rewindBtnCount = rbtns.length;
    R.rewindBtnText = rbtns.length ? rbtns[0].textContent.trim() : '';
    R.rewindInUserMsg = rbtns.length ? !!rbtns[0].closest('.msg.user') : false;

    /* ── 2. 检查点被拉下来了 ── */
    R.cpLoaded = (S.checkpoints || []).length;
    R.cpMsgCounts = (S.checkpoints || []).map(c => c.msg_count);
    R.cpSeqs = (S.checkpoints || []).map(c => c.seq);

    /* ── 3. 真点「回退到这里」→ 应该弹范围选择 ── */
    // 点第二条提问下面那个（它的 msg_seq 应该是 2，对应 msg_count=2 的检查点）
    const second = userMsgs[1];
    let target = null;
    if (second) target = second.querySelector('[data-rewind]');
    R.clickedSeq = target ? target.dataset.rewind : '';
    R.msgSeqs = userMsgs.map(el => el.dataset.msgSeq);
    if (target) {
      target.click();
      await sleep(700);
    }
    R.modalOpen = q('#mask').classList.contains('on');
    R.modalTitle = q('#modal .mhead h3') ? q('#modal .mhead h3').textContent.trim() : '';
    const scopes = qa('#modal [data-scope]');
    R.scopeCount = scopes.length;
    R.scopeIds = scopes.map(b => b.dataset.scope);

    /* ── 4. 真点「只退对话」── */
    const convBtn = scopes.find(b => b.dataset.scope === 'Conversation');
    R.hasConvBtn = !!convBtn;
    if (convBtn) {
      convBtn.click();
      await sleep(2500);
    }
    R.modalClosedAfter = !q('#mask').classList.contains('on');

    /* ── 5. 后端真的删掉了后续消息 ── */
    try {
      const r = await fetch('/api/sessions/' + encodeURIComponent(taskId));
      const d = await r.json();
      R.msgCountAfter = (d.messages || []).length;
      R.msgContentsAfter = (d.messages || []).map(m => m.content);
    } catch (e) { R.fetchErr = String(e); }

    /* ── 6. 界面上的消息条数也跟着变了 ── */
    R.uiUserMsgsAfter = qa('#messages .msg.user').length;

    /* ── 7. 左栏改名入口 ── */
    await loadSessions();
    await sleep(800);
    const renBtns = qa('#sessList [data-ren]');
    R.renameBtnCount = renBtns.length;
    // 找到造出来的那个会话那一行
    let row = null;
    for (const el of qa('#sessList .sess')) {
      if (el.dataset.id === taskId) { row = el; break; }
    }
    R.foundRow = !!row;
    const ren = row ? row.querySelector('[data-ren]') : null;
    R.hasRenameInRow = !!ren;
    if (ren) {
      ren.click();
      await sleep(700);
      R.renameModalOpen = q('#mask').classList.contains('on');
      const inp = q('#rnTitle');
      R.renameInputExists = !!inp;
      R.renameInputValue = inp ? inp.value : '';
      if (inp) {
        inp.value = '验收改的名字';
        const ok = q('#rnOk');
        R.renameOkBtn = !!ok;
        if (ok) {
          ok.click();
          await sleep(1800);
        }
      }
      R.renameModalClosed = !q('#mask').classList.contains('on');
    }
    /* ── 8. 列表里的名字真的变了 ── */
    await loadSessions();
    await sleep(600);
    const row2 = qa('#sessList .sess').find(el => el.dataset.id === taskId);
    R.listTitleAfter = row2 ? row2.querySelector('.tx').textContent.trim() : '(找不到这一行)';
    R.pageTitleAfter = q('#pageTitle') ? q('#pageTitle').textContent.trim() : '';

    /* ── 9. 右栏详情页有检查点时间线 ── */
    setView('chat');
    await renderDetail();
    await sleep(800);
    R.cpTimelineItems = qa('#p-detail .cpitem').length;

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
    return (
        src.replace("__ORYX_TOKEN__", token)
        .replace("</body>", inject + PROBE + "</body>")
    )


class ProbeHandler(BaseHTTPRequestHandler):
    page_html = ""
    token = ""
    result: dict = {}

    def log_message(self, *a):
        pass

    def _proxy(self, method: str) -> None:
        url = f"http://127.0.0.1:{SERVER_PORT}{self.path}"
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

    def do_DELETE(self) -> None:
        self._proxy("DELETE")


def run_chrome(chrome: str, token: str, task_id: str) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="oryx_rewind_acc_"))
    ProbeHandler.result = {}
    ProbeHandler.token = token
    ProbeHandler.page_html = build_page(WEB.read_text("utf-8"), token, task_id)
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


def test_checkpoint_api(srv: Server, task_id: str) -> None:
    """HTTP 层：检查点列表 + seq 递增 + 非法 scope 被拒。"""
    print("\n════ 检查点接口 ════")
    d = srv.get(f"/api/checkpoints/{task_id}")
    cps = d.get("checkpoints") or []
    check("列出了检查点", len(cps) == 2, str(cps))
    seqs = [c.get("seq") for c in cps]
    check("seq 递增", seqs == sorted(seqs) and len(set(seqs)) == 2, str(seqs))
    counts = [c.get("msg_count") for c in cps]
    check("msg_count 递增", counts == sorted(counts), str(counts))

    try:
        srv.post(f"/api/checkpoints/{cps[0]['id']}/rewind", {"scope": "Bogus"})
        ok = False
    except urllib.error.HTTPError as exc:
        ok = exc.code == 400
    check("非法 scope 被拒（400）", ok)

    try:
        srv.post("/api/checkpoints/nope/rewind", {"scope": "Code"})
        ok = False
    except urllib.error.HTTPError as exc:
        ok = exc.code == 404
    check("不存在的检查点被拒（404）", ok)


def test_rename_api(srv: Server, task_id: str) -> None:
    """HTTP 层：改名 + 清空名字退回默认。"""
    print("\n════ 改名接口 ════")
    r = srv.put(f"/api/sessions/{task_id}", {"title": "接口改的名字"})
    check("改名成功", r.get("renamed") == task_id, str(r))
    listed = srv.get("/api/sessions")["sessions"]
    item = next((s for s in listed if s["id"] == task_id), None)
    check("列表里的名字变了", item and item.get("title") == "接口改的名字", str(item))
    check("标了 renamed", item and item.get("renamed") is True, str(item))

    srv.put(f"/api/sessions/{task_id}", {"title": ""})
    listed = srv.get("/api/sessions")["sessions"]
    item = next((s for s in listed if s["id"] == task_id), None)
    check("清空名字退回默认标题", item and item.get("title") == "第一轮提问", str(item))


def main() -> int:
    chrome = find_chrome()
    if not chrome:
        print("找不到 Chrome/Edge，跳过")
        return 0

    srv = Server()
    if not srv.start():
        print("后端起不来，跳过")
        srv.stop()
        return 1
    print(f"后端: http://127.0.0.1:{SERVER_PORT}  浏览器: {chrome}")

    try:
        task_id, _ = seed_session(srv)
        print(f"造好的会话: {task_id}")

        test_checkpoint_api(srv, task_id)
        test_rename_api(srv, task_id)

        print("\n════ 界面（真点一次） ════")
        r = run_chrome(chrome, srv.token, task_id)
        if r.get("error"):
            check("界面验收脚本跑完了", False, r["error"])
        else:
            check("会话里有两条提问", r.get("userMsgCount") == 2, str(r.get("userMsgCount")))
            check("每条提问下都有「回退到这里」按钮", r.get("rewindBtnCount") == 2,
                  str(r.get("rewindBtnCount")))
            check("按钮文案对", "回退" in (r.get("rewindBtnText") or ""),
                  repr(r.get("rewindBtnText")))
            check("按钮在用户消息里", r.get("rewindInUserMsg") is True)
            check("检查点被拉下来了", (r.get("cpLoaded") or 0) == 2, str(r.get("cpLoaded")))
            check("检查点 msg_count 递增",
                  (r.get("cpMsgCounts") or []) == sorted(r.get("cpMsgCounts") or []),
                  str(r.get("cpMsgCounts")))
            # 序号口径要对：界面上的 msg_seq 跟检查点的 msg_count 必须能对上，
            # 否则「回退到这里」会退到别的位置去（只数用户消息就会错位）
            check("按钮序号与检查点 msg_count 对得上",
                  str(r.get("clickedSeq")) == str((r.get("cpMsgCounts") or [])[-1]),
                  f"按钮序号 {r.get('clickedSeq')} / 检查点 msg_count {r.get('cpMsgCounts')}")
            check("检查点 seq 递增",
                  (r.get("cpSeqs") or []) == sorted(r.get("cpSeqs") or []),
                  str(r.get("cpSeqs")))
            check("点了「回退到这里」弹出了范围选择", r.get("modalOpen") is True,
                  f"弹层标题：{r.get('modalTitle')!r}")
            check("范围选择有三个选项", r.get("scopeCount") == 3,
                  str(r.get("scopeIds")))
            check("有「只退对话」这个选项",
                  "Conversation" in (r.get("scopeIds") or []), str(r.get("scopeIds")))
            check("点完弹层关掉了", r.get("modalClosedAfter") is True)
            check("后端真的删掉了后续消息", r.get("msgCountAfter") == 2,
                  f"剩 {r.get('msgCountAfter')} 条：{r.get('msgContentsAfter')}")
            check("界面上的提问条数也跟着变了", r.get("uiUserMsgsAfter") == 1,
                  str(r.get("uiUserMsgsAfter")))
            check("左栏有改名入口", (r.get("renameBtnCount") or 0) >= 1,
                  str(r.get("renameBtnCount")))
            check("改名按钮在那一行里", r.get("hasRenameInRow") is True)
            check("点改名弹出了输入框", r.get("renameInputExists") is True)
            check("输入框带上了原名", (r.get("renameInputValue") or "") != "",
                  repr(r.get("renameInputValue")))
            check("改名后弹层关掉了", r.get("renameModalClosed") is True)
            check("列表里的名字真的改了", r.get("listTitleAfter") == "验收改的名字",
                  repr(r.get("listTitleAfter")))
            check("顶栏也跟着改了", r.get("pageTitleAfter") == "验收改的名字",
                  repr(r.get("pageTitleAfter")))
            check("右栏详情页有检查点时间线", (r.get("cpTimelineItems") or 0) >= 1,
                  str(r.get("cpTimelineItems")))
            if r.get("stack"):
                print(f"  INFO  探针异常栈：{r['stack']}")
    finally:
        srv.stop()

    print("\n" + "=" * 60)
    if FAILED:
        print(f"失败 {len(FAILED)} 项：{'、'.join(FAILED)}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
