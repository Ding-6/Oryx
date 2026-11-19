"""父进程监控：父进程没了要自杀，不能留孤儿内核。"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]


def test_win_alive_detects_live_and_dead_pids():
    """Windows 上不能依赖 psutil（可选依赖，缺了就退化成永不自杀）。"""
    from oryx.__main__ import _win_alive

    assert _win_alive(os.getpid()) is True, "自己当然活着"

    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    assert _win_alive(dead.pid) is False, "已退出的进程必须判定为不在"


def test_kernel_dies_with_its_parent():
    """HTTP 层被强杀后，它的 Agent 内核不能继续占着内存。"""
    if os.name != "nt":
        return  # Windows 专属路径

    parent = subprocess.Popen(
        [sys.executable, "-c",
         "import subprocess,sys;"
         "p=subprocess.Popen([sys.executable,'-m','oryx.agentd','--port','0',"
         "'--parent-pid',str(__import__('os').getpid())],"
         "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
         "print(p.pid,flush=True)"],
        cwd=str(CORE),
        stdout=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": str(CORE)},
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    line = parent.stdout.readline().decode().strip()
    kernel_pid = int(line.split()[-1])
    assert parent.wait(timeout=30) == 0

    # 内核每 1.5 秒查一次父进程，给它几个周期
    deadline = time.monotonic() + 20
    gone = False
    while time.monotonic() < deadline:
        from oryx.__main__ import _win_alive

        if not _win_alive(kernel_pid):
            gone = True
            break
        time.sleep(0.5)

    assert gone, "父进程退出后，Agent 内核必须跟着退出"


def test_watch_parent_returns_immediately():
    """注册监控不该阻塞调用方。"""
    from oryx.__main__ import _watch_parent

    start = time.monotonic()
    _watch_parent(os.getpid())  # 传自己的 pid，不会触发自杀
    assert time.monotonic() - start < 1.0