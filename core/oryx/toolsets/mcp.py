"""MCP server 的工具接入 —— server 侧返回什么我们不知道，一律当外部来源。

（从 `loop.py` 原样搬来 —— `self.` 一个字都没改，语义零变动。）
"""

from __future__ import annotations

from typing import Any

from ..policy import Risk
from ..tools import ToolSpec


class McpToolsMixin:
    def _register_mcp_tools(self) -> None:
        from ..mcp import MCPTool

        for server, client in self.mcp_clients.items():
            for tool in getattr(client, "tools", []):
                assert isinstance(tool, MCPTool)

                def _call(_tool: Any = tool, _client: Any = client, **kwargs: Any) -> dict[str, Any]:
                    return _client.call_tool(_tool.name, kwargs)

                self.registry[tool.qualified_name] = ToolSpec(
                    name=tool.qualified_name,
                    description=f"[{server}] {tool.description or tool.name}",
                    parameters=tool.input_schema,
                    risk=Risk(tool.risk_level()),
                    fn=_call,
                )
                # 登记进统一工具表 —— 三种来源一张表
                table = getattr(self, "tool_table", None)
                if table is not None:
                    table.add_mcp(self.registry[tool.qualified_name], server=server)
