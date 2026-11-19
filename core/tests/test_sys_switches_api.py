"""系统设置能力清单与只读查询接口。

`syssettings.switches()` 以前定义了却没有任何调用方 —— 是「有实现、
用户却触达不到」的典型。现在它接进设置页，这组测试把接线钉住。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from oryx import syssettings
from oryx.server import app


def _headers() -> dict:
    import oryx.server as server_mod

    return {"X-Oryx-Token": server_mod.API_TOKEN, "X-Oryx-Request": "1"}


@pytest.fixture
def client():
    return TestClient(app)


def _post(client, path: str, body: dict):
    return client.post(path, json=body, headers=_headers())


def _get(client, path: str):
    return client.get(path, headers=_headers())


def test_switches_groups():
    """分组齐全，且每项都标了 read / write —— 界面据此决定是否弹确认。"""
    groups = syssettings.switches()
    assert {"power", "display", "input"} <= set(groups)
    for gid, group in groups.items():
        assert group.get("label"), f"{gid} 没有 label"
        assert group["items"], f"{gid} 没有能力项"
        for it in group["items"]:
            assert it.get("name") and it.get("label")
            assert it["action"] in ("read", "write")


def test_write_actions_exist_but_are_not_queryable(client):
    """写操作确实存在（否则清单在说谎），但不能从设置页一点就执行。"""
    names = {
        it["name"]
        for group in syssettings.switches().values()
        for it in group["items"]
    }
    for write in ("lock_screen", "set_dpi_scaling", "set_wallpaper"):
        assert write in names, f"{write} 没出现在能力清单里"

    for write in ("lock_screen", "set_dpi_scaling", "set_wallpaper"):
        r = _post(client, "/api/sys/query", {"name": write})
        assert r.status_code == 400, f"{write} 居然能绕过 L2 审批直接执行"


def test_query_rejects_unknown_and_empty(client):
    """白名单之外一律拒，防止任意函数名被拼进来调用。"""
    assert _post(client, "/api/sys/query", {"name": "power_status.__class__"}).status_code == 400
    assert _post(client, "/api/sys/query", {"name": ""}).status_code == 400
    assert _post(client, "/api/sys/query", {}).status_code == 400


def test_switches_endpoint_returns_same_shape(client):
    """/api/sys/switches 要把清单原样交给前端。"""
    r = _get(client, "/api/sys/switches")
    assert r.status_code == 200
    assert r.json() == syssettings.switches()


def test_readonly_names_all_have_callables(client, monkeypatch):
    """每个标 read 的项都要有真实可调用的实现 —— 不然按钮点下去必炸。"""
    groups = syssettings.switches()
    readonly = {
        it["name"]
        for group in groups.values()
        for it in group["items"]
        if it["action"] == "read"
    }
    assert readonly, "一个只读项都没有，设置页全是空按钮"
    for name in readonly:
        fn = getattr(syssettings, name, None)
        assert callable(fn), f"清单里的只读项 {name} 没有对应实现"
