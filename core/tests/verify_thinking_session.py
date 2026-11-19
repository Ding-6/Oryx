"""思考过程与对话续接的端到端验证（不依赖真实模型）。

起一个内核，把 provider 换成会返回思考内容 + 会话续接的假实现，
然后断言事件流与消息落库行为。
"""
from __future__ import annotations

import sys
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


from oryx.config import DEFAULT_CONFIG, Config  # noqa: E402
from oryx.events import EventBus  # noqa: E402
from oryx.loop import AgentRuntime  # noqa: E402
from oryx.providers import ProviderReply, ToolCall  # noqa: E402
from oryx.store import Store  # noqa: E402


class ThinkingProvider:
    """假 provider：每轮都返回一段思考 + 一段正文。"""

    def __init__(self) -> None:
        self.calls: list[list[dict]] = []

    def chat(self, system, messages, tools, on_text=None, max_tokens=4096, on_reasoning=None):
        self.calls.append(list(messages))
        if on_reasoning:
            on_reasoning("先想想：")
            on_reasoning("该怎么做")
        text = "这是回答"
        if on_text:
            on_text(text)
        return ProviderReply(text=text, tool_calls=[], stop_reason="end_turn")


def make(tmp_path: Path):
    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    data["forbidden_paths"] = []
    config = Config(data)
    store = Store(tmp_path / "oryx.db")
    bus = EventBus()
    runtime = AgentRuntime(config, store, bus)
    provider = ThinkingProvider()
    import oryx.loop as loop_mod

    loop_mod.build_provider = lambda cfg: provider  # type: ignore[assignment]
    events: list[dict] = []
    bus.add_sync_sink(events.append)
    return runtime, store, bus, provider, events


def test_reasoning_events(tmp_path):
    """思考过程要发出 START / CONTENT / END 三段事件。"""
    runtime, store, bus, provider, events = make(tmp_path)
    runtime.run("做点什么")

    kinds = [e["type"] for e in events]
    check("发出 REASONING_START", "REASONING_START" in kinds, str(kinds[:12]))
    check("发出 REASONING_CONTENT", "REASONING_CONTENT" in kinds, str(kinds[:12]))
    check("发出 REASONING_END", "REASONING_END" in kinds, str(kinds[:12]))

    contents = [e.get("delta") for e in events if e["type"] == "REASONING_CONTENT"]
    check("思考内容完整", "".join(c for c in contents if c) == "先想想：该怎么做",
          repr(contents))

    # 顺序：START 在前，END 在后
    if "REASONING_START" in kinds and "REASONING_END" in kinds:
        check("START 在 END 之前",
              kinds.index("REASONING_START") < kinds.index("REASONING_END"),
              str(kinds))


def test_reasoning_not_in_text(tmp_path):
    """思考内容不能混进正文事件里。"""
    runtime, store, bus, provider, events = make(tmp_path)
    runtime.run("做点什么")
    texts = [e.get("delta") for e in events if e["type"] == "TEXT_MESSAGE_CONTENT"]
    joined = "".join(t for t in texts if t)
    check("正文不含思考内容", "先想想" not in joined, repr(joined))


def test_session_continuation_same_task(tmp_path):
    """不点新建对话时，两轮落在**同一个** task 里。"""
    runtime, store, bus, provider, events = make(tmp_path)
    runtime.run("第一句")
    first = runtime._active_task_id
    runtime.run("第二句", session_id=first)
    second = runtime._active_task_id
    check("续接用同一个 task", first == second, f"{first} vs {second}")

    sessions = store.list_sessions(limit=10)
    check("会话列表只有一条", len(sessions) == 1, f"{len(sessions)} 条")
    if sessions:
        check("消息累积到同一条", sessions[0]["msg_count"] == 4,
              f"msg_count={sessions[0]['msg_count']}")


def test_session_continuation_carries_history(tmp_path):
    """续接时要把上一轮的历史带进模型上下文。"""
    runtime, store, bus, provider, events = make(tmp_path)
    runtime.run("第一句")
    task = runtime._active_task_id
    provider.calls.clear()
    runtime.run("第二句", session_id=task)
    check("调了模型", len(provider.calls) >= 1)
    if provider.calls:
        msgs = provider.calls[0]
        roles = [m.get("role") for m in msgs]
        check("上下文带上了历史", roles.count("user") >= 2, str(roles))
        check("历史里有上一轮的提问",
              any(m.get("content") == "第一句" for m in msgs
                  if isinstance(m.get("content"), str)),
              str([m.get("content") for m in msgs])[:200])


