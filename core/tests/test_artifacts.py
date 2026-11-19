"""多模态交付（HTML / PDF / 图片）与产物面板判定的回归测试。

锁住四件事：
1. 三种格式都**真的落盘**，且是可读回的真文件（不是贴一段代码冒充产物）；
2. 用户输入被 HTML 转义，模型生成的网页不能直接注入脚本；
3. 产物按结果元数据判定，只读工具不会混进产物面板；
4. step.done 事件带 artifact_path 与 args（旧实现只发 args 的影子，前端取不到）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from oryx import artifacts
from oryx.config import DEFAULT_CONFIG, Config
from oryx.events import EventBus
from oryx.features import FEATURE_GATES
from oryx.loop import AgentRuntime
from oryx.providers import ToolCall
from oryx.store import Store
from oryx.tools import MUTATING_TOOLS
from oryx import autoclass

SECTIONS = [
    {
        "heading": "本季度概览",
        "paragraphs": ["这是一段中文说明，用来验证交付链路真的能落盘。"],
        "bullets": ["新增用户 1280 人", "环比 +12%"],
        "table": {"headers": ["指标", "本周", "环比"],
                  "rows": [["新用户", "1280", "+12%"], ["留存", "63%", "+2pt"]]},
        "chart": {"type": "bar", "labels": ["一月", "二月", "三月"],
                  "values": [120, 180, 150], "title": "月度趋势"},
        "note": "数据来自本机样例",
    }
]


def test_html_is_self_contained_and_escaped(tmp_path: Path):
    path = tmp_path / "report.html"
    out = artifacts.write_html(str(path), "季度报告 <script>", SECTIONS, "内部资料")
    assert out["ok"] and out["bytes"] > 0 and out["kind"] == "html"
    text = path.read_text("utf-8")
    assert text.startswith("<!doctype html>")
    assert "季度报告" in text and "<script>" not in text
    # 转义生效，且没有外链依赖（离线可打开）
    assert "&lt;script&gt;" in text
    # SVG 的 xmlns 是命名空间 URI，不是外链资源；这里只禁真正的远程加载
    assert "<script src=" not in text and "<link " not in text
    assert 'src="http' not in text and 'href="http' not in text
    assert "<svg" in text and "<table" in text


def test_pdf_is_a_real_pdf(tmp_path: Path):
    path = tmp_path / "report.pdf"
    out = artifacts.write_pdf(str(path), "季度报告", SECTIONS, "内部资料")
    assert out["ok"], out.get("error")
    assert path.read_bytes().startswith(b"%PDF-")
    assert out["pages"] >= 2 and out["bytes"] > 1000


def test_image_png_and_svg(tmp_path: Path):
    chart = {"type": "pie", "labels": ["A", "B", "C"], "values": [3, 5, 2], "title": "占比"}
    png = artifacts.write_image(str(tmp_path / "chart.png"), chart, "占比图")
    svg = artifacts.write_image(str(tmp_path / "chart.svg"), chart, "占比图")
    assert png["ok"] and (tmp_path / "chart.png").read_bytes().startswith(b"\x89PNG")
    assert svg["ok"] and "<svg" in (tmp_path / "chart.svg").read_text("utf-8")
    assert svg["points"] == 3


def test_bad_chart_values_do_not_crash(tmp_path: Path):
    """模型给了脏数据也要出一张能打开的图，而不是抛异常中断任务。"""
    chart = {"type": "bar", "labels": ["A", "B"], "values": ["oops", 12, None]}
    out = artifacts.write_image(str(tmp_path / "dirty.png"), chart)
    assert out["ok"] and out["points"] == 1


def test_extract_artifact_path_uses_result_metadata():
    assert artifacts.extract_artifact_path(
        "html_write", {"ok": True, "content": {"path": "D:/a.html", "bytes": 123}}
    ) == "D:/a.html"
    # 只读结果不该被当成产物
    assert artifacts.extract_artifact_path(
        "xlsx_read", {"ok": True, "content": {"rows": [], "sheet": "Sheet1"}}
    ) == ""
    # 失败了也不收
    assert artifacts.extract_artifact_path(
        "pdf_write", {"ok": False, "content": "缺依赖"}
    ) == ""
    # 路径字段的别名
    assert artifacts.extract_artifact_path(
        "anything", {"ok": True, "content": {"image_path": "D:/a.png", "bytes": 10}}
    ) == "D:/a.png"


def test_new_tools_are_wired_everywhere(tmp_path: Path):
    """三个新工具必须在注册表 / 改动清单 / 开关 / auto 分类器里口径一致。"""
    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    runtime = AgentRuntime(Config(data), Store(tmp_path / "oryx.db"), EventBus())
    for name in ("html_write", "pdf_write", "image_write"):
        assert name in runtime.registry, f"{name} 没注册"
        assert name in MUTATING_TOOLS, f"{name} 会写文件却没登记为改动"
        assert name in FEATURE_GATES["EnableOffice"], f"{name} 没接特性开关"
        assert name in autoclass.RECOVERABLE_TOOLS, f"{name} 没纳入检查点豁免口径"


def test_step_done_carries_artifact_path_and_args(tmp_path: Path):
    """真实走一次 _handle_tool_call：事件里必须带产物路径和调用参数。

    旧实现里 step.done 完全不带 args，前端 `ev.args?.path` 永远是 undefined，
    产物面板的实时收集等于没接线。
    """
    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    data["forbidden_paths"] = []
    store = Store(tmp_path / "oryx.db")
    bus = EventBus()
    runtime = AgentRuntime(Config(data), store, bus)
    events: list[dict] = []
    bus.add_sync_sink(events.append)
    runtime._active_registry = runtime.registry
    runtime._active_interaction = "craft"
    task_id = store.create_task("出一份看板", "craft", "test")
    runtime._active_task_id = task_id
    path = tmp_path / "board.html"

    runtime._handle_tool_call(task_id, 1, ToolCall(
        id="c1", name="html_write",
        arguments={"path": str(path), "title": "看板", "sections": SECTIONS},
    ))
    done = [e for e in events if e.get("type") == "step.done"]
    assert done, "没有发 step.done 事件"
    assert done[0].get("artifact_path") == str(path)
    assert done[0].get("args", {}).get("path") == str(path)
    assert path.exists()


# ---------------------------------------------------------------- 预览接口
@pytest.fixture()
def srv(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient

    import oryx.config as config_mod
    import oryx.server as server_mod

    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config_mod, "CONFIG_PATH", home / "config.json")
    monkeypatch.setattr(config_mod, "FEATURES_PATH", home / "features.yaml")
    # /api/files/preview 只读 config，根本不碰 store —— 所以这里不换库。
    # 以前顺手裸赋值 server_mod.store，结果 domains 路由（装配时闭包捕获了
    # 旧 store）与后续用例 from oryx.server import store 指向两个不同的库，
    # 让 test_domains.py 的会话改名报 404。配置改动也必须可自动恢复。
    monkeypatch.setitem(server_mod.config.data, "allowed_roots", [str(tmp_path)])
    monkeypatch.setitem(server_mod.config.data, "forbidden_paths", [])
    return TestClient(server_mod.app), tmp_path, server_mod.API_TOKEN


def test_preview_serves_html_with_sandbox_csp(srv):
    client, tmp, token = srv
    path = tmp / "board.html"
    path.write_text("<html><body>看板</body></html>", encoding="utf-8")
    r = client.get("/api/files/preview", params={"t": token, "path": str(path)})
    assert r.status_code == 200
    assert "看板" in r.text
    assert "sandbox" in r.headers.get("content-security-policy", "")
    assert r.headers.get("x-content-type-options") == "nosniff"


def test_preview_requires_token(srv):
    client, tmp, _ = srv
    path = tmp / "a.png"
    path.write_bytes(b"x")
    r = client.get("/api/files/preview", params={"path": str(path)})
    assert r.status_code == 401


def test_preview_rejects_outside_and_unsupported(srv):
    client, tmp, token = srv
    r = client.get("/api/files/preview",
                   params={"t": token, "path": "C:/Windows/win.ini"})
    assert r.status_code in (403, 404, 415)
    exe = tmp / "a.exe"
    exe.write_bytes(b"MZ")
    r = client.get("/api/files/preview", params={"t": token, "path": str(exe)})
    assert r.status_code == 415


def test_open_still_rejects_out_of_scope(srv):
    """抽了公共闸门口径后，旧的 /api/files/open 安全契约不能退化。"""
    client, _, token = srv
    r = client.post("/api/files/open", json={"path": "C:/Windows/win.ini"},
                    headers={"X-Oryx-Token": token, "X-Oryx-Request": "1"})
    assert r.status_code in (403, 404)
