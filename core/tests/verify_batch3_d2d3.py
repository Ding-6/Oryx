"""第三批 · D2/D3：内核自愈 + 子进程回收的验证。

D2 用真子进程做：拉起 AgentLink → 杀掉内核进程 → 确认看门狗把它拉回来。
D3 验证终端子进程被整组回收，不留孤儿。

运行：python -m tests.verify_batch3_d2d3
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILED.append(name)


def fresh_oryx(home: str):
    os.environ["ORYX_HOME"] = home
    for mod in [m for m in list(sys.modules) if m.startswith("oryx")]:
        del sys.modules[mod]


# ---------------------------------------------------------------- D2 内核自愈
def test_kernel_restart() -> None:
    tmp = tempfile.mkdtemp(prefix="oryx_b3d2_")
    fresh_oryx(tmp)
    from oryx.agentlink import AgentLink

    health: list[dict] = []
    link = AgentLink(on_event=lambda ev: health.append(ev))
    try:
        link.start(timeout=40.0)
        check("内核已启动", link.alive)
        first_pid = link._proc.pid
        print(f"    内核 pid={first_pid}")

        tools = link.call("catalog.tools")
        check("崩溃前可正常调用", isinstance(tools, dict) and tools.get("total", 0) > 0)

        # 模拟崩溃：直接杀内核进程
        link._proc.kill()
        link._proc.wait(timeout=5)
        check("杀掉后 alive 为 False", not link.alive)

        # 等看门狗（心跳 5s）
        deadline = time.time() + 40
        recovered = False
        while time.time() < deadline:
            if any(e.get("phase") == "recovered" for e in health):
                recovered = True
                break
            time.sleep(0.5)

        check("看门狗发出 recovered", recovered, str(health))
        check("重启后 alive 恢复", link.alive)
        if link._proc:
            check("确实是新进程", link._proc.pid != first_pid,
                  f"old={first_pid} new={link._proc.pid}")

        phases = [e.get("phase") for e in health if e.get("type") == "agent.kernel.health"]
        check("经历了 restarting → recovered",
              "restarting" in phases and "recovered" in phases, str(phases))

        # 重启后仍可服务
        tools2 = link.call("catalog.tools")
        check("重启后可正常调用", isinstance(tools2, dict) and tools2.get("total", 0) > 0)
        check("重启后工具数一致", tools2.get("total") == tools.get("total"),
              f"{tools.get('total')} vs {tools2.get('total')}")
    finally:
        link.stop()


def test_no_restart_after_stop() -> None:
    """stop() 之后看门狗不能再拉起内核。"""
    tmp = tempfile.mkdtemp(prefix="oryx_b3d2b_")
    fresh_oryx(tmp)
    from oryx.agentlink import AgentLink

    health: list[dict] = []
    link = AgentLink(on_event=lambda ev: health.append(ev))
    link.start(timeout=40.0)
    pid = link._proc.pid
    link.stop()
    check("stop 后 alive 为 False", not link.alive)

    time.sleep(8)  # 至少一个心跳周期
    check("stop 后没有被重启", health == [], str(health))
    check("stop 后进程确实没了",
          subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                         capture_output=True).stdout.decode(errors="replace").count(str(pid)) <= 1)


def test_call_after_crash_fails_fast() -> None:
    """崩溃到看门狗接手之间，调用应立刻报错而不是挂住。"""
    tmp = tempfile.mkdtemp(prefix="oryx_b3d2c_")
    fresh_oryx(tmp)
    from oryx.agentlink import AgentLink
    from oryx.ipc import IpcError

    link = AgentLink(on_event=lambda ev: None)
    try:
        link.start(timeout=40.0)
        link.remember_run(goal="冒烟测试")
        check("已记下 run 上下文", link._last_run is not None)
        link._proc.kill()
        link._proc.wait(timeout=5)
        # 清掉客户端，模拟"还没到看门狗接手"
        with link._lock:
            if link._client:
                link._client.close()
            link._client = None
        try:
            link.call("catalog.tools", timeout=2.0)
            check("崩溃后调用应报错", False, "居然成功了")
        except IpcError:
            check("崩溃后调用立刻报错", True)
    finally:
        link.stop()


# ---------------------------------------------------------------- D3 子进程回收
def _count_children(pid: int) -> int:
    """数一下 pid 名下还有几个进程（含孙进程）。"""
    try:
        out = subprocess.run(
            ["wmic", "process", "where", f"ParentProcessId={pid}", "get", "ProcessId"],
            capture_output=True, timeout=20,
        ).stdout.decode(errors="replace")
        return len([l for l in out.splitlines() if l.strip() and "ProcessId" not in l])
    except Exception:
        return -1


def test_terminal_reap() -> None:
    """终端命令的子进程要能被整组收掉。"""
    fresh_oryx(tempfile.mkdtemp(prefix="oryx_b3d3_"))
    from oryx.terminal import TerminalService

    svc = TerminalService()
    # 起一个会派生孙进程的长命令：python 拉起另一个 python
    session = svc.run(
        f'"{sys.executable}" -c "import subprocess,sys,time; '
        f'subprocess.Popen([sys.executable,\'-c\',\'import time; time.sleep(120)\']); '
        f'time.sleep(120)"',
        cwd=tempfile.gettempdir(),
        timeout=120,
    )
    sid = session["id"]
    time.sleep(3)  # 等它真的起来并派生了孙进程

    running = svc.get(sid)
    check("会话在跑", running is not None and running["running"] is True, str(running)[:120])

    reaped = svc.reap_all()
    check("reap_all 收到至少一个", reaped >= 1, f"reaped={reaped}")

    time.sleep(1.5)
    after = svc.get(sid)
    check("会话已结束", after is not None and after["running"] is False, str(after)[:120])

    # 单个 interrupt 也得能收
    s2 = svc.run(
        f'"{sys.executable}" -c "import time; time.sleep(60)"',
        cwd=tempfile.gettempdir(),
        timeout=60,
    )
    time.sleep(2)
    ok = svc.interrupt(s2["id"])
    check("interrupt 成功", ok)
    time.sleep(1)
    check("interrupt 后已结束", svc.get(s2["id"])["running"] is False)


def test_process_group_isolated() -> None:
    """确认命令跑在独立进程组里（Unix 可直接看 pgid）。"""
    if sys.platform == "win32":
        print("    SKIP  Windows 无 pgid 概念，跳过")
        return
    fresh_oryx(tempfile.mkdtemp(prefix="oryx_b3d3b_"))
    import os as _os

    from oryx.terminal import TerminalService

    svc = TerminalService()
    svc.run('sleep 30', cwd=tempfile.gettempdir(), timeout=30)
    time.sleep(1.5)
    for s in svc.sessions.values():
        if s.proc is not None:
            pgid = _os.getpgid(s.proc.pid)
            check("子进程组独立于当前进程", pgid != _os.getpgid(_os.getpid()),
                  f"pgid={pgid}")
            break
    else:
        check("找到运行中的会话", False, "没有")
    svc.reap_all()


def main() -> int:
    tests = [
        ("D2-1 内核崩溃自动重启", test_kernel_restart),
        ("D2-2 stop 后不再重启", test_no_restart_after_stop),
        ("D2-3 崩溃后调用快速失败", test_call_after_crash_fails_fast),
        ("D3-1 终端子进程整组回收", test_terminal_reap),
        ("D3-2 进程组隔离", test_process_group_isolated),
    ]
    for label, fn in tests:
        print(f"\n[{label}]")
        try:
            fn()
        except Exception as exc:
            import traceback

            traceback.print_exc()
            FAILED.append(label)
    print("\n" + "=" * 60)
    if FAILED:
        print(f"失败 {len(FAILED)} 项：{'、'.join(FAILED)}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())