"""模块 10：全局接口层 —— 统一错误语义、请求体校验、出参形状。

取证结论（为什么这一轮不「逐域套 v2 壳」）
------------------------------------------
`core/web/modules/*.js` 里老前端直接读裸对象：

    const s = await (await api('/api/state')).json();   // s.mode / s.busy
    const d = await (await api('/api/clipboard')).json();
    const d = await (await api('/api/prompt/optimize')).json();  // d.optimized

`/api/state` `/api/chat/cancel` `/api/approve` `/api/clipboard` `/api/paste`
`/api/index/*` 这些接口一旦包成 `{code,data,message}`，老前端全部读空。
`core/tests/test_error_contract.py` 也明确钉住「成功保持裸 dict，不包壳」。

所以本轮把「统一」落在三件不碰老前端契约的事上，前端 `unwrap()` 仍是
两种形状的唯一边界：

  1. IPC 错误 → HTTP 状态码的规则原来散在 `_common` / workflow /
     checkpoint / schedule **四份**，同一句报错在不同域状态码不同；
  2. `/api/paste` `/api/terminal/run|interrupt` `/api/sys/query`
     `/api/checkpoints/{id}/rewind` 还是 `req: dict` —— body 传成数组
     会 `req.get` 抛 AttributeError → 500；
  3. 规格 §3 的出参形状缺陷（prompt/optimize 退化成 JSON 字符串、
     files/list 缺 count、三个源文件带 BOM）。
"""
from __future__ import annotations

import json as jsonlib
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

CORE = Path(__file__).resolve().parents[1]
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from oryx.domains import _common  # noqa: E402
from oryx.domains import checkpoint as checkpoint_domain  # noqa: E402
from oryx.domains import file as file_domain  # noqa: E402
from oryx.domains import schedule as schedule_domain  # noqa: E402
from oryx.domains import system as system_domain  # noqa: E402
from oryx.domains import workflow as workflow_domain  # noqa: E402
from oryx.ipc import IpcError  # noqa: E402


@pytest.fixture(scope="module")
def client():
    from oryx.server import API_TOKEN, app

    with TestClient(app, raise_server_exceptions=False) as c:
        c.headers.update({"X-Oryx-Token": API_TOKEN, "X-Oryx-Request": "1"})
        yield c


# ── 1. 错误映射：全项目只有一套规则 ────────────────────────────────

@pytest.mark.parametrize("text,status", [
    ("只有创建者能改", 403),
    ("内核未就绪，稍后再试", 503),
    ("已有扫描在进行", 409),
    ("扫描进行中", 409),
    ("已释放", 409),
    ("没有这个会话", 404),
    ("不在允许访问的范围内", 404),
    ("已被删除", 404),
    ("参数不合法", 400),
])
def test_map_ipc_error_status(text, status):
    exc = _common.map_ipc_error(text)
    assert isinstance(exc, HTTPException)
    assert exc.status_code == status
    assert exc.detail == text


def test_kernel_unready_beats_not_found():
    """优先级：503 高于 404 —— 文本里同时出现两种提示时按依赖问题处理。"""
    assert _common.map_ipc_error("内核未就绪：没有这个会话").status_code == 503


def test_forbidden_beats_everything():
    assert _common.map_ipc_error("只有创建者，内核未就绪").status_code == 403


def test_custom_hints_are_additive():
    assert _common.map_ipc_error("配额用完了", forbidden=("配额",)).status_code == 403
    assert _common.map_ipc_error("任务重名", conflict=("重名",)).status_code == 409
    assert _common.map_ipc_error("找不到这条", not_found=("找不到",)).status_code == 404


def test_rpc_failure_delegates_to_shared_rule():
    out = _common.rpc_failure(IpcError("只有创建者能改"))
    assert out.status_code == 403
    assert out.detail == "只有创建者能改"


def test_all_domain_maps_share_one_rule():
    """以前 schedule / checkpoint 对「只有创建者」给 400，_common 给 403。

    同一句内核报错跨域状态码必须一致 —— 这是本轮最要紧的一条回归。
    """
    for text, status in (("只有创建者能改", 403), ("内核未就绪", 503),
                         ("已有扫描在进行", 409), ("参数不合法", 400)):
        exc = IpcError(text)
        for mod in (workflow_domain, schedule_domain, checkpoint_domain):
            assert mod._map(exc).status_code == status, f"{mod.__name__}: {text}"


def test_workflow_keeps_its_extra_not_found_hints():
    for text in ("没有这次运行", "没有正在跑", "没有这个模板"):
        assert workflow_domain._map(IpcError(text)).status_code == 404


