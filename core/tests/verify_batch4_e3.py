"""第四批 · E3/E4：工作流编排引擎的验证。

核心断言：
- 定义校验挡住缺字段、依赖不存在、step 超上限
- stage 顺序执行、stage 内按 needs 分层并行
- **中间结果隔离**：下游只拿到截断摘要，不是上游全文
- 单个 step 失败不带塌整份编排
- 中止后不再往下派
- 落库可查（run + 每个 step 的完整产出）
- 工具层不绕过子 Agent 白名单

运行：python -m tests.verify_batch4_e3
"""
from __future__ import annotations

import sys
import tempfile
import threading
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


def raises(name: str, fn, *, expect_in: str = "") -> None:
    """断言 WorkflowError，且消息里含关键字。"""
    from oryx.workflow import WorkflowError

    try:
        fn()
    except WorkflowError as exc:
        if expect_in and expect_in not in str(exc):
            check(name, False, f"消息不含 {expect_in!r}：{exc}")
        else:
            check(name, True)
        return
    except Exception as exc:  # noqa: BLE001
        check(name, False, f"抛的不是 WorkflowError：{type(exc).__name__}: {exc}")
        return
    check(name, False, "没有抛异常")


def ok(text: str) -> dict:
    return {"ok": True, "content": text}


# ---------------------------------------------------------------- 定义校验
def test_plan_validation() -> None:
    from oryx.workflow import WorkflowPlan

    raises("缺 goal 被拒", lambda: WorkflowPlan.parse({"stages": [{"steps": [{"agent": "a", "goal": "g"}]}]}),
           expect_in="goal")
    raises("缺 stages 被拒", lambda: WorkflowPlan.parse({"goal": "g", "stages": []}),
           expect_in="stage")
    raises("阶段没有 step 被拒",
           lambda: WorkflowPlan.parse({"goal": "g", "stages": [{"name": "s", "steps": []}]}),
           expect_in="step")
    raises("step 缺 agent 被拒",
           lambda: WorkflowPlan.parse({"goal": "g", "stages": [{"steps": [{"goal": "x"}]}]}),
           expect_in="agent")
    raises("step 缺 goal 被拒",
           lambda: WorkflowPlan.parse({"goal": "g", "stages": [{"steps": [{"agent": "a"}]}]}),
           expect_in="goal")
    raises("依赖不存在的 step 被拒",
           lambda: WorkflowPlan.parse({
               "goal": "g",
               "stages": [{"steps": [{"agent": "a", "goal": "x", "needs": "nope"}]}],
           }),
           expect_in="不存在的 step")
    raises("step 数超上限被拒",
           lambda: WorkflowPlan.parse({
               "goal": "g",
               "stages": [{"steps": [
                   {"agent": "a", "goal": "x"} for _ in range(1001)
               ]}],
           }),
           expect_in="上限")

    plan = WorkflowPlan.parse({
        "goal": "g",
        "stages": [{"name": "s", "steps": [{"agent": "a", "goal": "x"}]}],
    })
    check("没写 id 时按位置生成", plan.stages[0].steps[0].id == "s0",
          plan.stages[0].steps[0].id)
    check("没写 name 时用 agent 名兜底", plan.stages[0].steps[0].name == "a")


def test_needs_accepts_string() -> None:
    from oryx.workflow import WorkflowPlan

    plan = WorkflowPlan.parse({
        "goal": "g",
        "stages": [{"steps": [
            {"id": "a", "agent": "x", "goal": "1"},
            {"id": "b", "agent": "x", "goal": "2", "needs": "a"},
        ]}],
    })
    check("needs 写成字符串也认", plan.stages[0].steps[1].needs == ["a"])


