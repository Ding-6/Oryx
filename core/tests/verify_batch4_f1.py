"""第四批 · 前端接入的 HTTP 契约验证。

前端要渲染工作流列表与详情，所以接口返回的**结构**必须与前端期望严格对齐。
起真实内核 + 真实 HTTP（走 FastAPI + 鉴权头），跑一次编排，逐字段核对。

运行：python -m tests.verify_batch4_f1
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FAILED: list[str] = []
PYTHON = sys.executable
CORE = Path(__file__).resolve().parents[1]
ROOT = CORE.parent


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILED.append(name)


# ---------------------------------------------------------------- HTTP 客户端
class Server:
    """起一个真实的 Oryx server（uvicorn），用 HTTP 访问它。"""

    def __init__(self, port: int = 8791) -> None:
        self.port = port
        self.proc: subprocess.Popen | None = None
        self.token = ""
        # 隔离的 ORYX_HOME：这套会跑 prune-failed 这类**破坏性**接口，
        # 打到真实用户库上会把人家的工作流记录删掉
        self.home = tempfile.mkdtemp(prefix="oryx_f1_home_")

    def start(self) -> bool:
        # CONFIG_PATH 是导入期算出来的，所以只能在子进程的环境里设 ORYX_HOME，
        # 父进程里改 os.environ 对已经 import 过的模块无效
        env = {**os.environ, "ORYX_HOME": self.home}
        self.proc = subprocess.Popen(
            [PYTHON, "-m", "oryx", "--no-browser", "--port", str(self.port)],
            cwd=str(CORE),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                return False
            try:
                # 令牌随首页下发（前端就是这么拿的），从 HTML 里抠出来
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/", timeout=10
                ) as resp:
                    html = resp.read().decode("utf-8")
                m = re.search(r'const ORYX_TOKEN = "([^"]*)"', html)
                if m:
                    self.token = m.group(1)
                    return bool(self.token)
            except Exception:
                pass
            time.sleep(0.2)
        return False

    def restart(self) -> bool:
        """重启 server（同一 ORYX_HOME）。

        内核是独立进程，它启动时读了一次配置就冻结了 —— 改了 config.json
        之后必须重启才能生效。这正是被测系统的一个特性，测试要顺着它。
        """
        self.stop()
        return self.start()

    def _raw(self, method: str, path: str, body=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("X-Oryx-Token", self.token)
        req.add_header("X-Oryx-Request", "1")   # 写操作必须带的来源标记
        if data:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode())

    def get(self, path: str):
        return self._raw("GET", path)

    def post(self, path: str, body=None):
        return self._raw("POST", path, body if body is not None else {})

    def delete(self, path: str):
        return self._raw("DELETE", path)

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=8)
            except Exception:
                self.proc.kill()


# ---------------------------------------------------------------- 契约
def test_isolated_home(srv: Server) -> None:
    """确认 server 跑在隔离的临时库里，不是用户的真实数据。

    这套会调 prune-failed（破坏性）。一旦 ORYX_HOME 没传进去，
    用户的工作流记录会被真删 —— 所以这条断言要放在所有破坏性测试之前。

    光看目录还不够：真正要防的是"server 打开的库是用户的那个"。
    所以再查一次库里有没有被测试写进去的用例名数据 —— 用户库里
    不该出现「前端契约验证」这种名字。
    """
    check("配置落在临时目录", Path(srv.home).is_dir(), srv.home)
    check("临时目录里生成了配置", (Path(srv.home) / "config.json").exists())
    check("临时目录里建了库", (Path(srv.home) / "oryx.db").exists())
    check("临时目录与用户真实目录不同",
          Path(srv.home).resolve() != (Path(os.environ.get("APPDATA", "")) / "Oryx").resolve(),
          srv.home)

    # 直接验"库是隔离的"这件事本身：临时库此刻应当只有 0~1 条 run
    # （空库形状那条还没跑），而绝不该包含用户的历史记录
    import sqlite3

    con = sqlite3.connect(Path(srv.home) / "oryx.db")
    try:
        names = [r[0] for r in con.execute("SELECT name FROM workflow_runs")]
    except Exception:
        names = []
    finally:
        con.close()
    check("临时库是干净的（不含用例名数据）",
          not any("验证" in n or "清理目标" in n for n in names), str(names[:5]))


def test_token_and_ping(srv: Server) -> None:
    d = srv.get("/api/ping")
    check("/api/ping 通", isinstance(d, dict), str(d)[:120])


def test_workflow_list_shape(srv: Server) -> None:
    """空列表也要返回 {"runs": [...]} —— 前端 renderWorkflows 直接读 .runs。

    这条要在任何造数据的用例之前跑：临时库是全新的，空列表形状才有意义。
    """
    d = srv.get("/api/workflows?limit=5")
    check("列表返回 dict", isinstance(d, dict), str(d)[:120])
    check("列表有 runs 键", "runs" in d, str(list(d.keys()))[:120])
    check("runs 是数组", isinstance(d.get("runs"), list), type(d.get("runs")).__name__)
    check("空库时列表为空（前置条件）", d.get("runs") == [], str(len(d.get("runs") or [])))
    c = srv.get("/api/workflows/failed-count")
    check("failed-count 在空库时为 0", c.get("count") == 0, str(c))


def test_workflow_run_and_detail(srv: Server) -> None:
    """跑一次编排，核对列表项与详情项的字段是否够前端渲染。"""
    plan = {
        "name": "前端契约验证",
        "goal": "读项目文档并如实汇报",
        "stages": [
            {"name": "读取", "steps": [
                {"id": "a", "name": "读需求", "agent": "doc-manager",
                 "goal": "读一下项目文档里有哪些需求，读不到就明说"},
            ]},
            {"name": "复核", "steps": [
                {"id": "b", "name": "再确认", "agent": "doc-manager",
                 "goal": "把刚才看到的要点压缩成三条", "needs": ["a"]},
            ]},
        ],
    }
    r = srv.post("/api/workflows/run", {"plan": plan, "args": {"env": "test"}})
    run_id = r.get("run_id") or ""
    # 临时库里没有 provider，step 会失败 —— 这是**预期**的：契约验证要的是
    # 字段结构，不是模型输出。断言只依赖"run 被创建出来且结构完整"。
    check("run 返回 run_id", run_id.startswith("wfr_"), str(r)[:200])
    check("run 返回 content（汇总或失败说明）", len(str(r.get("content") or "")) > 0)
    check("run 带状态字段", r.get("status") in ("done", "failed"), str(r.get("status")))
    if not run_id:
        return

    # 详情：前端 openWorkflowDetail 依赖的字段
    d = srv.get(f"/api/workflows/{run_id}")
    check("详情返回 content 包装", "content" in d, str(list(d.keys())))
    item = d["content"]
    for field in ("id", "name", "goal", "status", "created_at", "steps", "args"):
        check(f"详情含 {field}", field in item, str(list(item.keys())))
    check("detail.content 是 run 本身", item.get("id") == run_id, str(item.get("id")))
    check("args 透传", item.get("args") == {"env": "test"}, str(item.get("args")))

    steps = item.get("steps") or []
    check("step 列表非空", len(steps) >= 2, str(len(steps)))
    # 前端按 stage 字段分组渲染 —— 这条是整个详情页的地基
    check("每个 step 都带 stage", all(s.get("stage") for s in steps),
          str([s.get("stage") for s in steps]))
    check("stage 名对应编排里的阶段名",
          {s.get("stage") for s in steps} == {"读取", "复核"},
          str({s.get("stage") for s in steps}))
    for field in ("id", "run_id", "stage", "name", "agent", "status",
                  "goal", "output", "duration_ms", "created_at", "ended_at"):
        check(f"step 含 {field}", field in steps[0], str(list(steps[0].keys())))
    check("step 有耗时", steps[0].get("duration_ms") is not None)
    # 主键是 "<run_id>:<step_id>" —— 前端 shortStepId() 靠冒号切出后半段
    check("step 主键是 <run_id>:<step_id>",
          str(steps[0].get("id", "")).startswith(run_id + ":"), str(steps[0].get("id")))
    check("至少两个阶段（验证跨阶段依赖跑通）",
          len({s.get("stage") for s in steps}) == 2,
          str({s.get("stage") for s in steps}))

    # 列表项：renderWorkflows 的表格列
    lst = srv.get("/api/workflows?limit=10")["runs"]
    hit = next((x for x in lst if x["id"] == run_id), None)
    check("列表能查到刚跑的 run", hit is not None, str([x["id"] for x in lst]))
    if hit:
        for field in ("id", "name", "goal", "status", "created_at", "ended_at",
                       "step_counts", "total_steps", "failed_steps"):
            check(f"列表项含 {field}", field in hit, str(list(hit.keys())))
        check("ended_at 已填（完成态）", hit.get("ended_at") is not None, str(hit.get("ended_at")))
        # 前端算耗时靠 created_at / ended_at 两个字段
        check("ended_at >= created_at",
              (hit.get("ended_at") or 0) >= (hit.get("created_at") or 0),
              f"{hit.get('created_at')} -> {hit.get('ended_at')}")
        # 前端靠 failed_steps 显示"N 失败"徽标，不能靠正则去猜 error 文本
        check("total_steps 与 step 数一致",
              hit.get("total_steps") == len(steps), f"{hit.get('total_steps')} vs {len(steps)}")
        check("failed_steps 统计正确",
              hit.get("failed_steps") == sum(1 for s in steps if s["status"] == "failed"),
              str(hit.get("failed_steps")))
        check("step_counts 按状态分组且总数吻合",
              isinstance(hit.get("step_counts"), dict)
              and sum(hit["step_counts"].values()) == hit.get("total_steps"),
              str(hit.get("step_counts")))
        check("列表项不含 step 正文（否则响应体爆炸）", "steps" not in hit, str(list(hit.keys())))
        # 临时库无 provider，step 必然全失败 → run 应报 failed（不是 done）。
        # 这条同时验证了"全军覆没不许报完成"这条语义。
        check("状态与 step 结果一致",
              hit.get("status") == ("failed" if hit.get("failed_steps") == hit.get("total_steps")
                                    else "done"),
              f"status={hit.get('status')} failed={hit.get('failed_steps')}/{hit.get('total_steps')}")


def test_delete_roundtrip(srv: Server) -> None:
    """删除一次 run 后，列表与详情都要真的少一条。"""
    plan = {
        "name": "删除接口验证",
        "goal": "跑完就删",
        "stages": [{"name": "S", "steps": [
            {"id": "a", "agent": "doc-manager", "goal": "读项目文档并用一句话概括"},
        ]}],
    }
    r = srv.post("/api/workflows/run", {"plan": plan})
    run_id = r.get("run_id") or ""
    check("先跑出一次 run", bool(run_id), str(r)[:160])
    if not run_id:
        return

    before = srv.get("/api/workflows?limit=50")["runs"]
    check("删除前列表含它", any(x["id"] == run_id for x in before), str(len(before)))

    srv.delete(f"/api/workflows/{run_id}")

    after = srv.get("/api/workflows?limit=50")["runs"]
    check("删除后列表不含它", not any(x["id"] == run_id for x in after), str(len(after)))
    try:
        srv.get(f"/api/workflows/{run_id}")
        check("删除后详情回 404", False, "居然还能查到")
    except urllib.error.HTTPError as exc:
        check("删除后详情回 404", exc.code == 404, str(exc.code))

    try:
        srv.delete(f"/api/workflows/{run_id}")
        check("重复删除回 404", False, "居然成功了")
    except urllib.error.HTTPError as exc:
        check("重复删除回 404", exc.code == 404, str(exc.code))


def test_prune_failed_endpoint(srv: Server) -> None:
    """批量清理全失败 run。

    关键点是"全失败"必须在库侧判定：列表接口分页，前端循环删的话
    删掉一页里的失败记录、更早的补位上来，永远清不干净。

    造一条全失败记录用 Store 直写 —— HTTP 层没有"故意失败"的入口，
    而走真编排要调模型，既慢又不确定（模型可能成功）。
    """
    before = srv.get("/api/workflows/failed-count")["count"]

    run_id = _seed_all_failed_run(srv)
    check("造出全失败 run", bool(run_id), "造数据失败")
    if not run_id:
        return

    # 用差值而不是绝对值：前面的用例已经在临时库里造过一些全失败记录
    mid = srv.get("/api/workflows/failed-count")["count"]
    check("全失败计数 +1", mid == before + 1, f"{before} -> {mid}")
    check("列表里能看到它（进度 0/1）",
          any(x["id"] == run_id and x["failed_steps"] == 1
              for x in srv.get("/api/workflows?limit=50")["runs"]))

    pruned = srv.post("/api/workflows/prune-failed")["pruned"]
    check("一次清掉全部（>=刚造的那 1 个）", pruned >= 1, str(pruned))
    check("清理后计数归零", srv.get("/api/workflows/failed-count")["count"] == 0,
          str(srv.get("/api/workflows/failed-count")["count"]))
    left = {x["id"] for x in srv.get("/api/workflows?limit=50")["runs"]}
    check("被清理的 run 已不在列表", run_id not in left)

    # 幂等：再清一次无事可做
    check("幂等：没有可清的返回 0", srv.post("/api/workflows/prune-failed")["pruned"] == 0)


def _seed_all_failed_run(srv: Server) -> str:
    """直写 Store 造一条全失败的 run，返回 run_id。

    走真编排造不出来：定义校验会先拒掉未知 agent，而正常 agent 能不能
    失败取决于模型 —— 不可重复。HTTP 契约本身不需要真的跑一遍。

    写入的是**隔离的临时库**（srv.home），绝不碰用户数据。
    """
    import time
    import uuid

    sys.path.insert(0, str(CORE))
    from oryx.store import Store

    store = Store(Path(srv.home) / "oryx.db")
    run_id = f"wfr_{uuid.uuid4().hex[:12]}"
    now = time.time()
    store.create_workflow_run({
        "id": run_id, "name": "清理目标-全失败", "goal": "触发全失败",
        "status": "failed", "args": {}, "created_at": now,
    })
    store.add_workflow_step({
        "id": f"{run_id}:a", "run_id": run_id, "stage": "S1", "name": "读文档",
        "agent": "doc-manager", "goal": "读文档", "status": "failed",
        "output": "", "error": "模型服务未就绪（构造的失败）",
        "duration_ms": 1, "created_at": now,
    })
    store.finish_workflow_run(run_id, "failed", "1 个 step 全部失败")
    return run_id


def test_event_loop_not_blocked(srv: Server) -> None:
    """编排跑起来时，其他接口必须还能响应。

    这是"运行中"视图能不能出现的前提：/api/workflows/run 是 async 端点，
    若在里面直接调同步阻塞的 agent.call()，整个事件循环会被独占 ——
    SSE 与列表接口全发不出去，前端就永远看不到运行态。

    要测出这个问题，编排必须**跑得够久**：临时库没真 provider 时编排会在
    几毫秒内失败，探活采不到样本，"测不出问题"会被误当成"没问题"。
    所以这里挂一个慢但成功的假 provider（见 stub_provider）。
    """
    import threading

    from tests.stub_provider import StubProvider

    stub = StubProvider(delay=1.2)
    stub.start()
    stub.configure(srv.home)
    srv.restart()          # 让内核按新配置重载
    time.sleep(1.0)

    plan = {
        "name": "事件循环占用检测",
        "goal": "跑一个多 step 编排，期间并发探活",
        "stages": [{"name": "S", "steps": [
            {"id": f"s{i}", "agent": "doc-manager", "goal": f"读文档要点 {i}"}
            for i in range(4)
        ]}],
    }
    latencies: list[float] = []
    errors: list[str] = []
    stop = threading.Event()

    def probe() -> None:
        while not stop.is_set():
            t0 = time.monotonic()
            try:
                # /api/appearance 是轻量接口；它被卡住就是事件循环被占
                req = urllib.request.Request(
                    f"http://127.0.0.1:{srv.port}/api/appearance", method="GET")
                req.add_header("X-Oryx-Token", srv.token)
                req.add_header("X-Oryx-Request", "1")
                with urllib.request.urlopen(req, timeout=30) as resp:
                    resp.read()
                latencies.append(time.monotonic() - t0)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{type(exc).__name__}")
                latencies.append(time.monotonic() - t0)
            time.sleep(0.2)

    t = threading.Thread(target=probe, name="wf-probe", daemon=True)
    t.start()
    try:
        srv.post("/api/workflows/run", {"plan": plan})
    finally:
        stop.set()
        t.join(timeout=5)
        stub.stop()

    check("假 provider 确实被调过", stub.calls >= 1, f"calls={stub.calls}")
    check("编排期间探活有多次采样", len(latencies) >= 3, str(len(latencies)))
    check("探活无失败", not errors, "、".join(errors[:3]))
    if latencies:
        worst = max(latencies)
        check("事件循环未被独占（最坏延迟 < 3s）", worst < 3.0,
              f"最坏 {worst:.2f}s，共 {len(latencies)} 次采样")
        print(f"  编排 {stub.calls} 次模型调用；探活延迟：中位 "
              f"{sorted(latencies)[len(latencies) // 2] * 1000:.0f}ms 最坏 {worst * 1000:.0f}ms")


def test_detail_unknown_run(srv: Server) -> None:
    """查不存在的 run 要 404，前端弹 toast 而不是拿到空对象。"""
    try:
        srv.get("/api/workflows/wfr_nonexistent")
        check("查不到的 run 回 404", False, "居然返回 200")
    except urllib.error.HTTPError as exc:
        check("查不到的 run 回 404", exc.code == 404, str(exc.code))
    except Exception as exc:  # noqa: BLE001
        check("查不到的 run 回 404", False, f"{type(exc).__name__}: {exc}")


def test_bad_plan_rejected(srv: Server) -> None:
    """坏定义要 400 —— 前端会把它显示成"失败：xxx"。"""
    for name, plan in (
        ("缺 stages", {"goal": "g"}),
        ("空 stages", {"goal": "g", "stages": []}),
        ("未知 agent", {"goal": "g", "stages": [{"steps": [{"agent": "不存在", "goal": "x"}]}]}),
    ):
        try:
            r = srv.post("/api/workflows/run", {"plan": plan})
            # FastAPI 校验不通过也可能是 200+ok:false，两种都算"被拒"
            rejected = (not r.get("ok")) or r.get("run_id") is None
            check(f"{name} 被拒", rejected, str(r)[:160])
        except urllib.error.HTTPError as exc:
            check(f"{name} 被拒", exc.code in (400, 422), str(exc.code))
        except Exception as exc:  # noqa: BLE001
            check(f"{name} 被拒", False, f"{type(exc).__name__}: {exc}")


def test_cancel_without_active(srv: Server) -> None:
    """没在跑时中止：返回 ok=false 即可，前端据此提示，不该是 500。"""
    try:
        r = srv.post("/api/workflows/wfr_nonexistent/cancel")
        check("中止不存在的 run 有明确返回", isinstance(r, dict), str(r)[:120])
        check("中止不存在的 run 报 ok=false", r.get("ok") is False, str(r)[:120])
    except urllib.error.HTTPError as exc:
        # 404 也算合理：前端两种都能显示提示
        check("中止不存在的 run 有明确返回", exc.code in (400, 404), str(exc.code))


def test_auth_guard(srv: Server) -> None:
    """工作流接口必须和其他接口一样受双防护约束。"""
    req = urllib.request.Request(
        f"http://127.0.0.1:{srv.port}/api/workflows", method="GET")
    req.add_header("X-Oryx-Request", "1")   # 有来源标记但没令牌
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            check("无令牌被拒", False, f"返回 {resp.status}")
    except urllib.error.HTTPError as exc:
        check("无令牌被拒", exc.code == 401, str(exc.code))


def test_web_page_served(srv: Server) -> None:
    """首页要真的带上工作流导航项（打包界面与源码一致）。"""
    url = f"http://127.0.0.1:{srv.port}/"
    with urllib.request.urlopen(url, timeout=20) as resp:
        html = resp.read().decode("utf-8")
    check("首页可访问", len(html) > 100_000, str(len(html)))
    check("首页含工作流导航", 'data-view="workflows"' in html)
    check("首页含 wfCnt 徽标", 'id="wfCnt"' in html)
    check("首页含工作流渲染函数", "function renderWorkflows" in html)
    check("首页含 SSE 接线", "case 'workflow.step.finished'" in html)


def test_bundle_sync() -> None:
    """打包暂存目录的界面必须与源码同步（否则双击启动加载旧界面）。"""
    src = (ROOT / "core" / "web" / "index.html").read_text("utf-8")
    staged = ROOT / "apps" / "desktop" / "src-tauri" / "target" / "release" / "core" / "web" / "index.html"
    if not staged.exists():
        check("打包暂存目录存在", False, str(staged))
        return
    copy = staged.read_text("utf-8")
    check("打包界面与源码一致", copy == src,
          f"源码 {len(src)} 字节 vs 暂存 {len(copy)} 字节")
    check("打包界面含工作流导航", 'data-view="workflows"' in copy)


def test_script_syntax() -> None:
    """内联脚本必须能被 JS 引擎真正编译过，且 CSS 注释不能漏闭合。

    括号配平检查漏过一次真错误：`${rest.length ?: ...}` 里把三元写成 `?:`，
    括号与引号都配平、静态检查全绿，而浏览器里整段脚本因 SyntaxError
    一行都不执行 —— 页面只剩导航外壳，列表一条都不渲染。

    CSS 注释漏 `*/` 更阴：静默把后面的规则吞掉，不报任何错。踩过一次，
    `table-layout:fixed` 与 min-width 全失效，列宽退化成 auto，按钮溢出 18px。
    """
    from tests.check_web_script import check_css_comments
    from tests.check_web_syntax import compile_in_chrome, extract_scripts, find_chrome

    text = (CORE / "web" / "index.html").read_text("utf-8")
    ok, why = check_css_comments(text)
    check("CSS 注释成对闭合", ok, why)

    chrome = find_chrome()
    if not chrome:
        check("找得到 Chrome/Edge", False, "静态检查无法降级，这条必须跑")
        return
    src = "\n".join(extract_scripts(text))
    ok, err = compile_in_chrome(chrome, src)
    check(f"内联脚本语法合法（{len(src)} 字节）", ok, err)


def main() -> int:
    srv = Server()
    print("启动 server …")
    if not srv.start():
        print("  FAIL server 没起来")
        return 1
    print(f"  server 就绪（端口 {srv.port}）\n")
    try:
        tests = [
            # 顺序有讲究：空库形状要先验（任何造数据之前），
            # 事件循环那条要放最后 —— 它会重启 server 并改 provider 配置
            ("F1-0 脚本能被引擎编译", test_script_syntax),
            ("F1-1 隔离临时库（破坏性测试的前提）", test_isolated_home),
            ("F1-2 令牌与探活", test_token_and_ping),
            ("F1-3 空库时的列表形状", test_workflow_list_shape),
            ("F1-4 run + 详情字段", test_workflow_run_and_detail),
            ("F1-5 删除往返", test_delete_roundtrip),
            ("F1-6 批量清理全失败", test_prune_failed_endpoint),
            ("F1-7 查不到 run 回 404", test_detail_unknown_run),
            ("F1-8 坏定义被拒", test_bad_plan_rejected),
            ("F1-9 空跑中止", test_cancel_without_active),
            ("F1-10 鉴权双防护", test_auth_guard),
            ("F1-11 首页带工作流视图", test_web_page_served),
            ("F1-12 打包界面同步", test_bundle_sync),
            ("F1-13 事件循环不被独占", test_event_loop_not_blocked),
        ]
        for label, fn in tests:
            print(f"\n[{label}]")
            try:
                fn(srv) if fn.__code__.co_argcount else fn()
            except Exception as exc:  # noqa: BLE001
                import traceback

                traceback.print_exc()
                FAILED.append(label)
    finally:
        srv.stop()
    print("\n" + "=" * 60)
    if FAILED:
        print(f"失败 {len(FAILED)} 项：{'、'.join(FAILED)}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())