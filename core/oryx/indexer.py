"""本地内容索引：扫描 → 解析 → 分块 → FTS5 入库。

设计要点：
- 索引永远在本地，切换模型模式不影响索引；
- 增量更新：size + mtime 未变则跳过；
- FTS5 用 trigram 分词，中文按子串命中（2 字以内退化为 LIKE）；
- 跳过禁区目录、系统目录、可执行/二进制文件与超大文件。
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .config import app_dir
from .policy import is_credential_path
from .store import DEFAULT_INDEX_PATH
from .symlink_guard import is_junction

CHUNK_SIZE = 900
CHUNK_OVERLAP = 120
MAX_CHUNKS_PER_FILE = 200
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_PAGES = 200
MAX_ROWS = 5000
# 语义检索的最低相似度。实测：相关文档 0.57-0.72，无关文档 0.23-0.32，取中间值。
DEFAULT_SEMANTIC_THRESHOLD = 0.45
# 跨文件批量 embedding，批越大越省；太小则每次都要付一次推理开销
EMBED_BATCH = 48

TEXT_EXT = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".yaml", ".yml",
    ".ini", ".cfg", ".log", ".py", ".js", ".ts", ".tsx", ".jsx", ".html",
    ".htm", ".css", ".java", ".c", ".cpp", ".h", ".go", ".rs", ".sql",
    ".sh", ".bat", ".ps1", ".xml", ".toml",
}
DOC_EXT = {".pdf", ".docx", ".xlsx", ".pptx"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}
SUPPORTED_EXT = TEXT_EXT | DOC_EXT | IMAGE_EXT

SKIP_DIRS = {
    "node_modules", ".git", ".svn", "__pycache__", ".venv", "venv", "env",
    ".idea", ".vscode", "$recycle.bin", "system volume information",
    "windows", "program files", "program files (x86)", "programdata",
    "appdata", ".cache", "dist", "build", ".next", ".gradle", "target",
    "packages", ".nuget", "site-packages",
}

# 扫描三重上限：深度 / 条目 / 时间。
# 扫全盘没有上限 = 一个能跑几小时的接口，还会把磁盘 IO 打满。
DEFAULT_MAX_DEPTH = 12
DEFAULT_SCAN_TIMEOUT = 1800  # 秒

# 上限命中标签的固定顺序（对外自述用）
_LIMIT_ORDER = ("深度", "条目", "时间")


def _add_limit(stats: "ScanStats", name: str) -> None:
    """记一个上限命中。同一个上限只记一次。"""
    if name not in stats.limit_hits:
        stats.limit_hits.append(name)
    stats.limit_hits.sort(key=lambda n: _LIMIT_ORDER.index(n) if n in _LIMIT_ORDER else 99)


def _is_link_dir(path: Path) -> bool:
    """这个路径是不是指向目录的链接（符号链接或 junction）。"""
    try:
        if path.is_symlink():
            return True
    except OSError:
        return True
    return is_junction(str(path))


def _is_link_file(path: Path) -> bool:
    """这个路径是不是符号链接。"""
    try:
        return path.is_symlink()
    except OSError:
        return True

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id          INTEGER PRIMARY KEY,
    path        TEXT UNIQUE NOT NULL,
    size        INTEGER,
    mtime       REAL,
    ext         TEXT,
    status      TEXT,
    note        TEXT,
    indexed_at  REAL
);
CREATE TABLE IF NOT EXISTS chunks (
    id          INTEGER PRIMARY KEY,
    file_id     INTEGER NOT NULL,
    ord         INTEGER,
    text        TEXT,
    hash        TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text,
    file_id UNINDEXED,
    tokenize='trigram'
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_num USING fts5(
    digits,
    file_id UNINDEXED,
    tokenize='trigram'
);
CREATE TABLE IF NOT EXISTS chunk_vecs (
    chunk_id    INTEGER PRIMARY KEY,
    file_id     INTEGER NOT NULL,
    dim         INTEGER,
    vec         BLOB
);
CREATE INDEX IF NOT EXISTS idx_vecs_file ON chunk_vecs(file_id);
CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks(file_id);
CREATE INDEX IF NOT EXISTS idx_files_path ON files(path);
"""


