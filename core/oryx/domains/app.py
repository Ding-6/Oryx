"""应用域：扩展安装、已安装应用、图标、启动、自定义应用。

安全上的两个要点（搬过来时别丢）
--------------------------------
* `apps/launch` **只接受 id**，不接受任意路径 ——
  接受路径的话这个端点就是「运行任意程序」的通用原语。
* `app/icon` 提取失败返回 404，前端回退到首字母色块 ——
  图标是锦上添花，不该成为功能可用性的前提。
"""
from __future__ import annotations

import base64
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from ._common import json, rpc_failure

router = APIRouter(prefix="/api", tags=["应用"])


class InstallRequest(BaseModel):
    source: str
    kinds: list[str] | None = None


class LaunchAppRequest(BaseModel):
    """启动应用 —— **只接受 id**，不接受任意路径。

    以前是裸 dict：body 传数组 / 字符串时 `req.get` 直接 AttributeError → 500。
    """
    id: str = ""


class CustomAppRequest(BaseModel):
    """新增 / 更新一个自定义应用。

    PUT 时路径上的 `app_id` 会覆盖 body 里的 `id`（保持既有行为）。
    业务层仍负责「名字不能为空 / 目标必须存在」等中文 400。
    """
    id: str | None = None
    name: str = ""
    target: str = ""
    note: str = ""
    created_at: float | None = None


# 仓库根 —— `oryx/domains/app.py` 往上三层
# （`oryx/server.py` 原本是两层：server.py → oryx → 仓库根）
REPO_ROOT = Path(__file__).resolve().parents[3]


async def install_extensions(agent, req: InstallRequest) -> dict:
    """从来源安装扩展（插件 / 专家 / 技能）。

    装完要重载内核的目录 —— 否则新装的技能这一轮还用不了。
    """
    from .. import manage
    from ..agentlink import IpcError

    kinds = tuple(req.kinds) if req.kinds else ("plugin", "expert", "skill")
    result = manage.install_extensions(req.source, REPO_ROOT, kinds)
    try:
        reloaded = await agent.call_async("catalog.reload")
    except IpcError as exc:
        raise rpc_failure(exc) from None
    return {**result.to_dict(), "reloaded": reloaded}


def installed_apps(limit: int = 200) -> dict:
    from .. import manage

    return manage._cached_installed_apps(limit)


def app_icon_png(id: str) -> Response:
    """应用图标（PNG）。

    提取失败返回 404，前端回退到首字母色块 —— 图标是锦上添花，
    不该成为功能可用性的前提。
    """
    from .. import appicon, manage

    item = None
    for a in manage._cached_installed_apps(1000)["apps"]:
        if a.get("name", "").lower() == str(id).lower():
            item = a
            break
    if item is None:
        for a in manage.load_custom_apps():
            if a["id"] == id:
                item = {"path": a.get("target", ""), "target": a.get("target", "")}
                break
    if item is None:
        raise HTTPException(404, "没有这个应用")
    png = appicon.app_icon_png(item)
    if not png:
        raise HTTPException(404, "取不到图标")
    return Response(
        png,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=86400"},
    )


def app_icons_payload() -> dict:
    """一次性把所有应用的图标打包返回。

    为什么要有这个：逐个 GET /api/app/icon 的话，前端要发 200 多个请求，
    每个请求还各起一个 PowerShell 进程 —— 实测要 5 分半钟。走这个接口
    一次 PowerShell 进程循环处理，200 多个图标 10 秒内拿完。

    返回的是 JSON：{应用名: "data:image/png;base64,..."}。
    """
    from .. import appicon, manage

    apps = manage._cached_installed_apps(1000)["apps"]
    items = [(a.get("name", ""), a) for a in apps if a.get("name")]
    icons = appicon.extract_batch(items)
    payload: dict[str, str] = {}
    for name, png in icons.items():
        payload[name] = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    return {"icons": payload, "count": len(payload)}


def launch_app(req: LaunchAppRequest) -> dict:
    """启动一个已登记的应用。

    **只接受 id**，不接受任意路径 —— 接受路径的话这个端点就是
    「运行任意程序」的通用原语。
    """
    from .. import manage

    app_id = req.id.strip()
    if not app_id:
        raise HTTPException(400, "id 不能为空")
    try:
        result = manage.launch_app(app_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return result


def custom_apps_list() -> dict:
    from .. import manage

    return {"apps": manage.load_custom_apps()}


def custom_app_create(req: CustomAppRequest) -> dict:
    from .. import manage

    try:
        item = manage.save_custom_app(req.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return {"ok": True, "app": item}


def custom_app_update(app_id: str, req: CustomAppRequest) -> dict:
    from .. import manage

    # 路径 id 是权威值：不让 body 里的 id 把资源改到别处去
    payload = {**req.model_dump(exclude_unset=True), "id": app_id}
    try:
        item = manage.save_custom_app(payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return {"ok": True, "app": item}


def custom_app_delete(app_id: str) -> dict:
    from .. import manage

    if not manage.delete_custom_app(app_id):
        raise HTTPException(404, "没有这个应用")
    return {"deleted": app_id}


def register(app, agent) -> None:
    @router.post("/install")
    async def _install(req: InstallRequest):
        return json(await install_extensions(agent, req))

    @router.get("/apps")
    async def _apps(limit: int = 200):
        return json(installed_apps(limit))

    @router.get("/app/icon")
    async def _app_icon(id: str):
        return app_icon_png(id)

    @router.get("/apps/icons")
    async def _app_icons():
        return json(app_icons_payload())

    @router.post("/apps/launch")
    async def _launch(req: LaunchAppRequest):
        return json(launch_app(req))

    @router.get("/apps/custom")
    async def _custom_list():
        return json(custom_apps_list())

    @router.post("/apps/custom")
    async def _custom_create(req: CustomAppRequest):
        return json(custom_app_create(req))

    @router.put("/apps/custom/{app_id}")
    async def _custom_update(app_id: str, req: CustomAppRequest):
        return json(custom_app_update(app_id, req))

    @router.delete("/apps/custom/{app_id}")
    async def _custom_delete(app_id: str):
        return json(custom_app_delete(app_id))

    # 路由要先挂到 router 再 include，否则 app 拿不到
    app.include_router(router)
