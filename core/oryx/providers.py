"""模型提供方：Anthropic 原生协议 + OpenAI 兼容协议（DeepSeek / 混元 / Ollama）。

两条协议都做流式，并统一成同一个返回结构：
    ProviderReply(text: str, tool_calls: list[ToolCall], stop_reason: str)

内部消息格式采用 Anthropic 的 content block 结构，OpenAI 侧做转换。

httpx 是**延迟导入**的：它 import 一次要 0.6s，而内核启动的整条时间预算
只有 0.8s。放在模块顶层等于每次启动都先付 0.6s 的"入场费"，
哪怕这次启动根本不调模型。延迟导入后，只有真要发请求时才付这个成本。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable


class ProviderError(RuntimeError):
    """模型调用失败。

    ``transient`` 标记这个失败是不是**瞬时的**（网络抖动、服务端过载、限流）。
    只有瞬时失败才值得重试 —— 参数写错、密钥无效这类重试一百次也一样失败，
    反而把用户多等好几秒。
    """

    def __init__(self, message: str, transient: bool = False):
        super().__init__(message)
        self.transient = transient
        # 这次失败之前是否已经往上下文里吐过内容。
        # 吐过就不能重试 —— 重试会把同一段内容再吐一遍，用户看到重复的半截回答。
        self.streamed = False


# 可重试的 HTTP 状态码：服务端临时过载、限流、请求超时。
#
# 4xx 里只有这几个是"等一会儿再来一次就好"的；
# 400（参数错）/401（密钥无效）/403（无权限）重试没有意义。
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

# 重试策略：最多重试 2 次（总共 3 次尝试），指数退避。
#
# 退避基数 0.5 秒：0.5 → 1.0。加上抖动，避免多个请求同时重试撞在一起。
RETRY_MAX_ATTEMPTS = 3
RETRY_BASE_DELAY = 0.5
RETRY_MAX_DELAY = 4.0


def _backoff_delay(attempt: int) -> float:
    """第 attempt 次重试前该等多久（秒）。

    attempt 从 1 开始。带 ±20% 抖动 —— 固定间隔会让并发请求同时重试。
    """
    import random

    base = min(RETRY_BASE_DELAY * (2 ** (attempt - 1)), RETRY_MAX_DELAY)
    return base * random.uniform(0.8, 1.2)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ProviderReply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=dict)
    # 模型的思考过程（reasoning / thinking）。不同厂商字段名不一样：
    # Anthropic 是 content block type=thinking，OpenAI 兼容是
    # delta.reasoning_content 或 delta.reasoning。
    reasoning: str = ""


TextCallback = Callable[[str], None]
# 思考过程的流式回调。签名与 TextCallback 一样，但内容走另一条通道。
ReasoningCallback = Callable[[str], None]


class BaseProvider:
    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.name = cfg.get("name", "unknown")
        self.model = cfg.get("model", "")
        self.base_url = (cfg.get("base_url") or "").rstrip("/")
        self.api_key = cfg.get("api_key") or ""

    def chat(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        on_text: TextCallback | None = None,
        max_tokens: int = 4096,
        on_reasoning: ReasoningCallback | None = None,
    ) -> ProviderReply:
        raise NotImplementedError

    def _chat_with_retry(self, attempt: Callable[[], ProviderReply]) -> ProviderReply:
        """跑一次模型调用，瞬时失败就退避重试。

        为什么需要：
        网络抖一下、网关返回 503、限流 429 —— 这些都是**会自己好**的故障。
        以前一次失败整个任务就结束，用户看到的是「模型调用失败」，
        什么也没得到。现在会自动重试，用户根本感知不到这次抖动。

        两条硬规则：
        1. **只重试瞬时失败**。参数写错、密钥无效重试一百次也一样失败，
           反而让用户多等好几秒。
        2. **已经吐过字就不重试**。流式响应中途断了，重试会把同一段内容
           再吐一遍 —— 用户看到重复的半截回答，比直接失败更糟。
        """
        last: ProviderError | None = None
        for attempt_no in range(1, RETRY_MAX_ATTEMPTS + 1):
            try:
                return attempt()
            except ProviderError as exc:
                last = exc
                if not exc.transient:
                    raise
                # 已经吐过内容 → 不能重试（会重复输出）
                if getattr(exc, "streamed", False):
                    raise
                if attempt_no >= RETRY_MAX_ATTEMPTS:
                    raise
                delay = _backoff_delay(attempt_no)
                try:
                    time.sleep(delay)
                except Exception:
                    raise
        # 理论上到不了这里（最后一次失败会 raise），但留个兜底
        raise last or ProviderError("模型调用失败")


class AnthropicProvider(BaseProvider):
    def chat(self, system, messages, tools, on_text=None, max_tokens=4096,
             on_reasoning=None) -> ProviderReply:
        import httpx

        if not self.api_key:
            raise ProviderError(
                f"provider「{self.name}」缺少 api_key，请在配置文件中填写"
            )
        url = f"{self.base_url or 'https://api.anthropic.com'}/v1/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
            "stream": True,
        }
        if tools:
            body["tools"] = tools

        return self._chat_with_retry(
            lambda: self._chat_once(url, headers, body, on_text, on_reasoning)
        )

    def _chat_once(self, url, headers, body, on_text, on_reasoning) -> ProviderReply:
        import httpx

        reply = ProviderReply()
        try:
            with httpx.Client(timeout=120.0) as client:
                with client.stream("POST", url, headers=headers, json=body) as resp:
                    if resp.status_code >= 400:
                        detail = resp.read().decode("utf-8", errors="replace")[:1000]
                        raise ProviderError(
                            f"Anthropic 返回 {resp.status_code}：{detail}",
                            transient=resp.status_code in RETRYABLE_STATUS,
                        )
                    self._consume(resp.iter_lines(), reply, on_text, on_reasoning)
        except httpx.HTTPError as exc:
            # 已经吐过字就不该重试 —— 重试会把同一段内容再吐一遍。
            # 用 streamed 标记把这件事告诉重试层。
            error = ProviderError(f"连接 Anthropic 失败：{exc}", transient=True)
            error.streamed = bool(reply.text or reply.reasoning)
            raise error from exc
        return reply

    @staticmethod
    def _consume(lines: Iterable[str], reply: ProviderReply, on_text: TextCallback | None,
                 on_reasoning: ReasoningCallback | None = None) -> None:
        current: dict[str, Any] | None = None
        partial_json = ""
        in_thinking = False
        for line in lines:
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue

            etype = event.get("type")
            if etype == "content_block_start":
                block = event.get("content_block", {})
                btype = block.get("type")
                if btype == "tool_use":
                    current = {"id": block.get("id"), "name": block.get("name"), "json": ""}
                    partial_json = ""
                elif btype == "thinking":
                    # 扩展思考：Anthropic 会先给 signature，再给 thinking_delta
                    in_thinking = True
            elif etype == "content_block_delta":
                delta = event.get("delta", {})
                dtype = delta.get("type")
                if dtype == "text_delta":
                    chunk = delta.get("text", "")
                    reply.text += chunk
                    if on_text and chunk:
                        on_text(chunk)
                elif dtype == "thinking_delta":
                    chunk = delta.get("thinking", "")
                    reply.reasoning += chunk
                    if on_reasoning and chunk:
                        on_reasoning(chunk)
                elif dtype == "input_json_delta":
                    partial_json += delta.get("partial_json", "")
            elif etype == "content_block_stop":
                in_thinking = False
                if current is not None:
                    raw = partial_json or current.get("json") or "{}"
                    try:
                        args = json.loads(raw)
                    except json.JSONDecodeError:
                        args = {}
                    reply.tool_calls.append(
                        ToolCall(id=current["id"], name=current["name"], arguments=args)
                    )
                    current = None
                    partial_json = ""
            elif etype == "message_start":
                usage = (event.get("message") or {}).get("usage") or {}
                for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
                    if key in usage:
                        reply.usage[key] = int(usage[key])
            elif etype == "message_delta":
                reply.stop_reason = event.get("delta", {}).get("stop_reason", "")
                usage = event.get("usage") or {}
                if "output_tokens" in usage:
                    reply.usage["output_tokens"] = int(usage["output_tokens"])
            elif etype == "error":
                raise ProviderError(str(event.get("error")))


class OpenAICompatProvider(BaseProvider):
    """DeepSeek / 混元 / Ollama 等 OpenAI 兼容端点。"""

    def chat(self, system, messages, tools, on_text=None, max_tokens=4096,
             on_reasoning=None) -> ProviderReply:
        import httpx

        base = self.base_url or "http://127.0.0.1:11434/v1"
        url = f"{base}/chat/completions"
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"

        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}] + self._convert(messages),
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = [{"type": "function", "function": t} for t in self._convert_tools(tools)]

        return self._chat_with_retry(
            lambda: self._chat_once(url, headers, body, on_text, on_reasoning)
        )

    def _chat_once(self, url, headers, body, on_text, on_reasoning) -> ProviderReply:
        import httpx

        reply = ProviderReply()
        try:
            with httpx.Client(timeout=120.0) as client:
                with client.stream("POST", url, headers=headers, json=body) as resp:
                    if resp.status_code >= 400:
                        detail = resp.read().decode("utf-8", errors="replace")[:1000]
                        raise ProviderError(
                            f"{self.name} 返回 {resp.status_code}：{detail}",
                            transient=resp.status_code in RETRYABLE_STATUS,
                        )
                    self._consume(resp.iter_lines(), reply, on_text, on_reasoning)
        except httpx.HTTPError as exc:
            # 已经吐过字就不该重试 —— 重试会把同一段内容再吐一遍。
            error = ProviderError(f"连接 {self.name} 失败：{exc}", transient=True)
            error.streamed = bool(reply.text or reply.reasoning)
            raise error from exc
        return reply

    @staticmethod
    def _convert_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for tool in tools:
            out.append(
                {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
                }
            )
        return out

    @staticmethod
    def _convert(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            if isinstance(content, str):
                out.append({"role": role, "content": content})
                continue
            if role == "assistant":
                text_parts, tool_parts = [], []
                for block in content:
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        tool_parts.append(
                            {
                                "id": block.get("id"),
                                "type": "function",
                                "function": {
                                    "name": block.get("name"),
                                    "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                                },
                            }
                        )
                entry: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts) or None}
                if tool_parts:
                    entry["tool_calls"] = tool_parts
                out.append(entry)
            elif role == "user":
                text_parts, tool_results = [], []
                for block in content:
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_result":
                        tool_results.append(
                            {
                                "role": "tool",
                                "tool_call_id": block.get("tool_use_id"),
                                "content": str(block.get("content", "")),
                            }
                        )
                if text_parts:
                    out.append({"role": "user", "content": "".join(text_parts)})
                out.extend(tool_results)
        return out

    @staticmethod
    def _consume(lines: Iterable[str], reply: ProviderReply, on_text: TextCallback | None,
                 on_reasoning: ReasoningCallback | None = None) -> None:
        calls: dict[int, dict[str, Any]] = {}
        for line in lines:
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                break
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if event.get("usage"):
                usage = event["usage"]
                if "prompt_tokens" in usage:
                    reply.usage["input_tokens"] = int(usage["prompt_tokens"])
                if "completion_tokens" in usage:
                    reply.usage["output_tokens"] = int(usage["completion_tokens"])

            choices = event.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            delta = choice.get("delta") or {}
            chunk = delta.get("content")
            if chunk:
                reply.text += chunk
                if on_text:
                    on_text(chunk)
            # 思考过程：各家字段名不一样，全收。
            #   DeepSeek reasoner → reasoning_content
            #   Qwen3 / 部分实现 → reasoning
            #   兜底 → reasoning_content 的 list 形态
            think = delta.get("reasoning_content") or delta.get("reasoning")
            if isinstance(think, list):
                think = "".join(
                    part.get("text", "") for part in think if isinstance(part, dict)
                )
            if think:
                reply.reasoning += think
                if on_reasoning:
                    on_reasoning(think)
            for tc in delta.get("tool_calls") or []:
                index = tc.get("index", 0)
                slot = calls.setdefault(index, {"id": None, "name": "", "args": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] += fn["name"]
                if fn.get("arguments"):
                    slot["args"] += fn["arguments"]
            if choice.get("finish_reason"):
                reply.stop_reason = choice["finish_reason"]

        for index in sorted(calls):
            slot = calls[index]
            try:
                args = json.loads(slot["args"]) if slot["args"] else {}
            except json.JSONDecodeError:
                args = {}
            reply.tool_calls.append(
                ToolCall(id=slot["id"] or f"call_{index}", name=slot["name"], arguments=args)
            )


def build_provider(cfg: dict[str, Any]) -> BaseProvider:
    kind = (cfg.get("type") or "openai").lower()
    if kind == "anthropic":
        return AnthropicProvider(cfg)
    return OpenAICompatProvider(cfg)
