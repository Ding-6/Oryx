"""读系统剪贴板：文本、图片、文件。

为什么放在后端而不是前端：
    浏览器**读不到剪贴板里的文件**。
    剪贴板里的文件是 CF_HDROP 格式（一个 DROPFILES 结构 + 文件路径列表），
    那是 Win32 的东西，Web 页面没有权限碰。
    唯一的绕法是 Electron/Tauri 的原生插件 —— 但本项目的后端本来就是
    本机 Python 进程，用 ctypes 直接调 Win32 更省事，也不必给桌面端加依赖。

    顺带把「读文本」「读图片」也一起做了 ——
    前端 navigator.clipboard.read() 要权限、还常常被浏览器拒，
    走本机后端稳定得多。

三种内容：
    text   CF_UNICODETEXT —— 纯文字
    image  CF_DIB / CF_BITMAP —— 剪贴板里的位图，转成 PNG
    files  CF_HDROP —— 文件路径列表，直接就是「粘贴文件」

非 Windows 平台：只有 text 能读（走 tkinter 那套），文件与图片不支持。
"""

from __future__ import annotations

import base64
import ctypes
import os
import struct
import sys
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any

# ── Win32 常量 ────────────────────────────────────────────────
CF_TEXT = 1
CF_BITMAP = 2
CF_DIB = 8
CF_UNICODETEXT = 13
CF_HDROP = 15


MAX_FILES = 50
MAX_IMAGE_BYTES = 20 * 1024 * 1024


def _user32() -> Any:
    return ctypes.WinDLL("user32", use_last_error=True)


def _kernel32() -> Any:
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _open_clipboard() -> bool:
    """打开剪贴板。

    剪贴板是**全局独占**的 —— 别的程序（比如剪贴板管理器、输入法）
    正开着它的时候 OpenClipboard 会失败。
    所以要重试几次：这是常态，不是异常。
    """
    u = _user32()
    u.OpenClipboard.restype = wintypes.BOOL
    for attempt in range(6):
        if u.OpenClipboard(None):
            return True
        # GetLastError 抢到别人的剪贴板时会被系统安排重试，等一下再来
        time.sleep(0.02 + attempt * 0.03)
    return False


def _close_clipboard() -> None:
    _user32().CloseClipboard()


def _get_clipboard_data(fmt: int) -> int:
    u = _user32()
    u.GetClipboardData.restype = wintypes.HANDLE
    u.GetClipboardData.argtypes = [wintypes.UINT]
    return int(u.GetClipboardData(fmt) or 0)


def _lock_global(hwnd: int) -> int:
    k = _kernel32()
    k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalLock.argtypes = [wintypes.HGLOBAL]
    return int(k.GlobalLock(hwnd) or 0)


def _unlock_global(hwnd: int) -> None:
    k = _kernel32()
    k.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    k.GlobalUnlock(hwnd)


def _global_size(hwnd: int) -> int:
    k = _kernel32()
    k.GlobalSize.restype = ctypes.c_size_t
    k.GlobalSize.argtypes = [wintypes.HGLOBAL]
    return int(k.GlobalSize(hwnd) or 0)


# ── 各格式读取 ────────────────────────────────────────────────

def _read_text() -> str:
    """CF_UNICODETEXT。

    读不出时退到 CF_TEXT（ANSI）—— 有些程序只放 ANSI 格式。
    """
    for fmt in (CF_UNICODETEXT, CF_TEXT):
        hwnd = _get_clipboard_data(fmt)
        if not hwnd:
            continue
        ptr = _lock_global(hwnd)
        if not ptr:
            continue
        try:
            if fmt == CF_UNICODETEXT:
                text = ctypes.wstring_at(ptr)
            else:
                text = ctypes.c_char_p(ptr).value.decode("mbcs", errors="ignore")
        finally:
            _unlock_global(hwnd)
        if text:
            return text
    return ""