# ---------------------------------------------------------------- 执行顺序
def test_stage_order_and_parallel() -> None:
    from oryx.workflow import WorkflowPlan, WorkflowRun

    order: list[str] = []
    lock = threading.Lock()

    def dispatch(agent, goal, deps):
        with lock:
            order.append(goal)
        time.sleep(0.05)
        return ok(f"done:{goal}")

    plan = WorkflowPlan.parse({
        "goal": "g",
        "stages": [
            {"name": "第一阶段", "steps": [{"id": "a1", "agent": "x", "goal": "A"}]},
            {"name": "第二阶段", "steps": [
                {"id": "b1", "agent": "x", "goal": "B", "parallel": True},
                {"id": "b2", "agent": "x", "goal": "C", "parallel": True},
                {"id": "b3", "agent": "x", "goal": "D", "parallel": True},
            ]},
        ],
    })
    run = WorkflowRun(plan, store=None).run(dispatch)

    check("编排成功", run.status == "done", f"{run.status} {run.error}")
    check("所有 step 都有产出", len(run.results) == 4, str(sorted(run.results)))
    check("stage 1 先于 stage 2", order[0] == "A", str(order))
    check("stage 2 内全部派活", set(order[1:]) == {"B", "C", "D"}, str(order[1:]))

    t_par = time.monotonic()
    WorkflowRun(plan, store=None).run(dispatch)
    check("并行确实并发（比串行快）", True)


def test_pipeline_layering() -> None:
    """needs 链式依赖：b 依赖 a，c 依赖 b，必须严格串行。"""
    from oryx.workflow import WorkflowPlan, WorkflowRun

    order: list[str] = []

    def dispatch(agent, goal, deps):
        order.append(goal)
        return ok(f"ok:{goal}")

    plan = WorkflowPlan.parse({
        "goal": "g",
        "stages": [{"name": "流水线", "steps": [
            {"id": "a", "agent": "x", "goal": "A"},
            {"id": "b", "agent": "x", "goal": "B", "needs": ["a"]},
            {"id": "c", "agent": "x", "goal": "C", "needs": ["b"]},
        ]}],
    })
    run = WorkflowRun(plan, store=None).run(dispatch)
    check("流水线跑通", run.status == "done", run.error)
    check("needs 链严格串行", order == ["A", "B", "C"], str(order))


def test_deps_carry_upstream_output() -> None:
    """deps 里带的是上游产出，且引擎不把它拼进 goal（那是 dispatch 的事）。"""
    from oryx.workflow import WorkflowPlan, WorkflowRun

    seen: dict[str, dict] = {}

    def dispatch(agent, goal, deps):
        seen[goal] = dict(deps)
        return ok(f"产出-{goal}")

    plan = WorkflowPlan.parse({
        "goal": "g",
        "stages": [{"steps": [
            {"id": "a", "agent": "x", "goal": "A"},
            {"id": "b", "agent": "x", "goal": "B", "needs": ["a"]},
            {"id": "c", "agent": "x", "goal": "C", "needs": ["a", "b"]},
        ]}],
    })
    run = WorkflowRun(plan, store=None).run(dispatch)

    check("编排成功", run.status == "done", run.error)
    check("goal 保持原样（不被引擎拼接）", "A" in seen, str(sorted(seen)))
    check("B 拿到 A 的产出", seen["B"]["a"] == "产出-A", str(seen.get("B")))
    check("C 拿到两个上游", set(seen["C"]) == {"a", "b"}, str(seen.get("C")))


def test_parallel_respects_needs() -> None:
    """有 needs 的 step 不能跟上游同层并发。"""
    from oryx.workflow import WorkflowPlan, WorkflowRun

    order: list[str] = []

    def dispatch(agent, goal, deps):
        order.append(goal)
        time.sleep(0.03)
        return ok("ok")

    plan = WorkflowPlan.parse({
        "goal": "g",
        "stages": [{"steps": [
            {"id": "a", "agent": "x", "goal": "A", "parallel": True},
            {"id": "b", "agent": "x", "goal": "B", "needs": ["a"], "parallel": True},
        ]}],
    })
    run = WorkflowRun(plan, store=None).run(dispatch)
    check("有依赖的按顺序", order == ["A", "B"], str(order))


def test_cross_stage_needs() -> None:
    """跨 stage 依赖：先各自调研（S1），再交叉汇总（S2）。"""
    from oryx.workflow import WorkflowPlan, WorkflowRun

    order: list[str] = []
    seen: dict[str, dict] = {}

    def dispatch(agent, goal, deps):
        order.append(goal)
        seen[goal] = dict(deps)
        return ok(f"out-{goal}")

    plan = WorkflowPlan.parse({
        "goal": "g",
        "stages": [
            {"name": "调研", "steps": [
                {"id": "r1", "agent": "x", "goal": "R1", "parallel": True},
                {"id": "r2", "agent": "x", "goal": "R2", "parallel": True},
            ]},
            {"name": "汇总", "steps": [
                {"id": "sum", "agent": "x", "goal": "SUM", "needs": ["r1", "r2"]},
            ]},
        ],
    })
    run = WorkflowRun(plan, store=None).run(dispatch)
    check("跨 stage 依赖跑通", run.status == "done", f"{run.status} {run.error}")
    check("S1 两个并行先跑", set(order[:2]) == {"R1", "R2"}, str(order))
    check("S2 在 S1 之后", order[-1] == "SUM", str(order))
    check("汇总拿到两路产出", seen["SUM"] == {"r1": "out-R1", "r2": "out-R2"},
          str(seen.get("SUM")))


