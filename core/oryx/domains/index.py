"""索引域：本地内容索引、磁盘列表、图库。

对外：
    GET  /api/index/status     索引状态
    POST /api/index/scan       扫描目录建索引
    POST /api/index/search     检索（FTS5 + 中文 trigram + 可选向量）
    POST /api/index/clear      清空索引
    GET  /api/index/libraries  按目录分组的「知识库」集合（Marvis 形态的知识库卡片）
    GET  /api/index/library   某个库（目录）下的已索引文件
    GET  /api/drives           本机可用磁盘
    GET  /api/images           图库：只收「值得看的图片」
    GET  /api/image/thumb      图片库缩略图（按需缩，原图直出会吃光内存）
"""
from __future__ import annotations

import io
import mimetypes
import os
import shutil
import string
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from ..ipc import IpcError
from ..store import Store
from ._common import json, rpc_failure

router = APIRouter(prefix="/api", tags=["索引与图库"])


class ScanRequest(BaseModel):
    roots: list[str] | None = None


class SearchRequest(BaseModel):
    query: str
    limit: int = 20
    mode: str = "hybrid"


# ── 索引 ──────────────────────────────────────────────────────────────

async def index_status(agent) -> dict:
    try:
        return await agent.call_async("index.stats")
    except IpcError as exc:
        raise rpc_failure(exc) from None


async def index_scan(agent, config, req: ScanRequest) -> dict:
    roots = req.roots or config.allowed_roots
    try:
        return await agent.call_async("index.scan", roots=roots)
    except IpcError as exc:
        raise rpc_failure(exc) from None


async def index_search(agent, req: SearchRequest) -> dict:
    try:
        hits = await agent.call_async(
            "index.search", query=req.query, limit=int(req.limit), mode=req.mode
        )
    except IpcError as exc:
        raise rpc_failure(exc) from None
    return {"query": req.query, "mode": req.mode, "hits": hits, "count": len(hits)}


async def index_clear(agent) -> dict:
    try:
        return await agent.call_async("index.clear")
    except IpcError as exc:
        raise rpc_failure(exc) from None


async def index_libraries(agent, limit: int = 60) -> dict:
    """按目录分组的「知识库」集合 —— Marvis 形态的知识库卡片。"""
    try:
        return await agent.call_async("index.libraries", limit=int(limit))
    except IpcError as exc:
        raise rpc_failure(exc) from None


async def index_library(agent, path: str, limit: int = 200) -> dict:
    """某个库（目录）下的已索引文件。"""
    try:
        return await agent.call_async(
            "index.library", path=path, limit=int(limit)
        )
    except IpcError as exc:
        raise rpc_failure(exc) from None


# ── 磁盘 ──────────────────────────────────────────────────────────────

def list_drives() -> dict:
    """列出本机可用磁盘（Windows 用逻辑驱动器，类 Unix 用挂载点）。

    **返回 dict 而不是 JSONResponse** ——
    调用方 `_drives` 会用 `json(...)` 包一层（那是全域统一的出口）。
    这里自己返回一个 Response，会被套成 `JSONResponse(JSONResponse(...))`，
    序列化直接炸 → 500。
    """
    import shutil
    import string

    items: list[dict[str, Any]] = []
    if os.name == "nt":
        for letter in string.ascii_uppercase:
            root = f"{letter}:\\"
            if not os.path.exists(root):
                continue
            try:
                usage = shutil.disk_usage(root)
                total = usage.total or 1
                items.append(
                    {
                        "name": f"{letter}:",
                        "label": f"本地磁盘 ({letter}:)",
                        "root": root,
                        "kind": "local",
                        "total_gb": round(total / 1024**3, 1),
                        "used_gb": round((total - usage.free) / 1024**3, 1),
                        "free_gb": round(usage.free / 1024**3, 1),
                        "percent": round((total - usage.free) / total * 100, 1),
                    }
                )
            except OSError:
                continue
    else:  # pragma: no cover - 开发机是 Windows
        for mount in ("/", "/mnt", "/media"):
            if not os.path.isdir(mount):
                continue
            try:
                usage = shutil.disk_usage(mount)
                total = usage.total or 1
                items.append(
                    {
                        "name": mount,
                        "label": mount,
                        "root": mount,
                        "kind": "mount",
                        "total_gb": round(total / 1024**3, 1),
                        "used_gb": round((total - usage.free) / 1024**3, 1),
                        "free_gb": round(usage.free / 1024**3, 1),
                        "percent": round((total - usage.free) / total * 100, 1),
                    }
                )
            except OSError:
                continue
    return {"drives": items}



