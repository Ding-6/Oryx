"""模型调用与 MCP 调用的重试退避。

这两条链路以前**没有重试**：网络抖一下、网关 503、限流 429 ——
都是会自己好的故障，但一次失败整个任务就结束，用户什么也没得到。

两条硬规则要在测试里钉死：
1. **只重试瞬时失败**。参数错、密钥无效重试一百次也一样失败，
   反而让用户多等好几秒。
2. **已经吐过字就不重试**。流式响应中途断了再重试，
   用户会看到同一段内容出现两遍 —— 比直接失败更糟。
"""

from __future__ import annotations

import httpx
import pytest

from oryx import providers as prov_mod
from oryx.providers import (
    AnthropicProvider,
    OpenAICompatProvider,
    ProviderError,
    ProviderReply,
)


# ------------------------------------------------------------------ 分类


def test_retryable_status_set():
    """可重试的状态码只包含「等一会儿就好」的那些。"""
    assert 429 in prov_mod.RETRYABLE_STATUS
    assert 503 in prov_mod.RETRYABLE_STATUS
    assert 500 in prov_mod.RETRYABLE_STATUS
    # 参数错与鉴权失败重试没有意义
    assert 400 not in prov_mod.RETRYABLE_STATUS
    assert 401 not in prov_mod.RETRYABLE_STATUS
    assert 403 not in prov_mod.RETRYABLE_STATUS
    assert 404 not in prov_mod.RETRYABLE_STATUS


def test_backoff_grows():
    """退避要递增，但不能无限涨。"""
    first = prov_mod._backoff_delay(1)
    second = prov_mod._backoff_delay(2)
    assert 0 < first <= prov_mod.RETRY_BASE_DELAY * 1.2
    assert second > first
    # 有上限
    assert prov_mod._backoff_delay(20) <= prov_mod.RETRY_MAX_DELAY * 1.2


def test_provider_error_defaults():
    """ProviderError 默认不是瞬时的 —— 不显式标就不重试。"""
    err = ProviderError("参数错了")
    assert err.transient is False
    assert err.streamed is False


# ------------------------------------------------------------------ 重试行为


class _FlakyProvider(OpenAICompatProvider):
    """前 N 次调用抛瞬时错误，之后成功。"""

    def __init__(self, fail_times: int, transient: bool = True, streamed: bool = False):
        super().__init__({"name": "flaky", "model": "m", "base_url": "http://127.0.0.1:1/v1"})
        self.fail_times = fail_times
        self.transient = transient
        self.streamed = streamed
        self.calls = 0

    def _chat_once(self, url, headers, body, on_text, on_reasoning):
        self.calls += 1
        if self.calls <= self.fail_times:
            err = ProviderError("连接失败：临时故障", transient=self.transient)
            err.streamed = self.streamed
            raise err
        return ProviderReply(text="成功", tool_calls=[], stop_reason="end_turn")


def test_transient_failure_is_retried(monkeypatch):
    """瞬时失败会重试，并在后面成功。"""
    monkeypatch.setattr(prov_mod, "_backoff_delay", lambda attempt: 0.0)
    provider = _FlakyProvider(fail_times=2)
    reply = provider.chat("sys", [{"role": "user", "content": "hi"}], [])
    assert reply.text == "成功"
    assert provider.calls == 3, f"应重试到第 3 次，实际 {provider.calls} 次"


def test_permanent_failure_is_not_retried(monkeypatch):
    """非瞬时失败**不重试** —— 一次就抛。"""
    monkeypatch.setattr(prov_mod, "_backoff_delay", lambda attempt: 0.0)
    provider = _FlakyProvider(fail_times=5, transient=False)
    with pytest.raises(ProviderError):
        provider.chat("sys", [{"role": "user", "content": "hi"}], [])
    assert provider.calls == 1, f"不该重试，实际调了 {provider.calls} 次"


