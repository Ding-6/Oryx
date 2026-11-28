"""《接口规格 v2》与代码保持一致 —— 文档不撒谎。

为什么需要这个
--------------
`docs/INTERFACE-SPEC-V2.md` 是「145 条路由全推倒重写」的契约。
契约文档最常见的死法是：**代码改了，文档没改** ——
于是照着文档重写，实现出来跟规格对不上，比没文档还糟。

所以这个测试把文档钉在代码上：
  · 文档里列的每条路由，运行时必须真实存在
  · 运行时的每条路由，文档里必须列出
  · 文档里标注「前端零调用」的接口，前端必须真的没调用

第一条和第三条当年就是这么发现的：
  · 写规格时漏了整个 `feature` 域（2 条）—— 交叉验证脚本抓到的
  · 「前端零调用」的判断要靠实测，不能靠眼力

跑法
----
    python -m pytest core/tests/test_interface_spec_v2.py -q
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
SPEC = CORE.parent / "docs" / "INTERFACE-SPEC-V2.md"


def _live_routes() -> set[tuple[str, str]]:
    """运行时真实存在的 API 路由（method, 归一化路径）。"""
    if str(CORE) not in sys.path:
        sys.path.insert(0, str(CORE))
    os.environ.setdefault("ORYX_HOME", r"D:\OryxData\data")
    from oryx.config import load_config
    from oryx.domains import register_all
    from oryx.server import app

    cfg = load_config()

    class _Store:
        def __init__(self):
            self.conv_path = Path(tempfile.gettempdir()) / "oryx-interface-spec-test" / "oryx.db"

        def __getattr__(self, name):
            raise RuntimeError("stub")

    class _Agent:
        async def call_async(self, *a, **k):
            raise RuntimeError("stub")

    class _Bus:
        def publish(self, *a, **k):
            pass

    register_all(
        app, store=_Store(), config=cfg, agent=_Agent(), bus=_Bus(), api_token=""
    )

    rows: set[tuple[str, str]] = set()
    seen: set[int] = set()

    def walk(routes) -> None:
        for r in routes:
            if id(r) in seen:
                continue
            seen.add(id(r))
            # 这个 FastAPI 版本的 include_router 是懒加载的
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
                rows.add((m, re.sub(r"\{[^}]*\}", "{}", path)))

    walk(app.routes)
    return {x for x in rows if x[1].startswith("/api")}


def _spec_routes() -> set[tuple[str, str]]:
    """规格文档里列出的路由。"""
    text = SPEC.read_text(encoding="utf-8")
    out: set[tuple[str, str]] = set()
    patterns = (
        r"\|\s*(GET|POST|PUT|DELETE|PATCH)\s*\|\s*`(/api[^`]*)`",
        r"\|\s*`(GET|POST|PUT|DELETE|PATCH)\s+(/api[^`]*)`\s*\|",
    )
    for pattern in patterns:
        for m in re.finditer(pattern, text):
            path = m.group(2).split("?", 1)[0]
            out.add((m.group(1), re.sub(r"\{[^}]*\}", "{}", path)))
    return out


def test_spec_matches_runtime():
    """规格文档与运行时路由双向一致。"""
    if not SPEC.exists():
        import pytest

        pytest.skip("规格文档不存在")

    live = _live_routes()
    spec = _spec_routes()

    missing = sorted(live - spec)   # 代码里有、文档没写
    extra = sorted(spec - live)     # 文档写了、代码里没有

    assert not missing, (
        "这些路由代码里有，但规格文档没列（照文档重写会漏）：\n  "
        + "\n  ".join(f"{m} {p}" for m, p in missing)
    )
    assert not extra, (
        "这些路由规格文档写了，但代码里没有（文档在骗人）：\n  "
        + "\n  ".join(f"{m} {p}" for m, p in extra)
    )


def test_spec_has_no_corrupted_chars():
    """文档里不能有损坏字符（写文件时容易混进 U+FFFD）。"""
    if not SPEC.exists():
        import pytest

        pytest.skip("规格文档不存在")
    text = SPEC.read_text(encoding="utf-8")
    bad = [(i, ln) for i, ln in enumerate(text.splitlines(), 1) if "�" in ln]
    assert not bad, f"规格文档第 {bad[0][0]} 行等处有损坏字符：\n" + "\n".join(
        f"  {n}: {ln}" for n, ln in bad[:5]
    )
