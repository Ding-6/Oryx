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

from typing import Any

from fastapi import HTTPException

from ..ipc import IpcError


def is_kernel_unready(text: str) -> bool:
    """这是不是「Agent 内核还没就绪」。

    只认「内核未就绪」，**不认所有「未就绪」** ——
    「模型服务未就绪」是用户没配 provider，那要提示他去设置里配，
    跟「内核暂时没起来」不是一回事。
    """
    return "内核未就绪" in text


# IPC 错误 → HTTP 状态码的判据，全项目**只在这一处**维护。
#
# 以前 `_common` / workflow / checkpoint / schedule 各写一份 `_map`，
# 同一句内核报错在不同域会得到不同状态码（「只有创建者」在 A 域 403、
# 在 B 域 400；「内核未就绪」在 C 域 400）。现在各域都委托给这里，
# 域特有的提示词通过参数追加，不再复制整段 if 链。
_FORBIDDEN_HINTS = ("只有创建者",)
_CONFLICT_HINTS = ("已有扫描", "扫描进行中", "已释放")
_NOT_FOUND_HINTS = ("没有这个", "不在", "已被")


def map_ipc_error(
    exc: IpcError | str,
    *,
    not_found: tuple[str, ...] = (),
    conflict: tuple[str, ...] = (),
    forbidden: tuple[str, ...] = (),
) -> HTTPException:
    """IPC 错误 → HTTP 状态码（全项目唯一一套规则）。

    优先级：403 越权 > 503 依赖未就绪 > 409 状态冲突 >
    404 资源不存在 > 400 参数 / 业务不对。

    「内核未就绪」必须是 503：请求本身完全正确，只是内核还在启动，
    报 400 等于说「你写错了」，前端会把它当成用户的错来提示。
    """
    text = str(exc)
    if any(h in text for h in (*_FORBIDDEN_HINTS, *forbidden)):
        return HTTPException(403, text)
    if is_kernel_unready(text):
        return HTTPException(503, text)
    if any(h in text for h in (*_CONFLICT_HINTS, *conflict)):
        return HTTPException(409, text)
    if any(h in text for h in (*_NOT_FOUND_HINTS, *not_found)):
        return HTTPException(404, text)
    return HTTPException(400, text)


def rpc_failure(exc: IpcError) -> HTTPException:
    """把 IPC 错误映射成合适的 HTTP 状态码（各域统一入口）。

    内核侧抛的是 ValueError（参数 / 业务不对）和 RuntimeError（状态冲突），
    到这里要还原成 400 / 409 / 404，不然前端只能看到笼统的 500。
    """
    return map_ipc_error(exc)


def json(payload: dict[str, Any]):
    """统一用 JSONResponse 返回 —— 各域别各写各的。"""
    from fastapi.responses import JSONResponse

    return JSONResponse(payload)
