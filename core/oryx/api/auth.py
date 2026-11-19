"""令牌解析 —— 鉴权凭据的**唯一**取法。

为什么单独放这里
----------------
令牌有三个来源：

    · **cookie**（HttpOnly，JS 读不到）—— 页面自己用的
    · `X-Oryx-Token` 头 —— 命令行 / 测试 / 跨端 relay
    · `?t=` 查询参数 —— EventSource 与 CSS `url()` 带不了自定义头

三者的**优先级与取法必须全项目一致**。中间件和某个域各写一遍，
迟早出「中间件放行、域里又拒」的怪事。

真踩过（不是假设）
------------------
背景图 `GET /api/appearance/background.png` 由 CSS 的 `url()` 发起。
浏览器对同源子资源**会**自动带 cookie，所以中间件放行了；
但这个路由自己又校验了一遍令牌、且**只认 `?t=`** ——
于是 cookie 明明带上了还是 401，背景图永远加载不出来。
（上一轮前端「补上 `?t=`」的修法是错的：令牌已经不在 JS 里了，补不出来。）

规则
----
按 cookie → 头 → 查询参数取**第一个非空**的候选，交给调用方判断对不对。

**不在这里比对** —— 比对要用恒定时间比较（`secrets.compare_digest`），
且不同调用点的报错文案不同（中间件说「缺少或错误的访问令牌」，
背景图那条说「缺少访问令牌」）。
"""

from __future__ import annotations

from starlette.requests import Request

# 令牌的 cookie 名。与 server.py 里的定义保持一致。
COOKIE_NAME = "oryx_token"

# 请求头的名字（HTTP 头不区分大小写，但 FastAPI/Starlette 取的是小写形式）。
TOKEN_HEADER = "x-oryx-token"

# 查询参数的名字。
TOKEN_QUERY = "t"


def resolve_token_candidate(request: Request) -> str:
    """取出「最可能的那个」令牌候选。

    返回空串表示**什么都没拿到**。

    三个来源按  cookie → 头 → 查询参数  的顺序，
    取**第一个非空**的 —— 与中间件原本的行为完全一致，
    换过去不会改变任何现有请求的鉴权结果。
    """
    return (
        request.cookies.get(COOKIE_NAME)
        or request.headers.get(TOKEN_HEADER)
        or request.query_params.get(TOKEN_QUERY, "")
        or ""
    )
