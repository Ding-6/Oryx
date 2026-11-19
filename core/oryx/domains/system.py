"""系统域：首屏状态、预热、任务、能力清单、系统设置、环境自检、目录浏览。

这一域的接口大多是**只读**的 —— 它们给设置页与各视图页提供数据。

两个特别的地方
--------------
* `/api/state` 是**首屏依赖的第一个接口**。它一 500，
  前端 `refreshState()` 整体 reject，设置页的 `Promise.all` 也跟着塌，
  用户看到的是「整页打不开」而不是「该去配模型」。
  所以 provider 没配好时**降级**成空字段，让界面先渲染出来。
* `/api/fs/list` 是**同步端点**（不是 async）：目录扫描是阻塞 IO，
  放进 async 端点会独占事件循环 —— SSE 发不出去、列表接口全部超时。
  FastAPI 会把它丢进线程池。
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ._common import json, rpc_failure

router = APIRouter(prefix="/api", tags=["系统"])


class OptimizeRequest(BaseModel):
    text: str
    skills: str = ""
    expert: str = ""
    style: str = ""


# ══════════ 状态与预热 ══════════

def prewarm_status() -> dict:
    """预热状态。给排障用：看内核是不是走了预热路径起来的。"""
    from ..prewarm import get_pool

    pool = get_pool()
    handle = pool.peek()
    return {
        "warming": pool.warming,
        "pending": handle is not None and handle.alive(),
        "port": handle.port if handle is not None else 0,
    }


async def app_state(config, agent, bus) -> dict:
    """首屏状态 —— 模式、人设、风格、当前模型、能力清单。"""
    from ..agentlink import IpcError
    from ..styles import catalog as style_catalog

    # provider 没配好时不能整个挂掉：/api/state 是首屏依赖的第一个接口，
    # 它一 500，前端 refreshState() 整体 reject，设置页的 Promise.all 也跟着塌，
    # 用户看到的是「整页打不开」而不是「该去配模型」。降级成空 provider 字段，
    # 让界面先渲染出来、把配置问题显示在设置页里。
    try:
        cfg = config.provider_config()
    except Exception as exc:  # noqa: BLE001 — 首屏不能因配置缺失而全挂
        cfg = {"name": "", "model": ""}
        bus.publish("agent.error", error=f"模型服务未配置：{exc}")

    experts: list[dict[str, Any]] = []
    skills: list[str] = []
    busy = False
    kernel_ready = True
    try:
        experts = await agent.call_async("catalog.experts")
        skills = await agent.call_async("catalog.skills")
        busy = await agent.call_async("chat.busy")
    except IpcError as exc:
        # 内核没起来也要把页面渲染出来，只是能力清单为空
        kernel_ready = False
        bus.publish("agent.error", error=str(exc))

    from .. import localmodels

    return {
        "mode": config.mode,
        "interaction": config.data.get("interaction", "craft"),
        "style": config.data.get("style", "professional"),
        "styles": style_catalog(),
        "expert": config.data.get("expert", ""),
        "experts": experts,
        "provider": cfg.get("name"),
        "model": cfg.get("model"),
        # 当前 provider 是本机跑的还是云端的 —— 顶栏芯片按它显示
        # 「本地模型 / 云端模型」。只给名字的话前端只能靠 provider 名猜，
        # 名字一改就认不出来了。
        "provider_is_local": localmodels.is_local_provider(cfg),
        "busy": busy,
        "skills": skills,
        # **内核起没起来**。
        #
        # 为什么必须显式告诉前端：内核没就绪时，那些走 RPC 的接口
        # （团队、工作流…）会全部失败。前端如果照常调，
        # 首屏就会刷出一排 4xx —— 用户看到的是「页面报错」，
        # 实际上是「内核还在启动」。
        #
        # 有了这个字段，前端能在内核没起来时**先别调**那些接口，
        # 等它就绪了再补上（refreshState 每 6 秒跑一次，会看到状态翻转）。
        "kernel_ready": kernel_ready,
    }


# ══════════ 用量与审计 ══════════

def audit_for_task(store, task_id: str) -> dict:
    return {"task_id": task_id, "audit": store.audit_for_task(task_id)}


def usage_summary(store, hours: int = 24 * 30) -> dict:
    return store.usage_summary(hours)


def recent_tasks(store) -> dict:
    return {"tasks": store.recent_tasks(30)}


# ══════════ 能力清单 ══════════

async def capabilities(agent) -> dict:
    """工具清单：按前缀分组，带风险等级与来源。分组在 Agent 内核侧做。"""
    from ..agentlink import IpcError

    try:
        payload = await agent.call_async("catalog.tools")
    except IpcError as exc:
        raise rpc_failure(exc) from None
    return payload


# ══════════ 系统设置 ══════════

def sys_switches() -> dict:
    """系统设置能力清单。

    syssettings.switches() 以前定义了却没有任何调用方 —— 用户看不到
    「Oryx 能改哪些系统设置」。这里把它接出来，设置页据此渲染卡片。
    """
    from .. import syssettings

    return syssettings.switches()


async def sys_query(req: dict) -> dict:
    """只读地查一项系统设置状态。

    白名单只放 read 类函数：写操作（锁屏 / 改缩放 / 换壁纸）必须走
    Agent 的 L2 审批，不能让用户从设置页点一下就绕过闸门。
    """
    from .. import syssettings

    name = str(req.get("name") or "").strip()
    readonly = {
        "power_status": syssettings.power_status,
        "display_settings": syssettings.display_settings,
        "mouse_settings": syssettings.mouse_settings,
        "input_method_list": syssettings.input_method_list,
    }
    if name not in readonly:
        raise HTTPException(400, f"不支持或需要审批的系统设置：{name or '(空)'}")
    try:
        result = await asyncio.to_thread(readonly[name])
    except Exception as exc:  # noqa: BLE001 — PowerShell 失败要给人看得懂的话
        raise HTTPException(500, f"读取失败：{exc}") from None
    return result


# ══════════ 环境自检 ══════════

def doctor(config) -> dict:
    """环境自检：能干什么、缺什么、下一步能做什么。

    不访问网络、不扫描用户文件 —— 抄 Marvis 的 doctor 纪律。
    """
    from ..doctor import run_doctor

    return run_doctor(config)


# ══════════ 提示词优化 ══════════

def skill_map(raw: Any) -> dict[str, Any]:
    """把技能目录统一成「名字 → 技能对象」的映射。

    catalog.skills 历史上返回过两种形状：
    - list[str]（名字列表）
    - list[dict]（带 name/description/path）
    两种都要吃得下 —— 不然用户一勾技能就崩。
    """
    out: dict[str, Any] = {}
    for item in raw or []:
        if isinstance(item, str):
            out[item] = {"name": item, "description": ""}
        elif isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            if name:
                out[name] = item
    return out


async def prompt_optimize(agent, config, req: OptimizeRequest) -> dict:
    """优化用户写的这句任务描述。"""
    from ..agentlink import IpcError
    from ..promptopt import optimize

    chosen = [s.strip() for s in req.skills.split(",") if s.strip()]
    try:
        skills = await agent.call_async("catalog.skills")
    except IpcError as exc:
        raise rpc_failure(exc) from None
    # catalog.skills 返回的是**名字列表**，而 optimize() 要的是
    # 「名字 → 技能对象」的映射（它要读 description 拼进提示词）。
    # 以前直接把 list 传过去，用户一勾技能就 AttributeError → HTTP 500。
    # 工具版（loop.py 的 _optimize）传的是真 dict，所以只有 HTTP 路径坏。
    result = optimize(
        req.text,
        skills=skill_map(skills),
        bound=chosen,
        expert=req.expert or config.data.get("expert", ""),
        style=req.style or config.data.get("style", ""),
        workspace=config.data.get("workspace") or "",
    )
    return result["content"]


# ══════════ 目录浏览 ══════════

def fs_list(config, path: str, show_hidden: int = 0,
            limit: int = 500, offset: int = 0) -> dict:
    """列一个目录的内容（单层）。

    **同步端点**（不是 async）：目录扫描是阻塞 IO，放进 async 端点会独占
    事件循环 —— SSE 发不出去、列表接口全部超时。FastAPI 会把它丢进线程池。

    路径走 query 而不是 path 参数：Windows 的 `C:\\` 在路径参数里转义
    容易出问题，query 更省心。
    """
    from .. import localfs

    try:
        return localfs.list_dir(
            path,
            forbidden=config.forbidden_paths,
            show_hidden=bool(show_hidden),
            limit=limit,
            offset=offset,
        )
    except localfs.BrowseError as exc:
        raise HTTPException(exc.code, str(exc)) from None


def register(app, config, agent, store, bus) -> None:
    @router.get("/prewarm")
    async def _prewarm():
        return json(prewarm_status())

    @router.get("/state")
    async def _state():
        return json(await app_state(config, agent, bus))

    @router.get("/audit/{task_id}")
    async def _audit(task_id: str):
        return json(audit_for_task(store, task_id))

    @router.get("/usage")
    async def _usage(hours: int = 24 * 30):
        return json(usage_summary(store, hours))

    @router.get("/tasks")
    async def _tasks():
        return json(recent_tasks(store))

    @router.get("/capabilities")
    async def _capabilities():
        return json(await capabilities(agent))

    @router.get("/sys/switches")
    async def _sys_switches():
        return json(sys_switches())

    @router.post("/sys/query")
    async def _sys_query(req: dict):
        return json(await sys_query(req))

    @router.get("/doctor")
    async def _doctor():
        return json(doctor(config))

    @router.post("/prompt/optimize")
    async def _prompt_optimize(req: OptimizeRequest):
        return json(await prompt_optimize(agent, config, req))

    # 目录浏览是**同步**的 —— 阻塞 IO 放进 async 端点会独占事件循环
    @router.get("/fs/list")
    def _fs_list(path: str, show_hidden: int = 0,
                 limit: int = 500, offset: int = 0):
        return json(fs_list(config, path, show_hidden, limit, offset))

    # 路由要先挂到 router 再 include，否则 app 拿不到
    app.include_router(router)
