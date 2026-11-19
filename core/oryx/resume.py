"""断点续跑：内核崩溃重启后，从最后一个成功步骤继续，而不是整体重跑。

诊断记录：``agentlink._handle_crash`` 原来是 ``chat.run(**pending)``
—— 已经跑完的工具调用全部重来一遍，用户看着进度条从 0 开始，
还可能重复写文件、重复发通知。

做法（不重放工具，只续上下文）：
1. 重启后的内核在 SQLite 里找**最近一个仍是 running 且有执行痕迹**的 task；
   崩溃来不及写 done，这个状态就是天然的中断标记；
2. 把已完成步骤（tool / 结果摘要 / 产物路径）整理成一段 brief 注入系统提示，
   明确要求模型「不要重复执行这些步骤，从第一个未完成处继续」；
3. 复用同一个 task（消息与检查点都还在），并跳过重复写 user 消息；
4. 没有可续的断点（崩在第一步之前）就老老实实回退成整体重跑。

为什么不重放工具结果：工具可能有副作用，且上次的上下文（审批、模型状态）
已经随进程消失。语义级续跑 + 明确产物路径，比假装能精确重放更安全。
"""

from __future__ import annotations

import json
import time
from typing import Any

# 只认最近半小时内中断的任务，避免把很久以前卡住的老会话误当成本次崩溃
INTERRUPTED_WINDOW_SECONDS = 30 * 60
MAX_BRIEF_STEPS = 20
MAX_RESULT_CHARS = 220


def find_interrupted_task(store: Any, within_seconds: int = INTERRUPTED_WINDOW_SECONDS) -> str:
    """找最近一个「还在 running、且确实跑过步骤」的任务。"""
    now = time.time()
    try:
        tasks = store.recent_tasks(limit=20)
    except Exception:
        return ""
    for task in tasks:
        if task.get("status") != "running":
            continue
        try:
            age = now - float(task.get("updated_at") or task.get("created_at") or 0)
        except (TypeError, ValueError):
            continue
        if age < 0 or age > within_seconds:
            continue
        task_id = str(task.get("id") or "")
        if not task_id:
            continue
        try:
            if store.steps_for_task(task_id):
                return task_id
        except Exception:
            continue
    return ""


def _result_snippet(raw: Any) -> str:
    if raw in (None, ""):
        return "（无输出）"
    text = str(raw)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            # 产物路径是续跑最需要的信息，优先摘出来
            path = parsed.get("path") or parsed.get("image_path") or parsed.get("output")
            if path:
                return f"产物 {path}"
            text = json.dumps(parsed, ensure_ascii=False)
    except (TypeError, ValueError):
        pass
    return " ".join(text.split())[:MAX_RESULT_CHARS]


def build_resume_brief(store: Any, task_id: str) -> dict[str, Any]:
    """把已完成步骤整理成给模型看的续跑说明。"""
    if not task_id:
        return {"task_id": "", "completed": 0, "has_progress": False, "brief": ""}
    try:
        steps = store.steps_for_task(task_id)
    except Exception:
        steps = []
    done = [s for s in steps if s.get("state") == "done"]
    if not done:
        return {"task_id": task_id, "completed": 0, "has_progress": False, "brief": ""}

    lines = []
    for step in done[:MAX_BRIEF_STEPS]:
        tool = str(step.get("tool") or "工具")
        lines.append(f"- {tool}：{_result_snippet(step.get('result'))}")
    brief = (
        "# 断点续跑（内核崩溃后恢复）\n"
        f"这个任务此前已成功执行 {len(done)} 个步骤，以下是真实结果：\n"
        + "\n".join(lines) + "\n\n"
        "**不要重复执行上述步骤**，也不要重新生成已经存在的产物。"
        "从第一个未完成的环节继续，直接沿用上面的产物路径；"
        "确实需要重新读取时优先用只读工具核实文件是否还在。\n"
    )
    return {
        "task_id": task_id,
        "completed": len(done),
        "last_tool": str(done[-1].get("tool") or ""),
        "has_progress": True,
        "brief": brief,
    }
