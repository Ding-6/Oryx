"""检查点域：列出可回退的位置、执行回退。

回退这件事的语义全是「撤销」，所以这一域只有两个动作：
    list     列出这个会话有哪些检查点（界面上每轮下方的「回退到这里」按钮）
    rewind   退到某一个检查点

执行本身在内核进程里（CheckpointService 在 AgentRuntime 上，
它持有 allowed_roots 的判定），所以这一域只做参数校验 + RPC 转发。

回退范围三选一（与内核的工具口径一致）：
    Code                  只还原文件
    Conversation          只删后续对话
    CodeAndConversation   两者都退
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..agentlink import AgentLink
from ..ipc import IpcError

router = APIRouter(prefix="/api/checkpoints", tags=["检查点"])

SCOPES = ("Code", "Conversation", "CodeAndConversation")


# ---------- actions ----------
def list_checkpoints(agent: AgentLink, task_id: str) -> dict:
    if not task_id:
        return {"task_id": "", "current": None, "checkpoints": []}
    return _call(agent, "checkpoint.list", task_id=task_id)


def rewind_checkpoint(agent: AgentLink, checkpoint_id: str, scope: str) -> dict:
    """退到某个检查点。

    内核用 ok=false 表达"退不了"（检查点不存在、scope 不合法），
    这里转成 404 / 400 —— 前端要靠状态码区分"没得点"和"点错了"。
    """
    if not checkpoint_id:
        raise HTTPException(400, "checkpoint 不能为空")
    if scope not in SCOPES:
        raise HTTPException(400, f"scope 只能是 {' / '.join(SCOPES)}")
    result = _call(agent, "checkpoint.rewind", checkpoint=checkpoint_id, scope=scope)
    if not result.get("ok"):
        detail = str(result.get("content") or "回退失败")
        if "没有这个检查点" in detail:
            raise HTTPException(404, detail)
        raise HTTPException(400, detail)
    return result


def _call(agent: AgentLink, method: str, **params):
    try:
        return agent.call(method, timeout=120.0, **params)
    except IpcError as exc:
        raise _map(exc) from None


def _map(exc: IpcError) -> HTTPException:
    text = str(exc)
    if "没有这个检查点" in text:
        return HTTPException(404, text)
    return HTTPException(400, text)


# ---------- routes ----------
def register(app, agent: AgentLink) -> None:
    from fastapi.responses import JSONResponse

    @router.get("/{task_id}")
    async def _list(task_id: str):
        return JSONResponse(list_checkpoints(agent, task_id))

    @router.post("/{checkpoint_id}/rewind")
    async def _rewind(checkpoint_id: str, req: dict):
        scope = str((req or {}).get("scope") or "CodeAndConversation")
        return JSONResponse(rewind_checkpoint(agent, checkpoint_id, scope))

    app.include_router(router)
