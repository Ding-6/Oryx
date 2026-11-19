"""工具集 —— 按能力拆分的 mixin。

为什么拆
--------
`loop.py` 一度 4859 行，其中 21 个 `_register_*` 方法占了一大半。
「工具从哪来」这件事被埋在一个巨大的类里，看不清边界。

拆法是 **mixin**，不是「函数 + runtime 参数」——
后者要把方法体里所有 `self.` 换成 `runtime.`
（`_register_mcp_tools` 里有 164 处），机械替换容易改坏。
mixin 把方法**原样搬走**，`self.` 一个字都不用改，语义零变动。

每个 mixin 对应**一个能力**：Office、浏览器、MCP、定时任务……
**边界清楚**，加新能力就是加一个文件，不用碰主类。
"""

from __future__ import annotations

from .agents import AgentToolsMixin
from .automation import AutomationToolsMixin
from .browser import BrowserToolsMixin
from .defer import DeferToolsMixin
from .doc import DocToolsMixin
from .index import IndexToolsMixin
from .manage import ManageToolsMixin
from .mcp import McpToolsMixin
from .memory import MemoryToolsMixin
from .office import OfficeToolsMixin
from .plugin_skills import PluginSkillsToolsMixin
from .prompt import PromptToolsMixin
from .remote import RemoteToolsMixin
from .sandbox import SandboxToolsMixin
from .system_settings import SystemSettingsMixin
from .terminal import TerminalToolsMixin
from .tool_search import ToolSearchToolsMixin
from .use_skill import UseSkillToolsMixin
from .workflow import WorkflowToolsMixin

__all__ = [
    "AgentToolsMixin",
    "AutomationToolsMixin",
    "BrowserToolsMixin",
    "DeferToolsMixin",
    "DocToolsMixin",
    "IndexToolsMixin",
    "ManageToolsMixin",
    "McpToolsMixin",
    "MemoryToolsMixin",
    "OfficeToolsMixin",
    "PluginSkillsToolsMixin",
    "PromptToolsMixin",
    "RemoteToolsMixin",
    "SandboxToolsMixin",
    "SystemSettingsMixin",
    "TerminalToolsMixin",
    "ToolSearchToolsMixin",
    "UseSkillToolsMixin",
    "WorkflowToolsMixin",
]
