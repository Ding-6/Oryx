"""浏览器验证：界面扩展声明的 `caps` **真的生效**。

以前 caps 是纯装饰
----------------
`uiext.py` 一直写着「扩展可以声明 caps，前端据此决定给不给某些 API」，
但前端 `uiext.js` **从头到尾没读过 caps** —— 声明了跟没声明一样。

这与之前修的 `requires` 是同一类毛病：**声明了却不生效**，
比不声明更糟 —— 它让人以为有约束。

caps 的语义
-----------
| 扩展声明的 caps | 调 `/api/agents`（要 proc.exec） | 调 `/api/state`（不要能力） |
|---|---|---|
| `["fs.read"]` | **拒**（缺 proc.exec） | 放行 |
| `["proc.exec"]` | 放行 | 放行 |
| 不声明 | 放行 | 放行 |

不声明 = 不限制，是**保守解释**：caps 是后加的字段，
老扩展没写，不该因为它就全哑了（但设置页会标出「未声明（等同全权）」）。

为什么这个脚本要开真浏览器
--------------------------
caps 的约束发生在**前端**（`ctx.api` 被按声明收窄），
只有真实页面里跑才能验 —— httpx 模拟不出扩展的运行环境。
"""
from __future__ import annotations

import asyncio

from _webcheck import describe_busy, ports_free
# 统一收工：杀 Chrome 的**进程树** + 清它的 profile。
# 只 terminate 父进程的话，子进程会活下来锁着 profile，
# 下一次跑 Chrome 就起不来（实测等 160 秒后报「Chrome 没起来」）。
from _webdrive import kill_tree, stop_chrome
import json
import os
import socket
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

CORE = Path(__file__).resolve().parent.parent
PORT = 8809

# 一个通用探针扩展：它按清单里给的目标去调 API，把结果记在 DOM 上。
#
# 目标写死在清单的 `caps` 旁边不合适（清单不支持），
# 所以用扩展 id 区分行为 —— 每个扩展测一条路径。
PROBE_JS = """
export default {
  setup(ctx) {
    const el = document.createElement('div');
    el.setAttribute('data-capext', ctx.extId);
    el.setAttribute('data-cap-result', '(还没跑)');
    ctx.mount.append(el);
    (async () => {
      try {
        const r = await ctx.api(PROBE_PATH, { method: PROBE_METHOD });
        const body = await r.text();
        // 判定依据是**状态码**，不是响应体里有没有某个词 ——
        // 文件名里带「能力」两个字的话，按正文判就会误判成「被拒」。
        const denied = r.status === 403 && body.includes('能力');
        el.setAttribute('data-cap-result', denied ? 'DENIED' : 'ALLOWED');
        el.setAttribute('data-cap-body', 'HTTP ' + r.status + ' ' + body.slice(0, 100));
      } catch (e) {
        el.setAttribute('data-cap-result', 'ERROR:' + (e && e.message || e));
      }
    })();
  }
};
"""

# 每个扩展测什么：路径 + 方法
#
# 刻意挑**无副作用**的端点 —— 放行的那几个会真的发出去，
# 要是挑了 /api/chat 就会真的开始对话。
CASES = {
    "cap-reader": ("/api/tools/search", "POST"),   # 要 proc.exec
    "cap-exec": ("/api/tools/search", "POST"),     # 要 proc.exec（自己声明了）
    "cap-none": ("/api/tools/search", "POST"),     # 不声明 caps
    "cap-state": ("/api/state", "GET"),             # 不需要任何能力
    "cap-fsread": ("/api/files/list", "GET"),      # 要 fs.read（自己声明了）
    # ── 记忆：同路径不同方法，三个能力各管各的 ──────────────────
    "cap-mem-read": ("/api/memory", "GET"),        # 要 fs.read
    "cap-mem-write": ("/api/memory", "POST"),      # 要 fs.write（**不**要 fs.read）
    "cap-mem-write-get": ("/api/memory", "GET"),   # 只声明 fs.write → 读要被拒
}


def _port_free(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        try:
            s.connect(("127.0.0.1", port))
            return False
        except OSError:
            return True


def check(label: str, ok: bool, detail: str = "") -> bool:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))
    return ok


