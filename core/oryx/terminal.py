"""应用内嵌执行：流式终端。

和 `shell` 工具的区别：
- shell 工具把命令跑完再一次性返回；
- 这里边跑边推事件，界面能实时看到输出，像真的终端。

命令在后台线程跑，输出按行推到事件总线。执行中的命令会登记到表里，
界面可以随时中断。

子进程回收（第三批）：命令跑在**独立的进程组**里，内核退出时整组一起收。
不对齐的话，`shell=True` 起的是 `cmd /c ...`，它再拉起的孙进程挂在
内核 PID 下面 —— 内核一死，这些进程就变成孤儿继续跑（装包、拉服务、
开着的编辑器），用户界面上也看不见，只能去任务管理器里手杀。
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

MAX_LINE = 4000

# 控制台输出的编码候选，按优先级试。
#
# **为什么不能写死 utf-8**
# ----------------------
# Windows 的命令行工具（cmd / pip / chcp / npm …）输出用的是**系统代码页**，
# 中文机器上是 GBK(cp936)。按 UTF-8 去解就是满屏「������」。
#
# 为什么 UTF-8 排第一
# ------------------
# 现在越来越多的工具默认输出 UTF-8（Python 3.6+、Git、Node 都是），
# 而 GBK 能「碰巧」解出一堆合法但无意义的字符 —— 那比直接报错还难发现。
#
# 所以顺序是：**先严格 UTF-8，解不出来再严格 GBK，最后才替换字符兜底**。
# 严格解码的好处是「解不出��」是明确的信号，不会悄悄给出乱码。
_CONSOLE_ENCODINGS = ("utf-8", "gbk", "cp936", "latin-1")


def _decode_console(raw: bytes) -> str:
    """把控制台输出的一行字节解成文本。

    纯 ASCII（大部分英文输出）在第一个候选就过了。
    """
    for enc in _CONSOLE_ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")
DEFAULT_TIMEOUT = 300

if sys.platform == "win32":
    CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
else:
    CREATE_NEW_PROCESS_GROUP = 0


@dataclass
class Session:
    id: str
    command: str
    cwd: str
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    exit_code: int | None = None
    proc: Any = None
    lines: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "command": self.command,
            "cwd": self.cwd,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "exit_code": self.exit_code,
            "running": self.proc is not None and self.proc.poll() is None,
            "lines": self.lines[-200:],
        }


class TerminalService:
    def __init__(self, on_event: Callable[[dict[str, Any]], None] | None = None):
        self.on_event = on_event
        self.sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def _emit(self, kind: str, **fields: Any) -> None:
        if self.on_event:
            try:
                self.on_event({"t": kind, **fields})
            except Exception:
                pass

    def run(self, command: str, cwd: str = "", timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
        session = Session(
            id=uuid.uuid4().hex[:8], command=command, cwd=cwd or str(_default_cwd())
        )
        with self._lock:
            self.sessions[session.id] = session

        self._emit("term.started", id=session.id, command=command, cwd=session.cwd)

        def worker() -> None:
            try:
                proc = subprocess.Popen(
                    command,
                    shell=True,
                    cwd=session.cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    # **按字节读，自己解码** ——
                    # 写死 encoding="utf-8" 会把 Windows 的输出解成乱码：
                    # cmd / pip / chcp 等工具输出的是**系统代码页**（中文 Windows 是 GBK），
                    # 按 UTF-8 解出来就是「������」这种鬼东西。
                    bufsize=0,
                    # 独立进程组：内核退出时能整组收掉，孙进程不会变孤儿
                    creationflags=CREATE_NEW_PROCESS_GROUP
                    | getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                session.proc = proc
                assert proc.stdout is not None
                for raw in iter(proc.stdout.readline, b""):
                    text = _decode_console(raw).rstrip("\r\n")
                    session.lines.append(text)
                    self._emit("term.output", id=session.id, line=text[:MAX_LINE])
                session.exit_code = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                session.exit_code = -1
                self._emit("term.output", id=session.id, line="[超时]")
                _kill_tree(session)
            except Exception as exc:
                session.exit_code = -1
                self._emit("term.output", id=session.id, line=f"[错误] {exc}")
            finally:
                session.proc = None
                session.finished_at = time.time()
                self._emit(
                    "term.finished",
                    id=session.id,
                    exit_code=session.exit_code,
                    lines=len(session.lines),
                )

        threading.Thread(target=worker, name=f"term-{session.id}", daemon=True).start()
        return session.to_dict()

    def interrupt(self, session_id: str) -> bool:
        with self._lock:
            session = self.sessions.get(session_id)
        if not session or session.proc is None:
            return False
        return _kill_tree(session)

    def reap_all(self) -> int:
        """收掉所有还在跑的会话。

        内核退出前调一次。不调的话 shell=True 拉起的孙进程会脱离内核
        继续运行，变成看不见的孤儿进程。
        """
        with self._lock:
            alive = [s for s in self.sessions.values() if s.proc is not None]
        reaped = 0
        for session in alive:
            if _kill_tree(session):
                reaped += 1
        return reaped

    def shutdown(self) -> None:
        """内核关停时调用：回收子进程。"""
        count = self.reap_all()
        if count:
            print(f"[oryx] 终端回收：结束 {count} 个残留命令", file=sys.stderr)

    def get(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            session = self.sessions.get(session_id)
        return session.to_dict() if session else None

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [s.to_dict() for s in self.sessions.values()]


def _default_cwd() -> str:
    from pathlib import Path

    return str(Path.cwd())


def _kill_tree(session: Session) -> bool:
    """结束一个会话及其所有子孙进程。

    `proc.kill()` 只杀 `cmd /c ...` 那一层，它拉起的孙进程（node、python、
    装包器）会活下来。必须按进程组整组收。

    Windows 用 `taskkill /T /F`（按父子树收）；Unix 用负 pgid 发信号
    （killpg）。
    """
    proc = session.proc
    if proc is None:
        return False
    try:
        if proc.poll() is not None:
            return False
    except Exception:
        return False

    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            pass
        # 兜底：taskkill 不可用时至少杀掉自己这一层
        try:
            proc.kill()
        except Exception:
            pass
        return True

    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
        # 给 3 秒优雅退出，之后再硬杀
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(pgid, signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            return False
    return True


def notify(
    title: str, message: str, method: str = "toast", webhook: str = ""
) -> dict[str, Any]:
    """通知。toast = Windows 系统通知；webhook = POST 到自定义地址。"""
    if method == "webhook":
        if not webhook:
            return {"ok": False, "content": "webhook 方式需要提供地址"}
        try:
            import httpx

            response = httpx.post(webhook, json={"title": title, "message": message}, timeout=15)
            return {"ok": True, "content": f"已发送，状态码 {response.status_code}"}
        except Exception as exc:
            return {"ok": False, "content": f"发送失败：{exc}"}

    # Windows toast：借用 PowerShell 的 BurntToast 不可靠，这里走系统通知中心
    if sys.platform == "win32":
        script = (
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications,"
            " ContentType=WindowsRuntime] | Out-Null;"
            "$t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(0);"
            f"$t.GetElementsByTagName('text').Item(0).AppendChild($t.CreateTextNode('{title}')) | Out-Null;"
            f"$t.GetElementsByTagName('text').Item(1).AppendChild($t.CreateTextNode('{message}')) | Out-Null;"
            "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier"
            "('Oryx').Show([Windows.UI.Notifications.ToastNotification]::new($t))"
        )
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                capture_output=True,
                text=True,
                timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode == 0:
                return {"ok": True, "content": "已发送系统通知"}
            return {"ok": False, "content": (result.stderr or "未知错误").strip()[:200]}
        except Exception as exc:
            return {"ok": False, "content": f"发送失败：{exc}"}

    return {"ok": False, "content": "当前系统不支持 toast 通知，请用 webhook 方式"}
