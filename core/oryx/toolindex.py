"""工具语义索引：让模型按「意思」找工具，而不是靠记工具名。

对齐 Marvis 的 tool_index / mcp_tool_index：每个工具带一段 embed_text
（人写的语义描述，覆盖「什么时候该用它」），建一次向量后就能被语义召回。

为什么要它：工具已经有 68 个，全塞进上下文既贵又干扰模型。关键词检索只能命中
「截图」这种字面词，但用户说的是「把当前屏幕存下来」—— 语义索引能召回。

三条设计约束：
1. **绝不阻塞工具调用**。向量模型缺失/加载失败时一律降级成关键词检索，
   语义层只是增强项，不是依赖项（与 indexer._vector_search 同一契约）。
2. **索引可持久化**。工具集很少变，向量算一次存起来；工具增删才失效重建。
3. **懒加载**。第一次用到才加载模型，不拖慢内核启动。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Iterable

from .config import app_dir

CACHE_NAME = "tool_index.json"
# embed_text 的最大长度：bge 侧截到 512 token，中文约 340 字，留足余量
MAX_EMBED_CHARS = 300
# 查询词本身就是工具名时的额外加权。归一化后两侧分都落在 0..1，
# 不给这个 bonus 的话精确命中会被语义分挤下去。
EXACT_BONUS = 2.0


def cache_path() -> Path:
    return app_dir() / CACHE_NAME


def build_embed_text(name: str, description: str, risk_label: str = "") -> str:
    """拼出用于向量化的那段文本。

    光有 description 不够 —— 描述是写给模型看的「这个工具做什么」，
    而召回靠的是「什么情况下该用它」。所以把工具名、描述、风险等级、
    以及参数名一起拼进去，让「怎么用」也能被语义命中。
    """
    parts = [name.replace("_", " "), description.strip()]
    if risk_label:
        parts.append(f"风险等级：{risk_label}")
    return "；".join(p for p in parts if p)[:MAX_EMBED_CHARS]


class ToolIndex:
    """工具向量索引。线程安全，支持懒加载与自动重建。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._matrix: Any = None          # (n, dim) float32，已归一化
        self._names: list[str] = []
        self._signature: str = ""         # 工具集指纹，变了要重建
        self._loaded = False
        self._last_error: str = ""

    # ---------------------------------------------------------------- 状态

    @property
    def ready(self) -> bool:
        return self._loaded and self._matrix is not None

    @property
    def error(self) -> str:
        return self._last_error

    def status(self) -> dict[str, Any]:
        from . import embeddings as emb

        return {
            "ready": self.ready,
            "count": len(self._names),
            "dim": emb.dim(),
            "cache": str(cache_path()),
            "cached": cache_path().exists(),
            "error": self._last_error,
            "model_available": emb.available(),
        }

    # ---------------------------------------------------------------- 构建

    def ensure(self, registry: dict[str, Any], embed_texts: dict[str, str] | None = None) -> bool:
        """确保索引与当前工具集一致。返回是否可用（不可用时上层要退回关键词）。"""
        signature = self._fingerprint(registry)
        with self._lock:
            if self.ready and signature == self._signature:
                return True

        cached = self._load_cache(signature)
        if cached is not None:
            with self._lock:
                self._matrix, self._names = cached
                self._signature = signature
                self._loaded = True
            return True

        built = self._build(registry, embed_texts or {})
        with self._lock:
            if built is not None:
                self._matrix, self._names = built
                self._signature = signature
                self._loaded = True
                self._last_error = ""
                self._save_cache(signature, built)
                return True
            # 构建失败 → **必须清掉旧索引**。
            #
            # 以前不清，导致：模型不可用（没装向量模型）时，
            # `_matrix` 还是上一次成功构建的**过期**矩阵，
            # `search()` 会拿它返回**过期结果** ——
            # 工具集早变了，检索还在按老数据排序。
            # 现象是「搜一个完全不相关的词也能召出一堆工具」。
            self._matrix = None
            self._names = []
            self._signature = ""
            self._loaded = False
            return False

    @staticmethod
    def _fingerprint(registry: dict[str, Any]) -> str:
        """工具集指纹。名字、描述或语义提示变了都要重建，否则检索会返回过期向量。"""
        import hashlib

        h = hashlib.sha256()
        for name in sorted(registry):
            spec = registry[name]
            h.update(name.encode("utf-8"))
            h.update(getattr(spec, "description", "").encode("utf-8"))
            h.update(getattr(spec, "embed_text", "").encode("utf-8"))
        return h.hexdigest()[:32]

    @staticmethod
    def _text_for(registry: dict[str, Any], name: str, override: str | None) -> str:
        """取出某个工具用于向量化的文本。

        优先级：显式传入 > ToolSpec.semantic_text()（读 embed_text）>
        从 name + description 自动拼。
        """
        if override:
            return override[:MAX_EMBED_CHARS]
        spec = registry[name]
        semantic_text = getattr(spec, "semantic_text", None)
        if callable(semantic_text):
            return semantic_text()
        return build_embed_text(name, getattr(spec, "description", ""))

    def _build(
        self, registry: dict[str, Any], embed_texts: dict[str, str]
    ) -> tuple[Any, list[str]] | None:
        try:
            import numpy as np

            from . import embeddings as emb

            if not emb.available():
                self._last_error = "向量模型不可用，语义检索已降级为关键词"
                return None
            names = sorted(registry)
            if not names:
                return None
            texts = [self._text_for(registry, name, embed_texts.get(name)) for name in names]
            matrix = emb.encode(texts)
            if matrix is None or len(matrix) != len(names):
                self._last_error = "向量维度与工具数不匹配"
                return None
            return matrix.astype("float32"), names
        except Exception as exc:  # noqa: BLE001 — 语义层不能影响主流程
            self._last_error = f"{type(exc).__name__}: {exc}"
            return None

    # ---------------------------------------------------------------- 缓存

    def _load_cache(self, signature: str) -> tuple[Any, list[str]] | None:
        path = cache_path()
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text("utf-8"))
            if payload.get("signature") != signature:
                return None
            names = payload.get("names") or []
            vecs = payload.get("vectors") or []
            if not names or len(names) != len(vecs):
                return None
            import numpy as np

            matrix = np.asarray(vecs, dtype="float32")
            if matrix.shape[0] != len(names):
                return None
            return matrix, list(names)
        except Exception:
            return None

    def _save_cache(self, signature: str, built: tuple[Any, list[str]]) -> None:
        matrix, names = built
        path = cache_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "signature": signature,
                        "names": names,
                        "vectors": [[round(float(x), 6) for x in row] for row in matrix],
                    },
                    ensure_ascii=False,
                ),
                "utf-8",
            )
        except OSError:
            #缓存写不了不影响功能，只是下次要重算
            pass

    def clear_cache(self) -> bool:
        try:
            cache_path().unlink()
        except OSError:
            return False
        with self._lock:
            self._matrix = None
            self._names = []
            self._signature = ""
            self._loaded = False
        return True

    # ---------------------------------------------------------------- 检索

    def search(self, query: str, limit: int = 8, min_score: float = 0.25) -> list[dict[str, Any]]:
        """按语义相似度召回工具。索引不可用时返回空列表（上层退回关键词）。"""
        query = (query or "").strip()
        if not query:
            return []
        with self._lock:
            matrix = self._matrix
            names = list(self._names)
            loaded = self._loaded
        if matrix is None or not names:
            return []
        # 没 ready 就不给结果 —— 宁可退回关键词路，
        # 也不要拿一份「不知道还对不对」的索引去凑数。
        if not loaded:
            return []

        try:
            import numpy as np

            from . import embeddings as emb

            qvec = emb.encode([query], is_query=True)[0]
            scores = matrix @ qvec
            order = np.argsort(-scores)[: max(1, limit)]
            return [
                {"name": names[i], "score": float(scores[i])}
                for i in order
                if float(scores[i]) >= min_score
            ]
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"{type(exc).__name__}: {exc}"
            return []