def test_is_kernel_unready_does_not_match_provider():
    assert _common.is_kernel_unready("内核未就绪") is True
    assert _common.is_kernel_unready("模型服务未就绪") is False


# ── 2. 请求体：脏 body 不能进业务层 ────────────────────────────────

@pytest.mark.parametrize("path", [
    "/api/paste",
    "/api/terminal/run",
    "/api/terminal/interrupt",
    "/api/sys/query",
    "/api/checkpoints/cp1/rewind",
])
def test_non_object_body_is_422(client, path):
    """`req: dict` 时代传数组会 AttributeError → 500；现在契约层 422。"""
    r = client.post(path, json=[1, 2, 3])
    assert r.status_code == 422, (path, r.status_code, r.text)
    body = r.json()
    assert body["code"] == 422
    assert "message" in body and "detail" in body


def test_paste_unknown_kind_is_chinese_400(client):
    r = client.post("/api/paste", json={"kind": "file"})
    assert r.status_code == 400
    assert "kind" in r.json()["detail"]


def test_paste_image_without_data_is_400(client):
    r = client.post("/api/paste", json={"kind": "image"})
    assert r.status_code == 400
    assert r.json()["detail"] == "没有图片数据"


def test_terminal_empty_command_is_400(client):
    """空命令仍由业务层给中文 400，不改成 422 —— 老前端提示路径不变。"""
    r = client.post("/api/terminal/run", json={"command": "   "})
    assert r.status_code == 400
    assert r.json()["detail"] == "command 不能为空"


def test_sys_query_empty_name_still_400(client):
    """兼容 test_sys_switches_api.py：{} 走业务层 400，不是 422。"""
    assert client.post("/api/sys/query", json={}).status_code == 400
    assert client.post("/api/sys/query", json={"name": ""}).status_code == 400


def test_rewind_scope_still_defaults(client):
    """空 body 拿 422（本来也必需 body），带 {} 时默认范围不变。"""
    assert client.post("/api/checkpoints/cp1/rewind", json={}).status_code in (400, 404)


# ── 3. 出参形状：规格 §3 的几处待修 ────────────────────────────────

def test_optimize_payload_failure_is_always_object():
    """空文本路径 content 是字符串 —— 以前直接 json() 会退化成 JSON 字符串。"""
    from oryx.promptopt import optimize

    out = system_domain.optimize_payload(optimize(""))
    assert isinstance(out, dict)
    assert out["ok"] is False
    for key in ("optimized", "notes", "original", "critique", "message"):
        assert key in out, key
    assert out["message"] == "先写点东西再优化。"


def test_optimize_payload_success_keeps_legacy_keys():
    from oryx.promptopt import optimize

    out = system_domain.optimize_payload(optimize("帮我写一份周报"))
    assert out["ok"] is True
    assert out["optimized"]
    assert out["original"] == "帮我写一份周报"
    assert isinstance(out["notes"], list)
    assert isinstance(out["critique"], list)


def test_optimize_payload_tolerates_plain_string_content():
    out = system_domain.optimize_payload({"content": "内核挂了"})
    assert out["ok"] is False and out["message"] == "内核挂了"
    assert out["optimized"] == "" and out["notes"] == []


def test_list_files_empty_roots_has_count():
    class _Cfg:
        allowed_roots: list[str] = []
        forbidden_paths: list[str] = []

    assert file_domain.list_files(_Cfg()) == {"files": [], "count": 0}


def test_sources_have_no_utf8_bom():
    for name in ("loop.py", "server.py", "workflow.py"):
        assert not (CORE / "oryx" / name).read_bytes().startswith(b"\xef\xbb\xbf"), name


# ── 4. 老前端契约：成功响应不包壳，错误体仍统一 ────────────────────

def test_json_keeps_bare_success_shape():
    """老接口用的 `_common.json()` 恒为裸对象 —— 包壳会砸掉老前端。"""
    payload = jsonlib.loads(_common.json({"mode": "craft", "busy": False}).body)
    assert payload == {"mode": "craft", "busy": False}
    assert not {"code", "data", "message"} <= payload.keys()


def test_ok_wraps_in_v2_shape():
    from oryx.api.response import ok

    payload = jsonlib.loads(ok({"x": 1}).body)
    assert payload == {"code": 0, "data": {"x": 1}, "message": ""}


def test_error_body_stays_unified(client):
    r = client.get("/api/audit/definitely-not-a-session")
    assert r.status_code == 404
    body = r.json()
    assert {"code", "message", "detail"} <= body.keys()
    assert body["code"] == 404
    assert body["detail"] == body["message"]
