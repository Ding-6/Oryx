"""设置页「依赖阻塞」与「回收区清理」的真实浏览器验证。

这一节是**用户可见**的 —— 与其相信代码写对了，不如在真浏览器里点一遍：

  1. 起服务，装一个**依赖缺失**的后端扩展 + 一个正常的
  2. 检查依赖解析说清了它缺什么、它**没被加载**
  3. 打开设置页 → 「扩展」标签
  4. 检查依赖阻塞的提示出现了
  5. 检查回收区的「清空回收区」按钮在、保留期说明在
  6. 删一个扩展 → 进回收区 → 能恢复

运行：python tests/check_web_ext_deps.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _webcheck import describe_busy, ports_free  # noqa: E402
from _webdrive import (Result, current_port, run_probe,  # noqa: E402
                        start_server, stop_server, wait_ready)

PORT = 8784
CDP_PORT = 9347
TOKEN = "ext-deps-check"

# 一个依赖缺失的后端扩展：它要的技能没装 → 应当被阻塞、不加载。
BLOCKED_MANIFEST = {
    "id": "blocked-ext",
    "name": "依赖缺失的扩展",
    "description": "用来验证依赖阻塞",
    "requires": {"skills": ["no-such-skill"]},
}

# 一个正常的扩展。
NORMAL_MANIFEST = {
    "id": "normal-ext",
    "name": "正常扩展",
    "description": "用来验证正常扩展不受影响",
}


def _api(port: int, path: str) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        headers={"X-Oryx-Token": TOKEN})
    return json.loads(urllib.request.urlopen(req, timeout=15).read().decode())


def _install_tool_ext(home: Path, manifest: dict) -> None:
    d = home / "extensions" / manifest["id"]
    d.mkdir(parents=True, exist_ok=True)
    (d / "oryx.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    # 端口被占用**不再跳过**。
    # 以前这里直接 return 0 —— 测试「通过」了，但实际一行都没跑：
    # 上个脚本的残留进程还占着端口时，套件看起来全绿，其实漏测。
    # 现在 `start_server` 会往后找一个空端口，测试照跑不误。
    if not ports_free(PORT):
        print(f"端口 {PORT} 被占用（{describe_busy(PORT)}）—— 换一个端口继续跑")

    home = Path(tempfile.mkdtemp())
    _install_tool_ext(home, BLOCKED_MANIFEST)
    _install_tool_ext(home, NORMAL_MANIFEST)

    server = start_server(PORT, home, TOKEN)


    srv_port = current_port(PORT)
    try:
        if not wait_ready(srv_port):
            print("服务没起来")
            return 1

        r = Result()

        # ── 依赖解析：它被阻塞了，原因说清了缺什么 ────────────────
        data = _api(srv_port, "/api/extensions")
        deps = data.get("dependencies") or {}
        blocked = deps.get("blocked") or []
        rec = next((b for b in blocked if b.get("id") == "blocked-ext"), None)

        r.check("依赖缺失的扩展被阻塞了", rec is not None, str(blocked)[:250])
        r.check("阻塞原因说清了缺什么",
                bool(rec) and "no-such-skill" in str(rec.get("reason", "")),
                str(rec)[:250] if rec else "")

        # 它没进工具表 —— 依赖不满足还硬跑，只会跑一半就卡住
        tools = (data.get("tool_table") or {}).get("tools") or []
        tool_ids = {t.get("id") for t in tools if isinstance(t, dict)}
        r.check("被阻塞的扩展没进工具表", "blocked-ext" not in tool_ids,
                str(sorted(x for x in tool_ids if x))[:200])

        # 正常的那个没受影响
        r.check("正常扩展没被阻塞",
                not any(b.get("id") == "normal-ext" for b in blocked),
                str(blocked)[:250])

        # ── 设置页：用户看得到「依赖没满足」 ──────────────────────
        probe = run_probe(srv_port, CDP_PORT, """(async () => {
            const sleep = ms => new Promise(r => setTimeout(r, ms));
            await window.__openExtensionsTab();
            const body = document.body.innerText || '';
            return {
                hasBlocked: body.includes('依赖没满足'),
                hasTrash: body.includes('回收区'),
                hasCleanup: body.includes('清空回收区') || body.includes('清理'),
                hasRetention: body.includes('保留') || body.includes('天'),
                mentions: body.includes('blocked-ext'),
                // 「怎么修」—— 依赖没满足之后，用户该做什么
                hasFix: body.includes('→') || body.includes('装一个叫')
                        || body.includes('重新启用'),
                // 缺的是什么类型（技能？专家？界面扩展？）
                hasKindLabel: body.includes('（技能）') || body.includes('（专家）')
                              || body.includes('（界面扩展）')
                              || body.includes('（工具扩展）'),
                body: body.slice(0, 300),
            };
        })()""")
        r.check("设置页说得出「依赖没满足，扩展没加载」",
                bool(probe.get("hasBlocked")), str(probe.get("body", ""))[:200])
        r.check("设置页点得出是哪个扩展", bool(probe.get("mentions")),
                str(probe.get("body", ""))[:200])
        r.check("设置页说得出**怎么修**",
                bool(probe.get("hasFix")),
                str(probe.get("body", ""))[:250])
        r.check("设置页说清了缺的是什么类型",
                bool(probe.get("hasKindLabel")),
                str(probe.get("body", ""))[:250])
        r.check("设置页有回收区", bool(probe.get("hasTrash")),
                str(probe.get("body", ""))[:200])
        r.check("回收区有清理入口", bool(probe.get("hasCleanup")),
                str(probe.get("body", ""))[:200])
        r.check("说明了保留期", bool(probe.get("hasRetention")),
                str(probe.get("body", ""))[:200])

        # ── 删除 → 进回收区 → 能恢复 ──────────────────────────────
        probe2 = run_probe(srv_port, CDP_PORT, """(async () => {
            const sleep = ms => new Promise(r => setTimeout(r, ms));
            await window.__openExtensionsTab();
            // 等扩展表格**渲染出来** —— 它是异步的（要拉 /api/extensions）。
            // 直接查会落在还没画完的空表上，表现为「时好时坏」。
            for (let i = 0; i < 40; i++) {
                if (document.querySelector('[data-extremove="normal-ext"]')) break;
                await sleep(300);
            }
            const rm = document.querySelector('[data-extremove="normal-ext"]');
            if (!rm) return {found: false};
            rm.click();
            // 删除后设置页会重画，回收区表格是重画出来的 —— 得等它
            for (let i = 0; i < 20; i++) {
                await sleep(300);
                const btn = document.querySelector('[data-trashrestore]');
                if (btn) return {found: true, restoreId:
                    btn.getAttribute('data-trashrestore')};
            }
            return {found: true, restoreId: null};
        })()""")
        r.check("扩展行上有删除按钮", bool(probe2.get("found")))

        # 回收区在**另一个接口** —— /api/extensions 里没有 trash 字段
        trash = _api(srv_port, "/api/extensions/trash").get("items") or []
        trash_ids = {t.get("id") for t in trash if isinstance(t, dict)}
        originals = {t.get("original_id") for t in trash if isinstance(t, dict)}
        r.check("删除后进了回收区",
                "normal-ext" in trash_ids or "normal-ext" in originals,
                str(trash_ids))
        r.check("回收区里有恢复按钮", bool(probe2.get("restoreId")),
                str(probe2.get("restoreId")))

        # ── 恢复 ───────────────────────────────────────────────────
        probe3 = run_probe(srv_port, CDP_PORT, """(async () => {
            const sleep = ms => new Promise(r => setTimeout(r, ms));
            await window.__openExtensionsTab();
            // 回收区表格是**异步拉完才渲染**的 —— 得等它出现
            let btn = null;
            for (let i = 0; i < 25; i++) {
                btn = document.querySelector('[data-trashrestore]');
                if (btn) break;
                await sleep(300);
            }
            if (!btn) return {found: false};
            btn.click();
            await sleep(2500);
            return {found: true};
        })()""")
        r.check("能点恢复", bool(probe3.get("found")))

        trash2 = _api(srv_port, "/api/extensions/trash").get("items") or []
        r.check("恢复后不在回收区了", not trash2, str(trash2)[:200])

        return r.summary()
    finally:
        stop_server(server)


if __name__ == "__main__":
    sys.exit(main())
