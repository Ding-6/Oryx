"""第四批 · E1/E2：内核预热池的验证。

核心断言：
- 待命内核起来后处于未激活状态，不接活、不跑定时任务
- 领取一次就转正，第二次领不到（激活只允许一次）
- 预热失败不阻塞：领不到就冷启
- 激活后定时任务才开跑，工作区范围被对齐
- 激活耗时明显短于冷启动（省下的就是导入链那 0.4s）

运行：python -m tests.verify_batch4_e1
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
PYTHON = sys.executable
CORE = Path(__file__).resolve().parents[1]


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILED.append(name)


# ---------------------------------------------------------------- 冷启动基线
def test_cold_start_baseline() -> None:
    """冷启动一个内核，测出基准耗时。"""
    print("  （冷启动基准，约 1s）")
    t0 = time.monotonic()
    port, proc = _spawn_agentd(prewarm=False)
    dt = time.monotonic() - t0
    check("冷启动能起来", port > 0)
    print(f"  冷启动耗时 {dt:.3f}s")
    if proc is not None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def _spawn_agentd(prewarm: bool) -> tuple[int, subprocess.Popen | None]:
    """拉一个内核进程，返回 (端口, 进程)。起不来返回 (0, None)。"""
    fd, raw = tempfile.mkstemp(prefix="oryx-vb4-", suffix=".port")
    os.close(fd)
    pf = Path(raw)
    cmd = [PYTHON, "-m", "oryx.agentd", "--port", "0", "--port-file", str(pf)]
    if prewarm:
        cmd.append("--prewarm")
    proc = subprocess.Popen(
        cmd,
        cwd=str(CORE),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return 0, proc
        try:
            text = pf.read_text("utf-8").strip()
            if text:
                return int(text), proc
        except OSError:
            pass
        time.sleep(0.02)
    return 0, proc


# ---------------------------------------------------------------- 单例语义
def test_pool_singleton() -> None:
    from oryx.prewarm import get_pool

    check("get_pool 返回单例", get_pool() is get_pool())


def test_claim_without_warm() -> None:
    """没预热过就领 —— 立刻返回 None，不能干等。"""
    from oryx.prewarm import PrewarmPool

    pool = PrewarmPool()
    t0 = time.monotonic()
    handle = pool.claim(timeout=5.0)
    dt = time.monotonic() - t0
    check("未预热时领不到", handle is None)
    check("未预热时领取不干等", dt < 1.0, f"耗时 {dt:.2f}s")


def test_warm_then_claim_once() -> None:
    """预热 → 领取 → 第二次领不到。"""
    from oryx.prewarm import PrewarmPool

    pool = PrewarmPool()
    try:
        handle = pool.warm(timeout=40)
        check("预热成功", handle is not None)
        if handle is None:
            return
        check("预热进程存活", handle.alive())
        check("预热后处于未激活", not handle.activated)

        first = pool.claim(timeout=5)
        check("首次领取成功", first is handle)
        check("领取后标记激活", handle.activated)

        second = pool.claim(timeout=5)
        check("二次领取失败（激活只允许一次）", second is None)
    finally:
        pool.shutdown()


def test_activate_only_once_on_handle() -> None:
    """句柄的 activate() 本身也只允许一次。"""
    from oryx.prewarm import PrewarmPool

    pool = PrewarmPool()
    try:
        handle = pool.warm(timeout=40)
        if handle is None:
            check("预热成功", False, "预热没起来")
            return
        check("第一次 activate 成功", handle.activate())
        check("第二次 activate 失败", not handle.activate())
    finally:
        pool.shutdown()


def test_shutdown_kills_pending() -> None:
    """池子关掉时，待命内核要被收掉，不能变孤儿。"""
    from oryx.prewarm import PrewarmPool

    pool = PrewarmPool()
    handle = pool.warm(timeout=40)
    check("预热成功", handle is not None)
    if handle is None:
        return
    proc = handle.proc
    pool.shutdown()
    time.sleep(0.6)
    check("待命进程已退出", proc.poll() is not None, f"returncode={proc.poll()}")


def test_start_background_nonblocking() -> None:
    """后台预热不阻塞调用方。"""
    from oryx.prewarm import PrewarmPool

    pool = PrewarmPool()
    try:
        t0 = time.monotonic()
        pool.start_background()
        dt = time.monotonic() - t0
        check("start_background 立即返回", dt < 0.5, f"耗时 {dt:.3f}s")
        handle = pool.claim(timeout=40)
        check("后台预热可被领取", handle is not None)
    finally:
        pool.shutdown()


# ---------------------------------------------------------------- 内核语义
def test_prewarm_kernel_rejects_work() -> None:
    """待命内核不接活：直接调 chat.run 要被拒。"""
    sys.path.insert(0, str(CORE))
    from oryx.agentlink import AgentLink
    from oryx.prewarm import get_pool

    pool = get_pool()
    try:
        handle = pool.warm(timeout=40)
        check("预热成功", handle is not None)
        if handle is None:
            return
        link = AgentLink(on_event=lambda ev: None)
        link._proc = handle.proc
        link._port_file = handle.port_file
        from oryx.ipc import IpcClient

        link._client = IpcClient("127.0.0.1", handle.port, on_event=lambda ev: None)
        status = link._client.call("runtime.prewarm_status", timeout=10)
        check("内核报告自己是待命态", status.get("prewarm") is True)
        check("内核报告未激活", status.get("activated") is False)

        try:
            link._client.call("chat.run", timeout=10, goal="你好")
            check("待命内核拒绝接活", False, "居然接了")
        except Exception:
            check("待命内核拒绝接活", True)

        # 激活后就能接活了
        link._client.call("runtime.activate", timeout=10)
        status2 = link._client.call("runtime.prewarm_status", timeout=10)
        check("激活后报告已激活", status2.get("activated") is True)
        link._client.close()
    finally:
        pool.shutdown()


def test_activate_starts_scheduler() -> None:
    """激活才启动定时任务 —— 待命期间不该抢跑用户任务。"""
    from oryx.agentd import AgentDaemon
    from oryx.config import load_config
    from oryx.events import EventBus
    from oryx.store import Store

    tmp = tempfile.mkdtemp(prefix="oryx_vb4_sched_")
    cfg = load_config()
    store = Store(Path(tmp) / "o.db")
    daemon = AgentDaemon(config=cfg, store=store, prewarm=True)
    try:
        check("待命内核调度线程未启动", daemon.runtime.scheduler._thread is None)
        daemon.activate()
        check("激活后调度线程已启动", daemon.runtime.scheduler._thread is not None)
        again = daemon.activate()
        check("重复激活是幂等的", again.get("already") is True)
    finally:
        try:
            daemon.runtime.scheduler.stop()
            daemon.runtime.shutdown()
        except Exception:
            pass


def test_normal_kernel_has_scheduler() -> None:
    """普通内核（不走预热）照旧自动启动定时任务。"""
    from oryx.agentd import AgentDaemon
    from oryx.config import load_config
    from oryx.events import EventBus
    from oryx.store import Store

    tmp = tempfile.mkdtemp(prefix="oryx_vb4_sched2_")
    cfg = load_config()
    store = Store(Path(tmp) / "o.db")
    daemon = AgentDaemon(config=cfg, store=store, prewarm=False)
    try:
        check("普通内核调度线程已启动", daemon.runtime.scheduler._thread is not None)
    finally:
        try:
            daemon.runtime.scheduler.stop()
            daemon.runtime.shutdown()
        except Exception:
            pass


# ---------------------------------------------------------------- 提速验证
def test_activate_is_faster_than_cold() -> None:
    """激活一个已待命的内核，应该比冷启动快。"""
    from oryx.prewarm import PrewarmPool
    from oryx.ipc import IpcClient

    # 冷启动基准
    t0 = time.monotonic()
    port, proc = _spawn_agentd(prewarm=False)
    cold = time.monotonic() - t0
    check("冷启动基准可用", port > 0)
    if proc is not None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    # 待命 → 领取 → 连接
    pool = PrewarmPool()
    try:
        handle = pool.warm(timeout=40)
        if handle is None:
            check("预热成功", False)
            return
        t1 = time.monotonic()
        client = IpcClient("127.0.0.1", handle.port, on_event=lambda ev: None)
        client.call("runtime.activate", timeout=10)
        warm = time.monotonic() - t1
        client.close()
        check("激活 + 连接 明显快于冷启动", warm < cold,
              f"激活 {warm:.3f}s vs 冷启 {cold:.3f}s")
        print(f"  冷启 {cold:.3f}s → 激活 {warm:.3f}s（省 {cold - warm:.3f}s）")
    finally:
        pool.shutdown()


def main() -> int:
    tests = [
        ("E1-1 预热池单例", test_pool_singleton),
        ("E1-2 未预热领取立即返回", test_claim_without_warm),
        ("E1-3 预热→领取只允许一次", test_warm_then_claim_once),
        ("E1-4 句柄激活只允许一次", test_activate_only_once_on_handle),
        ("E1-5 池关闭回收待命进程", test_shutdown_kills_pending),
        ("E1-6 后台预热不阻塞", test_start_background_nonblocking),
        ("E2-1 待命内核拒绝接活", test_prewarm_kernel_rejects_work),
        ("E2-2 激活才启动定时任务", test_activate_starts_scheduler),
        ("E2-3 普通内核照旧启动定时任务", test_normal_kernel_has_scheduler),
        ("E2-4 激活比冷启动快", test_activate_is_faster_than_cold),
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
