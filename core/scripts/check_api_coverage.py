"""接口对齐检查：前端调用的 API 与后端实际注册的路由，双向比对。

为什么需要这个工具
------------------
前端有 24 个模块、后端有 139 条路由 —— 靠人肉对，必然漏。
漏了的表现分两种，都很难查：

* **前端调了、后端没有** → 运行时 404，控制台报错，用户看到的是「功能没反应」
* **后端有、前端没用** → 死代码，或者前端漏接了某个能力

这个脚本把两边都**机械提取**出来再比 —— 不靠眼力，靠正则。

用法
----
    python core/scripts/check_api_coverage.py            # 报告（退出码 1 = 有缺口）
    python core/scripts/check_api_coverage.py --json     # 机器可读
    python core/scripts/check_api_coverage.py --strict   # 后端没被前端用的也算失败

提取规则
--------
**后端**：
  - `APIRouter(prefix="...")` 记下前缀
  - `@router.get("/x")` 等记下方法与路径，拼上所属 router 的前缀
  - `include_router(router)` 不带 prefix 参数时，前缀就是 router 自己的

**前端**：
  - `api('/api/xxx')` / `api("/api/xxx")` / ``api(`/api/xxx`)`` 都算
  - 模板字符串里的 `${...}` 归一化成 `{...}`，与后端的 `{param}` 对齐
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
WEB = CORE / "web"
ORYX = CORE / "oryx"
# 新前端（apps/web）。**两套前端都要扫** ——
# 新前端正在逐域替换老前端，老的那套还在跑；
# 只扫一套的话，另一套的调用就没人守了。
NEW_WEB = CORE.parent / "apps" / "web" / "src"

# ── 后端 ────────────────────────────────────────────────────────────────

PREFIX_RE = re.compile(r'APIRouter\(\s*prefix\s*=\s*["\']([^"\']*)["\']')
# 装饰器里直接带着 router 的变量名 —— 靠它把前缀对上
ROUTE_RE = re.compile(
    r'@(?P<var>\w+)\s*\.\s*'
    r'(?P<method>get|post|put|delete|patch|options|head)\s*\(\s*["\'](?P<path>[^"\']*)["\']'
)
# `include_router(router, prefix="/x")` —— 会覆盖 router 自己的前缀
INCLUDE_RE = re.compile(
    r'include_router\(\s*(\w+)\s*(?:,\s*prefix\s*=\s*["\']([^"\']*)["\'])?'
)


def _routers_in(text: str) -> dict[str, str]:
    """{router 变量名: prefix}"""
    out: dict[str, str] = {}
    for m in re.finditer(r'(\w+)\s*=\s*APIRouter\(\s*prefix\s*=\s*["\']([^"\']*)["\']', text):
        out[m.group(1)] = m.group(2)
    # 没有 prefix 的 router（直接挂在 app 上的）
    for m in re.finditer(r'(\w+)\s*=\s*APIRouter\(\s*\)', text):
        out.setdefault(m.group(1), "")
    return out


def _join(prefix: str, path: str) -> str:
    """拼前缀与路径 —— 两者都可能带/不带斜杠。"""
    prefix = prefix.rstrip("/")
    path = path.strip()
    if not path:
        return prefix or "/"
    if not path.startswith("/"):
        path = "/" + path
    full = prefix + path
    return full if full.startswith("/") else "/" + full


def extract_backend() -> dict[str, dict[str, str]]:
    """{METHOD PATH: {"file": ..., "line": ...}}

    **优先用运行时真值**：把 app 装配起来，遍历 `app.routes`，
    把 FastAPI 懒加载的 `_IncludedRouter` 摊平，取出真实的 path/method。

    为什么不用正则扫源码 —— 试过，坑很深：
      · 路由装饰器写在 `register()` **函数体内**（不是模块级）——
        正则扫源码能看见，但拿不准它属于哪个 router 前缀
      · 这个 FastAPI 版本的 `include_router` 产生的是 `_IncludedRouter`
        懒加载对象，`app.routes` 里**看不到**真实 path
      · 有 `include_router(router, prefix=...)` 覆盖前缀的情况
    拿运行时真值最省事，也不会跟实现脱节。

    运行时取不到时（比如依赖缺失）退回正则扫描 —— 至少还能报个大概。
    """
    rows = _routes_from_runtime()
    if rows:
        return {f"{m} {p}": {"file": "runtime", "line": "-"} for m, p in rows}
    return _routes_from_source()


def _routes_from_runtime() -> list[tuple[str, str]]:
    """从装配好的 app 里取真实路由。取不到就返回空列表。"""
    import importlib

    try:
        os.environ.setdefault("ORYX_HOME", r"D:\OryxData\data")
        sys.path.insert(0, str(CORE))
        server = importlib.import_module("oryx.server")
        domains = importlib.import_module("oryx.domains")
        config_mod = importlib.import_module("oryx.config")
        app = server.app
        cfg = config_mod.load_config()

        class _Stub:
            def __getattr__(self, name):
                raise RuntimeError("stub")

        class _StubAgent:
            async def call_async(self, *a, **k):
                raise RuntimeError("stub")

        class _StubBus:
            def publish(self, *a, **k):
                pass

        domains.register_all(
            app,
            store=_Stub(),
            config=cfg,
            agent=_StubAgent(),
            bus=_StubBus(),
            api_token="",
        )
    except Exception:
        return []

    out: set[tuple[str, str]] = set()
    seen: set[int] = set()

    def walk(routes) -> None:
        for r in routes:
            if id(r) in seen:
                continue
            seen.add(id(r))
            # FastAPI 新版：懒加载的 include_router 包装
            orig = getattr(r, "original_router", None)
            if orig is not None:
                walk(getattr(orig, "routes", []) or [])
                continue
            path = getattr(r, "path", None)
            methods = getattr(r, "methods", None)
            if not path or not methods:
                continue
            for m in methods:
                if m in ("HEAD", "OPTIONS"):
                    continue
                # 后端的 {task_id} 与前端的 ${taskId} 归一化成同一个形态
                out.add((m, re.sub(r"\{[^}]*\}", "{}", path)))

    walk(app.routes)
    return sorted(out)


def _routes_from_source() -> dict[str, dict[str, str]]:
    """正则扫源码 —— 运行时取不到时的退路。"""
    routes: dict[str, dict[str, str]] = {}
    files = sorted(list(ORYX.glob("*.py")) + list((ORYX / "domains").glob("*.py")))
    for f in files:
        text = f.read_text(encoding="utf-8")
        routers = _routers_in(text)
        overrides: dict[str, str] = {}
        for m in INCLUDE_RE.finditer(text):
            var, pfx = m.group(1), m.group(2)
            if pfx:
                overrides[var] = pfx
        for m in ROUTE_RE.finditer(text):
            var = m.group("var")
            method, path = m.group("method").upper(), m.group("path")
            prefix = overrides.get(var, routers.get(var, "")) if var in routers else ""
            full = _join(prefix, path)
            line = text[: m.start()].count("\n") + 1
            routes[f"{method} {full}"] = {"file": f.name, "line": str(line)}
    return routes


# ── 前端 ────────────────────────────────────────────────────────────────

API_CALL_RE = re.compile(r"""api\s*\(\s*[`'"](/[^`'"]*)[`'"]""")

# 新前端的客户端调用（apps/web/src/lib/api.ts）：
#     api.get<Session[]>('/api/sessions')
#     api.post('/api/chat', body) / api.put(...) / api.del(...)
#
# 与老前端的 `api('/api/x')` 区别：
#   · 前面多一个方法名（方法**写明**了，不用从上下文猜）
#   · 可能带泛型参数：api.get<Session[]>('/api/sessions')
#
# 泛型里可能有嵌套尖括号（`api.get<Array<{a: string}>>`），
# 所以泛型部分写成「一层或两层尖括号」，里面不含 `>`。
CLIENT_CALL_RE = re.compile(
    r"api\s*\.\s*(get|post|put|del|delete|patch)\s*"
    r"(?:<[^<>]*(?:<[^<>]*>[^<>]*)*>)?\s*\(\s*[`'\"](/[^`'\"]*)[`'\"]"
)
FETCH_RE = re.compile(r"""fetch\s*\(\s*[`'"](/[^`'"]*)[`'"]""")

# 新前端客户端的方法名 → HTTP 方法。
# 注意 `del` 是新前端的写法（对齐 api.del），要映射成 **DELETE**。
_CLIENT_METHODS = {
    "get": "GET",
    "post": "POST",
    "put": "PUT",
    "del": "DELETE",
    "delete": "DELETE",
    "patch": "PATCH",
}
# SSE —— EventSource 不走 api()/fetch()
EVENTSOURCE_RE = re.compile(r"""new\s+EventSource\s*\(\s*[`'"](/[^`'"]*)[`'"]""")
# 变量持有路径：const url = '/api/xxx'  /  const url = cond ? '/a' : '/b'
#
# 三元的两个分支常常**跨行**写：
#   const url = isUi
#     ? `/api/ui-extensions/${id}`
#     : `/api/extensions/${id}`;
# 所以 `?` 之前那段必须允许换行（用 {0,200}? 限长，别放开跑飞）。
VAR_PATH_RE = re.compile(
    r"""(?:const|let|var)\s+(\w+)\s*=\s*"""
    r"""(?:"""
    r"""[`'"](/[^`'"]*)[`'"]"""
    r"""|"""
    r"""[^;=]{0,200}?\?\s*[`'"](/[^`'"]*)[`'"]\s*:\s*[`'"](/[^`'"]*)[`'"]"""
    r""")"""
)
# 任何位置出现的 /api/ 字面量（含 CSS background:url()、img src）
ANY_API_LITERAL_RE = re.compile(r"""[`'"](/api/[^`'"]*)[`'"]""")
TEMPLATE_PART_RE = re.compile(r"\$\{[^}]*\}")


def _normalize(path: str) -> str:
    """`/api/x/${id}/y?limit=3` → `/api/x/{}/y`

    模板字符串里的 `${...}` 与后端的 `{param}` 对齐；
    查询串去掉（后端路由不区分 query）。

    **查询串变量要丢掉，路径参数要留着** ——
    这两种 `${...}` 长得一样，靠**前面有没有 `/`** 区分：

        `/api/sessions/${id}`       → `/api/sessions/{}`   （路径参数）
        `/api/overview/x${q}`       → `/api/overview/x`    （查询串变量）

    以前一律替换成 `{}`，第二种会变成 `/api/overview/x/{}` ——
    跟后端的 `/api/overview/x` 对不上，于是这个接口被误报成
    「后端有、前端没用」。
    """
    path = path.split("?", 1)[0].split("#", 1)[0]
    # 前面不是 `/` 的模板部分 = 查询串变量 → 整段丢掉
    path = re.sub(r"(?<!/)\$\{[^}]*\}", "", path)
    # 剩下的才是路径参数
    path = TEMPLATE_PART_RE.sub("{}", path)
    path = re.sub(r"\{[^}]*\}", "{}", path)
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return path


def _method_of(text: str, after: int) -> str:
    """从调用点往后看，判断 HTTP 方法。

    覆盖几种写法：
      api('/x', {method: 'POST'})
      api('/x', {method: "DELETE"})
      api('/x','POST')
      api('/x', { method : `DELETE` })
    看不出来就当 GET（后端多数查询接口是 GET，误判方向是「前端以为有」）。
    """
    tail = text[after : after + 160]
    m = re.search(r"method\s*:\s*[`'\"](\w+)[`'\"]", tail)
    if m:
        return m.group(1).upper()
    m = re.match(r"\s*,\s*[`'\"](\w+)[`'\"]", tail)
    if m:
        return m.group(1).upper()
    return "GET"


def _api_references(text: str) -> list[tuple[str, str, int]]:
    """前端对 API 的引用：[(method, 原始路径, 起始偏移)]"""
    refs: list[tuple[str, str, int]] = []

    # 1) 直接字面量调用
    for rx in (API_CALL_RE, FETCH_RE):
        for m in rx.finditer(text):
            refs.append((_method_of(text, m.end()), m.group(1), m.start()))

    # 1b) 新前端的客户端调用：api.get<T>('/api/x') —— 方法写明了，不用猜
    for m in CLIENT_CALL_RE.finditer(text):
        refs.append((_CLIENT_METHODS[m.group(1).lower()], m.group(2), m.start()))
    # 2) SSE
    for m in EVENTSOURCE_RE.finditer(text):
        refs.append(("GET", m.group(1), m.start()))

    # 3) 变量持有路径：const url = '/api/xxx'  →  api(url)
    #
    # **必须按作用域配对** —— 同一个变量名在一个文件里会被复用：
    #   const url = '/api/fs/list?...'   → api(url)          ← GET
    #   ...
    #   const url = '/api/files/preview' → api(url, {method:'DELETE'})
    # 不做「就近赋值」配对的话，会把 A 的路径和 B 的方法凑成一对，
    # 凭空造出「GET /api/files/preview + DELETE」这种不存在的接口。
    assigns: dict[str, list[tuple[int, list[str]]]] = {}
    for m in VAR_PATH_RE.finditer(text):
        var = m.group(1)
        paths = [g for g in m.groups()[1:] if g]
        assigns.setdefault(var, []).append((m.start(), paths))

    for var, items in assigns.items():
        for use in re.finditer(rf"\bapi\s*\(\s*{re.escape(var)}\b", text):
            # 找**最近的前一条**赋值
            best: tuple[int, list[str]] | None = None
            for pos, paths in items:
                if pos < use.start() and (best is None or pos > best[0]):
                    best = (pos, paths)
            if not best:
                continue
            method = _method_of(text, use.end())
            for p in best[1]:
                refs.append((method, p, use.start()))

    return refs


def extract_frontend() -> dict[str, list[str]]:
    """{METHOD PATH: ["file:line", ...]}"""
    calls: dict[str, list[str]] = {}
    for f in _frontend_files():
        text = f.read_text(encoding="utf-8")
        for method, raw, off in _api_references(text):
            if not raw.startswith("/api"):
                continue
            norm = _normalize(raw)
            line = text[:off].count("\n") + 1
            calls.setdefault(f"{method} {norm}", []).append(f"{f.name}:{line}")
    return calls


def frontend_used_paths() -> set[str]:
    """前端**任何地方**引用过的 /api 路径（不含方法）。

    比 `extract_frontend` 宽松 —— 还会算上
    `background:url("/api/appearance/background.png")` 这类
    不走 api() 但确实在用的引用。
    用来判断「后端这条路由前端到底用没用」。
    """
    used: set[str] = set()
    for f in _frontend_files():
        text = f.read_text(encoding="utf-8")
        for m in ANY_API_LITERAL_RE.finditer(text):
            used.add(_normalize(m.group(1)))
        for _method, raw, _off in _api_references(text):
            if raw.startswith("/api"):
                used.add(_normalize(raw))
    return used


def _frontend_files() -> list[Path]:
    """两套前端的源文件。

    老前端：core/web/modules/*.js（+ 用户目录里的界面扩展）
    新前端：apps/web/src/**/*.{ts,tsx}

    **两套都要扫** —— 新前端正在逐域替换老前端，老的那套还在跑。
    只扫一套的话，另一套的调用就没人守了：
    新前端调了后端没有的接口，跑起来才发现 404。
    """
    files = list((WEB / "modules").glob("*.js"))
    files += list((WEB / "ui-extensions").glob("**/*.js"))
    if NEW_WEB.is_dir():
        for pattern in ("**/*.ts", "**/*.tsx"):
            files += list(NEW_WEB.glob(pattern))
    return sorted(files)


# ── 比对 ────────────────────────────────────────────────────────────────


def build_method_path_sets(
    backend: dict[str, dict[str, str]], frontend: dict[str, list[str]]
) -> tuple[set[str], set[str]]:
    """按 (method, path) 建两边的集合 —— 路径形态可能不同
    （前端写死 GET、后端只有 POST），所以两边都按 method+path 全量比对。
    """
    b = set(backend.keys())
    f = set(frontend.keys())
    return b, f


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true", help="后端没被前端用的也算失败")
    args = ap.parse_args()

    backend = extract_backend()
    frontend = extract_frontend()
    bset, fset = build_method_path_sets(backend, frontend)

    # 「后端有、前端没用」要用**宽松**的路径集合来判 ——
    # 有些引用不走 api()（CSS background:url()、img src、SSE），
    # 只看 api() 调用会误报。
    used_paths = frontend_used_paths()

    missing_in_backend = sorted(fset - bset)   # 前端调了，后端没有
    unused_in_frontend = sorted(
        r for r in (bset - fset)
        if r.split(" ", 1)[1] not in used_paths
    )

    if args.json:
        print(json.dumps({
            "backend_total": len(bset),
            "frontend_total": len(fset),
            "missing_in_backend": [
                {"route": r, "calls": frontend[r]} for r in missing_in_backend
            ],
            "unused_in_frontend": [
                {"route": r, "at": f"{backend[r]['file']}:{backend[r]['line']}"}
                for r in unused_in_frontend
            ],
        }, ensure_ascii=False, indent=2))
        return 1 if (missing_in_backend or (args.strict and unused_in_frontend)) else 0

    print(f"后端路由：{len(bset)} 条")
    print(f"前端调用：{len(fset)} 条")
    print()

    if missing_in_backend:
        print(f"❌ 前端调了但后端没有（{len(missing_in_backend)} 条）—— 运行时 404")
        for r in missing_in_backend:
            print(f"   {r}")
            for c in frontend[r]:
                print(f"       ← {c}")
        print()

    if unused_in_frontend:
        tag = "⚠️" if args.strict else "ℹ️"
        print(f"{tag} 后端有但前端没用（{len(unused_in_frontend)} 条）")
        for r in unused_in_frontend:
            print(f"   {r}   ({backend[r]['file']}:{backend[r]['line']})")
        print()

    if not missing_in_backend:
        print("✅ 前端调用的接口，后端全部有。")
    return 1 if (missing_in_backend or (args.strict and unused_in_frontend)) else 0


if __name__ == "__main__":
    sys.exit(main())
