"""联网搜索 —— 可选能力，默认关闭。

为什么需要它
------------
Agent 原来只能查**本地**：文件、索引、记忆。
问「今天天气」「最新政策」「某个库的最新版」这类问题，它只能说自己不知道。

能不能做
--------
能 —— 但需要用户自己配一个搜索服务的 key。
所以这是**可选能力**：

* 默认关闭（`EnableWebSearch`）—— 项目承诺纯本地可用，不能默认就依赖外网
* 打开了但没配 key → **明确报错**，不静默失败

安全
----
搜索结果来自**外部** —— 可能包含伪装成指令的文本（提示注入）。
所以结果一律用 `taint.wrap` 包装，标明「这是资料不是指令」。

支持的 provider
---------------
``serper`` / ``tavily`` / ``brave`` —— 三家的请求格式不同，
但字段名能归一，所以**解析逻辑是共用的**。

多接几家不是为了功能多，是为了**好拿 key** ——
各家的免费额度是独立的，有一家注册不下来还能换一家。

配置（在配置文件里）
--------------------
```json
{
  "search": {
    "provider": "serper",
    "api_key": "…"
  }
}
```

`api_key` 里的明文会被搬进系统凭据库（跟 `providers[*].api_key` 一样）。
"""
from __future__ import annotations

from typing import Any

# provider 名 → 默认端点
DEFAULT_ENDPOINTS = {
    "serper": "https://google.serper.dev/search",
    "tavily": "https://api.tavily.com/search",
    "brave": "https://api.search.brave.com/res/v1/web/search",
}

# 各家把「结果数组」放在哪个字段下 —— 解析时依次找
_RESULT_PATHS = (
    ("organic",),         # serper
    ("results",),         # tavily
    ("web", "results"),   # brave
)


class SearchError(RuntimeError):
    """搜索失败。

    单独一个异常类 —— 调用方要能把它跟别的错误区分开：
    「没配 key」和「网络失败」要给的提示不一样。
    """


class SearchProvider:
    """联网搜索的客户端。

    加一个新 provider：写一个 `_search_<名字>` 方法（发请求、返回原始 JSON），
    解析逻辑是共用的（各家字段名不同，但都能归一成 title/url/snippet）。
    """

    def __init__(self, cfg: dict[str, Any] | None = None) -> None:
        cfg = cfg or {}
        self.name = str(cfg.get("provider") or "serper").strip().lower()
        self.api_key = str(cfg.get("api_key") or "").strip()
        self.base_url = str(
            cfg.get("base_url") or DEFAULT_ENDPOINTS.get(self.name, "")
        ).strip()
        try:
            self.timeout = float(cfg.get("timeout") or 20.0)
        except (TypeError, ValueError):
            self.timeout = 20.0

    @property
    def configured(self) -> bool:
        """配好了吗。"""
        return bool(self.api_key and self.base_url)

    def search(self, query: str, count: int = 5) -> list[dict[str, str]]:
        """搜一次，返回 `[{"title", "url", "snippet"}]`。"""
        if not query.strip():
            raise SearchError("搜索词不能为空")
        # **先查 provider 支不支持** —— 否则用户填了 `provider: bing`
        # 看到的却是「没填 api_key」，提示会误导人去查错的东西。
        if self.name not in DEFAULT_ENDPOINTS:
            raise SearchError(
                f"不支持的搜索服务：{self.name}"
                f"（当前支持 {'、'.join(DEFAULT_ENDPOINTS)}）"
            )
        if not self.configured:
            raise SearchError(
                "没有配置搜索服务 —— 在配置文件的 search 段填 api_key"
                "（provider 默认 serper），或把 EnableWebSearch 关掉"
            )

        request = getattr(self, f"_search_{self.name}", None)
        if request is None:
            raise SearchError(f"搜索服务 {self.name} 还没实现")
        try:
            data = request(query, max(1, min(int(count), 20)))
        except SearchError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SearchError(f"搜索失败：{exc}") from exc
        return self._parse(data if isinstance(data, dict) else {}, count)

    # ---------------- 各家 provider 的请求 ----------------
    # 统一约定：返回**原始 JSON dict**，解析交给共用的 _parse。

    def _http(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        """发一个请求、返回 JSON。

        **网络错误统一报「搜索请求失败」** —— 它跟「搜索服务返回了坏数据」
        是两回事，提示要能让人一眼分清。
        """
        import httpx  # 延迟导入 —— 启动时别为一个可选功能付代价

        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = getattr(client, method)(url, **kwargs)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as exc:
            raise SearchError(f"搜索请求失败：{exc}") from exc

    def _search_serper(self, query: str, count: int) -> dict[str, Any]:
        return self._http(
            "post", self.base_url,
            headers={
                "X-API-KEY": self.api_key,
                "Content-Type": "application/json",
            },
            json={"q": query, "num": count},
        )

    def _search_tavily(self, query: str, count: int) -> dict[str, Any]:
        return self._http(
            "post", self.base_url,
            json={
                "api_key": self.api_key,
                "query": query,
                "max_results": count,
                "search_depth": "basic",
            },
        )

    def _search_brave(self, query: str, count: int) -> dict[str, Any]:
        return self._http(
            "get", self.base_url,
            params={"q": query, "count": count},
            headers={
                "X-Subscription-Token": self.api_key,
                "Accept": "application/json",
            },
        )

    # ---------------- 解析（各家共用） ----------------

    @staticmethod
    def _result_items(data: dict[str, Any]) -> list[Any]:
        """从各家格式里找出结果数组。"""
        for path in _RESULT_PATHS:
            cur: Any = data
            for key in path:
                if not isinstance(cur, dict):
                    cur = None
                    break
                cur = cur.get(key)
            if isinstance(cur, list):
                return cur
        return []

    @staticmethod
    def _parse(data: dict[str, Any], count: int) -> list[dict[str, str]]:
        """把各家的结果归一成 `{title, url, snippet}`。

        字段名三家不同但都能对上：
          · serper → `organic[].{title, link, snippet}`
          · tavily → `results[].{title, url, content}`
          · brave → `web.results[].{title, url, description}`
        """
        out: list[dict[str, str]] = []
        for item in SearchProvider._result_items(data)[: max(1, int(count))]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            url = str(item.get("link") or item.get("url") or "").strip()
            snippet = str(
                item.get("snippet") or item.get("content")
                or item.get("description") or ""
            )
            out.append({
                "title": title,
                "url": url,
                # 摘要里的换行压成空格 —— 一行一条，模型读着顺
                "snippet": " ".join(snippet.split()),
            })
        return out


def format_results(query: str, results: list[dict[str, str]]) -> str:
    """把结果压成给模型看的紧凑文本。"""
    if not results:
        return f"没有搜到「{query}」的结果。"
    lines = [f"「{query}」的搜索结果（{len(results)} 条）：", ""]
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r['title']}")
        if r.get("url"):
            lines.append(f"    {r['url']}")
        if r.get("snippet"):
            lines.append(f"    {r['snippet']}")
    return "\n".join(lines)