def test_streamed_failure_is_not_retried(monkeypatch):
    """已经吐过字就不重试 —— 否则用户会看到重复的半截回答。"""
    monkeypatch.setattr(prov_mod, "_backoff_delay", lambda attempt: 0.0)
    provider = _FlakyProvider(fail_times=5, transient=True, streamed=True)
    with pytest.raises(ProviderError):
        provider.chat("sys", [{"role": "user", "content": "hi"}], [])
    assert provider.calls == 1, f"不该重试，实际调了 {provider.calls} 次"


def test_retry_gives_up_after_max_attempts(monkeypatch):
    """一直失败就放弃，不能无限重试。"""
    monkeypatch.setattr(prov_mod, "_backoff_delay", lambda attempt: 0.0)
    provider = _FlakyProvider(fail_times=99)
    with pytest.raises(ProviderError):
        provider.chat("sys", [{"role": "user", "content": "hi"}], [])
    assert provider.calls == prov_mod.RETRY_MAX_ATTEMPTS


def test_both_protocols_share_the_retry_path():
    """两种协议都走同一套重试 —— 不能只改了一半。"""
    assert hasattr(AnthropicProvider, "_chat_with_retry")
    assert hasattr(OpenAICompatProvider, "_chat_with_retry")
    # 两个 provider 都实现了 _chat_once（真正发请求的那一步）
    assert hasattr(AnthropicProvider, "_chat_once")
    assert hasattr(OpenAICompatProvider, "_chat_once")


# ------------------------------------------------------------------ MCP 重试


def test_mcp_retry_uses_backoff(monkeypatch):
    """MCP 工具调用也带重试。"""
    from oryx import mcp as mcp_mod

    assert mcp_mod.RETRY_MAX_ATTEMPTS >= 2
    assert 429 in mcp_mod.RETRYABLE_STATUS
    assert 400 not in mcp_mod.RETRYABLE_STATUS


def test_mcp_retries_transient_and_gives_up(monkeypatch):
    """瞬时失败会重试；超过上限就放弃。"""
    from oryx import mcp as mcp_mod

    calls = {"n": 0}

    class _Client(mcp_mod.MCPClient):
        def __init__(self):
            super().__init__(name="t", command="x")

        def _request(self, method, params, timeout=None):
            calls["n"] += 1
            if calls["n"] < 3:
                raise mcp_mod.MCPError("MCP 调用超时：tools/call")
            return {"content": [{"type": "text", "text": "好了"}]}

    monkeypatch.setattr(mcp_mod.time, "sleep", lambda s: None)
    client = _Client()
    out = client.call_tool("x", {})
    assert calls["n"] == 3
    assert "好了" in out["content"]


def test_mcp_does_not_retry_when_server_exited(monkeypatch):
    """server 已经退出就不重试 —— 进程都没了，再连也连不上。"""
    from oryx import mcp as mcp_mod

    calls = {"n": 0}

    class _Client(mcp_mod.MCPClient):
        def __init__(self):
            super().__init__(name="t", command="x")

        def _request(self, method, params, timeout=None):
            calls["n"] += 1
            raise mcp_mod.MCPError("MCP server「t」已退出。stderr：boom")

    monkeypatch.setattr(mcp_mod.time, "sleep", lambda s: None)
    client = _Client()
    with pytest.raises(mcp_mod.MCPError):
        client.call_tool("x", {})
    assert calls["n"] == 1, f"不该重试，实际调了 {calls['n']} 次"


def test_mcp_does_not_retry_on_protocol_error(monkeypatch):
    """协议级错误（方法不存在）不重试。"""
    from oryx import mcp as mcp_mod

    calls = {"n": 0}

    class _Client(mcp_mod.MCPClient):
        def __init__(self):
            super().__init__(name="t", command="x")

        def _request(self, method, params, timeout=None):
            calls["n"] += 1
            raise mcp_mod.MCPError("tools/call 失败：{'code': -32601}")

    monkeypatch.setattr(mcp_mod.time, "sleep", lambda s: None)
    client = _Client()
    with pytest.raises(mcp_mod.MCPError):
        client.call_tool("x", {})
    assert calls["n"] == 1, f"不该重试，实际调了 {calls['n']} 次"
