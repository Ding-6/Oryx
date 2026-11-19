"""内核预热池：把"冷启动的固定开销"挪到没人等的时候付。

问题：桌面壳点开 → 内核冷启动 → 才能对话。实测冷启动 0.63s，
其中 0.4s 花在 Python 导入链上（yaml / asyncio / 几十个小模块），
这部分开销**每次都一样**，跟用户等不等有关。

做法（对齐 WorkBuddy 的 prewarm 池）：
    1. 启动入口并行拉起一个"待命内核"——它把导入链、配置、数据库、
       工具注册表全部准备好，只是不接活（定时任务也不启动）；
    2. 真正要对话时，"激活"这个待命内核：连上 IPC、启动定时任务、
       广播工作区变更，省掉整段冷启动；
    3. 激活后这个内核就是正式内核，后续自愈重启也走它。

关键设计取舍：
- **不做成"池"**：Oryx 是桌面单用户应用，同时只会有一个内核在跑。
  池化（多个待命实例）在这里是浪费内存，换不来收益。
- **激活只允许一次**：待命内核被激活后就转正，不能再被第二个会话抢走。
- **兜底**：预热失败（端口冲突、模块缺失）不阻塞启动 —— 直接冷启。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

# 预热进程的等待上限：超过就放弃预热，走冷启动
WARMUP_TIMEOUT = 30.0
# 领取时的等待上限：预热可能还没完成，等一会儿再决定冷启
CLAIM_TIMEOUT = 15.0
# 待命内核的空闲回收时间：这么久没人用就自己退出，别白占内存
IDLE_TIMEOUT = 300.0


def _core_dir() -> Path:
    return Path(__file__).resolve().parent.parent


class PrewarmHandle:
    """一个待命内核的句柄。"""

    def __init__(self, proc: subprocess.Popen, port_file: Path, port: int):
        self.proc = proc
        self.port_file = port_file
        self.port = port
        self.activated = False
        self.created_at = time.time()
        self._lock = threading.Lock()

    def activate(self) -> bool:
        """把这个待命内核转正。只能成功一次。"""
        with self._lock:
            if self.activated:
                return False
            self.activated = True
            return True

    def release_port_file(self) -> None:
        """删掉端口临时文件。

        端口号已经在内存里（self.port），文件没用了 —— 但删不掉就会一直留着。
        这个泄漏很隐蔽：每次启动 Oryx 就在 %TEMP% 里多一个 .port 文件，
        攒久了几百个（实测 150+ 个躺在 C 盘临时目录里）。

        删不掉不算错 —— 内核进程退出时还会再试一次。
        """
        try:
            self.port_file.unlink()
        except OSError:
            pass

    def alive(self) -> bool:
        return self.proc.poll() is None

    def cleanup(self) -> None:
        """结束待命进程并删掉临时文件。"""
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
        except Exception:
            pass
        try:
            self.port_file.unlink()
        except OSError:
            pass


class PrewarmPool:
    """预热池。当前只有一个待命实例（桌面应用单用户）。"""

    def __init__(self, python: str | None = None):
        self.python = python or sys.executable or "python"
        self._handle: PrewarmHandle | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        # 预热尝试是否已经结束。初始为真 —— 没启动过预热时不该让领取方干等。
        self._ready = threading.Event()
        self._ready.set()

    # ---------------------------------------------------------------- 预热

    def start_background(self, timeout: float = WARMUP_TIMEOUT) -> threading.Thread:
        """在后台线程里预热，调用方立刻返回。

        启动入口用它：预热要跟 fastapi 导入并行，两者抢的是同一段 CPU，
        谁先谁后无所谓，**但总耗时是并行的**。
        """
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self._thread
            self._ready.clear()
            thread = threading.Thread(
                target=self.warm, args=(timeout,), name="oryx-prewarm-warm", daemon=True
            )
            self._thread = thread
        thread.start()
        return thread

    def warm(self, timeout: float = WARMUP_TIMEOUT) -> PrewarmHandle | None:
        """拉起一个待命内核。失败返回 None（调用方走冷启动）。"""
        try:
            with self._lock:
                if self._handle is not None and self._handle.alive():
                    return self._handle
            handle = self._spawn(timeout)
            with self._lock:
                self._handle = handle
            if handle is not None:
                self._start_idle_watch(handle)
            return handle
        finally:
            self._ready.set()

    def _spawn(self, timeout: float) -> PrewarmHandle | None:
        fd, raw = tempfile.mkstemp(prefix="oryx-prewarm-", suffix=".port")
        os.close(fd)
        port_file = Path(raw)
        core = _core_dir()
        try:
            proc = subprocess.Popen(
                [self.python, "-m", "oryx.agentd", "--port", "0",
                 "--port-file", str(port_file), "--prewarm",
                 "--parent-pid", str(os.getpid())],
                cwd=str(core),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            try:
                port_file.unlink()
            except OSError:
                pass
            return None

        port = self._wait_port(proc, port_file, timeout)
        if port is None:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait(timeout=5)
            except Exception:
                pass
            try:
                port_file.unlink()
            except OSError:
                pass
            return None
        return PrewarmHandle(proc, port_file, port)

    @staticmethod
    def _wait_port(proc: subprocess.Popen, port_file: Path, timeout: float) -> int | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return None
            try:
                text = port_file.read_text("utf-8").strip()
                if text:
                    return int(text)
            except (OSError, ValueError):
                pass
            time.sleep(0.02)
        return None

    # ---------------------------------------------------------------- 领取

    def claim(self, timeout: float = CLAIM_TIMEOUT) -> PrewarmHandle | None:
        """领取待命内核。领不到（或已被别人领走）返回 None。

        会等预热完成——预热是在后台线程里跑的，调用方多半比它先到。
        等太久说明预热这条路走不通，直接放弃（免得再养一个白费的进程）。
        """
        if not self._ready.wait(timeout):
            self.shutdown()
            return None
        with self._lock:
            handle = self._handle
            if handle is None or not handle.alive():
                self._handle = None
                return None
            if not handle.activate():
                self._handle = None
                return None
            self._handle = None
            return handle

    def peek(self) -> PrewarmHandle | None:
        with self._lock:
            return self._handle

    @property
    def warming(self) -> bool:
        """预热是否还在进行中。"""
        return not self._ready.is_set()

    # ---------------------------------------------------------------- 清理

    def _start_idle_watch(self, handle: PrewarmHandle) -> None:
        """待命太久没人要就自己退出，别白占内存。"""

        def _watch() -> None:
            while True:
                time.sleep(10)
                if handle.activated or not handle.alive():
                    return
                if time.time() - handle.created_at > IDLE_TIMEOUT:
                    handle.cleanup()
                    with self._lock:
                        if self._handle is handle:
                            self._handle = None
                    return

        threading.Thread(target=_watch, name="oryx-prewarm-idle", daemon=True).start()

    def shutdown(self) -> None:
        """把还待命的内核收掉。

        池子本身还能再 warm —— 上层 shutdown 事件与测试都会反复调它，
        一旦永久封死，第二次预热就没了。已激活的实例不在这里管：
        它已经归 AgentLink 所有，由 AgentLink 负责关。
        """
        with self._lock:
            handle = self._handle
            self._handle = None
        if handle is not None:
            handle.cleanup()


# 进程内单例
_pool: PrewarmPool | None = None


def get_pool() -> PrewarmPool:
    global _pool
    if _pool is None:
        _pool = PrewarmPool()
    return _pool
