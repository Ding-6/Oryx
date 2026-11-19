"""本地模型探测：把机器上已装的本地模型列出来，让用户能直接切。

用户电脑上装了 Ollama（本地跑 Qwen 之类），但 Oryx 的 provider 列表里
只有一条写死的 ``ollama → qwen3:8b``。换个模型就得去设置里手填一遍
base_url / model / api_key —— 其实这些值 Ollama 自己就知道。

所以这里去问 Ollama 要它装了哪些模型，界面据此列出可选项。
用户选了就**自动注册**成一条 provider，不用手填。

只读不写：探测本身不碰配置，注册由调用方决定。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

# Ollama 默认端口。探测不到就当没装，不报错。
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"

# 探测超时。Ollama 没起时会立刻连接失败；起了但慢的时候给 3 秒。
_TIMEOUT = 3.0


def _base_url(base_url: str | None) -> str:
    raw = (base_url or DEFAULT_OLLAMA_URL).strip().rstrip("/")
    # 传进来的可能是 OpenAI 兼容端点（.../v1），要退回服务根地址
    if raw.endswith("/v1"):
        raw = raw[:-3].rstrip("/")
    return raw


def list_local_models(base_url: str | None = None) -> list[dict[str, Any]]:
    """列出本地模型服务上已装的模型。

    返回 ``[{"name": "qwen3:8b", "size": 4983, "family": "qwen3", "raw": ...}]``。
    服务没起来时返回空列表 —— 这是正常状态，不是错误。
    """
    root = _base_url(base_url)
    try:
        with urllib.request.urlopen(f"{root}/api/tags", timeout=_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return []

    out: list[dict[str, Any]] = []
    for item in payload.get("models") or []:
        name = str(item.get("name") or item.get("model") or "").strip()
        if not name:
            continue
        details = item.get("details") or {}
        out.append({
            "name": name,
            "size": int(item.get("size") or 0),
            "family": str(details.get("family") or ""),
            "parameter_size": str(details.get("parameter_size") or ""),
            "quantization": str(details.get("quantization_level") or ""),
        })
    # 已装模型按名字排，稳定的顺序比"按体积排"更好用
    out.sort(key=lambda m: m["name"])
    return out


def provider_spec_for(
    model_name: str,
    base_url: str | None = None,
    api_key: str = "ollama",
) -> tuple[dict[str, Any], str]:
    """给一个本地模型名，造出对应的 provider 配置和 provider 名。

    返回 ``(spec, name)``。

    名字里带冒号的（``qwen3:8b``）不能直接当 provider 名 ——
    冒号在配置键、URL 路径里都是麻烦。换成 ``-``。
    """
    root = _base_url(base_url)
    safe = (model_name or "").strip().replace(":", "-").replace("/", "-")
    spec = {
        "type": "openai",
        "base_url": f"{root}/v1",
        "api_key": api_key,
        "model": model_name,
    }
    return spec, safe


def is_local_provider(spec: dict[str, Any]) -> bool:
    """这个 provider 指的是本机跑的模型吗？

    判据是 base_url 指向回环地址 —— 不管它叫 ollama 还是 lmstudio，
    只要地址在 127.0.0.1 / localhost 上，它就是本地模型。
    云端服务（api.anthropic.com、api.deepseek.com…）一律不是。
    """
    from .config import _is_local_spec

    return _is_local_spec(spec)