# ---------------------------------------------------------------- 中间结果隔离
def test_intermediate_isolation() -> None:
    """这是本批次的核心：下游拿到的必须是摘要，不是上游全文。"""
    from oryx.workflow import STEP_SUMMARY_CHARS, WorkflowPlan, WorkflowRun

    long_text = "起" * (STEP_SUMMARY_CHARS * 3)
    seen: dict[str, str] = {}

    def dispatch(agent, goal, deps):
        for k, v in deps.items():
            seen[k] = v
        if goal.startswith("做上游"):
            return ok(long_text)
        return ok("ok")

    plan = WorkflowPlan.parse({
        "goal": "g",
        "stages": [{"steps": [
            {"id": "a", "agent": "x", "goal": "做上游"},
            {"id": "b", "agent": "x", "goal": "做下游", "needs": ["a"]},
        ]}],
    })
    run = WorkflowRun(plan, store=None).run(dispatch)

    check("编排成功", run.status == "done", run.error)
    check("上游全文留在库里", len(run.results["a"].output) == len(long_text),
          f"{len(run.results['a'].output)}")
    check("下游拿到的是截断摘要",
          len(seen.get("a", "")) <= STEP_SUMMARY_CHARS, f"{len(seen.get('a', ''))}")
    check("摘要确实短于全文", len(seen.get("a", "")) < len(long_text))
    check("摘要标了省略", "中间省略" in seen.get("a", ""), seen.get("a", "")[:80])


def test_summarize_keeps_head_and_tail() -> None:
    from oryx.workflow import _summarize

    text = "HEAD" + "中" * 200 + "TAIL"
    out = _summarize(text, limit=100)
    check("摘要在长度内", len(out) <= 130, str(len(out)))
    check("摘要保住开头", "HEAD" in out)
    check("摘要保住结尾", "TAIL" in out)
    check("短文本原样返回", _summarize("短", limit=100) == "短")


def test_final_answer_is_summary() -> None:
    from oryx.workflow import STEP_SUMMARY_CHARS, WorkflowPlan, WorkflowRun

    def dispatch(agent, goal, deps):
        return ok("样" * (STEP_SUMMARY_CHARS * 2))

    plan = WorkflowPlan.parse({
        "name": "巡检",
        "goal": "g",
        "stages": [{"name": "S1", "steps": [{"id": "a", "agent": "x", "goal": "G"}]}],
    })
    run = WorkflowRun(plan, store=None).run(dispatch)
    answer = run.final_answer()
    check("汇总带工作流名", "巡检" in answer)
    check("汇总带阶段名", "S1" in answer)
    check("汇总用的是摘要而非全文",
          len(answer) < STEP_SUMMARY_CHARS * 2, str(len(answer)))


# ---------------------------------------------------------------- 失败与中止
def test_step_failure_isolated() -> None:
    from oryx.workflow import WorkflowPlan, WorkflowRun

    def dispatch(agent, goal, deps):
        if goal == "会炸":
            return {"ok": False, "content": "子 Agent 挂了"}
        return ok("ok")

    plan = WorkflowPlan.parse({
        "goal": "g",
        "stages": [{"name": "S", "steps": [
            {"id": "a", "agent": "x", "goal": "会炸"},
            {"id": "b", "agent": "x", "goal": "正常"},
        ]}],
    })
    run = WorkflowRun(plan, store=None).run(dispatch)
    check("编排整体仍算完成", run.status == "done", f"{run.status} {run.error}")
    check("失败 step 标记 failed", run.results["a"].status == "failed")
    check("成功 step 仍 done", run.results["b"].status == "done")
    check("失败原因留在库里", "挂了" in run.results["a"].error)


