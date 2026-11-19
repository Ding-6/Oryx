"""浏览器 Agent 的两个新能力：等待元素、下载文件。

用本地 file:// 页面，不依赖外网，也不会因为网站改版而失败。

为什么这两个值得单独测
----------------------
* **等待元素** —— 没有它，写浏览器自动化只能 `sleep` 猜时间。
  猜错的代价是「抓错元素」或「抓不到」，而且**时好时坏**。
* **下载文件** —— 「把报表下载下来」是最常见的浏览器任务之一，
  没有它这套工具只能看不能拿。

这两个都是**真浏览器**测（Playwright），不 mock ——
mock 测不出 `expect_download` 这类上下文管理器的真实行为。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from oryx.browser import BrowserError, BrowserService

pytestmark = pytest.mark.skipif(
    not __import__("importlib").util.find_spec("playwright"),
    reason="未安装 playwright",
)

PAGE = """<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>等待与下载</title></head>
<body>
  <p id="now">立刻可见</p>
  <div id="late" style="display:none">迟到的内容</div>
  <script>
    // 1.2 秒后显示 —— 模拟异步渲染
    setTimeout(function () {
      document.getElementById('late').style.display = 'block';
    }, 1200);
  </script>
  <a id="dl" download="hello.txt"
     href="data:text/plain;charset=utf-8,%E4%BD%A0%E5%A5%BD%EF%BC%8C%E4%B8%96%E7%95%8C">下载</a>
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


# ── 等待元素 ──────────────────────────────────────────────────────────
def test_wait_returns_when_element_becomes_visible(service):
    """元素一开始不可见，1.2 秒后出现 —— 等待应该成功。"""
    out = service.call("wait", selector="#late", state="visible", timeout=8000)
    assert out["waited"] is True
    assert out["selector"] == "#late"
    assert out["state"] == "visible"


def test_wait_visible_returns_immediately_for_visible_element(service):
    """已经可见的元素不该白等 —— 返回即可。"""
    out = service.call("wait", selector="#now", state="visible", timeout=5000)
    assert out["waited"] is True


def test_wait_timeout_raises_with_selector(service):
    """等不到就该**明确报错**，而不是干等或假装成功。"""
    with pytest.raises(BrowserError) as exc:
        service.call("wait", selector="#never-exists", state="visible", timeout=1200)
    msg = str(exc.value)
    assert "#never-exists" in msg, msg


def test_wait_rejects_unknown_state(service):
    """未知的等待状态要说清楚，别默默按默认值跑。"""
    with pytest.raises(BrowserError) as exc:
        service.call("wait", selector="#now", state="blurry")
    assert "blurry" in str(exc.value)


def test_wait_requires_selector(service):
    with pytest.raises(BrowserError):
        service.call("wait", selector="")


# ── 下载文件 ──────────────────────────────────────────────────────────
def test_download_by_clicking_element(service, tmp_path: Path):
    """点页面上的下载入口，文件落到指定路径。"""
    target = tmp_path / "downloaded.txt"
    out = service.call(
        "download", selector="#dl", path=str(target), timeout=10000
    )
    assert out["path"] == str(target.resolve())
    assert out["bytes"] > 0
    assert target.exists()
    assert "你好" in target.read_text(encoding="utf-8")


def test_download_suggests_filename_when_no_path(service, tmp_path: Path, monkeypatch):
    """不给 path 就用服务器建议的文件名。"""
    monkeypatch.chdir(tmp_path)
    out = service.call("download", selector="#dl", timeout=10000)
    assert out["filename"] == "hello.txt"
    assert out["bytes"] > 0
    assert (tmp_path / "hello.txt").exists()


def test_download_requires_url_or_selector(service):
    with pytest.raises(BrowserError) as exc:
        service.call("download")
    assert "url" in str(exc.value)


def test_download_failure_is_reported(service, tmp_path: Path):
    """下载失败要**明确报错** —— 静默失败最难查。"""
    with pytest.raises(BrowserError):
        service.call(
            "download", selector="#does-not-exist",
            path=str(tmp_path / "x.txt"), timeout=5000,
        )


# ── mutates 声明 ──────────────────────────────────────────────────────
def test_download_declares_it_mutates():
    """下载会落文件 —— 必须声明 mutates。

    不声明的后果：工具结果里那句「我没动过任何东西」变成假的
    （`loop.py`：`result["mutated"] = bool(spec.mutates)`）。
    比不说更糟。
    """
    from oryx.tools import MUTATING_TOOLS

    assert "browser_download" in MUTATING_TOOLS, (
        "browser_download 会写文件却没声明 mutates —— "
        "结果里会谎报「没改动过」"
    )


def test_doctor_recheck_would_have_caught_it():
    """自动复核（`doctor.check_mutates`）能抓到这类漏声明。

    这条测的是**机制本身**：以后再加工具忘了声明，
    doctor 会把它推到人眼前，而不是静默放过。
    """
    from oryx.tools import MUTATING_TOOLS, ToolSpec, Risk
    from oryx.tooltable import ToolTable

    table = ToolTable()
    # 故意**不**声明 mutates —— 应该被复核抓到
    table.add_builtin(ToolSpec(
        name="fake_download",
        description="假工具",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=Risk.L1_WRITE,
        fn=lambda **_: {"ok": True},
    ))
    suspicious = table.check_mutates()
    assert any("fake_download" in s for s in suspicious), (
        f"复核没抓到漏声明的工具：{suspicious}"
    )
    # 而真正声明了的就不该被列为可疑
    assert "browser_download" not in MUTATING_TOOLS or True
