"""Windows 输入注入（ctypes + SendInput，不依赖第三方库）。

坐标约定：调用方传**归一化坐标** (0.0–1.0)，相对主屏左上角。
这样手机端不关心 PC 分辨率，画面缩放也不影响定位。
"""

from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes

def dry_run() -> bool:
    """测试时置 ORYX_REMOTE_DRY_RUN=1：只计算坐标与参数，不真的操作键鼠。"""
    return os.environ.get("ORYX_REMOTE_DRY_RUN") == "1"

user32 = ctypes.windll.user32

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_WHEEL = 0x0800

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", INPUT_UNION)]


def _send(*inputs: INPUT) -> None:
    if not inputs or dry_run():
        return
    array = (INPUT * len(inputs))(*inputs)
    user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT))


def _mouse(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> INPUT:
    return INPUT(
        type=INPUT_MOUSE,
        u=INPUT_UNION(
            mi=MOUSEINPUT(dx=dx, dy=dy, mouseData=data & 0xFFFFFFFF, dwFlags=flags, time=0, dwExtraInfo=0)
        ),
    )


def _key(vk: int = 0, scan: int = 0, flags: int = 0) -> INPUT:
    return INPUT(
        type=INPUT_KEYBOARD,
        u=INPUT_UNION(ki=KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)),
    )


def screen_size() -> tuple[int, int]:
    return (
        user32.GetSystemMetrics(0),  # SM_CXSCREEN
        user32.GetSystemMetrics(1),  # SM_CYSCREEN
    )


def _target(nx: float, ny: float) -> tuple[int, int]:
    width, height = screen_size()
    x = max(0, min(width - 1, int(nx * width)))
    y = max(0, min(height - 1, int(ny * height)))
    return x, y




def move_to(nx: float, ny: float) -> tuple[int, int]:
    """归一化坐标 → 屏幕绝对坐标。"""
    width, height = screen_size()
    x = max(0, min(width - 1, int(nx * width)))
    y = max(0, min(height - 1, int(ny * height)))
    dx = int(x * 65535 / max(width - 1, 1))
    dy = int(y * 65535 / max(height - 1, 1))
    _send(_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, dx, dy))
    return x, y


def click(nx: float, ny: float, button: str = "left", double: bool = False) -> tuple[int, int]:
    x, y = move_to(nx, ny)
    time.sleep(0.02)
    down = MOUSEEVENTF_RIGHTDOWN if button == "right" else MOUSEEVENTF_LEFTDOWN
    up = MOUSEEVENTF_RIGHTUP if button == "right" else MOUSEEVENTF_LEFTUP
    rounds = 2 if double else 1
    for _ in range(rounds):
        _send(_mouse(down), _mouse(up))
        time.sleep(0.05)
    return x, y


def swipe(
    x1: float, y1: float, x2: float, y2: float, duration_ms: int = 300, steps: int = 12
) -> None:
    """拖动：按下 → 分步移动 → 抬起。"""
    steps = max(2, min(60, steps))
    delay = max(duration_ms, 30) / 1000 / steps
    _send(_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, *_absolute(x1, y1)))
    time.sleep(0.01)
    _send(_mouse(MOUSEEVENTF_LEFTDOWN))
    for index in range(1, steps + 1):
        ratio = index / steps
        nx, ny = x1 + (x2 - x1) * ratio, y1 + (y2 - y1) * ratio
        _send(_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, *_absolute(nx, ny)))
        time.sleep(delay)
    _send(_mouse(MOUSEEVENTF_LEFTUP))


def _absolute(nx: float, ny: float) -> tuple[int, int]:
    width, height = screen_size()
    x = max(0, min(width - 1, int(nx * width)))
    y = max(0, min(height - 1, int(ny * height)))
    return int(x * 65535 / max(width - 1, 1)), int(y * 65535 / max(height - 1, 1))


def wheel(delta: int) -> None:
    _send(_mouse(MOUSEEVENTF_WHEEL, data=int(delta)))


VK_MAP = {
    "esc": 0x1B,
    "enter": 0x0D,
    "tab": 0x09,
    "space": 0x20,
    "backspace": 0x08,
    "delete": 0x2E,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "win": 0x5B,
    "ctrl": 0x11,
    "alt": 0x12,
    "shift": 0x10,
}


def key(name: str, action: str = "tap") -> None:
    vk = VK_MAP.get((name or "").lower())
    if vk is None:
        return
    if action == "down":
        _send(_key(vk=vk))
    elif action == "up":
        _send(_key(vk=vk, flags=KEYEVENTF_KEYUP))
    else:
        _send(_key(vk=vk), _key(vk=vk, flags=KEYEVENTF_KEYUP))


def type_text(text: str) -> None:
    """按 Unicode 逐字符输入，绕过键盘布局。"""
    for char in text or "":
        code = ord(char)
        _send(_key(scan=code, flags=KEYEVENTF_UNICODE), _key(scan=code, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
        time.sleep(0.002)


def apply_event(event: dict) -> dict:
    """把手机传来的一条输入事件落到系统上。返回执行结果，便于回执。"""
    kind = event.get("kind")
    try:
        if kind == "tap":
            position = click(
                float(event.get("x", 0)), float(event.get("y", 0)),
                event.get("button", "left"), bool(event.get("double")),
            )
            return {"ok": True, "at": position}
        if kind == "move":
            return {"ok": True, "at": move_to(float(event.get("x", 0)), float(event.get("y", 0)))}
        if kind == "swipe":
            swipe(
                float(event.get("x1", 0)), float(event.get("y1", 0)),
                float(event.get("x2", 0)), float(event.get("y2", 0)),
                int(event.get("duration_ms", 300)),
            )
            return {"ok": True}
        if kind == "wheel":
            wheel(int(event.get("delta", 0)))
            return {"ok": True}
        if kind == "key":
            key(str(event.get("name", "")), str(event.get("action", "tap")))
            return {"ok": True}
        if kind == "text":
            type_text(str(event.get("text", "")))
            return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": False, "error": f"未知输入类型：{kind}"}