@dataclass
class ScanStats:
    scanned: int = 0
    indexed: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    chars: int = 0
    embedded: int = 0
    reused: int = 0
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    # 扫描有没有被上限截断。partial=True 表示结果不完整 ——
    # 界面与接口都要自述这一点，否则用户会以为扫全了。
    partial: bool = False
    # 触发了哪些上限：深度 / 条目 / 时间
    limit_hits: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.__dict__)
        elapsed = (self.finished_at or time.time()) - self.started_at
        data["elapsed_s"] = round(elapsed, 1)
        return data


# ------------------------------------------------------------------ 文本抽取


def _decode(raw: bytes) -> str:
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return ""


def _clean(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t　]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_text(path: Path, ocr: bool = True, *, unlimited: bool = False) -> tuple[str, str]:
    """返回 (正文, 说明)。说明用于记录跳过的原因。"""
    ext = path.suffix.lower()
    try:
        if ext in IMAGE_EXT:
            if not ocr:
                return "", "图片 OCR 已关闭"
            from .ocr import ocr_image

            result = ocr_image(path)
            if not result.get("ok"):
                return "", result.get("error", "OCR 失败")
            text = (result.get("text") or "").strip()
            return (_clean(text), "" if text else "图片中没有识别到文字")
        if ext in TEXT_EXT:
            return _clean(_decode(path.read_bytes())), ""
        if ext == ".pdf":
            return _extract_pdf(path, max_pages=None if unlimited else MAX_PAGES), ""
        if ext == ".docx":
            return _extract_docx(path), ""
        if ext == ".xlsx":
            return _extract_xlsx(path, max_rows=None if unlimited else MAX_ROWS), ""
        if ext == ".pptx":
            return _extract_pptx(path), ""
    except Exception as exc:
        return "", f"解析失败：{type(exc).__name__}: {exc}"
    return "", "不支持的文件类型"


def _extract_pdf(path: Path, max_pages: int | None = MAX_PAGES) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        from PyPDF2 import PdfReader  # type: ignore

    reader = PdfReader(str(path))
    parts: list[str] = []
    for index, page in enumerate(reader.pages):
        if max_pages is not None and index >= max_pages:
            break
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            continue
    return _clean("\n".join(parts))


def _extract_docx(path: Path) -> str:
    try:
        import docx  # type: ignore

        document = docx.Document(str(path))
        parts = [p.text for p in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                parts.append(" | ".join(cell.text for cell in row.cells))
        return _clean("\n".join(parts))
    except Exception:
        # 退化为直接读 XML
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml").decode("utf-8", errors="ignore")
        return _clean(re.sub(r"<[^>]+>", " ", xml))


def _extract_xlsx(path: Path, max_rows: int | None = MAX_ROWS) -> str:
    try:
        import openpyxl  # type: ignore

        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        parts: list[str] = []
        count = 0
        for sheet in wb.worksheets:
            parts.append(f"[工作表] {sheet.title}")
            for row in sheet.iter_rows(values_only=True):
                cells = [str(c) for c in row if c is not None]
                if cells:
                    parts.append(" | ".join(cells))
                    count += 1
                if max_rows is not None and count >= max_rows:
                    break
            if max_rows is not None and count >= max_rows:
                break
        wb.close()
        return _clean("\n".join(parts))
    except Exception:
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if n.startswith("xl/sharedStrings")]
            chunks = []
            for name in names:
                xml = zf.read(name).decode("utf-8", errors="ignore")
                chunks.extend(re.findall(r"<t[^>]*>(.*?)</t>", xml, re.S))
        return _clean("\n".join(chunks))


