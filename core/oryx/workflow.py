"""工作流编排引擎：把"计划"从模型上下文挪到脚本里。

对齐 WorkBuddy 的 Dynamic Workflows（`agent()` / `parallel()` / `pipeline()`
/ `phase()`），但**不引入 JS 运行时**。差别与理由：

    WorkBuddy 跑的是一段 JavaScript，宿主注入 agent/parallel/pipeline/phase。
    Oryx 不跑 JS —— 在本地进程里 eval 一段模型写的脚本，等于把 RCE 敞口
    直接打开，而 Oryx 的权限模型（第一批 B3/B4）正是围着这个敞口建的。
    所以编排脚本用**声明式数据**表达（stages + steps），语义等价，
    但每一行都要过白名单校验，没有"任意代码"这条路。

为什么需要它（这是第四批要解决的短板）：

    子 Agent / 团队协作的编排者是**主模型**——它每一轮都要决定下一步派谁、
    做什么，所有中间结果都落进上下文窗口。任务一大就装不下，
    而且"编排在哪儿"没有记录，没法重跑。

    工作流把循环、分支、中间结果交给引擎管理，模型上下文里只剩最终答案。
    编排过程本身落库，可读、可 diff、可重跑。

三条不可协商的约束（对齐 WorkBuddy 的运行时限制）：
    1. **中间结果隔离**：step 的完整产出只落 SQLite，不回灌对话上下文。
       模型要引用中间结论时只能拿到截断后的摘要 —— 这是省上下文的根本手段。
    2. **并发上限 16**（CPU 核少时更少）：限制本地资源消耗。
    3. **单 run 上限 1000 个 step**：防脚本写错时空转烧 token。
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable

from .tools import MUTATING_TOOLS

# 单 run 最多派多少个子 Agent（对齐 WorkBuddy）
MAX_STEPS_PER_RUN = 1000
# 同时最多跑多少个子 Agent（对齐 WorkBuddy 的 16）
MAX_CONCURRENCY = 16
# step 产出回灌给模型时的截断长度。
# 完整产出在 workflow_steps.output 里，随时能查；模型上下文里只留摘要。
STEP_SUMMARY_CHARS = 4000
# 阶段名长度上限（事件与展示用）
MAX_STAGE_NAME = 60


def _stage_limit() -> int:
    """并发上限按 CPU 核数收敛：核少的机器上开 16 个只是互相抢时间片。"""
    import os

    return max(2, min(MAX_CONCURRENCY, (os.cpu_count() or 4)))


class WorkflowError(Exception):
    """工作流定义或执行出错。"""


# ---------------------------------------------------------------- 定义


@dataclass
class WorkflowStep:
    """一个 step = 一次子 Agent 调用。

    为什么 step 固定绑一个 agent：子 Agent 的工具白名单是它身份的一部分
    （第一批 B5 / C12 的硬约束）。让 step 自己声明 tools 会绕过这条约束，
    所以这里只认 agents 目录里已定义的子 Agent。
    """

    id: str = ""
    name: str = ""
    agent: str = ""
    goal: str = ""
    # 依赖哪些 step 的产出（step id）。用于 pipeline 的顺序串联。
    needs: list[str] = field(default_factory=list)
    # 本 step 是否并行执行。同一 stage 里的并行 step 一起跑。
    parallel: bool = False

    @staticmethod
    def parse(raw: dict[str, Any]) -> WorkflowStep:
        if not isinstance(raw, dict):
            raise WorkflowError(f"step 必须是对象，收到 {type(raw).__name__}")
        agent = str(raw.get("agent") or "").strip()
        if not agent:
            raise WorkflowError("step 缺少 agent（要指明用哪个子 Agent）")
        goal = str(raw.get("goal") or "").strip()
        if not goal:
            raise WorkflowError(f"step {raw.get('name') or agent} 缺少 goal")
        needs = raw.get("needs") or []
        if isinstance(needs, str):
            needs = [needs]
        if not isinstance(needs, list):
            raise WorkflowError("step.needs 必须是 id 列表")
        return WorkflowStep(
            id=str(raw.get("id") or "").strip(),
            name=str(raw.get("name") or agent).strip(),
            agent=agent,
            goal=goal,
            needs=[str(n).strip() for n in needs if str(n).strip()],
            parallel=bool(raw.get("parallel", False)),
        )


@dataclass
class WorkflowStage:
    """一个 stage = 进度视图里的一行。

    对齐 WorkBuddy 的 phase()：编排按阶段推进，进度按阶段聚合显示。
    """

    name: str
    steps: list[WorkflowStep] = field(default_factory=list)

    @staticmethod
    def parse(raw: dict[str, Any], index: int) -> WorkflowStage:
        name = str(raw.get("name") or f"阶段{index + 1}").strip()[:MAX_STAGE_NAME]
        raw_steps = raw.get("steps") or []
        if not isinstance(raw_steps, list) or not raw_steps:
            raise WorkflowError(f"阶段「{name}」没有任何 step")
        return WorkflowStage(
            name=name,
            steps=[WorkflowStep.parse(s) for s in raw_steps],
        )


@dataclass
class WorkflowPlan:
    """一份编排。stage 之间顺序执行，stage 内的 step 视 parallel 而定。"""

    name: str
    goal: str
    stages: list[WorkflowStage] = field(default_factory=list)

    @property
    def total_steps(self) -> int:
        return sum(len(s.steps) for s in self.stages)

    def to_dict(self) -> dict[str, Any]:
        """导回可 JSON 序列化的定义。

        字段名必须与 parse() 的入参**完全对称** —— 这是最容易犯的错：
        存进去的 JSON 自己解析不回来，"从 run 存成模板"就变成一个空模板。
        所以解析后立刻 to_dict、再 parse 回来，两边必须完全一致。
        """
        return {
            "name": self.name,
            "goal": self.goal,
            "stages": [
                {
                    "name": s.name,
                    "steps": [
                        {
                            "id": st.id,
                            "name": st.name,
                            "agent": st.agent,
                            "goal": st.goal,
                            "needs": list(st.needs),
                            "parallel": st.parallel,
                        }
                        for st in s.steps
                    ],
                }
                for s in self.stages
            ],
        }

    @staticmethod
    def parse(raw: dict[str, Any]) -> WorkflowPlan:
        if not isinstance(raw, dict):
            raise WorkflowError(f"工作流定义必须是对象，收到 {type(raw).__name__}")
        name = str(raw.get("name") or "workflow").strip()[:MAX_STAGE_NAME]
        goal = str(raw.get("goal") or "").strip()
        if not goal:
            raise WorkflowError("工作流缺少 goal")
        raw_stages = raw.get("stages") or []
        if not isinstance(raw_stages, list) or not raw_stages:
            raise WorkflowError("工作流没有任何 stage")
        plan = WorkflowPlan(
            name=name,
            goal=goal,
            stages=[WorkflowStage.parse(s, i) for i, s in enumerate(raw_stages)],
        )
        # step id 没写就按位置生成 —— needs 引用它
        counter = 0
        for stage in plan.stages:
            for step in stage.steps:
                if not step.id:
                    step.id = f"s{counter}"
                counter += 1
        if plan.total_steps > MAX_STEPS_PER_RUN:
            raise WorkflowError(
                f"这份工作流有 {plan.total_steps} 个 step，超过单次上限 {MAX_STEPS_PER_RUN}"
            )
        known = {s.id for stage in plan.stages for s in stage.steps}
        for stage in plan.stages:
            for step in stage.steps:
                missing = [n for n in step.needs if n not in known]
                if missing:
                    raise WorkflowError(
                        f"step「{step.name}」依赖了不存在的 step：{'、'.join(missing)}"
                    )
        return plan


# ---------------------------------------------------------------- 运行态


@dataclass
class StepResult:
    step_id: str
    name: str
    agent: str
    status: str  # done / failed / skipped
    output: str = ""
    error: str = ""
    duration_ms: int = 0
    # 给下游 / 给模型的摘要（截断后）
    summary: str = ""
    # 回灌给模型的产出。中间隔离的关键：这是截断版，不是完整版。
    handoff: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "name": self.name,
            "agent": self.agent,
            "status": self.status,
            "output": self.output,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "summary": self.summary,
        }


class WorkflowRun:
    """一次编排的执行。

    生命周期：created → running → done / failed / cancelled。
    引擎不持有模型上下文 —— 它只负责"派谁、拿什么回来、给谁"，
    最终答案由调用方（主循环）决定要不要落进对话。
    """

    def __init__(
        self,
        plan: WorkflowPlan,
        store: Any,
        bus: Any = None,
        run_id: str | None = None,
        args: dict[str, Any] | None = None,
    ) -> None:
        self.plan = plan
        self.store = store
        self.bus = bus
        self.id = run_id or f"wfr_{uuid.uuid4().hex[:12]}"
        self.args = args or {}
        self.status = "created"
        self.error = ""
        self.created_at = time.time()
        self.ended_at: float | None = None
        self.results: dict[str, StepResult] = {}
        self._stages: list[str] = [s.name for s in plan.stages]
        self._current_stage = ""
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._dispatched = 0

    # ---------------------------------------------------------------- 落库

    def _persist_start(self) -> None:
        if self.store is None:
            return
        try:
            self.store.create_workflow_run(
                {
                    "id": self.id,
                    "name": self.plan.name,
                    "goal": self.plan.goal,
                    "status": "running",
                    "args": self.args,
                    "created_at": self.created_at,
                }
            )
        except Exception:
            pass
        # plan 本体单独存一份 —— "把这次运行存成模板"要靠它。
        # 之前只存了 name/goal/status/args，编排结构本身没落库，
        # 于是跑完的编排无法复用，只能让模型重新"临场发挥"一份。
        try:
            self.store.save_workflow_run_plan(self.id, self.plan.to_dict())
        except Exception:
            pass

    def _persist_end(self) -> None:
        if self.store is None:
            return
        try:
            self.store.finish_workflow_run(self.id, self.status, self.error)
        except Exception:
            pass

    # ---------------------------------------------------------------- 事件

    def _emit(self, kind: str, **payload: Any) -> None:
        """工作流事件。前端进度视图靠它们画阶段与 step。

        带 stage / step 名而**不带产出正文**：正文进上下文就白做了。
        """
        if self.bus is None:
            return
        try:
            self.bus.publish(kind, run_id=self.id, run_name=self.plan.name, **payload)
        except Exception:
            pass

    # ---------------------------------------------------------------- 控制

    def cancel(self) -> None:
        """请求中止。正在跑的不打断，跑完当前 step 就不再往下派。"""
        self._cancel.set()
        self._emit("workflow.cancelling")

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    # ---------------------------------------------------------------- 执行

    def run(self, dispatch: Callable[[str, str, dict[str, Any]], dict[str, Any]]) -> "WorkflowRun":
        """跑完整份编排。

        dispatch(agent, goal, deps) -> {"ok": bool, "content": str}
        由调用方注入（内核里接 run_subagent）。引擎自己不碰模型，
        所以它可以被纯单测覆盖 —— 不需要 mock 任何 provider。
        """
        self._persist_start()
        self.status = "running"
        self._emit("workflow.started", goal=self.plan.goal, stages=self._stages,
                   total_steps=self.plan.total_steps)
        try:
            for stage in self.plan.stages:
                if self.cancelled:
                    self.status = "cancelled"
                    break
                self._current_stage = stage.name
                self._emit("workflow.stage.started", stage=stage.name,
                           steps=len(stage.steps))
                self._run_stage(stage, dispatch)
                self._emit("workflow.stage.finished", stage=stage.name)
                if self.cancelled:
                    self.status = "cancelled"
                    break
            else:
                # 全部 step 都跑完了，但"跑完"不等于"成功"：
                # 单 step 失败是隔离的（不塌编排），所以编排本身会正常走完。
                # 这时要按结果定性 —— 全军覆没却报"完成"，用户会以为真做成了。
                self.status = "done" if self._any_step_done() else "failed"
                if self.status == "failed":
                    failed = self.count_by_status("failed")
                    self.error = (
                        f"{failed} 个 step 全部失败"
                        f"（共 {len(self.results)} 个）"
                        if failed == len(self.results)
                        else f"{failed} 个 step 失败，共 {len(self.results)} 个"
                    )
            if self.status == "cancelled":
                # 中止后把没跑的 step 记成 skipped ——
                # 报告里得看得见"哪些没跑"，否则用户没法判断能不能用已出的结果
                self._skip_remaining()
        except WorkflowError as exc:
            self.status = "failed"
            self.error = str(exc)
        except Exception as exc:  # noqa: BLE001 — run 整体兜底，别让线程静默死掉
            self.status = "failed"
            self.error = f"{type(exc).__name__}: {exc}"
        self.ended_at = time.time()
        self._persist_end()
        self._emit("workflow.finished", status=self.status, error=self.error,
                   duration_ms=int((self.ended_at - self.created_at) * 1000))
        return self

    def _run_stage(self, stage: WorkflowStage, dispatch) -> None:
        """跑一个 stage。

        pipeline（needs 串联）在 stage 内按依赖顺序分层：同一层的并行跑，
        上一层全部结束后才发下一层。这样"顺序串联"和"并行 fan-out"
        用同一套机制，不额外开一条代码路径。

        done 是**跨 stage 累积**的：step 可以依赖上一阶段产出的 step
        （"先各自调研，再交叉汇总"就是两阶段）。所以它从已有结果初始化，
        不是每个 stage 从空集开始。

        但只算**真正跑过**的 step —— 被中止的 step 也进了 results，
        拿它当依赖会让下游去引用一段"未执行"。
        """
        with self._lock:
            done = {
                sid
                for sid, r in self.results.items()
                if r.status in ("done", "failed")
            }
        pending = list(stage.steps)

        while pending:
            if self.cancelled:
                return

            # 本层：所有 needs 都已完成的 step
            layer = [s for s in pending if all(n in done for n in s.needs)]
            if not layer:
                # 依赖成环 —— WorkflowPlan.parse 已挡过一道，这里是防御
                names = "、".join(s.name for s in pending)
                raise WorkflowError(f"阶段「{stage.name}」的依赖无法满足：{names}")

            self._reserve(layer)
            if len(layer) == 1 or not any(s.parallel for s in layer):
                for step in layer:
                    self._run_step(step, stage, dispatch)
                    done.add(step.id)
            else:
                self._run_parallel(layer, stage, dispatch)
                done.update(s.id for s in layer)

            ids = {s.id for s in layer}
            pending = [s for s in pending if s.id not in ids]

    def _run_parallel(self, steps: list[WorkflowStep], stage: WorkflowStage, dispatch) -> None:
        """并行跑一批 step，上限按 CPU 核数收敛。"""
        workers = min(len(steps), _stage_limit())
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="oryx-wf") as pool:
            futures = {
                pool.submit(self._run_step, step, stage, dispatch): step for step in steps
            }
            for fut in as_completed(futures):
                # _run_step 自己兜异常，这里只是不让 as_completed 抛出打断整批
                fut.exception()

    def _reserve(self, steps: list[WorkflowStep]) -> None:
        """占 step 配额。超上限就在派活前拦住，而不是烧完 token 才发现。"""
        with self._lock:
            self._dispatched += len(steps)
            over = self._dispatched > MAX_STEPS_PER_RUN
        if over:
            raise WorkflowError(
                f"本次运行已派出超过 {MAX_STEPS_PER_RUN} 个子 Agent，已中止（防失控循环）"
            )

    def _run_step(self, step: WorkflowStep, stage: WorkflowStage, dispatch) -> StepResult:
        # 上游产出交给 dispatch 自己拼 —— 引擎只管"派谁、给什么"，
        # 提示词怎么组织是调用方的事。这里再拼一遍就成了两份。
        deps = self._deps_for(step)
        step_db_id = f"{self.id}:{step.id}"
        if self.store is not None:
            try:
                self.store.add_workflow_step(
                    {
                        "id": step_db_id,
                        "run_id": self.id,
                        "stage": stage.name,
                        "name": step.name,
                        "agent": step.agent,
                        "goal": step.goal,
                        "status": "running",
                        "created_at": time.time(),
                    }
                )
            except Exception:
                pass
        self._emit("workflow.step.started", stage=stage.name, step_id=step.id,
                   step=step.name, agent=step.agent)

        started = time.monotonic()
        try:
            result = dispatch(step.agent, step.goal, deps)
            ok = bool(result.get("ok"))
            text = result.get("content")
            output = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)
            error = "" if ok else output
        except Exception as exc:  # noqa: BLE001 — 单个 step 失败不该带塌整份编排
            ok = False
            output = ""
            error = f"{type(exc).__name__}: {exc}"

        duration_ms = int((time.monotonic() - started) * 1000)
        summary = _summarize(output)
        entry = StepResult(
            step_id=step.id,
            name=step.name,
            agent=step.agent,
            status="done" if ok else "failed",
            output=output,
            error=error,
            duration_ms=duration_ms,
            summary=summary,
            handoff=summary if ok else f"[失败] {error[:600]}",
        )
        with self._lock:
            self.results[step.id] = entry

        if self.store is not None:
            try:
                self.store.finish_workflow_step(
                    step_db_id,
                    entry.status,
                    output=output,
                    error=error,
                    duration_ms=duration_ms,
                )
            except Exception:
                pass
        self._emit(
            "workflow.step.finished",
            stage=stage.name,
            step_id=step.id,
            step=step.name,
            agent=step.agent,
            status=entry.status,
            duration_ms=duration_ms,
            # 摘要而非全文：正文进 SSE 就等于进前端内存，进模型上下文也一样
            summary=summary[:STEP_SUMMARY_CHARS],
            error=error[:600],
        )
        return entry

    # ---------------------------------------------------------------- 结果统计

    def count_by_status(self, status: str) -> int:
        """某种状态的 step 数。调用方（runtime / UI）据此判断部分失败。"""
        with self._lock:
            return sum(1 for r in self.results.values() if r.status == status)

    def _any_step_done(self) -> bool:
        """有没有 step 真的产出了东西。

        编排"跑完"不等于"成功"：单 step 失败是隔离的，所以编排会正常走完。
        全军覆没还报 done，用户会以为真做成了 —— 这里给出判据。
        """
        return self.count_by_status("done") > 0

    def _skip_remaining(self) -> None:
        """把还没跑的 step 全记成 skipped。

        中止是用户的动作，但"哪些没跑"必须留在报告里 ——
        只说一句"已中止"，用户没法判断该不该重跑、哪部分结果还能用。
        """
        for stage in self.plan.stages:
            for step in stage.steps:
                with self._lock:
                    already = step.id in self.results
                if not already:
                    self._current_stage = stage.name
                    self._record_skip(step, "已中止")

    def _record_skip(self, step: WorkflowStep, reason: str) -> None:
        entry = StepResult(
            step_id=step.id,
            name=step.name,
            agent=step.agent,
            status="skipped",
            error=reason,
            handoff=f"[未执行] {reason}",
        )
        with self._lock:
            self.results[step.id] = entry
        step_db_id = f"{self.id}:{step.id}"
        if self.store is not None:
            try:
                self.store.add_workflow_step(
                    {
                        "id": step_db_id,
                        "run_id": self.id,
                        "stage": self._current_stage,
                        "name": step.name,
                        "agent": step.agent,
                        "goal": step.goal,
                        "status": "skipped",
                        "error": reason,
                        "created_at": time.time(),
                    }
                )
            except Exception:
                pass
        self._emit("workflow.step.finished", stage=self._current_stage,
                   step_id=step.id, step=step.name, agent=step.agent,
                   status="skipped", error=reason)

    # ---------------------------------------------------------------- 中间结果

    def _deps_for(self, step: WorkflowStep) -> dict[str, str]:
        """上游 step 的产出摘要。

        这就是"中间结果隔离"的落点：给下游的是 handoff（截断摘要），
        不是 output（全文）。完整产出留在 SQLite，需要时按 step_id 查。

        上游失败时下游照样能跑，但要看得见上游失败了 —— 直接给一句
        "[失败] 原因"，比让下游拿到一段空字符串去猜要好。
        """
        with self._lock:
            out: dict[str, str] = {}
            for need in step.needs:
                prev = self.results.get(need)
                if prev is None:
                    out[need] = "[未执行]"
                else:
                    out[need] = prev.handoff or f"[{prev.status}]"
            return out

    # ---------------------------------------------------------------- 汇总

    def final_answer(self) -> str:
        """把所有 step 的产出摘要拼成一份报告。

        这是唯一允许回到主模型上下文的内容 —— 注意是**摘要**，
        不是每个 step 的全文。
        """
        lines = [f"# 工作流「{self.plan.name}」产出\n"]
        # 部分失败要在报告最前面讲清楚，否则主模型会把一份"全军覆没"的
        # 报告当成正常结果继续往下推理
        failed = self.count_by_status("failed")
        skipped = self.count_by_status("skipped")
        if failed:
            lines.append(
                f"\n> ⚠ {failed} 个 step 失败"
                + (f"、{skipped} 个未执行" if skipped else "")
                + " —— 下面只列实际产出的部分，缺失处请自行判断是否需要补跑。\n"
            )
        for stage in self.plan.stages:
            items = [
                r for r in (self.results.get(s.id) for s in stage.steps) if r is not None
            ]
            if not items:
                continue
            lines.append(f"\n## {stage.name}")
            for r in items:
                mark = "✓" if r.status == "done" else ("!" if r.status == "failed" else "-")
                lines.append(f"\n### {mark} {r.name}（{r.agent}，{r.duration_ms}ms）")
                lines.append(r.summary or r.error or "（无产出）")
        return "\n".join(lines).strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.plan.name,
            "goal": self.plan.goal,
            "status": self.status,
            "error": self.error,
            "created_at": self.created_at,
            "ended_at": self.ended_at,
            "stages": self._stages,
            "total_steps": self.plan.total_steps,
            "steps": [r.to_dict() for r in self.results.values()],
        }


def _summarize(text: str, limit: int = STEP_SUMMARY_CHARS) -> str:
    """把 step 产出压成摘要。

    截断时**从中间省略**，保留首尾：结论通常在开头，细节在结尾，
    中间的过程描述对下游价值最低。

    省略标记本身也计入 limit —— 上限是给下游 prompt 的硬预算，
    标记把实际长度顶出去的话，超出的部分又漏回上下文了。
    """
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    # 先给标记留位置，再把剩下的对半分
    marker_budget = 40
    room = max(limit - marker_budget, limit // 2)
    head = room * 2 // 3
    tail = room - head
    dropped = len(text) - head - tail
    return f"{text[:head]}\n…（中间省略 {dropped} 字符）…\n{text[-tail:]}"


# ---------------------------------------------------------------- 只读分级


def plan_read_only(plan: dict[str, Any], agent_tools: dict[str, list[str]]) -> bool:
    """这份编排是不是只读的。

    判据：所有 step 用到的子 Agent，其工具白名单里**没有会改动东西的工具**。

    为什么从子 Agent 的白名单反推，而不是看 step 干了什么：
    子 Agent 的白名单是它身份的硬边界（第一批 B5 / C12）——
    step 声明「我要写文件」是没用的，agent 没那个工具就写不了。
    所以白名单是唯一可信的判据。

    ``agent_tools`` 是 {agent_id: [工具名]}。查不到的 agent 按**不可知即不安全**处理
    —— 判成只读会漏掉一个有写权限的 agent，那比误判成可写更糟。

    抄 Marvis 的 ``runnable`` 标记 —— 但语义更严：
    Marvis 是模板自己声明「我能不能直跑」，
    Oryx 是**从权限算出来**，用户改不了这个结论。
    """
    for stage in plan.get("stages") or []:
        for step in stage.get("steps") or []:
            agent_id = str(step.get("agent") or "")
            tools = agent_tools.get(agent_id)
            if tools is None:
                return False
            for tool in tools:
                if tool in MUTATING_TOOLS:
                    return False
    return True


def write_new_report(path: str, text: str) -> str:
    """把报告写到**新文件**。目标已存在时拒绝覆盖。

    抄 Marvis 的 ``_write_new_report``。三条规矩：
    1. 路径不能带空字符与换行（换行能骗过日志，路径校验要挡）；
    2. 父目录必须存在（不替用户建目录 —— 建了也是猜的）；
    3. 用 ``open(dest, "x")`` **独占创建** —— 已存在就直接失败。

    第 3 条是关键：工作流报告是有编号的产出（run_xxx.md），
    覆盖掉上一份 = 用户再也找不到上次那份了。宁可失败。
    """
    if (
        not isinstance(path, str)
        or not path.strip()
        or "\x00" in path
        or "\n" in path
        or "\r" in path
    ):
        raise ValueError("报告路径无效")
    dest = os.path.abspath(os.path.expanduser(path))
    parent = os.path.dirname(dest)
    if not parent or not os.path.isdir(parent):
        raise ValueError(f"报告目录不存在: {parent}")
    if os.path.isdir(dest):
        raise ValueError("报告路径是目录")
    if os.path.exists(dest):
        raise ValueError(f"报告文件已存在，未覆盖: {dest}")
    payload = text if text.endswith("\n") else text + "\n"
    with open(dest, "x", encoding="utf-8") as handle:
        handle.write(payload)
    return dest