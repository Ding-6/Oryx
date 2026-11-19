"""产物接口（/api/files/*）的测试。

这个接口能**用系统默认程序打开本地文件** —— 安全边界必须严：
只能打开允许范围内的真实文件，不能变成「读任意路径 + 启动任意程序」的探针。

同时验证：打开失败时要有可读的原因，不能静默。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture()
def srv(tmp_path: Path, monkeypatch):
    """起一个真实的服务实例，配置限定在 tmp_path 内。"""
    from fastapi.testclient import TestClient

    import oryx.config as config_mod
    import oryx.server as server_mod

    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config_mod, "CONFIG_PATH", home / "config.json")
    monkeypatch.setattr(config_mod, "FEATURES_PATH", home / "features.yaml")

    # 这两处必须走 monkeypatch：直接改 config.data / 裸赋值 store 不会自动恢复，
    # 会把模块级全局污染留给后面所有用例。
    monkeypatch.setitem(server_mod.config.data, "allowed_roots", [str(tmp_path)])
    monkeypatch.setitem(server_mod.config.data, "forbidden_paths", [])
    # domains/* 的路由在 app 装配时用闭包捕获了 store；这里仍要临时换库，
    # 但交给 monkeypatch 在用例结束时还原成原来那个对象。
    monkeypatch.setattr(server_mod, "store", server_mod.Store(tmp_path / "oryx.db"))

    client = TestClient(server_mod.app)
    yield client, tmp_path


def _token() -> str:
    import oryx.server as server_mod

    return server_mod.API_TOKEN


def _post(client, path: str, body: dict):
    return client.post(
        path,
        json=body,
        headers={"X-Oryx-Token": _token(), "X-Oryx-Request": "1"},
    )


def test_open_requires_token(client=None):
    """没令牌不能开文件 —— 这是能力接口，不是公开接口。"""
    from fastapi.testclient import TestClient

    import oryx.server as server_mod

    c = TestClient(server_mod.app)
    r = c.post("/api/files/open", json={"path": "C:/Windows/win.ini"})
    assert r.status_code == 401


def test_open_rejects_empty_path(srv):
    client, _ = srv
    r = _post(client, "/api/files/open", {"path": ""})
    assert r.status_code == 400


def test_open_rejects_missing_file(srv):
    """不存在的文件不能「打开」—— 免得变成路径探针。"""
    client, tmp = srv
    r = _post(client, "/api/files/open", {"path": str(tmp / "no-such-file.txt")})
    assert r.status_code == 404


def test_open_rejects_outside_allowed_roots(srv):
    """允许范围之外的文件不能打开 —— 这是最关键的一条。"""
    client, tmp = srv
    outside = Path("C:/Windows/win.ini")
    r = _post(client, "/api/files/open", {"path": str(outside)})
    assert r.status_code in (403, 404), f"越界路径应该被拒，实际 {r.status_code}"


def test_open_rejects_forbidden_path(srv, tmp_path, monkeypatch):
    """禁区路径直接拒。"""
    import oryx.server as server_mod

    client, tmp = srv
    forbidden = tmp / "forbidden"
    forbidden.mkdir()
    (forbidden / "secret.txt").write_text("x", encoding="utf-8")
    monkeypatch.setitem(server_mod.config.data, "forbidden_paths", [str(forbidden)])

    r = _post(client, "/api/files/open", {"path": str(forbidden / "secret.txt")})
    assert r.status_code == 403


def test_list_only_returns_files_in_scope(srv):
    """列文件只返回范围内的，不能泄露别的目录。"""
    client, tmp = srv
    (tmp / "a.txt").write_text("x", encoding="utf-8")
    (tmp / "sub").mkdir()
    (tmp / "sub" / "b.txt").write_text("y", encoding="utf-8")

    r = client.get("/api/files/list", headers={"X-Oryx-Token": _token()})
    assert r.status_code == 200
    data = r.json()
    names = {f["name"] for f in data["files"]}
    assert "a.txt" in names
    assert "b.txt" in names


def test_list_sorted_by_mtime_desc(srv):
    """按修改时间倒序 —— 最近改的排前面。"""
    import time

    client, tmp = srv
    (tmp / "old.txt").write_text("x", encoding="utf-8")
    time.sleep(0.05)
    (tmp / "new.txt").write_text("y", encoding="utf-8")

    r = client.get("/api/files/list", headers={"X-Oryx-Token": _token()})
    files = r.json()["files"]
    assert files, "应该列出文件"
    mtimes = [f["mtime"] for f in files]
    assert mtimes == sorted(mtimes, reverse=True)


def test_list_rejects_outside_prefix(srv):
    """prefix 越界要拒。"""
    client, _ = srv
    r = client.get(
        "/api/files/list",
        params={"prefix": "C:/Windows"},
        headers={"X-Oryx-Token": _token()},
    )
    assert r.status_code == 403
