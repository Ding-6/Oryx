"""浏览器 Agent 回归：快照压缩、按编号点击、输入提交、表格提取、截图。

用本地 file:// 页面，不依赖外网，也不会因为网站改版而失败。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from oryx.browser import BrowserService, format_snapshot

pytestmark = pytest.mark.skipif(
    not __import__("importlib").util.find_spec("playwright"), reason="未安装 playwright"
)

PAGE = """<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>测试页面</title></head>
<body>
  <h1>产品列表</h1>
  <button id="btn" onclick="document.getElementById('out').innerText='已点击'">立即查看</button>
  <p id="out">未点击</p>
  <input id="q" placeholder="搜索关键词">
  <button id="go" onclick="document.getElementById('echo').innerText=document.getElementById('q').value">提交</button>
  <p id="echo"></p>
  <table id="t">
    <tr><th>名称</th><th>价格</th></tr>
    <tr><td>耳机</td><td>299</td></tr>
    <tr><td>键盘</td><td>499</td></tr>
  </table>
  <a href="/docs">帮助文档</a>
</body>
</html>
"""


@pytest.fixture(scope="module")
def service(tmp_path_factory):
    page_path = tmp_path_factory.mktemp("web") / "page.html"
    page_path.write_text(PAGE, encoding="utf-8")
    svc = BrowserService(headless=True)
    svc.call("open", url=page_path.as_uri())
    yield svc
    try:
        svc.call("close")
    except Exception:
        pass


def find_index(snapshot, text: str) -> int:
    for element in snapshot["elements"]:
        if text in element["text"]:
            return element["idx"]
    raise AssertionError(f"快照里找不到文本为「{text}」的元素：{[e['text'] for e in snapshot['elements']]}")


def test_snapshot_is_compact_and_structured(service):
    snap = service.call("snapshot")
    assert snap["title"] == "测试页面"
    assert "产品列表" in snap["text"]
    assert snap["elements"], "应当识别出可交互元素"
    assert find_index(snap, "立即查看") > 0

    rendered = format_snapshot(snap)
    assert "URL:" in rendered
    assert "[1]" in rendered
    assert len(rendered) < 4000, "快照必须压缩，不能把整页塞给模型"


def test_click_by_index_then_page_updates(service):
    snap = service.call("snapshot")
    idx = find_index(snap, "立即查看")
    after = service.call("click", index=idx)
    assert "已点击" in after["text"]


def test_type_and_submit(service):
    snap = service.call("snapshot")
    input_idx = next(e["idx"] for e in snap["elements"] if e["tag"] == "input")
    service.call("type", text="机械键盘", index=input_idx)
    snap2 = service.call("snapshot")
    go_idx = find_index(snap2, "提交")
    after = service.call("click", index=go_idx)
    assert "机械键盘" in after["text"]


def test_extract_table(service):
    data = service.call("extract", selector="#t", as_table=True)
    rows = data["rows"]
    assert rows[0] == ["名称", "价格"]
    assert ["耳机", "299"] in rows


def test_links(service):
    data = service.call("links")
    assert any("帮助文档" in link["text"] for link in data["links"])


def test_screenshot(service, tmp_path: Path):
    target = tmp_path / "shot.png"
    info = service.call("screenshot", path=str(target))
    assert target.exists()
    assert info["bytes"] > 0


def test_browser_policy_blocks_payment_click():
    """点「确认支付」必须被策略拦成 L3，不允许浏览器 Agent 自己付钱。"""
    from oryx.policy import PolicyEngine, Risk
    from oryx.tools import build_registry

    engine = PolicyEngine(allowed_roots=["D:\\tmp"], forbidden_paths=[])
    registry = build_registry()

    class FakeSpec:
        name = "browser_click"
        risk = Risk.L1_WRITE

    decision = engine.evaluate(FakeSpec(), {"index": 3, "target": "确认支付"})
    assert decision.forbidden

    safe = engine.evaluate(FakeSpec(), {"index": 3, "target": "查看更多"})
    assert not safe.forbidden
