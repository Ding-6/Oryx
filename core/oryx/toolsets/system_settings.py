"""Windows 系统设置读写（写操作一律 L2 审批）。

（从 `loop.py` 原样搬来 —— `self.` 一个字都没改，语义零变动。）
"""

from __future__ import annotations

from typing import Any

from ..policy import Risk
from ..tools import ToolSpec


class SystemSettingsMixin:
    def _register_system_settings(self) -> None:
        from .. import syssettings

        def _wrap(fn, name: str):
            def inner(**kwargs):
                try:
                    return fn(**kwargs)
                except Exception as exc:  # noqa: BLE001
                    return {"ok": False, "content": f"{type(exc).__name__}: {exc}", "meta": {"tool": name}}
            return inner

        self.registry["sys_power_status"] = ToolSpec(
            name="sys_power_status",
            description="读取电源与电池状态（AC 接入、电池剩余）。",
            parameters={"type": "object", "properties": {}, "required": []},
            risk=Risk.L0_READ,
            fn=_wrap(syssettings.power_status, "sys_power_status"),
        )
        self.registry["sys_lock_screen"] = ToolSpec(
            name="sys_lock_screen",
            description="立即锁屏。L2 动作，需要用户确认。",
            parameters={"type": "object", "properties": {}, "required": []},
            risk=Risk.L2_DESTRUCTIVE,
            fn=_wrap(syssettings.lock_screen, "sys_lock_screen"),
        )
        self.registry["sys_set_screen_timeout"] = ToolSpec(
            name="sys_set_screen_timeout",
            description="设置 AC 电源下屏幕关闭时间（秒，60..86400）。L2 动作。",
            parameters={
                "type": "object",
                "properties": {"seconds": {"type": "integer"}},
                "required": ["seconds"],
            },
            risk=Risk.L2_DESTRUCTIVE,
            fn=_wrap(lambda seconds: syssettings.set_screen_timeout_seconds(int(seconds)),
                     "sys_set_screen_timeout"),
        )
        self.registry["sys_display_settings"] = ToolSpec(
            name="sys_display_settings",
            description="读取主屏分辨率与缩放。",
            parameters={"type": "object", "properties": {}, "required": []},
            risk=Risk.L0_READ,
            fn=_wrap(syssettings.display_settings, "sys_display_settings"),
        )
        self.registry["sys_set_dpi_scaling"] = ToolSpec(
            name="sys_set_dpi_scaling",
            description="设置显示缩放（100/125/150/175/200/225/250/300）。L2 改动，需要确认。",
            parameters={
                "type": "object",
                "properties": {"scale": {"type": "integer"}},
                "required": ["scale"],
            },
            risk=Risk.L2_DESTRUCTIVE,
            fn=_wrap(lambda scale: syssettings.set_dpi_scaling(int(scale)), "sys_set_dpi_scaling"),
        )
        self.registry["sys_set_wallpaper"] = ToolSpec(
            name="sys_set_wallpaper",
            description="把指定图片设置为壁纸（PNG/JPG/BMP）。L2 改动。",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            risk=Risk.L2_DESTRUCTIVE,
            fn=_wrap(syssettings.set_wallpaper, "sys_set_wallpaper"),
        )
        self.registry["sys_mouse_settings"] = ToolSpec(
            name="sys_mouse_settings",
            description="读取鼠标双击速度、指针速度、滚轮行数。",
            parameters={"type": "object", "properties": {}, "required": []},
            risk=Risk.L0_READ,
            fn=_wrap(syssettings.mouse_settings, "sys_mouse_settings"),
        )
        self.registry["sys_input_methods"] = ToolSpec(
            name="sys_input_methods",
            description="列出已启用的输入法。",
            parameters={"type": "object", "properties": {}, "required": []},
            risk=Risk.L0_READ,
            fn=_wrap(syssettings.input_method_list, "sys_input_methods"),
        )
