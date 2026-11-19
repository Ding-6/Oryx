"""领域模块的共用小工具。

为什么单独放一个
----------------
以前 `_rpc_failure` 这类辅助住在 `server.py` 里，各路由直接用。
把路由拆到 `domains/` 之后，跨域要用它就得 `from ..server import _rpc_failure`
—— **反向依赖**：server 装配域，域又依赖 server。**循环依赖**，
而且「域之间不互相依赖」这条规矩也会被破掉。

所以共用的小东西放这里，域只依赖 `_common`，不依赖彼此、更不依赖 server。
"""
from __future__ import annotations

from fastapi import HTTPException

from ..ipc import IpcError


def rpc_failure(exc: IpcError) -> HTTPException:
    """把 IPC 错误映射成合适的 HTTP 状态码。

    内核侧抛的是 ValueError（参数/业务不对）和 RuntimeError（状态冲突），
    到这里要还原成 400 / 409 / 404，不然前端只能看到笼统的 500。
    """
    text = str(exc)
    if "已有扫描" in text or "扫描进行中" in text or "已释放" in text:
        return HTTPException(409, text)
    if "没有这个" in text or "不在" in text or "已被" in text:
        return HTTPException(404, text)
    return HTTPException(400, text)


def json(payload: dict):
    """统一用 JSONResponse 返回 —— 各域别各写各的。"""
    from fastapi.responses import JSONResponse

    return JSONResponse(payload)