def _extract_pptx(path: Path) -> str:
    parts: list[str] = []
    with zipfile.ZipFile(path) as zf:
        for name in sorted(n for n in zf.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)):
            xml = zf.read(name).decode("utf-8", errors="ignore")
            texts = re.findall(r"<a:t>(.*?)</a:t>", xml, re.S)
            if texts:
                parts.append("\n".join(texts))
    return _clean("\n\n".join(parts))


def digit_digest(text: str) -> str:
    """把文本里的数字序列抽出来，去掉千分位与空格，便于按金额、编号检索。"""
    runs = re.findall(r"\d[\d,，]{1,}", text)
    cleaned = {r.replace(",", "").replace("，", "") for r in runs}
    return " ".join(sorted(cleaned))


def _hash_piece(text: str) -> str:
    """片段内容指纹，用于判断向量能否复用。"""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def chunk_text(text: str, *, max_chunks: int | None = MAX_CHUNKS_PER_FILE) -> list[str]:
    if not text:
        return []
    chunks: list[str] = []
    step = CHUNK_SIZE - CHUNK_OVERLAP
    for start in range(0, len(text), step):
        piece = text[start : start + CHUNK_SIZE]
        if piece.strip():
            chunks.append(piece)
        if max_chunks is not None and len(chunks) >= max_chunks:
            break
    return chunks


# ------------------------------------------------------------------ 索引主体


