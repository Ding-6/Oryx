"""外观域：主题、背景图。

对外就五件事：
    GET  /api/appearance              当前主题与背景
    POST /api/appearance/background-fit  背景填充方式
    POST /api/appearance/theme        明暗主题
    POST /api/appearance/background   上传/清除背景图
    GET  /api/appearance/background.png  背景图本体
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from .. import appearance as ap
from ..api.auth import resolve_token_candidate
from ._common import json

router = APIRouter(prefix="/api/appearance", tags=["外观"])

FITS = ("cover", "contain", "tile", "center")
THEMES = ("light", "dark")


class ThemeRequest(BaseModel):
    theme: str = "light"


class BackgroundFitRequest(BaseModel):
    fit: str = "cover"


class BackgroundRequest(BaseModel):
    data_url: str = ""


def read_appearance(config) -> dict:
    """当前外观设置。"""
    cur = ap.current()
    return {
        "theme": config.data.get("theme", "light"),
        "background": cur["background"],
        "background_exists": cur["background_exists"],
        "background_fit": config.data.get("background_fit", "cover"),
    }


def set_theme(config, theme: str) -> dict:
    if theme not in THEMES:
        raise HTTPException(400, "theme 只能是 light 或 dark")
    config.data["theme"] = theme
    config.save()
    return {"theme": theme}


def set_background_fit(config, fit: str) -> dict:
    if fit not in FITS:
        raise HTTPException(400, "fit 只能是 cover / contain / tile / center")
    config.data["background_fit"] = fit
    config.save()
    return {"background_fit": fit}


def set_background(config, data_url: str) -> dict:
    if not data_url:
        ap.clear_background(config)
        return {"background": "", "cleared": True}
    try:
        return ap.save_background(data_url, config)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


def read_background_file(request: Request, api_token: str):
    """背景图本体。

    令牌走**同一个解析器**（api/auth.py）—— cookie / 头 / `?t=` 都认。

    之前这里**只认 `?t=`**，于是：
      · 前端用 CSS `url("/api/appearance/background.png")` 引背景图
      · 浏览器对同源子资源**会**自动带 HttpOnly cookie，中间件放行
      · 但这个函数又把 cookie 拒了 → 401 → **背景图永远加载不出来**

    令牌早就不在 JS 里了（`ORYX_TOKEN` 已移除），
    所以「前端补上 `?t=`」那条路走不通 —— 只能在后端把 cookie 认下来。
    """
    provided = resolve_token_candidate(request)
    if not provided or not secrets.compare_digest(provided, api_token):
        raise HTTPException(401, "缺少访问令牌")
    path, mime = ap.read_background()
    if path is None:
        raise HTTPException(404, "没有设置背景")
    return FileResponse(path, media_type=mime)


def register(app, config, api_token: str) -> None:
    """把本域路由挂到 app。依赖用闭包注入，action 本身保持纯函数。"""

    @router.get("")
    async def _get():
        return json(read_appearance(config))

    @router.post("/background-fit")
    async def _fit(req: BackgroundFitRequest):
        return json(set_background_fit(config, req.fit))

    @router.post("/theme")
    async def _theme(req: ThemeRequest):
        return json(set_theme(config, req.theme))

    @router.post("/background")
    async def _background(req: BackgroundRequest):
        return json(set_background(config, req.data_url))

    @router.get("/background.png")
    async def _background_png(request: Request):
        return read_background_file(request, api_token)

    app.include_router(router)
