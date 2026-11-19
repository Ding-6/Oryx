"""设置页「扩展」小节的真实浏览器验证。

这一节是**用户可见**的 —— 与其相信代码写对了，不如在真浏览器里点一遍：

  1. 起服务，装一个后端扩展 + 一个界面扩展
  2. 打开设置页 → 点「扩展」标签
  3. 检查两张表都出现了、扩展行在、按钮齐
  4. 检查那些**说明文字**在（用户得知道规矩）
  5. 点启停 → 状态变了、有提示 → 能恢复原状
  6. 点「重新加载」→ 有提示、表格还在

运行：python tests/check_web_settings_ext.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _webcheck import describe_busy, ports_free  # noqa: E402
from _webdrive import (Result, install_ui_ext, run_probe,  # noqa: E402
                        start_server, stop_server, wait_ready)

PORT = 8782
CDP_PORT = 9345
TOKEN = "settings-ext-check"

UI_PANEL_JS = """
export default {
  setup(ctx) {
    const el = document.createElement('div');
    el.setAttribute('data-uiext', 'settings-panel');
    ctx.mount.append(el);
  }
};
"""


def _api(port: int, path: str) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        headers={"X-Oryx-Token": TOKEN})
    return json.loads(urllib.request.urlopen(req, timeout=15).read().decode())


def main() -> int:
    if not ports_free(PORT):
        print(f"端口被占用（{describe_busy(PORT)}）—— 跳过"
              "（可能有上次的测试进程没退出）")
        return 0

    home = Path(tempfile.mkdtemp())
    ext = home / "extensions" / "settings-ext"
    ext.mkdir(parents=True)
    (ext / "oryx.json").write_text(json.dumps({
        "id": "settings-ext", "name": "设置页验证扩展",
        "description": "用来验证设置页",
        # 声明能力 —— 设置页要显示**实际生效**的那些
        "caps": ["fs.read", "net.fetch"],
    }, ensure_ascii=False), encoding="utf-8")
    install_ui_ext(home, "settings-panel",
                   {"id": "settings-panel", "name": "设置页验证面板",
                    "mount": "rail", "enabled": True,
                    "caps": ["fs.read"]}, UI_PANEL_JS)

    server = start_server(PORT, home, TOKEN)
    try:
        if not wait_ready(PORT):
            print("服务没起来")
            return 1

        r = Result()

        # ── 设置页结构 ────────────────────────────────────────────
        probe = run_probe(PORT, CDP_PORT, """(async () => {
            const sleep = ms => new Promise(r => setTimeout(r, ms));
            const ok = await window.__openExtensionsTab();
            const body = document.body.innerText || '';
            return {
                opened: ok,
                hasTab: !!document.querySelector('#page [data-sec="extensions"]'),
                rows: document.querySelectorAll('#page table tr').length,
                extToggles: document.querySelectorAll('[data-exttoggle]').length,
                uiToggles: document.querySelectorAll('[data-uitoggle]').length,
                extRemoves: document.querySelectorAll('[data-extremove]').length,
                uiRemoves: document.querySelectorAll('[data-uiremove]').length,
                hasReload: !!document.querySelector('#extReload'),
                hasInstallBe: !!document.querySelector('#extInstall'),
                hasInstallUi: !!document.querySelector('#extInstallUi'),
                body,
                // 扩展往哪放 —— 路径不是固定的（Windows 优先 D 盘），
                // 只能运行时告诉用户
                hasUserDir: body.includes('extensions') && body.includes('ui-extensions')
                            && /[A-Za-z]:\\\\/.test(body),
            };
        })()""")

        body = str(probe.get("body", ""))
        r.check("设置页打开了「扩展」标签", bool(probe.get("opened")))
        r.check("有「扩展」标签", bool(probe.get("hasTab")))
        r.check("扩展小节渲染出了表格行", int(probe.get("rows") or 0) > 0,
                str(probe.get("rows")))
        r.check("后端扩展在列表里", int(probe.get("extToggles") or 0) > 0)
        r.check("界面扩展在列表里", int(probe.get("uiToggles") or 0) > 0)
        r.check("后端扩展有启停按钮", int(probe.get("extToggles") or 0) > 0)
        r.check("界面扩展有启停按钮", int(probe.get("uiToggles") or 0) > 0)
        r.check("后端扩展有删除按钮", int(probe.get("extRemoves") or 0) > 0)
        r.check("界面扩展有删除按钮", int(probe.get("uiRemoves") or 0) > 0)
        r.check("有「重新加载」按钮", bool(probe.get("hasReload")))
        r.check("有「安装后端」按钮", bool(probe.get("hasInstallBe")))
        r.check("有「安装界面」按钮", bool(probe.get("hasInstallUi")))

        # ── 说明文字与信任级别 ────────────────────────────────────
        r.check("显示了信任级别信息",
                any(w in body for w in ("内置", "用户", "项目", "插件")),
                body[:160])
        r.check("说明了「扩展不能覆盖内置工具」",
                "不能覆盖内置工具" in body, body[:160])
        r.check("说明了「装完默认不启用」",
                "装完默认不启用" in body, body[:160])
        r.check("说明了回收区", "回收区" in body, body[:160])
        r.check("说明了界面扩展的能力",
                "能力：" in body or "fs.read" in body, body[:200])
        r.check("显示了后端扩展的能力",
                "fs.read" in body and "net.fetch" in body, body[:300])
        r.check("告诉用户扩展往哪放（真实路径）",
                bool(probe.get("hasUserDir")),
                str(probe.get("body", ""))[:300])

        # ── 能力速查表 ──────────────────────────────────────────────
        # <details> 收起时内容是 display:none —— innerText 读不到，
        # 所以先展开再读，验的是**用户真能看见**的状态。
        probe2 = run_probe(PORT, CDP_PORT, """(async () => {
            const sleep = ms => new Promise(r => setTimeout(r, ms));
            await window.__openExtensionsTab();
            for (let i = 0; i < 20; i++) {
                const d = document.querySelector('details');
                if (d) break;
                await sleep(300);
            }
            const d = document.querySelector('details');
            if (!d) return {found: false};
            d.open = true;
            await sleep(300);
            // textContent 而不是 innerText —— 表格在说明文字后面，
            // innerText 会被可见性过滤，textContent 拿的是完整文本
            const text = d.textContent || '';
            const rows = d.querySelectorAll('tbody tr').length;
            return {found: true, rows, text: text.slice(0, 3000)};
        })()""")
        r.check("设置页有「能力速查表」", bool(probe2.get("found")))
        r.check("速查表列出了能力", int(probe2.get("rows") or 0) >= 5,
                str(probe2.get("rows")))
        quick = str(probe2.get("text", ""))
        r.check("速查表写了每个能力管什么",
                "执行命令" in quick and "读本机的文件" in quick, quick[:200])
        r.check("速查表标出了高危能力",
                "高危" in quick, quick[:200])
        r.check("速查表说了谁能声明",
                "不可信来源" in quick, quick[:200])

        # ── 启停：状态变了 → 有提示 → 能恢复 ──────────────────────
        # 表格是**异步渲染**的（新页面打开设置页 → 拉数据 → 重画），
        # 所以一律轮询等按钮出现，别赌固定等待时长。
        probe2 = run_probe(PORT, CDP_PORT, """(async () => {
            const sleep = ms => new Promise(r => setTimeout(r, ms));
            await window.__openExtensionsTab();
            let btn = null;
            for (let i = 0; i < 25; i++) {
                btn = document.querySelector('[data-exttoggle="settings-ext"]');
                if (btn) break;
                await sleep(300);
            }
            if (!btn) return {found: false};
            const before = btn.getAttribute('data-on');
            btn.click();
            // toast 只留 2.4 秒 —— 得马上读
            await sleep(900);
            const note = (document.querySelector('#notice') || {}).textContent || '';
            return {found: true, before, note};
        })()""")
        r.check("扩展行上找得到启停按钮", bool(probe2.get("found")))
        r.check("点启停有提示", bool(str(probe2.get("note", "")).strip()),
                str(probe2.get("note", "")))

        overview = _api(PORT, "/api/extensions")
        rec = next((x for x in overview.get("extensions", [])
                    if x.get("id") == "settings-ext"), None)
        r.check("启停后状态真的变了", bool(rec) and bool(rec.get("disabled")),
                str(rec)[:160])

        # 恢复原状 —— 别污染后续测试
        probe3 = run_probe(PORT, CDP_PORT, """(async () => {
            const sleep = ms => new Promise(r => setTimeout(r, ms));
            await window.__openExtensionsTab();
            let btn = null;
            for (let i = 0; i < 25; i++) {
                btn = document.querySelector('[data-exttoggle="settings-ext"]');
                if (btn) break;
                await sleep(300);
            }
            if (!btn) return {found: false};
            btn.click();
            await sleep(1500);
            return {found: true};
        })()""")
        r.check("能再点回来", bool(probe3.get("found")))
        overview2 = _api(PORT, "/api/extensions")
        rec2 = next((x for x in overview2.get("extensions", [])
                     if x.get("id") == "settings-ext"), None)
        r.check("恢复后不再是停用状态",
                bool(rec2) and not rec2.get("disabled"), str(rec2)[:160])

        # ── 重新加载 ──────────────────────────────────────────────
        probe4 = run_probe(PORT, CDP_PORT, """(async () => {
            const sleep = ms => new Promise(r => setTimeout(r, ms));
            await window.__openExtensionsTab();
            const btn = document.querySelector('#extReload');
            if (!btn) return {found: false};
            btn.click();
            await sleep(900);
            const note = (document.querySelector('#notice') || {}).textContent || '';
            await sleep(1500);
            return {found: true, note,
                    rows: document.querySelectorAll('#page table tr').length};
        })()""")
        r.check("有「重新加载」按钮可点", bool(probe4.get("found")))
        r.check("点重载有提示", bool(str(probe4.get("note", "")).strip()),
                str(probe4.get("note", "")))
        r.check("重载后表格还在", int(probe4.get("rows") or 0) > 0,
                str(probe4.get("rows")))

        return r.summary()
    finally:
        # 兜底恢复 —— 万一上面某一步失败，扩展可能停在「停用」状态。
        # 留着不管的话，同一次运行后面的检查会被带偏。
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{PORT}/api/extensions/settings-ext/enable",
                headers={"X-Oryx-Token": TOKEN, "X-Oryx-Request": "1"},
                method="POST", data=b"")
            urllib.request.urlopen(req, timeout=10).read()
        except Exception:
            pass
        stop_server(server)


if __name__ == "__main__":
    sys.exit(main())
