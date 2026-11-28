"""模块 13：剩余裸 dict 请求体全量迁移的契约测试。

取证结论（为什么不是「27 处」）
--------------------------------
上一轮用 grep 数出的 27 处里，有 7 行是模块 10 迁移完留下的
「以前是裸 dict」注释；真实 `req: dict` 签名是 20 处，其中：

* 9 处是**真正的 HTTP 端点 body**（零校验，传数组 / 字符串会 500）—— 本轮全换 Pydantic；
* 6 处是这些端点 1:1 对应的实现函数 —— 跟着换；
* 5 处是 `persona` 的内部纯函数（路由层早已用 `extra="forbid"` 的
  `ExpertPayload` / `AgentPayload` 保护）—— 参数改名 `data: dict[str, Any]`，
  不再冒充请求体。

另有 8 处非 `req` 名的裸 `dict` 注解（内部数据结构），统一精确化成
`dict[str, Any]`。守门断言两条：域层零裸 dict 参数注解、
`core/oryx/` 运行时源码零 `req: dict` 字面（测试/文档/打包副本不在扫描范围）。

**刻意保留**：业务层中文 400 不改成 422 —— 老前端靠 `e.detail` 显示错误提示，
换了就变成「字段格式错误」这种看不懂的话。Pydantic 只负责「body 形状脏」。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

CORE = Path(__file__).resolve().parents[1]
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from oryx.domains import app as app_domain  # noqa: E402
from oryx.domains import file as file_domain  # noqa: E402
from oryx.domains import mcp as mcp_domain  # noqa: E402
from oryx.domains import skill as skill_domain  # noqa: E402
from oryx.domains import toolpack as toolpack_domain  # noqa: E402
from oryx.domains import workflow as workflow_domain  # noqa: E402

DOMAINS_DIR = CORE / "oryx" / "domains"


@pytest.fixture(scope="module")
def client():
    from oryx.server import API_TOKEN, app

    with TestClient(app, raise_server_exceptions=False) as c:
        c.headers.update({"X-Oryx-Token": API_TOKEN, "X-Oryx-Request": "1"})
        yield c


# ── 1. 九个端点：非对象 body 一律 422，不再 500 ──────────────────────
# (method, path, 说明)
ENDPOINTS = [
    ("post", "/api/apps/launch", "启动应用"),
    ("post", "/api/apps/custom", "新增自定义应用"),
    ("put", "/api/apps/custom/some-id", "更新自定义应用"),
    ("post", "/api/skills", "新建技能"),
    ("put", "/api/skills/some-name", "更新技能"),
    ("post", "/api/files/open", "打开产物"),
    ("post", "/api/mcp/toggle", "启停 MCP"),
    ("post", "/api/workflows/templates/tpl-1/run", "按模板运行"),
    ("post", "/api/tool-packs/import", "导入工具包清单"),
]

DIRTY_BODIES = [
    (["不是对象"], "数组"),
    ("也不是对象", "字符串"),
    (123, "数字"),
]


@pytest.mark.parametrize("method,path,label", ENDPOINTS, ids=[e[2] for e in ENDPOINTS])
@pytest.mark.parametrize("body,kind", DIRTY_BODIES, ids=[d[1] for d in DIRTY_BODIES])
def test_non_object_body_is_422(client, method, path, label, body, kind):
    resp = getattr(client, method)(path, json=body)
    assert resp.status_code == 422, (
        f"{label} 传{kind} body 应被契约层挡成 422，实际 {resp.status_code}: {resp.text[:200]}"
    )


# ── 2. 合法对象但业务字段为空：仍是中文 400，没被 422 吞掉 ────────────
@pytest.mark.parametrize("method,path,body,detail", [
    ("post", "/api/apps/launch", {"id": ""}, "id 不能为空"),
    ("post", "/api/apps/custom", {}, "应用名不能为空"),
    ("put", "/api/apps/custom/some-id", {}, "应用名不能为空"),
    ("post", "/api/skills", {"name": ""}, "name 不能为空"),
    ("post", "/api/files/open", {"path": ""}, "path 不能为空"),
    ("post", "/api/mcp/toggle", {"name": ""}, "name 不能为空"),
    ("post", "/api/tool-packs/import", {}, "清单里没有名字（displayName）"),
])
def test_business_400_still_chinese(client, method, path, body, detail):
    resp = getattr(client, method)(path, json=body)
    assert resp.status_code == 400, (path, resp.status_code, resp.text[:200])
    assert resp.json()["detail"] == detail


def test_skill_update_keeps_unset_fields(client, tmp_path, monkeypatch):
    """PUT 只传 description 时，body 必须沿用旧值（exclude_unset 语义）。

    直接读落盘的 SKILL.md —— GET 详情还要过内核那份权威库，
    全局 app 连着真实内核时它不一定认得临时目录里的技能，会误报 404。
    """
    root = tmp_path / "skills"
    root.mkdir()
    monkeypatch.setattr(skill_domain, "skills_root", lambda: root)
    created = client.post("/api/skills", json={
        "name": "demo", "description": "说明", "body": "原始正文"})
    assert created.status_code == 200, created.text
    updated = client.put("/api/skills/demo", json={"description": "新说明"})
    assert updated.status_code == 200, updated.text
    text = (root / "demo" / "SKILL.md").read_text("utf-8")
    assert "description: 新说明" in text
    assert "原始正文" in text


# ── 3. 模型自身的行为 ────────────────────────────────────────────────
def test_model_defaults_and_extra_ignored():
    from pydantic import ValidationError

    assert app_domain.LaunchAppRequest().id == ""
    assert app_domain.CustomAppRequest().target == ""
    assert file_domain.OpenFileRequest().reveal is False
    assert mcp_domain.McpToggleRequest().enabled is False
    assert workflow_domain.TemplateRunRequest().args is None
    # 老前端编辑技能时会带 name（只读展示），额外字段要被忽略而不是 422
    assert skill_domain.SkillUpdateRequest(name="demo", description="x").description == "x"
    # RootModel 里 root 就是清单本体
    manifest = toolpack_domain.ToolPackImportRequest({"displayName": "包", "tools": ["a"]})
    assert manifest.root["displayName"] == "包"
    with pytest.raises(ValidationError):
        toolpack_domain.ToolPackImportRequest(["不是对象"])
    # 显式 null 不能悄悄变成字符串 "None"
    with pytest.raises(ValidationError):
        skill_domain.SkillCreateRequest(name=None)


def test_custom_app_path_id_is_authoritative():
    """PUT 时路径 id 覆盖 body id —— 不让 body 把资源改到别处。"""
    import oryx.manage as manage

    captured = {}

    def fake_save(item):
        captured.update(item)
        return {"id": item["id"], "ok": True}

    original = manage.save_custom_app
    manage.save_custom_app = fake_save
    try:
        result = app_domain.custom_app_update(
            "path-id", app_domain.CustomAppRequest(id="body-id", name="x", target="y"))
    finally:
        manage.save_custom_app = original
    assert captured["id"] == "path-id"
    assert result["app"]["id"] == "path-id"


# ── 4. 静态守门：不许再有裸 dict 参数 / `req: dict` 字面 ───────────────
def _bare_dict_annotation(node: ast.AST) -> bool:
    """annotation 是裸 `dict` / `Dict` / `dict | None` 就返回 True。"""
    if isinstance(node, ast.Name) and node.id in ("dict", "Dict"):
        return True
    if isinstance(node, ast.Subscript):
        return False  # dict[str, Any] 之类是精确类型，允许
    if isinstance(node, ast.BinOp):  # X | Y
        return _bare_dict_annotation(node.left) or _bare_dict_annotation(node.right)
    return False


def test_no_bare_dict_params_in_domains():
    offenders: list[str] = []
    for path in sorted(DOMAINS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text("utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            args = node.args
            for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs):
                if arg.annotation is not None and _bare_dict_annotation(arg.annotation):
                    offenders.append(f"{path.name}:{node.lineno} {node.name}({arg.arg}: dict)")
    assert not offenders, "域层仍有裸 dict 参数注解：\n" + "\n".join(offenders)


def test_no_req_dict_literal_anywhere():
    """运行时源码连历史注释一起清掉，保证 grep 归零可被验证。

    只扫 `oryx/` —— 测试、文档、`apps/desktop/target/release` 打包副本
    里的历史描述不受这条约束。
    """
    offenders = []
    for path in sorted((CORE / "oryx").rglob("*.py")):
        for lineno, line in enumerate(path.read_text("utf-8").splitlines(), 1):
            if "req: dict" in line:
                offenders.append(f"{path.relative_to(CORE)}:{lineno}")
    assert not offenders, "仍有 `req: dict` 字面：\n" + "\n".join(offenders)


# OpenAPI 里路径参数是模板形式，与上面带具体 id 的调用路径不同
OPENAPI_PATH = {
    "/api/apps/custom/some-id": "/api/apps/custom/{app_id}",
    "/api/skills/some-name": "/api/skills/{name}",
    "/api/workflows/templates/tpl-1/run": "/api/workflows/templates/{template_id}/run",
}


NEW_MODELS = {
    "LaunchAppRequest", "CustomAppRequest", "SkillCreateRequest",
    "SkillUpdateRequest", "OpenFileRequest", "McpToggleRequest",
    "TemplateRunRequest", "ToolPackImportRequest",
}


def _refs(node):
    if isinstance(node, dict):
        if "$ref" in node:
            yield node["$ref"]
        for value in node.values():
            yield from _refs(value)
    elif isinstance(node, list):
        for value in node:
            yield from _refs(value)


def test_openapi_request_bodies_are_typed_models(client):
    """九个端点的 body 必须 $ref 到本轮的模型，而不是无约束裸 dict。

    裸 `req: dict` 在 OpenAPI 里是内联 `{type: object, additionalProperties: true}`
    （没有 $ref）—— 这条断言能真正区分「换了模型」与「只改了类型注解」。
    `TemplateRunRequest | None` 的 $ref 藏在 anyOf 里，所以要递归找。
    """
    spec = client.get("/openapi.json").json()
    for method, path, label in ENDPOINTS:
        spec_path = OPENAPI_PATH.get(path, path)
        op = spec["paths"][spec_path][method]
        assert "requestBody" in op, f"{label} 缺 requestBody"
        schema = op["requestBody"]["content"]["application/json"]["schema"]
        names = {ref.rsplit("/", 1)[-1] for ref in _refs(schema)}
        assert names & NEW_MODELS, (label, schema)