def under_allowed_roots(config, target: Path) -> bool:
    """路径必须落在授权目录内，越界（../ 或符号链接逃逸）一律拒绝。"""
    try:
        real = target.resolve()
    except OSError:
        return False
    for root in config.allowed_roots or []:
        try:
            if real.is_relative_to(Path(root).resolve()):
                return True
        except OSError:
            continue
    return False


def list_images(store: Store, config, limit: int = 400) -> dict:
    """图库：只收「值得看的图片」。

    三类来源：对话里聊过的图、常用图片目录里的图、最近改过的图。
    图标和小杂物在收集阶段就被挡掉了 —— 扫一遍整盘能找出几百个图标，
    用户一眼看到的就是「一堆图标」，那不叫图库。

    每张图带 source 字段（chat / common / recent），前端据此分区。
    """
    from .. import imagegallery

    try:
        chat_paths = store.chat_image_paths(limit=400)
    except Exception:
        chat_paths = []
    return imagegallery.collect_cached(
        allowed_roots=config.allowed_roots,
        chat_images=chat_paths,
        limit=limit,
    )


def image_thumb(config, path: str, w: int = 320):
    """图片库缩略图。

    原图直出会让几百张图的网格把内存吃光，所以按需缩到 w 像素宽。
    Pillow 缺失时退回原图，浏览器照样能显示。
    """
    import io
    import mimetypes

    w = max(48, min(int(w), 2048))
    full = Path(path)
    if not under_allowed_roots(config, full):
        raise HTTPException(403, "该路径不在授权范围内")
    suffixes = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
    if full.suffix.lower() not in suffixes or not full.is_file():
        raise HTTPException(404, "文件不存在或不是图片")

    mime = mimetypes.guess_type(full.name)[0] or "application/octet-stream"
    try:
        from PIL import Image
    except ImportError:
        return FileResponse(full, media_type=mime)

    try:
        with Image.open(full) as im:
            im.thumbnail((w, w), Image.LANCZOS)  # 只缩不放大，保持原比例
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGB")
            buf = io.BytesIO()
            fmt = "PNG" if im.mode == "RGBA" else "JPEG"
            out_mime = "image/png" if fmt == "PNG" else "image/jpeg"
            im.save(buf, fmt, quality=82, optimize=True)
    except OSError as exc:
        raise HTTPException(415, f"无法解码该图片：{exc}") from exc
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type=out_mime,
        headers={"Cache-Control": "private, max-age=3600"},
    )



def register(app, config, agent, store: Store) -> None:
    """把本域路由挂到 app。依赖用闭包注入，action 本身保持纯函数。"""

    @router.get("/index/status")
    async def _status():
        return json(await index_status(agent))

    @router.post("/index/scan")
    async def _scan(req: ScanRequest):
        return json(await index_scan(agent, config, req))

    @router.post("/index/search")
    async def _search(req: SearchRequest):
        return json(await index_search(agent, req))

    @router.post("/index/clear")
    async def _clear():
        return json(await index_clear(agent))

    @router.get("/index/libraries")
    async def _libraries(limit: int = 60):
        return json(await index_libraries(agent, limit))

    @router.get("/index/library")
    async def _library(path: str, limit: int = 200):
        return json(await index_library(agent, path, limit))

    @router.get("/drives")
    async def _drives():
        return json(list_drives())

    @router.get("/images")
    async def _images(limit: int = 400):
        return json(list_images(store, config, limit))

    @router.get("/image/thumb")
    async def _thumb(path: str, w: int = 320):
        return image_thumb(config, path, w)

    app.include_router(router)