async def _browser_probe(port: int) -> dict:
    import websockets

    from check_web_modules import find_chrome

    chrome = find_chrome()
    # 固定名 + 跑完清掉 —— Chrome 的缓存比文件本身大得多，
    # 留着跑几百轮能占好几个 GB。
    profile = Path(tempfile.gettempdir()) / "oryx_caps_profile"
    shutil.rmtree(profile, ignore_errors=True)
    proc = subprocess.Popen(
        [
            chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
            f"--remote-debugging-port={port + 100}",
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
                    f"http://127.0.0.1:{port + 100}/json/version", timeout=3
                ) as r:
                    if r.read():
                        break
            except Exception:
                continue
        else:
            # Chrome 没起来 —— **立刻返回**，别一路走到下一个超时
            # （否则白等几十秒，最后只报一句「没有结论」）
            return {"__error__": "Chrome 没起来（可能端口被占或系统负载高）"}

        req = urllib.request.Request(
            f"http://127.0.0.1:{port + 100}/json/new?about:blank", method="PUT"
        )
        target = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())

        async with websockets.connect(
            target["webSocketDebuggerUrl"], max_size=16 * 1024 * 1024,
            open_timeout=15,
        ) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Page.enable"}))
            await ws.send(json.dumps({
                "id": 3, "method": "Page.navigate",
                "params": {"url": f"http://127.0.0.1:{port}/"},
            }))
            # 等页面**真的加载完** —— 固定 sleep 是猜，负载高时猜不够。
            # （单独跑通过、批量跑失败，而且每次挂的脚本还不一样）
            from _webdrive import wait_page_ready
            await wait_page_ready(ws, timeout=25.0)
            await asyncio.sleep(8)   # 保留原来的余量

            expr = """(()=>{
                // 扩展的 DOM 在各自的 shadow root 里 —— 逐个 host 进去找。
                const out = {};
                for (const host of document.querySelectorAll('.uiext-host')) {
                    const root = host.shadowRoot;
                    if (!root) continue;
                    for (const el of root.querySelectorAll('[data-capext]')) {
                        out[el.getAttribute('data-capext')] = {
                            result: el.getAttribute('data-cap-result') || '',
                            body: el.getAttribute('data-cap-body') || '',
                        };
                    }
                }
                return out;
            })()"""
            await ws.send(json.dumps({
                "id": 300, "method": "Runtime.evaluate",
                "params": {"expression": expr, "returnByValue": True},
            }))
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=25)
                msg = json.loads(raw)
                if msg.get("id") == 300:
                    return (msg.get("result", {}).get("result", {})
                            .get("value") or {})
    finally:
        # 统一收工 —— 杀**进程树**（只 terminate 的话 Chrome 的子进程
        # 会活下来锁着 profile，下一次跑就起不来）+ 清掉 profile。
        stop_chrome(proc, profile, port + 100)