def _read_files() -> list[str]:
    """CF_HDROP —— 剪贴板里的文件列表。

    结构：HGLOBAL 里先是 DROPFILES 头（20 字节），
    紧接着是双 null 结尾的、以 null 分隔的文件路径列表（UTF-16）。
    """
    hwnd = _get_clipboard_data(CF_HDROP)
    if not hwnd:
        return []
    ptr = _lock_global(hwnd)
    if not ptr:
        return []
    try:
        # DROPFILES: pFiles(4) + pt(8) + fNC(4) + fWide(1) + 填充(3)
        header = ctypes.string_at(ptr, 20)
        files_offset = struct.unpack_from("<I", header, 0)[0]
        f_wide = bool(header[16])
        start = ptr + files_offset
        size = _global_size(hwnd)
        raw = ctypes.string_at(start, max(0, size - files_offset))
    finally:
        _unlock_global(hwnd)

    paths: list[str] = []
    if f_wide:
        text = raw.decode("utf-16-le", errors="ignore")
    else:
        text = raw.decode("mbcs", errors="ignore")
    for chunk in text.split("\x00"):
        chunk = chunk.strip()
        if not chunk:
            continue
        paths.append(chunk)
        if len(paths) >= MAX_FILES:
            break
    # 只留真实存在的路径 —— 剪贴板里可能有早就删掉的文件
    return [p for p in paths if Path(p).exists()][:MAX_FILES]


