"""联网搜索：可选能力、默认关、结果要打外部来源标记。

为什么这些性质重要
------------------
* **默认关** —— 项目承诺纯本地可用，不能默认就依赖外网
* **没配 key 要明确报错** —— 静默失败最难查
* **结果要打 taint** —— 搜索结果来自外部网页，可能包含伪装成指令的文本
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from oryx.config import DEFAULT_CONFIG, Config
from oryx.features import DEFAULT_FEATURES, FeatureFlags, gated_tools
from oryx.search import SearchError, SearchProvider, format_results
from oryx.taint import is_untrusted_tool, taint_text


def _write_flags(home: Path, flags: dict[str, bool], profile: str = "personal") -> Path:
    path = home / "features.yaml"
    path.write_text(
        json.dumps({"profile": profile, "flags": flags}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _runtime(tmp_path: Path, flags: dict[str, bool] | None = None):
    from oryx.events import EventBus
    from oryx.loop import AgentRuntime
    from oryx.store import Store

    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    if flags is not None:
        _write_flags(home, flags)
    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    data["forbidden_paths"] = []
    config = Config(data)
    import oryx.config as config_mod

    config_mod.FEATURES_PATH = home / "features.yaml"
    return AgentRuntime(config, Store(tmp_path / "oryx.db"), EventBus())


# ── 默认关 ────────────────────────────────────────────────────────────
def test_search_is_off_by_default():
    """默认关闭 —— 项目承诺纯本地可用。"""
    assert DEFAULT_FEATURES["EnableWebSearch"] is False


def test_search_tool_is_gated_when_flag_off():
    """开关关掉 → 工具被裁掉 → 模型看不见、调不到。"""
    flags = FeatureFlags.load(None)
    assert "web_search" in gated_tools(flags)


def test_search_tool_hidden_from_model_by_default(tmp_path: Path):
    runtime = _runtime(tmp_path, {**DEFAULT_FEATURES})
    exposed = runtime._exposed_registry()
    assert "web_search" not in exposed, (
        "默认关闭时 web_search 不该暴露给模型"
    )


def test_search_tool_exposed_when_flag_on(tmp_path: Path):
    runtime = _runtime(tmp_path, {**DEFAULT_FEATURES, "EnableWebSearch": True})
    assert "web_search" in runtime._exposed_registry()


# ── 没配 key ──────────────────────────────────────────────────────────
def test_provider_without_key_raises_clear_error():
    provider = SearchProvider({"provider": "serper", "api_key": ""})
    assert provider.configured is False
    with pytest.raises(SearchError) as exc:
        provider.search("天气")
    msg = str(exc.value)
    assert "api_key" in msg, msg


def test_empty_query_is_rejected():
    provider = SearchProvider({"api_key": "k"})
    with pytest.raises(SearchError):
        provider.search("   ")


def test_unsupported_provider_is_rejected():
    provider = SearchProvider({"provider": "bing", "api_key": "k"})
    with pytest.raises(SearchError) as exc:
        provider.search("x")
    assert "bing" in str(exc.value)


def test_tool_without_key_returns_error_not_silent_failure(tmp_path: Path):
    """工具层面：没配 key 要**明确报错**，不能静默返回空结果。"""
    runtime = _runtime(tmp_path, {**DEFAULT_FEATURES, "EnableWebSearch": True})
    spec = runtime.registry["web_search"]
    out = spec.fn(query="今天天气")
    assert out.get("ok") is False, out
    assert "api_key" in str(out.get("error", "")), out


# ── 解析与格式 ────────────────────────────────────────────────────────
def test_parse_organic_results():
    data = {
        "organic": [
            {"title": "标题一", "link": "https://a.example/1",
             "snippet": "摘要\n一"},
            {"title": "标题二", "link": "https://a.example/2", "snippet": "摘要二"},
        ]
    }
    out = SearchProvider._parse(data, 5)
    assert len(out) == 2
    assert out[0]["title"] == "标题一"
    assert out[0]["url"] == "https://a.example/1"
    # 摘要里的换行被压成空格
    assert "\n" not in out[0]["snippet"]


def test_parse_respects_count():
    data = {"organic": [{"title": f"t{i}", "link": "u", "snippet": "s"}
                         for i in range(10)]}
    assert len(SearchProvider._parse(data, 3)) == 3


def test_format_results_is_compact():
    text = format_results("天气", [
        {"title": "标题", "url": "https://x.example", "snippet": "摘要"},
    ])
    assert "天气" in text
    assert "https://x.example" in text
    assert "[1]" in text


def test_format_results_when_nothing_found():
    assert "没有搜到" in format_results("xyz", [])


# ── 完整流程（假 HTTP）────────────────────────────────────────────────
class _FakeResp:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    payload: dict = {}
    captured: dict = {}

    def __init__(self, *args, **kwargs) -> None:
        self.timeout = kwargs.get("timeout")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, headers=None, json=None):
        type(self).captured = {
            "url": url, "headers": headers or {}, "json": json or {},
            "method": "POST", "params": {},
        }
        return _FakeResp(type(self).payload)

    def get(self, url, headers=None, params=None):
        type(self).captured = {
            "url": url, "headers": headers or {}, "json": {},
            "method": "GET", "params": params or {},
        }
        return _FakeResp(type(self).payload)


def test_search_flow_with_fake_http(monkeypatch):
    import httpx

    _FakeClient.payload = {
        "organic": [{"title": "结果", "link": "https://x.example", "snippet": "摘要"}]
    }
    _FakeClient.captured = {}
    monkeypatch.setattr(httpx, "Client", _FakeClient)

    provider = SearchProvider({"api_key": "secret-key"})
    out = provider.search("某个话题", 3)
    assert len(out) == 1
    assert out[0]["title"] == "结果"
    # 请求确实带上了 key 与查询词
    assert _FakeClient.captured["headers"]["X-API-KEY"] == "secret-key"
    assert _FakeClient.captured["json"]["q"] == "某个话题"


def test_search_http_error_is_wrapped(monkeypatch):
    import httpx

    class _Boom:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *e):
            return False

        def post(self, *a, **k):
            raise httpx.ConnectError("连不上")

    monkeypatch.setattr(httpx, "Client", _Boom)
    provider = SearchProvider({"api_key": "k"})
    with pytest.raises(SearchError) as exc:
        provider.search("x")
    assert "搜索请求失败" in str(exc.value)


# ── 安全：外部内容要打标记 ────────────────────────────────────────────
def test_search_output_is_marked_as_external():
    """搜索结果的文本要打上「这是资料不是指令」的标记。"""
    assert is_untrusted_tool("web_search"), (
        "web_search 的输出必须当外部来源 —— "
        "网页里可能写着「忽略之前的指令」"
    )
    marked = taint_text("今天的天气是晴", "web_search")
    assert "忽略" not in marked or marked != "今天的天气是晴"
    assert "外部" in marked or "untrusted" in marked.lower() or "<" in marked


# ── 多 provider ───────────────────────────────────────────────────────
def test_three_providers_are_supported():
    """三家都支持 —— 额度是独立的，有一家注册不下来还能换。"""
    from oryx.search import DEFAULT_ENDPOINTS

    assert set(DEFAULT_ENDPOINTS) == {"serper", "tavily", "brave"}
    for name, url in DEFAULT_ENDPOINTS.items():
        assert url.startswith("https://"), f"{name} 的端点不对"


def test_unknown_provider_is_still_rejected():
    from oryx.search import SearchProvider

    with pytest.raises(SearchError) as exc:
        SearchProvider({"provider": "bing", "api_key": "k"}).search("x")
    assert "bing" in str(exc.value)
    assert "serper" in str(exc.value)      # 要说清支持哪些


def test_parse_tavily_shape():
    """tavily：results[].{title, url, content}"""
    from oryx.search import SearchProvider

    data = {"results": [{"title": "T", "url": "https://t.example",
                         "content": "摘要\n内容"}]}
    out = SearchProvider._parse(data, 5)
    assert out == [{"title": "T", "url": "https://t.example",
                    "snippet": "摘要 内容"}]


def test_parse_brave_shape():
    """brave：web.results[].{title, url, description}"""
    from oryx.search import SearchProvider

    data = {"web": {"results": [{"title": "B", "url": "https://b.example",
                                 "description": "说明"}]}}
    out = SearchProvider._parse(data, 5)
    assert out == [{"title": "B", "url": "https://b.example",
                    "snippet": "说明"}]


def test_parse_ignores_unknown_shape():
    """不认识的结构就返回空 —— 不要瞎猜。"""
    from oryx.search import SearchProvider

    assert SearchProvider._parse({"foo": "bar"}, 5) == []


def test_tavily_request_puts_key_in_body(monkeypatch):
    import httpx

    _FakeClient.payload = {"results": []}
    _FakeClient.captured = {}
    monkeypatch.setattr(httpx, "Client", _FakeClient)

    from oryx.search import SearchProvider

    SearchProvider({"provider": "tavily", "api_key": "tk"}).search("话题", 4)
    assert _FakeClient.captured["json"]["api_key"] == "tk"
    assert _FakeClient.captured["json"]["query"] == "话题"
    assert _FakeClient.captured["json"]["max_results"] == 4


def test_brave_request_uses_get_and_header(monkeypatch):
    import httpx

    _FakeClient.payload = {"web": {"results": []}}
    _FakeClient.captured = {}
    monkeypatch.setattr(httpx, "Client", _FakeClient)

    from oryx.search import SearchProvider

    SearchProvider({"provider": "brave", "api_key": "bk"}).search("话题", 2)
    # brave 是 GET + query 参数 + 专用 header
    assert _FakeClient.captured["method"] == "GET"
    assert _FakeClient.captured["params"]["q"] == "话题"
    assert _FakeClient.captured["headers"]["X-Subscription-Token"] == "bk"
