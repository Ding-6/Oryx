"""配置与开关的读写。

（从 `loop.py` 原样搬来 —— `self.` 一个字都没改，语义零变动。）
"""

from __future__ import annotations

from typing import Any
from ..checkpoint import CheckpointService
from pathlib import Path
from ..policy import PolicyEngine, Risk
from ..projectdoc import ProjectDoc
from ..tools import ToolSpec, build_registry, tool_result_text


class ManageToolsMixin:
    def _register_manage_tools(self) -> None:
        from .. import manage

        def _apps(limit: int = 200) -> dict[str, Any]:
            return {"ok": True, "content": manage.list_installed_apps(int(limit))}

        def _workspace() -> dict[str, Any]:
            return {"ok": True, "content": manage.workspace_summary(self.config)}

        def _switch_workspace(path: str) -> dict[str, Any]:
            try:
                result = manage.switch_workspace(self.config, path)
            except ValueError as exc:
                return {"ok": False, "content": str(exc)}
            self.project_doc = ProjectDoc(self.config.data.get("workspace") or Path.cwd())
            # 工作区换了，项目级配置也跟着换（先换配置，再据此重建策略引擎，
            # 否则新工作区的项目规则要等下一次启动才生效）
            self.config.reload_project_scopes()
            self.policy = PolicyEngine(
                self.config.allowed_roots, self.config.forbidden_paths, self.config
            )
            self.checkpoints = CheckpointService(
                self.store, self.config.allowed_roots
            )
            # 记忆里的条件规则也跟着换工作区
            try:
                self.memory.refresh(self.config.data.get("workspace") or None)
            except Exception:
                pass
            self.bus.publish("workspace.changed", workspace=result["workspace"])
            return {"ok": True, "content": result}

        def _providers() -> dict[str, Any]:
            providers = self.config.data.get("providers") or {}
            return {
                "ok": True,
                "content": {
                    "active": self.config.active_provider_name,
                    "local": self.config.data.get("local_provider"),
                    "items": [
                        {"name": name, **manage.mask_key(spec)} for name, spec in providers.items()
                    ],
                },
            }

        def _add_provider(name: str, spec: dict[str, Any]) -> dict[str, Any]:
            try:
                result = manage.upsert_provider(self.config, name, spec)
            except ValueError as exc:
                return {"ok": False, "content": str(exc)}
            return {"ok": True, "content": {"name": result["name"], "type": result["type"]}}

        def _use_provider(name: str) -> dict[str, Any]:
            try:
                result = manage.switch_provider(self.config, name)
            except ValueError as exc:
                return {"ok": False, "content": str(exc)}
            return {"ok": True, "content": result}

        self.registry["list_apps"] = ToolSpec(
            name="list_apps",
            description=(
                "列出本机已安装的程序（名称、版本、发布者）。"
                "用户问「我装了什么」「有没有 XX 软件」时用它。"
            ),
            parameters={
                "type": "object",
                "properties": {"limit": {"type": "integer", "description": "最多返回条数"}},
                "required": [],
            },
            risk=Risk.L0_READ,
            fn=_apps,
        )
        self.registry["workspace_info"] = ToolSpec(
            name="workspace_info",
            description="查看当前工作目录与允许访问的路径范围。",
            parameters={"type": "object", "properties": {}, "required": []},
            risk=Risk.L0_READ,
            fn=_workspace,
        )
        self.registry["workspace_switch"] = ToolSpec(
            name="workspace_switch",
            description="切换工作目录。切换后项目文档与允许路径都跟着走。",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string", "description": "目标目录"}},
                "required": ["path"],
            },
            risk=Risk.L1_WRITE,
            fn=_switch_workspace,
        )
        self.registry["list_providers"] = ToolSpec(
            name="list_providers",
            description="列出可用的模型服务及其当前状态。",
            parameters={"type": "object", "properties": {}, "required": []},
            risk=Risk.L0_READ,
            fn=_providers,
        )
        self.registry["provider_add"] = ToolSpec(
            name="provider_add",
            description=(
                "接入一个新的模型服务（自定义大模型）。type 用 anthropic 或 openai；"
                "openai 兼容 DeepSeek、混元、Ollama、vLLM 等。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "服务名，如 deepseek、my-llm"},
                    "type": {"type": "string", "enum": ["anthropic", "openai"]},
                    "base_url": {"type": "string"},
                    "api_key": {"type": "string"},
                    "model": {"type": "string"},
                },
                "required": ["name", "type", "base_url", "model"],
            },
            risk=Risk.L1_WRITE,
            fn=_add_provider,
        )
        self.registry["provider_use"] = ToolSpec(
            name="provider_use",
            description="切换当前使用的模型服务。",
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            risk=Risk.L1_WRITE,
            fn=_use_provider,
        )
