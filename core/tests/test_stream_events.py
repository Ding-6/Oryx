"""流式正文 / 思考过程的事件契约。

新前端 `apps/web/src/features/sessions/useStreamText.ts` 靠 SSE 把逐块文本
攒成一条会长的消息气泡。下面这些字段就是它的契约 —— 改个名、丢个字段、
delta 不连续，界面上就是「正文丢一半」或「一直转圈」。

后端链路：providers 逐 chunk 回调 → loop `text_delta` / `reasoning_delta`
→ 事件总线 → `/api/events` SSE。本文件钉住这条链路的两端。
"""

from __future__ import annotations

from pathlib import Path

from oryx import events as ev
from oryx.events import EventBus
from oryx.providers import ProviderReply


def _sink(bus: EventBus) -> list[dict]:
    seen: list[dict] = []
    bus.add_sync_sink(seen.append)
    return seen


def test_text_message_family_carries_ids_and_concatenable_deltas():
    """START/CONTENT/END 必须能靠 message_id 串成一条，delta 拼起来就是全文。"""
    bus = EventBus()
    seen = _sink(bus)

    start = bus.text_start("t1", "m1")
    bus.text_delta("t1", "m1", "你好")
    bus.text_delta("t1", "m1", "，世界")
    end = bus.text_end("t1", "m1")

    assert start["type"] == ev.TEXT_MESSAGE_START
    assert start["task_id"] == "t1"
    assert start["message_id"] == "m1"
    assert start["role"] == "assistant", "前端按 role 决定气泡样式"

    contents = [e for e in seen if e["type"] == ev.TEXT_MESSAGE_CONTENT]
    assert [e["delta"] for e in contents] == ["你好", "，世界"]
    assert "".join(e["delta"] for e in contents) == "你好，世界"
    assert all(e["task_id"] == "t1" for e in contents), "SSE 按 task_id 做账号/会话过滤"
    assert all(e["message_id"] == "m1" for e in contents)

    assert end["type"] == ev.TEXT_MESSAGE_END
    assert end["message_id"] == "m1"
    seqs = [e["seq"] for e in seen]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


def test_reasoning_is_a_separate_channel():
    """思考与正文是两条通道：顺序成组、不混进正文。"""
    bus = EventBus()
    seen = _sink(bus)

    bus.reasoning_start("t1")
    bus.reasoning_delta("t1", "先看")
    bus.reasoning_delta("t1", "一下目录")
    bus.reasoning_end("t1")
    bus.text_start("t1", "m1")
    bus.text_delta("t1", "m1", "答案")
    bus.text_end("t1", "m1")

    kinds = [e["type"] for e in seen]
    assert kinds.index(ev.REASONING_START) < kinds.index(ev.REASONING_CONTENT)
    assert kinds.index(ev.REASONING_CONTENT) < kinds.index(ev.REASONING_END)
    assert kinds.index(ev.REASONING_END) < kinds.index(ev.TEXT_MESSAGE_START)
    reasoning = [e for e in seen if e["type"] == ev.REASONING_CONTENT]
    assert "".join(e["delta"] for e in reasoning) == "先看一下目录"
    assert all(e["task_id"] == "t1" for e in reasoning)
    assert [e["delta"] for e in seen if e["type"] == ev.TEXT_MESSAGE_CONTENT] == ["答案"]


def test_text_deltas_survive_reconnect_replay():
    """断线重连用 ?since= 补发 —— delta 必须进环形缓冲，否则正文会缺一段。"""
    bus = EventBus()
    bus.text_start("t1", "m1")
    watermark = bus.current_seq
    bus.text_delta("t1", "m1", "A")
    bus.text_delta("t1", "m1", "B")
    bus.text_end("t1", "m1")

    replayed = bus.replay_since(watermark)
    assert [e["delta"] for e in replayed if e["type"] == ev.TEXT_MESSAGE_CONTENT] == ["A", "B"]
    assert replayed[-1]["type"] == ev.TEXT_MESSAGE_END


def test_provider_chunks_become_deltas_equal_to_stored_reply(tmp_path: Path):
    """前端拼出来的正文必须等于后端落库的正文。

    `_call_provider` 把 provider 的逐块回调转发成事件；模型返回的
    `reply.text` 随后被 `store.add_message` 落库。两者若不一致，
    界面上会出现「一段流式文字 + 一段不一样的历史消息」。
    """
    from oryx import loop as loop_mod
    from oryx.config import DEFAULT_CONFIG, Config
    from oryx.store import Store

    class FakeProvider:
        def chat(self, system, messages, schemas, on_text=None, **kwargs):
            for chunk in ("第一段", "第二段", "第三段"):
                if on_text:
                    on_text(chunk)
            return ProviderReply(text="第一段第二段第三段")

    data = dict(DEFAULT_CONFIG)
    data.update(
        allowed_roots=[str(tmp_path)],
        forbidden_paths=[],
        workspace=str(tmp_path),
        interaction="craft",
    )
    bus = EventBus()
    seen = _sink(bus)
    runtime = loop_mod.AgentRuntime(Config(data), Store(tmp_path / "oryx.db"), bus)

    reply = runtime._call_provider(FakeProvider(), "", [], [], "t1", "m1", 0)

    deltas = [e["delta"] for e in seen if e["type"] == ev.TEXT_MESSAGE_CONTENT]
    assert deltas == ["第一段", "第二段", "第三段"], "回调必须逐块转发，不能只发一次全文"
    assert "".join(deltas) == reply.text == "第一段第二段第三段"
    assert all(e["task_id"] == "t1" and e["message_id"] == "m1" for e in seen
               if e["type"] == ev.TEXT_MESSAGE_CONTENT)
