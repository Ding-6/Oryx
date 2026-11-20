"""图片 OCR：端侧中文识别，用于截图、发票、白板照片。

引擎按需加载（首次约 1-2 秒），不拖慢启动。若未安装 rapidocr-onnxruntime，
所有接口返回明确的不可用状态，不会抛异常打断上层流程。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

MAX_SIDE = 1600
MAX_IMAGE_BYTES = 20 * 1024 * 1024

_engine: Any = None
_engine_error: str | None = None
_lock = threading.Lock()


def _pip_cmd(package: str) -> str:
    """装包命令 —— 用**当前解释器**的 pip。

    裸 ``pip`` 可能指向别的 Python（这台机器上 PATH 的 pip 是 WorkBuddy 的），
    照着装，Oryx 这边照样缺。
    """
    import sys

    return f'"{sys.executable}" -m pip install {package}'


def available() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401

        return True
    except Exception:
        return False


def _get_engine():
    global _engine, _engine_error
    if _engine is not None:
        return _engine
    with _lock:
        if _engine is not None:
            return _engine
        try:
            from rapidocr_onnxruntime import RapidOCR

            _engine = RapidOCR()
        except Exception as exc:
            _engine_error = f"{type(exc).__name__}: {exc}"
            raise RuntimeError(f"OCR 引擎初始化失败：{_engine_error}") from exc
    return _engine


def _prepare(path: Path) -> Any:
    """超大图片先缩放，避免耗时爆炸。"""
    from PIL import Image

    image = Image.open(path)
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    width, height = image.size
    longest = max(width, height)
    if longest > MAX_SIDE:
        scale = MAX_SIDE / longest
        image = image.resize((int(width * scale), int(height * scale)))
    return image


def ocr_image(path: str | Path) -> dict[str, Any]:
    """返回 {"ok", "text", "lines", "elapsed_ms", "error"}。"""
    target = Path(path)
    if not target.exists():
        return {"ok": False, "text": "", "lines": [], "error": f"文件不存在：{target}"}
    try:
        if target.stat().st_size > MAX_IMAGE_BYTES:
            return {"ok": False, "text": "", "lines": [], "error": "图片过大（>20MB）"}
    except OSError as exc:
        return {"ok": False, "text": "", "lines": [], "error": str(exc)}

    if not available():
        return {
            "ok": False,
            "text": "",
            "lines": [],
            "error": "未安装 OCR 引擎。安装命令：" + _pip_cmd("rapidocr-onnxruntime"),
        }

    started = time.time()
    try:
        import numpy as np

        engine = _get_engine()
        image = _prepare(target)
        result, _elapse = engine(np.array(image))
    except Exception as exc:
        return {"ok": False, "text": "", "lines": [], "error": f"识别失败：{exc}"}

    lines: list[dict[str, Any]] = []
    for item in result or []:
        try:
            _box, text, score = item[0], item[1], item[2]
        except Exception:
            continue
        if text:
            lines.append({"text": str(text), "score": round(float(score), 3)})

    return {
        "ok": True,
        "text": "\n".join(line["text"] for line in lines),
        "lines": lines,
        "elapsed_ms": int((time.time() - started) * 1000),
    }