def main() -> int:
    if not _port_free(PORT):
        print(f"端口 {PORT} 已被占用 —— 跳过（可能有上次的测试进程没退出）")
        return 0

    home = Path(tempfile.mkdtemp())
    ext_root = home / "ui-extensions"

    caps_of = {
        "cap-reader": ["fs.read"],
        "cap-exec": ["proc.exec"],
        "cap-none": [],
        "cap-state": [],
        "cap-fsread": ["fs.read"],
        "cap-mem-read": ["fs.read"],
        "cap-mem-write": ["fs.write"],
        "cap-mem-write-get": ["fs.write"],
    }
    for ext_id, caps in caps_of.items():
        d = ext_root / ext_id
        d.mkdir(parents=True)
        manifest = {
            "id": ext_id, "name": ext_id, "mount": "rail", "enabled": True,
        }
        if caps:
            manifest["caps"] = caps
        (d / "oryx.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        path, method = CASES[ext_id]
        (d / "index.js").write_text(
            PROBE_JS.replace("PROBE_PATH", json.dumps(path))
            .replace("PROBE_METHOD", json.dumps(method))
            .replace("ctx.extId", json.dumps(ext_id)),
            encoding="utf-8",
        )

    env = dict(os.environ)
    env["ORYX_API_TOKEN"] = "caps-check-token"
    env["ORYX_HOME"] = str(home)
    # 端口被占 → 这次测的其实是**别人的服务**（或别人的 Chrome），
    # 结论全是假的。先查一下：宁可跳过，也不能给出一个基于错误环境的「通过」。
    if not ports_free(PORT):
        print(f"端口被占用（{describe_busy(PORT)}）—— 跳过"
              "（可能有上次的测试进程没退出）")
        return 0
    server = subprocess.Popen(
        [sys.executable, "-m", "oryx", "--no-browser", "--port", str(PORT)],
        cwd=str(CORE), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    try:
        for _ in range(80):
            time.sleep(0.5)
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{PORT}/api/ping", timeout=3
                ) as r:
                    if r.read():
                        break
            except Exception:
                continue
        else:
            print("服务没起来")
            return 1

        data = asyncio.run(_browser_probe(PORT))
        print("浏览器实测：")
        if not data:
            print("  [FAIL] 没读到任何扩展的探测结果")
            return 1

        ok = True
        # 1. 声明了 fs.read 的扩展调需要 proc.exec 的接口 → 必须被拒
        if data.get("__error__"):
            print(f"  [FAIL] 浏览器探针失败：{data['__error__']}")
            return 1
        r = data.get("cap-reader", {})
        ok &= check(
            "只声明 fs.read 的扩展调 POST /api/tools/search 被拒",
            r.get("result") == "DENIED",
            f"{r.get('result')} · {r.get('body', '')[:70]}",
        )
        # 2. 声明了 proc.exec 的扩展调同一个接口 → 放行
        r = data.get("cap-exec", {})
        ok &= check(
            "声明了 proc.exec 的扩展调 POST /api/tools/search 放行",
            r.get("result") == "ALLOWED",
            f"{r.get('result')} · {r.get('body', '')[:70]}",
        )
        # 3. 没声明 caps 的扩展 → 不限制（保守解释，不弄坏老扩展）
        r = data.get("cap-none", {})
        ok &= check(
            "没声明 caps 的扩展调 POST /api/tools/search 不被拦",
            r.get("result") == "ALLOWED",
            f"{r.get('result')} · {r.get('body', '')[:70]}",
        )
        # 4. 不需要任何能力的路径 → 谁都放行
        r = data.get("cap-state", {})
        ok &= check(
            "不需要能力的 GET /api/state 对谁都放行",
            r.get("result") == "ALLOWED",
            f"{r.get('result')} · {r.get('body', '')[:70]}",
        )
        # 5. 声明了 fs.read 的扩展读文件清单 → 放行（它要的正是这个）
        r = data.get("cap-fsread", {})
        ok &= check(
            "声明了 fs.read 的扩展调 GET /api/files/list 放行",
            r.get("result") == "ALLOWED",
            f"{r.get('result')} · {r.get('body', '')[:70]}",
        )
        # 6-8. 记忆：同路径不同方法，三个能力各管各的
        r = data.get("cap-mem-read", {})
        ok &= check(
            "声明了 fs.read 的扩展调 GET /api/memory 放行",
            r.get("result") == "ALLOWED",
            f"{r.get('result')} · {r.get('body', '')[:70]}",
        )
        r = data.get("cap-mem-write", {})
        ok &= check(
            "只声明 fs.write 的扩展调 POST /api/memory 放行"
            "（写记忆不该顺带要求会读记忆）",
            r.get("result") == "ALLOWED",
            f"{r.get('result')} · {r.get('body', '')[:70]}",
        )
        r = data.get("cap-mem-write-get", {})
        ok &= check(
            "只声明 fs.write 的扩展调 GET /api/memory 被拒（缺 fs.read）",
            r.get("result") == "DENIED",
            f"{r.get('result')} · {r.get('body', '')[:70]}",
        )
        print()
        print("=" * 62)
        if ok:
            print("全部通过")
            return 0
        print("有断言未通过")
        return 1
    finally:
        # 杀**进程树** —— agentd 是子进程，只 terminate 父进程的话
        # 它会变成孤儿继续占着端口，下一个脚本就被跳过。
        kill_tree(server)


if __name__ == "__main__":
    sys.exit(main())
