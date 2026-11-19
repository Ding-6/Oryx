"""密钥存储：把 api_key 从 config.json 的明文里挪进操作系统凭据库。

优先使用系统自带的凭据存储，不引第三方依赖：
- Windows：凭据管理器（CredWriteW / CredReadW / CredDeleteW）
- macOS：钥匙串（security add/find/delete-generic-password）
- Linux：secret-tool（GNOME Keyring / KWallet）

三者都不可用时返回 None —— 调用方保持原来的明文行为。
**不做"看起来像加密"的假实现**：没有系统凭据库就是没有，写文件加密只是障眼法。

对齐 WorkBuddy 的凭据保护（Keychain / Keyring / Windows Credential Manager）。
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
from pathlib import Path

SERVICE = "oryx"

# 测试环境下用独立的凭据 target ——
# 测试的假密钥不该污染用户的凭据管理器，用户的真密钥也不该被测试读走。
# 判据用「pytest 模块在 sys.modules 里」：PYTEST_CURRENT_TEST 只在单个
# 测试执行期间有值，模块导入发生在收集阶段，那时还没设。
_IS_TEST = "pytest" in sys.modules or bool(os.environ.get("PYTEST_CURRENT_TEST"))

# 各后端返回值的统一编码：凭据库是二进制容器，统一 base64 存。
# Windows 的 CredWriteW 实际接受纯字节，这里也统一走 base64，
# 保证「存进去什么、读出来什么」在三个平台上是同一套语义。


def backend() -> str | None:
    """返回可用的后端名；都不可用时返回 None。"""
    if sys.platform == "win32":
        try:
            import ctypes  # noqa: F401

            return "windows"
        except Exception:
            return None
    if sys.platform == "darwin":
        if shutil.which("security"):
            return "keychain"
        return None
    if shutil.which("secret-tool"):
        return "secret-tool"
    return None


def available() -> bool:
    return backend() is not None


# ---------------------------------------------------------------- Windows


def _win_write(target: str, blob: bytes) -> bool:
    import ctypes
    import ctypes.wintypes as w

    class CREDENTIAL_ATTRIBUTE(ctypes.Structure):
        _fields_ = [
            ("Keyword", w.LPWSTR),
            ("Flags", w.DWORD),
            ("ValueSize", w.DWORD),
            ("Value", ctypes.POINTER(ctypes.c_byte)),
        ]

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [
            ("Flags", w.DWORD),
            ("Type", w.DWORD),
            ("TargetName", w.LPWSTR),
            ("Comment", w.LPWSTR),
            ("LastWritten", w.FILETIME),
            ("CredentialBlobSize", w.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
            ("Persist", w.DWORD),
            ("AttributeCount", w.DWORD),
            ("Attributes", ctypes.POINTER(CREDENTIAL_ATTRIBUTE)),
            ("TargetAlias", w.LPWSTR),
            ("UserName", w.LPWSTR),
        ]

    adv = ctypes.windll.advapi32
    cred = CREDENTIAL()
    cred.Flags = 0
    cred.Type = 1  # CRED_TYPE_GENERIC
    cred.TargetName = target
    cred.CredentialBlobSize = len(blob)
    buf = ctypes.create_string_buffer(blob, len(blob))
    cred.CredentialBlob = ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))
    cred.Persist = 2  # CRED_PERSIST_LOCAL_MACHINE：跨重启保留
    cred.UserName = SERVICE
    return bool(adv.CredWriteW(ctypes.byref(cred), 0))


def _win_read(target: str) -> bytes | None:
    import ctypes

    adv = ctypes.windll.advapi32
    ptr = ctypes.c_void_p()
    if not adv.CredReadW(target, 1, 0, ctypes.byref(ptr)):
        return None
    try:
        class CREDENTIAL(ctypes.Structure):
            _fields_ = [
                ("Flags", ctypes.c_uint32),
                ("Type", ctypes.c_uint32),
                ("TargetName", ctypes.c_wchar_p),
                ("Comment", ctypes.c_wchar_p),
                ("LastWritten", ctypes.c_byte * 8),
                ("CredentialBlobSize", ctypes.c_uint32),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", ctypes.c_uint32),
                ("AttributeCount", ctypes.c_uint32),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", ctypes.c_wchar_p),
                ("UserName", ctypes.c_wchar_p),
            ]

        cred = ctypes.cast(ptr, ctypes.POINTER(CREDENTIAL)).contents
        if not cred.CredentialBlobSize:
            return b""
        return ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
    finally:
        adv.CredFree(ptr)


def _win_delete(target: str) -> bool:
    import ctypes

    return bool(ctypes.windll.advapi32.CredDeleteW(target, 1, 0))


# ---------------------------------------------------------------- macOS


def _mac_write(target: str, blob: bytes) -> bool:
    proc = subprocess.run(
        [
            "security", "add-generic-password", "-U",
            "-a", SERVICE, "-s", target, "-w", base64.b64encode(blob).decode(),
        ],
        capture_output=True,
    )
    return proc.returncode == 0


def _mac_read(target: str) -> bytes | None:
    proc = subprocess.run(
        ["security", "find-generic-password", "-a", SERVICE, "-s", target, "-w"],
        capture_output=True,
    )
    if proc.returncode != 0:
        return None
    try:
        return base64.b64decode(proc.stdout.decode().strip())
    except Exception:
        return None


def _mac_delete(target: str) -> bool:
    proc = subprocess.run(
        ["security", "delete-generic-password", "-a", SERVICE, "-s", target],
        capture_output=True,
    )
    return proc.returncode == 0


# ---------------------------------------------------------------- Linux


def _secret_tool_write(target: str, blob: bytes) -> bool:
    proc = subprocess.run(
        [
            "secret-tool", "store", "--label=Oryx",
            "service", SERVICE, "credential", target,
        ],
        input=base64.b64encode(blob),
        capture_output=True,
    )
    return proc.returncode == 0


def _secret_tool_read(target: str) -> bytes | None:
    proc = subprocess.run(
        ["secret-tool", "lookup", "service", SERVICE, "credential", target],
        capture_output=True,
    )
    if proc.returncode != 0:
        return None
    try:
        return base64.b64decode(proc.stdout.decode().strip())
    except Exception:
        return None


def _secret_tool_delete(target: str) -> bool:
    proc = subprocess.run(
        ["secret-tool", "clear", "service", SERVICE, "credential", target],
        capture_output=True,
    )
    return proc.returncode == 0


# ---------------------------------------------------------------- 统一入口


def set_secret(name: str, value: str) -> bool:
    """写入一条密钥。返回是否真的存进了系统凭据库。"""
    kind = backend()
    if kind is None:
        return False
    blob = value.encode("utf-8")
    target = f"{SERVICE}:{name}"
    if _IS_TEST:
        target = f"oryx-test:{name}"
    try:
        if kind == "windows":
            return _win_write(target, blob)
        if kind == "keychain":
            return _mac_write(target, blob)
        return _secret_tool_write(target, blob)
    except Exception:
        return False


def get_secret(name: str) -> str:
    """读取一条密钥；不存在或后端不可用时返回空串。"""
    kind = backend()
    if kind is None:
        return ""
    target = f"{SERVICE}:{name}"
    if _IS_TEST:
        target = f"oryx-test:{name}"
    try:
        if kind == "windows":
            blob = _win_read(target)
        elif kind == "keychain":
            blob = _mac_read(target)
        else:
            blob = _secret_tool_read(target)
    except Exception:
        return ""
    if not blob:
        return ""
    try:
        return blob.decode("utf-8")
    except UnicodeDecodeError:
        return ""


def delete_secret(name: str) -> bool:
    """删除一条密钥。"""
    kind = backend()
    if kind is None:
        return False
    target = f"{SERVICE}:{name}"
    if _IS_TEST:
        target = f"oryx-test:{name}"
    try:
        if kind == "windows":
            return _win_delete(target)
        elif kind == "keychain":
            return _mac_delete(target)
        else:
            return _secret_tool_delete(target)
    except Exception:
        return False


def migrate_plaintext(provider: str, plaintext: str) -> bool:
    """把明文密钥搬进凭据库。成功后调用方应清掉配置文件里的明文。"""
    if not plaintext:
        return False
    return set_secret(f"provider:{provider}", plaintext)


def describe() -> dict[str, str]:
    """给前端/诊断看的后端状态。"""
    kind = backend() or "none"
    labels = {
        "windows": "Windows 凭据管理器",
        "keychain": "macOS 钥匙串",
        "secret-tool": "系统密钥环",
        "none": "不可用（密钥仍以明文保存在配置文件中）",
    }
    return {"backend": kind, "label": labels.get(kind, kind)}