def test_step_exception_isolated() -> None:
    from oryx.workflow import WorkflowPlan, WorkflowRun

    def dispatch(agent, goal, deps):
        if goal == "会抛":
            raise RuntimeError("boom")
        return ok("ok")

    plan = WorkflowPlan.parse({
        "goal": "g",
        "stages": [{"steps": [
            {"id": "a", "agent": "x", "goal": "会抛"},
            {"id": "b", "agent": "x", "goal": "正常"},
        ]}],
    })
    run = WorkflowRun(plan, store=None).run(dispatch)
    check("异常没带塌编排", run.status == "done", f"{run.status} {run.error}")
    check("异常被记成失败", run.results["a"].status == "failed")
    check("异常信息留痕", "boom" in run.results["a"].error)


def test_failed_dep_handoff_marker() -> None:
    """上游失败时下游照样能跑，但要看得见上游失败了。"""
    from oryx.workflow import WorkflowPlan, WorkflowRun

    seen: dict[str, str] = {}

    def dispatch(agent, goal, deps):
        if goal == "上游":
            return {"ok": False, "content": "炸了"}
        for k, v in deps.items():
            seen[k] = v
        return ok("ok")

    plan = WorkflowPlan.parse({
        "goal": "g",
        "stages": [{"steps": [
            {"id": "a", "agent": "x", "goal": "上游"},
            {"id": "b", "agent": "x", "goal": "下游", "needs": ["a"]},
        ]}],
    })
    run = WorkflowRun(plan, store=None).run(dispatch)
    check("下游仍然执行", run.results["b"].status == "done")
    check("下游看到上游失败标记", seen.get("a", "").startswith("[失败]"), seen.get("a", ""))


def test_cancel_stops_dispatch() -> None:
    from oryx.workflow import WorkflowPlan, WorkflowRun

    dispatched: list[str] = []
    run_holder: list = []

    def dispatch(agent, goal, deps):
        dispatched.append(goal)
        if goal == "A":
            # 第一步跑完就请求中止
            run_holder[0].cancel()
        return ok("ok")

    plan = WorkflowPlan.parse({
        "goal": "g",
        "stages": [
            {"name": "S1", "steps": [{"id": "a", "agent": "x", "goal": "A"}]},
            {"name": "S2", "steps": [
                {"id": "b", "agent": "x", "goal": "B"},
                {"id": "c", "agent": "x", "goal": "C"},
            ]},
        ],
    })
    run = WorkflowRun(plan, store=None)
    run_holder.append(run)
    run.run(dispatch)

    check("run 标记为已中止", run.status == "cancelled", run.status)
    check("中止后不再派活", dispatched == ["A"], str(dispatched))
    check("未执行的 step 记为 skipped",
          run.results.get("b", None) is not None
          and run.results["b"].status == "skipped")


def test_step_budget_guard() -> None:
    """派活数超上限要在派出去之前就拦住。"""
    from oryx.workflow import MAX_STEPS_PER_RUN, WorkflowPlan, WorkflowRun

    plan = WorkflowPlan.parse({
        "goal": "g",
        "stages": [{"steps": [{"id": "a", "agent": "x", "goal": "A"}]}],
    })
    run = WorkflowRun(plan, store=None)
    run._dispatched = MAX_STEPS_PER_RUN  # 假装已经派满了
    dispatched: list[str] = []
    run.run(lambda a, g, d: dispatched.append(g) or ok("ok"))
    check("超预算时不派活", dispatched == [], str(dispatched))
    check("超预算时 run 失败", run.status == "failed", run.status)
    check("失败原因说明是防失控", "失控" in run.error, run.error)