def _read_image() -> bytes | None:
    """CF_DIB / CF_BITMAP —— 转成 PNG 字节。

    CF_DIB 里是 BITMAPINFOHEADER + 像素数据（没有文件头），
    拼成 BMP 文件头就是标准 BMP，Pillow 能读。
    CF_BITMAP 是 GDI 句柄，要先 GetObject 转成位图再拿 DIB。
    """
    try:
        from PIL import Image
    except ImportError:
        return None

    import io

    # 优先 CF_DIB —— 它直接就是一段 DIB 数据，不用过 GDI
    hwnd = _get_clipboard_data(CF_DIB)
    if hwnd:
        ptr = _lock_global(hwnd)
        if ptr:
            try:
                size = _global_size(hwnd)
                dib = ctypes.string_at(ptr, size)
            finally:
                _unlock_global(hwnd)
            png = _dib_to_png(dib)
            if png:
                return png

    # 退到 CF_BITMAP（GDI 句柄）
    hwnd = _get_clipboard_data(CF_BITMAP)
    if not hwnd:
        return None
    u = _user32()
    gdi = ctypes.WinDLL("gdi32", use_last_error=True)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", ctypes.c_long),
            ("biHeight", ctypes.c_long),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", ctypes.c_long),
            ("biYPelsPerMeter", ctypes.c_long),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class BITMAP(ctypes.Structure):
        _fields_ = [
            ("bmType", wintypes.DWORD),
            ("bmWidth", ctypes.c_long),
            ("bmHeight", ctypes.c_long),
            ("bmWidthBytes", wintypes.WORD),
            ("bmPlanes", wintypes.WORD),
            ("bmBitsPixel", wintypes.WORD),
            ("bmBits", ctypes.c_void_p),
        ]

    gdi.GetObject.restype = ctypes.c_void_p
    gdi.GetObject.argtypes = [wintypes.HGLOBAL, ctypes.c_int, ctypes.c_void_p]
    bmp = BITMAP()
    got = gdi.GetObject(hwnd, ctypes.sizeof(BITMAP), ctypes.byref(bmp))
    if not got:
        return None
    try:
        ih = BITMAPINFOHEADER()
        ih.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        gdi.GetObject.restype = ctypes.c_void_p
        gdi.GetObject.argtypes = [wintypes.HGLOBAL, ctypes.c_int, ctypes.c_void_p]
        gdi.GetObject(hwnd, ctypes.sizeof(BITMAPINFOHEADER), ctypes.byref(ih))
        header = ctypes.create_string_buffer(ctypes.sizeof(BITMAPINFOHEADER) + 1024)
        ctypes.memmove(header, ctypes.byref(ih), ctypes.sizeof(BITMAPINFOHEADER))
        gdi.GetDIBits.restype = ctypes.c_int
        gdi.GetDIBits.argtypes = [
            wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
            ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT,
        ]
        stride = ((bmp.bmWidthBytes + 3) // 4) * 4
        pixels = ctypes.create_string_buffer(stride * abs(bmp.bmHeight))
        hdc = u.GetDC(None)
        try:
            scan = max(1, abs(bmp.bmHeight))
            ok = gdi.GetDIBits(hdc, hwnd, 0, scan, pixels, header, 0)
        finally:
            u.ReleaseDC(None, hdc)
        if not ok:
            return None
        # 拼成 BMP 文件
        file_header = struct.pack("<2sIHHI", b"BM", 14 + len(header) + len(pixels), 0, 0, 14 + len(header))
        blob = file_header + bytes(header) + pixels
        with Image.open(io.BytesIO(blob)) as im:
            buf = io.BytesIO()
            im.convert("RGBA").save(buf, format="PNG")
            return buf.getvalue()
    except Exception:
        return None


def _dib_to_png(dib: bytes) -> bytes | None:
    """CF_DIB 的裸数据 → PNG。

    CF_DIB 没有 BMP 文件头（那 14 字节），
    补上就是标准 BMP，Pillow 直接能读。
    """
    import io

    try:
        from PIL import Image
    except ImportError:
        return None
    if len(dib) < 40:
        return None
    try:
        file_header = struct.pack("<2sIHHI", b"BM", 14 + len(dib), 0, 0, 14)
        with Image.open(io.BytesIO(file_header + dib)) as im:
            buf = io.BytesIO()
            im.convert("RGBA").save(buf, format="PNG")
            return buf.getvalue()
    except Exception:
        return None


# ── 对外接口 ──────────────────────────────────────────────────

def read_clipboard() -> dict[str, Any]:
    """读一次剪贴板，返回 {kind, ...}。

    kind 取值：text / image / files / empty。
    文件优先于文本 —— 用户在资源管理器里复制的文件，
    剪贴板里往往**同时**有路径文本和 CF_HDROP，
    这时要的是文件，不是那串路径文本。
    """
    if sys.platform != "win32":
        return {"kind": "empty", "detail": "读剪贴板只在 Windows 上支持"}

    if not _open_clipboard():
        return {"kind": "empty", "detail": "打不开剪贴板（可能正被别的程序占着）"}
    try:
        u = _user32()
        u.IsClipboardFormatAvailable.restype = wintypes.BOOL
        u.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]

        # 文件优先
        if u.IsClipboardFormatAvailable(CF_HDROP):
            files = _read_files()
            if files:
                return {"kind": "files", "files": files}

        if u.IsClipboardFormatAvailable(CF_DIB) or u.IsClipboardFormatAvailable(CF_BITMAP):
            png = _read_image()
            if png:
                if len(png) > MAX_IMAGE_BYTES:
                    return {"kind": "empty", "detail": "剪贴板里的图片太大"}
                return {
                    "kind": "image",
                    "data_url": "data:image/png;base64," + base64.b64encode(png).decode("ascii"),
                    "bytes": len(png),
                }

        if u.IsClipboardFormatAvailable(CF_UNICODETEXT) or u.IsClipboardFormatAvailable(CF_TEXT):
            text = _read_text()
            if text:
                return {"kind": "text", "text": text}

        return {"kind": "empty", "detail": "剪贴板里没有可用的内容"}
    finally:
        _close_clipboard()


def clipboard_available() -> bool:
    """剪贴板里现在有没有 Oryx 能处理的内容（文本 / 图片 / 文件）。

    只问「格式在不在」，不把内容读出来 —— 图片可能有 20MB，
    光为了判断按钮亮不亮就把整块位图解码一遍太亏。
    剪贴板是全局独占的，打不开就当作不可用（别的程序占着，稍后再试）。

    这是 UI 判断「粘贴按钮该不该亮」和剪贴板回归测试的公共入口。
    """
    if sys.platform != "win32":
        return False
    if not _open_clipboard():
        return False
    try:
        u = _user32()
        u.IsClipboardFormatAvailable.restype = wintypes.BOOL
        u.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
        return any(
            u.IsClipboardFormatAvailable(fmt)
            for fmt in (CF_HDROP, CF_DIB, CF_BITMAP, CF_UNICODETEXT, CF_TEXT)
        )
    finally:
        _close_clipboard()


