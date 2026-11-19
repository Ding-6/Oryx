"""中间件层：跨域通用的横切逻辑。

现在有：
  · `errors.py` —— 全局异常处理 + 请求日志

放在这里的理由：这些是**横切**关注点（错误、日志、计时），
不属于任何单个域。塞进 `domains/` 里的任何一个都不对 ——
那是「业务」的位置。

规矩：域只写业务，不碰 HTTP 细节（错误怎么变成响应、日志怎么打）。
"""

from __future__ import annotations

from .errors import install

__all__ = ["install"]
