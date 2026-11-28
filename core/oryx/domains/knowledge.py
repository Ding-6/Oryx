"""Knowledge collections, document uploads, parsing and source-aware search."""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..api.response import ok
from ..knowledge import KnowledgeBase, KnowledgeError, SUPPORTED_EXTENSIONS, safe_filename

router = APIRouter(prefix="/api/knowledge", tags=["知识库"])


class CollectionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    visibility: Literal["private", "shared"] = "private"

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("集合名称不能为空")
        return value

    @field_validator("description")
    @classmethod
    def clean_description(cls, value: str) -> str:
        return value.strip()


class CollectionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    visibility: Literal["private", "shared"] | None = None

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("集合名称不能为空")
        return value

    @field_validator("description")
    @classmethod
    def clean_description(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class SearchPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=20, ge=1, le=100)
    """为空 = 检索全部可见集合；给了 id 就只搜这一个（后端会先校验可见性）。"""
    collection_id: str | None = Field(default=None, max_length=128)

    @field_validator("query")
    @classmethod
    def clean_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("请输入检索内容")
        return value

    @field_validator("collection_id")
    @classmethod
    def clean_collection_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


def _invoke(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except KnowledgeError as exc:
        raise HTTPException(exc.status, str(exc)) from None


def register(app, store, config=None, bus=None) -> None:
    root = store.conv_path.parent / "knowledge"
    ocr_enabled = bool(config.data.get("ocr", True)) if config is not None else True
    # bus 可选：不传时解析完成不推事件，老调用方（单测 / 嵌入式用法）无感。
    knowledge = KnowledgeBase(root, ocr=ocr_enabled, bus=bus)
    routes = APIRouter(prefix="/api/knowledge", tags=["知识库"])

    @routes.get("/collections")
    async def _collections(request: Request):
        return ok({"collections": knowledge.list_collections(getattr(request.state, "user_id", None))})

    @routes.post("/collections")
    async def _create_collection(req: CollectionPayload, request: Request):
        return ok(knowledge.create_collection(
            getattr(request.state, "user_id", None), req.name, req.description, req.visibility,
        ))

    @routes.put("/collections/{collection_id}")
    async def _update_collection(collection_id: str, req: CollectionPatch, request: Request):
        if not req.model_fields_set:
            raise HTTPException(400, "至少提供一个要修改的字段")
        return ok(_invoke(
            knowledge.update_collection, getattr(request.state, "user_id", None), collection_id,
            name=req.name, description=req.description, visibility=req.visibility,
        ))

    @routes.delete("/collections/{collection_id}")
    async def _delete_collection(collection_id: str, request: Request):
        return ok(_invoke(knowledge.delete_collection, getattr(request.state, "user_id", None), collection_id))

    @routes.post("/collections/{collection_id}/clear")
    async def _clear_collection(collection_id: str, request: Request):
        return ok(_invoke(knowledge.clear_collection, getattr(request.state, "user_id", None), collection_id))

    @routes.get("/collections/{collection_id}/documents")
    async def _documents(collection_id: str, request: Request):
        return ok({"documents": _invoke(
            knowledge.list_documents, getattr(request.state, "user_id", None), collection_id,
        )})

    @routes.post("/collections/{collection_id}/documents")
    async def _upload_documents(
        collection_id: str,
        request: Request,
        background: BackgroundTasks,
        files: list[UploadFile] = File(...),
    ):
        actor_id = getattr(request.state, "user_id", None)
        # Check write access before accepting potentially large uploads.
        _invoke(knowledge.require_editable_collection, actor_id, collection_id)
        accepted = []
        rejected = []
        for upload in files:
            name = upload.filename or ""
            temporary = knowledge.files_dir / f".{uuid.uuid4().hex}.upload"
            try:
                clean_name = safe_filename(name)
                extension = Path(clean_name).suffix.lower()
                if extension not in SUPPORTED_EXTENSIONS:
                    raise KnowledgeError(415, f"暂不支持 {extension or '无扩展名'} 文件")
                digest = hashlib.sha256()
                with temporary.open("wb") as stream:
                    while block := await upload.read(1024 * 1024):
                        stream.write(block)
                        digest.update(block)
                document = _invoke(
                    knowledge.accept_upload, actor_id, collection_id, clean_name, temporary,
                    digest.hexdigest(),
                )
                background.add_task(knowledge.rebuild_document, document["id"])
                accepted.append(document)
            except KnowledgeError as exc:
                temporary.unlink(missing_ok=True)
                rejected.append({"name": name, "reason": str(exc)})
            except OSError:
                temporary.unlink(missing_ok=True)
                rejected.append({"name": name, "reason": "保存文件失败，请检查磁盘空间和目录权限"})
            finally:
                await upload.close()
        return ok({"accepted": accepted, "rejected": rejected})

    @routes.post("/documents/{document_id}/rebuild")
    async def _rebuild(document_id: str, request: Request, background: BackgroundTasks):
        doc = _invoke(knowledge.queue_rebuild, getattr(request.state, "user_id", None), document_id)
        background.add_task(knowledge.rebuild_document, document_id)
        return ok(doc)

    @routes.delete("/documents/{document_id}")
    async def _delete_document(document_id: str, request: Request):
        return ok(_invoke(knowledge.delete_document, getattr(request.state, "user_id", None), document_id))

    @routes.get("/documents/{document_id}/text")
    async def _document_text(document_id: str, request: Request, offset: int = 0, limit: int = 12000):
        if offset < 0:
            raise HTTPException(400, "offset 不能小于 0")
        return ok(_invoke(
            knowledge.parsed_text, getattr(request.state, "user_id", None), document_id, offset, limit,
        ))

    @routes.get("/documents/{document_id}/source")
    async def _document_source(document_id: str, request: Request):
        path, name = _invoke(knowledge.source_path, getattr(request.state, "user_id", None), document_id)
        return FileResponse(path, filename=name, headers={"Cache-Control": "private, no-store"})

    @routes.post("/search")
    async def _search(req: SearchPayload, request: Request):
        hits = _invoke(knowledge.search, getattr(request.state, "user_id", None),
                       req.query, req.limit, req.collection_id)
        return ok({"query": req.query, "hits": hits, "count": len(hits),
                   "collection_id": req.collection_id})

    app.include_router(routes)
