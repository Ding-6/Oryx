"""第十五批的界面验收：应用库打开应用慢。

用户的原话：「现在在应用库里的应用点击打开后需要特别久才能打开」。

**根因不是「打开」慢，是图标请求把后端线程池占满了**：
应用库有 258 个应用，之前前端给每个卡片挂一个 /api/app/icon 请求，
每个请求占住一个线程池槽位约 1.1 秒（要起一个 PowerShell 进程）。
40 个槽位被占满 → 同一时刻点的「打开应用」只能排队。
实测：图标请求进行中点「打开」，最长等 **96 秒**；空闲时只要 4~40 毫秒。

这个脚本要验的：
  1. 打开应用库时**不发**逐个图标请求（只有 1 个批量请求）
  2. 图标能真的显示出来（不是一直首字母色块）
  3. 点「打开」的延迟在可接受范围（不是几十秒）

做法：起同源代理（8799）→ 真页面 + 真后端 → headless Chrome 里跑探针。

运行：python -m tests.verify_ui_batch15
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
BACKEND = "http://127.0.0.1:8765"
PROBE_PORT = 8801
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


# 探针：记录页面实际发出的请求，量「打开」的延迟
PROBE = r"""
<script>
(async () => {
  const R = {};
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const q = s => document.querySelector(s);

  // 拦 fetch：记录每个请求的 URL 与耗时
  const reqs = [];
  const origFetch = window.fetch;
  window.fetch = function(...args) {
    const url = (args[0] && typeof args[0] === 'string') ? args[0] : (args[0] && args[0].url) || '';
    const t0 = performance.now();
    reqs.push({ url: String(url), t0 });
    return origFetch.apply(this, args).then(r => {
      const e = reqs.find(x => x.url === String(url) && x.done === undefined);
      if (e) { e.done = true; e.ms = performance.now() - e.t0; }
      return r;
    });
  };

  const post = obj => fetch('/__acc_result', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(obj),
  }).catch(() => {});
  try {
    await sleep(1200);
    const before = reqs.length;

    /* ── 打开应用库 ── */
    // 应用库在「本地资料」下面：S.localView = 'apps' + gotoView('local')。
    // 不是 setView('apps') —— 那个视图不存在。
    S.localView = 'apps';
    gotoView('local');
    // 等应用卡片渲染出来
    const deadline = Date.now() + 15000;
    while (Date.now() < deadline) {
      if (q('#appGrid .appcard')) break;
      await sleep(120);
    }
    await sleep(400);
    const cards = [...document.querySelectorAll('#appGrid .appcard')];
    R.cardCount = cards.length;
    R.requestsAfterOpen = reqs.length - before;

    // 逐个图标请求（这是要禁的）
    R.perIconRequests = reqs.filter(r => r.url.includes('/api/app/icon')).length;
    R.batchIconRequests = reqs.filter(r => r.url.includes('/api/apps/icons')).length;

    /* ── 等图标真的显示出来 ── */
    const iconDeadline = Date.now() + 25000;
    while (Date.now() < iconDeadline) {
      const imgs = [...document.querySelectorAll('#appGrid .appcard .appicon img')];
      if (imgs.length > 5) break;
      await sleep(200);
    }
    const withImg = [...document.querySelectorAll('#appGrid .appcard .appicon')].filter(
      el => el.querySelector('img')
    ).length;
    const withFall = [...document.querySelectorAll('#appGrid .appcard .appicon')].filter(
      el => el.querySelector('.fall')
    ).length;
    R.iconsAsImage = withImg;
    R.iconsAsLetter = withFall;
    R.iconTotal = withImg + withFall;

    /* ── 点「打开」的延迟（真点，不看代码） ── */
    const btn = q('#appGrid .appcard [data-applaunch]');
    R.hasLaunchBtn = !!btn;
    if (btn) {
      const notice = q('#notice');
      const t0 = performance.now();
      btn.click();
      // 等提示条出现（打开成功的反馈）。提示条是 #notice（不是 .toast）。
      const tEnd = Date.now() + 20000;
      while (Date.now() < tEnd) {
        if (notice && notice.classList.contains('on') && notice.textContent.trim()) break;
        await sleep(40);
      }
      R.launchMs = performance.now() - t0;
      R.toastText = notice ? notice.textContent.trim() : '';
    }

    /* ── 再次进入应用库：不该重复发图标请求 ── */
    const before2 = reqs.length;
    setView('chat');
    await sleep(500);
    S.localView = 'apps';
    gotoView('local');
    await sleep(1500);
    R.requestsOnSecondVisit = reqs.length - before2;
    R.perIconOnSecondVisit = reqs.filter(r => r.url.includes('/api/app/icon')).length;

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
        url = BACKEND + self.path
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("X-Oryx-Token", ProbeHandler.token)
        if method not in ("GET", "HEAD"):
            req.add_header("X-Oryx-Request", "1")
        if body is not None:
            req.add_header("Content-Type", self.headers.get("Content-Type", "application/json"))
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
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


def read_token() -> str:
    candidates = [
        Path("D:/OryxData/data/config.json"),
        Path(os.environ.get("APPDATA", "")) / "Oryx" / "config.json",
    ]
    for c in candidates:
        try:
            return json.loads(c.read_text("utf-8")).get("api_token", "")
        except Exception:
            continue
    return ""


def run(chrome: str, token: str) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="oryx_acc15_"))
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
            # **不要** --virtual-time-budget：页面有 setInterval，
            # 虚拟时间会把后面的 setTimeout 全冻结。
            f"http://127.0.0.1:{PROBE_PORT}/",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        deadline = time.time() + 120
        while time.time() < deadline:
            if ProbeHandler.result.get("done"):
                return ProbeHandler.result
            if proc.poll() is not None:
                time.sleep(1.0)
                return ProbeHandler.result or {
                    "error": f"Chrome 提前退出（{proc.returncode}）",
                }
            time.sleep(0.4)
        return {"error": "等验收结果超时（120 秒）", "partial": ProbeHandler.result}
    finally:
        try:
            proc.kill()
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)
        srv.shutdown()


