"""命令沙箱：在裸 shell 之上加三层约束（对齐 WorkBuddy sandbox-runtime 的思路）。

约束：
1. **环境变量剔除**——子进程看不到 API_KEY / TOKEN / SECRET / PASSWORD 这类变量，
   防止一句恶意命令把本机密钥外发；
2. **工作目录监禁**——命令只能在工作区（或允许的根目录）里跑，
   相对的 ./ 操作不会落到系统目录；
3. **资源上限**——超时 + 输出截断，失控命令不会把进程拖死。

注意：沙箱是纵深防御，不替代策略引擎——危险命令黑名单仍然生效，
L2 审批流也不变。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from .redact import redact

SANDBOX_TIMEOUT = 60
MAX_STDOUT = 20000
MAX_STDERR = 5000

# 命中这些片段的环境变量不会传进子进程（大小写不敏感）
_SENSITIVE_ENV = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|PRIVATE", re.IGNORECASE)

# 即使名字命中敏感片段也必须保留的白名单（Windows 系统变量）
_ENV_WHITELIST = {
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP",
    "USERPROFILE", "USERNAME", "USERDOMAIN", "HOMEDRIVE", "HOMEPATH",
    "COMSPEC", "OS", "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS",
    "APPDATA", "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES (X86)", "PROGRAMDATA",
}


def scrub_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """返回剔除敏感变量后的环境。白名单里的系统变量始终保留。"""
    source = dict(base if base is not None else os.environ)
    out: dict[str, str] = {}
    for name, value in source.items():
        upper = name.upper()
        if upper in _ENV_WHITELIST:
            out[name] = value
        elif _SENSITIVE_ENV.search(name):
            continue  # 丢弃密钥类变量
        else:
            out[name] = value
    return out


def resolve_jail(root: str | None, fallback: str) -> Path:
    """把请求的工作目录解析成绝对路径；不给就用 fallback（通常是工作区）。"""
    candidate = root or fallback
    return Path(os.path.expandvars(os.path.expanduser(candidate))).resolve()


def run_sandboxed(
    command: str,
    cwd: str | Path,
    timeout: int = SANDBOX_TIMEOUT,
) -> dict[str, Any]:
    """在沙箱约束下执行命令，返回与 tools.shell 相同的结构。"""
    jail = Path(cwd)
    if not jail.exists() or not jail.is_dir():
        return {"ok": False, "content": f"沙箱工作目录不存在：{jail}"}
    env = scrub_env()
    try:
        if sys.platform == "win32":
            proc = subprocess.run(
                command,
                shell=True,
                cwd=str(jail),
                env=env,
                capture_output=True,
                timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            encoding = "gb18030"
        else:
            proc = subprocess.run(
                command, shell=True, cwd=str(jail), env=env,
                capture_output=True, timeout=timeout,
            )
            encoding = "utf-8"
        stdout = proc.stdout.decode(encoding, errors="replace")[:MAX_STDOUT]
        stderr = proc.stderr.decode(encoding, errors="replace")[:MAX_STDERR]
        return {
            "ok": True,
            "content": {
                # 命令输出里可能带 `cat .env` 的内容，出门前打码。
                "stdout": redact(stdout),
                "stderr": redact(stderr),
                "exit_code": proc.returncode,
                "sandbox": {"cwd": str(jail), "env_scrubbed": True},
            },
            # 命令行本身也是泄密点（`echo sk-xxx`），一并打码。
            "command": redact(command),
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "content": f"命令超时（>{timeout}s）：{redact(command)}"}
    except Exception as exc:
        return {"ok": False, "content": f"执行失败：{redact(str(exc))}"}
