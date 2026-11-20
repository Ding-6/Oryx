"""自定义工具包：定义 → 列出 → 改 → 删。

「一个工具包」是什么：名字 + 描述 + 功能说明 + 由哪些工具组成。
描述和功能是给**模型**看的（据此判断该不该用这套工具），
所以这两个字段是这个功能的核心，不是装饰。

这里守住四件事：
  · 中文名也能用（id 自动生成，且不撞）
  · 少字段 / 空工具 → 明确报错，不静默存半个包
  · 改名 ≠ 换 id（id 改了会让引用失效）
  · 存的是磁盘配置，不是只在内存里
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    """独立的数据目录 —— 工具包要真落盘，不能污染用户的配置。"""
    home = tmp_path_factory.mktemp("toolpack") / "home"
    home.mkdir()
    import os

    os.environ["ORYX_HOME"] = str(home)
    saved = {n: m for n, m in sys.modules.items() if n.startswith("oryx")}
    for n in list(saved):
        del sys.modules[n]
    from oryx.server import API_TOKEN, app

    with TestClient(app) as c:
        c.headers.update({"X-Oryx-Token": API_TOKEN, "X-Oryx-Request": "1"})
        yield c, home
    for n in list(sys.modules):
        if n.startswith("oryx"):
            del sys.modules[n]
    sys.modules.update(saved)


def _data(resp):
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]


def test_toolpack_closed_loop(env):
    """建 → 读 → 改 → 删，每一步都要看得见。"""
    client, home = env

    # 测试用的配置存储是**跨运行共享**的（<临时目录>/oryx-test-store），
    # 上一轮跑完留下的包还在 —— 先清场再断言「从空开始」。
    for p in _data(client.get("/api/tool-packs"))["packs"]:
        client.delete(f"/api/tool-packs/{p['id']}")
    assert _data(client.get("/api/tool-packs"))["packs"] == []

    created = _data(client.post("/api/tool-packs", json={
        "name": "日常三件套",
        "description": "读文件、看系统、查网页",
        "function": "fs_read 读文件；system_info 查系统；web_search 查网页",
        "tools": ["fs_read", "system_info", "web_search"],
    }))
    pid = created["id"]
    assert created["name"] == "日常三件套"
    assert len(created["tools"]) == 3

    # 读回来
    listed = _data(client.get("/api/tool-packs"))["packs"]
    mine = next(p for p in listed if p["id"] == pid)
    assert mine["description"] == "读文件、看系统、查网页"
    assert mine["function"].startswith("fs_read")

    # 真的落盘了 —— 重开一个进程也读得到
    #
    # 配置路径用模块里的真值：测试环境下它固定指向
    # <临时目录>/oryx-test-store/config.json（不跟着 ORYX_HOME 走），
    # 自己拼路径会找不到。
    from oryx.config import CONFIG_PATH

    on_disk = json.loads(CONFIG_PATH.read_text("utf-8"))
    assert pid in on_disk["tool_packs"]

    # 改
    updated = _data(client.put(f"/api/tool-packs/{pid}", json={
        "id": pid, "name": "日常两件套",
        "description": "读文件、看系统",
        "function": "少一个",
        "tools": ["fs_read", "system_info"],
    }))
    assert updated["name"] == "日常两件套"
    assert len(updated["tools"]) == 2

    # 删
    _data(client.delete(f"/api/tool-packs/{pid}"))
    assert _data(client.get("/api/tool-packs"))["packs"] == []


def test_chinese_name_gets_unique_ids(env):
    """中文名也要能建，而且两个中文名不能撞 id。"""
    client, _ = env
    a = _data(client.post("/api/tool-packs", json={"name": "打包甲", "tools": ["fs_read"]}))
    b = _data(client.post("/api/tool-packs", json={"name": "打包乙", "tools": ["fs_read"]}))
    assert a["id"] != b["id"]
    for p in (a, b):
        client.delete(f"/api/tool-packs/{p['id']}")


def test_invalid_packs_are_rejected(env):
    """少字段要明确报错，不要静默存半个包。"""
    client, _ = env
    # 没有名字
    assert client.post("/api/tool-packs", json={"tools": ["fs_read"]}).status_code == 400
    # 一个工具都没有
    assert client.post("/api/tool-packs", json={"name": "空的", "tools": []}).status_code == 400
    # id 不合法
    assert client.post("/api/tool-packs", json={
        "id": "带 空格/斜杠", "name": "x", "tools": ["fs_read"]}).status_code == 400
    # 改不存在的
    assert client.put("/api/tool-packs/nope", json={
        "id": "nope", "name": "x", "tools": ["fs_read"]}).status_code == 404
    # 删不存在的
    assert client.delete("/api/tool-packs/nope").status_code == 404


def test_duplicate_id_is_rejected(env):
    """同一个 id 不能建两次 —— 否则后一个会把前一个悄悄覆盖掉。"""
    client, _ = env
    payload = {"id": "dup", "name": "第一个", "tools": ["fs_read"]}
    assert client.post("/api/tool-packs", json=payload).status_code == 200
    assert client.post("/api/tool-packs", json={
        "id": "dup", "name": "第二个", "tools": ["fs_write"]}).status_code == 409
    client.delete("/api/tool-packs/dup")


def test_id_cannot_change_on_update(env):
    """改名不改 id —— id 变了引用就断了（工具包被别处引用时）。"""
    client, _ = env
    created = _data(client.post("/api/tool-packs", json={
        "id": "keepme", "name": "原名", "tools": ["fs_read"]}))
    assert created["id"] == "keepme"
    bad = client.put("/api/tool-packs/keepme", json={
        "id": "changed", "name": "原名", "tools": ["fs_read"]})
    assert bad.status_code == 400
    client.delete("/api/tool-packs/keepme")


def test_export_import_roundtrip(env):
    """导出成插件清单 → 再导入回来，东西不能丢。

    这是「工具包能分享、能装到别的机器上」的前提。
    清单格式跟扩展系统同一套（oryx.json）。
    """
    client, _ = env
    created = _data(client.post("/api/tool-packs", json={
        "name": "可导出的包", "description": "一句话说明",
        "function": "fs_read 读文件", "tools": ["fs_read", "system_info"]}))
    pid = created["id"]

    manifest = _data(client.get(f"/api/tool-packs/{pid}/export"))
    assert manifest["kind"] == "tool"
    assert manifest["apiVersion"] == "oryx/v1"
    assert manifest["displayName"] == "可导出的包"
    assert manifest["description"] == "一句话说明"
    assert manifest["function"] == "fs_read 读文件"
    assert [t["name"] for t in manifest["tools"]] == ["fs_read", "system_info"]

    # 删掉再导回来 —— 模拟「在另一台机器上装」
    client.delete(f"/api/tool-packs/{pid}")
    back = _data(client.post("/api/tool-packs/import", json=manifest))
    assert back["name"] == "可导出的包"
    assert back["function"] == "fs_read 读文件"
    assert back["tools"] == ["fs_read", "system_info"]
    client.delete(f"/api/tool-packs/{back['id']}")


def test_import_rejects_wrong_kind(env):
    """不是工具包的清单要明确拒掉，别装进来一个半成品。"""
    client, _ = env
    bad = client.post("/api/tool-packs/import", json={
        "kind": "skill", "displayName": "某个技能", "tools": [{"name": "fs_read"}]})
    assert bad.status_code == 400
    # 没有工具的也要拒
    empty = client.post("/api/tool-packs/import", json={
        "kind": "tool", "displayName": "空的", "tools": []})
    assert empty.status_code == 400


def test_tools_are_deduplicated(env):
    """同一个工具写两遍只算一个 —— 免得卡片上显示「3 个工具」其实只有 2 个。"""
    client, _ = env
    created = _data(client.post("/api/tool-packs", json={
        "name": "去重", "tools": ["fs_read", "fs_read", " fs_read ", "fs_write"]}))
    assert created["tools"] == ["fs_read", "fs_write"]
    client.delete(f"/api/tool-packs/{created['id']}")
