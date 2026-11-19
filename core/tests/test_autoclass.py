"""自动判定模式（auto）的分类器。

这条链路以前是**假的**：modes.py 声明了 use_classifier=True，
但全项目没有任何地方读它 —— 切到 auto 模式后行为与 craft 完全一样，
L2 照样弹确认卡。用户以为开了自动，实际没开。

这里锁住三件事：
1. 分类器本身的三档判定；
2. auto 模式下确实由分类器接管（而不是退化回 craft）；
3. fail-closed：判不准就退回确认，绝不默认放行。
"""

from __future__ import annotations

from pathlib import Path

from oryx import autoclass
from oryx.config import DEFAULT_CONFIG, Config
from oryx.events import EventBus
from oryx.loop import AgentRuntime
from oryx.providers import ProviderReply, ToolCall
from oryx.store import Store


class ScriptedProvider:
    """按脚本依次返回工具调用。"""

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
                tool_calls=[ToolCall(id=f"c{self.index}", name=name, arguments=args)],
                stop_reason="tool_use",
            )
        return ProviderReply(text="完成", tool_calls=[], stop_reason="end_turn")


def make_runtime(tmp_path: Path, script):
    """造一个 runtime，并把 provider 钉死。

    `loop_mod.build_provider` 是**模块级全局**，别的测试会替换它且从不还原。
    不钉的话本组用例会继承上一个测试留下的 provider ——
    那个 provider 可能去调 L2 工具，卡在 300 秒审批等待上。
    """
    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    data["forbidden_paths"] = []
    config = Config(data)
    store = Store(tmp_path / "oryx.db")
    bus = EventBus()
    runtime = AgentRuntime(config, store, bus)
    from oryx import loop as loop_mod

    loop_mod.build_provider = lambda cfg: ScriptedProvider(script)  # type: ignore[assignment]
    events: list[dict] = []
    bus.add_sync_sink(events.append)
    return runtime, store, bus, events


# ------------------------------------------------------------------ 分类器本身


def test_recoverable_tools_are_allowed():
    """有检查点快照兜底的动作 → 放行。"""
    for name in ("fs_write", "fs_delete", "fs_move", "docx_write", "xlsx_write", "pptx_write"):
        verdict, why = autoclass.classify(name, {}, risk=2)
        assert verdict == autoclass.ALLOW, f"{name} 应该放行，实际 {verdict}"
        assert why


def test_non_recoverable_destructive_is_ask():
    """不可回滚的破坏性动作 → 照常确认。"""
    for name in ("shell", "shell_sandbox", "term_run", "sys_set_wallpaper"):
        verdict, _ = autoclass.classify(name, {}, risk=2)
        assert verdict == autoclass.ASK, f"{name} 应该走确认，实际 {verdict}"


def test_readonly_is_allowed():
    """只读动作 → 放行。"""
    verdict, _ = autoclass.classify("fs_read", {}, risk=0)
    assert verdict == autoclass.ALLOW


def test_credential_path_is_denied():
    """凭据类文件 → 直接拒（没人能授权）。"""
    verdict, why = autoclass.classify(
        "fs_read", {}, risk=2, credential_paths=[r"D:\x\.env"]
    )
    assert verdict == autoclass.DENY
    assert "授权" in why


def test_human_only_tool_is_denied():
    """需要人拍板的工具 → 直接拒。"""
    verdict, why = autoclass.classify("remote_start", {}, risk=2)
    assert verdict == autoclass.DENY
    assert why


def test_classifier_never_raises_on_odd_input():
    """分类器不能抛 —— 抛了就等于没有分类器。"""
    for args in ({}, {"path": None}, {"path": 123}, {"root": ""}):
        verdict, why = autoclass.classify("fs_write", args, risk=2)
        assert verdict in (autoclass.ALLOW, autoclass.ASK, autoclass.DENY)
        assert why


# ------------------------------------------------------------------ 接线


def test_auto_mode_actually_differs_from_craft(tmp_path):
    """auto 模式下，删文件不该弹确认卡（craft 模式会弹）。

    这是本组测试最要紧的一条：它证明 auto 不再是 craft 的别名。
    """
    target = tmp_path / "a.txt"
    target.write_text("x", encoding="utf-8")

    runtime, store, bus, events = make_runtime(
        tmp_path, [("fs_delete", {"path": str(target)}), (None, {})]
    )
    runtime.run("删掉这个文件", interaction="auto")

    assert not target.exists(), "auto 模式下删文件没有执行"
    assert any(e["type"] == "step.done" for e in events), "没有成功执行的步骤"
    # 全程没有弹过确认卡 —— 这正是 auto 与 craft 的区别
    assert not any(e["type"] == "step.awaiting_approval" for e in events), (
        "auto 模式仍然弹了确认卡，说明分类器没接管"
    )


def test_auto_mode_emits_autoclass_event(tmp_path):
    """分类器的判定要发事件 —— 否则界面上看不出为什么没弹确认卡。"""
    target = tmp_path / "b.txt"
    target.write_text("x", encoding="utf-8")
    runtime, store, bus, events = make_runtime(
        tmp_path, [("fs_delete", {"path": str(target)}), (None, {})]
    )
    runtime.run("删掉", interaction="auto")

    verdicts = [e for e in events if e["type"] == "step.autoclass"]
    assert verdicts, "没发 step.autoclass 事件"
    assert verdicts[0]["verdict"] == autoclass.ALLOW
    assert verdicts[0]["reason"]


def test_auto_mode_denies_credential_read(tmp_path):
    """auto 模式读凭据文件 → 被分类器拒掉，且文件没被读。"""
    secret = tmp_path / ".env"
    secret.write_text("API_KEY=super-secret", encoding="utf-8")

    runtime, store, bus, events = make_runtime(
        tmp_path, [("fs_read", {"path": str(secret)}), (None, {})]
    )
    runtime.run("看看我的 .env", interaction="auto")

    denied = [e for e in events if e["type"] == "step.denied"]
    assert denied, "凭据文件没有被拒"
    assert "授权" in denied[0]["reason"]


def test_auto_mode_falls_back_to_ask_for_shell(tmp_path, monkeypatch):
    """判不准的破坏性动作 → 退回确认（fail-closed）。

    没人点确认时会等满 300 秒 —— 这里把审批等待换成「立刻拒绝」，
    既能验证走了确认通道，又不用真的等五分钟。
    """
    runtime, store, bus, events = make_runtime(
        tmp_path, [("shell", {"command": "echo hi"}), (None, {})]
    )
    monkeypatch.setattr(runtime, "wait_approval", lambda step_id: False)

    runtime.run("跑个命令", interaction="auto")

    verdicts = [e for e in events if e["type"] == "step.autoclass"]
    assert verdicts, "没发 step.autoclass 事件"
    assert verdicts[0]["verdict"] == autoclass.ASK
    # 走了确认通道（而不是直接执行）
    assert any(e["type"] == "step.awaiting_approval" for e in events), "没有走确认通道"
    assert not any(e["type"] == "step.done" for e in events), "命令被执行了"
