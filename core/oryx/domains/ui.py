"""界面扩展域：给前端扩展提供「核心样式子集」与「扩展清单」。

对外三件事：
    GET /api/ui/base-css      核心样式的子集（扩展在 Shadow DOM 里用不了核心类名）
    GET /api/ui/core-classes  核心 CSS 里有哪些类名
    GET /api/ui/modules       前端扩展清单（动态 import 哪些模块）

**为什么不把核心样式整份给扩展**
--------------------------------
扩展跑在自己的 Shadow DOM 里，核心的类名（`.btn` / `.inp` / `.iconbtn`）用不了。
与其让扩展把样式抄一遍（抄了就不会跟着核心一起变），
不如让它声明「我要用哪些类」，这里按需把**核心原文**挑出来。
"""
from __future__ import annotations

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse

from ..config import app_dir
from ..extmanage import disabled_ids
from ..registry import UI_CAP_NOTES, UI_CAP_RULES
from ..uiext import MOUNT_POINTS, load_ui_extensions
from ._common import json

router = APIRouter(prefix="/api/ui", tags=["界面扩展"])


def base_css(classes: str = "") -> Response:
    """核心样式的子集 —— 给前端扩展复用。

    例：`GET /api/ui/base-css?classes=btn,inp` → 只给 `.btn` 与 `.inp` 的规则。

    返回 `text/css` —— 前端拿去做 `<style>` 的内容。
    """
    from ..uicss import subset_for

    wanted = [c.strip().lstrip(".") for c in (classes or "").split(",") if c.strip()]
    if not wanted:
        return Response(content="", media_type="text/css; charset=utf-8")
    return Response(content=subset_for(wanted), media_type="text/css; charset=utf-8")


def core_classes() -> dict:
    """核心 CSS 里有哪些类名 —— 扩展作者写清单时可以参考。"""
    from ..uicss import core_classes as _classes

    return {"classes": _classes()}


async def modules(config, agent) -> dict:
    """前端扩展清单：前端据此决定动态 import 哪些模块。

    只返回**元数据与 URL** —— 不含令牌，也不含任何用户数据。
    """
    allow = config.data.get("ui_extensions")
    allow_ids = [str(x) for x in allow] if isinstance(allow, list) else None
    # 停用名单与后端扩展**共用一套** —— 用户在界面上关掉的，
    # 不管是后端还是界面扩展，都不加载。
    off = disabled_ids(config.data)
    # 界面扩展可以声明依赖某个**后端扩展** —— 但 server 进程看不到
    # 内核的注册表（那是内核进程的东西）。向内核要一份轻量索引，
    # 跨端依赖才能判。
    from .extension import backend_ext_index

    backend_records = await backend_ext_index(agent)
    loaded, skipped = load_ui_extensions(
        app_dir(), allow_ids=allow_ids, disabled_ids=off,
        backend_records=backend_records,
    )
    return {
        "extensions": [e.to_dict() for e in loaded],
        "skipped": skipped,
        "disabled": sorted(off),
        "mount_points": list(MOUNT_POINTS),
        "cap_rules": UI_CAP_RULES,
        "cap_notes": UI_CAP_NOTES,
    }


def register(app, config, agent) -> None:
    """把本域路由挂到 app。依赖用闭包注入，action 本身保持纯函数。"""

    @router.get("/base-css")
    async def _base_css(classes: str = ""):
        return base_css(classes)

    @router.get("/core-classes")
    async def _core_classes():
        return json(core_classes())

    @router.get("/modules")
    async def _modules():
        # **不能缓存** —— 清单里带内容指纹（扩展的 URL 上），
        # 缓存的清单会给旧指纹，扩展改了也不生效。刷新页面就该拿到新的。
        return JSONResponse(
            await modules(config, agent),
            headers={"Cache-Control": "no-store, must-revalidate"},
        )

    app.include_router(router)
