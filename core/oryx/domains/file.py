"""文件域：产物预览/打开/列出、工作区切换、项目文档。

对外：
    GET  /api/files/preview   在界面里预览产物（HTML 看板 / SVG / 图片 / PDF / 文本）
    POST /api/files/open      用系统默认程序打开（或在文件管理器里定位）
    GET  /api/files/list      列出允许范围内的文件（按修改时间倒序）
    GET  /api/workspace       当前工作区概况
    POST /api/workspace       切换工作区
    GET  /api/doc             项目文档（需求 / 决策 / 变更）

**路径闸门**
----------
产物类接口共用同一套路径校验（`_checked_file`），避免两份校验迟早漂移：
必须真实存在、必须在 allowed_roots 内、不能落在 forbidden_paths。
少了任何一条，这个接口都会变成「读任意路径的探针」。
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from ..ipc import IpcError
from ._common import json, rpc_failure

router = APIRouter(prefix="/api/files", tags=["文件与产物"])
ws_router = APIRouter(prefix="/api/workspace", tags=["工作区"])
doc_router = APIRouter(prefix="/api/doc", tags=["项目文档"])

# 本域有**三个** router —— 统一暴露成列表，
# 免得「只看 router 属性」的检查漏掉另外两个。
routers = (router, ws_router, doc_router)

# 只允许预览「看得到内容」的格式，不把接口变成任意文件读取器
PREVIEW_EXTS = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
    ".csv": "text/plain; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}
PREVIEW_MAX_BYTES = 8 * 1024 * 1024


class WorkspaceRequest(BaseModel):
    path: str = ""


class OpenFileRequest(BaseModel):
    """打开 / 定位产物。`path` 为空仍由业务层给中文 400，不改成 422。"""
    path: str = ""
    reveal: bool = False


def checked_file(config, path: Any) -> Path:
    """产物类接口的统一路径闸门。

    必须真实存在、必须在 allowed_roots 内、不能落在 forbidden_paths。
    """
    raw = str(path or "").strip()
    if not raw:
        raise HTTPException(400, "path 不能为空")
    target = Path(os.path.expandvars(os.path.expanduser(raw))).resolve()
    if not target.exists():
        raise HTTPException(404, f"文件不存在：{target}")
    roots = [Path(os.path.expandvars(os.path.expanduser(r))).resolve()
             for r in (config.allowed_roots or [])]
    if roots and not any(target == r or r in target.parents for r in roots):
        raise HTTPException(403, f"不在允许访问的范围内：{target}")
    for forbidden in (config.forbidden_paths or []):
        f = Path(os.path.expandvars(os.path.expanduser(forbidden))).resolve()
        try:
            if target == f or f in target.parents:
                raise HTTPException(403, f"路径位于禁区：{target}")
        except HTTPException:
            raise
        except Exception:
            continue
    return target


def preview(config, path: str = "") -> Response:
    """在界面里直接预览产物。

    HTML 与 SVG 是**模型生成的不可信内容**：如果直接同源返回，
    里面的脚本就能拿到同源下的接口访问权。所以对这两类加 CSP sandbox
    （唯一 origin + 禁脚本），自包含的内联样式和 data: 图片仍可正常显示。
    """
    target = checked_file(config, path)
    if not target.is_file():
        raise HTTPException(400, "只能预览单个文件")
    media = PREVIEW_EXTS.get(target.suffix.lower())
    if not media:
        raise HTTPException(415, f"不支持预览这种格式：{target.suffix}")
    size = target.stat().st_size
    if size > PREVIEW_MAX_BYTES:
        raise HTTPException(413, "文件太大，请用「打开」在系统程序里查看")
    try:
        data = target.read_bytes()
    except OSError as exc:
        raise HTTPException(500, f"读取失败：{exc}") from None
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
        "Content-Disposition": "inline",
    }
    if target.suffix.lower() in (".html", ".htm", ".svg"):
        headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; "
            "img-src data: blob:; font-src data:; sandbox"
        )
    return Response(content=data, media_type=media, headers=headers)


def open_file(config, req: OpenFileRequest) -> dict:
    """用系统默认程序打开一个文件（或在文件管理器里定位它）。

    为什么需要：产物面板以前只能显示一串路径，用户看到「生成好了 D:\\...\\报表.xlsx」
    之后**只能自己去找文件** —— 交付的最后一公里没通。
    这就是 Marvis 说的「产物点开即用」。
    """
    target = checked_file(config, req.path)
    reveal = req.reveal
    try:
        if sys.platform == "win32":
            if reveal:
                subprocess.Popen(["explorer", "/select,", str(target)])
            else:
                os.startfile(str(target))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(target)] if reveal else ["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
    except Exception as exc:
        raise HTTPException(500, f"打开失败：{exc}") from None
    return {"ok": True, "path": str(target), "reveal": reveal}


def list_files(config, prefix: str = "", limit: int = 200) -> dict:
    """列出允许范围内的文件（按修改时间倒序）。

    用途：会话重开后产物面板是空的 —— 因为产物只存在前端内存里。
    这个接口让界面能从磁盘重建列表，用户回看历史会话时
    还能找到「上次那个文件在哪」。
    """
    roots = [Path(os.path.expandvars(os.path.expanduser(r))).resolve()
             for r in (config.allowed_roots or [])]
    if not roots:
        # 规格 §3 缺陷 11：空范围也要带 count，否则前端按 count 读会拿到 undefined。
        return {"files": [], "count": 0}
    base = Path(os.path.expandvars(os.path.expanduser(prefix))).resolve() if prefix else roots[0]
    if roots and not any(base == r or r in base.parents for r in roots):
        raise HTTPException(403, "不在允许访问的范围内")

    items: list[dict[str, Any]] = []
    skip = {"node_modules", ".git", "__pycache__", ".venv", "venv", ".oryx"}
    try:
        for path in base.rglob("*"):
            if len(items) >= max(1, min(limit, 500)):
                break
            try:
                if not path.is_file():
                    continue
                if any(part in skip for part in path.parts):
                    continue
                stat = path.stat()
                items.append({
                    "path": str(path),
                    "name": path.name,
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                })
            except OSError:
                continue
    except OSError as exc:
        raise HTTPException(500, f"读取目录失败：{exc}") from None

    items.sort(key=lambda x: x["mtime"], reverse=True)
    return {"files": items, "count": len(items)}


def workspace_summary(config) -> dict:
    from .. import manage

    return manage.workspace_summary(config)


async def workspace_switch(config, agent, path: str) -> dict:
    from .. import manage

    try:
        result = manage.switch_workspace(config, path)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    # 内核侧的策略引擎与项目文档要跟着新工作区走，否则它还在按旧路径判权限
    try:
        await agent.call_async(
            "runtime.workspace_changed",
            allowed_roots=config.allowed_roots,
            forbidden_paths=config.forbidden_paths,
        )
    except IpcError as exc:
        raise rpc_failure(exc) from None
    return result


async def read_doc(agent) -> dict:
    """项目文档（需求 / 决策 / 变更）。"""
    try:
        stats, content = await asyncio.gather(
            agent.call_async("doc.stats"), agent.call_async("doc.read")
        )
    except IpcError as exc:
        raise rpc_failure(exc) from None
    return {"stats": stats, "content": content}


def register(app, config, agent) -> None:
    """把本域路由挂到 app。依赖用闭包注入，action 本身保持纯函数。"""

    @router.get("/preview")
    async def _preview(path: str = ""):
        return preview(config, path)

    @router.post("/open")
    async def _open(req: OpenFileRequest):
        return json(open_file(config, req))

    @router.get("/list")
    async def _list(prefix: str = "", limit: int = 200):
        return json(list_files(config, prefix, limit))

    @ws_router.get("")
    async def _workspace():
        return json(workspace_summary(config))

    @ws_router.post("")
    async def _workspace_switch(req: WorkspaceRequest):
        return json(await workspace_switch(config, agent, req.path))

    @doc_router.get("")
    async def _doc():
        return json(await read_doc(agent))

    app.include_router(router)
    app.include_router(ws_router)
    app.include_router(doc_router)
