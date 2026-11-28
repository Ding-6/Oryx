"""模块 8：知识库检索范围、字面量匹配、清空集合、解析完成事件。

重点不是「接口能用」，而是四个容易悄悄出错的边界：

1. 限定集合前**必须校验可见性** —— 否则传别人的私有集合 id 就能借
   搜索接口读到内容；
2. 模糊匹配兜底不能把用户输入的 ``%`` / ``_`` 当通配符；
3. 清空集合要**保留集合本身**，且解析中一律拦住（后台线程还会写回来）；
4. 解析事件只是加速刷新，发布失败不能影响解析结果落库。
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from oryx.domains import knowledge as knowledge_domain
from oryx.knowledge import KnowledgeBase, KnowledgeError
from oryx.store import Store


class _Bus:
    """只记录 publish 的假总线，接口与 EventBus.publish 对齐。"""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def publish(self, event_type: str, **payload) -> dict:
        event = {"type": event_type, **payload}
        self.events.append(event)
        return event


def _client(tmp_path, bus=None):
    app = FastAPI()

    @app.middleware("http")
    async def identity(request: Request, call_next):
        request.state.user_id = request.headers.get("x-test-user")
        return await call_next(request)

    store = Store(tmp_path / "oryx.db")
    knowledge_domain.register(app, store, bus=bus)
    return TestClient(app), store


def _data(resp):
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]


def _error(resp) -> str:
    body = resp.json()
    return str(body.get("message") or body.get("detail") or body)


def _collection(client, name, visibility="private", user="alice") -> str:
    resp = client.post(
        "/api/knowledge/collections",
        headers={"x-test-user": user},
        json={"name": name, "description": "", "visibility": visibility},
    )
    assert resp.status_code == 200, resp.text
    return _data(resp)["id"]


def _upload(client, collection_id, name, content, user="alice") -> str:
    resp = client.post(
        f"/api/knowledge/collections/{collection_id}/documents",
        headers={"x-test-user": user},
        files=[("files", (name, content.encode("utf-8"), "text/plain"))],
    )
    assert resp.status_code == 200, resp.text
    accepted = _data(resp)["accepted"]
    assert len(accepted) == 1, accepted
    return accepted[0]["id"]


def _documents(client, collection_id, user="alice"):
    return _data(client.get(
        f"/api/knowledge/collections/{collection_id}/documents",
        headers={"x-test-user": user},
    ))["documents"]


# ── 检索范围 ─────────────────────────────────────────────────────────


def test_search_scope_limits_to_one_collection(tmp_path):
    client, _ = _client(tmp_path)
    a = _collection(client, "甲")
    b = _collection(client, "乙")
    _upload(client, a, "a.txt", "共同的关键词在两份文档里都出现")
    _upload(client, b, "b.txt", "共同的关键词在两份文档里都出现")

    all_hits = _data(client.post(
        "/api/knowledge/search", headers={"x-test-user": "alice"},
        json={"query": "共同的关键词"},
    ))["hits"]
    assert len(all_hits) == 2

    scoped = _data(client.post(
        "/api/knowledge/search", headers={"x-test-user": "alice"},
        json={"query": "共同的关键词", "collection_id": a},
    ))
    assert scoped["collection_id"] == a
    assert len(scoped["hits"]) == 1
    assert scoped["hits"][0]["collection_id"] == a


def test_search_scope_rejects_other_users_private_collection(tmp_path):
    client, _ = _client(tmp_path)
    private = _collection(client, "私人的", visibility="private", user="alice")
    _upload(client, private, "a.txt", "只有 alice 能看见的内容")

    resp = client.post(
        "/api/knowledge/search", headers={"x-test-user": "bob"},
        json={"query": "只有", "collection_id": private},
    )
    assert resp.status_code == 404
    assert "没有这个知识库集合" in _error(resp)


def test_search_scope_rejects_unknown_collection(tmp_path):
    client, _ = _client(tmp_path)
    resp = client.post(
        "/api/knowledge/search", headers={"x-test-user": "alice"},
        json={"query": "任意", "collection_id": "不存在的集合"},
    )
    assert resp.status_code == 404
    assert "没有这个知识库集合" in _error(resp)


def test_search_scope_allows_shared_collection(tmp_path):
    client, _ = _client(tmp_path)
    shared = _collection(client, "共享的", visibility="shared", user="alice")
    _upload(client, shared, "a.txt", "共享集合里的一句话")

    hits = _data(client.post(
        "/api/knowledge/search", headers={"x-test-user": "bob"},
        json={"query": "共享集合里的一句话", "collection_id": shared},
    ))["hits"]
    assert len(hits) == 1
    assert hits[0]["collection_id"] == shared


def test_empty_collection_id_means_all_visible(tmp_path):
    client, _ = _client(tmp_path)
    a = _collection(client, "甲")
    _upload(client, a, "a.txt", "空串范围应等价于不传")
    for body in ({"query": "空串范围"}, {"query": "空串范围", "collection_id": ""},
                 {"query": "空串范围", "collection_id": "   "}):
        hits = _data(client.post(
            "/api/knowledge/search", headers={"x-test-user": "alice"}, json=body,
        ))["hits"]
        assert len(hits) == 1, body


def test_search_special_chars_are_literals(tmp_path):
    """短查询走兜底模糊匹配；`%` / `_` 必须是字面量。"""
    client, _ = _client(tmp_path)
    a = _collection(client, "字面量")
    _upload(client, a, "mark.txt", "进度 a%_b 已记录")
    _upload(client, a, "plain.txt", "这一行完全没有特殊符号")

    hits = _data(client.post(
        "/api/knowledge/search", headers={"x-test-user": "alice"},
        json={"query": "%_"},
    ))["hits"]
    assert [h["document_name"] for h in hits] == ["mark.txt"], "%_ 不能被 LIKE 当通配符"


def test_search_scope_applies_to_fts_branch_too(tmp_path):
    """长查询走 FTS 分支，范围过滤同样要生效。"""
    client, _ = _client(tmp_path)
    a = _collection(client, "甲")
    b = _collection(client, "乙")
    _upload(client, a, "a.txt", "全文检索分支也要遵守集合范围限定")
    _upload(client, b, "b.txt", "全文检索分支也要遵守集合范围限定")

    hits = _data(client.post(
        "/api/knowledge/search", headers={"x-test-user": "alice"},
        json={"query": "全文检索分支也要遵守集合范围限定", "collection_id": b},
    ))["hits"]
    assert len(hits) == 1
    assert hits[0]["collection_id"] == b


# ── 清空集合 ─────────────────────────────────────────────────────────


def test_clear_collection_keeps_collection_and_purges_index(tmp_path):
    client, _ = _client(tmp_path)
    cid = _collection(client, "保留我")
    _upload(client, cid, "a.txt", "第一份文档里有独特关键词甲")
    _upload(client, cid, "b.txt", "第二份文档里有独特关键词乙")

    result = _data(client.post(
        f"/api/knowledge/collections/{cid}/clear", headers={"x-test-user": "alice"},
    ))
    assert result == {"collection_id": cid, "deleted": 2}

    collections = _data(client.get(
        "/api/knowledge/collections", headers={"x-test-user": "alice"},
    ))["collections"]
    assert [c["id"] for c in collections] == [cid], "集合本身不能被删掉"
    assert collections[0]["document_count"] == 0
    assert _documents(client, cid) == []
    assert _data(client.post(
        "/api/knowledge/search", headers={"x-test-user": "alice"},
        json={"query": "独特关键词"},
    ))["hits"] == [], "FTS 行必须一起清掉"


def test_clear_collection_requires_creator(tmp_path):
    client, _ = _client(tmp_path)
    shared = _collection(client, "共享的", visibility="shared", user="alice")
    _upload(client, shared, "a.txt", "共享集合的文档")
    resp = client.post(
        f"/api/knowledge/collections/{shared}/clear", headers={"x-test-user": "bob"},
    )
    assert resp.status_code == 403
    assert "创建者" in _error(resp)


def test_clear_collection_rejects_unknown(tmp_path):
    client, _ = _client(tmp_path)
    resp = client.post(
        "/api/knowledge/collections/不存在的集合/clear", headers={"x-test-user": "alice"},
    )
    assert resp.status_code == 404


def test_clear_empty_collection_is_noop(tmp_path):
    client, _ = _client(tmp_path)
    cid = _collection(client, "空的")
    result = _data(client.post(
        f"/api/knowledge/collections/{cid}/clear", headers={"x-test-user": "alice"},
    ))
    assert result["deleted"] == 0


def test_clear_collection_blocks_while_parsing(tmp_path):
    """后台线程还会往文档 id 上写结果，这时清空会撞车。"""
    kb = KnowledgeBase(tmp_path / "kb")
    cid = kb.create_collection("alice", "待清空", "", "private")["id"]
    with kb._conn() as conn:
        conn.execute(
            "INSERT INTO documents(id,collection_id,owner_id,original_name,stored_path,parsed_path,"
            "extension,size_bytes,sha256,version,status,note,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("doc1", cid, "alice", "a.txt", "", "", ".txt", 1, "x", 1,
             "processing", "", 0.0, 0.0),
        )
    kb._active_documents.add("doc1")
    try:
        kb.clear_collection("alice", cid)
        assert False, "解析中不该允许清空"
    except KnowledgeError as exc:
        assert exc.status == 409
        assert "正在解析" in str(exc)


# ── 解析事件 ─────────────────────────────────────────────────────────


def test_rebuild_publishes_ready_event_with_owner(tmp_path):
    bus = _Bus()
    client, _ = _client(tmp_path, bus=bus)
    cid = _collection(client, "事件")
    doc_id = _upload(client, cid, "a.txt", "解析完成后应该有一条事件")

    events = [e for e in bus.events if e["type"] == "knowledge.document"]
    assert len(events) == 1
    event = events[0]
    assert event["document_id"] == doc_id
    assert event["collection_id"] == cid
    assert event["status"] == "ready"
    assert event["_owner_id"] == "alice"


def test_rebuild_publishes_failed_event_for_unparseable_file(tmp_path):
    bus = _Bus()
    client, _ = _client(tmp_path, bus=bus)
    cid = _collection(client, "失败事件")
    _upload(client, cid, "empty.txt", "")

    events = [e for e in bus.events if e["type"] == "knowledge.document"]
    assert len(events) == 1
    assert events[0]["status"] == "failed"
    docs = _documents(client, cid)
    assert docs[0]["status"] == "failed", "事件发布失败也不能影响落库"


def test_no_bus_is_silent_and_does_not_break_uploads(tmp_path):
    """老调用方（含既有单测）不传 bus，一切照旧。"""
    client, _ = _client(tmp_path)
    cid = _collection(client, "无总线")
    _upload(client, cid, "a.txt", "没有总线也要能解析完成")
    assert _documents(client, cid)[0]["status"] == "ready"


def test_broken_bus_does_not_break_parsing(tmp_path):
    class _BrokenBus:
        def publish(self, *a, **k):
            raise RuntimeError("总线挂了")

    client, _ = _client(tmp_path, bus=_BrokenBus())
    cid = _collection(client, "坏总线")
    _upload(client, cid, "a.txt", "总线异常不能把解析结果吞掉")
    assert _documents(client, cid)[0]["status"] == "ready"
