"""统一响应体 —— 「返回格式不统一」的解药。

问题
----
体检发现（阶段一）：
  · 215 处直接 `return dict`（裸返回）
  · ~100 处用 `domains/_common.py:json()` 包装
  · 112 处散落 `raise HTTPException(...)`
  · 56 处 `rpc_failure(...)`，**还分三套映射规则**（`_common` / workflow / checkpoint / schedule 各一套）

同一个项目里，错误响应有四种形状：
    {"detail": "没有这个会话"}            ← FastAPI 默认
    {"detail": "缺少访问令牌"}              ← 中间件手写
    {"detail": {"x": 1}}                   ← FastAPI 校验失败
    纯文本 / 二进制 / 500 的 HTML          ← 未捕获异常

前端因此到处写 `x.detail || r.status` —— 有的接口根本没有 `detail`。

目标
----
**增量统一**，不破坏现有契约：
  · 成功响应：统一成 `Response{code, data, message}`
  · 错误响应：**保留 `detail` 键**（前端现有的 `x.detail` 读取照常工作），
    同时补上 `code` / `message`，让新代码有统一字段可用

为什么保留 `detail`
------------------
195 个前端调用点里大量写着 `x.detail || r.status`。
如果直接把 `detail` 去掉，所有错误提示都会变成一句「请求失败」——
那是拿「统一」换「能用」。重写是分批做的，在那之前不能先砸了现有的。

用法
----
新写的接口这样返回：

    from ...api.response import ok

    @router.get("/api/xxx")
    async def _get():
        return ok({"items": [...]})

迁移期的旧接口保持原样（裸 dict），逐域改造时再换。
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

T = TypeVar("T")


class Response(BaseModel, Generic[T]):
    """统一响应体。

    - `code`：0 表示成功；非 0 时与 HTTP 状态码一致
    - `data`：领域数据。列表类接口的 `data` 恒为数组
    - `message`：给用户看的一句话（成功时通常为空）
    """

    code: int = Field(0, description="0 = 成功；非 0 = 错误码（与 HTTP 状态码一致）")
    data: T | None = Field(None, description="领域数据")
    message: str = Field("", description="错误说明（成功时为空）")


def ok(data: Any = None, message: str = "") -> JSONResponse:
    """成功响应。"""
    return JSONResponse(
        Response[Any](code=0, data=data, message=message).model_dump()
    )


def fail(status: int, message: str, data: Any = None) -> JSONResponse:
    """错误响应。

    **保留 `detail` 键** —— 现有前端大量读 `x.detail`，
    去掉会让所有错误提示退化成「请求失败」。详见模块文档。
    """
    return JSONResponse(
        status_code=status,
        content={
            "code": status,
            "data": data,
            "message": message,
            "detail": message,  # 兼容旧前端
        },
    )


def page(items: list[Any], total: int, limit: int, offset: int) -> dict:
    """分页数据的统一形状。

    `total` 是**全量条数**，`len(items)` 只是本页条数 ——
    这两个混用是前端「显示 0 条但其实有 20 条」的常见原因。
    """
    return {"items": items, "total": total, "limit": limit, "offset": offset}