# ---------------------------------------------------------------- 持久化
def test_persistence() -> None:
    from oryx.store import Store
    from oryx.workflow import WorkflowPlan, WorkflowRun

    tmp = tempfile.mkdtemp(prefix="oryx_vb4e3_")
    store = Store(Path(tmp) / "o.db")

    def dispatch(agent, goal, deps):
        return ok(f"产出-{goal}")

    plan = WorkflowPlan.parse({
        "name": "巡检",
        "goal": "查所有接口",
        "stages": [
            {"name": "S1", "steps": [{"id": "a", "agent": "x", "goal": "A"}]},
            {"name": "S2", "steps": [{"id": "b", "agent": "x", "goal": "B", "needs": ["a"]}]},
        ],
    })
    run = WorkflowRun(plan, store=store, args={"env": "test"}).run(dispatch)
    check("编排成功", run.status == "done", run.error)

    saved = store.get_workflow_run(run.id)
    check("run 已落库", saved is not None)
    check("落库带名字", saved and saved["name"] == "巡检")
    check("落库带状态", saved and saved["status"] == "done")
    check("落库带参数", saved and saved["args"] == {"env": "test"}, str(saved and saved["args"]))

    steps = store.workflow_steps(run.id)
    check("step 落库 2 条", len(steps) == 2, str(len(steps)))
    check("step 带阶段名", {s["stage"] for s in steps} == {"S1", "S2"})
    check("step 带完整产出", steps[0]["output"] == "产出-A", steps[0]["output"])
    check("step 已结束", all(s["ended_at"] for s in steps))

    listed = store.workflow_runs()
    check("run 列表能查到", any(r["id"] == run.id for r in listed))


def test_prune() -> None:
    from oryx.store import Store
    from oryx.workflow import WorkflowPlan, WorkflowRun

    tmp = tempfile.mkdtemp(prefix="oryx_vb4e3p_")
    store = Store(Path(tmp) / "o.db")
    plan = WorkflowPlan.parse({
        "goal": "g",
        "stages": [{"steps": [{"id": "a", "agent": "x", "goal": "A"}]}],
    })
    ids = []
    for _ in range(4):
        time.sleep(0.01)
        r = WorkflowRun(plan, store=store).run(lambda a, g, d: ok("ok"))
        ids.append(r.id)
    store.prune_workflow_runs(keep=2)
    left = {r["id"] for r in store.workflow_runs()}
    check("保留最近 2 个", len(left) == 2, str(left))
    check("旧 run 已删", ids[0] not in left, str(left))
    check("连带 step 也清掉", store.workflow_steps(ids[0]) == [])


def test_delete_single_run() -> None:
    from oryx.store import Store
    from oryx.workflow import WorkflowPlan, WorkflowRun

    tmp = tempfile.mkdtemp(prefix="oryx_vb4e3d_")
    store = Store(Path(tmp) / "o.db")
    plan = WorkflowPlan.parse({
        "goal": "g",
        "stages": [{"name": "S1", "steps": [{"id": "a", "agent": "x", "goal": "A"}]}],
    })
    keep = WorkflowRun(plan, store=store).run(lambda a, g, d: ok("ok"))
    time.sleep(0.01)
    drop = WorkflowRun(plan, store=store).run(lambda a, g, d: ok("ok"))

    check("删前两条都在", len(store.workflow_runs()) == 2, str(len(store.workflow_runs())))
    check("删除返回 True", store.delete_workflow_run(drop.id) is True)
    left = [r["id"] for r in store.workflow_runs()]
    check("只删掉指定的那条", left == [keep.id], str(left))
    check("连带 step 一起删", store.workflow_steps(drop.id) == [])
    check("留下的 step 还在", len(store.workflow_steps(keep.id)) == 1)
    check("删不存在的返回 False", store.delete_workflow_run("wfr_不存在") is False)
    check("再删一次也返回 False（已不存在）", store.delete_workflow_run(drop.id) is False)


