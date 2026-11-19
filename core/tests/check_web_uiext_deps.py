"""界面扩展依赖的**真实浏览器**验证。

界面扩展此前不能声明依赖 —— 一个面板缺了它要的东西，
照样挂上去（挂个空壳），用户只看到「怎么是空的」。

这一节验证：
  1. 装三个界面扩展：一个正常、一个依赖缺失、一个依赖另一个
  2. 清单接口里：正常的在，缺依赖的**不在**，且 skipped 里有原因
  3. 浏览器里：缺依赖的扩展**没挂上**（DOM 里查不到）
  4. 依赖界面扩展的能挂上（依赖满足）
  5. 设置页看得出「有扩展没加载」

运行：python tests/check_web_uiext_deps.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _webcheck import describe_busy, ports_free  # noqa: E402
from _webdrive import (Result, current_port, install_ui_ext,  # noqa: E402
                        run_probe, start_server, stop_server, wait_ready)

PORT = 8788
CDP_PORT = 9351
TOKEN = "uiext-deps-check"

PANEL_JS = """
export default {
  setup(ctx) {
    const el = document.createElement('div');
    el.setAttribute('data-uiext', 'EXT_ID');
    el.textContent = 'EXT_ID';
    ctx.mount.append(el);
  }
};
"""


def _panel_js(ext_id: str) -> str:
    return PANEL_JS.replace("EXT_ID", ext_id)


def main() -> int:
    # 端口被占用**不再跳过**。
    # 以前这里直接 return 0 —— 测试「通过」了，但实际一行都没跑：
    # 上个脚本的残留进程还占着端口时，套件看起来全绿，其实漏测。
    # 现在 `start_server` 会往后找一个空端口，测试照跑不误。
    if not ports_free(PORT):
        print(f"端口 {PORT} 被占用（{describe_busy(PORT)}）—— 换一个端口继续跑")

    home = Path(tempfile.mkdtemp())
    install_ui_ext(home, "plain",
                   {"id": "plain", "name": "无依赖", "mount": "rail",
                    "enabled": True}, _panel_js("plain"))
    install_ui_ext(home, "needs-ok",
                   {"id": "needs-ok", "name": "依赖满足", "mount": "rail",
                    "enabled": True,
                    "requires": {"ui": ["plain"]}}, _panel_js("needs-ok"))
    install_ui_ext(home, "needs-missing",
                   {"id": "needs-missing", "name": "依赖缺失", "mount": "rail",
                    "enabled": True,
                    "requires": {"ui": ["no-such-panel"]}},
                   _panel_js("needs-missing"))

    server = start_server(PORT, home, TOKEN)


    srv_port = current_port(PORT)
    try:
        if not wait_ready(srv_port):
            print("服务没起来")
            return 1

        # ── 清单接口：谁被放行、谁被拦、理由是什么 ──────────────────
        req = urllib.request.Request(
            f"http://127.0.0.1:{srv_port}/api/ui/modules",
            headers={"X-Oryx-Token": TOKEN})
        data = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
        ids = [e["id"] for e in data.get("extensions", [])]
        skipped = {s["id"]: s.get("reason", "") for s in data.get("skipped", [])}

        r = Result()
        r.check("无依赖的扩展被放行", "plain" in ids, str(ids))
        r.check("依赖满足的扩展被放行", "needs-ok" in ids, str(ids))
        r.check("依赖不满足的扩展没被放行", "needs-missing" not in ids, str(ids))
        r.check("被拦下的原因说清了缺什么",
                "no-such-panel" in skipped.get("needs-missing", ""),
                skipped.get("needs-missing", "(没进 skipped)"))

        # ── 浏览器：确实挂上了、确实没挂上 ─────────────────────────
        probe = run_probe(srv_port, CDP_PORT, """(()=>{
            const out = {mounted: [], hosts: 0};
            for (const host of document.querySelectorAll('.uiext-host')) {
                out.hosts++;
                const root = host.shadowRoot;
                if (!root) continue;
                for (const el of root.querySelectorAll('[data-uiext]')) {
                    out.mounted.push(el.getAttribute('data-uiext'));
                }
            }
            return out;
        })()""")

        mounted = probe.get("mounted", [])
        r.check("浏览器里 plain 挂上了", "plain" in mounted, str(mounted))
        r.check("浏览器里 needs-ok 挂上了", "needs-ok" in mounted, str(mounted))
        r.check("浏览器里 needs-missing 没挂上",
                "needs-missing" not in mounted, str(mounted))

        # ── 设置页：用户能看见「有扩展没加载」 ──────────────────────
        probe2 = run_probe(srv_port, CDP_PORT, """(async () => {
            const sleep = ms => new Promise(r => setTimeout(r, ms));
            // 设置面板要先打开 —— 标签的点击监听是渲染时才绑的
            await window.__openExtensionsTab();
            const body = document.body.innerText || '';
            return {
                opened: true,
                hasLabel: body.includes('没加载的界面扩展'),
                mentions: body.includes('needs-missing'),
                body: body.slice(0, 400),
            };
        })()""")
        r.check("设置页看得出「有扩展没加载」",
                bool(probe2.get("hasLabel")),
                str(probe2.get("body", ""))[:200])
        r.check("设置页说清了是哪个扩展没加载",
                bool(probe2.get("mentions")),
                str(probe2.get("body", ""))[:200])

        return r.summary()
    finally:
        stop_server(server)


if __name__ == "__main__":
    sys.exit(main())
