"""检查点回滚的界面入口：后端接口 + 前端接线。

这一批验的是三件事：
  1. **seq 分配**（真 bug）：原来 loop.py 每轮都传 seq=0，
     而 files_after 按 seq>= 选文件 —— 回退到第 3 轮会把第 1 轮改的文件一起退掉。
  2. **回退范围**：Code / Conversation / CodeAndConversation 三态真的分开生效。
  3. **接口接线**：域路由挂上了、RPC 转发对了、非法参数被拒。

界面那一半靠 headless Chrome 真点（见 verify_ui_rewind.py）——
单测只能证明「函数自己能用」，证不了「按钮真的能点」。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from oryx.domains import checkpoint as ckpt_domain
from oryx.domains import session as session_domain
from oryx.ipc import IpcError


# ------------------------------------------------------------------ seq 分配


def test_checkpoint_seq_increments_per_round(tmp_path):
    """每轮打的检查点 seq 必须递增。

    这是回退正确性的地基：files_after 按 seq>= 选文件，
    seq 全是 0 就等于「回退到任何一轮 = 退掉整个会话的改动」。
    """
    from oryx.store import Store

    store = Store(tmp_path / "oryx.db")
    task = store.create_task("多轮", "craft", "test")
    a = store.create_checkpoint(task, 0, "第一轮")
    b = store.create_checkpoint(task, 0, "第二轮")
    c = store.create_checkpoint(task, 0, "第三轮")
    seqs = [cp["seq"] for cp in store.list_checkpoints(task)]
    assert seqs == sorted(seqs) and len(set(seqs)) == 3, f"seq 没递增：{seqs}"
    assert len({a, b, c}) == 3


def test_rewind_to_later_checkpoint_keeps_earlier_changes(tmp_path):
    """回退到第 2 个点，第 1 轮改的文件要留着。

    这是 seq bug 的行为级证据：seq 全为 0 时，
    回退到第 2 个点会把第 1 轮改的文件一起退掉。
    """
    from oryx.checkpoint import CheckpointService
    from oryx.store import Store

    store = Store(tmp_path / "oryx.db")
    ws = tmp_path / "ws"
    ws.mkdir()
    svc = CheckpointService(store, [str(ws)])
    task = store.create_task("两轮", "craft", "test")

    a = ws / "a.txt"
    a.write_text("v0", encoding="utf-8")

    cp1 = svc.mark(task, 0, "第一轮")
    svc.snapshot_write(task, str(a), "v1")
    a.write_text("v1", encoding="utf-8")

    cp2 = svc.mark(task, 0, "第二轮")
    svc.snapshot_write(task, str(a), "v2")
    a.write_text("v2", encoding="utf-8")

    # 回退到第 2 个点：a.txt 回到 v1
    result = svc.rewind(cp2, scope="Code")
    assert result["ok"] is True, str(result)
    assert a.read_text(encoding="utf-8") == "v1", f"没退到 v1：{a.read_text(encoding='utf-8')}"


def test_files_after_takes_the_earliest_snapshot(tmp_path):
    """同一个文件被改多次时，取**最早**那份 before。

    原来按 checkpoint_id（随机 hex）排序，"最早"其实是"任意"，
    退出来的内容是随机的。

    判据用 files_after(cp1)：它同时含 cp1 与 cp2 两份快照，
    去重必须留下 cp1 那份（before="原始"）。
    只看 files_after(cp2) 验不出来 —— 那本来就只有一份。
    """
    from oryx.store import Store

    store = Store(tmp_path / "oryx.db")
    task = store.create_task("多次改", "craft", "test")
    cp1 = store.create_checkpoint(task, 0, "第一轮")
    store.record_checkpoint_file(cp1, "f.txt", "原始", "第一版", True)
    cp2 = store.create_checkpoint(task, 0, "第二轮")
    store.record_checkpoint_file(cp2, "f.txt", "第一版", "第二版", True)

    files = store.files_after(cp1)
    assert len(files) == 1, str(files)
    assert files[0]["before"] == "原始", f"取的不是最早那份：{files[0]}"

    # 只退第二轮时，取的是第二轮那份
    files2 = store.files_after(cp2)
    assert len(files2) == 1
    assert files2[0]["before"] == "第一版", str(files2)


# ------------------------------------------------------------------ 回退三态


def _make_session(tmp_path):
    from oryx.checkpoint import CheckpointService
    from oryx.store import Store

    store = Store(tmp_path / "oryx.db")
    ws = tmp_path / "ws"
    ws.mkdir()
    svc = CheckpointService(store, [str(ws)])
    task = store.create_task("目标", "craft", "test")
    return svc, store, ws, task


def test_scope_code_only_restores_files_keeps_messages(tmp_path):
    svc, store, ws, task = _make_session(tmp_path)
    store.add_message(task, "user", "第一句")
    cid = svc.mark(task, 0, "")
    store.add_message(task, "assistant", "回答")

    f = ws / "x.txt"
    f.write_text("old", encoding="utf-8")
    svc.snapshot_write(task, str(f), "new")
    f.write_text("new", encoding="utf-8")

    r = svc.rewind(cid, scope="Code")
    assert r["ok"] is True
    assert f.read_text(encoding="utf-8") == "old"
    assert len(store.messages_for_task(task)) == 2, "只退代码不该动对话"


def test_scope_conversation_only_trims_messages_keeps_files(tmp_path):
    svc, store, ws, task = _make_session(tmp_path)
    store.add_message(task, "user", "第一句")
    cid = svc.mark(task, 0, "")
    store.add_message(task, "assistant", "回答1")
    store.add_message(task, "user", "第二句")
    store.add_message(task, "assistant", "回答2")

    f = ws / "y.txt"
    f.write_text("keep", encoding="utf-8")
    svc.snapshot_write(task, str(f), "changed")
    f.write_text("changed", encoding="utf-8")

    r = svc.rewind(cid, scope="Conversation")
    assert r["ok"] is True
    assert f.read_text(encoding="utf-8") == "changed", "只退对话不该动文件"
    assert r["content"]["messages_removed"] == 3, str(r["content"])
    remaining = store.messages_for_task(task)
    assert len(remaining) == 1 and remaining[0]["content"] == "第一句"


def test_scope_both_removes_messages_and_steps(tmp_path):
    """两者都退时，执行记录也要一起裁掉。

    留着那些步骤，界面右栏会显示一堆「已经不存在」的工具调用，
    用户会以为回退没生效。
    """
    svc, store, ws, task = _make_session(tmp_path)
    store.add_message(task, "user", "第一句")
    cid = svc.mark(task, 0, "")
    store.add_message(task, "assistant", "回答")
    store.create_step(task, 1, "fs_write", {"path": "x"}, 1)
    store.create_step(task, 2, "fs_read", {"path": "x"}, 0)

    f = ws / "z.txt"
    f.write_text("old", encoding="utf-8")
    svc.snapshot_write(task, str(f), "new")
    f.write_text("new", encoding="utf-8")

    r = svc.rewind(cid, scope="CodeAndConversation")
    assert r["ok"] is True
    assert f.read_text(encoding="utf-8") == "old"
    assert r["content"]["messages_removed"] == 1, str(r["content"])
    assert r["content"]["steps_removed"] == 2, str(r["content"])
    assert store.steps_for_task(task) == [], "步骤没被裁掉"


def test_rewind_rejects_unknown_scope(tmp_path):
    svc, store, ws, task = _make_session(tmp_path)
    cid = svc.mark(task, 0, "")
    r = svc.rewind(cid, scope="Bogus")
    assert r["ok"] is False
    assert "scope" in str(r["content"])


def test_rewind_unknown_checkpoint_fails(tmp_path):
    svc, store, ws, task = _make_session(tmp_path)
    r = svc.rewind("nope", scope="Code")
    assert r["ok"] is False


def test_status_lists_checkpoints_with_message_counts(tmp_path):
    """界面要靠 msg_count 把「回退到这里」按钮挂到对应的提问下面。"""
    svc, store, ws, task = _make_session(tmp_path)
    store.add_message(task, "user", "第一句")
    svc.mark(task, 0, "第一轮")
    store.add_message(task, "assistant", "回答")
    svc.mark(task, 0, "第二轮")

    st = svc.status(task)
    assert len(st["checkpoints"]) == 2, str(st)
    assert st["checkpoints"][0]["msg_count"] == 1, str(st)
    assert st["checkpoints"][1]["msg_count"] == 2, str(st)


# ------------------------------------------------------------------ 域层


class FakeAgent:
    """假 AgentLink。域层只做参数校验与 RPC 转发。"""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def call(self, method: str, timeout: float = 60.0, **params):
        self.calls.append((method, params))
        if method == "checkpoint.list":
            return {"task_id": params.get("task_id", ""), "current": "cp2",
                    "checkpoints": [{"id": "cp1", "seq": 0, "files": 1},
                                    {"id": "cp2", "seq": 1, "files": 0}]}
        if method == "checkpoint.rewind":
            if params.get("checkpoint") == "missing":
                return {"ok": False, "content": "没有这个检查点：missing"}
            return {"ok": True, "content": {"scope": params.get("scope"),
                                           "restored": ["a.txt"], "removed": [],
                                           "skipped": [], "errors": [],
                                           "messages_removed": 1, "steps_removed": 2}}
        raise AssertionError(f"未预期的 RPC：{method}")


def test_domain_lists_checkpoints():
    agent = FakeAgent()
    out = ckpt_domain.list_checkpoints(agent, "task1")
    assert len(out["checkpoints"]) == 2
    assert agent.calls[0][0] == "checkpoint.list"


def test_domain_rewind_forwards_scope():
    agent = FakeAgent()
    out = ckpt_domain.rewind_checkpoint(agent, "cp1", "Code")
    assert out["ok"] is True
    assert out["content"]["restored"] == ["a.txt"]
    method, params = agent.calls[-1]
    assert method == "checkpoint.rewind"
    assert params == {"checkpoint": "cp1", "scope": "Code"}


def test_domain_rewind_rejects_bad_scope():
    with pytest.raises(HTTPException) as exc:
        ckpt_domain.rewind_checkpoint(FakeAgent(), "cp1", "Bogus")
    assert exc.value.status_code == 400


def test_domain_rewind_missing_checkpoint_raises_404():
    with pytest.raises(HTTPException) as exc:
        ckpt_domain.rewind_checkpoint(FakeAgent(), "missing", "Code")
    assert exc.value.status_code == 404


def test_domain_rpc_error_maps_to_404():
    class GoneAgent(FakeAgent):
        def call(self, method: str, timeout: float = 60.0, **params):
            raise IpcError("没有这个检查点：cp_x")

    with pytest.raises(HTTPException) as exc:
        ckpt_domain.rewind_checkpoint(GoneAgent(), "cp_x", "Code")
    assert exc.value.status_code == 404


def test_domain_rewind_empty_id_rejected():
    with pytest.raises(HTTPException) as exc:
        ckpt_domain.rewind_checkpoint(FakeAgent(), "", "Code")
    assert exc.value.status_code == 400


# ------------------------------------------------------------------ 会话重命名


def test_rename_session_sets_a_custom_title(tmp_path):
    from oryx.store import Store

    store = Store(tmp_path / "oryx.db")
    task = store.create_task("原始目标", "craft", "test")
    assert store.rename_session(task, "我的新名字") is True

    listed = store.list_sessions()
    item = listed[0]
    assert item["title"] == "我的新名字", str(item)
    assert item["renamed"] is True


def test_rename_session_missing_raises(tmp_path):
    from oryx.store import Store

    store = Store(tmp_path / "oryx.db")
    assert store.rename_session("nope", "名字") is False


def test_rename_session_keeps_task_id_and_messages(tmp_path):
    """改名不能动会话 id —— 消息、步骤、检查点都按 id 关联。"""
    from oryx.store import Store

    store = Store(tmp_path / "oryx.db")
    task = store.create_task("原始目标", "craft", "test")
    store.add_message(task, "user", "你好")
    store.rename_session(task, "改过的名字")
    assert store.get_task(task)["id"] == task
    assert len(store.messages_for_task(task)) == 1


def test_rename_session_empty_title_falls_back_to_goal(tmp_path):
    """清空名字 = 撤掉自定义标题，退回用提问当标题。"""
    from oryx.store import Store

    store = Store(tmp_path / "oryx.db")
    task = store.create_task("原始目标", "craft", "test")
    store.rename_session(task, "改过的名字")
    store.rename_session(task, "")
    item = store.list_sessions()[0]
    assert item["title"] == "原始目标", str(item)
    assert item["renamed"] is False


def test_session_domain_rename(tmp_path):
    from oryx.store import Store

    store = Store(tmp_path / "oryx.db")
    task = store.create_task("原始目标", "craft", "test")
    out = session_domain.rename_session(store, task, "新名字")
    assert out == {"renamed": task, "title": "新名字"}


def test_session_domain_rename_missing_raises_404(tmp_path):
    from oryx.store import Store

    store = Store(tmp_path / "oryx.db")
    with pytest.raises(HTTPException) as exc:
        session_domain.rename_session(store, "nope", "新名字")
    assert exc.value.status_code == 404


# ------------------------------------------------------------------ 运行中守卫


class BusyAgent(FakeAgent):
    """内核说「这个会话正在运行」。"""

    def call(self, method: str, timeout: float = 60.0, **params):
        return {"ok": False, "content": "这个会话正在运行，先停止或等它结束再回退"}


def test_domain_rewind_busy_maps_to_400():
    """运行中回退要给出人话，而不是 500 或静默成功。"""
    with pytest.raises(HTTPException) as exc:
        ckpt_domain.rewind_checkpoint(BusyAgent(), "cp1", "Code")
    assert exc.value.status_code == 400
    assert "正在运行" in str(exc.value.detail)


class _FakeStore:
    def get_checkpoint(self, checkpoint_id):
        return {"id": checkpoint_id, "task_id": "task-1"}


class _NoRewind:
    def rewind(self, *args, **kwargs):
        raise AssertionError("运行中不该真的回退")


def test_agentd_blocks_rewind_while_task_running():
    """运行中边写文件边回退会把内容写乱 —— 必须拦在真正执行之前。

    只拦**目标会话自己**的运行：别的会话在跑不影响这里。
    """
    import types

    from oryx.agentd import AgentDaemon

    daemon = AgentDaemon.__new__(AgentDaemon)   # 不起进程，只借这个方法
    daemon.runtime = types.SimpleNamespace(
        store=_FakeStore(),
        checkpoints=_NoRewind(),
        is_task_running=lambda task_id: task_id == "task-1",
    )

    out = daemon.checkpoint_rewind("cp1", "Code")

    assert out["ok"] is False
    assert "正在运行" in out["content"]


def test_is_task_running_only_matches_own_task(tmp_path):
    from oryx.config import DEFAULT_CONFIG, Config
    from oryx.events import EventBus
    from oryx.loop import AgentRuntime
    from oryx.store import Store

    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    data["forbidden_paths"] = []
    data["workspace"] = str(tmp_path)
    runtime = AgentRuntime(Config(data), Store(tmp_path / "oryx.db"), EventBus())

    assert runtime.is_task_running("t1") is False
    runtime.mark_run_pending(task_id="t1")
    assert runtime.is_task_running("t1") is True, "已排队未开跑也要算运行中"
    assert runtime.is_task_running("t2") is False, "别的会话不能被算进来"
