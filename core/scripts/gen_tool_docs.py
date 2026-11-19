"""生成 `docs/TOOLS.md` —— 全部工具的清单。

为什么要有这个
--------------
工具是**分散注册**的：

* `tools.py` 的 `build_registry()` 给 10 个基础工具
* `loop.py` 里 21 个 `_register_*` 方法再陆续加 60 多个
* MCP server 的工具最后到
* 用户装的扩展还能再加

所以「这个项目到底有哪些工具」这个问题，**读代码是答不出来的** ——
得跑起来看。而跑起来看只能看一次，下次工具集变了就不知道了。

这份清单从**真实运行时**导出（不是手写），所以它跟实现永远一致。
`tests/test_tool_docs.py` 守着它 —— 工具集变了而清单没跟上，测试会红。

用法
----
    python core/scripts/gen_tool_docs.py          # 写 docs/TOOLS.md
    python core/scripts/gen_tool_docs.py --check  # 只检查是否最新（CI 用）
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
REPO = CORE.parent
OUT = REPO / "docs" / "TOOLS.md"

sys.path.insert(0, str(CORE))

# 风险等级 → 人话
RISK_LABEL = {
    0: "只读",
    1: "可逆写入",
    2: "不可逆写入",
    3: "破坏性",
}

# 分组 → 一句话说明（这是给人看的，脚本不认识语义）
GROUP_NOTE = {
    "list": "目录与文件列表",
    "read": "读文件内容",
    "write": "写文件",
    "delete": "删除文件",
    "move": "移动/改名",
    "glob": "按模式找文件",
    "grep": "按内容搜文件",
    "search": "本地内容索引与语义检索",
    "shell": "执行命令",
    "sandbox": "命令沙箱",
    "system": "系统信息",
    "system_settings": "Windows 系统设置",
    "memory": "长期记忆",
    "notify": "通知渠道",
    "prompt": "提示词管理",
    "provider": "模型接入",
    "automation": "定时任务",
    "browser": "浏览器 Agent",
    "docx": "Word 文档",
    "xlsx": "Excel 表格",
    "pptx": "PPT 演示",
    "pdf": "PDF 生成",
    "html": "HTML 生成",
    "image": "图片生成",
    "ocr": "图片文字识别",
    "doc": "项目文档（需求/决策/变更）",
    "agent": "子 Agent",
    "spawn": "派发子 Agent",
    "use": "技能调用",
    "defer": "延迟执行",
    "term": "内嵌终端",
    "remote": "跨端远控",
    "workflow": "工作流",
    "list_agents": "Agent 名册",
    "tool": "工具检索",
    "workspace": "工作目录",
}


def collect() -> list[dict]:
    """从真实运行时收集工具清单。

    **只收 `builtin`** —— MCP 和用户扩展是**因环境而异**的
    （装没装 MCP server、装没装扩展都会变）。
    仓库里的清单必须稳定，否则测试会随环境忽红忽绿。
    那两类来源在文档里另有说明（见「注册结构」那节）。
    """
    from oryx.config import load_config
    from oryx.loop import AgentRuntime

    cfg = load_config()
    runtime = AgentRuntime(cfg, None, None, autostart_scheduler=False)
    table = runtime.tool_table.to_dict()
    tools = [
        t for t in (table.get("tools") or [])
        if str(t.get("source") or "") == "builtin"
    ]
    # 按名字排 —— 清单的顺序要稳定，否则每次生成 diff 都是噪音
    tools.sort(key=lambda t: (str(t.get("group") or ""), str(t.get("name") or "")))
    return tools


def register_map() -> list[tuple[str, int, str]]:
    """从源码里提取 `_register_*` 方法与它们的行号 —— 用于「注册结构」那节。

    自动提取而不是手写：手写的清单必然会和代码脱节。
    """
    src = (CORE / "oryx" / "loop.py").read_text("utf-8")
    out = []
    for m in re.finditer(r"^    def (_register_\w+)\(self\)", src, re.M):
        line = src[: m.start()].count("\n") + 1
        out.append((m.group(1), line, m.group(1)[len("_register_"):]))
    return out


def render(tools: list[dict]) -> str:
    total = len(tools)
    by_source = Counter(str(t.get("source") or "?") for t in tools)
    by_risk = Counter(str(t.get("risk_label") or "?") for t in tools)
    by_group: dict[str, list[dict]] = defaultdict(list)
    for t in tools:
        by_group[str(t.get("group") or "other")].append(t)

    L: list[str] = []
    L.append("# 工具清单")
    L.append("")
    L.append("> **自动生成** —— 别手改。改完代码跑：")
    L.append(">")
    L.append("> ```bash")
    L.append("> python core/scripts/gen_tool_docs.py")
    L.append("> ```")
    L.append(">")
    L.append("> `core/tests/test_tool_docs.py` 守着它跟运行时一致 ——")
    L.append("> 工具集变了而这份清单没跟上，测试会红。")
    L.append("")
    L.append("## 概览")
    L.append("")
    L.append(f"- 工具总数：**{total}**")
    L.append("- 来源：" + "、".join(
        f"{k} {v}" for k, v in sorted(by_source.items())))
    L.append("- 风险：" + "、".join(
        f"{k} {v}" for k, v in sorted(by_risk.items())))
    L.append(f"- 分组：**{len(by_group)}** 个")
    L.append("")
    L.append("> 来源的含义：")
    L.append(">")
    L.append("> | 来源 | 是什么 |")
    L.append("> |---|---|")
    L.append("> | `builtin` | 产品内置 |")
    L.append("> | `mcp` | MCP server 提供的（`mcp__<server>__<name>`） |")
    L.append("> | `extension` | 扩展提供的（用户装的后端扩展） |")
    L.append("")
    L.append("## 按分组列出")
    L.append("")
    for group in sorted(by_group):
        items = by_group[group]
        note = GROUP_NOTE.get(group, "")
        L.append(f"### `{group}`" + (f" —— {note}" if note else ""))
        L.append("")
        L.append("| 工具 | 说明 | 风险 | 会改动 |")
        L.append("|---|---|---|---|")
        for t in items:
            # 表格单元格**不能有换行** —— 会把 Markdown 表格拆坏。
            # 描述里带换行的不少（比如 workflow_run），压成空格。
            desc = " ".join(str(t.get("description") or "").split())
            desc = desc.replace("|", "\\|")
            risk = str(t.get("risk_label") or "?")
            mut = "是" if t.get("mutates") else "—"
            src_detail = str(t.get("source_detail") or "")
            name = str(t.get("name") or "")
            if src_detail:
                name = f"`{name}`<br><sub>{t.get('source')}/{src_detail}</sub>"
            else:
                name = f"`{name}`"
            L.append(f"| {name} | {desc} | {risk} | {mut} |")
        L.append("")
    return "\n".join(L) + "\n"


def render_registration(tools: list[dict]) -> str:
    """「注册结构」那节 —— 从源码自动提取。"""
    L: list[str] = []
    L.append("## 注册结构：工具是怎么进来的")
    L.append("")
    L.append("工具**分散注册**，所以「有哪些工具」读代码答不出来。")
    L.append("")
    L.append("来源一共四条：")
    L.append("")
    L.append("| 来源 | 入口 | 说明 |")
    L.append("|---|---|---|")
    L.append("| 基础工具 | `core/oryx/tools.py` 的 `build_registry()` | "
             "文件读写、搜索、shell、系统信息等 |")
    L.append("| 内置扩展 | `core/oryx/loop.py` 的 `_register_*` 方法 | "
             "下面这张表 |")
    L.append("| MCP | `loop.py` 的 `_register_mcp_tools()` | "
             "名字带 `mcp__<server>__` 前缀 |")
    L.append("| 用户扩展 | `core/oryx/toolplug.py` | "
             "装到用户目录的扩展，可信且允许执行时才注册 |")
    L.append("")
    L.append("全部登记完之后，`AgentRuntime.refresh_tool_table()` 重建"
             "**统一工具表** —— 三种来源一张表，冲突按优先级裁定"
             "（内置 > MCP > 扩展）。")
    L.append("")
    L.append("### `_register_*` 一览")
    L.append("")
    L.append("| 方法 | 负责 |")
    L.append("|---|---|")
    for name, line, _ in register_map():
        L.append(f"| `{name}()`<br><sub>loop.py:{line}</sub> | |")
    L.append("")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="只检查文件是否最新，不写（CI 用）")
    args = ap.parse_args()

    tools = collect()
    text = render(tools) + "\n" + render_registration(tools)

    if args.check:
        if not OUT.exists():
            print(f"{OUT} 不存在 —— 跑 python core/scripts/gen_tool_docs.py 生成")
            return 1
        old = OUT.read_text("utf-8")
        if old != text:
            print(f"{OUT} 与运行时不一致 —— 跑 python core/scripts/gen_tool_docs.py")
            return 1
        print(f"{OUT.name} 与运行时一致（{len(tools)} 个工具）")
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"已写 {OUT}（{len(tools)} 个工具）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
