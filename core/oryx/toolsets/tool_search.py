"""工具语义检索 —— 让模型在几百个工具里找到对的那个。

（从 `loop.py` 原样搬来 —— `self.` 一个字都没改，语义零变动。）
"""

from __future__ import annotations

from typing import Any
from ..policy import PolicyEngine, Risk
from ..tools import ToolSpec, build_registry, tool_result_text
from ..toolindex import ToolIndex, fuse


class ToolSearchToolsMixin:
    def _register_tool_search(self) -> None:
        """工具检索：关键词 + 语义双路召回。

        对齐 Marvis 的 tool_index / WorkBuddy 的 ToolSearch：
        工具有 68 个，全塞进上下文既贵又干扰模型，所以按需召回。
        语义层（toolindex）是增强项，模型不可用时自动退回纯关键词。
        """

        def _search(query: str = "", limit: int = 12) -> dict[str, Any]:
            capped = max(1, min(30, int(limit)))
            needle = (query or "").strip().lower()

            # ---- 关键词路 ----
            #
            # 分词用 registry.tokenize —— 中文按 bigram 切。
            #
            # 原来这里是 `needle.split()`：中文没有空格，
            # 「统计字数」整句成一个 token，去 `in` 描述里的
            # 「统计文本里的字数」当然匹配不上 —— **中文查询基本搜不到东西**。
            # 这跟技能路由那个老毛病是同一类。
            from ..registry import tokenize

            needle_tokens = tokenize(needle)
            keyword: list[dict[str, Any]] = []
            for name, spec in self.registry.items():
                exact = needle == name.lower()
                if not needle:
                    score = 1
                else:
                    score = 0
                    if needle in name.lower():
                        score += 10
                    name_tokens = tokenize(name)
                    desc_tokens = tokenize(spec.description)
                    sem_tokens = tokenize(spec.semantic_text())
                    for token in needle_tokens:
                        if token in name_tokens:
                            score += 6          # 命中工具名 —— 最强信号
                        elif token in desc_tokens:
                            score += 3          # 命中描述 —— 次强
                        elif token in sem_tokens:
                            score += 1          # 只命中语义补充 —— 最弱
                if score > 0:
                    keyword.append({"name": name, "score": float(score), "exact": exact})

            # ---- 语义路 ----
            semantic: list[dict[str, Any]] = []
            if needle:
                try:
                    self.tool_index.ensure(self.registry)
                    # 关键词路一条都没命中时，语义单独扛 —— 这时模型多半在胡说，
                    # 要求语义**更强**才给结果。
                    #
                    # 依据：实测「zzzqqqxyz」这种乱词，所有工具的余弦分都在
                    # 0.35-0.40（平顶，没区分度）；而真查询「看电脑配置」
                    # 的 system_info 是 0.57，明显高出其余。
                    # 阈值 0.45 能把前者全挡掉，又放过后者。
                    min_score = 0.25 if keyword else 0.45
                    semantic = self.tool_index.search(
                        query, limit=capped, min_score=min_score
                    )
                except Exception:
                    semantic = []

            if semantic:
                order = [h["name"] for h in fuse(keyword, semantic, capped)]
            else:
                keyword.sort(key=lambda k: (-k["score"], k["name"]))
                order = [k["name"] for k in keyword[:capped]]

            # 召回分数：让「为什么召回它」可见。
            # 关键词路的分数是整数（命中名字 6 / 描述 3 / 语义 1），
            # 语义路是余弦相似度（0-1）—— 混在一起没法直接比，
            # 所以分开给。
            kw_score = {k["name"]: k["score"] for k in keyword}
            sem_score = {h["name"]: h.get("score", 0.0) for h in semantic}

            results = []
            for name in order:
                spec = self.registry.get(name)
                if spec is None:
                    continue
                item = {
                    "name": name,
                    "description": spec.description,
                    "risk": int(spec.risk),
                    "risk_label": spec.risk.label,
                    "parameters": spec.parameters,
                }
                if name in kw_score:
                    item["keyword_score"] = kw_score[name]
                if name in sem_score:
                    item["semantic_score"] = round(float(sem_score[name]), 4)
                # 延迟工具不在上下文里，模型得知道要用 defer_execute 调它
                if self.defer.should_defer(name):
                    item["deferred"] = True
                    item["invoke_via"] = "defer_execute"
                results.append(item)
            self.bus.publish(
                "tools.searched",
                query=query,
                count=len(results),
                mode="semantic" if semantic else "keyword",
            )
            return {"ok": True, "content": results}

        self.registry["tool_search"] = ToolSpec(
            name="tool_search",
            description=(
                "按需检索可用工具及其参数说明。工具很多，当你不确定有没有某个能力时，"
                "先用它查一下再决定调用什么，不要凭猜测编造工具名。"
                "支持自然语言描述你要做的事，不必记得工具名。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "描述你要做的事，如「把当前屏幕截图存下来」「搜一下代码里的 TODO」",
                    },
                    "limit": {"type": "integer", "description": "最多返回几个"},
                },
                "required": [],
            },
            risk=Risk.L0_READ,
            fn=_search,
        )