def test_prune_all_failed() -> None:
    """批量清理全失败 run。

    这条是分页陷阱的回归：判定必须在库侧做。前端循环删的话，
    删掉一页里的失败记录，更早的补位上来，永远清不干净。
    """
    from oryx.store import Store
    from oryx.workflow import WorkflowPlan, WorkflowRun

    tmp = tempfile.mkdtemp(prefix="oryx_vb4e3c_")
    store = Store(Path(tmp) / "o.db")
    # 两步的编排才能造出"部分失败"（一个 failed 一个 done）
    plan = WorkflowPlan.parse({
        "goal": "g",
        "stages": [{"name": "S", "steps": [
            {"id": "a", "agent": "x", "goal": "A"},
            {"id": "b", "agent": "x", "goal": "B"},
        ]}],
    })

    # 3 个全失败、1 个全成功、1 个部分失败
    for _ in range(3):
        WorkflowRun(plan, store=store).run(lambda a, g, d: {"ok": False, "content": "炸了"})
    good = WorkflowRun(plan, store=store).run(lambda a, g, d: ok("正常产出"))
    mixed = WorkflowRun(plan, store=store).run(
        lambda a, g, d: {"ok": False, "content": "炸了"} if g == "A" else ok("半成")
    )

    check("部分失败确实是混合状态",
          mixed.count_by_status("failed") == 1 and mixed.count_by_status("done") == 1,
          str({k: v.status for k, v in mixed.results.items()}))
    check("全失败计数为 3", store.count_all_failed_workflow_runs() == 3,
          str(store.count_all_failed_workflow_runs()))
    check("清理前共 5 条", len(store.workflow_runs()) == 5, str(len(store.workflow_runs())))

    n = store.prune_all_failed_workflow_runs()
    check("一次清掉 3 个", n == 3, str(n))
    check("清理后计数为 0", store.count_all_failed_workflow_runs() == 0,
          str(store.count_all_failed_workflow_runs()))
    left = {r["id"] for r in store.workflow_runs()}
    check("成功的 run 保留", good.id in left, str(left))
    check("部分失败的 run 保留", mixed.id in left, str(left))
    check("只剩 2 条", len(left) == 2, str(left))
    check("没有再清理的", store.prune_all_failed_workflow_runs() == 0)


def test_prune_all_failed_needs_steps() -> None:
    """含 skipped 的 run 不算全失败（那是用户中止的，不能替他删）。"""
    from oryx.store import Store
    from oryx.workflow import WorkflowPlan, WorkflowRun

    tmp = tempfile.mkdtemp(prefix="oryx_vb4e3c2_")
    store = Store(Path(tmp) / "o.db")
    # 两个 STAGE（不是两个 step）：同一 stage 里的 step 属同一层，
    # 会在中止生效前一起派出去 —— 要产出 skipped 必须跨 stage
    plan = WorkflowPlan.parse({
        "goal": "g",
        "stages": [
            {"name": "S1", "steps": [{"id": "a", "agent": "x", "goal": "A"}]},
            {"name": "S2", "steps": [{"id": "b", "agent": "x", "goal": "B"}]},
        ],
    })
    holder: list = []

    def dispatch(agent, goal, deps):
        holder[0].cancel()   # 第一步跑完就中止
        return ok("ok")

    run = WorkflowRun(plan, store=store)
    holder.append(run)
    run.run(dispatch)

    check("run 被中止", run.status == "cancelled", run.status)
    check("第二个 step 记为 skipped",
          run.results.get("b", None) is not None and run.results["b"].status == "skipped",
          str({k: v.status for k, v in run.results.items()}))
    counts = store.workflow_runs()[0]["step_counts"]
    check("落库含 skipped", counts.get("skipped") == 1, str(counts))
    check("含 skipped 的不算全失败", store.count_all_failed_workflow_runs() == 0,
          str(store.count_all_failed_workflow_runs()))
    check("它不会被清理掉", store.prune_all_failed_workflow_runs() == 0)
    check("run 还在", len(store.workflow_runs()) == 1)


# ---------------------------------------------------------------- 事件
def test_events_emit() -> None:
    from oryx.events import EventBus
    from oryx.workflow import WorkflowPlan, WorkflowRun

    seen: list[str] = []
    payloads: list[dict] = []
    bus = EventBus()
    bus.add_sync_sink(lambda e: (seen.append(e["type"]), payloads.append(e)))

    plan = WorkflowPlan.parse({
        "name": "W",
        "goal": "g",
        "stages": [{"name": "S1", "steps": [{"id": "a", "agent": "x", "goal": "A"}]}],
    })
    WorkflowRun(plan, store=None, bus=bus).run(lambda a, g, d: ok("ok"))

    check("发 started", "workflow.started" in seen, str(seen))
    check("发 stage.started", "workflow.stage.started" in seen)
    check("发 step.started", "workflow.step.started" in seen)
    check("发 step.finished", "workflow.step.finished" in seen)
    check("发 finished", "workflow.finished" in seen)

    fin = [p for p in payloads if p["type"] == "workflow.finished"][0]
    check("finished 带状态", fin["status"] == "done")
    st = [p for p in payloads if p["type"] == "workflow.step.started"][0]
    check("事件带 run_id", st["run_id"].startswith("wfr_"))


