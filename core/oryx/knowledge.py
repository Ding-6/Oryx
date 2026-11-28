"""Persistent, account-aware collections and uploaded knowledge documents."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from . import indexer

SUPPORTED_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".pdf", ".docx", ".xlsx", ".pptx",
    *indexer.IMAGE_EXT,
}
CHUNK_STEP = indexer.CHUNK_SIZE - indexer.CHUNK_OVERLAP

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS collections (
    id TEXT PRIMARY KEY,
    owner_id TEXT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    visibility TEXT NOT NULL DEFAULT 'private' CHECK(visibility IN ('private','shared')),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kb_collections_owner ON collections(owner_id, updated_at);
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    collection_id TEXT NOT NULL,
    owner_id TEXT,
    original_name TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    parsed_path TEXT,
    extension TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    parsed_at REAL,
    FOREIGN KEY(collection_id) REFERENCES collections(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_kb_documents_collection ON documents(collection_id, created_at DESC);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    document_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    content TEXT NOT NULL,
    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE,
    UNIQUE(document_id, ordinal)
);
CREATE INDEX IF NOT EXISTS idx_kb_chunks_document ON chunks(document_id, ordinal);
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
    content, document_id UNINDEXED, ordinal UNINDEXED, tokenize='trigram'
);
"""


