"""全局异常处理 —— 把「静默 500」变成「有据可查的 500」。

问题
----
体检发现：
  · **0 个全局异常处理器** —— 任何未捕获异常直接冒泡成 FastAPI 默认的
    `{"detail":"Internal Server Error"}`，**服务端日志里也没有任何线索**
  · **0 处日志配置** —— 项目里没有 `logging` 的任何配置，
    线上出问题时只能靠猜
  · **211 处 `except Exception:` 裸吞** —— 异常被吞掉后，
    用户看到的是「功能没反应」，开发看到的是「什么都没发生」

目标
----
1. **兜住所有未捕获异常** → 统一错误体 + **带上下文的错误日志**（traceback）
2. **HTTPException 统一形状** → 保留 `detail`（兼容旧前端），补 `code`/`message`
3. **请求日志** → 每个请求一行，含耗时与状态码

关于「保留 detail」
-------------------
见 `oryx/api/response.py` 的模块文档 ——
195 个前端调用点大量读 `x.detail`，去掉会让所有错误提示退化成「请求失败」。
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("oryx.api")


def error_body(status: int, message: str, data: Any = None) -> dict:
    """统一错误体。

    `detail` 保留 —— 旧前端读它；`code`/`message` 给新代码用。
    """
    return {
        "code": status,
        "data": data,
        "message": message,
        "detail": message,
    }


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """HTTPException → 统一形状。

    4xx 是「用户/前端的问题」，记 warning 就够，不用打 traceback
    （否则日志会被 404 刷爆，真正的错误反而看不见）。
    """
    detail = exc.detail
    if isinstance(detail, dict):
        message = str(detail.get("message") or detail)
        body = error_body(exc.status_code, message, detail)
    else:
        message = str(detail)
        body = error_body(exc.status_code, message)
    if exc.status_code >= 500:
        log.error(
            "%s %s → %s", request.method, request.url.path, exc.status_code,
            exc_info=exc,
        )
    else:
        log.warning("%s %s → %s：%s", request.method, request.url.path, exc.status_code, message)
    return JSONResponse(status_code=exc.status_code, content=body)


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """入参校验失败 → 422，统一形状。

    FastAPI 默认给的是 `{"detail": [{loc, msg, type}, ...]}` ——
    数组形式对前端不好用（要遍历找第一条 msg）。
    这里压成一句话放进 `message`，**同时保留原始数组在 `data` 里**，
    调试时能拿到字段级信息。
    """
    errors = exc.errors()
    if errors:
        first = errors[0]
        loc = ".".join(str(x) for x in first.get("loc", ()))
        message = f"{loc}：{first.get('msg', '参数不合法')}" if loc else str(first.get("msg", "参数不合法"))
    else:
        message = "参数不合法"
    log.warning("%s %s → 422：%s", request.method, request.url.path, message)
    return JSONResponse(status_code=422, content=error_body(422, message, errors))


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """**任何未捕获异常** → 500 + 带 traceback 的日志。

    这是最要紧的一条：以前这类异常在服务端**没有任何记录**，
    只在前端表现为「没反应」，排障时完全无从下手。

    响应体里**不放异常细节** —— 那会泄露路径与内部结构。
    细节只进日志。
    """
    log.exception(
        "未捕获异常 %s %s：%s", request.method, request.url.path, exc,
    )
    return JSONResponse(
        status_code=500,
        content=error_body(500, "服务器内部错误，请查看服务端日志"),
    )


async def request_logging_middleware(request: Request, call_next):
    """每个请求一行日志：方法、路径、状态码、耗时、请求 id。

    请求 id 会回写响应头 `X-Request-Id`，
    出问题时用户把这个 id 报过来，就能定位到具体那一条日志。
    """
    rid = uuid.uuid4().hex[:12]
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        # 交给上面的全局异常处理器 —— 这里不吞，让它继续往上走
        raise
    finally:
        pass
    elapsed = (time.perf_counter() - started) * 1000
    response.headers["X-Request-Id"] = rid
    log.info(
        "%s %s → %s（%.0fms）rid=%s",
        request.method, request.url.path, response.status_code, elapsed, rid,
    )
    return response


def install(app: FastAPI) -> None:
    """把异常处理与请求日志装到 app 上。

    **注意与既有中间件的顺序**：`_require_token` 是业务中间件，
    本函数装的 `request_logging_middleware` 会在它**外层**，
    所以连 401/403 也会留下日志（这正是排障时最需要的）。
    """
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.middleware("http")(request_logging_middleware)
