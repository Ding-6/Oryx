"""自然语言意图 → 工作流编排草案（补齐「意图 → 编排」中间层）。

诊断记录：``workflow_run`` 要求模型自己写出完整的 stages/steps JSON，
「帮我并行调研这三个仓库」要变成编排定义，全靠模型现场翻译。
WorkBuddy 的 Dynamic Workflows 允许宿主注入编排原语，模型只需表达结构意图。

这里补的是**确定性的草案层**：
1. 从自然语言里抽「并行子目标」或退化为「调研 → 核对 → 交付」流水线；
2. 每个子目标按关键词映射到**真实存在**的子 Agent（不会编造 id）；
3. 生成的草案立刻用 ``WorkflowPlan.parse`` 自检，保证能直接交给
   ``workflow_run``，而不是给一份看起来像 JSON 的废纸；
4. **不自动执行** —— 只返回草案与「哪些是推断的」说明，由模型/用户确认。

仍然坚持声明式数据，不 eval 模型写的脚本（那是 RCE 敞口）。
"""

from __future__ import annotations

import re
from typing import Any

MAX_DRAFT_STEPS = 8
MIN_PART_CHARS = 4

PARALLEL_HINTS = ("分别", "同时", "并行", "各自", "多个", "一批", "逐个", "每个",
                  "交叉", "三份", "两份", "两份以上", "all", "parallel")
COMPLEX_HINTS = ("整仓", "批量", "全量", "巡检", "分诊", "交叉", "多源", "汇总",
                 "重构", "架构", "深入", "详细", "全流程")

_SPLIT_RE = re.compile(r"[、,，;；\n]|\s+(?:以及|还有)\s+")
_LEAD_RE = re.compile(r"^(?:请|帮我|麻烦|同时|分别|并行|各自|逐个|然后|再|并|以及|还有|去|把|对|为)\s*")

_AGENT_HINTS: dict[str, tuple[str, ...]] = {
    "file-agent": ("文件", "目录", "整理", "生成", "导出", "代码", "仓库", "表格", "文档"),
    "computer-agent": ("系统", "设置", "进程", "命令", "硬件", "排障", "环境", "配置"),
    "app-agent": ("应用", "安装", "启动", "卸载", "软件", "程序"),
    "browser-agent": ("网页", "网址", "http", "浏览器", "页面", "网站"),
    "search-agent": ("搜索", "检索", "查资料", "信息", "资料", "来源"),
    "researcher": ("研究", "分析", "调研", "对比", "评估", "梳理"),
    "reviewer": ("审查", "检查", "核对", "评审", "验收", "复核"),
    "doc-manager": ("记录", "文档", "需求", "决策", "改动", "归档"),
}


def _clean(part: str) -> str:
    text = _LEAD_RE.sub("", part.strip()).strip()
    return text.strip("。.!！？? \"'")


def _split_goals(goal: str) -> list[str]:
    parts = [_clean(p) for p in _SPLIT_RE.split(goal) if p.strip()]
    return [p for p in parts if len(p) >= MIN_PART_CHARS][:MAX_DRAFT_STEPS]


def _pick_agent(text: str, available: list[str], fallback: str) -> str:
    best, best_hits = "", 0
    for name, keywords in _AGENT_HINTS.items():
        if name not in available:
            continue
        hits = sum(1 for kw in keywords if kw in text)
        if hits > best_hits:
            best, best_hits = name, hits
    return best or fallback


def draft_workflow(goal: str, agent_ids: list[str] | None = None, name: str = "") -> dict[str, Any]:
    """生成一份可直接交给 workflow_run 的编排草案。

    返回 ``{"ok", "draft", "valid", "confidence", "notes", "total_steps"}``。
    """
    from .workflow import WorkflowError, WorkflowPlan

    goal = str(goal or "").strip()
    if not goal:
        return {"ok": False, "error": "goal 不能为空"}
    available = [str(a) for a in (agent_ids or []) if str(a).strip()]
    if not available:
        return {"ok": False, "error": "当前没有可用的子 Agent，无法生成编排草案"}
    fallback = available[0]
    notes: list[str] = []

    parts = _split_goals(goal)
    parallel = len(parts) >= 2 and (
        any(h in goal for h in PARALLEL_HINTS) or len(parts) >= 3
    )

    if parallel:
        stages = [{
            "name": "并行执行",
            "steps": [
                {
                    "id": f"s{i}",
                    "name": part[:40],
                    "agent": _pick_agent(part, available, fallback),
                    "goal": f"{part}。围绕总目标「{goal[:80]}」给出可核对的结论与证据。",
                    "needs": [],
                    "parallel": True,
                }
                for i, part in enumerate(parts)
            ],
        }]
        confidence = "high" if any(h in goal for h in PARALLEL_HINTS) else "medium"
        notes.append(f"从原始需求里识别出 {len(parts)} 个并列子目标，排在同一阶段并行执行。")
        notes.append("子 Agent 是按关键词推断的，请确认分工后再执行。")
    else:
        # 没有明确并列结构：给一条保守的串行流水线，而不是硬凑并行
        stages = [
            {"name": "调研", "steps": [{
                "id": "s0", "name": "收集信息与证据",
                "agent": _pick_agent(goal, available, fallback),
                "goal": f"围绕「{goal[:120]}」收集事实、路径与数据，输出结构化要点。",
                "needs": [], "parallel": False,
            }]},
            {"name": "核对", "steps": [{
                "id": "s1", "name": "复核结论",
                "agent": "reviewer" if "reviewer" in available else fallback,
                "goal": "复核上一阶段的结论：指出证据不足、互相矛盾或推断过度的部分，给出修正建议。",
                "needs": ["s0"], "parallel": False,
            }]},
            {"name": "交付", "steps": [{
                "id": "s2", "name": "汇总交付",
                "agent": "doc-manager" if "doc-manager" in available else fallback,
                "goal": "把前两阶段的结论整理成一份可交付的汇总，标明产物路径与未完成项。",
                "needs": ["s1"], "parallel": False,
            }]},
        ]
        confidence = "low"
        notes.append("原需求没有明显的并列结构，草案退化为「调研 → 核对 → 交付」串行流水线。")
        notes.append("如果任务其实很小，直接用 spawn_agent 单派一个子 Agent 更省事；"
                     "这份草案只是给你一个可改的起点。")
        if any(h in goal for h in COMPLEX_HINTS):
            confidence = "medium"
            notes.append("检测到整仓/批量/多源类信号，适合用编排隔离中间结果。")

    draft = {
        "name": (name or goal[:24] or "编排草案"),
        "goal": goal,
        "stages": stages,
    }
    try:
        parsed = WorkflowPlan.parse(draft)
    except WorkflowError as exc:
        return {"ok": False, "error": f"草案自检失败：{exc}", "draft": draft}
    # 用 parse 后的规范结构回吐，保证字段与 workflow_run 完全对称
    return {
        "ok": True,
        "draft": parsed.to_dict(),
        "valid": True,
        "confidence": confidence,
        "notes": notes,
        "total_steps": parsed.total_steps,
        "hint": "确认草案后把 draft 原样交给 workflow_run；不要自己重写 stages。",
    }