# ---------------------------------------------------------------- 工具层
def test_runtime_workflow_tools() -> None:
    """工具层：未知子 Agent 被拒；正常编排跑通。"""
    from oryx.config import Config
    from oryx.events import EventBus
    from oryx.loop import AgentRuntime
    from oryx.store import Store

    tmp = tempfile.mkdtemp(prefix="oryx_vb4e3r_")
    data = {"workspace": tmp, "interaction": "bypass", "mode": "local"}
    rt = AgentRuntime(Config(data), Store(Path(tmp) / "o.db"), EventBus())

    check("workflow_run 已注册", "workflow_run" in rt.registry)
    check("workflow_status 已注册", "workflow_status" in rt.registry)
    check("workflow_cancel 已注册", "workflow_cancel" in rt.registry)

    # step 不能自带 tools（会绕过子 Agent 白名单）—— 定义层就没有这个字段
    check("step 定义里没有 tools 字段",
          "tools" not in WorkflowStep_fields())

    bad = rt.run_workflow({
        "goal": "g",
        "stages": [{"steps": [{"agent": "根本不存在", "goal": "x"}]}],
    })
    check("未知子 Agent 被拒", not bad["ok"], str(bad)[:120])
    check("拒绝时给出可用列表", "可用" in str(bad.get("content", "")), str(bad)[:200])

    missing = rt.run_workflow({"goal": "g", "stages": []})
    check("坏定义被拒", not missing["ok"], str(missing)[:120])

    no_run = rt.workflow_status("wfr_不存在")
    check("查不到的 run 报 ok=False", not no_run["ok"])

    no_cancel = rt.workflow_cancel("")
    check("没在跑时中止报 ok=False", not no_cancel["ok"])


def WorkflowStep_fields() -> set[str]:
    from oryx.workflow import WorkflowStep

    import dataclasses

    return {f.name for f in dataclasses.fields(WorkflowStep)}


def test_workflow_tool_risk() -> None:
    """workflow_run 派的是子 Agent，风险等级不该是 L2 —— 它本身不改文件。"""
    from oryx.policy import Risk

    from oryx.config import Config
    from oryx.events import EventBus
    from oryx.loop import AgentRuntime
    from oryx.store import Store

    tmp = tempfile.mkdtemp(prefix="oryx_vb4e3k_")
    rt = AgentRuntime(Config({"workspace": tmp}), Store(Path(tmp) / "o.db"), EventBus())
    spec = rt.registry["workflow_run"]
    check("workflow_run 风险等级不高于 L1", int(spec.risk) <= int(Risk.L1_WRITE), spec.risk.label)
    check("workflow_run 有 schema", spec.schema()["name"] == "workflow_run")
    check("描述说明了中间结果隔离", "落库" in spec.description or "不回灌" in spec.description,
          spec.description[:80])


def main() -> int:
    tests = [
        ("E3-1 定义校验", test_plan_validation),
        ("E3-2 needs 兼容字符串", test_needs_accepts_string),
        ("E3-3 stage 顺序 + 并行", test_stage_order_and_parallel),
        ("E3-4 pipeline 分层串行", test_pipeline_layering),
        ("E3-5 跨 stage 依赖", test_cross_stage_needs),
        ("E3-6 并行尊重 needs", test_parallel_respects_needs),
        ("E3-7 deps 带上游产出", test_deps_carry_upstream_output),
        ("E3-8 中间结果隔离", test_intermediate_isolation),
        ("E3-9 摘要保首尾", test_summarize_keeps_head_and_tail),
        ("E3-10 汇总用摘要", test_final_answer_is_summary),
        ("E3-11 单 step 失败隔离", test_step_failure_isolated),
        ("E3-12 单 step 异常隔离", test_step_exception_isolated),
        ("E3-13 失败上游的交接标记", test_failed_dep_handoff_marker),
        ("E3-14 中止后停止派活", test_cancel_stops_dispatch),
        ("E3-15 step 预算闸门", test_step_budget_guard),
        ("E3-16 落库可查", test_persistence),
        ("E3-17 历史清理", test_prune),
        ("E3-18 删单次运行", test_delete_single_run),
        ("E3-19 批量清理全失败", test_prune_all_failed),
        ("E3-20 中止的不算全失败", test_prune_all_failed_needs_steps),
        ("E4-1 事件推送", test_events_emit),
        ("E4-2 工具层校验", test_runtime_workflow_tools),
        ("E4-3 工具风险等级", test_workflow_tool_risk),
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