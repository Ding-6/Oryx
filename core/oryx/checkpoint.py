"""检查点与回退：把"Agent 改错了"变成一件可撤销的事。

对齐 WorkBuddy 的 checkpointing：
- 每次用户提示打一个检查点，跨会话保留，30 天自动清理
- 回退分三种范围：仅对话 / 仅代码 / 两者都退

设计取舍：
1. **只快照写操作碰过的文件**，不复制整个工作区。Agent 改 3 个文件就存 3 份
   内容，磁盘占用与改动量成正比，而不是与仓库大小成正比。
2. **回退代码走文件内容还原**，不依赖 git。用户可能压根没初始化仓库，
   而"让 Agent 自己回退"往往回退不干净（它不知道该退到哪）。
3. **不跟踪 bash 造成的改动** —— shell 能改的东西太杂（装包、改注册表、
   写数据库），快照不下来的。与 WorkBuddy 的限制一致，文档里写明。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

# 单文件快照上限：超过这个大小只记元信息，不存内容
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024
# 单个检查点最多记多少个文件
MAX_FILES_PER_CHECKPOINT = 200
# 检查点保留天数
RETENTION_DAYS = 30

# 回退范围
SCOPE_CODE = "Code"
SCOPE_CONVERSATION = "Conversation"
SCOPE_BOTH = "CodeAndConversation"


def _resolve(path: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(path))).resolve()


class CheckpointService:
    """检查点的打点、快照与回退。"""

    def __init__(self, store: Any, allowed_roots: list[str] | None = None):
        self.store = store
        self.allowed_roots = [_resolve(p) for p in (allowed_roots or [])]
        # 当前任务的活动检查点：task_id -> checkpoint_id
        self._active: dict[str, str] = {}

    # ---------------------------------------------------------------- 打点

    def mark(self, task_id: str, seq: int, label: str = "") -> str:
        """在一次用户提示开始时打点。"""
        cid = self.store.create_checkpoint(task_id, seq, label)
        self._active[task_id] = cid
        return cid

    def current(self, task_id: str) -> str | None:
        return self._active.get(task_id)

    def clear(self, task_id: str) -> None:
        self._active.pop(task_id, None)

    # ---------------------------------------------------------------- 快照

    def snapshot_write(self, task_id: str, path: str, new_content: str | None) -> bool:
        """写文件前调用：记下这个文件"改之前"长什么样。

        new_content=None 表示是删除操作。
        """
        cid = self._active.get(task_id)
        if not cid:
            return False
        try:
            target = _resolve(path)
        except Exception:
            return False
        # 不快照禁区与允许范围外的路径
        if not self._in_scope(target):
            return False
        # 单个检查点的文件数封顶，防止一轮改几百个文件把库撑爆
        try:
            if len(self.store.checkpoint_files(cid)) >= MAX_FILES_PER_CHECKPOINT:
                return False
        except Exception:
            return False

        existed = target.exists()
        before: str | None = None
        if existed:
            try:
                if target.stat().st_size > MAX_SNAPSHOT_BYTES:
                    # 太大了只记路径，内容不存 —— 回退时会被告知无法自动还原
                    self.store.record_checkpoint_file(cid, str(target), None, None, True)
                    return True
                before = target.read_text("utf-8", errors="replace")
            except Exception:
                before = None

        try:
            self.store.record_checkpoint_file(
                cid, str(target), before, new_content, existed
            )
        except Exception:
            return False
        return True

    def _in_scope(self, target: Path) -> bool:
        for root in self.allowed_roots:
            try:
                if target == root or root in target.parents:
                    return True
            except Exception:
                continue
        return False

    def snapshot_move(self, task_id: str, src: str, dst: str) -> bool:
        """移动/重命名前存一份快照，供 rewind 还原。

        移动要记**两个路径**，缺一不可：
          源   —— 移动后它不存在了，回退要把内容放回去；
          目标 —— 移动后它存在了，回退要把它删掉（它本来可能不存在）。

        只记源不行：回退后源回来了、目标还留着 = 文件变成两份。
        只记目标不行：回退删掉目标，源却没了 = 文件丢了。

        目录只能做到一半：目录没法用文本快照还原内容，
        所以源目录记「存在但内容不可还原」（回退时会跳过），
        目标目录记「本来不存在」—— 回退时整个删掉，这部分是准的。
        """
        cid = self._active.get(task_id)
        if not cid:
            return False
        try:
            source = _resolve(src)
            target = _resolve(dst)
        except Exception:
            return False
        if not self._in_scope(source) or not self._in_scope(target):
            return False
        try:
            if len(self.store.checkpoint_files(cid)) >= MAX_FILES_PER_CHECKPOINT:
                return False
        except Exception:
            return False

        # ---- 目标：移动前「本来不存在」→ 回退时删掉 ----
        # 目标本来就存在（覆盖式移动）的话不能这么记 ——
        # 那样回退会删掉用户原有的文件。用 existed=True + 内容快照兜住。
        if target.exists():
            before = None
            if target.is_file():
                try:
                    if target.stat().st_size <= MAX_SNAPSHOT_BYTES:
                        before = target.read_text("utf-8", errors="replace")
                except Exception:
                    before = None
            try:
                self.store.record_checkpoint_file(cid, str(target), before, None, True)
            except Exception:
                return False
        else:
            try:
                self.store.record_checkpoint_file(cid, str(target), None, None, False)
            except Exception:
                return False

        # ---- 源：移动后它没了 → 回退时内容放回来 ----
        if source.is_dir():
            # 目录的内容没法用文本快照存。记「存在但不可还原」，
            # 回退时会跳过这一条（用户会看到 skipped 提示）。
            try:
                self.store.record_checkpoint_file(cid, str(source), None, None, True)
            except Exception:
                pass
            return True
        before = None
        try:
            if source.stat().st_size <= MAX_SNAPSHOT_BYTES:
                before = source.read_text("utf-8", errors="replace")
        except Exception:
            before = None
        try:
            self.store.record_checkpoint_file(cid, str(source), before, None, True)
        except Exception:
            return False
        return True

    # ---------------------------------------------------------------- 回退

    def rewind(
        self,
        checkpoint_id: str,
        scope: str = SCOPE_BOTH,
        skip_missing: bool = False,
    ) -> dict[str, Any]:
        """回退到一个检查点。

        scope 取 SCOPE_CODE / SCOPE_CONVERSATION / SCOPE_BOTH。
        返回 {"ok", "code": [...], "conversation": n, "errors": [...], "skipped": [...]}。
        """
        cp = self.store.get_checkpoint(checkpoint_id)
        if not cp:
            return {"ok": False, "content": f"没有这个检查点：{checkpoint_id}"}
        if scope not in (SCOPE_CODE, SCOPE_CONVERSATION, SCOPE_BOTH):
            return {"ok": False, "content": f"scope 只能是 {SCOPE_CODE} / {SCOPE_CONVERSATION} / {SCOPE_BOTH}"}

        task_id = str(cp["task_id"])
        restored: list[str] = []
        removed: list[str] = []
        skipped: list[str] = []
        errors: list[str] = []

        # ---- 代码 ----
        if scope in (SCOPE_CODE, SCOPE_BOTH):
            for item in self.store.files_after(checkpoint_id):
                path = item["path"]
                try:
                    target = _resolve(path)
                except Exception:
                    errors.append(f"{path}: 路径无法解析")
                    continue
                if not self._in_scope(target):
                    # 回退也不能写到允许范围外去
                    skipped.append(path)
                    continue
                try:
                    if item.get("existed") and item.get("before") is not None:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text(item["before"], encoding="utf-8")
                        restored.append(path)
                    elif not item.get("existed"):
                        # 本来不存在的文件：回退就是删掉
                        if target.exists():
                            if target.is_dir():
                                shutil.rmtree(target)
                            else:
                                target.unlink()
                            removed.append(path)
                        else:
                            restored.append(path)
                    else:
                        # existed 但 before 是 None —— 文件太大没存内容
                        if skip_missing:
                            skipped.append(path)
                        else:
                            skipped.append(path)
                except Exception as exc:
                    errors.append(f"{path}: {exc}")

        # ---- 对话 ----
        trimmed = 0
        steps_trimmed = 0
        if scope in (SCOPE_CONVERSATION, SCOPE_BOTH):
            trimmed = self.store.trim_messages(task_id, int(cp.get("msg_count") or 0))
            # 执行记录一起裁 —— 留着那些步骤，界面右栏会显示一堆
            # 「已经不存在」的工具调用，用户会以为回退没生效。
            steps_trimmed = self.store.trim_steps(task_id, int(cp.get("step_count") or 0))

        # 回退后这个检查点就不再是活跃边界了
        if self._active.get(task_id) == checkpoint_id:
            self._active.pop(task_id, None)

        return {
            "ok": True,
            "content": {
                "scope": scope,
                "restored": restored,
                "removed": removed,
                "skipped": skipped,
                "errors": errors,
                "messages_removed": trimmed,
                "steps_removed": steps_trimmed,
            },
        }

    def status(self, task_id: str) -> dict[str, Any]:
        """列出这个任务的检查点，给界面做时间线用。"""
        items = self.store.list_checkpoints(task_id)
        current = self._active.get(task_id)
        out = []
        for cp in items:
            files = 0
            try:
                files = len(self.store.files_after(cp["id"]))
            except Exception:
                pass
            out.append({
                "id": cp["id"],
                "seq": cp["seq"],
                "label": cp.get("label") or "",
                "created_at": cp.get("created_at"),
                "files": files,
                "msg_count": int(cp.get("msg_count") or 0),
                "current": cp["id"] == current,
            })
        return {"task_id": task_id, "current": current, "checkpoints": out}

    def prune(self) -> int:
        """清过期检查点。"""
        return self.store.prune_checkpoints(RETENTION_DAYS)