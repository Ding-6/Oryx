"""Agent 内核独立进程。

对齐 Marvis 的 MarvisAgent.exe：把「会跑很久、容易崩、带内存态」的东西
从 HTTP 进程里剥出来，让 server 只做控制面。

搬进来的：AgentRuntime 整体（工具注册表、索引、终端、调度器、团队、MCP 客户端、
子 Agent、策略引擎）。留在 server 的：SQLite（本来就WAL 多进程可共享）、
纯读的文件接口、静态资源。

事件出口是单向的：Agent 进程把 EventBus 的事件通过 IPC 推给 server，
由 server 统一进 SSE —— 前端完全不知道内核已经进程化了。
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .config import load_config
from .events import EventBus
from .ipc import IpcServer
from .loop import AgentRuntime
from .policy import PolicyEngine
from .store import Store


class AgentDaemon:
    """Agent 内核。所有对外的动作都经由 handle(method, params)。"""

    def __init__(self, config=None, store: Store | None = None, prewarm: bool = False):
        self.config = config or load_config()
        self.store = store or Store()
        self.bus = EventBus()
        # 待命内核：不启动定时任务，等 activate 再说
        self.runtime = AgentRuntime(
            self.config, self.store, self.bus, autostart_scheduler=not prewarm
        )
        self.server = IpcServer(self.methods(), on_event=self.bus.publish)
        # Agent 的事件要先攒一下再推：publish 可能在 Agent 工作线程里调用，
        # 直接 emit 会跨线程碰 socket
        self.bus.add_sync_sink(self.server.emit)
        self._busy = False
        self._prewarm = prewarm
        self._activated = not prewarm
        # 后台监视扩展文件的变化 —— 改了自动重载，不用手动点按钮。
        # 跟前端扩展一个思路（轮询 + 指纹比对），只是周期长一点：
        # 后端扩展是 Python 代码，重载有代价（模块重新执行、状态归零）。
        self._ext_watch = threading.Event()
        self._ext_watch_thread: threading.Thread | None = None

    # ---------------------------------------------------------------- 扩展监视

    def start_extension_watch(self, interval: float = 8.0) -> None:
        """启动扩展文件监视（幂等）。

        改了扩展的代码 → 自动重载。跟前端扩展一个思路（轮询 + 指纹比对），
        只是周期长一点：后端扩展是 Python 代码，重载有代价
        （模块重新执行、模块里的状态归零）。
        """
        if self._ext_watch_thread is not None:
            return
        # 关掉就停 —— 不想自动重载时用
        if os.environ.get("ORYX_EXT_AUTORELOAD", "1") == "0":
            return

        # 启动时清一次过期回收区条目。
        # 为什么在启动时而不是每轮监视里：清理是**不可逆**的，
        # 每 8 秒跑一次太频繁；而启动时清一次正好覆盖
        # 「关机很久之后回来，回收区已经瘦身」这个真实场景。
        self.cleanup_trash()

        def _watch() -> None:
            while not self._ext_watch.wait(interval):
                try:
                    res = self.runtime.reload_extensions()
                    if res.get("reloaded"):
                        print(f"[agentd] 扩展自动重载：{'、'.join(res['reloaded'])}",
                              flush=True)
                        # 重载后统一工具表变了 → 通知界面
                        self.bus.publish("extensions.reloaded",
                                         extensions=list(res["reloaded"]))
                except Exception as exc:  # noqa: BLE001 — 监视不该拖垮服务
                    print(f"[agentd] 扩展监视出错：{type(exc).__name__}: {exc}",
                          flush=True)

        self._ext_watch_thread = threading.Thread(
            target=_watch, name="oryx-ext-watch", daemon=True
        )
        self._ext_watch_thread.start()

    def cleanup_trash(self) -> list[str]:
        """清掉超过保留期的回收区条目。返回被清掉的 id 列表。"""
        try:
            res = self.catalog_extension_trash_cleanup()
        except Exception as exc:  # noqa: BLE001 — 清理不该拖垮启动
            print(f"[agentd] 回收区清理出错：{type(exc).__name__}: {exc}", flush=True)
            return []
        purged = list(res.get("purged") or [])
        if purged:
            print(f"[agentd] 回收区已清理 {len(purged)} 项：{'、'.join(purged)}",
                  flush=True)
        return purged

    def stop_extension_watch(self) -> None:
        self._ext_watch.set()
        self._ext_watch_thread = None

    # ---------------------------------------------------------------- RPC 面

    def methods(self) -> dict[str, Callable[..., Any]]:
        rt = self.runtime
        return {
            # ---- 任务 ----
            "chat.run": self.chat_run,
            "chat.resume": self.chat_resume,   # 内核崩溃后从断点续跑
            "chat.cancel": self.chat_cancel,
            "chat.approve": lambda step_id, approved: rt.resolve_approval(step_id, bool(approved)),
            # 页面刷新 / SSE 断线后靠它重建审批卡；内核没起来时 RPC 会报 503。
            "chat.pending_approvals": lambda task_id=None, owner_id=None: rt.pending_approvals(
                str(task_id or "") or None, owner_id
            ),
            "chat.busy": self.chat_busy,
            # ---- 能力清单 ----
            "catalog.tools": self.catalog_tools,
            "catalog.tools_search": lambda query="", limit=12: self.catalog_tools_search(query, int(limit)),
            "catalog.tool_index": self.catalog_tool_index,
            "catalog.skills": lambda: list(rt.skills.skills),
            # 技能清单的**详情** —— 走内核那份权威的 skills 库。
            #
            # 为什么不用 server 侧自己建一个 SkillLibrary：
            # 那会绕过注册表 —— 停用名单、依赖阻塞全都不生效，
            # 界面上关掉的技能照样列在这里。
            "catalog.skills_detail": self.catalog_skills_detail,
            "catalog.experts": lambda: rt.experts.catalog(),
            "catalog.expert": lambda expert_id: self.expert_get(expert_id),
            "catalog.agents": lambda: rt.agents.catalog(),
            "catalog.agent": lambda agent_id: self.agent_get(agent_id),
            "catalog.extensions": self.catalog_extensions,
            # 后端扩展的**轻量索引** —— 给 server 侧判定界面扩展的跨端依赖用。
            # 只给 kind/id/version，不给工具清单与报告：那是为了判依赖，
            # 不是给界面看的。
            "catalog.ext_index": self.catalog_ext_index,
            "catalog.reload_extensions": self.catalog_reload_extensions,
            "catalog.extension_enable": self.catalog_extension_enable,
            "catalog.extension_disable": self.catalog_extension_disable,
            "catalog.extension_install": self.catalog_extension_install,
            "catalog.extension_remove": self.catalog_extension_remove,
            "catalog.extension_trash": self.catalog_extension_trash,
            "catalog.ui_extension_remove": self.catalog_ui_extension_remove,
            "catalog.ui_extension_install": self.catalog_ui_extension_install,
            "catalog.extension_trash_list": self.catalog_extension_trash_list,
            "catalog.extension_trash_restore": self.catalog_extension_trash_restore,
            "catalog.extension_trash_purge": self.catalog_extension_trash_purge,
            "catalog.extension_trash_cleanup": self.catalog_extension_trash_cleanup,
            "catalog.extension_trash_purge_all": self.catalog_extension_trash_purge_all,
            "catalog.reload": self.catalog_reload,
            # ---- MCP ----
            "mcp.status": lambda: rt.mcp_status(),
            "mcp.set_enabled": lambda name, enabled: rt.mcp_set_enabled(name, bool(enabled)),
            # ---- 项目文档 ----
            "doc.stats": lambda: rt.project_doc.stats(),
            "doc.read": lambda limit=20000: rt.project_doc.read(int(limit)),
            # ---- 索引 ----
            "index.stats": lambda: rt.index.stats(),
            "index.search": lambda query, limit=20, mode="hybrid": rt.index.search(query, int(limit), mode=mode),
            "index.scan": lambda roots=None: self.index_scan(roots),
            "index.clear": self.index_clear,
            "index.libraries": lambda limit=60: {"libraries": rt.index.library_groups(int(limit))},
            "index.library": lambda path="", limit=200: {
                "files": rt.index.library_files(str(path or ""), int(limit))
            },
            # ---- 终端 ----
            "terminal.run": lambda command, cwd="": self.terminal_run(command, str(cwd or "")),
            "terminal.list": lambda: rt.terminal.list(),
            "terminal.interrupt": lambda id: rt.terminal.interrupt(str(id)),
            # ---- 检查点 ----
            "checkpoint.list": lambda task_id="": self.checkpoint_list(str(task_id or "")),
            "checkpoint.rewind": lambda checkpoint, scope="CodeAndConversation": self.checkpoint_rewind(
                str(checkpoint), str(scope or "CodeAndConversation")
            ),
            # ---- 自动任务 ----
            "automation.list": lambda owner_id=None: rt.scheduler.list(owner_id),
            "automation.add": lambda name, cron, goal, owner_id=None: rt.scheduler.add(
                name, cron, goal, owner_id
            ).to_dict(),
            "automation.remove": lambda id, owner_id=None: rt.scheduler.remove(str(id), owner_id),
            "automation.update": lambda id, name="", cron="", goal="", owner_id=None: (
                item.to_dict() if (item := rt.scheduler.update(
                    str(id), str(name or ""), str(cron or ""), str(goal or ""), owner_id
                )) else None
            ),
            "automation.toggle": lambda id, enabled, owner_id=None: (
                item.to_dict() if (item := rt.scheduler.toggle(str(id), bool(enabled), owner_id)) else None
            ),
            "automation.runs": lambda id="", limit=50, owner_id=None: {
                "runs": rt.store.automation_runs(
                    str(id or ""), max(1, min(int(limit), 200)), owner_id=owner_id
                )
            },
            "automation.retry_run": lambda run_id, owner_id=None: rt.scheduler.retry_failed_run(
                str(run_id), owner_id
            ),
            # ---- 团队 ----
            "team.list": lambda owner_id=None: self.team_list(owner_id),
            "team.get": lambda name, owner_id=None: self.team_get(str(name), owner_id),
            "team.create": lambda name, members, goal="", owner_id=None: self.team_create(
                name, members, str(goal or ""), owner_id
            ),
            "team.delete": lambda name, owner_id=None: rt.teams.delete(str(name), owner_id),
            "team.add_member": lambda team, agent, role="", owner_id=None: self.team_add_member(
                team, agent, str(role or ""), owner_id
            ),
            "team.remove_member": lambda team, agent, owner_id=None: self.team_remove_member(team, agent, owner_id),
            "team.update": lambda team, new_name="", goal=None, member="", role=None, owner_id=None: self.team_update(
                team, str(new_name or ""), goal, str(member or ""), role, owner_id
            ),
            "team.save": lambda team, new_name="", goal=None, members=None, owner_id=None: self.team_save(
                team, str(new_name or ""), goal, members, owner_id
            ),
            "team.send_message": lambda team, to, content, owner_id=None: self.team_send(team, to, content, owner_id),
            "team.dispatch": lambda team, agent, owner_id=None: self.team_dispatch(team, agent, owner_id),
            "team.inbox": lambda team, agent, owner_id=None: self.team_inbox(team, agent, owner_id),
            # ---- 工作流编排 ----
            "workflow.run": self.workflow_run,
            "workflow.status": lambda run_id="": self.workflow_status(str(run_id or "")),
            "workflow.cancel": lambda run_id="": self.workflow_cancel(str(run_id or "")),
            "workflow.list": lambda limit=20: {"runs": rt.store.workflow_runs(int(limit))},
            "workflow.delete": self.workflow_delete,
            "workflow.prune_failed": self.workflow_prune_failed,
            "workflow.failed_count": lambda: {"count": rt.store.count_all_failed_workflow_runs()},
            "workflow.plan": self.workflow_plan,
            # ---- 工作流模板 ----
            "workflow.template.list": self.workflow_template_list,
            "workflow.template.get": self.workflow_template_get,
            "workflow.template.save": self.workflow_template_save,
            "workflow.template.delete": self.workflow_template_delete,
            "workflow.template.run": self.workflow_template_run,
            # ---- 远程 ----
            "remote.start": lambda relay_url="", code="", policy=0: rt.registry["remote_start"].fn(
                relay_url=relay_url, code=code, policy=int(policy)
            ),
            "remote.stop": lambda: rt.registry["remote_stop"].fn(),
            "remote.status": lambda: rt.registry["remote_status"].fn()["content"],
            # ---- 生命周期 ----
            "runtime.shutdown": self.shutdown,
            "runtime.workspace_changed": self.workspace_changed,
            "runtime.activate": self.activate,
            "runtime.prewarm_status": self.prewarm_status,
            "runtime.config_reload": self.config_reload,
        }

    # ---------------------------------------------------------------- 任务

    def chat_run(self, goal: str, mode=None, interaction=None, style=None, expert=None,
                  agent_id=None, config_snapshot=None, session_id=None,
                  question_kept=False, owner_id=None) -> dict[str, Any]:
        """起一个 Agent 任务。立即返回，不等它跑完 —— 结果走事件流。

        ``session_id`` 是要续接的上一轮会话。传了就把它的上下文带过来，
        用户不用点「新建对话」也能接着聊。

        ``question_kept`` 表示这条提问**已经在库里了**（重新生成场景：
        控制面删掉了旧回答但保留了提问）。这时内核不能再写一遍，
        否则库里会出现 user, user, assistant 三条。
        """
        return self._launch(goal, mode, interaction, style, expert, session_id,
                            question_kept, owner_id=owner_id, agent_id=agent_id,
                            config_snapshot=config_snapshot)

    def chat_resume(self, goal: str, mode=None, interaction=None, style=None,
                    expert=None, agent_id=None, config_snapshot=None,
                    session_id=None, owner_id=None) -> dict[str, Any]:
        """内核崩溃重启后：找中断的任务，带着已完成步骤从断点继续。

        找不到可续的断点（比如崩在第一步之前）就退回普通重跑 ——
        语义和以前完全一致，不会让「恢复」变成新的失败点。
        """
        from .resume import build_resume_brief, find_interrupted_task

        task_id = find_interrupted_task(self.runtime.store)
        if task_id:
            task = self.runtime.store.get_task(task_id)
            owner_id = (task or {}).get("owner_id")
            info = build_resume_brief(self.runtime.store, task_id)
            if info["has_progress"]:
                self.bus.publish(
                    "run.resumed", task_id=task_id,
                    completed=info["completed"], last_tool=info["last_tool"],
                )
                return self._launch(goal, mode, interaction, style, expert,
                                    session_id, False, info["brief"], task_id,
                                    owner_id=owner_id, agent_id=agent_id,
                                    config_snapshot=config_snapshot)
        return self._launch(goal, mode, interaction, style, expert, session_id, False,
                            owner_id=owner_id, agent_id=agent_id,
                            config_snapshot=config_snapshot)

    def _launch(self, goal: str, mode, interaction, style, expert, session_id,
                question_kept=False, resume_brief="", resume_task_id="",
                owner_id=None, agent_id=None, config_snapshot=None) -> dict[str, Any]:
        """起任务线程的唯一入口。chat_run / chat_resume 共用。"""
        rt = self.runtime

        if self._prewarm and not self._activated:
            # 待命内核不接活：它还没转正，跑了任务就没人知道结果该给谁
            raise RuntimeError("内核尚未激活")

        # Allocate the conversation id before starting the background thread so the
        # React client can open the new run immediately. Existing sessions and crash
        # resumes keep their original id and frozen snapshot.
        if not session_id and not resume_task_id:
            provider_cfg = rt.config.provider_config()
            runtime_cfg = (config_snapshot or {}).get("runtime") or {}
            session_id = rt.store.create_task(
                goal,
                str(runtime_cfg.get("mode") or mode or rt.config.mode),
                str(provider_cfg.get("name") or "?"),
                owner_id=owner_id,
                agent_id=agent_id,
                expert_id=expert,
                config_snapshot=config_snapshot,
            )
        task_id = session_id or resume_task_id

        def _run() -> None:
            self._busy = True
            try:
                # pending_slot=True：把 chat_run 里登记的「正要开始跑」那个槽位
                # 交接给「正在跑」。交接在 run() 入口的同一个锁里完成，
                # 中间不留「两边都是 0」的缝 —— 用户恰好在那儿点停止，
                # 那一下不会被当成「没有任务在跑」而丢掉。
                rt.run(goal, mode, interaction, style, expert, session_id,
                       question_kept, pending_slot=True,
                       resume_brief=resume_brief, resume_task_id=resume_task_id,
                       owner_id=owner_id, agent_id=agent_id,
                       config_snapshot=config_snapshot)
            except Exception as exc:  # noqa: BLE001 — 兜住，别让线程静默死掉
                if task_id:
                    try:
                        rt.store.set_task_status(task_id, "failed")
                    except Exception:
                        pass
                # 事件名跟 AG-UI 家族一致（大写）—— 前端按大小写精确匹配，
                # 这里写小写 run.error 的话前端的 case 'RUN_ERROR' 永远匹配不上。
                self.bus.publish(
                    "RUN_ERROR", error=f"{type(exc).__name__}: {exc}", _owner_id=owner_id
                )
            finally:
                self._busy = False
                # 明确告诉控制面这轮结束了：它据此清掉"崩溃时补跑"的记录
                self.bus.publish("RUN_FINISHED", _owner_id=owner_id)

        # 先登记「有任务要跑」再起线程 ——
        # 从现在到 run() 真正开跑之间有个窗口（建 task、查配置、拼提示词、连模型），
        # 用户在这个窗口点「停止」也得算数。
        # 之前这段窗口里内核认为「没有任务在跑」，那一下停止直接被丢掉。
        rt.mark_run_pending(task_id=task_id, owner_id=owner_id)
        threading.Thread(target=_run, name="oryx-agent-run", daemon=True).start()
        return {"status": "started", "resumed": bool(resume_task_id), "task_id": task_id}

    def chat_busy(self) -> bool:
        return self._busy

    def chat_cancel(
        self, task_id: str | None = None, owner_id: str | None = None,
    ) -> dict[str, Any]:
        """中止正在跑的任务（用户点「停止」）。"""
        return self.runtime.cancel(task_id=task_id, owner_id=owner_id)

    # ---------------------------------------------------------------- 检查点

    def checkpoint_list(self, task_id: str) -> dict[str, Any]:
        """列出某个会话的检查点，给界面做「回退到这里」按钮用。"""
        if not task_id:
            return {"task_id": "", "current": None, "checkpoints": []}
        return self.runtime.checkpoints.status(task_id)

    def checkpoint_rewind(self, checkpoint: str, scope: str = "CodeAndConversation") -> dict[str, Any]:
        """回退到某个检查点。

        回退会真的改文件、删消息 —— 所以要发事件通知前端刷新，
        否则界面还显示着已经退掉的那些回答。

        **运行中不许回退**：这一轮可能正在写文件，边写边退会把内容写乱。
        只拦目标会话自己的运行，别的会话在跑不影响这里。
        """
        cp = self.runtime.store.get_checkpoint(checkpoint)
        if cp:
            task_id = str(cp.get("task_id") or "")
            if self.runtime.is_task_running(task_id):
                return {
                    "ok": False,
                    "content": "这个会话正在运行，先停止或等它结束再回退",
                }
        result = self.runtime.checkpoints.rewind(checkpoint, scope)
        if result.get("ok"):
            cp = cp or self.runtime.store.get_checkpoint(checkpoint)
            task_id = str(cp.get("task_id")) if cp else ""
            self.bus.publish(
                "checkpoint.rewound",
                task_id=task_id,
                checkpoint=checkpoint,
                scope=scope,
                content=result["content"],
            )
        return result

    # ---------------------------------------------------------------- 清单

    def catalog_ext_index(self) -> dict[str, Any]:
        """后端扩展的轻量索引 —— `[{kind, id, version}]`。

        给 server 侧用：界面扩展可以声明依赖某个后端扩展，
        但 server 进程**看不到**内核的注册表（那是内核进程的东西）。
        这个接口把「有哪些后端扩展、什么版本」递过去，
        界面扩展的跨端依赖就能判了。
        """
        rt = self.runtime
        reg = getattr(rt, "extension_registry", None)
        if reg is None:
            return {"ok": False, "reason": "扩展注册表不可用", "records": []}
        return {
            "ok": True,
            "records": [
                {"kind": r.kind, "id": r.id, "version": r.version}
                for r in reg.all_records()
            ],
        }

    def catalog_extensions(self) -> dict[str, Any]:
        """扩展总览：注册表、profile、工具加载报告。

        这是「可自由支配」的可见性 —— 用户能随时问：
        · 现在加载了哪些扩展？各自什么信任级别？
        · 当前用的是哪个 profile？它包含哪些扩展？
        · 加载了谁的代码？哪些被跳过了、为什么？
        """
        from .registry import capability_catalog as _capability_catalog

        rt = self.runtime
        # 用户数据目录 —— 用户得知道**往哪放扩展**。
        # 这个路径不是固定的（Windows 上优先落在 D 盘），
        # 所以只能运行时告诉，不能在文档里写死。
        try:
            from .config import app_dir
            user_dir = str(app_dir())
        except Exception:
            user_dir = ""
        reg = getattr(rt, "extension_registry", None)
        if reg is None:
            return {"ok": False, "reason": "扩展注册表不可用"}

        records = []
        for rec in reg.all_records():
            item = rec.to_dict()
            item["body_path"] = str(rec.body_path) if rec.body_path else ""
            records.append(item)

        # 被停用的扩展**不在注册表里**（不进入 all_records），
        # 所以要从停用名单里补出来 —— 否则界面上看不到「我关掉的那些」。
        disabled = getattr(rt, "_disabled_extensions", None) or set()
        known_ids = {r["id"] for r in records}
        for ext_id in sorted(disabled):
            if ext_id in known_ids:
                continue
            records.append({
                "id": ext_id, "kind": "?", "name": ext_id, "description": "",
                "trust": "user", "disabled": True, "triggers": [], "caps": [],
            })

        report = getattr(rt, "tool_plugin_report", None)
        plugin_tools = [
            {
                "name": t.spec.name,
                "extension": t.extension_id,
                "trust": t.trust,
                "risk": t.declared_risk,
                "description": t.spec.description,
            }
            for t in (report.loaded if report else [])
        ]

        profile_id = reg.profile_id()
        profiles = getattr(rt, "extension_profiles", None)
        active: list[str] = []
        if profiles is not None and profile_id:
            active = list(profiles.resolve_with_fallback(profile_id))

        return {
            "ok": True,
            "profile": profile_id,
            "profile_active": active,
            "available_profiles": profiles.list_profiles() if profiles else [],
            "stats": reg.stats(),
            "digest": reg.digest(),
            "extensions": records,
            "plugin_tools": plugin_tools,
            # 依赖解析报告 —— 「为什么这个扩展没跑」的答案。
            # 扩展自己的 oryx.json 里写了 requires，但依赖没装上时，
            # 用户得在界面上看见原因，而不是发现「工具莫名其妙少了一个」。
            "dependencies": (reg.dep_report.to_dict()
                             if getattr(reg, "dep_report", None) else {}),
            "tool_report": {
                "loaded": len(report.loaded) if report else 0,
                "skipped": [{"name": n, "reason": w} for n, w in (report.skipped if report else [])],
                "errors": list(report.errors) if report else [],
                "sources": list(report.sources) if report else [],
            },
            # 统一工具表：三种来源一张表 + 冲突 + 声明问题
            "tool_table": getattr(rt, "tool_table", None).to_dict()
            if getattr(rt, "tool_table", None) else {},
            # 前端扩展（界面模块）—— 与后端扩展分开列
            # 它们住在不同目录、加载机制完全不同，混在一起会看不清。
            "ui_extensions": self._ui_extensions(),
            # 能力词典 —— 「这个能力到底管什么」的完整答案。
            # 写扩展的人不用翻源码就能查到：风险分级、人话说明、
            # 不可信来源能不能声明、界面侧覆盖哪些路径。
            "capability_catalog": _capability_catalog(),
            # 用户数据目录 —— 「扩展往哪放」的答案
            "user_dir": user_dir,
            # 回收区统计 —— 界面上要显示「保留 N 天」与占用空间，
            # 让自动清理这件事是**看得见的**，不是后台悄悄发生的事。
            "trash_stats": self._trash_stats(),
        }

    def _trash_stats(self) -> dict[str, Any]:
        """两个回收区的统计。"""
        from .extmanage import trash_stats

        be_root, ui_root = self._ext_roots()
        days = self._trash_retention_days()
        backend = trash_stats(be_root, days)
        ui = trash_stats(ui_root, days)
        return {
            "retention_days": days,
            "backend": backend,
            "ui": ui,
            "count": backend["count"] + ui["count"],
            "bytes": backend["bytes"] + ui["bytes"],
            "expiring": [f"backend:{x}" for x in backend["expiring"]]
                        + [f"ui:{x}" for x in ui["expiring"]],
        }

    def _ui_extensions(self) -> dict[str, Any]:
        """前端扩展的清单 —— 界面上的「扩展」节要用。

        依赖也要在这里判定 —— 设置页要能看见
        「这个界面扩展因为缺依赖没挂上」。
        """
        from .config import app_dir
        from .uiext import (
            discover_ui_extensions,
            resolve_ui_dependencies,
            ui_extension_roots,
        )

        roots = ui_extension_roots(app_dir())
        found: list[dict[str, Any]] = []
        all_exts = []
        for root in roots:
            for ext in discover_ui_extensions(root):
                all_exts.append(ext)
                item = ext.to_dict()
                item["problems"] = list(ext.problems)
                found.append(item)

        # 依赖判定（跨端依赖要看后端扩展 —— 注册表就在这儿）
        reg = getattr(self.runtime, "extension_registry", None)
        backend_records = reg.all_records() if reg is not None else []
        off = getattr(self.runtime, "_disabled_extensions", None) or set()
        resolve_ui_dependencies(all_exts, backend_records, disabled_ids=off)

        # 判定结果写回 items —— blocked_reason 才有值
        by_id = {e.id: e for e in all_exts}
        for item in found:
            ext = by_id.get(item["id"])
            if ext is not None:
                item["blocked_reason"] = ext.blocked_reason

        return {
            "roots": [str(r) for r in roots],
            "extensions": found,
            "skipped": [
                {"id": e["id"], "reason": "；".join(e["problems"])}
                for e in found if e["problems"]
            ] + [
                {"id": e.id, "reason": e.blocked_reason}
                for e in all_exts if e.blocked_reason
            ],
        }

    def catalog_reload_extensions(self) -> dict[str, Any]:
        """重新加载改了的后端扩展 —— 不用重启内核。"""
        return self.runtime.reload_extensions()

    # ---- 扩展管理（启用 / 停用 / 安装 / 删除）--------------------

    def _ext_roots(self) -> tuple[Any, Any]:
        """（后端扩展根目录, 界面扩展根目录）。"""
        from .config import app_dir
        from .uiext import ui_extension_roots

        home = app_dir()
        return home / "extensions", (ui_extension_roots(home) or [home])[0]

    def catalog_ui_extension_remove(self, ext_id: str = "") -> dict[str, Any]:
        """删除一个界面扩展 —— **移进回收区**，不是真删。

        与后端扩展各有各的回收区（`extensions/.trash/` 与
        `ui-extensions/.trash/`）—— 两者内容形态不同，混在一起会看不清。
        """
        from .extmanage import ExtensionError, move_to_trash

        rt = self.runtime
        _be_root, ui_root = self._ext_roots()
        try:
            path = move_to_trash(ui_root, ext_id)
        except ExtensionError as exc:
            return {"ok": False, "reason": str(exc)}
        self._apply_disabled()
        return {"ok": True, "trashed": path, "id": ext_id}

    def catalog_ui_extension_install(self, source: str = "") -> dict[str, Any]:
        """从本地目录安装一个界面扩展。装完**默认禁用**。"""
        from .extmanage import ExtensionError, install_extension, set_disabled

        rt = self.runtime
        _be_root, ui_root = self._ext_roots()
        try:
            res = install_extension(Path(source), ui_root)
        except ExtensionError as exc:
            return {"ok": False, "reason": str(exc)}
        if set_disabled(rt.config.data, res.id, True):
            rt.config.save()
        self._apply_disabled()
        return {"ok": True, "extension": res.to_dict()}

    def catalog_extension_trash_list(self) -> dict[str, Any]:
        """两个回收区的内容。"""
        from .extmanage import list_trash

        be_root, ui_root = self._ext_roots()
        return {
            "ok": True,
            "items": (
                [{**i, "kind": "backend"} for i in list_trash(be_root)]
                + [{**i, "kind": "ui"} for i in list_trash(ui_root)]
            ),
            "retention_days": self._trash_retention_days(),
        }

    def _trash_retention_days(self) -> int:
        """回收区保留天数（0 = 永不自动清理）。"""
        try:
            return max(0, int(self.config.data.get("extension_trash_retention_days") or 0))
        except Exception:
            return 0

    def catalog_extension_trash_cleanup(self) -> dict[str, Any]:
        """清掉超过保留期的回收区条目。

        回收区只进不出的话就是个只涨不消的垃圾桶 ——
        装几十个扩展试一遍、每个都删掉，那些代码就一直占着地方。
        """
        from .extmanage import purge_expired

        days = self._trash_retention_days()
        be_root, ui_root = self._ext_roots()
        purged = purge_expired(be_root, days) + purge_expired(ui_root, days)
        return {"ok": True, "purged": purged, "retention_days": days}

    def catalog_extension_trash_purge_all(self, kind: str = "backend") -> dict[str, Any]:
        """清空回收区（手动，比自动清理更彻底）。

        需要显式调用 —— 这是不可逆操作，不该由定时任务替用户做。
        """
        from .extmanage import list_trash, purge_from_trash

        be_root, ui_root = self._ext_roots()
        root = ui_root if kind == "ui" else be_root
        purged: list[str] = []
        for item in list_trash(root):
            try:
                purge_from_trash(root, item["id"])
                purged.append(item["id"])
            except Exception:
                continue
        return {"ok": True, "purged": purged, "kind": kind}

    def catalog_extension_trash_restore(self, name: str = "", kind: str = "backend") -> dict[str, Any]:
        """从回收区恢复。"""
        from .extmanage import ExtensionError, restore_from_trash

        be_root, ui_root = self._ext_roots()
        root = ui_root if kind == "ui" else be_root
        try:
            path = restore_from_trash(root, name)
        except ExtensionError as exc:
            return {"ok": False, "reason": str(exc)}
        self._apply_disabled()
        return {"ok": True, "restored": path, "id": name}

    def catalog_extension_trash_purge(self, name: str = "", kind: str = "backend") -> dict[str, Any]:
        """从回收区彻底删除。"""
        from .extmanage import ExtensionError, purge_from_trash

        be_root, ui_root = self._ext_roots()
        root = ui_root if kind == "ui" else be_root
        try:
            purge_from_trash(root, name)
        except ExtensionError as exc:
            return {"ok": False, "reason": str(exc)}
        return {"ok": True, "purged": name}

    def _apply_disabled(self) -> None:
        """把停用名单应用到注册表**并重载各个 library**。

        以前只重扫注册表 —— 于是界面上点「停用」，
        技能库/专家库/Agent 库**照旧留着那些扩展**：
        停用看着生效了（注册表里没了），实际还在被用。
        """
        from .extmanage import disabled_ids

        rt = self.runtime
        rt._disabled_extensions = disabled_ids(rt.config.data)
        rt.extension_registry.set_disabled(rt._disabled_extensions)
        rt.extension_registry.discover()
        # library 要跟着重载 —— 它们现在都以注册表为准，
        # 停用名单变了就得重新扫一遍。
        for _lib in ("skills", "experts", "agents"):
            _obj = getattr(rt, _lib, None)
            if _obj is not None and hasattr(_obj, "reload"):
                try:
                    _obj.reload()
                except Exception:
                    pass

    def catalog_extension_enable(self, ext_id: str = "") -> dict[str, Any]:
        """启用一个扩展。"""
        from .extmanage import set_disabled

        rt = self.runtime
        changed = set_disabled(rt.config.data, ext_id, False)
        if changed:
            rt.config.save()
            self._apply_disabled()
        return {"ok": True, "changed": changed, "id": ext_id}

    def catalog_extension_disable(self, ext_id: str = "") -> dict[str, Any]:
        """停用一个扩展。"""
        from .extmanage import set_disabled

        rt = self.runtime
        changed = set_disabled(rt.config.data, ext_id, True)
        if changed:
            rt.config.save()
            self._apply_disabled()
        return {"ok": True, "changed": changed, "id": ext_id}

    def catalog_extension_install(self, source: str = "") -> dict[str, Any]:
        """从本地目录安装一个扩展。

        装完**默认禁用** —— 「装」和「让它跑」是两个独立决定。
        """
        from .extmanage import ExtensionError, install_extension

        rt = self.runtime
        be_root, _ui_root = self._ext_roots()
        try:
            res = install_extension(Path(source), be_root)
        except ExtensionError as exc:
            return {"ok": False, "reason": str(exc)}
        # 装完加进停用名单 —— 默认不跑
        from .extmanage import set_disabled

        if set_disabled(rt.config.data, res.id, True):
            rt.config.save()
        self._apply_disabled()
        return {"ok": True, "extension": res.to_dict()}

    def catalog_extension_remove(self, ext_id: str = "") -> dict[str, Any]:
        """删除一个扩展 —— **移进回收区**，不是真删。"""
        from .extmanage import ExtensionError, move_to_trash

        rt = self.runtime
        be_root, _ui_root = self._ext_roots()
        try:
            path = move_to_trash(be_root, ext_id)
        except ExtensionError as exc:
            return {"ok": False, "reason": str(exc)}
        self._apply_disabled()
        return {"ok": True, "trashed": path, "id": ext_id}

    def catalog_extension_trash(self) -> dict[str, Any]:
        """回收区列表。"""
        from .extmanage import list_trash

        be_root, _ui_root = self._ext_roots()
        return {"ok": True, "items": list_trash(be_root)}

    def catalog_tools(self) -> dict[str, Any]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for name, spec in sorted(self.runtime.registry.items()):
            group = "mcp" if name.startswith("mcp__") else name.split("_")[0]
            groups.setdefault(group, []).append(
                {
                    "name": name,
                    "description": spec.description,
                    "risk": int(spec.risk),
                    "risk_label": spec.risk.label,
                }
            )
        return {
            "total": len(self.runtime.registry),
            "groups": [
                {"group": g, "tools": items, "count": len(items)}
                for g, items in sorted(groups.items(), key=lambda kv: -len(kv[1]))
            ],
            "runtime_tools": ["task_create", "task_update", "task_list"],
            "agents": self.runtime.agents.catalog(),
        }

    def catalog_tools_search(self, query: str, limit: int) -> dict[str, Any]:
        out = self.runtime.registry["tool_search"].fn(query=query, limit=limit)
        tools = out.get("content") or []
        # 命中数按实际返回条数算：工具内部只在部分分支填 meta.hits，
        # 拿 meta 当 count 会在无参数检索时误报 0 条
        return {"count": len(tools), "tools": tools}

    def catalog_tool_index(self) -> dict[str, Any]:
        """工具语义索引的状态。顺带把它建起来，省得首次 tool_search 卡一下。"""
        self.runtime.tool_index.ensure(self.runtime.registry)
        return self.runtime.tool_index.status()

    def expert_get(self, expert_id: str) -> dict[str, Any] | None:
        e = self.runtime.experts.get(expert_id)
        return e.to_dict() if e else None

    def agent_get(self, agent_id: str) -> dict[str, Any] | None:
        a = self.runtime.agents.get(agent_id)
        return a.to_dict() if a else None

    def catalog_skills_detail(self) -> dict[str, Any]:
        """技能清单（含描述与路径）—— 走内核那份权威的库。

        server 侧自己建 SkillLibrary 会**绕过注册表**：
        停用名单、依赖阻塞全都不生效。
        """
        rt = self.runtime
        return {
            "skills": [
                {"name": s.name, "description": s.description, "path": str(s.path)}
                for s in rt.skills.skills.values()
            ]
        }

    def catalog_reload(self) -> dict[str, Any]:
        rt = self.runtime
        rt.skills.reload()
        rt.experts.reload()
        rt.agents.reload()
        rt._init_plugins()
        # _init_plugins 会重挂 MCP 工具，语义标注要跟着补回来
        rt._annotate_semantics()
        return {
            "skills": len(rt.skills.skills),
            "experts": len(rt.experts.catalog()),
            "agents": len(rt.agents.catalog()),
        }

    def workspace_changed(
        self,
        allowed_roots: list[str],
        forbidden_paths: list[str],
        workspace: str | None = None,
    ) -> dict[str, Any]:
        """工作区切换后，内核侧的策略引擎与项目文档要跟着走。

        注意这里不接config —— server 的 config 才是权威，跨进程只传算好的结果，
        避免两个进程各持一份内存配置互相覆盖。
        """
        from .policy import PolicyEngine
        from .projectdoc import ProjectDoc

        rt = self.runtime
        rt.config.data["allowed_roots"] = list(allowed_roots)
        rt.config.data["forbidden_paths"] = list(forbidden_paths)
        if workspace:
            rt.config.data["workspace"] = workspace
        rt.policy = PolicyEngine(list(allowed_roots), list(forbidden_paths))
        rt.project_doc = ProjectDoc(rt.config.data.get("workspace") or Path.cwd())
        return {"ok": True}

    # ---------------------------------------------------------------- 预热

    def activate(
        self,
        allowed_roots=None,
        forbidden_paths=None,
        workspace: str | None = None,
    ) -> dict[str, Any]:
        """把待命内核转正。

        待命期间它已经准备好了导入链、配置、数据库、工具注册表，
        但**故意没启动定时任务**（它还不是正式内核，不该抢跑用户任务）。
        激活时补上这一步，同时把工作区范围同步过来 ——
        待命期间配置可能被改过（切工作区），激活时以控制面为准对齐一次。

        重复激活是安全的 —— 第二次直接返回已激活状态。
        """
        if self._activated:
            return {"ok": True, "already": True}
        self._activated = True
        if allowed_roots is not None or forbidden_paths is not None or workspace:
            rt = self.runtime
            roots = list(allowed_roots or rt.config.allowed_roots)
            forbidden = list(forbidden_paths or rt.config.forbidden_paths)
            self.workspace_changed(roots, forbidden, workspace)
        # 定时任务现在才开跑
        try:
            self.runtime.scheduler.start()
        except Exception:
            pass
        return {"ok": True, "already": False}

    def prewarm_status(self) -> dict[str, Any]:
        """当前内核是不是待命状态。控制面用它判断该不该走激活路径。"""
        return {"prewarm": self._prewarm, "activated": self._activated}

    def config_reload(self) -> dict[str, Any]:
        """重读磁盘配置。

        内核是独立进程，config.json 在它启动后就冻结了。用户中途新增或
        切换 provider，不重读就一直按旧配置跑（"明明配好了却报未知 provider"）。
        由 AgentLink._sync_config 在检测到 mtime 变化时调用。
        """
        changed = self.config.refresh()
        # 策略引擎也吃 allowed_roots / forbidden_paths，得跟着走
        if changed:
            self.runtime.policy = PolicyEngine(
                self.config.allowed_roots, self.config.forbidden_paths
            )
        return {"ok": True, "changed": bool(changed)}

    # ---------------------------------------------------------------- 索引

    def index_scan(self, roots: list[str] | None) -> dict[str, Any]:
        rt = self.runtime
        if rt.index.scanning:
            raise RuntimeError("已有扫描在进行中")
        targets = roots or rt.config.allowed_roots

        def progress(payload: dict) -> None:
            self.bus.publish("index.done" if payload.get("done") else "index.progress", **payload)

        self.bus.publish("index.started", roots=targets)

        def _run() -> None:
            try:
                rt.index.scan(targets, on_progress=progress)
            except Exception as exc:  # noqa: BLE001
                self.bus.publish("index.error", error=str(exc))

        threading.Thread(target=_run, name="oryx-index-scan", daemon=True).start()
        return {"status": "started", "roots": targets}

    def index_clear(self) -> dict[str, Any]:
        rt = self.runtime
        if rt.index.scanning:
            raise RuntimeError("扫描进行中，无法清空")
        rt.index.clear()
        return {"status": "cleared"}

    # ---------------------------------------------------------------- 终端

    def terminal_run(self, command: str, cwd: str) -> dict[str, Any]:
        session = self.runtime.terminal.run(command, cwd)
        return {
            "id": session["id"],
            "status": "已启动，输出会实时推送到界面",
            "command": command,
            "cwd": session["cwd"],
        }

    # ---------------------------------------------------------------- 团队

    def team_list(self, owner_id: str | None = None) -> list[dict[str, Any]]:
        rt = self.runtime
        return [
            {**t, "thread": rt.teams.thread(t["name"], 12, owner_id)}
            for t in rt.teams.list(owner_id)
        ]

    def team_get(self, name: str, owner_id: str | None = None) -> dict[str, Any] | None:
        rt = self.runtime
        team = rt.teams.get(name)
        return {**team.to_dict(owner_id), "thread": rt.teams.thread(name, 50, owner_id)} if team else None

    def team_create(self, name: str, members: Any, goal: str, owner_id: str | None = None) -> dict[str, Any]:
        rt = self.runtime
        known = set(rt.agents.agents)
        team, err = rt.teams.create(name, members, goal, known_agents=known, owner_id=owner_id)
        if team is None:
            raise ValueError(err)
        rt.bus.publish("team.created", team=team.to_dict(owner_id), _owner_id=owner_id)
        return {"ok": True, "message": f"已建团队「{team.name}」，成员：{team.roles()}",
                "team": team.to_dict(owner_id)}

    def team_add_member(
        self, team: str, agent: str, role: str, owner_id: str | None = None
    ) -> dict[str, Any]:
        rt = self.runtime
        if agent not in set(rt.agents.agents):
            raise ValueError(f"没有这个子 Agent：{agent}")
        ok, err = rt.teams.add_member(team, agent, role, owner_id)
        if not ok:
            raise ValueError(err)
        obj = rt.teams.get(team)
        rt.bus.publish("team.updated", team=obj.to_dict(owner_id) if obj else {}, _owner_id=owner_id)
        return {"ok": True, "message": f"{agent} 已加入「{team}」，成员：{obj.roles() if obj else ''}"}

    def team_remove_member(self, team: str, agent: str, owner_id: str | None = None) -> dict[str, Any]:
        """把成员移出团队。真正的逻辑在 runtime 那边（它才持有 teams）。"""
        return self.runtime.team_remove_member(team, agent, owner_id)

    def team_update(
        self,
        team: str,
        new_name: str,
        goal: Any,
        member: str,
        role: Any,
        owner_id: str | None = None,
    ) -> dict[str, Any]:
        """改团队的名字 / 目标 / 某个成员的分工。"""
        return self.runtime.team_update(team, new_name, goal, member, role, owner_id)

    def team_save(
        self,
        team: str,
        new_name: str = "",
        goal: Any = None,
        members: Any = None,
        owner_id: str | None = None,
    ) -> dict[str, Any]:
        """整体保存团队定义。控制面发一次 IPC，内核一次校验一次落地。"""
        return self.runtime.team_save(team, new_name, goal, members, owner_id)

    def team_send(self, team: str, to: str, content: str, owner_id: str | None = None) -> dict[str, Any]:
        rt = self.runtime
        msg, err = rt.teams.post(team, "main", str(to), content, owner_id)
        if msg is None:
            raise ValueError(err)
        rt.bus.publish("team.message", team=team, message=msg.to_dict(), _owner_id=owner_id)
        if to in ("broadcast", "all"):
            return {"ok": True, "message": f"已广播给「{team}」全体成员"}
        return {"ok": True,
                "message": f"已投递消息给 {to}（内容未执行，用 team_dispatch 才会真的开工）"}

    def team_dispatch(self, team: str, agent: str, owner_id: str | None = None) -> dict[str, Any]:
        result = self.runtime.registry["team_dispatch"].fn(
            team=str(team), agent=str(agent), owner_id=owner_id
        )
        if not result.get("ok"):
            raise ValueError(str(result.get("content", "派活失败")))
        return {"ok": True, "content": result.get("content", "")}

    def team_inbox(self, team: str, agent: str, owner_id: str | None = None) -> list[dict[str, Any]]:
        return [m.to_dict() for m in self.runtime.teams.inbox(
            str(team), str(agent), mark_read=False, owner_id=owner_id
        )]

    # ---------------------------------------------------------------- 工作流

    def workflow_run(self, plan: dict[str, Any], args: dict[str, Any] | None = None) -> dict[str, Any]:
        """跑一份编排。同步执行 —— 可能几分钟，所以客户端那边要用长超时。

        校验失败抛 ValueError（不是 ok=False）：HTTP 层据此回 400，
        走"成功但失败"的返回会被前端当成正常结果渲染。
        """
        return self._workflow_result(self.runtime.run_workflow(plan, args or {}))

    def _workflow_result(self, result: dict[str, Any]) -> dict[str, Any]:
        """把引擎的返回收成统一的对外形状。

        模板触发与直接发起走的是同一套返回 —— 前端处理逻辑才能复用一份。

        `report_path` 要一并带出去：内核把报告写到了文件里，
        这个路径是用户找到那份文件的唯一线索。漏了它，
        报告就写了一份谁也看不见。
        """
        if not result.get("ok") and not (result.get("meta") or {}).get("run_id"):
            # 没有 run_id = 压根没开跑（定义有问题），属于调用错误
            raise ValueError(str(result.get("content", "编排定义有问题")))
        meta = result.get("meta") or {}
        return {"ok": bool(result.get("ok")), "content": result.get("content", ""),
                "run_id": meta.get("run_id", ""), "status": meta.get("status", ""),
                "report_path": meta.get("report_path", "")}

    def workflow_status(self, run_id: str = "") -> dict[str, Any]:
        return self.runtime.workflow_status(run_id)

    def workflow_cancel(self, run_id: str = "") -> dict[str, Any]:
        return self.runtime.workflow_cancel(run_id)

    def workflow_delete(self, run_id: str) -> dict[str, Any]:
        """删掉一次历史 run。正在跑的不能删 —— 删了拿不到事件，跑完会变孤儿。"""
        active = self.runtime.active_workflow
        if active is not None and active.id == run_id:
            raise RuntimeError("这次工作流还在跑，先中止再删")
        if not self.runtime.store.delete_workflow_run(run_id):
            raise ValueError(f"没有这次运行：{run_id}")
        return {"ok": True, "deleted": run_id}

    def workflow_prune_failed(self) -> dict[str, Any]:
        """批量删掉"每个 step 都失败"的 run。

        判定与删除都在库侧做 —— 列表接口是分页的，前端循环删永远删不干净
        （删掉一批，更早的全失败记录补位上来）。
        """
        n = self.runtime.store.prune_all_failed_workflow_runs()
        return {"ok": True, "pruned": n}

    def workflow_plan(self, run_id: str) -> dict[str, Any]:
        """取某次 run 用的编排定义。"""
        return self.runtime.workflow_run_plan(run_id)

    # ---------------------------------------------------------------- 模板

    def workflow_template_list(self) -> dict[str, Any]:
        # 走 runtime 而不是 store：只读标记要**重算**（子 Agent 白名单事后变了，
        # 存下来的标记会过期），runtime.list_workflow_templates 负责这件事。
        return {"templates": self.runtime.list_workflow_templates()}

    def workflow_template_get(self, template_id: str) -> dict[str, Any]:
        # 走 runtime：只读标记要重算，别名也要能当键用
        tpl = self.runtime.get_workflow_template(str(template_id))
        if tpl is None:
            raise ValueError(f"没有这个模板：{template_id}")
        return {"ok": True, "content": tpl}

    def workflow_template_save(self, item: dict[str, Any]) -> dict[str, Any]:
        """存一份模板。定义有问题要抛 ValueError（HTTP 层据此回 400）。"""
        result = self.runtime.save_workflow_template(item)
        if not result.get("ok"):
            raise ValueError(str(result.get("content", "模板有问题")))
        return {"ok": True, "content": result.get("content")}

    def workflow_template_delete(self, template_id: str) -> dict[str, Any]:
        if not self.runtime.store.delete_workflow_template(str(template_id)):
            raise ValueError(f"没有这个模板：{template_id}")
        return {"ok": True, "deleted": template_id}

    def workflow_template_run(
        self, template_id: str, args: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """按模板跑。同步执行 —— 跟 workflow_run 同一个阻塞语义。

        不改成"立即返回 run_id 后台跑"：内核的 self._workflow 只有一个槽位，
        并发跑两个 run 会互相覆盖，中止功能就指错对象了。
        """
        tpl = self.runtime.store.get_workflow_template(str(template_id))
        if tpl is None:
            raise ValueError(f"没有这个模板：{template_id}")
        return self._workflow_result(self.runtime.run_workflow_template(str(template_id), args or {}))

    # ---------------------------------------------------------------- 生命周期

    def shutdown(self) -> dict[str, Any]:
        self.runtime.shutdown()
        return {"ok": True}

    # ---------------------------------------------------------------- 入口

    def serve(self, host: str = "127.0.0.1", port: int = 0) -> int:
        actual = self.server.start(host, port)
        print(f"[agentd] Agent 内核已就绪 {host}:{actual}", flush=True)
        return actual


def main() -> None:
    parser = argparse.ArgumentParser(description="Oryx Agent 内核（独立进程）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0, help="0 = 由系统分配")
    parser.add_argument("--port-file", default="", help="把实际端口写到这个文件")
    parser.add_argument("--parent-pid", type=int, default=None)
    parser.add_argument(
        "--prewarm",
        action="store_true",
        help="待命模式：把冷启动开销提前付掉，激活前不跑定时任务",
    )
    args = parser.parse_args()

    # 缓存与临时目录指到 D 盘。
    # agentd 是**独立进程**（由 agentlink / prewarm 拉起），
    # 不继承桌面端的完整环境时它的缓存就会漏到 C 盘。
    # 必须在 tempfile.gettempdir() 被调用前做。
    from .paths import redirect_env

    redirect_env()

    if args.parent_pid:
        # 父进程监控与 HTTP 层共用一份实现，避免两处逻辑漂移
        from .__main__ import _watch_parent

        _watch_parent(args.parent_pid)

    daemon = AgentDaemon(prewarm=args.prewarm)
    # 扩展文件监视：改了自动重载（可关：ORYX_EXT_AUTORELOAD=0）
    daemon.start_extension_watch()

    # 兜底回收：内核被强杀（崩溃 / 任务管理器结束进程）时 shutdown 不会走，
    # atexit 能在正常解释器退出时补上；剩下的交给 Windows 的
    # Job Object —— 子进程挂在内核 PID 下，内核死了系统会一并收掉。
    import atexit

    atexit.register(lambda: _safe_shutdown(daemon))

    actual = daemon.serve(args.host, args.port)
    if args.port_file:
        Path(args.port_file).write_text(str(actual), "utf-8")

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        daemon.server.stop()


def _safe_shutdown(daemon: "AgentDaemon") -> None:
    """退出前收子进程。任何异常都不能影响退出流程。"""
    try:
        daemon.runtime.terminal.shutdown()
    except Exception:
        pass


if __name__ == "__main__":
    main()
