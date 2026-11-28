from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from oryx.domains import knowledge as knowledge_domain
from oryx.indexer import chunk_text
from oryx.store import Store


def test_knowledge_collections_upload_versions_search_and_permissions(tmp_path):
    app = FastAPI()

    @app.middleware("http")
    async def test_identity(request: Request, call_next):
        request.state.user_id = request.headers.get("x-test-user")
        return await call_next(request)

    store = Store(tmp_path / "oryx.db")
    knowledge_domain.register(app, store)
    client = TestClient(app)
    alice = {"x-test-user": "alice"}
    bob = {"x-test-user": "bob"}

    private = client.post("/api/knowledge/collections", headers=alice, json={
        "name": "项目文档", "description": "个人集合", "visibility": "private",
    })
    assert private.status_code == 200
    collection_id = private.json()["data"]["id"]
    assert client.get("/api/knowledge/collections", headers=bob).json()["data"]["collections"] == []

    content = "版本化文件可以重复上传；来源追踪保留集合、文件名和片段位置。"
    files = [
        ("files", ("说明.txt", content.encode("utf-8"), "text/plain")),
        ("files", ("说明.txt", content.encode("utf-8"), "text/plain")),
    ]
    uploaded = client.post(
        f"/api/knowledge/collections/{collection_id}/documents", headers=alice, files=files,
    )
    assert uploaded.status_code == 200
    accepted = uploaded.json()["data"]["accepted"]
    assert [doc["version"] for doc in accepted] == [1, 2]

    docs = client.get(f"/api/knowledge/collections/{collection_id}/documents", headers=alice).json()["data"]["documents"]
    assert len(docs) == 2
    assert all(doc["status"] == "ready" for doc in docs)
    search = client.post("/api/knowledge/search", headers=alice, json={"query": "来源追踪"})
    hit = search.json()["data"]["hits"][0]
    assert hit["document_name"] == "说明.txt"
    assert hit["collection_name"] == "项目文档"
    assert hit["chunk_number"] == 1

    source = client.get(f"/api/knowledge/documents/{docs[0]['id']}/source", headers=alice)
    assert source.status_code == 200
    assert "attachment" in source.headers["content-disposition"]
    parsed = client.get(f"/api/knowledge/documents/{docs[0]['id']}/text", headers=alice).json()["data"]
    assert "来源追踪" in parsed["text"]

    assert client.get(f"/api/knowledge/documents/{docs[0]['id']}/source", headers=bob).status_code == 404
    assert client.delete(f"/api/knowledge/documents/{docs[0]['id']}", headers=bob).status_code == 404
    assert client.delete(f"/api/knowledge/documents/{docs[0]['id']}", headers=alice).status_code == 200
    assert len(client.get(f"/api/knowledge/collections/{collection_id}/documents", headers=alice).json()["data"]["documents"]) == 1

    shared = client.post("/api/knowledge/collections", headers=alice, json={
        "name": "共享资料", "description": "团队可检索", "visibility": "shared",
    }).json()["data"]
    visible = client.get("/api/knowledge/collections", headers=bob).json()["data"]["collections"]
    assert any(item["id"] == shared["id"] and item["can_edit"] is False for item in visible)
    assert client.put(f"/api/knowledge/collections/{shared['id']}", headers=bob,
                      json={"name": "不应修改"}).status_code == 403
    assert client.delete(f"/api/knowledge/collections/{shared['id']}", headers=bob).status_code == 403


def test_upload_chunking_has_no_per_file_chunk_cap():
    text = "x" * 200_000
    assert len(chunk_text(text, max_chunks=None)) > 200

