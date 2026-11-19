"""读系统剪贴板：文本、文件、图片。

为什么要有这个：浏览器**读不到剪贴板里的文件**。
剪贴板里的文件是 Win32 的 CF_HDROP 格式（DROPFILES 结构 + 路径列表），
Web 页面没权限碰。所以「粘贴文件」只能走后端调 Win32。

测试自己往剪贴板写内容再读回来 —— 不依赖用户当时剪贴板里是什么。
"""
from __future__ import annotations

import ctypes
import struct
import sys
from ctypes import wintypes

import pytest

from oryx import clipboard

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="读剪贴板只在 Windows 上做")


def _put_text(text: str) -> None:
    """往剪贴板写一段文本。"""
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.OpenClipboard.restype = wintypes.BOOL
    u.EmptyClipboard.restype = wintypes.BOOL
    u.SetClipboardData.restype = wintypes.HANDLE
    u.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.GlobalAlloc.restype = wintypes.HGLOBAL
    k.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalLock.argtypes = [wintypes.HGLOBAL]
    k.GlobalUnlock.restype = wintypes.BOOL
    k.GlobalUnlock.argtypes = [wintypes.HGLOBAL]

    buf = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(buf)
    h = k.GlobalAlloc(0x0002, size)
    ptr = k.GlobalLock(h)
    ctypes.memmove(ptr, buf, size)
    # **必须先解锁再交给剪贴板** ——
    # 不解锁的话这个句柄一直是锁着的，下一次 EmptyClipboard 会失败，
    # 剪贴板里还是上一次的旧内容（写第二遍时读出来还是第一遍的）。
    k.GlobalUnlock(h)
    # 用模块里的 open —— 它带重试，剪贴板被别的程序占着时也能写进去
    assert clipboard._open_clipboard()
    u.EmptyClipboard()
    u.SetClipboardData(clipboard.CF_UNICODETEXT, h)
    u.CloseClipboard()


def _put_files(paths: list[str]) -> None:
    """往剪贴板写文件列表（CF_HDROP）。"""
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.OpenClipboard.restype = wintypes.BOOL
    u.EmptyClipboard.restype = wintypes.BOOL
    u.SetClipboardData.restype = wintypes.HANDLE
    u.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.GlobalAlloc.restype = wintypes.HGLOBAL
    k.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalLock.argtypes = [wintypes.HGLOBAL]
    k.GlobalUnlock.restype = wintypes.BOOL
    k.GlobalUnlock.argtypes = [wintypes.HGLOBAL]

    # DROPFILES: pFiles(4) + POINT(8) + fNC(4) + fWide(4) = 20 字节
    header = struct.pack("<I", 20) + struct.pack("<ii", 0, 0) + struct.pack("<I", 0) + struct.pack("<I", 1)
    payload = header + ("\x00".join(paths) + "\x00\x00").encode("utf-16-le")
    h = k.GlobalAlloc(0x0002, len(payload))
    ptr = k.GlobalLock(h)
    ctypes.memmove(ptr, payload, len(payload))
    k.GlobalUnlock(h)
    assert clipboard._open_clipboard()
    u.EmptyClipboard()
    u.SetClipboardData(clipboard.CF_HDROP, h)
    u.CloseClipboard()


def test_read_text():
    _put_text("你好，剪贴板")
    r = clipboard.read_clipboard()
    assert r["kind"] == "text"
    assert r["text"] == "你好，剪贴板"


def test_read_text_english():
    _put_text("hello clipboard")
    r = clipboard.read_clipboard()
    assert r["kind"] == "text"
    assert "hello" in r["text"]


def test_read_files(tmp_path):
    """粘贴文件 —— 浏览器做不到的那件事。"""
    a = tmp_path / "a.txt"
    b = tmp_path / "b.md"
    a.write_text("a", encoding="utf-8")
    b.write_text("b", encoding="utf-8")
    _put_files([str(a), str(b)])
    r = clipboard.read_clipboard()
    assert r["kind"] == "files", r
    assert str(a) in r["files"]
    assert str(b) in r["files"]


def test_read_files_filters_missing(tmp_path):
    """剪贴板里可能有早就删掉的文件 —— 只返回真实存在的。"""
    real = tmp_path / "real.txt"
    real.write_text("x", encoding="utf-8")
    ghost = tmp_path / "ghost.txt"
    _put_files([str(real), str(ghost)])
    r = clipboard.read_clipboard()
    assert r["kind"] == "files"
    assert r["files"] == [str(real)]


def test_files_take_priority_over_text(tmp_path):
    """文件优先于文本。

    用户在资源管理器里复制文件时，剪贴板里**同时**有路径文本和 CF_HDROP。
    这时要的是文件，不是那串路径文本。
    """
    f = tmp_path / "x.txt"
    f.write_text("x", encoding="utf-8")
    _put_files([str(f)])
    r = clipboard.read_clipboard()
    assert r["kind"] == "files"


def test_empty_clipboard():
    """剪贴板清空后要读出 empty。

    坑：剪贴板是**全局独占**资源，别的程序（剪贴板管理器、输入法、
    资源管理器）随时可能正占着它。
    这里必须用模块里的 ``_open_clipboard()``（带退避重试），
    不能直接调 ``OpenClipboard`` 且**不检查返回值** ——
    打不开时它是静默失败的，接着调 ``EmptyClipboard`` 也不会真清空，
    剪贴板里还留着上一个测试写的文件列表，
    读出来就成了 'files' 而不是 'empty'（整轮跑的时候才偶发，单跑不犯）。
    """
    assert clipboard._open_clipboard(), "打不开剪贴板（别的程序正占着）"
    try:
        u = ctypes.WinDLL("user32", use_last_error=True)
        u.EmptyClipboard.restype = wintypes.BOOL
        assert u.EmptyClipboard(), "清空剪贴板失败"
    finally:
        clipboard._close_clipboard()
    r = clipboard.read_clipboard()
    assert r["kind"] == "empty"


def test_available_flag():
    _put_text("x")
    assert clipboard.clipboard_available() is True