def main() -> int:
    chrome = find_chrome()
    if not chrome:
        print("找不到 Chrome/Edge，跳过")
        return 0
    token = read_token()
    if not token:
        print("读不到 api_token，跳过")
        return 0

    print(f"浏览器: {chrome}")
    r = run(chrome, token)
    if r.get("error"):
        print(f"  验收脚本出错：{r['error']}")
        print(f"  部分结果：{r.get('partial')}")
        return 1

    print("\n════ 应用库打开速度 ════")
    check("应用卡片渲染出来了", (r.get("cardCount") or 0) > 0, str(r.get("cardCount")))

    # 核心断言：不能有逐个图标请求
    check(
        "打开应用库不发逐个图标请求",
        (r.get("perIconRequests") or 0) == 0,
        f"发了 {r.get('perIconRequests')} 个 /api/app/icon —— "
        f"这会把后端线程池占满，点「打开」要排队等最多 96 秒",
    )
    check(
        "只发 1 个批量图标请求",
        (r.get("batchIconRequests") or 0) == 1,
        f"发了 {r.get('batchIconRequests')} 个 /api/apps/icons",
    )

    print("\n════ 图标显示 ════")
    check(
        "图标真的显示出来了（不是一直首字母色块）",
        (r.get("iconsAsImage") or 0) > 0,
        f"图片图标 {r.get('iconsAsImage')} 个",
    )
    check(
        "图标与色块都有（取不到的才用色块）",
        (r.get("iconTotal") or 0) >= (r.get("cardCount") or 0),
        f"图标 {r.get('iconsAsImage')} + 色块 {r.get('iconsAsLetter')} "
        f"vs 卡片 {r.get('cardCount')}",
    )

    print("\n════ 点「打开」的延迟 ════")
    check("有「打开」按钮", bool(r.get("hasLaunchBtn")))
    ms = r.get("launchMs")
    if isinstance(ms, (int, float)):
        print(f"  实测：{ms:.0f} ms")
    check(
        "点「打开」在 5 秒内完成",
        isinstance(ms, (int, float)) and ms < 5000,
        f"耗时 {ms} ms",
    )
    check(
        "点「打开」有成功反馈",
        "打开" in (r.get("toastText") or ""),
        repr(r.get("toastText")),
    )

    print("\n════ 重复进入 ════")
    check(
        "再次进入不重复发图标请求",
        (r.get("perIconOnSecondVisit") or 0) == 0,
        f"又发了 {r.get('perIconOnSecondVisit')} 个",
    )

    print("\n" + "=" * 60)
    if FAILED:
        print(f"失败 {len(FAILED)} 项：{'、'.join(FAILED)}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
