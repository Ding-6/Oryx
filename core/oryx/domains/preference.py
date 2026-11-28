"""偏好域：交互模式、回答风格、计算模式。

这三个是**用户的一贯偏好** —— 顶栏上那三个下拉就是它们。
跟「当前会话」的设置分开：会话设置只影响这一轮，偏好是全局的。

路由前缀用 `/api`（三个路径没有共同前缀），靠 `tags` 区分。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..api.response import ok
from ._common import json

router = APIRouter(prefix="/api", tags=["偏好"])

# 计算模式只有这两档（models 里的「效率 / 本地」），它决定用哪一类模型服务，
# 所以是**全局**设置 —— 单次运行覆盖它并不会换 provider，见模块文档。
COMPUTE_MODES: list[dict[str, str]] = [
    {
        "name": "efficiency",
        "label": "效率模式",
        "hint": "云端理解 + 本地索引，响应快、能力强，需要配好云端模型服务。",
    },
    {
        "name": "local",
        "label": "本地模式",
        "hint": "全流程走本机模型，可断网使用；切换后会改用上次选过的本地模型服务。",
    },
]

# 风格的一句话说明。STYLES 里的 prompt 是系统提示词，不往前端发。
_STYLE_HINTS: dict[str, str] = {
    "professional": "先给结论再给依据，术语准确、不夸大。",
    "efficient": "极简直给，多用短句、列表、表格，信息密度优先。",
    "friendly": "温和耐心，术语顺带解释，可以有温度但不啰嗦。",
    "straightforward": "直来直去，有问题直接指出，不绕弯子。",
    "creative": "除直接方案外，再给 1-2 个不同角度的思路与取舍。",
    "socratic": "先点出关键前提与假设，必要时用问题帮你把目标想清楚。",
}


def preference_catalog() -> dict:
    """三份偏好目录 —— 前端渲染下拉的唯一数据源。

    放在这里而不是塞进 `/api/state`：`/api/state` 是老前端的首屏接口，
    保持它不变就不会给老界面添负担；新接口用 v2 统一响应，
    前端 `api.get` 会自动剥壳。
    """
    from ..modes import catalog as interaction_catalog
    from ..styles import catalog as style_catalog

    return {
        "interactions": interaction_catalog(),
        "styles": [
            {**item, "hint": _STYLE_HINTS.get(str(item.get("name")), "")}
            for item in style_catalog()
        ],
        "compute_modes": [dict(item) for item in COMPUTE_MODES],
    }


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
    # 模型服务还没配好时 provider_config() 会抛 KeyError。但模式已经改了、
    # 也已经存盘 —— 这时报 500，前端会显示「切换失败」，用户于是反复点，
    # 而实际上早就生效了。降级成空 provider 字段，让界面显示「（未配置）」
    # 并引导去配模型服务（跟 /api/state 首屏的降级是同一套思路）。
    try:
        cfg = config.provider_config()
    except Exception:  # noqa: BLE001 — 配置缺失不该让已完成的切换报错
        cfg = {"name": "", "model": ""}
    return {
        "mode": mode,
        "provider": cfg.get("name") or None,
        "model": cfg.get("model") or None,
    }


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

    @router.get("/preferences/catalog")
    async def _catalog():
        return ok(preference_catalog())

    # 路由要先挂到 router 再 include，否则 app 拿不到
    app.include_router(router)
