"""大屏域：多 Agent 一屏总览。

对外三件事：
    GET  /api/overview/teams        编队总览：团队、成员、可用的子 Agent
    GET  /api/overview/runs         运行总览：最近运行 + 整体计数
    GET  /api/overview/suggestions  主动建议：上一轮产出之后，下一步做什么

为什么要有这三个接口
--------------------
大屏（多 Agent 一屏总览）此前**一个接口都不调** ——
它纯渲染 SSE 流里攒出来的内存状态。后果是：
**刷新一下页面，总览就空了。**

真相其实都在服务端，只是没人给前端一个取的口子：

    · 历史运行在 **oryx.db**（tasks 表）
    · 编队与子 Agent 在**内核**（team.list / catalog.agents）
    · 产物路径在**步骤**里（steps.args / steps.result）

这三个接口就是那个口子。前端仍然是 SSE 驱动的（实时性靠它），
这三个接口负责**冷启动与恢复** —— 页面打开或刷新时先拉一份底，
之后由 SSE 增量覆盖。两边不冲突：SSE 说「现在」，接口说「本来」。

**降级**：内核没起来时，teams/suggestions 返回空列表而不是 500 ——
大屏是「锦上添花」的视图，它挂了不该拖垮别的接口，
更不该让整屏变成一个红色错误框。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter

from ..api.response import ok
from ..ipc import IpcError
from ..store import Store
from ..suggestions import MAX_SUGGESTIONS, artifact_paths, suggest

router = APIRouter(prefix="/api/overview", tags=["大屏总览"])

# 最多回看多少个步骤来挖产物。
_SCAN_STEPS = 40


# ── teams ───────────────────────────────────────────────────────────


async def _call(agent, action: str, **kwargs) -> tuple[Any, bool]:
    """调一次内核 RPC。

    返回 ``(结果, 是否降级)`` —— 降级时结果是 None，
    由调用方决定是给空列表还是报错。
    """
    try:
        return await agent.call_async(action, **kwargs), False
    except IpcError:
        return None, True
    except Exception:
        # 内核没起来 / 管道断了 —— 一律降级，不往上抛。
        return None, True


async def overview_teams(agent) -> dict:
    """编队总览：现在有哪些团队、成员是谁、有哪些子 Agent 可以调。"""
    degraded: list[str] = []

    raw_teams, bad = await _call(agent, "team.list")
    if bad:
        degraded.append("teams")
        raw_teams = None
    teams_raw = raw_teams if isinstance(raw_teams, list) else []

    raw_agents, bad = await _call(agent, "catalog.agents")
    if bad:
        degraded.append("agents")
        raw_agents = None
    agents_raw = raw_agents if isinstance(raw_agents, list) else []

    # id → 子 Agent 元信息，用来把成员 id 翻成人看得懂的名字
    catalog: dict[str, dict[str, Any]] = {}
    agents_out: list[dict[str, Any]] = []
    for item in agents_raw:
        if not isinstance(item, dict):
            continue
        agent_id = str(item.get("id") or "")
        if not agent_id:
            continue
        catalog[agent_id] = item
        agents_out.append(
            {
                "id": agent_id,
                "name": str(item.get("name") or agent_id),
                "description": str(item.get("description") or ""),
                "kind": str(item.get("kind") or "agent"),
                "icon": str(item.get("icon") or ""),
                "subtitle": str(item.get("subtitle") or ""),
            }
        )

    teams_out: list[dict[str, Any]] = []
    for team in teams_raw:
        if not isinstance(team, dict):
            continue
        members = []
        for member in team.get("members") or []:
            if not isinstance(member, dict):
                continue
            agent_id = str(member.get("agent_id") or "")
            known = catalog.get(agent_id)
            members.append(
                {
                    "agent_id": agent_id,
                    "role": str(member.get("role") or "成员"),
                    # 名字从子 Agent 目录里查 —— 团队里存的是 id，
                    # 光看 id 认不出「researcher」是谁。
                    "name": str((known or {}).get("name") or agent_id),
                    "known": known is not None,
                }
            )
        teams_out.append(
            {
                "id": str(team.get("id") or ""),
                "name": str(team.get("name") or ""),
                "goal": str(team.get("goal") or ""),
                "members": members,
                "member_count": len(members),
                "message_count": int(team.get("message_count") or 0),
                "unread": int(team.get("unread") or 0),
                "created_at": team.get("created_at"),
            }
        )

    return {
        "teams": teams_out,
        "team_count": len(teams_out),
        "member_count": sum(t["member_count"] for t in teams_out),
        "agents": agents_out,
        "agent_count": len(agents_out),
        "degraded": degraded,
    }


# ── runs ────────────────────────────────────────────────────────────


def overview_runs(store: Store, limit: int = 20) -> dict:
    """运行总览：最近跑了什么 + 整体有多少。

    ``total`` / ``active`` / ``today`` 来自库里的聚合查询，
    **不是**从返回的列表里数的 —— 列表是限量的，数出来的是
    「最近 N 个里有几个」，跟「一共跑了多少」不是一回事。
    """
    limit = max(1, min(int(limit), 100))
    sessions = store.list_sessions(limit)
    stats = store.session_stats()

    runs: list[dict[str, Any]] = []
    for item in sessions:
        runs.append(
            {
                "id": str(item.get("id") or ""),
                "title": str(item.get("title") or ""),
                "goal": str(item.get("goal") or ""),
                "status": str(item.get("status") or ""),
                "mode": str(item.get("mode") or ""),
                "provider": str(item.get("provider") or ""),
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
                "msg_count": int(item.get("msg_count") or 0),
                "step_count": int(item.get("step_count") or 0),
                "todo_open": int(item.get("todo_open") or 0),
                "renamed": bool(item.get("renamed")),
            }
        )

    return {
        "runs": runs,
        "window": limit,
        "total": stats["total"],
        "active": stats["active"],
        "today": stats["today"],
    }


# ── suggestions ─────────────────────────────────────────────────────


def _loads(raw: Any) -> dict[str, Any]:
    """steps.args / steps.result 在库里是 JSON 文本，解不出就当空。"""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def overview_suggestions(store: Store, task_id: str = "") -> dict:
    """主动建议：上一轮产出之后，下一步做什么。

    走的是**确定性规则**（`suggestions.suggest`），不调模型 ——
    理由同 suggestions 模块：建议必须可执行、路径明确、可回归。

    给定 ``task_id`` 就针对那次运行；不给就用**最近一次运行**。
    """
    if task_id:
        session = next(
            (s for s in store.list_sessions(200) if s.get("id") == task_id), None
        )
    else:
        sessions = store.list_sessions(1)
        session = sessions[0] if sessions else None

    if not session:
        return {"suggestions": [], "count": 0, "source": {}}

    session_id = str(session.get("id") or "")
    steps = store.steps_for_task(session_id)

    # 先把这一轮的所有产物捞出来 —— 「攒了 3 个产物就提示打包交付」
    # 这种建议要看的是**整体**，不是最后一步。
    artifacts: list[str] = []
    seen: set[str] = set()
    for step in steps:
        for path in artifact_paths(_loads(step.get("args")), _loads(step.get("result"))):
            if path not in seen:
                seen.add(path)
                artifacts.append(path)

    out: list[dict[str, Any]] = []
    labels: set[str] = set()
    failed_fallback: list[dict[str, Any]] = []

    # 从**后往前**看步骤：最近产出的东西最能代表「现在到哪了」。
    for step in reversed(steps[-_SCAN_STEPS:]):
        tool = str(step.get("tool") or "")
        state = str(step.get("state") or "")
        args = _loads(step.get("args"))
        result = _loads(step.get("result"))
        ok_step = state != "failed"

        items = suggest(
            tool=tool,
            args=args,
            result=result,
            ok=ok_step,
            artifacts=artifacts,
        )
        if not ok_step:
            failed_fallback = items
            continue
        for item in items:
            label = str(item.get("label") or "")
            if not label or label in labels:
                continue
            labels.add(label)
            out.append(item)
        if len(out) >= MAX_SUGGESTIONS:
            break

    # 一个成功步骤都没产出建议 → 退回到「诊断失败」那条（如果有失败步骤）
    if not out:
        out = failed_fallback

    out = out[:MAX_SUGGESTIONS]
    return {
        "suggestions": out,
        "count": len(out),
        "source": {
            "task_id": session_id,
            "title": str(session.get("title") or ""),
            "status": str(session.get("status") or ""),
            "steps": len(steps),
            "artifacts": artifacts[:20],
            "artifact_count": len(artifacts),
        },
    }


# ── 装配 ────────────────────────────────────────────────────────────


def register(app, agent, store: Store) -> None:
    """把本域路由挂到 app。依赖用闭包注入，action 本身保持纯函数。"""

    @router.get("/teams")
    async def _teams():
        return ok(await overview_teams(agent))

    @router.get("/runs")
    async def _runs(limit: int = 20):
        return ok(overview_runs(store, limit))

    @router.get("/suggestions")
    async def _suggestions(task_id: str = ""):
        return ok(overview_suggestions(store, task_id))

    app.include_router(router)
