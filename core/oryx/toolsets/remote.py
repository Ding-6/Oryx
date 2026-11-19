"""跨端远控：看屏、输入、状态。

（从 `loop.py` 原样搬来 —— `self.` 一个字都没改，语义零变动。）
"""

from __future__ import annotations

from typing import Any
from ..policy import PolicyEngine, Risk
from ..tools import ToolSpec, build_registry, tool_result_text
import threading


class RemoteToolsMixin:
    def _register_remote_tools(self) -> None:
        from ..remote import RemoteHost
        from ..remote.host import normalize_policy

        self._remote: dict[str, Any] = {"host": None, "thread": None}

        def _start(relay_url: str = "", code: str = "", policy: int = 0) -> dict[str, Any]:
            if self._remote.get("host") is not None:
                host = self._remote["host"]
                return {"ok": False, "content": f"远程控制已在运行，配对码 {host.code}"}
            url = relay_url or self.config.data.get("remote_relay", "")
            if not url:
                return {
                    "ok": False,
                    "content": "还没有配置中转地址。请在配置里填 remote_relay，例如 wss://你的中转域名",
                }

            level = normalize_policy(
                policy or self.config.data.get("remote_policy", 0)
            )
            host = RemoteHost(
                url,
                code=code or None,
                fps=int(self.config.data.get("remote_fps", 8)),
                policy=level,
                on_event=lambda event: self.bus.publish("remote.event", **event),
            )

            def _runner() -> None:
                import asyncio as _asyncio

                loop = _asyncio.new_event_loop()
                _asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(host.run())
                finally:
                    loop.close()

            thread = threading.Thread(target=_runner, name="oryx-remote", daemon=True)
            thread.start()
            self._remote.update(host=host, thread=thread)
            self.bus.publish("remote.started", code=host.code, relay=url)
            return {
                "ok": True,
                "content": {
                    "code": host.code,
                    "relay": url,
                    "policy": host.policy,
                    "policy_label": host.status()["policy_label"],
                    "hint": "在手机 App 里输入这个配对码即可连接",
                },
            }

        def _stop() -> dict[str, Any]:
            host = self._remote.get("host")
            if host is None:
                return {"ok": False, "content": "当前没有运行中的远程会话"}
            host.stop()
            self._remote.update(host=None, thread=None)
            self.bus.publish("remote.stopped")
            return {"ok": True, "content": "已停止远程控制"}

        def _status() -> dict[str, Any]:
            host = self._remote.get("host")
            if host is None:
                return {"ok": True, "content": {"running": False}}
            return {"ok": True, "content": {"running": True, **host.status()}}

        self.registry["remote_start"] = ToolSpec(
            name="remote_start",
            description=(
                "开启手机远程控制：把本机屏幕推送到中继，并接受手机上的点击/滑动。"
                "返回配对码，在手机 App 里输入即可。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "relay_url": {"type": "string", "description": "中转地址，留空用配置里的"},
                    "code": {"type": "string", "description": "自定义配对码，留空自动生成"},
                    "policy": {
                        "type": "integer",
                        "description": "安全策略等级：1=只读（仅看屏）2=可控（拦系统键）3=完整，留空用配置",
                    },
                },
                "required": [],
            },
            risk=Risk.L2_DESTRUCTIVE,  # 暴露屏幕，必须用户确认
            fn=_start,
        )
        self.registry["remote_status"] = ToolSpec(
            name="remote_status",
            description="查看远程控制状态：是否连接、已发送多少帧。",
            parameters={"type": "object", "properties": {}, "required": []},
            risk=Risk.L0_READ,
            fn=_status,
        )
        self.registry["remote_stop"] = ToolSpec(
            name="remote_stop",
            description="停止远程控制，断开中转连接。",
            parameters={"type": "object", "properties": {}, "required": []},
            risk=Risk.L1_WRITE,
            fn=_stop,
        )
