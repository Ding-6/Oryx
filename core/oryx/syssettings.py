"""Windows 系统设置工具集（对齐 Marvis 的 win_use_mcp toolkit）。

覆盖：电源、显示、输入设备、个性化、通知、区域语言。
所有改动走 L2，必须经用户确认；只读项是 L0。

设计原则（来自 rea 报告 §2.3）：
- 规格（specs）→ 模型看的是 JSON Schema
- 卡片（cards）→ 用户看的是设置项描述
- 一律用 PowerShell + CIM，避免 GUI 自动化漂移；个别读写不上的项降级到 registry。
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PS_TIMEOUT = 25


def _run_ps(script: str) -> tuple[bool, str]:
    """执行一段 PowerShell，返回 (ok, output)。"""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=PS_TIMEOUT,
            creationflags=CREATE_NO_WINDOW,
        )
        out = (proc.stdout or "").strip()
        if proc.returncode != 0:
            return False, out or proc.stderr or f"exit {proc.returncode}"
        return True, out
    except subprocess.TimeoutExpired:
        return False, "powershell 超时"
    except FileNotFoundError:
        return False, "未找到 powershell"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def _ok(content: Any) -> dict[str, Any]:
    return {"ok": True, "content": content}


def _err(msg: str) -> dict[str, Any]:
    return {"ok": False, "content": msg}


# ------------------------------------------------------------------ 电源


def power_status() -> dict[str, Any]:
    """读取电源状态：电池百分比、AC 接入、最后唤醒源。"""
    script = (
        "$b = Get-CimInstance -ClassName BatteryStatus -ErrorAction SilentlyContinue |"
        " Select-Object -First 1;"
        "$ac = (Get-CimInstance -ClassName Win32_Battery -ErrorAction SilentlyContinue |"
        " Select-Object -First 1).BatteryStatus;"
        "{ac=$null -eq $ac; battery=$b} | ConvertTo-Json -Depth 2"
    )
    ok, out = _run_ps(script)
    if not ok:
        return _err(out)
    try:
        data = json.loads(out) if out else {}
    except Exception:
        data = {"raw": out}
    return _ok(data)


def lock_screen() -> dict[str, Any]:
    """立即锁屏。L2 动作，必须经过用户确认。"""
    script = "rundll32.exe user32.dll,LockWorkStation; 'locked'"
    ok, out = _run_ps(script)
    return _ok({"locked": ok, "detail": out}) if ok else _err(out)


def set_screen_timeout_seconds(seconds: int) -> dict[str, Any]:
    """设置 AC 状态下的屏幕关闭时间（秒）。仅改电源计划 user 副本，不动 system 副本。"""
    if seconds < 60 or seconds > 24 * 3600:
        return _err("seconds 必须在 60..86400 之间")
    script = (
        "powercfg /change monitor-timeout-ac " + str(int(seconds))
    )
    ok, out = _run_ps(script)
    if not ok:
        return _err(out)
    return _ok({"applied": True, "seconds": int(seconds)})


# ------------------------------------------------------------------ 显示


def display_settings() -> dict[str, Any]:
    """读取当前显示设置：分辨率、缩放、主屏。"""
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$s = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
        "{width=$s.Width; height=$s.Height}"
        " | ConvertTo-Json"
    )
    ok, out = _run_ps(script)
    if not ok:
        return _err(out)
    try:
        data = json.loads(out) if out else {}
    except Exception:
        data = {"raw": out}
    return _ok(data)


def set_dpi_scaling(scale: int) -> dict[str, Any]:
    """设置显示缩放（100/125/150/175/200）。L2 改动。"""
    if scale not in (100, 125, 150, 175, 200, 225, 250, 300):
        return _err("scale 必须是 100/125/150/175/200/225/250/300")
    script = (
        f"Set-ItemProperty -Path 'HKCU:\\Control Panel\\Desktop\\WindowMetrics'"
        f" -Name LogPixels -Value {int(scale * 96 / 100)} -Force;"
        f"'applied={{scale}}'"
    ).replace("{scale}", str(scale))
    ok, out = _run_ps(script)
    return _ok({"applied": ok, "scale": scale, "detail": out}) if ok else _err(out)


def set_wallpaper(path: str) -> dict[str, Any]:
    """把指定图片设为壁纸。L2 改动。"""
    target = os.path.expandvars(os.path.expanduser(path))
    if not os.path.exists(target):
        return _err(f"图片不存在：{target}")
    script = (
        "$p = '" + target.replace("'", "''") + "';"
        "$s = New-Object System.Windows.Forms.Form;"
        "[System.Windows.Forms.SystemInformation]::UserInteractive;"
        "Add-Type -Namespace W -Name S -MemberDefinition '[DllImport(\"user32.dll\", CharSet=CharSet.Auto)] public static extern int SystemParametersInfo(int a, int b, string c, int d);';"
        "[W.S]::SystemParametersInfo(0x0014, 0, $p, 0x00000001);"
        "'applied'"
    )
    ok, out = _run_ps(script)
    return _ok({"applied": ok, "path": target, "detail": out}) if ok else _err(out)


# ------------------------------------------------------------------ 输入设备


def mouse_settings() -> dict[str, Any]:
    """读取鼠标设置：双击间隔、指针速度、滚轮行数。"""
    script = (
        "$u = 'HKCU:\\Control Panel\\Mouse';"
        "{double=$(Get-ItemProperty -Path $u -Name DoubleClickSpeed -ErrorAction SilentlyContinue).DoubleClickSpeed;"
        " speed=$(Get-ItemProperty -Path $u -Name MouseSpeed -ErrorAction SilentlyContinue).MouseSpeed;"
        " hwheel=$(Get-ItemProperty -Path $u -Name WheelScrollLines -ErrorAction SilentlyContinue).WheelScrollLines}"
        " | ConvertTo-Json"
    )
    ok, out = _run_ps(script)
    if not ok:
        return _err(out)
    try:
        data = json.loads(out) if out else {}
    except Exception:
        data = {"raw": out}
    return _ok(data)


# ------------------------------------------------------------------ 区域语言


def input_method_list() -> dict[str, Any]:
    """列出已安装的输入法（keyboards 而非真正 IME 的运行时切换，留作占位）。"""
    script = (
        "$k = Get-WinUserLanguageList;"
        "$k | ForEach-Object { '{0}|{1}|{2}' -f $_.LanguageTag, $_.InputMethodTips, $_.NativeName }"
    )
    ok, out = _run_ps(script)
    if not ok:
        return _err(out)
    items = [line for line in out.splitlines() if line.strip()]
    return _ok({"items": items, "count": len(items)})


# ------------------------------------------------------------------ 能力清单

def switches() -> dict[str, Any]:
    """系统设置能力的分组清单（给 UI / 工具目录用，不执行任何操作）。

    分成电源、显示、输入三组。每项标了 read / write —— 界面据此决定
    哪些能直接展示、哪些必须先弹确认（写操作属于 L2）。
    """
    return {
        "power": {
            "label": "电源",
            "items": [
                {"name": "power_status", "label": "电源与电池状态", "action": "read"},
                {"name": "lock_screen", "label": "锁定屏幕", "action": "write"},
                {"name": "set_screen_timeout_seconds", "label": "设置息屏时间", "action": "write"},
            ],
        },
        "display": {
            "label": "显示",
            "items": [
                {"name": "display_settings", "label": "分辨率与缩放", "action": "read"},
                {"name": "set_dpi_scaling", "label": "设置缩放比例", "action": "write"},
                {"name": "set_wallpaper", "label": "更换壁纸", "action": "write"},
            ],
        },
        "input": {
            "label": "输入",
            "items": [
                {"name": "mouse_settings", "label": "鼠标设置", "action": "read"},
                {"name": "input_method_list", "label": "已装输入法", "action": "read"},
            ],
        },
    }


