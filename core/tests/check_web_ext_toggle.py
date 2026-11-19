"""界面「停用」对**技能/专家**是否真的生效的真实验证。

这一节是**用户可见**的 —— 与其相信代码写对了，不如在真浏览器里点一遍：

  1. 起服务，装一个技能 + 一个专家
  2. 打开设置页 →「扩展」标签
  3. 点「停用」→ 检查**接口返回的扩展清单**里它标成了停用
  4. 检查**技能目录 / 专家清单**里它真的不见了
  5. 点「启用」→ 恢复

第 4 步是重点 —— 以前停用只让注册表里少了一条，
skill/expert 两个库照常留着，用户点了跟没点一样。

运行：python tests/check_web_ext_toggle.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _webcheck import describe_busy, ports_free  # noqa: E402
from _webdrive import (Result, run_probe, start_server,  # noqa: E402
                        stop_server, wait_ready)

PORT = 8786
CDP_PORT = 9349
TOKEN = "ext-toggle-check"

SKILL_MD = """---
name: probe-skill
description: 停用验证用的探针技能
triggers: 探针,probe
---

# 探针技能

这是一个用来验证「停用是否真生效」的技能。
停用它之后，它应当从技能清单里消失。
"""

EXPERT_MD = """---
name: probe-expert
description: 停用验证用的探针专家
---

# 探针专家

停用技能时**它不该受影响** —— 用来验证停用只影响被停用的那一个。
"""


def _api(port: int, path: str) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        headers={"X-Oryx-Token": TOKEN})
    return json.loads(urllib.request.urlopen(req, timeout=15).read().decode())


def _skill_names(port: int) -> list[str]:
    """技能名列表。

    `/api/skills` 返回的是 `{"skills": [{name, description, path}, …]}` ——
    直接拿它做 `in` 判断会查 dict 的 key，永远是 False。
    """
    data = _api(port, "/api/skills")
    return [s.get("name", "") for s in data.get("skills", [])]


def main() -> int:
    if not ports_free(PORT):
        print(f"端口被占用（{describe_busy(PORT)}）—— 跳过"
              "（可能有上次的测试进程没退出）")
        return 0

    home = Path(tempfile.mkdtemp())
    skill_dir = home / "skills" / "probe-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    # 第二个技能 —— 用来验证「停用一个不影响另一个」
    other_dir = home / "skills" / "other-skill"
    other_dir.mkdir(parents=True)
    (other_dir / "SKILL.md").write_text(
        SKILL_MD.replace("probe-skill", "other-skill"), encoding="utf-8")

    server = start_server(PORT, home, TOKEN)
    try:
        if not wait_ready(PORT):
            print("服务没起来")
            return 1

        r = Result()
        names = _skill_names(PORT)
        r.check("装好后技能在清单里", "probe-skill" in names, str(names))

        # ── 界面上的「停用」按钮：真点一次 ────────────────────────
        probe = run_probe(PORT, CDP_PORT, """(async () => {
            const sleep = ms => new Promise(r => setTimeout(r, ms));
            await window.__openExtensionsTab();
            // 等扩展表格**渲染出来** —— 它是异步的（要拉 /api/extensions）。
            // 直接查会落在还没画完的空表上，表现为「时好时坏」。
            for (let i = 0; i < 40; i++) {
                if (document.querySelector('[data-extremove="normal-ext"]')) break;
                await sleep(300);
            }
            const toggle = document.querySelector('[data-exttoggle="probe-skill"]');
            if (!toggle) {
                return {found: false,
                        rows: Array.from(
                            document.querySelectorAll('[data-exttoggle]'))
                            .map(b => b.getAttribute('data-exttoggle'))};
            }
            const wasOn = toggle.getAttribute('data-on');
            toggle.click();
            await sleep(2000);
            return {found: true, wasOn};
        })()""")

        r.check("扩展清单里找得到这个技能", bool(probe.get("found")),
                str(probe.get("rows", ""))[:200])

        # ── 停用后：技能目录里它没了，另一个不受影响 ────────────────
        after = _skill_names(PORT)
        r.check("停用后技能从技能清单里消失", "probe-skill" not in after,
                str(after))
        r.check("没停用的技能不受影响", "other-skill" in after, str(after))

        # 扩展总览里它标成了停用
        overview = _api(PORT, "/api/extensions")
        recs = overview.get("extensions", [])
        rec = next((x for x in recs if x.get("id") == "probe-skill"), None)
        r.check("扩展总览里它标成了停用",
                bool(rec) and bool(rec.get("disabled")), str(rec)[:200])

        # ── 恢复：点「启用」 ──────────────────────────────────────
        probe2 = run_probe(PORT, CDP_PORT, """(async () => {
            const sleep = ms => new Promise(r => setTimeout(r, ms));
            await window.__openExtensionsTab();
            // 等扩展表格**渲染出来** —— 它是异步的（要拉 /api/extensions）。
            for (let i = 0; i < 40; i++) {
                if (document.querySelector('[data-exttoggle="probe-skill"]')) break;
                await sleep(300);
            }
            const toggle = document.querySelector('[data-exttoggle="probe-skill"]');
            if (!toggle) return {found: false};
            toggle.click();
            await sleep(2000);
            return {found: true};
        })()""")
        r.check("扩展清单里找得到它（恢复时）", bool(probe2.get("found")))

        restored = _skill_names(PORT)
        r.check("启用后技能回到清单里", "probe-skill" in restored, str(restored))

        return r.summary()
    finally:
        stop_server(server)


if __name__ == "__main__":
    sys.exit(main())
