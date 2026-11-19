"""定时任务：增删查改、启停、执行记录。

（从 `loop.py` 原样搬来 —— `self.` 一个字都没改，语义零变动。）
"""

from __future__ import annotations

from typing import Any
from ..policy import PolicyEngine, Risk
from ..tools import ToolSpec, build_registry, tool_result_text
import time


class AutomationToolsMixin:
    def _register_automation_tools(self) -> None:
        from ..automation import AutomationScheduler, describe

        def _fire(item: Any) -> None:
            self.bus.publish(
                "automation.fired", id=item.id, name=item.name, goal=item.goal
            )
            # 执行记录：先写一条 running 的行。
            # 这样**任务卡死或进程被杀**时也能看到「那次没跑完」。
            run_id = f"arun_{item.id}_{int(time.time() * 1000)}"
            started = time.time()
            try:
                self.store.start_automation_run(
                    run_id, item.id, item.name, item.goal, started
                )
            except Exception:
                pass
            try:
                text = self.run(item.goal, interaction="craft")
                self.bus.publish(
                    "automation.done", id=item.id, name=item.name, text=text
                )
                try:
                    self.store.finish_automation_run(
                        run_id, "done", output=text, task_id=self._active_task_id or ""
                    )
                except Exception:
                    pass
            except Exception as exc:
                self.bus.publish("automation.failed", id=item.id, error=str(exc))
                try:
                    self.store.finish_automation_run(
                        run_id, "failed", error=f"{type(exc).__name__}: {exc}"
                    )
                except Exception:
                    pass
                # 失败要**看得见** —— 用户不打开界面就完全错过。
                # 走通知渠道：系统通知或 webhook。
                self._notify_automation_failure(item, exc)

        def _on_error(item: Any, error: str) -> None:
            """调度器层的失败回调：事件 + **系统级通知**。

            以前这条兜底路径只发 automation.failed 事件 —— 用户不打开界面
            就完全收不到，等于「失败通知依旧静默」。两条路径共用带去重的
            通知函数，不会出现重复轰炸。
            """
            self.bus.publish("automation.failed", id=item.id, error=error)
            self._notify_automation_failure(item, error)

        self.scheduler = AutomationScheduler(
            _fire,
            store=self.store,
            # 「忙」用 _run_in_flight，不用 _active_task_id ——
            # 后者是「当前/最近一个任务」的句柄，跑完**从不清**，
            # 拿它当忙碌标志的后果是定时任务永远排不上队。
            is_busy=self.is_busy,
            on_error=_on_error,
        )
        # 待命内核不启动定时任务：它还没被激活，不该抢跑用户的定时任务。
        # 激活时再补启动（见 AgentDaemon.activate）。
        if self._autostart_scheduler:
            self.scheduler.start()

        def _add(name: str, cron: str, goal: str) -> dict[str, Any]:
            try:
                item = self.scheduler.add(name, cron, goal)
            except ValueError as exc:
                return {"ok": False, "content": str(exc)}
            return {"ok": True, "content": item.to_dict()}

        def _list() -> dict[str, Any]:
            return {"ok": True, "content": self.scheduler.list()}

        def _remove(id: str) -> dict[str, Any]:
            removed = self.scheduler.remove(id)
            return {"ok": removed, "content": "已删除" if removed else "没有这个任务"}

        def _toggle(id: str, enabled: bool) -> dict[str, Any]:
            item = self.scheduler.toggle(id, enabled)
            if item is None:
                return {"ok": False, "content": f"没有这个任务：{id}"}
            return {"ok": True, "content": item.to_dict()}

        def _describe(cron: str) -> dict[str, Any]:
            try:
                return {"ok": True, "content": describe(cron)}
            except ValueError as exc:
                return {"ok": False, "content": str(exc)}

        self.registry["automation_add"] = ToolSpec(
            name="automation_add",
            description=(
                "创建一个定时任务。cron 是 5 段表达式（分 时 日 月 周），"
                "支持 *、a-b、a,b、*/n，也支持 @daily 这类别名。"
                "任务到点会自动执行 goal 里的指令。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "任务名"},
                    "cron": {"type": "string", "description": "cron 表达式，如 0 9 * * 1-5"},
                    "goal": {"type": "string", "description": "到点执行什么"},
                },
                "required": ["cron", "goal"],
            },
            risk=Risk.L1_WRITE,
            fn=_add,
        )
        self.registry["automation_list"] = ToolSpec(
            name="automation_list",
            description="列出所有定时任务及其上次执行情况。",
            parameters={"type": "object", "properties": {}, "required": []},
            risk=Risk.L0_READ,
            fn=_list,
        )
        self.registry["automation_remove"] = ToolSpec(
            name="automation_remove",
            description="删除一个定时任务。",
            parameters={
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
            risk=Risk.L1_WRITE,
            fn=_remove,
        )
        self.registry["automation_toggle"] = ToolSpec(
            name="automation_toggle",
            description="启用或停用一个定时任务。",
            parameters={
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "enabled": {"type": "boolean"},
                },
                "required": ["id", "enabled"],
            },
            risk=Risk.L1_WRITE,
            fn=_toggle,
        )

        def _runs(id: str = "", limit: int = 20) -> dict[str, Any]:
            """查定时任务的执行记录。

            「上周三那次到底生成了什么、成没成」——以前查不到，
            automations 表只记了 last_run_at。
            """
            try:
                rows = self.store.automation_runs(id, limit=max(1, min(limit, 200)))
            except Exception as exc:
                return {"ok": False, "content": f"查执行记录失败：{exc}"}
            # 输出压一压：完整产出可能很长，只给摘要
            for row in rows:
                output = str(row.get("output") or "")
                if len(output) > 500:
                    row["output"] = output[:500] + f"…（共 {len(output)} 字符）"
            return {"ok": True, "content": rows, "meta": {"count": len(rows)}}

        self.registry["automation_runs"] = ToolSpec(
            name="automation_runs",
            description=(
                "查看定时任务的执行记录：每次跑的状态、产出摘要、耗时、失败原因。"
                "给 id 查某一个任务的历史，不给就查全部。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "定时任务 id，留空查全部"},
                    "limit": {"type": "integer", "description": "最多返回多少条"},
                },
                "required": [],
            },
            risk=Risk.L0_READ,
            fn=_runs,
        )
