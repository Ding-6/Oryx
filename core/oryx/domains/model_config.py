"""模型配置域：模型服务的增删改查与激活。

对齐 Marvis 的 modules/model_config/actions/{list,create,update,delete,
validate,set_binding,get_binding}——路由与用例一一对应。

密钥只在写入时接收，读取一律走 manage.mask_key 出厂脱敏，
领域层不碰明文密钥。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import manage
from ..config import Config

router = APIRouter(prefix="/api/providers", tags=["模型配置"])


class ProviderRequest(BaseModel):
    name: str
    type: str = "openai"
    base_url: str
    model: str
    api_key: str = ""


class ProviderEditRequest(BaseModel):
    """编辑一条已存在的 provider 配置。

    字段全部可选 —— 只传要改的。api_key 传空串表示"不动它"
    （前端不回显明文密钥，留空就保持原样）。
    """

    type: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None


class LocalModelRequest(BaseModel):
    """把本机已装的某个模型登记成 provider。"""

    model: str
    base_url: str = ""


# ---------- actions ----------
def list_providers(config: Config) -> dict:
    from .. import localmodels

    items = []
    for name, spec in (config.data.get("providers") or {}).items():
        masked = manage.mask_key(spec)
        masked["capabilities"] = manage.provider_capabilities(spec)
        # 本地 / 云端标记 —— 前端据此把两个列表分开显示，
        # 也据此决定"计算模式"切到本地时只列本地的、切到云端时只列云端的。
        masked["is_local"] = localmodels.is_local_provider(spec)
        items.append({"name": name, **masked})
    return {
        "active": config.active_provider_name,
        "local": config.data.get("local_provider"),
        "items": items,
    }


def _local_base_url(config: Config) -> str:
    """本机模型服务的地址。

    从已登记的本地 provider 里取 —— 用户可能把 Ollama 装在别的端口上。
    一个都没有就用默认端口。
    """
    from .. import localmodels

    providers = config.data.get("providers") or {}
    local_name = config.data.get("local_provider") or ""
    # 先看当前本地 provider，再看任何一个指向回环地址的
    for name in [local_name] + list(providers):
        spec = providers.get(name) or {}
        base = str(spec.get("base_url") or "")
        if base and localmodels.is_local_provider(spec):
            return base
    return localmodels.DEFAULT_OLLAMA_URL


def list_local_models(config: Config) -> dict:
    """本机已装的本地模型。

    base_url 从现有的本地 provider 里取 —— 用户可能把 Ollama 装在别的端口上。
    每个模型标出「已经有哪个 provider 在用它」，前端据此决定是切换还是新建。
    """
    from .. import localmodels

    providers = config.data.get("providers") or {}
    base = _local_base_url(config)
    models = localmodels.list_local_models(base)
    # model -> 正在用它的 provider 名
    serving: dict[str, str] = {}
    for name, spec in providers.items():
        model = str(spec.get("model") or "").strip()
        if model and model not in serving:
            serving[model] = name
    for m in models:
        m["provider"] = serving.get(m["name"], "")
    return {"models": models, "base_url": base}


def register_local_model(config: Config, req: LocalModelRequest) -> dict:
    """把本机已装的模型登记成 provider（或直接切到已有的那个）。"""
    from .. import localmodels

    model = (req.model or "").strip()
    if not model:
        raise HTTPException(400, "模型名不能为空")

    providers = config.data.get("providers") or {}
    # 已经有 provider 在跑这个模型了 → 不新建，直接用那个。
    # 否则同一台机器上的同一个模型会出现两条记录，切来切去很乱。
    for name, spec in providers.items():
        if str(spec.get("model") or "").strip() == model:
            config.data["local_provider"] = name
            config.save()
            return {"name": name, "model": model, "type": spec.get("type", "openai"),
                    "created": False}

    base = req.base_url or _local_base_url(config)

    spec, name = localmodels.provider_spec_for(model, base)
    try:
        result = manage.upsert_provider(config, name, spec)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    # 本地模式用的是 local_provider，不是 active_provider。
    # 不一起改的话，用户在"本地模式"下选了新模型，切过去还是旧的 ——
    # 看起来就像"切换没生效"。
    config.data["local_provider"] = name
    config.save()
    return {"name": result["name"], "model": model, "type": result["type"], "created": True}


def create_provider(config: Config, req: ProviderRequest) -> dict:
    spec = {"type": req.type, "base_url": req.base_url, "model": req.model}
    if req.api_key:
        spec["api_key"] = req.api_key
    try:
        result = manage.upsert_provider(config, req.name, spec)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"name": result["name"], "type": result["type"]}


def edit_provider(config: Config, name: str, req: ProviderEditRequest) -> dict:
    """改一条已存在的 provider 配置。

    只改传了的字段。api_key 传空串 = 不动它 —— 前端不回显明文密钥，
    用户留空提交不该把已存的密钥擦掉。
    """
    providers = config.data.get("providers") or {}
    if name not in providers:
        raise HTTPException(404, "没有这个 provider")

    spec: dict[str, Any] = {}
    if req.type:
        spec["type"] = req.type
    if req.base_url is not None and req.base_url.strip():
        spec["base_url"] = req.base_url.strip()
    if req.model is not None and req.model.strip():
        spec["model"] = req.model.strip()
    # 密钥单独处理：空串不进 spec，upsert 会保留原有凭据库标记
    if req.api_key:
        spec["api_key"] = req.api_key

    if not spec:
        raise HTTPException(400, "没有要改的内容")

    try:
        result = manage.upsert_provider(config, name, spec)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"name": result["name"], "type": result["type"]}


def activate_provider(config: Config, name: str) -> dict:
    try:
        return manage.switch_provider(config, name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def delete_provider(config: Config, name: str) -> dict:
    try:
        removed = manage.delete_provider(config, name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not removed:
        raise HTTPException(404, "没有这个 provider")
    return {"deleted": name}


# ---------- routes ----------
def register(app, config: Config) -> None:
    from fastapi.responses import JSONResponse

    @router.get("")
    async def _list():
        return JSONResponse(list_providers(config))

    @router.get("/local")
    async def _local():
        return JSONResponse(list_local_models(config))

    @router.post("/local")
    async def _register_local(req: LocalModelRequest):
        return JSONResponse(register_local_model(config, req))

    @router.post("")
    async def _create(req: ProviderRequest):
        return JSONResponse(create_provider(config, req))

    @router.put("/{name}")
    async def _edit(name: str, req: ProviderEditRequest):
        return JSONResponse(edit_provider(config, name, req))

    @router.post("/{name}/activate")
    async def _activate(name: str):
        return JSONResponse(activate_provider(config, name))

    @router.delete("/{name}")
    async def _delete(name: str):
        return JSONResponse(delete_provider(config, name))

    app.include_router(router)