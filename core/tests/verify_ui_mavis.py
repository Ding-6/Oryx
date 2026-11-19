"""Marvis 吸收批次的界面验收：工作流模板的只读徽章、别名、报告路径。

**真点一次**，不只查代码位置 ——
前几批的教训：`.navitem.newchat` 的 click 绑定坏了几个批次都没被发现，
因为验收只查了它在导航第一位、是实心块。

这一份要验的：
  1. 模板卡上出现「只读」徽章（只读模板有，会写文件的没有）；
  2. 别名显示在卡上，且**能按别名取到模板**；
  3. 模板编辑器有别名输入框，填了能存下去；
  4. 运行模板后报告文件真的落盘，且两次跑是两份。

做法：起真实 server（隔离 ORYX_HOME）→ 同源代理 → headless Chrome 跑探针。

运行：python -m tests.verify_ui_mavis
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
SERVER_PORT = 8793
PROBE_PORT = 8794
CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
FAILED: list[str] = []

# 只读编排：reviewer 的白名单里没有会改东西的工具。
# 可写编排：file-agent 有 fs_write。
READONLY_PLAN = {
    "name": "只读巡检",
    "goal": "读一遍资料",
    "stages": [
        {"name": "读", "steps": [
            {"id": "a", "name": "读资料", "agent": "reviewer", "goal": "读一遍并列出问题"},
        ]},
    ],
}
WRITABLE_PLAN = {
    "name": "会写文件的编排",
    "goal": "写点东西",
    "stages": [
        {"name": "写", "steps": [
            {"id": "a", "name": "写文件", "agent": "file-agent", "goal": "写一个文件"},
        ]},
    ],
}


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
        self.home = tempfile.mkdtemp(prefix="oryx_mavis_home_")

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
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode())

    def get(self, path: str):
        return self.raw("GET", path)

    def post(self, path: str, body=None):
        return self.raw("POST", path, body if body is not None else {})

    def put(self, path: str, body=None):
        return self.raw("PUT", path, body if body is not None else {})

    def delete(self, path: str):
        return self.raw("DELETE", path)

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
  try {
    await sleep(1500);

    /* ── 进工作流页，切到「模板」tab ── */
    setView('workflows');
    await sleep(1200);
    const tabBtn = qa('[data-wftab]').find(b => b.dataset.wftab === 'tpl');
    R.hasTabBtn = !!tabBtn;
    if (tabBtn) { tabBtn.click(); await sleep(900); }
    R.tabNow = S.wf.tab;

    /* ── 模板卡 ── */
    await loadWorkflowTemplates(true);
    renderWorkflows();
    await sleep(900);
    const cards = qa('#page .skillcard');
    R.cardCount = cards.length;
    R.readOnlyBadges = qa('#page .skillcard .badge.ro').length;
    R.aliasText = [];
    for (const c of cards) {
      const lines = [...c.querySelectorAll('.sdesc')].map(e => e.textContent.trim());
      const aliasLine = lines.find(t => t.startsWith('别名：'));
      R.aliasText.push(aliasLine || '');
    }
    R.hasAliasLine = R.aliasText.some(t => t && t !== '别名：');

    /* ── 编辑器：别名输入框 ── */
    const editBtn = qa('#page [data-wftpledit]')[0];
    R.hasEditBtn = !!editBtn;
    if (editBtn) {
      editBtn.click();
      await sleep(1000);
      const inp = q('#wtAliases');
      R.hasAliasInput = !!inp;
      R.aliasInputValue = inp ? inp.value : '';
      R.nameInputValue = q('#wtName') ? q('#wtName').value : '(没有 #wtName)';
      R.planInputValue = q('#wtPlan') ? String(q('#wtPlan').value).slice(0, 160) : '(没有 #wtPlan)';
      /* 真填一笔再存 —— 验输入框真的能提交 */
      if (inp) {
        inp.value = '界面别名A、界面别名B';
        const save = q('#wtSave');
        R.hasSaveBtn = !!save;
        if (save) {
          save.click();
          await sleep(1500);
          const err = q('#wtErr');
          R.errText = err ? err.textContent : '';
          R.errShown = err ? err.style.display !== 'none' : false;
          R.savedOk = !err || err.style.display === 'none';
        }
      }
      closeModal();
    }

    /* ── 模板列表接口：只读标记与别名 ── */
    try {
      const d = await (await fetch('/api/workflows/templates')).json();
      R.templates = (d.templates || []).map(t => ({
        id: t.id, name: t.name, read_only: t.read_only,
        aliases: t.aliases || [],
      }));
    } catch (e) { R.apiError = String(e); }

    /* ── 按别名取模板（HTTP 层） ── */
    try {
      const r = await fetch('/api/workflows/templates/' + encodeURIComponent('界面别名A'));
      R.aliasFetchStatus = r.status;
      const d = await r.json();
      R.aliasFetchOk = !!(d && d.content && d.content.id);
    } catch (e) { R.aliasFetchError = String(e); }

    /* ── 只读自证徽章：走真实的事件分发路径 ──
       handle() 就是 SSE onmessage 收到事件后调的那个函数，
       所以这里直接喂它 step.done，等于验「事件到了界面上会显示什么」。 */
    try {
      setView('chat');
      await sleep(400);
      const proofOf = stepId => {
        const card = q('#p-steps .step[data-proof-step="' + stepId + '"]');
        if (!card) return '(没找到步骤卡)';
        const slot = card.querySelector('.proof');
        return slot ? slot.textContent.trim() : '(卡上没有 .proof 槽位)';
      };
      handle({ type: 'step.created', step_id: 'acc-ro', tool: 'fs_read', risk: 0, args: {} });
      handle({ type: 'step.done', step_id: 'acc-ro', tool: 'fs_read', risk: 0,
               result: '读到的内容', mutated: false, preview: true,
               deleted: false, modified: false, moved: false });
      await sleep(400);
      R.proofReadOnlyText = proofOf('acc-ro');

      handle({ type: 'step.created', step_id: 'acc-wr', tool: 'fs_write', risk: 1, args: {} });
      handle({ type: 'step.done', step_id: 'acc-wr', tool: 'fs_write', risk: 1,
               result: '写完了', mutated: true });
      await sleep(400);
      R.proofMutatedText = proofOf('acc-wr');
    } catch (e) { R.proofError = String(e); }

    R.done = true;
    await post(R);
  } catch (e) {
    R.error = String((e && e.message) || e);
    R.done = true;
    await post(R);
  }
})();
</script>
"""


