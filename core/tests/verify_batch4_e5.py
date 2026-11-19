"""第四批 · E5：内核进程级 + 端到端验证。

前面的套件都在进程内跑（引擎不碰模型，可以纯单测）。
这一套起真实内核进程，走真实 IPC，验证：
    1. 工作流 RPC 在内核进程里可用（定义校验、事件推送、落库）
    2. 预热路径端到端：启动 → 领取 → 激活 → 能干活
    3. 两个能力接在一起不打架（预热起来的内核也能跑工作流）

运行：python -m tests.verify_batch4_e5
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
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


def _spawn(extra: list[str], timeout: float = 40.0, home: str = ""):
    """起一个内核进程，返回 (port, proc)。

    隔离的 ORYX_HOME —— 这套会真调模型跑工作流，
    借用户真实的 provider 配置的话，跑到一半超时是必然的
    （本机挂着的 Base 模型不会终止生成，一次调用能烧好几分钟）。
    """
    import tempfile as tf

    fd, raw = tf.mkstemp(prefix="oryx-e5-", suffix=".port")
    os.close(fd)
    pf = Path(raw)
    if not home:
        home = tf.mkdtemp(prefix="oryx-e5-home-")
    cmd = [PYTHON, "-m", "oryx.agentd", "--port", "0", "--port-file", str(pf)] + extra
    proc = subprocess.Popen(
        cmd,
        cwd=str(CORE),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={**os.environ, "ORYX_HOME": home},
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    deadline = time.monotonic() + timeout
    try:
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
    finally:
        try:
            pf.unlink()
        except OSError:
            pass


def _reap(proc) -> None:
    if proc is None:
        return
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


# ---------------------------------------------------------------- 工作流 RPC
def test_workflow_rpc_over_ipc() -> None:
    from oryx.ipc import IpcClient
    from tests.stub_provider import StubProvider

    events: list[dict] = []
    # 挂一个慢但成功的假 provider —— 这套会真调模型跑工作流。
    # 借用户真实的 provider 不行：本机挂着的 Base 模型不会终止生成，
    # 一次调用能烧好几分钟，测试必超时。
    stub = StubProvider(delay=0.2)
    stub.start()
    home = tempfile.mkdtemp(prefix="oryx-e5-home-")
    stub.configure(home)
    port, proc = _spawn([], home=home)
    try:
        check("内核进程起来了", port > 0)
        if port == 0:
            return
        client = IpcClient("127.0.0.1", port, on_event=events.append)

        # 坏定义要被拒，且是 IpcError（不是静默失败）
        try:
            client.call("workflow.run", timeout=20, plan={"goal": "g", "stages": []})
            check("坏定义被拒", False, "居然成功了")
        except Exception as exc:
            check("坏定义被拒", "stages" in str(exc) or "stage" in str(exc), str(exc)[:120])

        # 未知子 Agent 要被拒
        try:
            client.call("workflow.run", timeout=20, plan={
                "goal": "g",
                "stages": [{"steps": [{"agent": "不存在", "goal": "x"}]}],
            })
            check("未知子 Agent 被拒", False, "居然成功了")
        except Exception as exc:
            check("未知子 Agent 被拒", "可用" in str(exc), str(exc)[:160])

        # 合法定义 —— 这里跑的是 doc-manager，它不需要模型就能给出结论
        result = client.call("workflow.run", timeout=120, plan={
            "name": "文档巡检",
            "goal": "把项目文档看一遍并汇报",
            "stages": [{
                "name": "读取",
                "steps": [{
                    "id": "a", "name": "读需求", "agent": "doc-manager",
                    "goal": "读一下项目文档里有哪些需求，如实汇报，读不到就说读不到",
                }],
            }],
        })
        check("工作流跑通", bool(result.get("ok")), str(result)[:200])
        check("返回带 run_id", str(result.get("run_id", "")).startswith("wfr_"),
              str(result.get("run_id")))

        run_id = result["run_id"]
        detail = client.call("workflow.status", timeout=20, run_id=run_id)
        item = detail["content"]
        check("状态是 done", item["status"] == "done", str(item.get("status")))
        check("落库带名字", item["name"] == "文档巡检", str(item.get("name")))
        check("step 已落库", len(item["steps"]) == 1, str(len(item["steps"])))
        check("step 有阶段名", item["steps"][0]["stage"] == "读取",
              item["steps"][0]["stage"])

        listed = client.call("workflow.list", timeout=20, limit=5)
        check("列表能查到", any(r["id"] == run_id for r in listed["runs"]),
              str([r["id"] for r in listed["runs"]]))

        # 事件确实推到控制面了（阶段 + step）
        kinds = [e.get("type") for e in events]
        check("推了 workflow.started", "workflow.started" in kinds, str(set(kinds)))
        check("推了 workflow.finished", "workflow.finished" in kinds, str(set(kinds)))
        check("推了 step 事件",
              any(k and k.startswith("workflow.step") for k in kinds), str(set(kinds)))

        # 事件里只带摘要，不带 step 全文 —— 正文进 SSE 就等于进前端内存
        finished = [e for e in events if e.get("type") == "workflow.step.finished"]
        if finished:
            out_len = len(finished[0].get("summary") or "")
            full_len = len(item["steps"][0]["output"] or "")
            check("事件只带摘要不带全文", out_len <= full_len,
                  f"事件 {out_len} vs 库里 {full_len}")

        client.close()
    finally:
        _reap(proc)
        stub.stop()


def test_workflow_cancel_rpc() -> None:
    """没在跑时中止要报清楚，而不是静默成功。"""
    from oryx.ipc import IpcClient

    port, proc = _spawn([])
    try:
        if port == 0:
            check("内核进程起来了", False)
            return
        client = IpcClient("127.0.0.1", port, on_event=lambda e: None)
        out = client.call("workflow.cancel", timeout=20)
        check("没在跑时中止返回 ok=False", out.get("ok") is False, str(out))
        client.close()
    finally:
        _reap(proc)


def test_workflow_tools_registered_in_kernel() -> None:
    """内核暴露的工具清单里要有 workflow_*。"""
    from oryx.ipc import IpcClient

    port, proc = _spawn([])
    try:
        if port == 0:
            check("内核进程起来了", False)
            return
        client = IpcClient("127.0.0.1", port, on_event=lambda e: None)
        cat = client.call("catalog.tools", timeout=30)
        names = {t["name"] for g in cat["groups"] for t in g["tools"]}
        for want in ("workflow_run", "workflow_status", "workflow_cancel"):
            check(f"工具 {want} 已注册", want in names)
        client.close()
    finally:
        _reap(proc)


# ---------------------------------------------------------------- 预热端到端
def test_prewarm_end_to_end() -> None:
    """AgentLink 完整走一遍预热路径：warm → start → 能调 RPC。"""
    from oryx.agentlink import AgentLink
    from oryx.prewarm import get_pool

    pool = get_pool()
    try:
        handle = pool.warm(timeout=40)
        check("预热成功", handle is not None)
        if handle is None:
            return

        t0 = time.monotonic()
        link = AgentLink(on_event=lambda e: None)
        link.start()
        dt = time.monotonic() - t0
        check("AgentLink 起来了", link.alive)
        check("复用了预热实例", link._proc is handle.proc)
        check("起得够快（< 0.5s）", dt < 0.5, f"{dt:.3f}s")
        print(f"  预热路径启动耗时 {dt:.3f}s")

        status = link.call("runtime.prewarm_status", timeout=10)
        check("激活后报告已激活", status.get("activated") is True, str(status))

        # 关键：预热起来的内核要能真正干活
        cat = link.call("catalog.tools", timeout=30)
        check("预热内核能取到工具清单", cat["total"] > 40, str(cat.get("total")))
        link.stop()
    finally:
        pool.shutdown()


def test_prewarm_kernel_runs_workflow() -> None:
    """两个能力接在一起：预热起来的内核也能跑工作流。"""
    from oryx.agentlink import AgentLink
    from oryx.prewarm import get_pool
    from tests.stub_provider import StubProvider

    # 预热池是拿当前进程的环境起内核的 —— 要隔离只能改进程环境
    stub = StubProvider(delay=0.2)
    stub.start()
    home = tempfile.mkdtemp(prefix="oryx-e5-home-")
    stub.configure(home)
    old_home = os.environ.get("ORYX_HOME")
    os.environ["ORYX_HOME"] = home
    pool = get_pool()
    try:
        handle = pool.warm(timeout=40)
        if handle is None:
            check("预热成功", False)
            return
        link = AgentLink(on_event=lambda e: None)
        link.start()
        try:
            result = link.call("workflow.run", timeout=180, plan={
                "name": "预热后跑工作流",
                "goal": "看一下项目文档",
                "stages": [{"name": "S", "steps": [{
                    "id": "a", "agent": "doc-manager",
                    "goal": "读项目文档并如实汇报",
                }]}],
            })
            check("预热内核能跑工作流", bool(result.get("ok")), str(result)[:200])
            check("返回 run_id", str(result.get("run_id", "")).startswith("wfr_"))
        finally:
            link.stop()
    finally:
        pool.shutdown()
        stub.stop()
        if old_home is None:
            os.environ.pop("ORYX_HOME", None)
        else:
            os.environ["ORYX_HOME"] = old_home


def test_cold_start_fallback() -> None:
    """预热池没跑过时，AgentLink 也能正常冷启（兜底路径）。"""
    from oryx.agentlink import AgentLink
    from oryx.prewarm import PrewarmPool, get_pool

    # 换一个没预热的池子，模拟"预热失败/没启动"
    get_pool()._handle = None
    link = AgentLink(on_event=lambda e: None)
    try:
        link.start()
        check("没预热也能冷启", link.alive)
        cat = link.call("catalog.tools", timeout=30)
        check("冷启内核能干活", cat["total"] > 40, str(cat.get("total")))
    finally:
        link.stop()
        PrewarmPool().shutdown()


def test_two_links_dont_share_kernel() -> None:
    """预热实例只能被领一次：第二个 AgentLink 必须自己冷启。"""
    from oryx.agentlink import AgentLink
    from oryx.prewarm import get_pool

    pool = get_pool()
    try:
        handle = pool.warm(timeout=40)
        if handle is None:
            check("预热成功", False)
            return
        first = AgentLink(on_event=lambda e: None)
        first.start()
        second = AgentLink(on_event=lambda e: None)
        try:
            second.start()
            check("第一个用了预热实例", first._proc is handle.proc)
            check("第二个自己冷启了", second._proc is not handle.proc)
            check("两个内核都活着", first.alive and second.alive)
        finally:
            second.stop()
            first.stop()
    finally:
        pool.shutdown()


# ---------------------------------------------------------------- 打包体检
def test_desktop_bundle_has_new_modules() -> None:
    """打包资源里必须有新模块，否则双击启动会加载旧代码。"""
    import json

    root = CORE.parent
    conf = root / "apps" / "desktop" / "src-tauri" / "tauri.conf.json"
    check("桌面端配置存在", conf.exists(), str(conf))
    if not conf.exists():
        return

    data = json.loads(conf.read_text("utf-8"))
    resources = data.get("bundle", {}).get("resources", {})
    # 资源按目录声明 —— 新增模块不需要改配置，但目录本身必须在列表里
    check("core/oryx 在打包资源里", "../../../core/oryx" in resources, str(resources))
    check("agents 在打包资源里（工作流依赖子 Agent）", "../../../agents" in resources)

    # 源码侧模块确实存在（CORE 是 core/，模块在 core/oryx/）
    for name in ("workflow.py", "prewarm.py"):
        check(f"oryx/{name} 存在", (CORE / "oryx" / name).exists(), str(CORE))

    # 已构建的资源副本若存在，必须同步（否则双击加载旧代码）
    for base in (conf.parent / "target" / "release", conf.parent / "target" / "debug"):
        copied = base / "core" / "oryx"
        if copied.is_dir():
            for name in ("workflow.py", "prewarm.py"):
                check(f"构建产物含 {name}（{base}）", (copied / name).exists())


def main() -> int:
    tests = [
        ("E5-1 工作流 RPC 端到端", test_workflow_rpc_over_ipc),
        ("E5-2 工作流中止 RPC", test_workflow_cancel_rpc),
        ("E5-3 内核工具清单含 workflow_*", test_workflow_tools_registered_in_kernel),
        ("E5-4 预热端到端", test_prewarm_end_to_end),
        ("E5-5 预热内核跑工作流", test_prewarm_kernel_runs_workflow),
        ("E5-6 无预热时冷启兜底", test_cold_start_fallback),
        ("E5-7 两个实例不共享内核", test_two_links_dont_share_kernel),
        ("E5-8 桌面打包资源同步", test_desktop_bundle_has_new_modules),
    ]
    for label, fn in tests:
        print(f"\n[{label}]")
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
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