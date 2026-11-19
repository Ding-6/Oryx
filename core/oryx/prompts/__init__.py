"""系统提示词资源。

正文在 `system/*.md`，由 `loader` 负责拼装。
为什么要外置：主控提示原先是 `loop.py` 里一个 5950 字符的三引号常量，
改一句提示词要翻 Python 代码，改坏了跑起来才知道。

细节见 `loader.py` 的模块文档。
"""
from __future__ import annotations

from .loader import (
    PromptError,
    SECTION_ORDER,
    find_placeholders,
    load_system_prompt,
    raw_system_prompt,
    section_names,
    section_path,
)

__all__ = [
    "PromptError",
    "SECTION_ORDER",
    "find_placeholders",
    "load_system_prompt",
    "raw_system_prompt",
    "section_names",
    "section_path",
]
