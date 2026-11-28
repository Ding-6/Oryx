"""特性开关域：列出与切换开关。

对外两件事：
    GET  /api/features          当前开关清单
    POST /api/features/toggle   切换单个开关
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from ..config import FEATURES_PATH
from ..features import FeatureFlags, feature_catalog
from ._common import json

router = APIRouter(prefix="/api/features", tags=["特性开关"])


class FeatureToggleRequest(BaseModel):
    """切换开关的请求契约。

    以前这里是裸 dict 请求体 + `bool(req.get("value"))`：
      · `{"value": "false"}` → `bool("false")` 是 **True** —— 想关结果开了；
      · `key` 完全不校验，随便一个名字都能写进开关文件，
        `GET /api/features` 里又不会出现，成了永远看不见的幽灵键。

    现在交给 Pydantic：`value` 走标准布尔解析（`"false"` → False），
    `extra="forbid"` 挡住拼错的字段；`key` 的白名单在 `toggle()` 里
    用 `feature_catalog()` 动态取，不另抄一份清单。
    """

    model_config = ConfigDict(extra="forbid")

    key: str
    value: bool


def read_features() -> dict:
    """当前开关清单。"""
    flags = FeatureFlags.load(FEATURES_PATH)
    return {
        "profile": read_profile(),
        "flags": feature_catalog(flags),
        "raw": flags.snapshot(),
    }


def read_profile() -> str:
    """读出当前 profile。

    与 `FeatureFlags.load` 用同一份解析逻辑（JSON 优先、YAML 兜底），
    不另写一套 —— 两套解析迟早会对不上。
    """
    import json as _json

    try:
        raw = FEATURES_PATH.read_text("utf-8", errors="replace")
    except OSError:
        return "personal"
    try:
        data = _json.loads(raw)
    except Exception:
        try:
            import yaml  # type: ignore

            data = yaml.safe_load(raw)
        except Exception:
            return "personal"
    if isinstance(data, dict) and isinstance(data.get("profile"), str):
        return data["profile"]
    return "personal"


def toggle(req: FeatureToggleRequest) -> dict:
    """切换单个开关。需要 `EnableFeatureFlags` 为真。

    `key` 必须在 `feature_catalog()` 里 —— 目录是唯一数据源，
    不在这里另维护一份白名单，免得加开关时两处对不上。
    """
    key = req.key.strip()
    value = req.value
    if not key:
        raise HTTPException(400, "key 不能为空")

    flags = FeatureFlags.load(FEATURES_PATH)
    known = {str(card.get("key") or "") for card in feature_catalog(flags)}
    if key not in known:
        raise HTTPException(400, f"没有这个特性开关：{key}")
    if not flags.is_enabled("EnableFeatureFlags"):
        raise HTTPException(403, "EnableFeatureFlags 已关闭，禁止运行时调整")

    flags.flags[key] = value
    # 写 JSON 而不是 YAML —— JSON 是 YAML 的子集，
    # 所以「写的时候不依赖 PyYAML，读的时候两种都能读」。
    # （以前这里写 JSON，读的时候 PyYAML 缺失就把整个文件丢弃。）
    import json as _json

    try:
        FEATURES_PATH.parent.mkdir(parents=True, exist_ok=True)
        FEATURES_PATH.write_text(
            _json.dumps({"profile": read_profile(), "flags": flags.flags},
                        ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        raise HTTPException(500, f"写开关文件失败：{exc}")
    return {"key": key, "value": value}


def register(app) -> None:
    """把本域路由挂到 app。依赖用闭包注入，action 本身保持纯函数。"""

    @router.get("")
    async def _list():
        return json(read_features())

    @router.post("/toggle")
    async def _toggle(req: FeatureToggleRequest):
        return json(toggle(req))

    app.include_router(router)
