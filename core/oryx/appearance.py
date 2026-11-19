"""外观：主题与自定义背景。

自定义背景存成文件放在应用目录，页面用 blob URL 引用——
这样不用把令牌拼进 CSS 的 url() 里，图片本身也不会被外部网页读到。
"""

from __future__ import annotations

import base64
import binascii
import re
from pathlib import Path
from typing import Any

from .config import app_dir

ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
MAX_BYTES = 12 * 1024 * 1024
BACKGROUND_FILE = "background"

_DATA_URL = re.compile(r"^data:image/(png|jpe?g|webp|gif|bmp);base64,(.+)$", re.S)


def background_path() -> Path:
    return app_dir() / BACKGROUND_FILE


def current() -> dict[str, Any]:
    path, _mime = read_background()
    return {
        "theme": "light",
        "background": str(path) if path else "",
        "background_exists": path is not None,
    }


def save_background(data_url: str, config: Any = None) -> dict[str, Any]:
    """接收浏览器传来的 data URL，落盘成图片文件。"""
    match = _DATA_URL.match((data_url or "").strip())
    if not match:
        raise ValueError("只支持 png/jpeg/webp/gif/bmp 格式的 data URL")
    extension, payload = match.group(1), match.group(2)
    extension = ".jpg" if extension == "jpeg" else f".{extension}"
    if extension not in ALLOWED_EXT:
        raise ValueError(f"不支持的图片格式：{extension}")

    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"图片数据损坏：{exc}") from exc
    if len(raw) > MAX_BYTES:
        raise ValueError(f"图片太大（{len(raw) // 1024 // 1024}MB），上限 12MB")

    path = background_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # 清掉旧扩展名的文件
    for old in path.parent.glob(f"{BACKGROUND_FILE}.*"):
        try:
            old.unlink()
        except OSError:
            pass
    target = path.with_suffix(extension)
    target.write_bytes(raw)

    if config is not None:
        config.data["background_image"] = str(target)
        config.save()
    return {"path": str(target), "bytes": len(raw)}


def clear_background(config: Any = None) -> bool:
    removed = False
    for old in app_dir().glob(f"{BACKGROUND_FILE}.*"):
        try:
            old.unlink()
            removed = True
        except OSError:
            pass
    if config is not None:
        config.data["background_image"] = ""
        config.save()
    return removed


def read_background() -> tuple[Path | None, str]:
    """返回 (文件路径, MIME)。没有背景时返回 (None, "")。"""
    for old in app_dir().glob(f"{BACKGROUND_FILE}.*"):
        if old.suffix.lower() in ALLOWED_EXT and old.exists():
            mime = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".webp": "image/webp",
                ".gif": "image/gif",
                ".bmp": "image/bmp",
            }.get(old.suffix.lower(), "application/octet-stream")
            return old, mime
    return None, ""