class KnowledgeError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def safe_filename(value: str) -> str:
    name = str(value or "").replace("\\", "/").split("/")[-1].strip()
    name = "".join(ch for ch in name if ch >= " " and ch not in "\x7f")
    if not name or len(name) > 255:
        raise KnowledgeError(400, "文件名无效或超过 255 个字符")
    return name


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class KnowledgeBase:
    """Files live under the app data directory; metadata and search stay local."""

    def __init__(self, root: Path, *, ocr: bool = True, bus: Any | None = None):
        self.root = Path(root).resolve()
        self.ocr = ocr
        # 可选事件总线：解析在后台线程完成，推一条 SSE 让界面立刻刷新，
        # 不必死等定时轮询。不传时完全静默（老调用方 / 单元测试无感）。
        self.bus = bus
        self.files_dir = self.root / "files"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "knowledge.db"
        self._jobs_lock = threading.Lock()
        self._active_documents: set[str] = set()
        self.root.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            # In-process background parsing cannot survive a restart. Preserve uploads
            # and tell the owner to rebuild so no document remains silently stuck.
            conn.execute(
                "UPDATE documents SET status='failed', note='服务重启时解析中断，请重新构建', updated_at=? "
                "WHERE status IN ('queued','processing')", (time.time(),),
            )

    def _publish_document(self, document_id: str, collection_id: str,
                          status: str, owner_id: str | None) -> None:
        """解析结束推一条 SSE。事件带 ``_owner_id``，服务端只发给本人。

        共享集合的其他账号看不到别人的私有事件，所以前端仍保留低频兜底
        轮询 —— 这条事件是「立刻刷新」的加速，不是唯一通路。
        发布失败绝不能影响解析结果落库。
        """
        if self.bus is None:
            return
        try:
            self.bus.publish(
                "knowledge.document",
                document_id=document_id,
                collection_id=collection_id,
                status=status,
                _owner_id=owner_id,
            )
        except Exception:  # noqa: BLE001 — 事件是旁路，不能拖垮解析
            pass

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @staticmethod
    def _visible_sql(owner_id: str | None, alias: str = "c") -> tuple[str, tuple[Any, ...]]:
        if owner_id is None:
            return f"({alias}.owner_id IS NULL OR {alias}.visibility='shared')", ()
        return f"({alias}.owner_id=? OR {alias}.visibility='shared')", (owner_id,)

    def _collection(self, conn, collection_id: str, owner_id: str | None, *, write=False):
        row = conn.execute("SELECT * FROM collections WHERE id=?", (collection_id,)).fetchone()
        if not row:
            raise KnowledgeError(404, "没有这个知识库集合")
        if row["owner_id"] != owner_id and row["visibility"] != "shared":
            raise KnowledgeError(404, "没有这个知识库集合")
        if write and row["owner_id"] != owner_id:
            raise KnowledgeError(403, "共享集合只有创建者可以修改")
        return row

    @staticmethod
    def _collection_dict(row, *, document_count=0, total_bytes=0, can_edit=False) -> dict[str, Any]:
        return {
            "id": row["id"], "name": row["name"], "description": row["description"],
            "visibility": row["visibility"], "owner_id": row["owner_id"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "document_count": int(document_count), "total_bytes": int(total_bytes),
            "can_edit": can_edit,
        }

    def list_collections(self, owner_id: str | None) -> list[dict[str, Any]]:
        visible, args = self._visible_sql(owner_id)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT c.*, COUNT(d.id) AS document_count, COALESCE(SUM(d.size_bytes),0) AS total_bytes "
                "FROM collections c LEFT JOIN documents d ON d.collection_id=c.id "
                f"WHERE {visible} GROUP BY c.id ORDER BY c.updated_at DESC, c.name COLLATE NOCASE", args,
            ).fetchall()
        return [self._collection_dict(row, document_count=row["document_count"],
                                      total_bytes=row["total_bytes"], can_edit=row["owner_id"] == owner_id)
                for row in rows]

    def create_collection(self, owner_id: str | None, name: str, description: str, visibility: str) -> dict[str, Any]:
        collection_id = uuid.uuid4().hex
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO collections(id,owner_id,name,description,visibility,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?)", (collection_id, owner_id, name, description, visibility, now, now),
            )
            row = conn.execute("SELECT * FROM collections WHERE id=?", (collection_id,)).fetchone()
        return self._collection_dict(row, can_edit=True)

    def require_editable_collection(self, owner_id: str | None, collection_id: str) -> None:
        with self._conn() as conn:
            self._collection(conn, collection_id, owner_id, write=True)

    def update_collection(self, owner_id: str | None, collection_id: str, *, name=None, description=None,
                          visibility=None) -> dict[str, Any]:
        with self._conn() as conn:
            row = self._collection(conn, collection_id, owner_id, write=True)
            values = (name if name is not None else row["name"],
                      description if description is not None else row["description"],
                      visibility if visibility is not None else row["visibility"],
                      time.time(), collection_id)
            conn.execute("UPDATE collections SET name=?,description=?,visibility=?,updated_at=? WHERE id=?", values)
            updated = conn.execute("SELECT * FROM collections WHERE id=?", (collection_id,)).fetchone()
        return self._collection_dict(updated, can_edit=True)

    def delete_collection(self, owner_id: str | None, collection_id: str) -> dict[str, Any]:
        with self._jobs_lock:
            with self._conn() as conn:
                self._collection(conn, collection_id, owner_id, write=True)
                documents = conn.execute("SELECT id,stored_path,parsed_path FROM documents WHERE collection_id=?",
                                         (collection_id,)).fetchall()
                if any(row["id"] in self._active_documents for row in documents):
                    raise KnowledgeError(409, "集合中有文档正在解析，请稍后再删除")
                for document in documents:
                    conn.execute("DELETE FROM knowledge_fts WHERE document_id=?", (document["id"],))
                conn.execute("DELETE FROM collections WHERE id=?", (collection_id,))
        for document in documents:
            self._unlink_owned(document["stored_path"])
            self._unlink_owned(document["parsed_path"])
        return {"deleted": collection_id}

    def clear_collection(self, owner_id: str | None, collection_id: str) -> dict[str, Any]:
        """清空集合里的全部文档与索引，**保留集合本身**。

        与 ``delete_collection`` 的区别就在这里：用户往往只想换一批文档，
        不想连名字、说明、可见范围一起重建。解析中的文档一律拦住，
        否则后台线程会往一个已经删掉的文档 id 上写回结果。
        """
        with self._jobs_lock:
            with self._conn() as conn:
                self._collection(conn, collection_id, owner_id, write=True)
                documents = conn.execute(
                    "SELECT id,stored_path,parsed_path FROM documents WHERE collection_id=?",
                    (collection_id,),
                ).fetchall()
                if any(row["id"] in self._active_documents for row in documents):
                    raise KnowledgeError(409, "集合中有文档正在解析，请稍后再清空")
                for document in documents:
                    conn.execute("DELETE FROM knowledge_fts WHERE document_id=?", (document["id"],))
                # chunks 上有 ON DELETE CASCADE，删 documents 会连带清掉。
                conn.execute("DELETE FROM documents WHERE collection_id=?", (collection_id,))
                conn.execute("UPDATE collections SET updated_at=? WHERE id=?",
                             (time.time(), collection_id))
        for document in documents:
            self._unlink_owned(document["stored_path"])
            self._unlink_owned(document["parsed_path"])
        return {"collection_id": collection_id, "deleted": len(documents)}

    def _unlink_owned(self, value: str | None) -> None:
        if not value:
            return
        try:
            path = Path(value).resolve()
            if path.is_relative_to(self.files_dir.resolve()) and path.is_file():
                path.unlink()
        except OSError:
            pass

    def accept_upload(self, owner_id: str | None, collection_id: str, original_name: str,
                      temporary_path: Path, sha256: str) -> dict[str, Any]:
        original_name = safe_filename(original_name)
        extension = Path(original_name).suffix.lower()
        if extension not in SUPPORTED_EXTENSIONS:
            raise KnowledgeError(415, f"暂不支持 {extension or '无扩展名'} 文件")
        doc_id = uuid.uuid4().hex
        destination = self.files_dir / f"{doc_id}{extension}"
        parsed_path = self.files_dir / f"{doc_id}.parsed.txt"
        size = temporary_path.stat().st_size
        now = time.time()
        try:
            with self._conn() as conn:
                collection = self._collection(conn, collection_id, owner_id, write=True)
                version = int(conn.execute(
                    "SELECT COALESCE(MAX(version),0)+1 FROM documents WHERE collection_id=? "
                    "AND lower(original_name)=lower(?)", (collection_id, original_name),
                ).fetchone()[0])
                os.replace(temporary_path, destination)
                conn.execute(
                    "INSERT INTO documents(id,collection_id,owner_id,original_name,stored_path,parsed_path,"
                    "extension,size_bytes,sha256,version,status,note,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,'queued','',?,?)",
                    (doc_id, collection_id, owner_id, original_name, str(destination), str(parsed_path),
                     extension, size, sha256, version, now, now),
                )
                conn.execute("UPDATE collections SET updated_at=? WHERE id=?", (now, collection["id"]))
        except Exception:
            self._unlink_owned(str(destination))
            raise
        return self.get_document(owner_id, doc_id)

    @staticmethod
    def _document_dict(row, *, can_edit=False) -> dict[str, Any]:
        return {
            "id": row["id"], "collection_id": row["collection_id"], "owner_id": row["owner_id"],
            "original_name": row["original_name"], "extension": row["extension"],
            "size_bytes": row["size_bytes"], "sha256": row["sha256"], "version": row["version"],
            "status": row["status"], "note": row["note"], "created_at": row["created_at"],
            "updated_at": row["updated_at"], "parsed_at": row["parsed_at"], "can_edit": can_edit,
        }

    def get_document(self, owner_id: str | None, document_id: str) -> dict[str, Any]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT d.*,c.owner_id AS collection_owner,c.visibility FROM documents d "
                "JOIN collections c ON c.id=d.collection_id WHERE d.id=?", (document_id,),
            ).fetchone()
        if not row or (row["collection_owner"] != owner_id and row["visibility"] != "shared"):
            raise KnowledgeError(404, "没有这篇文档")
        data = self._document_dict(row, can_edit=row["collection_owner"] == owner_id)
        data["collection_name"] = self.collection_name(row["collection_id"])
        return data

    def collection_name(self, collection_id: str) -> str:
        with self._conn() as conn:
            row = conn.execute("SELECT name FROM collections WHERE id=?", (collection_id,)).fetchone()
        return row["name"] if row else ""

    def list_documents(self, owner_id: str | None, collection_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            collection = self._collection(conn, collection_id, owner_id)
            rows = conn.execute("SELECT * FROM documents WHERE collection_id=? ORDER BY created_at DESC,version DESC",
                                (collection_id,)).fetchall()
        return [self._document_dict(row, can_edit=collection["owner_id"] == owner_id) for row in rows]

    def rebuild_document(self, document_id: str) -> None:
        with self._jobs_lock:
            if document_id in self._active_documents:
                return
            self._active_documents.add(document_id)
        try:
            self._rebuild_document(document_id)
        finally:
            with self._jobs_lock:
                self._active_documents.discard(document_id)

    def _rebuild_document(self, document_id: str) -> None:
        """Parse one source in full; there is deliberately no app-level size cap."""
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
            if not row:
                return
            conn.execute("UPDATE documents SET status='processing',note='',updated_at=? WHERE id=?",
                         (time.time(), document_id))
        collection_id = str(row["collection_id"])
        owner_id = row["owner_id"]
        source = Path(row["stored_path"])
        parsed = Path(row["parsed_path"])
        temporary_text = parsed.with_suffix(parsed.suffix + ".tmp")
        try:
            text, note = indexer.extract_text(source, ocr=self.ocr, unlimited=True)
            if not text:
                raise ValueError(note or "文档中没有可检索的文字")
            pieces: list[tuple[int, int, int, str]] = []
            for ordinal, start in enumerate(range(0, len(text), CHUNK_STEP)):
                content = text[start:start + indexer.CHUNK_SIZE]
                if content.strip():
                    pieces.append((ordinal, start, min(start + indexer.CHUNK_SIZE, len(text)), content))
            temporary_text.write_text(text, encoding="utf-8")
            os.replace(temporary_text, parsed)
            now = time.time()
            with self._conn() as conn:
                conn.execute("DELETE FROM knowledge_fts WHERE document_id=?", (document_id,))
                conn.execute("DELETE FROM chunks WHERE document_id=?", (document_id,))
                for ordinal, start, end, content in pieces:
                    conn.execute("INSERT INTO chunks(document_id,ordinal,start_offset,end_offset,content) "
                                 "VALUES(?,?,?,?,?)", (document_id, ordinal, start, end, content))
                    conn.execute("INSERT INTO knowledge_fts(content,document_id,ordinal) VALUES(?,?,?)",
                                 (content, document_id, ordinal))
                conn.execute("UPDATE documents SET status='ready',note='',parsed_at=?,updated_at=? WHERE id=?",
                             (now, now, document_id))
            self._publish_document(document_id, collection_id, "ready", owner_id)
        except Exception as exc:
            self._unlink_owned(str(temporary_text))
            with self._conn() as conn:
                conn.execute("UPDATE documents SET status='failed',note=?,updated_at=? WHERE id=?",
                             (f"解析失败：{type(exc).__name__}: {exc}", time.time(), document_id))
            self._publish_document(document_id, collection_id, "failed", owner_id)

    def queue_rebuild(self, owner_id: str | None, document_id: str) -> dict[str, Any]:
        with self._jobs_lock:
            if document_id in self._active_documents:
                raise KnowledgeError(409, "文档正在重建")
            with self._conn() as conn:
                row = conn.execute("SELECT d.*,c.owner_id AS collection_owner,c.visibility FROM documents d "
                                   "JOIN collections c ON c.id=d.collection_id WHERE d.id=?", (document_id,)).fetchone()
                if not row or (row["collection_owner"] != owner_id and row["visibility"] != "shared"):
                    raise KnowledgeError(404, "没有这篇文档")
                if row["collection_owner"] != owner_id:
                    raise KnowledgeError(403, "共享文档只有创建者可以重建")
                if row["status"] in ("queued", "processing"):
                    raise KnowledgeError(409, "文档已在等待或正在解析")
                conn.execute("UPDATE documents SET status='queued',note='',updated_at=? WHERE id=?",
                             (time.time(), document_id))
        return self.get_document(owner_id, document_id)

    def delete_document(self, owner_id: str | None, document_id: str) -> dict[str, Any]:
        with self._jobs_lock:
            if document_id in self._active_documents:
                raise KnowledgeError(409, "文档正在解析，请稍后再删除")
            with self._conn() as conn:
                row = conn.execute("SELECT d.*,c.owner_id AS collection_owner,c.visibility FROM documents d "
                                   "JOIN collections c ON c.id=d.collection_id WHERE d.id=?", (document_id,)).fetchone()
                if not row or (row["collection_owner"] != owner_id and row["visibility"] != "shared"):
                    raise KnowledgeError(404, "没有这篇文档")
                if row["collection_owner"] != owner_id:
                    raise KnowledgeError(403, "共享文档只有创建者可以删除")
                conn.execute("DELETE FROM knowledge_fts WHERE document_id=?", (document_id,))
                conn.execute("DELETE FROM documents WHERE id=?", (document_id,))
                conn.execute("UPDATE collections SET updated_at=? WHERE id=?", (time.time(), row["collection_id"]))
        self._unlink_owned(row["stored_path"])
        self._unlink_owned(row["parsed_path"])
        return {"deleted": document_id}

    def source_path(self, owner_id: str | None, document_id: str) -> tuple[Path, str]:
        with self._conn() as conn:
            row = conn.execute("SELECT d.*,c.owner_id AS collection_owner,c.visibility FROM documents d "
                               "JOIN collections c ON c.id=d.collection_id WHERE d.id=?", (document_id,)).fetchone()
        if not row or (row["collection_owner"] != owner_id and row["visibility"] != "shared"):
            raise KnowledgeError(404, "没有这篇文档")
        path = Path(row["stored_path"]).resolve()
        if not path.is_relative_to(self.files_dir.resolve()) or not path.is_file():
            raise KnowledgeError(404, "原始文件已不存在")
        return path, row["original_name"]

    def parsed_text(self, owner_id: str | None, document_id: str, offset: int, limit: int) -> dict[str, Any]:
        doc = self.get_document(owner_id, document_id)
        if not doc["parsed_at"]:
            raise KnowledgeError(409, "文档尚未解析完成")
        with self._conn() as conn:
            row = conn.execute("SELECT parsed_path FROM documents WHERE id=?", (document_id,)).fetchone()
        try:
            path = Path(row["parsed_path"])
            text = path.read_text(encoding="utf-8")
        except OSError:
            raise KnowledgeError(404, "解析文本暂不可用，请重新构建") from None
        start = max(0, min(int(offset), len(text)))
        size = max(100, min(int(limit), 20000))
        return {"document": doc, "text": text[start:start + size], "offset": start,
                "total_chars": len(text), "has_more": start + size < len(text)}

    def search(self, owner_id: str | None, query: str, limit: int,
               collection_id: str | None = None) -> list[dict[str, Any]]:
        """检索。``collection_id`` 为空 = 全部可见集合；给了就只搜这一个。

        两个坑：
        1. 限定集合前先校验可见性 —— 否则传别人的私有集合 id，就能借
           搜索接口读到内容（可见性条件本身只按 owner / shared 过滤）。
        2. 兜底的模糊匹配用 ``instr`` 而不是 ``LIKE '%q%'``：用户搜
           ``%`` / ``_`` 是想找这两个字，不该被当通配符把全库捞出来。
        """
        query = query.strip()
        if not query:
            return []
        visible, args = self._visible_sql(owner_id)
        scope = ""
        scope_args: tuple[Any, ...] = ()
        if collection_id:
            with self._conn() as conn:
                self._collection(conn, collection_id, owner_id)
            scope = " AND d.collection_id = ?"
            scope_args = (collection_id,)
        with self._conn() as conn:
            if len(query) >= 3:
                phrase = '"' + query.replace('"', '""') + '"'
                try:
                    rows = conn.execute(
                        "SELECT d.id,d.collection_id,d.original_name,d.version,c.name AS collection_name,"
                        "k.ordinal,ch.start_offset,ch.end_offset,snippet(knowledge_fts,0,'「','」','…',14) AS snippet "
                        "FROM knowledge_fts k JOIN chunks ch ON ch.document_id=k.document_id AND ch.ordinal=CAST(k.ordinal AS INTEGER) "
                        "JOIN documents d ON d.id=k.document_id JOIN collections c ON c.id=d.collection_id "
                        f"WHERE k.content MATCH ? AND d.parsed_at IS NOT NULL AND {visible}{scope} "
                        "ORDER BY rank LIMIT ?", (phrase, *args, *scope_args, limit),
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = []
            else:
                rows = []
            if not rows:
                rows = conn.execute(
                    "SELECT d.id,d.collection_id,d.original_name,d.version,c.name AS collection_name,"
                    "ch.ordinal,ch.start_offset,ch.end_offset,substr(ch.content,1,260) AS snippet "
                    "FROM chunks ch JOIN documents d ON d.id=ch.document_id JOIN collections c ON c.id=d.collection_id "
                    f"WHERE instr(lower(ch.content), lower(?)) > 0 AND d.parsed_at IS NOT NULL AND {visible}{scope} "
                    "ORDER BY d.created_at DESC,ch.ordinal LIMIT ?", (query, *args, *scope_args, limit),
                ).fetchall()
        return [{
            "document_id": r["id"], "collection_id": r["collection_id"],
            "collection_name": r["collection_name"], "document_name": r["original_name"],
            "version": r["version"], "chunk_number": int(r["ordinal"]) + 1,
            "start_offset": r["start_offset"], "end_offset": r["end_offset"],
            "snippet": r["snippet"],
        } for r in rows]

