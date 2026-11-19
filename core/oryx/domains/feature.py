"""特性开关域：列出与切换开关。

对外两件事：
    GET  /api/features          当前开关清单
    POST /api/features/toggle   切换单个开关
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..config import FEATURES_PATH
from ..features import FeatureFlags, feature_catalog
from ._common import json

router = APIRouter(prefix="/api/features", tags=["特性开关"])


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


def toggle(req: dict) -> dict:
    """切换单个开关。需要 `EnableFeatureFlags` 为真。"""
    key = str(req.get("key", "")).strip()
    value = bool(req.get("value", False))
    if not key:
        raise HTTPException(400, "key 不能为空")

    flags = FeatureFlags.load(FEATURES_PATH)
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
    async def _toggle(req: dict):
        return json(toggle(req))

    app.include_router(router)
