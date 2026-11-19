"""契约层 —— 前后端之间的「法律条文」。

这里放两样东西：

1. `response.py` —— 统一响应体（成功 / 错误 / 分页）
2. `schemas/` —— 每个域的出入参 Pydantic 模型

为什么要有这一层
----------------
体检发现：接口的错误有**四种形状**、入参有一半是裸 `req: dict`（零校验）、
前端 195 个调用点里只有 91 个检查了 HTTP 状态。

根子在于：**没有一份「契约」**。
每个接口的形状是当年随手写的，后来的人只能照着猜。
所以要把形状写成**代码**（模型），而不是文档 —— 文档会过期，模型会报错。

用法
----
    # 域里
    from ...api.schemas.session import RenameSessionRequest
    from ...api.response import ok

    @router.put("/api/sessions/{task_id}")
    async def _rename(task_id: str, req: RenameSessionRequest):
        ...
        return ok({"renamed": task_id})

好处：入参形状、字段名、默认值、是否必填，全部集中在一处，
改一个字段只改一个文件，前端的 TS 类型可以直接从它生成。
"""

from __future__ import annotations

from .response import Response, fail, ok, page

__all__ = ["Response", "ok", "fail", "page"]