def fuse(
    keyword: list[dict[str, Any]],
    semantic: list[dict[str, Any]],
    limit: int,
    semantic_weight: float = 0.6,
) -> list[dict[str, Any]]:
    """关键词与语义结果融合。

    两边都按分数降序归一化后加权 —— 语义权重更高，因为它对「换一种说法」的
    召回明显好于字面匹配；关键词分保留，用来在语义模棱两可时稳住精确命中。

    exact 是「查询词就是工具名」的集合，必须无条件排前面：模型自己报出
    fs_read 时却召回不到它，语义层就成了负优化。
    """
    def _norm(items: list[dict[str, Any]], key: str) -> dict[str, float]:
        if not items:
            return {}
        top = max(float(i.get(key, 0.0)) for i in items) or 1.0
        return {i["name"]: float(i.get(key, 0.0)) / top for i in items}

    exact = {i["name"] for i in keyword if i.get("exact")}
    kw = _norm(keyword, "score")
    sem = _norm(semantic, "score")

    merged: dict[str, float] = {}
    for name, score in kw.items():
        merged[name] = merged.get(name, 0.0) + (1.0 - semantic_weight) * score
    for name, score in sem.items():
        merged[name] = merged.get(name, 0.0) + semantic_weight * score
    # 精确命中加权压过一切
    for name in exact:
        merged[name] = merged.get(name, 0.0) + EXACT_BONUS

    return [
        {"name": name, "score": round(score, 4)}
        for name, score in sorted(merged.items(), key=lambda kv: -kv[1])[: max(1, limit)]
    ]