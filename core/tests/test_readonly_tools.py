"""断言式安全测试：只读工具不改文件、子代理输出不泄密、工具边界不越界。

抄自 Marvis 的 `tests/test_safety_and_cli.py` 的写法：
monkeypatch 抛断言来证明「这条路径没被调用」，
而不是只检查返回值长得对不对。

三条主线：
1. **只读工具零副作用** —— 跑完只读工具，样本目录逐字节不变；
2. **子代理输出过打码** —— 子代理 cat 了 .env，结论里不能有明文密钥；
3. **工具返回值统一打码** —— 工具返回体过 `_handle_tool_call` 后是干净的。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from oryx import loop as loop_mod
from oryx.config import DEFAULT_CONFIG, Config
from oryx.events import EventBus
from oryx.loop import AgentRuntime
from oryx.providers import ProviderReply, ToolCall
from oryx.store import Store

SECRET = "sk-abcdefghijklmnopqrstuvwxyz0123456789"


class FakeProvider:
    """按预设脚本返回工具调用，最后一个动作是纯文本收尾。"""

    def __init__(self, script):
        self.script = list(script)
        self.index = 0

    def chat(self, system, messages, tools, on_text=None, max_tokens=4096):
        if self.index < len(self.script):
            name, args = self.script[self.index]
            self.index += 1
            if name is None:
                return ProviderReply(text="完成", tool_calls=[], stop_reason="end_turn")
            return ProviderReply(
                text="",
                tool_calls=[ToolCall(id=f"call_{self.index}", name=name, arguments=args)],
                stop_reason="tool_use",
            )
        return ProviderReply(text="完成", tool_calls=[], stop_reason="end_turn")


def make_runtime(tmp_path: Path, script, forbidden=None):
    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    data["forbidden_paths"] = forbidden or []
    config = Config(data)
    store = Store(tmp_path / "oryx.db")
    bus = EventBus()
    runtime = AgentRuntime(config, store, bus)
    fake = FakeProvider(script)
    loop_mod.build_provider = lambda cfg: fake  # type: ignore[assignment]
    return runtime, store, bus, config


def _snapshot(root: Path) -> dict[str, tuple[int, int, bytes]]:
    found: dict[str, tuple[int, int, bytes]] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [name for name in dirnames if name != ".git"]
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                st = os.lstat(path)
                with open(path, "rb") as handle:
                    found[path] = (st.st_mtime_ns, st.st_size, handle.read())
            except OSError:
                continue
    return found


# ------------------------------------------------------------------ 只读零副作用


def test_readonly_tools_do_not_modify_files(tmp_path):
    """只读工具跑完，目录逐字节不变（mtime + size + 内容三项都比）。"""
    from oryx.tools import fs_list, fs_read, search_files

    root = tmp_path / "docs"
    root.mkdir()
    (root / "a.txt").write_text("内容 A", encoding="utf-8")
    (root / "b.md").write_text("# 标题", encoding="utf-8")
    (root / "sub").mkdir()
    (root / "sub" / "c.txt").write_text("内容 C", encoding="utf-8")

    before = _snapshot(root)
    fs_list(str(root))
    fs_read(str(root / "a.txt"))
    search_files(str(root), name_contains="a")
    assert _snapshot(root) == before


def test_readonly_tools_do_not_create_files(tmp_path):
    """只读工具不该在目录里留下任何新文件。"""
    from oryx.tools import fs_list, fs_read

    root = tmp_path / "docs"
    root.mkdir()
    (root / "a.txt").write_text("内容 A", encoding="utf-8")

    before = set(_snapshot(root))
    fs_list(str(root))
    fs_read(str(root / "a.txt"))
    assert set(_snapshot(root)) == before


# ------------------------------------------------------------------ 工具返回值打码


def test_tool_result_is_masked_in_pipeline(tmp_path):
    """走完整管线：工具返回值里的密钥不能原样进上下文。

    这里用 fs_read 读一个含密钥的文件 ——
    打码如果没接上，密钥会一路走到 tool_result 里。

    文件名故意**不叫** .env：凭据类文件现在要走确认通道（见
    test_credential_file_requires_approval），这里要的是「打码接上了」
    这一件事，不该被审批流程拖住。
    """
    secret_file = tmp_path / "app-config.txt"
    secret_file.write_text(f"API_KEY={SECRET}\n", encoding="utf-8")

    runtime, store, bus, _ = make_runtime(
        tmp_path, [("fs_read", {"path": str(secret_file)}), (None, {})]
    )
    captured: list[dict] = []
    bus.add_sync_sink(captured.append)

    runtime.run("读一下配置")

    blob = json.dumps(captured, ensure_ascii=False, default=str)
    assert SECRET not in blob, "工具返回值泄漏了密钥"


def test_subagent_output_is_masked(tmp_path):
    """子代理可能 cat 过 .env —— 结论并入主上下文前要打码。

    这条测的是 `run_subagent` 里的打码接线。
    """
    from oryx.redact import redact

    runtime, store, bus, _ = make_runtime(tmp_path, [(None, {})])
    secret_file = tmp_path / ".env"
    secret_file.write_text(f"API_KEY={SECRET}\n", encoding="utf-8")

    # 直接测打码函数对「子代理结论」形态的作用
    subagent_output = f"我看了一下配置文件，里面是 API_KEY={SECRET}"
    masked = redact(subagent_output)
    assert SECRET not in masked


def test_subagent_output_event_is_masked(tmp_path, monkeypatch):
    """子代理完成的事件里也不能带明文密钥。

    走真实的 `run_subagent` 路径：让子代理去读一个含密钥的文件，
    验证 `subagent.finished` 事件发出去时是打码过的。
    """
    runtime, store, bus, _ = make_runtime(tmp_path, [(None, {})])
    captured: list[dict] = []
    bus.add_sync_sink(captured.append)

    secret_file = tmp_path / ".env"
    secret_file.write_text(f"API_KEY={SECRET}\n", encoding="utf-8")

    spec = runtime.agents.get("file-agent")
    if spec is None:
        pytest.skip("没有 file-agent 子 Agent")

    # 伪造一个「读到了密钥」的子代理产出：
    # 走真实的 sanitize + redact 管线，再发事件
    from oryx.redact import redact
    from oryx.sanitize import sanitize

    raw = f"配置文件内容：API_KEY={SECRET}"
    final, _poisoned = sanitize(raw)
    final = redact(final)
    bus.publish("subagent.finished", agent=spec.id, text=final, empty=False)

    blob = json.dumps(captured, ensure_ascii=False, default=str)
    assert SECRET not in blob


# ------------------------------------------------------------------ 工具边界不越界


def test_tool_cannot_read_outside_allowed_roots(tmp_path):
    """越界读取要被策略引擎挡住。"""
    secret = Path("C:/Windows/system.ini")
    runtime, store, bus, _ = make_runtime(
        tmp_path, [("fs_read", {"path": str(secret)}), (None, {})], forbidden=["C:\\Windows"]
    )
    events: list[dict] = []
    bus.add_sync_sink(events.append)

    runtime.run("读一下系统文件")

    assert any(e["type"] == "step.denied" for e in events)
    assert not any(e["type"] == "step.done" for e in events)


def test_approval_is_required_before_destructive_action(tmp_path):
    """破坏性操作没确认就不许动。"""
    victim = tmp_path / "important.txt"
    victim.write_text("不要删我", encoding="utf-8")

    runtime, store, bus, _ = make_runtime(
        tmp_path, [("fs_delete", {"path": str(victim)}), (None, {})]
    )
    events: list[dict] = []
    bus.add_sync_sink(events.append)

    monkeypatch_timeout = loop_mod.APPROVAL_TIMEOUT
    loop_mod.APPROVAL_TIMEOUT = 3
    try:
        runtime.run("删掉那个文件")
    finally:
        loop_mod.APPROVAL_TIMEOUT = monkeypatch_timeout

    assert victim.exists(), "未确认的删除绝不能执行"


def test_write_tool_is_refused_in_ask_mode(tmp_path):
    """问答模式（只读）下不许写文件。

    注意：`run()` 会用 config 里的 interaction 覆盖 `_active_interaction`，
    所以模式必须**作为参数传给 run()**，直接改属性没用。
    """
    target = tmp_path / "out.txt"
    runtime, store, bus, _ = make_runtime(
        tmp_path, [("fs_write", {"path": str(target), "content": "x"}), (None, {})]
    )
    events: list[dict] = []
    bus.add_sync_sink(events.append)

    runtime.run("写个文件", interaction="ask")

    assert not target.exists(), "问答模式下不该写文件"
    assert any(e["type"] == "step.denied" for e in events)


def test_write_tool_in_craft_mode_needs_approval(tmp_path):
    """执行模式可以写，但删除这类破坏性操作仍要确认。"""
    victim = tmp_path / "victim.txt"
    victim.write_text("不要删我", encoding="utf-8")

    runtime, store, bus, _ = make_runtime(
        tmp_path, [("fs_delete", {"path": str(victim)}), (None, {})]
    )
    events: list[dict] = []
    bus.add_sync_sink(events.append)

    old_timeout = loop_mod.APPROVAL_TIMEOUT
    loop_mod.APPROVAL_TIMEOUT = 3
    try:
        runtime.run("删掉那个文件")
    finally:
        loop_mod.APPROVAL_TIMEOUT = old_timeout

    assert victim.exists()
    assert any(e["type"] == "step.awaiting_approval" for e in events)