class FileIndex:
    def __init__(
        self,
        path: Path | None = None,
        forbidden: Iterable[str] | None = None,
        ocr: bool = True,
        embeddings: bool = True,
        semantic_threshold: float = DEFAULT_SEMANTIC_THRESHOLD,
    ):
        self.path = path or DEFAULT_INDEX_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.forbidden = [Path(p).expanduser() for p in (forbidden or [])]
        self.ocr = ocr
        self.embeddings = embeddings
        self.semantic_threshold = semantic_threshold
        self._vec_cache: tuple[Any, Any, Any, Any] | None = None
        self._pending: list[tuple[int, int, str]] = []  # (chunk_id, file_id, text)
        self._lock = threading.Lock()
        self.scanning = False
        self.last_stats: ScanStats | None = None
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """兼容旧库：补上 chunks.hash 列。"""
        columns = {row[1] for row in conn.execute("PRAGMA table_info(chunks)")}
        if "hash" not in columns:
            conn.execute("ALTER TABLE chunks ADD COLUMN hash TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_hash ON chunks(hash)")

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    # ---------- 状态 ----------
    def stats(self) -> dict[str, Any]:
        with self._conn() as conn:
            files = conn.execute("SELECT COUNT(*) FROM files WHERE status='ok'").fetchone()[0]
            total = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            vectors = conn.execute("SELECT COUNT(*) FROM chunk_vecs").fetchone()[0]
            size = self.path.stat().st_size if self.path.exists() else 0
        try:
            from . import embeddings as emb

            embed_ready = emb.available()
        except Exception:
            embed_ready = False
        return {
            "files": files,
            "known_files": total,
            "chunks": chunks,
            "vectors": vectors,
            "embedding_ready": embed_ready,
            "db_mb": round(size / 1024 / 1024, 1),
            "scanning": self.scanning,
            "last_scan": self.last_stats.to_dict() if self.last_stats else None,
        }

    # ---------- 库（按目录分组的知识库集合） ----------
    def library_groups(self, limit: int = 60) -> list[dict[str, Any]]:
        """把索引到的文件按所在目录分组，作为「知识库」卡片。

        语义仍是"我索引了哪些内容"，只是把平铺的文件按目录聚成集合 ——
        Marvis 的知识库卡片就是这个形态。目录本身当库名。
        """
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT path, size, mtime, ext FROM files WHERE status='ok' "
                "ORDER BY mtime DESC"
            ).fetchall()
        groups: dict[str, dict[str, Any]] = {}
        for row in rows:
            path = row["path"] or ""
            parent = str(Path(path).parent).replace("\\", "/") or "/"
            g = groups.get(parent)
            if g is None:
                g = groups[parent] = {
                    "path": parent,
                    "name": Path(parent).name or parent,
                    "file_count": 0,
                    "total_bytes": 0,
                    "last_mtime": 0,
                    "files": [],
                }
            g["file_count"] += 1
            g["total_bytes"] += int(row["size"] or 0)
            mtime = float(row["mtime"] or 0)
            if mtime > g["last_mtime"]:
                g["last_mtime"] = mtime
            if len(g["files"]) < 60:
                g["files"].append(
                    {
                        "name": Path(path).name,
                        "path": path,
                        "size": int(row["size"] or 0),
                        "mtime": int(mtime),
                        "ext": row["ext"] or "",
                    }
                )
        out = sorted(groups.values(), key=lambda g: (-g["file_count"], g["name"].lower()))
        return out[:limit]

    def library_files(self, path: str, limit: int = 200) -> list[dict[str, Any]]:
        """某个目录下的已索引文件。"""
        prefix = str(path or "").replace("\\", "/").rstrip("/")
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT path, size, mtime, ext FROM files WHERE status='ok' "
                "ORDER BY mtime DESC LIMIT ?",
                (int(limit) * 4,),
            ).fetchall()
        out = []
        for row in rows:
            p = (row["path"] or "").replace("\\", "/")
            parent = str(Path(p).parent).replace("\\", "/")
            if parent != prefix:
                continue
            out.append(
                {
                    "name": Path(p).name,
                    "path": p,
                    "size": int(row["size"] or 0),
                    "mtime": int(row["mtime"] or 0),
                    "ext": row["ext"] or "",
                }
            )
            if len(out) >= int(limit):
                break
        return out

    # ---------- 扫描 ----------
    def scan(
        self,
        roots: Iterable[str],
        on_progress: Callable[[dict[str, Any]], None] | None = None,
        max_files: int = 50000,
        max_depth: int = DEFAULT_MAX_DEPTH,
        timeout_s: int = DEFAULT_SCAN_TIMEOUT,
    ) -> ScanStats:
        if self.scanning:
            raise RuntimeError("已有扫描在进行中")
        self.scanning = True
        stats = ScanStats()
        deadline = time.monotonic() + max(1, int(timeout_s))
        try:
            for root in roots:
                base = Path(os.path.expandvars(os.path.expanduser(root)))
                if not base.exists():
                    continue
                for file_path in self._walk(base, stats, max_files, max_depth, deadline):
                    stats.scanned += 1
                    try:
                        self._index_one(file_path, stats)
                    except Exception:
                        stats.failed += 1
                    if on_progress and stats.scanned % 25 == 0:
                        on_progress({"scanned": stats.scanned, "indexed": stats.indexed,
                                     "current": str(file_path)})
                    if stats.scanned >= max_files:
                        break
                if stats.scanned >= max_files:
                    break
        finally:
            self._flush_pending(stats)
            stats.finished_at = time.time()
            # 触发了上限就自述不完整 —— 界面不能说「扫完了」而其实只扫了一半。
            stats.partial = bool(stats.limit_hits)
            self.last_stats = stats
            self.scanning = False
        if on_progress:
            on_progress({"done": True, **stats.to_dict()})
        return stats

    def _walk(
        self,
        base: Path,
        stats: ScanStats,
        max_files: int,
        max_depth: int = DEFAULT_MAX_DEPTH,
        deadline: float | None = None,
    ):
        seen = 0
        base_str = str(base)
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            current = Path(dirpath)
            if deadline is not None and time.monotonic() >= deadline:
                _add_limit(stats, "时间")
                return
            if self._is_forbidden(current):
                dirnames[:] = []
                continue
            # 深度按「相对根目录的层数」算 —— os.walk 是自顶向下的，
            # 用计数器累加会把同层的兄弟目录算成不同深度。
            rel = os.path.relpath(dirpath, base_str)
            depth = 0 if rel == os.curdir else rel.count(os.sep) + 1
            if max_depth and depth >= max_depth:
                _add_limit(stats, "深度")
                dirnames[:] = []
                continue
            dirnames[:] = [
                d for d in dirnames
                if d.lower() not in SKIP_DIRS and not d.startswith(".")
            ]
            # 目录联接/符号链接不进 —— 跟着走可能绕回祖先目录形成环，
            # 也可能把根目录之外的内容索引进来。
            dirnames[:] = [
                d for d in dirnames if not _is_link_dir(current / d)
            ]
            for name in filenames:
                path = current / name
                # 文件级链接同样跳过：索引的是内容，不是别处的替身。
                if _is_link_file(path):
                    stats.skipped += 1
                    continue
                # 凭据类文件不进索引 —— 索引会把内容切块存库，
                # 之后任何一次 index_search 都能把密钥捞出来。
                # 只看扩展名不够：.env.local 的扩展名是 .local，
                # 而 .ssh/ 里的文件扩展名五花八门 —— 要按路径整体判。
                if is_credential_path(path):
                    stats.skipped += 1
                    continue
                if path.suffix.lower() not in SUPPORTED_EXT:
                    stats.skipped += 1
                    continue
                try:
                    # lstat：不跟随链接取大小
                    if path.lstat().st_size > MAX_FILE_BYTES:
                        stats.skipped += 1
                        continue
                except OSError:
                    stats.skipped += 1
                    continue
                seen += 1
                if seen >= max_files:
                    _add_limit(stats, "条目")
                    return
                yield path

    def _is_forbidden(self, path: Path) -> bool:
        resolved = path.resolve()
        for f in self.forbidden:
            try:
                if resolved == f or f in resolved.parents:
                    return True
            except Exception:
                continue
        return False

    def _index_one(self, path: Path, stats: ScanStats) -> None:
        try:
            stat = path.stat()
        except OSError:
            stats.skipped += 1
            return

        key = str(path.resolve())
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, size, mtime, status FROM files WHERE path=?", (key,)
            ).fetchone()

        if row and row["size"] == stat.st_size and abs(row["mtime"] - stat.st_mtime) < 1:
            stats.skipped += 1
            return

        text, note = extract_text(path, ocr=self.ocr)
        if not text:
            with self._lock, self._conn() as conn:
                self._upsert_file(conn, key, stat, "skipped", note)
                conn.execute("DELETE FROM chunks WHERE file_id=(SELECT id FROM files WHERE path=?)", (key,))
                conn.execute("DELETE FROM chunks_fts WHERE file_id=(SELECT id FROM files WHERE path=?)", (key,))
                conn.execute("DELETE FROM chunks_num WHERE file_id=(SELECT id FROM files WHERE path=?)", (key,))
                conn.execute("DELETE FROM chunk_vecs WHERE file_id=(SELECT id FROM files WHERE path=?)", (key,))
            stats.skipped += 1
            return

        pieces = chunk_text(text)
        hashes = [_hash_piece(piece) for piece in pieces]

        # 复用内容未变的片段向量（同一文件内），避免整文件重算
        reuse: dict[str, bytes] = {}
        if self.embeddings and pieces:
            with self._conn() as conn:
                old_rows = conn.execute(
                    "SELECT c.hash, v.vec FROM chunks c JOIN chunk_vecs v ON v.chunk_id = c.id"
                    " WHERE c.file_id = (SELECT id FROM files WHERE path = ?)",
                    (key,),
                ).fetchall()
            for old_row in old_rows:
                if old_row["hash"]:
                    reuse[old_row["hash"]] = old_row["vec"]

        with self._lock, self._conn() as conn:
            file_id = self._upsert_file(conn, key, stat, "ok", "")
            conn.execute("DELETE FROM chunks WHERE file_id=?", (file_id,))
            conn.execute("DELETE FROM chunks_fts WHERE file_id=?", (file_id,))
            conn.execute("DELETE FROM chunks_num WHERE file_id=?", (file_id,))
            conn.execute("DELETE FROM chunk_vecs WHERE file_id=?", (file_id,))
            for order, piece in enumerate(pieces):
                digest_hash = hashes[order]
                cur = conn.execute(
                    "INSERT INTO chunks(file_id, ord, text, hash) VALUES(?,?,?,?)",
                    (file_id, order, piece, digest_hash),
                )
                chunk_id = cur.lastrowid
                conn.execute(
                    "INSERT INTO chunks_fts(text, file_id) VALUES(?,?)",
                    (piece, file_id),
                )
                digest = digit_digest(piece)
                if len(digest) >= 3:
                    conn.execute(
                        "INSERT INTO chunks_num(digits, file_id) VALUES(?,?)",
                        (digest, file_id),
                    )
                if self.embeddings:
                    cached = reuse.get(digest_hash)
                    if cached is not None:
                        conn.execute(
                            "INSERT INTO chunk_vecs(chunk_id, file_id, dim, vec) VALUES(?,?,?,?)",
                            (chunk_id, file_id, len(cached) // 4, cached),
                        )
                        stats.reused += 1
                    else:
                        self._pending.append((chunk_id, file_id, piece))
        self._vec_cache = None
        stats.chars += len(text)
        if row:
            stats.updated += 1
        else:
            stats.indexed += 1

        if len(self._pending) >= EMBED_BATCH:
            self._flush_pending(stats)

    def _flush_pending(self, stats: ScanStats | None = None) -> None:
        """把攒下的片段一次性送进模型，跨文件批处理。"""
        if not self._pending:
            return
        pending, self._pending = self._pending, []
        if not self.embeddings:
            return
        try:
            from . import embeddings as emb

            if not emb.available():
                return
            vectors = emb.encode([text for _, _, text in pending])
            with self._lock, self._conn() as conn:
                for (chunk_id, file_id, _text), vec in zip(pending, vectors):
                    conn.execute(
                        "INSERT OR REPLACE INTO chunk_vecs(chunk_id, file_id, dim, vec) VALUES(?,?,?,?)",
                        (chunk_id, file_id, int(vec.shape[0]), vec.tobytes()),
                    )
            if stats is not None:
                stats.embedded += len(pending)
            self._vec_cache = None
        except Exception:
            pass  # 向量失败不影响关键词索引

    def _upsert_file(self, conn, key: str, stat, status: str, note: str) -> int:
        conn.execute(
            "INSERT INTO files(path, size, mtime, ext, status, note, indexed_at)"
            " VALUES(?,?,?,?,?,?,?)"
            " ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime,"
            " status=excluded.status, note=excluded.note, indexed_at=excluded.indexed_at",
            (key, stat.st_size, stat.st_mtime, Path(key).suffix.lower(), status, note, time.time()),
        )
        return conn.execute("SELECT id FROM files WHERE path=?", (key,)).fetchone()["id"]

    # ---------- 检索 ----------
    def search(self, query: str, limit: int = 20, mode: str = "hybrid") -> list[dict[str, Any]]:
        """mode: hybrid（默认）| keyword | semantic"""
        query = (query or "").strip()
        if not query:
            return []

        keyword = self._keyword_search(query, limit * 2) if mode != "semantic" else []
        if mode == "keyword":
            return keyword[:limit]

        semantic = self._vector_search(query, limit * 2)
        if mode == "semantic":
            return semantic[:limit]
        if not semantic:
            return keyword[:limit]
        if not keyword:
            return semantic[:limit]
        return self._fuse(keyword, semantic)[:limit]

    # ---------- 关键词 / 数字 ----------
    def _keyword_search(self, query: str, limit: int) -> list[dict[str, Any]]:
        digits = re.sub(r"[\s,，.．]", "", query)
        rows: list[sqlite3.Row] = []
        with self._conn() as conn:
            if digits.isdigit() and len(digits) >= 3:
                rows = conn.execute(
                    "SELECT f.path, f.mtime, f.size,"
                    " (SELECT substr(c.text,1,160) FROM chunks c WHERE c.file_id=f.id LIMIT 1) AS snip"
                    " FROM chunks_num JOIN files f ON f.id = chunks_num.file_id"
                    " WHERE chunks_num MATCH ? ORDER BY rank LIMIT ?",
                    ('"' + digits + '"', limit),
                ).fetchall()

            if not rows and len(query) >= 3:
                try:
                    rows = conn.execute(
                        "SELECT f.path, f.mtime, f.size, snippet(chunks_fts, 0, '<<', '>>', '…', 10) AS snip"
                        " FROM chunks_fts JOIN files f ON f.id = chunks_fts.file_id"
                        " WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?",
                        ('"' + query.replace('"', '""') + '"', limit),
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = []

            if not rows:
                rows = conn.execute(
                    "SELECT f.path, f.mtime, f.size, substr(c.text, 1, 160) AS snip"
                    " FROM chunks c JOIN files f ON f.id = c.file_id"
                    " WHERE c.text LIKE ? LIMIT ?",
                    (f"%{query}%", limit),
                ).fetchall()

        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for r in rows:
            if r["path"] in seen:
                continue
            seen.add(r["path"])
            results.append(
                {
                    "path": r["path"],
                    "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(r["mtime"] or 0)),
                    "size": r["size"],
                    "snippet": r["snip"],
                    "source": "keyword",
                }
            )
            if len(results) >= limit:
                break
        return results

    # ---------- 语义 ----------
    def _load_vec_cache(self):
        if self._vec_cache is not None:
            return self._vec_cache
        import numpy as np

        with self._conn() as conn:
            count = conn.execute("SELECT COUNT(*) FROM chunk_vecs").fetchone()[0]
            if count == 0 or count > 200_000:
                return None
            rows = conn.execute(
                "SELECT v.vec, f.path, f.mtime, f.size,"
                " (SELECT substr(c.text,1,160) FROM chunks c WHERE c.file_id=f.id LIMIT 1) AS snip"
                " FROM chunk_vecs v JOIN files f ON f.id = v.file_id"
            ).fetchall()

        if not rows:
            return None
        matrix = np.vstack([np.frombuffer(r["vec"], dtype="float32") for r in rows])
        items = [
            {
                "path": r["path"],
                "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(r["mtime"] or 0)),
                "size": r["size"],
                "snippet": r["snip"],
                "source": "semantic",
            }
            for r in rows
        ]
        self._vec_cache = (matrix, items)
        return self._vec_cache

    def _vector_search(self, query: str, limit: int) -> list[dict[str, Any]]:
        try:
            import numpy as np

            from . import embeddings as emb

            if not emb.available():
                return []
            cache = self._load_vec_cache()
            if cache is None:
                return []
            matrix, items = cache
            qvec = emb.encode([query], is_query=True)[0]
            scores = matrix @ qvec
            order = np.argsort(-scores)[: limit * 3]
        except Exception:
            return []

        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index in order:
            score = float(scores[int(index)])
            if score < self.semantic_threshold:
                break  # 已按分数降序，后面的只会更低
            item = items[int(index)]
            if item["path"] in seen:
                continue
            seen.add(item["path"])
            results.append({**item, "score": round(score, 4)})
            if len(results) >= limit:
                break
        return results

    @staticmethod
    def _fuse(keyword: list[dict[str, Any]], semantic: list[dict[str, Any]], k: int = 60):
        """Reciprocal Rank Fusion：两路排名融合，按路径去重。"""
        scores: dict[str, float] = {}
        meta: dict[str, dict[str, Any]] = {}
        for ranked in (keyword, semantic):
            for rank, item in enumerate(ranked):
                path = item["path"]
                scores[path] = scores.get(path, 0.0) + 1.0 / (k + rank + 1)
                meta.setdefault(path, item)
        ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        return [{**meta[p], "rrf": round(s, 4)} for p, s in ordered]

    def clear(self) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM chunks_fts")
            conn.execute("DELETE FROM chunks_num")
            conn.execute("DELETE FROM chunk_vecs")
            conn.execute("DELETE FROM chunks")
            conn.execute("DELETE FROM files")
        self._vec_cache = None
