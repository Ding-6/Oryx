"""日志配置 —— 项目此前**完全没有**日志。

问题
----
体检发现：项目里 `logging` / `logger` / `basicConfig` **一处都没有**。
后果：
  · 服务端出问题，日志里**什么都没有**（只有 Windows 事件日志里的栈回溯）
  · 线上「功能没反应」，无法判断是前端没发、后端没收到、还是内核没回
  · 排查只能靠加 print 试 —— 而 `print` 只在 stdout，重定向后就没了

现在有
------
1. **控制台 + 文件**双输出（文件按大小轮转，不会撑爆磁盘）
2. **日志文件跟着数据目录走**（`D:\OryxData\logs\`）——
   不写死在 C 盘，用户明确要求过「所有落盘都在 D 盘」
3. 级别用环境变量 `ORYX_LOG_LEVEL` 调，默认 INFO
4. `ORYX_LOG_FILE=0` 可以关掉文件输出（只留控制台）

为什么不用 structlog / loguru
---------------------------
多一个依赖就多一处版本风险，而标准库 logging 已经够用。
本项目连依赖清单都是刚补上的（阶段一发现完全没有），
先把标准库用对，不要一上来就加依赖。
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

_LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup_logging(data_root: Path | None = None) -> None:
    """配置根日志。重复调用只生效一次。

    `data_root` 一般传 `paths.data_root()` —— 日志跟着数据目录走。
    """
    global _configured
    if _configured:
        return
    _configured = True

    level_name = os.environ.get("ORYX_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    # 已有 handler 就别重复加（uvicorn reload 时会重新 import）
    if root.handlers:
        return

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    if os.environ.get("ORYX_LOG_FILE", "1") != "0":
        base = data_root or Path("D:/OryxData")
        log_dir = base / "logs"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            # 10MB 一个文件，最多留 5 个 —— 不会撑爆磁盘
            handler = logging.handlers.RotatingFileHandler(
                log_dir / "oryx.log", maxBytes=10 * 1024 * 1024, backupCount=5,
                encoding="utf-8",
            )
            handler.setFormatter(formatter)
            root.addHandler(handler)
        except OSError:
            # 落不了盘就只留控制台 —— 日志文件失败不该让服务起不来
            root.warning("[oryx] 日志文件不可用，只输出到控制台")

    # uvicorn 自己的 logger 默认级别是 INFO，但它会重复打 access log
    # （我们中间件已经打过一行了）。关掉它的 access log，保留错误。
    logging.getLogger("uvicorn.access").disabled = True
