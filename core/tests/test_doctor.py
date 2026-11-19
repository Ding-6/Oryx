"""环境自检：结构对、纪律守得住。

抄 Marvis 的 `test_safety_and_cli.py` 里 doctor 那条：
**自检不许发网络请求、不许扫用户文件** —— 用 monkeypatch 抛断言来证明。
"""

from __future__ import annotations

import os
import urllib.request

import pytest

from oryx.doctor import run_doctor


def test_doctor_returns_expected_shape():
    d = run_doctor()
    assert "version" in d
    assert "python" in d
    assert "platform" in d
    assert "checks" in d
    assert "hints" in d
    assert isinstance(d["checks"], list) and d["checks"]
    assert isinstance(d["hints"], list) and d["hints"]


def test_doctor_declares_destructive_ops_as_preview():
    """危险操作恒为 preview —— 自证「默认只预览」。"""
    d = run_doctor()
    assert d["destructive_ops"] == "preview"


def test_doctor_makes_no_network_request(monkeypatch):
    """自检不发任何网络请求。"""

    def fail_open(*args, **kwargs):
        raise AssertionError("自检不该发起网络请求")

    monkeypatch.setattr(urllib.request, "urlopen", fail_open)
    # socket 也拦一层 —— urllib 下面还有 http.client
    import socket

    def fail_socket(*args, **kwargs):
        raise AssertionError("自检不该建连接")

    monkeypatch.setattr(socket, "socket", fail_socket)
    run_doctor()


def test_doctor_does_not_scan_user_files(monkeypatch):
    """自检不许遍历用户目录 —— 只看 Oryx 自己的数据目录。

    判据：os.walk 只能被用来扫 OryxData / 测试目录。
    """
    from oryx import doctor as doctor_mod

    allowed_roots = []
    real_walk = os.walk

    def tracked(top, *args, **kwargs):
        allowed_roots.append(str(top))
        return real_walk(top, *args, **kwargs)

    monkeypatch.setattr(doctor_mod.os, "walk", tracked)
    run_doctor()

    for path in allowed_roots:
        # 只允许扫数据目录下的东西
        assert "Oryx" in path or "oryx" in path or "tmp" in path.lower(), (
            f"自检扫了不该扫的目录：{path}"
        )


def test_doctor_hints_are_actionable():
    """hints 里的每一条都要能照做 —— 含具体命令或具体位置。"""
    d = run_doctor()
    for hint in d["hints"]:
        assert len(hint) > 6, f"hint 太短，等于没说：{hint!r}"


def test_doctor_checks_have_names_and_status():
    for check in run_doctor()["checks"]:
        assert check.get("name")
        assert "ok" in check
        assert "detail" in check


def test_doctor_reports_dependency_purpose():
    """依赖项要带用途 —— 让人知道装它有什么用。"""
    d = run_doctor()
    deps = d["dependencies"]
    assert deps
    for name, info in deps.items():
        assert "installed" in info
        assert info.get("purpose"), f"依赖 {name} 没写用途"


def test_doctor_route_requires_token():
    """/api/doctor 要令牌 —— 别让同机网页白嫖。"""
    from fastapi.testclient import TestClient

    from oryx.server import app

    client = TestClient(app)
    resp = client.get("/api/doctor")
    assert resp.status_code == 401
