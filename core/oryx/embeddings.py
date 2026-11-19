"""端侧向量模型：bge-small-zh-v1.5（512 维，CLS pooling）。

推理后端按优先级自动选择：
    1. ONNX int8 量化（onnx/model_quantized.onnx）—— 最快，实测约为 torch 的 2.1 倍
    2. ONNX fp32（onnx/model.onnx）
    3. PyTorch（model.safetensors）

模型放在本地目录，不依赖任何在线服务；缺失时所有接口返回不可用，
上层自动退回纯关键词检索。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from .config import app_dir

# 模型目录。
#
# 测试环境下复用**真实**模型目录（只读资源）：模型有 205MB，
# 拷一份进临时目录既慢又浪费；而模型是只读的，测试用它没问题。
#
# 用 env 变量 ORYX_MODEL_DIR 显式指 —— 测试的 ORYX_HOME 是临时目录，
# 从那儿推出来的模型路径是不存在的。
_MODEL_DIR_ENV = "ORYX_MODEL_DIR"
DEFAULT_MODEL_DIR = Path(
    os.environ.get(_MODEL_DIR_ENV) or (app_dir() / "models" / "bge-small-zh-v1.5")
)
# bge 系列做检索时建议给「查询」加指令前缀，文档侧不加
QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："
BATCH_SIZE = 32
MAX_LENGTH = 512
DIM = 512

_lock = threading.Lock()
_backend: str | None = None
_session: Any = None
_model: Any = None
_tokenizer: Any = None
_load_error: str | None = None
_loaded_dir: Path | None = None


def runtime_available(model_dir: Path | None = None) -> bool:
    """推理**依赖**装没装。

    模型文件在，但 `transformers` / `onnxruntime` 没装 —— 一调用就炸。
    `available()` 只看模型文件，会误报「可用」，
    于是本该跳过的测试变成**失败**（本地没装 torch 时的常态）。

    判断依据用 `importlib.util.find_spec` —— 只查「装没装」，
    不真的 import（import torch 要好几秒）。
    """
    import importlib.util

    target = model_dir or DEFAULT_MODEL_DIR

    def _has(module: str) -> bool:
        try:
            return importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            return False

    # 三个都必需：
    #   numpy      —— 结果矩阵
    #   transformers —— 分词器（**所有后端都要**，ONNX 也一样）
    #   后端本身   —— onnxruntime 或 torch
    if not all(_has(m) for m in ("numpy", "transformers")):
        return False
    _backend_name, weights = _resolve_backend(target)
    return _has("onnxruntime") if weights is not None else _has("torch")


def available(model_dir: Path | None = None) -> bool:
    """向量检索能不能用 —— **模型文件在** 且 **推理依赖在**。

    两个条件都要满足：
      · 模型文件在不在（`config.json` + 权重）
      · 推理依赖装没装（numpy / transformers / 后端）

    以前只看模型文件，于是「模型在、依赖没装」会被判成可用，
    一调用就 RuntimeError —— 上层说好的「自动退回纯关键词检索」
    根本走不到（`_load` 直接抛了）。
    """
    target = model_dir or DEFAULT_MODEL_DIR
    has_config = (target / "config.json").exists()
    has_weights = (
        (target / "onnx" / "model_quantized.onnx").exists()
        or (target / "onnx" / "model.onnx").exists()
        or (target / "model.safetensors").exists()
        or (target / "pytorch_model.bin").exists()
    )
    return has_config and has_weights and runtime_available(target)


def _resolve_backend(target: Path) -> tuple[str, Path | None]:
    quantized = target / "onnx" / "model_quantized.onnx"
    if quantized.exists():
        return "onnx-int8", quantized
    fp32 = target / "onnx" / "model.onnx"
    if fp32.exists():
        return "onnx-fp32", fp32
    return "torch", None


def _load(target: Path) -> None:
    global _backend, _session, _model, _tokenizer, _load_error, _loaded_dir
    if _backend is not None and _loaded_dir == target:
        return
    with _lock:
        if _backend is not None and _loaded_dir == target:
            return
        try:
            from transformers import AutoTokenizer

            _tokenizer = AutoTokenizer.from_pretrained(str(target))
            backend, weights = _resolve_backend(target)

            if weights is not None:
                import onnxruntime as ort

                options = ort.SessionOptions()
                options.intra_op_num_threads = max(1, min(4, _cpu_count()))
                options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                _session = ort.InferenceSession(
                    str(weights), options, providers=["CPUExecutionProvider"]
                )
                _model = None
            else:
                import torch
                from transformers import AutoModel

                torch.set_num_threads(max(1, min(4, _cpu_count())))
                _model = AutoModel.from_pretrained(str(target))
                _model.eval()
                _session = None

            _backend = backend
            _loaded_dir = target
            _load_error = None
        except Exception as exc:
            _load_error = f"{type(exc).__name__}: {exc}"
            raise RuntimeError(f"向量模型加载失败：{_load_error}") from exc


def _cpu_count() -> int:
    import os

    return os.cpu_count() or 4


def encode(
    texts: list[str],
    *,
    is_query: bool = False,
    batch_size: int = BATCH_SIZE,
    model_dir: Path | None = None,
) -> Any:
    """返回 (n, 512) 的归一化 float32 矩阵。"""
    import numpy as np

    target = model_dir or DEFAULT_MODEL_DIR
    if not available(target):
        # 分两种情况说清楚 —— 「模型没下载」和「依赖没装」修法完全不同
        if not runtime_available(target):
            raise RuntimeError(
                "向量推理依赖没装（需要 numpy / transformers / onnxruntime 或 torch）。"
                "运行 pip install -r core/requirements.txt，"
                "或用 core/scripts/install_extras.py 单独装。"
            )
        raise RuntimeError(
            f"向量模型不存在：{target}。运行 core/scripts/fetch_embedding_model.py 下载。"
        )
    _load(target)

    prepared = [f"{QUERY_INSTRUCTION}{t}" if is_query else t for t in texts]
    chunks: list[Any] = []
    for start in range(0, len(prepared), batch_size):
        batch = prepared[start : start + batch_size]
        encoded = _tokenizer(
            batch,
            padding=True,
            truncation=True,
            max_length=MAX_LENGTH,
            return_tensors="np",
        )
        input_ids = encoded["input_ids"].astype("int64")
        attention_mask = encoded["attention_mask"].astype("int64")

        if _session is not None:
            feed: dict[str, Any] = {}
            for spec in _session.get_inputs():
                if spec.name == "input_ids":
                    feed[spec.name] = input_ids
                elif spec.name == "attention_mask":
                    feed[spec.name] = attention_mask
                elif spec.name == "token_type_ids":
                    feed[spec.name] = np.zeros_like(input_ids)
            hidden = _session.run(None, feed)[0]
            cls = hidden[:, 0, :]
        else:
            import torch

            with torch.no_grad():
                outputs = _model(
                    input_ids=torch.from_numpy(input_ids),
                    attention_mask=torch.from_numpy(attention_mask),
                )
            cls = outputs.last_hidden_state[:, 0].cpu().numpy()

        norms = np.linalg.norm(cls, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        chunks.append((cls / norms).astype("float32"))

    if not chunks:
        return np.zeros((0, DIM), dtype="float32")
    return np.vstack(chunks)


def dim() -> int:
    return DIM


def status() -> dict[str, Any]:
    target = DEFAULT_MODEL_DIR
    backend, weights = _resolve_backend(target)
    return {
        "available": available(target),
        "model_dir": str(target),
        "backend": _backend or backend,
        "weights": str(weights) if weights else "pytorch",
        "loaded": _backend is not None,
        "error": _load_error,
    }
