"""扩展域：后端扩展与界面扩展的安装、启停、回收区。

对外（后端扩展 `/api/extensions`）：
    POST   /api/extensions/reload            重新加载（改了代码不用重启内核）
    POST   /api/extensions/{id}/enable       启用
    POST   /api/extensions/{id}/disable      停用
    POST   /api/extensions/install           从本地目录安装
    DELETE /api/extensions/{id}              删除（**移进回收区**，不是真删）
    GET    /api/extensions/trash             回收区列表
    POST   /api/extensions/trash/restore     从回收区恢复
    POST   /api/extensions/trash/purge       彻底删除（不可逆）
    POST   /api/extensions/trash/cleanup     清掉过期的
    POST   /api/extensions/trash/purge-all   清空回收区（不可逆）
    GET    /api/extensions                    总览

对外（界面扩展 `/api/ui-extensions`）：
    DELETE /api/ui-extensions/{id}           删除（移进回收区）
    POST   /api/ui-extensions/install        安装（装完**默认禁用**）

这些全是**转发到内核的 RPC** —— 控制面不碰实现细节。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..ipc import IpcError
from ._common import json, rpc_failure

router = APIRouter(prefix="/api/extensions", tags=["扩展"])
ui_router = APIRouter(prefix="/api/ui-extensions", tags=["界面扩展"])

# 本域有**两个** router —— 统一暴露成列表，
# 免得「只看 router 属性」的检查漏掉第二个。
routers = (router, ui_router)


class ExtensionActionRequest(BaseModel):
    id: str = ""
    source: str = ""


class TrashRequest(BaseModel):
    name: str = ""
    kind: str = "backend"      # backend | ui


async def _rpc(agent, method: str, *, timeout: float = 600.0, **params):
    """转发到内核，统一把 IPC 错误翻成 HTTP 错误。"""
    try:
        return await agent.call_async(method, timeout=timeout, **params)
    except IpcError as exc:
        raise rpc_failure(exc) from None


async def backend_ext_index(agent) -> list[dict[str, str]]:
    """向内核要后端扩展的轻量索引。

    内核没起来时返回空列表 —— 那时界面扩展的**跨端**依赖判不了，
    但界面扩展之间的依赖照判（那不需要内核）。
    """
    try:
        payload = await agent.call_async("catalog.ext_index", timeout=10.0)
    except IpcError:
        return []
    if not isinstance(payload, dict):
        return []
    records = payload.get("records")
    if not isinstance(records, list):
        return []
    return [r for r in records if isinstance(r, dict)]


# ---------------- 后端扩展 ----------------

async def reload_extensions(agent) -> dict:
    """重新加载改了的后端扩展 —— 不用重启内核。"""
    return await _rpc(agent, "catalog.reload_extensions", timeout=60.0)


async def enable(agent, ext_id: str) -> dict:
    return await _rpc(agent, "catalog.extension_enable", ext_id=ext_id)


async def disable(agent, ext_id: str) -> dict:
    return await _rpc(agent, "catalog.extension_disable", ext_id=ext_id)


async def install(agent, source: str) -> dict:
    """从本地目录安装一个扩展。

    装完**默认禁用** —— 「装」和「让它跑」是两个独立决定。
    扩展是在你的机器上执行代码的东西。
    """
    payload = await _rpc(agent, "catalog.extension_install", source=source)
    if not payload.get("ok"):
        raise HTTPException(400, payload.get("reason") or "安装失败")
    return payload


async def remove(agent, ext_id: str) -> dict:
    """删除一个扩展 —— **移进回收区**，不是真删。"""
    payload = await _rpc(agent, "catalog.extension_remove", ext_id=ext_id)
    if not payload.get("ok"):
        raise HTTPException(400, payload.get("reason") or "删除失败")
    return payload


async def trash_list(agent) -> dict:
    """回收区里的扩展（后端与界面扩展各一个回收区）。"""
    return await _rpc(agent, "catalog.extension_trash_list")


async def trash_restore(agent, name: str, kind: str) -> dict:
    """从回收区恢复一个扩展。"""
    payload = await _rpc(
        agent, "catalog.extension_trash_restore", name=name, kind=kind)
    if not payload.get("ok"):
        raise HTTPException(400, payload.get("reason") or "恢复失败")
    return payload


async def trash_purge(agent, name: str, kind: str) -> dict:
    """从回收区彻底删除一个扩展 —— 这一步不可逆。"""
    payload = await _rpc(
        agent, "catalog.extension_trash_purge", name=name, kind=kind)
    if not payload.get("ok"):
        raise HTTPException(400, payload.get("reason") or "删除失败")
    return payload


async def trash_cleanup(agent) -> dict:
    """清掉超过保留期的回收区条目。

    保留期由配置 `extension_trash_retention_days` 决定（默认 30 天，
    0 = 永不自动清理）。
    """
    return await _rpc(agent, "catalog.extension_trash_cleanup", timeout=30.0)


async def trash_purge_all(agent, kind: str) -> dict:
    """清空一个回收区 —— 不可逆。"""
    return await _rpc(agent, "catalog.extension_trash_purge_all", kind=kind)


async def overview(agent) -> dict:
    """扩展总览：加载了什么、当前 profile、工具扩展的加载报告。

    「可自由支配」需要可见 —— 用户得能随时问「现在到底加载了谁的代码、
    哪些被跳过了、为什么」。
    """
    return await _rpc(agent, "catalog.extensions", timeout=30.0)


# ---------------- 界面扩展 ----------------

async def ui_extension_remove(agent, ext_id: str) -> dict:
    """删除一个界面扩展 —— **移进回收区**，不是真删。"""
    payload = await _rpc(agent, "catalog.ui_extension_remove", ext_id=ext_id)
    if not payload.get("ok"):
        raise HTTPException(400, payload.get("reason") or "删除失败")
    return payload


async def ui_extension_install(agent, source: str) -> dict:
    """从本地目录安装一个界面扩展。装完**默认禁用**。"""
    payload = await _rpc(agent, "catalog.ui_extension_install", source=source)
    if not payload.get("ok"):
        raise HTTPException(400, payload.get("reason") or "安装失败")
    return payload


def register(app, agent) -> None:
    """把本域路由挂到 app。依赖用闭包注入，action 本身保持纯函数。"""

    @router.post("/reload")
    async def _reload():
        return json(await reload_extensions(agent))

    @router.post("/{ext_id}/enable")
    async def _enable(ext_id: str):
        return json(await enable(agent, ext_id))

    @router.post("/{ext_id}/disable")
    async def _disable(ext_id: str):
        return json(await disable(agent, ext_id))

    @router.post("/install")
    async def _install(req: ExtensionActionRequest):
        return json(await install(agent, req.source))

    @router.delete("/{ext_id}")
    async def _remove(ext_id: str):
        return json(await remove(agent, ext_id))

    @router.get("/trash")
    async def _trash():
        return json(await trash_list(agent))

    @router.post("/trash/restore")
    async def _trash_restore(req: TrashRequest):
        return json(await trash_restore(agent, req.name, req.kind))

    @router.post("/trash/purge")
    async def _trash_purge(req: TrashRequest):
        return json(await trash_purge(agent, req.name, req.kind))

    @router.post("/trash/cleanup")
    async def _trash_cleanup():
        return json(await trash_cleanup(agent))

    @router.post("/trash/purge-all")
    async def _trash_purge_all(req: TrashRequest):
        return json(await trash_purge_all(agent, req.kind))

    @router.get("")
    async def _overview():
        return json(await overview(agent))

    # ---------------- 界面扩展 ----------------

    @ui_router.delete("/{ext_id}")
    async def _ui_remove(ext_id: str):
        return json(await ui_extension_remove(agent, ext_id))

    @ui_router.post("/install")
    async def _ui_install(req: ExtensionActionRequest):
        return json(await ui_extension_install(agent, req.source))

    app.include_router(router)
    app.include_router(ui_router)