def test_new_session_isolated(tmp_path):
    """显式新建会话（不传 session_id）要开新 task。"""
    runtime, store, bus, provider, events = make(tmp_path)
    runtime.run("第一句")
    first = runtime._active_task_id
    runtime.run("第二句")
    second = runtime._active_task_id
    check("新建会话换了 task", first != second, f"{first} vs {second}")
    sessions = store.list_sessions(limit=10)
    check("会话列表两条", len(sessions) == 2, f"{len(sessions)} 条")


def _user_texts(msg: dict) -> str:
    """取出一条 user 消息的文本。

    content 有两种形态：历史轮次是纯字符串，本轮提问是 Anthropic 的块
    形态 [{"type": "text", "text": ...}]。两种都要能读出来。
    """
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)
    return ""


def test_history_not_duplicated(tmp_path):
    """续接时自己的这条提问不能出现两次。"""
    runtime, store, bus, provider, events = make(tmp_path)
    runtime.run("第一句")
    task = runtime._active_task_id
    provider.calls.clear()
    runtime.run("第二句", session_id=task)
    if provider.calls:
        users = [_user_texts(m) for m in provider.calls[0] if m.get("role") == "user"]
        check("本轮提问只出现一次", users.count("第二句") == 1, str(users))
        check("上一轮提问只出现一次", users.count("第一句") == 1, str(users))


def test_retry_does_not_duplicate_question(tmp_path):
    """重新生成：那条提问已经在库里了，内核不能再写一遍。

    否则库里会是 user, user, assistant 三条 —— 用户看到自己的话被复读了一次。
    """
    runtime, store, bus, provider, events = make(tmp_path)
    runtime.run("第一句")
    task = runtime._active_task_id

    # 模拟控制面的 prepare_retry：删掉回答，保留提问
    prep = store.prepare_retry(task)
    check("prepare_retry 成功", prep["ok"] is True, str(prep))
    check("prepare_retry 拿到提问", prep["goal"] == "第一句", str(prep))

    provider.calls.clear()
    runtime.run(prep["goal"], session_id=task, question_kept=True)

    rows = store.messages_for_task(task)
    roles = [r.get("role") for r in rows]
    check("提问没有重复", roles.count("user") == 1, str(roles))
    check("回答重新生成了一条", roles.count("assistant") == 1, str(roles))
    check("提问内容不变", rows[0].get("content") == "第一句", str(rows))

    # 模型上下文里这条提问也只能出现一次
    if provider.calls:
        users = [_user_texts(m) for m in provider.calls[0] if m.get("role") == "user"]
        check("上下文里提问只出现一次", users.count("第一句") == 1, str(users))


def test_retry_history_keeps_earlier_same_question(tmp_path):
    """用户问过一模一样的话时，重试只该动**最后**那条。"""
    runtime, store, bus, provider, events = make(tmp_path)
    runtime.run("同样的问题")
    runtime.run("同样的问题", session_id=runtime._active_task_id)
    task = runtime._active_task_id

    prep = store.prepare_retry(task)
    check("prepare_retry 成功", prep["ok"] is True, str(prep))
    provider.calls.clear()
    runtime.run(prep["goal"], session_id=task, question_kept=True)

    rows = store.messages_for_task(task)
    users = [r for r in rows if r.get("role") == "user"]
    check("提问总数不变（只删了回答）", len(users) == 2, str(len(users)))


def main() -> int:
    import shutil
    import tempfile

    # 每个用例独立的临时目录 —— 共享 db 会让会话列表串味。
    # 不用 TemporaryDirectory 的上下文管理器：SQLite 连接还开着，
    # 退出时删不掉文件会抛 PermissionError。
    for fn in (
        test_reasoning_events,
        test_reasoning_not_in_text,
        test_session_continuation_same_task,
        test_session_continuation_carries_history,
        test_new_session_isolated,
        test_history_not_duplicated,
        test_retry_does_not_duplicate_question,
        test_retry_history_keeps_earlier_same_question,
    ):
        print(f"\n[{fn.__name__}]")
        tmp = tempfile.mkdtemp(prefix="oryx_think_")
        try:
            fn(Path(tmp))
        except Exception as exc:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            FAILED.append(fn.__name__)
        finally:
            try:
                shutil.rmtree(tmp, ignore_errors=True)
            except OSError:
                pass

    print("\n" + "=" * 60)
    if FAILED:
        print(f"失败 {len(FAILED)} 项：{'、'.join(FAILED)}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
