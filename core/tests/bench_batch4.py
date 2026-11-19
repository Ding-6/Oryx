"""第四批 · 冷启动提速实测。

对比三条路径，验证预热池真的省掉了导入链那段时间：
    A. 冷启动（无预热）—— 进程起 → IPC 可调用
    B. 预热 → 领取 → 激活 —— 领取 → IPC 可调用
    C. 重复冷启动（页缓存已热）—— 排除磁盘 IO 噪声

跑法：python -m tests.bench_batch4
"""
from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PYTHON = sys.executable
CORE = Path(__file__).resolve().parents[1]
ROUNDS = 5


def cold_once(prewarm: bool) -> float:
    """起一个内核到端口就绪，返回秒数。"""
    import os
    import subprocess
    import tempfile

    fd, raw = tempfile.mkstemp(prefix="oryx-bench-", suffix=".port")
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
    t0 = time.monotonic()
    try:
        while True:
            if proc.poll() is not None:
                raise RuntimeError(f"内核退出 code={proc.returncode}")
            try:
                if pf.read_text("utf-8").strip():
                    return time.monotonic() - t0
            except OSError:
                pass
            time.sleep(0.005)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        try:
            pf.unlink()
        except OSError:
            pass


def warm_once() -> float:
    """预热 → 领取 → 连接，返回秒数（不含预热本身）。"""
    from oryx.ipc import IpcClient
    from oryx.prewarm import PrewarmPool

    pool = PrewarmPool()
    try:
        handle = pool.warm(timeout=40)
        if handle is None:
            raise RuntimeError("预热失败")
        t0 = time.monotonic()
        client = IpcClient("127.0.0.1", handle.port, on_event=lambda e: None)
        client.call("runtime.activate", timeout=10)
        dt = time.monotonic() - t0
        client.close()
        return dt
    finally:
        pool.shutdown()


def main() -> int:
    # 先跑一轮当预热（把页缓存弄热），后面的对比才公平
    cold_once(False)

    cold = [cold_once(False) for _ in range(ROUNDS)]
    warm = [warm_once() for _ in range(ROUNDS)]
    warm_cold = [cold_once(False) for _ in range(ROUNDS)]

    def fmt(xs: list[float]) -> str:
        return (
            f"平均 {statistics.mean(xs) * 1000:7.1f}ms  "
            f"中位 {statistics.median(xs) * 1000:7.1f}ms  "
            f"最好 {min(xs) * 1000:7.1f}ms  最差 {max(xs) * 1000:7.1f}ms"
        )

    print(f"\n各 {ROUNDS} 轮（已预热页缓存）")
    print(f"  A 冷启动（首轮）    {fmt(cold)}")
    print(f"  B 预热后激活        {fmt(warm)}")
    print(f"  C 冷启动（末轮）    {fmt(warm_cold)}")

    base = statistics.median(warm_cold)
    got = statistics.median(warm)
    saved = base - got
    print(f"\n结论：中位 {base * 1000:.0f}ms → {got * 1000:.0f}ms，省 {saved * 1000:.0f}ms"
          f"（{saved / base * 100:.0f}%）")

    # 硬断言：激活必须明显快于冷启，否则预热白做
    if got >= base * 0.5:
        print("\n失败：激活路径没有明显快于冷启动，预热池没起作用")
        return 1
    print(f"\n达标：激活耗时是冷启的 {got / base * 100:.0f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())