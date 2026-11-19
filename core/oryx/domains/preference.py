"""偏好域：交互模式、回答风格、计算模式。

这三个是**用户的一贯偏好** —— 顶栏上那三个下拉就是它们。
跟「当前会话」的设置分开：会话设置只影响这一轮，偏好是全局的。

路由前缀用 `/api`（三个路径没有共同前缀），靠 `tags` 区分。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ._common import json

router = APIRouter(prefix="/api", tags=["偏好"])


class InteractionRequest(BaseModel):
    interaction: str


class StyleRequest(BaseModel):
    style: str


class ModeRequest(BaseModel):
    mode: str


def set_interaction(config, name: str) -> dict:
    """切交互模式 —— 「问答 / 规划 / 执行」。"""
    from ..modes import MODE_POLICIES, mode_cycle

    name = (name or "").strip()
    if name not in MODE_POLICIES:
        raise HTTPException(
            400, f"interaction 只能是 {' / '.join(MODE_POLICIES)}"
        )
    # 开发者模式可以被显式关掉（对齐 WorkBuddy 的 disableBypassPermissionsMode）
    if name == "bypass" and config.data.get("disable_bypass_mode"):
        raise HTTPException(400, "开发者模式已被禁用")
    config.data["interaction"] = name
    config.save()
    return {"interaction": name, "cycle": mode_cycle()}


def set_style(config, style: str) -> dict:
    """切回答风格 —— 「专业 / 随性」。"""
    from ..styles import STYLES

    if style not in STYLES:
        raise HTTPException(400, "未知的风格")
    config.data["style"] = style
    config.save()
    return {"style": style}


def set_mode(config, mode: str) -> dict:
    """切计算模式 —— 「效率 / 本地」。

    切模式时把顶栏芯片指到同类模型上 ——
    不然「本地模式」下芯片还显示着云端模型，看起来就像没切。
    取的是用户上次在这一类里选过的那个，来回切不会乱跳。
    """
    from .. import manage

    if mode not in ("efficiency", "local"):
        raise HTTPException(400, "mode 只能是 efficiency 或 local")
    config.data["mode"] = mode
    name = manage.pick_provider_for_mode(config, mode)
    if name:
        config.data["active_provider"] = name
        # 把这次的选择记进槽位 —— 下次切回来还是它
        slot = "local_provider" if mode == "local" else "cloud_provider"
        config.data[slot] = name
    config.save()
    cfg = config.provider_config()
    return {"mode": mode, "provider": cfg.get("name"), "model": cfg.get("model")}


def register(app, config) -> None:
    @router.post("/interaction")
    async def _interaction(req: InteractionRequest):
        return json(set_interaction(config, req.interaction))

    @router.post("/style")
    async def _style(req: StyleRequest):
        return json(set_style(config, req.style))

    @router.post("/mode")
    async def _mode(req: ModeRequest):
        return json(set_mode(config, req.mode))

    # 路由要先挂到 router 再 include，否则 app 拿不到
    app.include_router(router)
