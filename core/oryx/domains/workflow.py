"""工作流域：编排 run 的发起、查询与中止。

编排引擎跑在内核进程里（它要派子 Agent、调模型），
所以这一域只做参数校验 + RPC 转发。执行路径仍走内核的工具层，
风险评级与审计都留在工具层。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..agentlink import AgentLink
from ..ipc import IpcError
from ._common import is_kernel_unready

router = APIRouter(prefix="/api/workflows", tags=["工作流"])

# 一次编排可能跑很久（几十个子 Agent 串起来就是几分钟），
# 不能用默认的 600s IPC 超时
RUN_TIMEOUT = 3600.0


class WorkflowRunRequest(BaseModel):
    plan: dict
    args: dict | None = None


class WorkflowTemplateRequest(BaseModel):
    id: str = ""
    name: str = ""
    description: str = ""
    plan: dict
    args: dict | None = None
    created_at: float | None = None
    # 别名：除了 id 与名字，还能用别名取这份模板。
    # 抄 Marvis 的模板别名 —— 用户能记住「巡检」比记住「tpl_a3f9…」容易得多。
    aliases: list[str] | None = None


def run_workflow(agent: AgentLink, req: WorkflowRunRequest) -> dict:
    return _call(agent, "workflow.run", plan=req.plan, args=req.args or {}, timeout=RUN_TIMEOUT)


def list_workflows(agent: AgentLink, limit: int = 20) -> dict:
    return _call(agent, "workflow.list", limit=limit)


def workflow_detail(agent: AgentLink, run_id: str) -> dict:
    d = _call(agent, "workflow.status", run_id=run_id)
    # 内核用 ok=false 表达"没这次运行"，但 HTTP 层要回 404 ——
    # 前端 openWorkflowDetail 判的是 content 是否为空，返回 200 + 文本
    # 会让它当成正常结果渲染出一个空弹层。
    if not d.get("ok"):
        raise HTTPException(404, str(d.get("content") or "没有这次运行"))
    return d


def workflow_plan(agent: AgentLink, run_id: str) -> dict:
    """取某次 run 用的编排定义 —— "把这次运行存成模板"的数据来源。"""
    d = _call(agent, "workflow.plan", run_id=run_id)
    if not d.get("ok"):
        raise HTTPException(404, str(d.get("content") or "没有这次运行"))
    return d


def cancel_workflow(agent: AgentLink, run_id: str) -> dict:
    return _call(agent, "workflow.cancel", run_id=run_id)


def delete_workflow(agent: AgentLink, run_id: str) -> dict:
    if not _call(agent, "workflow.delete", run_id=run_id).get("ok"):
        raise HTTPException(404, "没有这次运行")
    return {"deleted": run_id}


def prune_failed_workflows(agent: AgentLink) -> dict:
    """批量清理全失败的 run。全失败判定在库侧做，不受列表分页影响。"""
    return _call(agent, "workflow.prune_failed")


def failed_workflow_count(agent: AgentLink) -> dict:
    """全失败的 run 有多少个。前端据此决定要不要显示清理按钮。"""
    return _call(agent, "workflow.failed_count")


# ---------- 模板 ----------
def list_templates(agent: AgentLink) -> dict:
    return _call(agent, "workflow.template.list")


def get_template(agent: AgentLink, template_id: str) -> dict:
    d = _call(agent, "workflow.template.get", template_id=template_id)
    if not d.get("ok"):
        raise HTTPException(404, str(d.get("content") or "没有这个模板"))
    return d


def save_template(agent: AgentLink, req: WorkflowTemplateRequest) -> dict:
    """存一份模板。

    id 留空就按名字生成 —— 用户不该被迫自己想一个 id。
    """
    template_id = (req.id or "").strip() or _slug(req.name)
    if not template_id:
        raise HTTPException(400, "模板要有名字")
    payload = {
        "id": template_id,
        "name": req.name or template_id,
        "description": req.description,
        "plan": req.plan,
        "args": req.args or {},
        "aliases": list(req.aliases or []),
    }
    if req.created_at:
        payload["created_at"] = req.created_at
    d = _call(agent, "workflow.template.save", item=payload)
    if not d.get("ok"):
        raise HTTPException(400, str(d.get("content") or "模板有问题"))
    return d


def delete_template(agent: AgentLink, template_id: str) -> dict:
    if not _call(agent, "workflow.template.delete", template_id=template_id).get("ok"):
        raise HTTPException(404, "没有这个模板")
    return {"deleted": template_id}


def run_template(agent: AgentLink, template_id: str, args: dict | None = None) -> dict:
    """按模板跑。阻塞语义与直接发起一致 —— 内核一次只能跑一个编排。"""
    return _call(agent, "workflow.template.run", template_id=template_id,
                 args=args or {}, timeout=RUN_TIMEOUT)


def _slug(name: str) -> str:
    """从模板名生成一个可用的 id。

    保留中英文数字，其余换成横线 —— 中文 id 在 URL 里能用，但要 encode，
    内部当路径参数传反而麻烦。
    """
    import re
    import time

    text = (name or "").strip()
    out = re.sub(r"[^\w\-一-鿿]+", "-", text, flags=re.UNICODE).strip("-").lower()
    out = out[:48]
    if not out:
        out = f"tpl-{int(time.time() * 1000) % 1000000}"
    return out


def _call(agent: AgentLink, method: str, **params):
    try:
        return agent.call(method, **params)
    except IpcError as exc:
        raise _map(exc) from None


def _map(exc: IpcError) -> HTTPException:
    text = _clean(str(exc))
    # 「内核未就绪」是 503 —— 服务端依赖没就绪，不是请求写错了。
    # 规则与 _common.rpc_failure 一致，判据也共用同一个函数。
    if is_kernel_unready(text):
        return HTTPException(503, text)
    if "没有这次运行" in text or "没有正在跑" in text or "没有这个模板" in text:
        return HTTPException(404, text)
    return HTTPException(400, text)


def _clean(text: str) -> str:
    """去掉 IPC 错误里的异常类名前缀。

    内核抛 ValueError 时，IPC 会把整句 "ValueError: 编排定义有问题：工作流缺少 goal"
    送回来。这个前缀是给开发者看的，用户看到的是"编排定义有问题：工作流缺少 goal"。
    """
    import re

    return re.sub(r"^[A-Za-z_][A-Za-z0-9_]*(Error|Exception):\s*", "", text or "").strip()


# ---------- routes ----------
def register(app, agent: AgentLink) -> None:
    import asyncio

    from fastapi.responses import JSONResponse

    @router.post("/run")
    async def _wf_run(req: WorkflowRunRequest):
        # 必须丢进线程池：编排要跑几分钟（几十个子 Agent 串起来），
        # 直接在 async 端点里调同步阻塞的 agent.call() 会独占事件循环 ——
        # 期间 SSE 发不出去、列表接口也超时，"运行中"视图就永远看不见。
        return JSONResponse(await asyncio.to_thread(run_workflow, agent, req))

    @router.get("")
    async def _wf_list(limit: int = 20):
        return JSONResponse(await asyncio.to_thread(list_workflows, agent, limit))

    # 静态路径要排在 /{run_id} 前面 —— 路径参数路由一旦先注册就会吃掉同名段
    @router.get("/failed-count")
    async def _wf_failed_count():
        return JSONResponse(await asyncio.to_thread(failed_workflow_count, agent))

    @router.post("/prune-failed")
    async def _wf_prune_failed():
        return JSONResponse(await asyncio.to_thread(prune_failed_workflows, agent))

    # ---- 模板 ----
    # 同样必须排在 /{run_id} 前面："templates" 会被它当成 run_id 吃掉
    @router.get("/templates")
    async def _tpl_list():
        return JSONResponse(await asyncio.to_thread(list_templates, agent))

    @router.post("/templates")
    async def _tpl_save(req: WorkflowTemplateRequest):
        return JSONResponse(await asyncio.to_thread(save_template, agent, req))

    @router.get("/templates/{template_id}")
    async def _tpl_get(template_id: str):
        return JSONResponse(await asyncio.to_thread(get_template, agent, template_id))

    @router.put("/templates/{template_id}")
    async def _tpl_update(template_id: str, req: WorkflowTemplateRequest):
        # 更新走同一个保存入口，但 id 由路径给 —— 不让 body 里的 id 改掉它
        req.id = template_id
        return JSONResponse(await asyncio.to_thread(save_template, agent, req))

    @router.delete("/templates/{template_id}")
    async def _tpl_delete(template_id: str):
        return JSONResponse(await asyncio.to_thread(delete_template, agent, template_id))

    @router.post("/templates/{template_id}/run")
    async def _tpl_run(template_id: str, req: dict | None = None):
        # 阻塞语义：编排要跑几分钟，和 POST /run 一样用线程池兜住，
        # 否则会独占事件循环 —— SSE 发不出去，"运行中"视图就看不见了
        return JSONResponse(
            await asyncio.to_thread(run_template, agent, template_id, (req or {}).get("args"))
        )

    @router.get("/{run_id}")
    async def _wf_detail(run_id: str):
        return JSONResponse(await asyncio.to_thread(workflow_detail, agent, run_id))

    @router.get("/{run_id}/plan")
    async def _wf_plan(run_id: str):
        return JSONResponse(await asyncio.to_thread(workflow_plan, agent, run_id))

    @router.post("/{run_id}/cancel")
    async def _wf_cancel(run_id: str):
        return JSONResponse(await asyncio.to_thread(cancel_workflow, agent, run_id))

    @router.delete("/{run_id}")
    async def _wf_delete(run_id: str):
        return JSONResponse(await asyncio.to_thread(delete_workflow, agent, run_id))

    app.include_router(router)