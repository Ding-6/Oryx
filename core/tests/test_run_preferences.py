"""单次运行的模式 / 交互 / 风格覆盖 —— 从 HTTP 入参到内核参数的闭环。

新前端在「新建运行」页允许为**这一次运行**单独选交互模式与回答风格，
把它们随 `POST /api/chat` 一起提交。这条链路有三处会悄悄跑偏：

  1. 入参没被后端认出来，静默回退到全局默认（用户以为选了问答，实际全权限）
  2. 值进了请求但没固化进 `config_snapshot.runtime`（会话详情显示错、续聊漂移）
  3. 续聊时把新值盖到已固化的快照上（同一会话前后两轮规则不一致）

所以这里直接跑 `run_chat` 的业务函数，用假内核接住 `chat.run` 的参数，
逐段钉住，不依赖真模型、也不起服务。
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from oryx.config import DEFAULT_CONFIG, Config
from oryx.domains.chat import ChatRequest, DispatchTracker, run_chat


class FakeAgent:
    """只接住参数，不真跑模型。"""

    def __init__(self) -> None:
        self.remembered: list[dict] = []
        self.calls: list[tuple[str, dict]] = []

    def remember_run(self, **params):
        self.remembered.append(params)

    async def call_async(self, name: str, **params):
        self.calls.append((name, params))
        return {"task_id": "task-1", "ok": True}


class FakeStore:
    def __init__(self, task: dict | None = None) -> None:
        self.task = task
        self.set_calls: list[tuple] = []

    def get_task(self, task_id, owner_id=None):
        return self.task

    def set_task_config(self, *args, **kwargs):
        self.set_calls.append((args, kwargs))


def make(config_overrides: dict | None = None, task: dict | None = None):
    data = dict(DEFAULT_CONFIG)
    data.update(config_overrides or {})
    return Config(data), FakeAgent(), FakeStore(task), DispatchTracker()


def launch(req: ChatRequest, agent: FakeAgent, config: Config,
           store: FakeStore, dispatch: DispatchTracker) -> dict:
    return asyncio.run(
        run_chat(agent, config, dispatch, req, store=store, owner_id="u1")
    )


def run_params(agent: FakeAgent) -> dict:
    names = [name for name, _ in agent.calls]
    assert names == ["chat.run"], f"内核调用序列不对：{names}"
    return agent.calls[0][1]


def test_explicit_overrides_reach_kernel_and_snapshot():
    """选了具体值：内核参数与会话快照都要用它们，不是全局默认。"""
    config, agent, store, dispatch = make({
        "interaction": "craft", "style": "professional", "mode": "efficiency",
    })
    req = ChatRequest(goal="整理需求", interaction="ask", style="efficient")
    result = launch(req, agent, config, store, dispatch)

    assert result["task_id"] == "task-1"
    params = run_params(agent)
    assert params["interaction"] == "ask"
    assert params["style"] == "efficient"
    assert params["config_snapshot"]["runtime"] == {
        "mode": "efficiency", "interaction": "ask", "style": "efficient",
    }
    # 崩溃补跑用的 remember_run 必须是同一份参数
    assert agent.remembered[0]["interaction"] == "ask"
    assert agent.remembered[0]["style"] == "efficient"


def test_omitted_overrides_fall_back_to_global_defaults():
    """不传（前端「跟随全局」）时回退 config 里的全局值。"""
    config, agent, store, dispatch = make({
        "interaction": "plan", "style": "friendly", "mode": "local",
    })
    launch(ChatRequest(goal="随便聊聊"), agent, config, store, dispatch)

    params = run_params(agent)
    assert params["interaction"] == "plan"
    assert params["style"] == "friendly"
    assert params["config_snapshot"]["runtime"] == {
        "mode": "local", "interaction": "plan", "style": "friendly",
    }


def test_blank_overrides_are_treated_as_unset():
    """前端若传了空串，不能被当成一次覆盖写进快照。"""
    config, agent, store, dispatch = make({"interaction": "craft", "style": "professional"})
    launch(ChatRequest(goal="x", interaction="", style=""), agent, config, store, dispatch)

    assert run_params(agent)["config_snapshot"]["runtime"]["interaction"] == "craft"


def test_resume_keeps_frozen_runtime():
    """续聊时快照是写死的：本次请求里带的覆盖不能改变这一会话的规则。"""
    frozen = {
        "version": 1,
        "agent_id": None, "expert_id": None, "agent": None, "expert": None,
        "runtime": {"mode": "efficiency", "interaction": "ask", "style": "efficient"},
    }
    task = {"id": "s1", "agent_id": None, "expert_id": None, "config_snapshot": frozen}
    config, agent, store, dispatch = make({
        "interaction": "bypass", "style": "creative",
    }, task=task)
    launch(ChatRequest(
        goal="继续", session_id="s1", interaction="bypass", style="creative",
    ), agent, config, store, dispatch)

    params = run_params(agent)
    assert params["interaction"] == "ask", "续聊不能把只读会话升级成开发者模式"
    assert params["style"] == "efficient"
    assert params["config_snapshot"] == frozen


def test_case_insensitive_values_are_normalized():
    """dontAsk 这类大小写混合的名字要规范化成内核 canonical 名。"""
    config, agent, store, dispatch = make()
    launch(ChatRequest(goal="x", interaction="DONTASK", style="EFFICIENT", mode="LOCAL"),
           agent, config, store, dispatch)
    params = run_params(agent)
    assert params["interaction"] == "dontAsk"
    assert params["style"] == "efficient"
    assert params["mode"] == "local"


def test_unknown_overrides_are_rejected_not_silently_downgraded():
    """权限相关的入参拼错必须报 422，绝不能静默回退成 craft。"""
    with pytest.raises(ValidationError):
        ChatRequest(goal="x", interaction="Askk")
    with pytest.raises(ValidationError):
        ChatRequest(goal="x", style="casual")
    with pytest.raises(ValidationError):
        ChatRequest(goal="x", mode="turbo")


def test_every_catalog_value_is_accepted_by_chat_request():
    """目录里列出来的每个选项，/api/chat 都得认（前后端不能各说各话）。"""
    from oryx.domains.preference import preference_catalog

    catalog = preference_catalog()
    for item in catalog["interactions"]:
        assert ChatRequest(goal="x", interaction=item["name"]).interaction == item["name"]
    for item in catalog["styles"]:
        assert ChatRequest(goal="x", style=item["name"]).style == item["name"]
    for item in catalog["compute_modes"]:
        assert ChatRequest(goal="x", mode=item["name"]).mode == item["name"]
