"""运行中的主动建议与进度快照。

对齐 Marvis 的「任务推进中主动提示下一步、主动汇报进度」。
之前 Oryx 只有被动事件流：用户说完，界面滚一串步骤，跑完就没了。

**为什么用确定性规则而不是再调一次模型**：
1. 运行中每步都调模型做建议，成本和延迟都翻倍，还会把上下文越撑越大；
2. 建议必须是**可执行、路径明确**的，模型容易编出不存在的文件名；
3. 规则可测试、可回归 —— 这正是「不靠提示词自觉」的一贯做法。

建议只发给界面展示，绝不自动执行：用户点一下才变成下一轮输入。
"""

from __future__ import annotations

from typing import Any

MAX_SUGGESTIONS = 3

# 产物后缀 → 针对该格式的下一步。
# action 型建议由前端直接执行（如打开预览），不占用一轮对话。
_BY_EXTENSION: dict[str, list[dict[str, str]]] = {
    ".xlsx": [
        {"label": "画成图表", "hint": "把 {name} 里的数据画成一张柱状图，输出 PNG 图片并给我路径", "kind": "chart"},
        {"label": "做一份看板", "hint": "读一下 {name}，做一份自包含的 HTML 可视化看板，带表格和图表", "kind": "html"},
        {"label": "导出 PDF", "hint": "把 {name} 整理成一份带表格的 PDF 报告", "kind": "pdf"},
    ],
    ".csv": [
        {"label": "生成表格", "hint": "把 {name} 转成带表头的 Excel 表格", "kind": "xlsx"},
        {"label": "画成图表", "hint": "用 {name} 里的数据画一张折线图，输出 PNG", "kind": "chart"},
    ],
    ".html": [
        {"label": "在界面里预览", "action": "preview", "kind": "preview"},
        {"label": "导出 PDF 版", "hint": "把 {name} 的内容整理成一份可打印的 PDF 报告", "kind": "pdf"},
    ],
    ".htm": [
        {"label": "在界面里预览", "action": "preview", "kind": "preview"},
    ],
    ".png": [
        {"label": "插进报告", "hint": "把 {name} 这张图放进一份 HTML 报告里，配一段说明", "kind": "html"},
    ],
    ".svg": [
        {"label": "在界面里预览", "action": "preview", "kind": "preview"},
        {"label": "转成 PNG", "hint": "把 {name} 转成一张 PNG 位图", "kind": "image"},
    ],
    ".docx": [
        {"label": "导出 PDF", "hint": "把 {name} 的内容整理成一份可打印的 PDF", "kind": "pdf"},
        {"label": "做汇报 PPT", "hint": "根据 {name} 生成一份 5 页左右的演示文稿", "kind": "pptx"},
    ],
    ".pptx": [
        {"label": "生成讲义", "hint": "把 {name} 每页的要点整理成一份 Word 讲义", "kind": "docx"},
    ],
    ".pdf": [
        {"label": "做个网页版", "hint": "把 {name} 的内容做成一份自包含的 HTML 页面", "kind": "html"},
    ],
}

# 没有产物、但已经调研过一轮时，给「把结论落成文件」的建议。
_RESEARCH_TOOLS = frozenset({
    "index_search", "fs_grep", "fs_glob", "search_files", "fs_read",
    "browser_extract", "browser_links", "doc_read", "xlsx_read",
})


def _path_from(args: dict[str, Any], result: dict[str, Any]) -> str:
    for key in ("path", "image_path", "output", "file", "dest"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    content = result.get("content")
    if isinstance(content, dict):
        for key in ("path", "image_path", "output", "file"):
            value = content.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def suggest(
    *,
    tool: str,
    args: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    ok: bool = True,
    artifacts: list[str] | None = None,
) -> list[dict[str, Any]]:
    """根据刚发生的一步，给出最多 3 条可点的下一步建议。

    返回结构：``{"label", "prompt" | "action", "path", "kind"}``。
    ``action`` 型由前端直接执行（不发起新一轮对话）。
    """
    args = args or {}
    result = result or {}
    path = _path_from(args, result)
    out: list[dict[str, Any]] = []

    if not ok:
        return [{
            "label": "诊断并换路径",
            "prompt": f"刚才调用 {tool} 失败了。先按失败分类说明原因，再换一条能走通的路径重试，不要重复同样的参数。",
            "path": "",
            "kind": "recover",
        }]

    suffix = ""
    if path:
        dot = path.rfind(".")
        suffix = path[dot:].lower() if dot >= 0 else ""
    name = path.replace("\\", "/").rsplit("/", 1)[-1] or "这份文件"

    for template in _BY_EXTENSION.get(suffix, []):
        item: dict[str, Any] = {
            "label": template["label"],
            "kind": template["kind"],
            "path": path,
        }
        if template.get("action"):
            item["action"] = template["action"]
        else:
            item["prompt"] = template["hint"].format(name=name)
        out.append(item)
        if len(out) >= MAX_SUGGESTIONS:
            return out

    if tool in _RESEARCH_TOOLS and not path:
        out.append({
            "label": "汇总成报告",
            "prompt": "把刚才查到的内容整理成一份自包含的 HTML 报告，带表格，并告诉我落盘路径",
            "path": "",
            "kind": "html",
        })

    # 已经攒了多个产物：提示可以打包交付
    seen = [p for p in (artifacts or []) if isinstance(p, str)]
    if len(seen) >= 3:
        out.append({
            "label": "整理交付清单",
            "prompt": "把这一轮生成的所有产物按用途列一张清单，标出每个文件的绝对路径和用途",
            "path": "",
            "kind": "summary",
        })
    return out[:MAX_SUGGESTIONS]


def progress_snapshot(
    *, turn: int, completed: list[str], artifacts: list[str], current: str = ""
) -> dict[str, Any]:
    """结构化进度快照（给总览面板，不是给模型当旁白）。

    系统提示词明令禁止「正在处理中」这类无信息量的播报，
    所以这里只给可核对的数字：第几轮、完成几步、产出几个文件、当前在做什么。
    """
    return {
        "turn": int(turn),
        "steps": len(completed),
        "artifacts": len(artifacts),
        "current": current,
        "recent": list(completed[-5:]),
        "label": f"第 {int(turn) + 1} 轮 · 已完成 {len(completed)} 步 · 产出 {len(artifacts)} 个文件",
    }