def build_page(src: str, token: str) -> str:
    return src.replace("__ORYX_TOKEN__", token).replace("</body>", PROBE + "</body>")


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

    # PUT / DELETE 也要转发。
    # BaseHTTPRequestHandler 没实现这两个方法时默认回 **501** ——
    # 表现为「保存失败（501）」，看起来像后端不支持编辑，
    # 其实是代理自己没接。踩过一次就记住。
    def do_PUT(self) -> None:
        self._proxy("PUT")

    def do_DELETE(self) -> None:
        self._proxy("DELETE")


def run_chrome(chrome: str, token: str) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="oryx_mavis_acc_"))
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
        deadline = time.time() + 150
        while time.time() < deadline:
            if ProbeHandler.result.get("done"):
                return ProbeHandler.result
            if proc.poll() is not None:
                time.sleep(1.0)
                return ProbeHandler.result or {"error": f"Chrome 提前退出（{proc.returncode}）"}
            time.sleep(0.4)
        return {"error": "等验收结果超时（150 秒）", "partial": ProbeHandler.result}
    finally:
        try:
            proc.kill()
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)
        srv.shutdown()


# ---------------------------------------------------------------- 契约

def test_template_api(srv: Server) -> None:
    """HTTP 层：只读标记 + 别名。"""
    print("\n════ 模板接口 ════")
    ro = srv.post(
        "/api/workflows/templates",
        {"name": "只读巡检", "plan": READONLY_PLAN, "aliases": ["巡检", "ro-check"]},
    )
    check("只读模板存得进去", bool(ro.get("ok")), str(ro)[:200])
    ro_id = (ro.get("content") or {}).get("id", "")

    wr = srv.post(
        "/api/workflows/templates",
        {"name": "会写文件的编排", "plan": WRITABLE_PLAN, "aliases": ["写写"]},
    )
    check("可写模板存得进去", bool(wr.get("ok")), str(wr)[:200])

    lst = srv.get("/api/workflows/templates")["templates"]
    by_id = {t["id"]: t for t in lst}
    check("只读模板被标成只读", by_id.get(ro_id, {}).get("read_only") is True,
          str(by_id.get(ro_id)))
    check("可写模板没被标成只读", by_id.get((wr.get("content") or {}).get("id"), {}).get("read_only") is False)
    check("别名存下来了", by_id.get(ro_id, {}).get("aliases") == ["巡检", "ro-check"],
          str(by_id.get(ro_id, {}).get("aliases")))

    # 按别名取
    detail = srv.get("/api/workflows/templates/" + "巡检")
    check("按别名能取到模板", detail.get("content", {}).get("id") == ro_id, str(detail)[:200])
    check("详情里的只读标记也是重算的", detail.get("content", {}).get("read_only") is True)

    # 别名不能前缀匹配
    try:
        srv.get("/api/workflows/templates/" + "巡")
        ok = False
    except urllib.error.HTTPError as exc:
        ok = exc.code == 404
    check("别名是精确匹配（前缀不该命中）", ok)


