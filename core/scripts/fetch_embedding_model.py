"""下载端侧向量模型 bge-small-zh-v1.5 到本地模型目录。

默认下载 ONNX int8 量化版（约 24MB，速度约为 PyTorch 的 2 倍）。
加 --with-torch 会额外下载 PyTorch 权重（约 96MB），作为无 onnxruntime 时的兜底。

用法：
    python core/scripts/fetch_embedding_model.py
    python core/scripts/fetch_embedding_model.py --mirror https://huggingface.co
    python core/scripts/fetch_embedding_model.py --with-torch
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from oryx.embeddings import DEFAULT_MODEL_DIR  # noqa: E402

TOKENIZER_REPO = "BAAI/bge-small-zh-v1.5"
TOKENIZER_FILES = [
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.txt",
    "special_tokens_map.json",
]
ONNX_REPO = "onnx-community/bge-small-zh-v1.5-ONNX"
ONNX_FILES = [
    "onnx/model_quantized.onnx",
    "onnx/model_quantized.onnx_data",
]
TORCH_FILES = ["model.safetensors"]


def fetch(mirror: str, repo: str, name: str, dest: Path) -> None:
    target = dest / name
    target.parent.mkdir(parents=True, exist_ok=True)
    url = f"{mirror}/{repo}/resolve/main/{name}"
    print(f"→ {name}", end="", flush=True)
    try:
        with urllib.request.urlopen(url, timeout=180) as resp:
            data = resp.read()
        target.write_bytes(data)
        print(f"  {len(data):,} bytes")
    except Exception as exc:
        print(f"  失败：{exc}")
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mirror", default="https://hf-mirror.com", help="模型源")
    parser.add_argument("--dest", default=str(DEFAULT_MODEL_DIR), help="保存目录")
    parser.add_argument("--with-torch", action="store_true", help="额外下载 PyTorch 权重")
    args = parser.parse_args()

    mirror = args.mirror.rstrip("/")
    dest = Path(args.dest)
    print(f"来源：{mirror}\n目录：{dest}\n")

    for name in TOKENIZER_FILES:
        fetch(mirror, TOKENIZER_REPO, name, dest)
    for name in ONNX_FILES:
        fetch(mirror, ONNX_REPO, name, dest)
    if args.with_torch:
        for name in TORCH_FILES:
            fetch(mirror, TOKENIZER_REPO, name, dest)

    required = dest / "onnx" / "model_quantized.onnx"
    if required.exists() and (dest / "config.json").exists():
        print("\n完成。重启 Oryx 后向量检索即可用（后端：onnx-int8）。")
    else:
        print("\n文件不完整，请重试。")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
