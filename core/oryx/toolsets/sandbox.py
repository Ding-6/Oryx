"""命令沙箱 —— 沙箱里跑，出错不伤主环境。

（从 `loop.py` 原样搬来 —— `self.` 一个字都没改，语义零变动。）
"""

from __future__ import annotations

from typing import Any
from pathlib import Path
from ..policy import PolicyEngine, Risk
from ..tools import ToolSpec, build_registry, tool_result_text


class SandboxToolsMixin:
    def _register_sandbox_tool(self) -> None:
        """shell_sandbox：比裸 shell 多 env 剔密钥 + 工作目录监禁（WorkBuddy 沙箱思路）。

        风险定 L1：黑名单规则仍会把命中命令升级成 L2 走审批，
        沙箱只缩爆半径不替代审批。

        **DisableSandbox 开关在暴露层生效**（见 ``_exposed_registry``），
        不在这里判断 —— 注册时判断的话，用户在界面上关掉沙箱后
        得重启内核才生效。现在下一轮就生效。
        """
        from ..sandbox import resolve_jail, run_sandboxed

        def _fallback_jail() -> str:
            roots = self.config.data.get("allowed_roots") or []
            workspace = self.config.data.get("workspace") or ""
            return workspace or (roots[0] if roots else str(Path.home()))

        def _run(command: str, root: str = "") -> dict[str, Any]:
            jail = resolve_jail(root or None, _fallback_jail())
            # 策略引擎已对 root 做过 allowed_roots 检查（root 是路径参数），
            # 这里再兜一层：默认工作区之外的目录不允许作为沙箱根。
            if self.policy.path_forbidden(str(jail)):
                return {"ok": False, "content": f"沙箱目录位于禁区：{jail}"}
            return run_sandboxed(command, jail)

        self.registry["shell_sandbox"] = ToolSpec(
            name="shell_sandbox",
            description=(
                "在沙箱里执行 shell 命令：子进程看不到 API 密钥等敏感环境变量，"
                "工作目录限定在工作区内。执行命令时优先用它，而不是 shell。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的命令"},
                    "root": {
                        "type": "string",
                        "description": "工作目录（可选，默认工作区；必须在允许范围内）",
                    },
                },
                "required": ["command"],
            },
            risk=Risk.L1_WRITE,
            fn=_run,
        )