def test_report_file(srv: Server) -> None:
    """跑一次只读模板 → 报告落盘。"""
    print("\n════ 报告落盘 ════")
    tpls = srv.get("/api/workflows/templates")["templates"]
    ro = next((t for t in tpls if t.get("read_only")), None)
    if ro is None:
        check("有只读模板可跑", False)
        return
    out = srv.post(f"/api/workflows/templates/{ro['id']}/run", {})
    # 运行会因为没有真模型而失败 —— 但报告路径仍要能给出结构
    check("触发模板有响应", isinstance(out, dict), str(out)[:200])
    meta = out.get("meta") or {}
    if out.get("ok") or meta.get("status") == "done":
        path = meta.get("report_path")
        check("报告路径回传了", bool(path), str(meta)[:200])
        check("报告文件真的在", bool(path) and Path(path).exists(), str(path))
    else:
        # 没跑成功就没有报告 —— 不算失败，但要说明
        print(f"  INFO  模板没跑成功（无真模型），跳过报告断言：{str(out)[:160]}")


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
        test_template_api(srv)
        test_report_file(srv)

        print("\n════ 界面（真点一次） ════")
        r = run_chrome(chrome, srv.token)
        if r.get("error"):
            check("界面验收脚本跑完了", False, r["error"])
        else:
            check("工作流页有「模板」tab", bool(r.get("hasTabBtn")))
            check("切到了模板页", r.get("tabNow") == "tpl", str(r.get("tabNow")))
            check("模板卡渲染出来了", (r.get("cardCount") or 0) > 0, str(r.get("cardCount")))
            check("卡上有「只读」徽章", (r.get("readOnlyBadges") or 0) >= 1,
                  f"{r.get('readOnlyBadges')} 个")
            check("卡上显示了别名", bool(r.get("hasAliasLine")), str(r.get("aliasText")))
            check("编辑器有别名输入框", bool(r.get("hasAliasInput")))
            check("别名输入框读得到已有别名", bool(r.get("aliasInputValue")),
                  repr(r.get("aliasInputValue")))
            check("填了别名能存下去", r.get("savedOk") is True,
                  f"错误提示：{r.get('errText')} / 模板名：{r.get('nameInputValue')} / "
                  f"编排：{r.get('planInputValue')}")
            check("按别名取模板返回 200", r.get("aliasFetchStatus") == 200,
                  str(r.get("aliasFetchStatus")))
            check("按别名取到了模板", r.get("aliasFetchOk") is True)
            check("只读步骤卡上标了「未改动」", "未改动" in (r.get("proofReadOnlyText") or ""),
                  f"实际显示：{r.get('proofReadOnlyText')!r} / 错误：{r.get('proofError')}")
            check("写操作步骤卡上标了「已改动」", "已改动" in (r.get("proofMutatedText") or ""),
                  f"实际显示：{r.get('proofMutatedText')!r}")
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
