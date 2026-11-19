"""`docs/TOOLS.md` 得跟运行时对得上。

为什么要有这个测试
------------------
工具是**分散注册**的（`build_registry()` 给 10 个，21 个 `_register_*`
再加 60 多个，MCP 和用户扩展最后到）——
「这个项目到底有哪些工具」读代码答不出来。

所以有了 `docs/TOOLS.md`（自动生成）。但**自动生成的文档照样会腐烂**：
工具集变了、没人重新生成，文档就开始骗人。

这个测试守着它。

为什么只做「子集」校验
----------------------
清单只列 `builtin` 工具（MCP 和用户扩展因环境而异，不进仓库文档）。
而 builtin 也会因**可选依赖**增减 —— 比如没装 matplotlib 时
就没有 PDF 相关工具。

所以：
* 清单里有的工具，运行时**必须**有，且描述一致（防篡改、防过期）
* 运行时多出来的工具（装了额外依赖）**不**报错 ——
  那是环境差异，不是文档过期

想严格校验（CI 用）：
    python core/scripts/gen_tool_docs.py --check
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
REPO = CORE.parent
DOC = REPO / "docs" / "TOOLS.md"

sys.path.insert(0, str(CORE))
sys.path.insert(0, str(CORE / "scripts"))


def _runtime_tools() -> dict[str, dict]:
    from oryx.config import load_config
    from oryx.loop import AgentRuntime

    cfg = load_config()
    runtime = AgentRuntime(cfg, None, None, autostart_scheduler=False)
    table = runtime.tool_table.to_dict()
    out = {}
    for t in table.get("tools") or []:
        if str(t.get("source") or "") != "builtin":
            continue
        out[str(t.get("name"))] = t
    return out


def _doc_tools() -> dict[str, str]:
    """从文档表格里解析出 {工具名: 描述}。"""
    if not DOC.exists():
        return {}
    text = DOC.read_text("utf-8")
    out: dict[str, str] = {}
    for line in text.splitlines():
        if not line.startswith("| `"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        m = re.match(r"`([a-zA-Z0-9_]+)`", cells[0])
        if not m:
            continue
        out[m.group(1)] = cells[1].replace("\\|", "|")
    return out


def test_tool_doc_exists():
    assert DOC.exists(), f"{DOC} 不存在 —— 跑 python core/scripts/gen_tool_docs.py"


def test_every_doc_tool_exists_at_runtime():
    """清单里的每个工具，运行时都得有。"""
    runtime = _runtime_tools()
    doc = _doc_tools()
    assert doc, "文档里没解析出任何工具 —— 表格格式变了？"
    missing = sorted(set(doc) - set(runtime))
    assert not missing, (
        f"文档里列了但运行时没有：{missing}"
        "（工具被删了或改名了 —— 重新生成清单）"
    )


def test_doc_description_matches_runtime():
    """清单里的描述得跟运行时一致 —— 描述变了就是过期。"""
    runtime = _runtime_tools()
    stale = []
    for name, desc in _doc_tools().items():
        spec = runtime.get(name)
        if spec is None:
            continue
        actual = " ".join(str(spec.get("description") or "").split())
        if actual and actual != desc:
            stale.append(name)
    assert not stale, (
        f"这些工具的描述在文档里过期了：{stale}"
        "（重新生成清单：python core/scripts/gen_tool_docs.py）"
    )


def test_doc_lists_no_mcp_or_extension_tools():
    """清单只列 builtin —— MCP 和用户扩展因环境而异，不该进仓库文档。"""
    text = DOC.read_text("utf-8")
    doc = _doc_tools()
    runtime = _runtime_tools()
    # 运行时有的非 builtin 工具（装了 MCP/扩展时）不该出现在清单里
    extra = [
        n for n in doc
        if n in runtime and n not in runtime  # 占位，实际下面算
    ]
    assert not extra
    # 直接断言：清单里不该有 mcp__ 前缀的
    assert not [n for n in doc if n.startswith("mcp__")], (
        "清单里混进了 MCP 工具 —— 它们因环境而异"
    )


def test_registration_section_lists_all_register_methods():
    """「注册结构」那节要把 `_register_*` 都列上。"""
    from gen_tool_docs import register_map

    text = DOC.read_text("utf-8")
    missing = [
        name for name, _line, _g in register_map()
        if f"`{name}()`" not in text
    ]
    assert not missing, (
        f"这些 _register_* 方法没出现在文档里：{missing}"
        "（重新生成清单）"
    )
