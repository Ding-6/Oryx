"""粘贴导入：把剪贴板里的图片、文本、文件落盘并登记成附件。

为什么需要它：
    浏览器里能拿到剪贴板里的**图片**（``clipboardData.items`` 里的 image/*），
    但拿不到「用户在资源管理器里复制的那个文件」—— 那是系统剪贴板里的
    文件列表，Web 页面没有权限读。所以文件仍然走「选择文件」对话框。
    能自动化的只有：
      图片   截图软件写进剪贴板的位图 → 直接落盘
      文本   纯文字 → 直接进输入框（不落盘）
      文件   拿不到 → 提示用户用「选择文件」

落盘位置：应用目录下的 ``pastes/``，按日期分目录。
    用应用目录而不是工作区 —— 粘贴是随手行为，用户没指定放哪，
    放工作区会污染用户自己的项目目录。

命名：``paste-<时:分:秒>-<序号>.<扩展名>``，
    同名不会撞（时间戳精确到毫秒 + 序号兜底）。
"""

from __future__ import annotations

import base64
import binascii
import re
import time
from pathlib import Path
from typing import Any

from .config import app_dir

# 允许粘贴的图片格式（和图库一致）
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_TEXT_CHARS = 200_000

# data URL 头：data:image/png;base64,xxxx
_DATA_URL = re.compile(r"^data:(image/[a-zA-Z0-9.+-]+);base64,(.*)$", re.S)

# 文件名里不能用的字符（Windows）
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def paste_dir() -> Path:
    return app_dir() / "pastes"


def _safe_name(name: str, fallback: str = "paste") -> str:
    """把原始文件名洗成能落盘的名字。"""
    base = _UNSAFE.sub("_", (name or "").strip()) or fallback
    base = base.lstrip(".") or fallback
    return base[:120]


def _unique(target: Path) -> Path:
    """同名已存在就加序号。"""
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for i in range(1, 100):
        candidate = target.with_name(f"{stem}-{i}{suffix}")
        if not candidate.exists():
            return candidate
    return target.with_name(f"{stem}-{int(time.time())}{suffix}")


def save_image_data_url(data_url: str, name: str = "") -> dict[str, Any]:
    """把浏览器传来的图片 data URL 落盘。

    浏览器剪贴板里的图片是 ``data:image/png;base64,...``，
    有时带 ``image/jpeg``。扩展名按真实 MIME 猜，不信客户端给的。
    """
    match = _DATA_URL.match((data_url or "").strip())
    if not match:
        raise ValueError("只支持图片 data URL")
    mime, payload = match.group(1).lower(), match.group(2)
    ext = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/bmp": ".bmp",
    }.get(mime)
    if ext is None:
        raise ValueError(f"不支持的图片格式：{mime}")

    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"图片数据损坏：{exc}") from exc
    if not raw:
        raise ValueError("图片是空的")
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError(f"图片太大（{len(raw) // 1024 // 1024}MB），上限 20MB")

    day = time.strftime("%Y%m%d")
    folder = paste_dir() / day
    folder.mkdir(parents=True, exist_ok=True)
    stem = _safe_name(Path(name).stem if name else "", "paste")
    target = _unique(folder / f"{stem}{ext}")
    target.write_bytes(raw)
    return {
        "path": str(target),
        "name": target.name,
        "size": len(raw),
        "kind": "image",
        "mime": mime,
    }


def save_text(text: str, name: str = "pasted.txt") -> dict[str, Any]:
    """把粘贴的纯文本落盘（长文本用得上 —— 直接塞进输入框会挤爆界面）。"""
    body = (text or "")
    if len(body) > MAX_TEXT_CHARS:
        raise ValueError(f"文本太长（{len(body)} 字符），上限 {MAX_TEXT_CHARS}")
    day = time.strftime("%Y%m%d")
    folder = paste_dir() / day
    folder.mkdir(parents=True, exist_ok=True)
    target = _unique(folder / _safe_name(name, "pasted.txt"))
    target.write_text(body, "utf-8")
    return {
        "path": str(target),
        "name": target.name,
        "size": len(body.encode("utf-8")),
        "kind": "text",
        "mime": "text/plain",
    }
