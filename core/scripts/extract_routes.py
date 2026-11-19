"""把已搬进 `domains/` 的路由从 `server.py` 里删掉。

用 **ast** 定位函数/类，不用正则 —— 正则切不准边界
（docstring 里有同样的字符串就完了）。

删的是「装饰器行 → 定义结束」整块，顺带把前面的空行吃掉。

用法
----
    python scripts/extract_routes.py <批次名>
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1] / "oryx" / "server.py"

# 批次 → (要删的路由 [(方法, 路径)], 要删的顶层定义, 要删的模块级常量)
BATCHES: dict[str, tuple[list[tuple[str, str]], list[str], list[str]]] = {
    # 最后一批：偏好 / 对话 / 应用 / 系统 —— 搬完 server.py 只剩框架层
    "batch5": (
        [
            # preference
            ("post", "/api/interaction"),
            ("post", "/api/style"),
            ("post", "/api/mode"),
            # chat（含剪贴板 / 粘贴 / 审批）
            ("post", "/api/chat"),
            ("post", "/api/chat/retry"),
            ("post", "/api/chat/cancel"),
            ("get", "/api/clipboard"),
            ("post", "/api/paste"),
            ("post", "/api/approve"),
            # app
            ("post", "/api/install"),
            ("get", "/api/apps"),
            ("get", "/api/app/icon"),
            ("get", "/api/apps/icons"),
            ("post", "/api/apps/launch"),
            ("get", "/api/apps/custom"),
            ("post", "/api/apps/custom"),
            ("put", "/api/apps/custom/{app_id}"),
            ("delete", "/api/apps/custom/{app_id}"),
            # system
            ("get", "/api/prewarm"),
            ("get", "/api/state"),
            ("get", "/api/audit/{task_id}"),
            ("get", "/api/usage"),
            ("post", "/api/prompt/optimize"),
            ("get", "/api/tasks"),
            ("get", "/api/capabilities"),
            ("get", "/api/sys/switches"),
            ("post", "/api/sys/query"),
            ("get", "/api/doctor"),
            ("get", "/api/fs/list"),
        ],
        # 请求模型 + 辅助 —— chat.py / system.py 里已有自己的副本
        [
            "_DispatchTracker", "_dispatch", "_skill_map",
            "ChatRequest", "RetryRequest", "CancelRequest", "ApprovalRequest",
            "ModeRequest", "InteractionRequest", "StyleRequest",
            "InstallRequest", "OptimizeRequest",
        ],
        [],
    ),
    "batch1": (
        [
            ("get", "/api/appearance"),
            ("post", "/api/appearance/background-fit"),
            ("post", "/api/appearance/theme"),
            ("post", "/api/appearance/background"),
            ("get", "/api/appearance/background.png"),
            ("get", "/api/features"),
            ("post", "/api/features/toggle"),
            ("get", "/api/memory"),
            ("post", "/api/memory"),
            ("delete", "/api/memory/{memory_id}"),
            ("post", "/api/remote/start"),
            ("post", "/api/remote/stop"),
            ("get", "/api/remote/status"),
            ("get", "/api/ui/base-css"),
            ("get", "/api/ui/core-classes"),
            ("get", "/api/ui/modules"),
            ("post", "/api/extensions/reload"),
            ("post", "/api/extensions/{ext_id}/enable"),
            ("post", "/api/extensions/{ext_id}/disable"),
            ("post", "/api/extensions/install"),
            ("delete", "/api/extensions/{ext_id}"),
            ("get", "/api/extensions/trash"),
            ("post", "/api/extensions/trash/restore"),
            ("post", "/api/extensions/trash/purge"),
            ("post", "/api/extensions/trash/cleanup"),
            ("post", "/api/extensions/trash/purge-all"),
            ("get", "/api/extensions"),
            ("delete", "/api/ui-extensions/{ext_id}"),
            ("post", "/api/ui-extensions/install"),
        ],
        [
            "_backend_ext_index", "_features_profile",
            "ThemeRequest", "BackgroundFitRequest", "BackgroundRequest",
            "RemoteStartRequest", "ExtensionActionRequest", "TrashRequest",
        ],
        [],
    ),
    "batch4": (
        [
            ("get", "/api/teams"),
            ("get", "/api/teams/{name}"),
            ("post", "/api/teams"),
            ("delete", "/api/teams/{name}"),
            ("put", "/api/teams/{name}"),
            ("post", "/api/teams/{name}/members"),
            ("delete", "/api/teams/{name}/members/{agent_id}"),
            ("post", "/api/teams/{name}/message"),
            ("post", "/api/teams/{name}/dispatch"),
            ("get", "/api/index/status"),
            ("post", "/api/index/scan"),
            ("post", "/api/index/search"),
            ("post", "/api/index/clear"),
            ("get", "/api/index/libraries"),
            ("get", "/api/index/library"),
            ("get", "/api/drives"),
            ("get", "/api/images"),
            ("get", "/api/image/thumb"),
        ],
        ["_under_allowed_roots", "ScanRequest", "SearchRequest"],
        [],
    ),
    "batch3": (
        [
            ("get", "/api/persona"),
            ("post", "/api/expert"),
            ("get", "/api/experts/{expert_id}"),
            ("post", "/api/experts"),
            ("put", "/api/experts/{expert_id}"),
            ("delete", "/api/experts/{expert_id}"),
            ("get", "/api/agents/{agent_id}"),
            ("post", "/api/agents"),
            ("put", "/api/agents/{agent_id}"),
            ("delete", "/api/agents/{agent_id}"),
        ],
        [
            "_experts_root", "_agents_root", "_expert_dir", "_agent_dir",
            "_write_expert_md", "_write_agent_md", "_reload_catalog",
            "_known_tools", "ExpertRequest",
        ],
        [],
    ),
    "batch2": (
        [
            ("get", "/api/tools/index"),
            ("post", "/api/tools/search"),
            ("get", "/api/mcp"),
            ("post", "/api/mcp/toggle"),
            ("get", "/api/files/preview"),
            ("post", "/api/files/open"),
            ("get", "/api/files/list"),
            ("get", "/api/workspace"),
            ("post", "/api/workspace"),
            ("get", "/api/doc"),
        ],
        ["_checked_file", "WorkspaceRequest", "ToolSearchRequest"],
        ["PREVIEW_EXTS", "PREVIEW_MAX_BYTES"],
    ),
}


def spans_to_remove(src: str, routes, top_level, consts) -> list[tuple[int, int]]:
    tree = ast.parse(src.lstrip("\ufeff"))
    spans: list[tuple[int, int]] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if node.name in top_level:
            start = node.lineno - 1
            for dec in node.decorator_list:
                start = min(start, dec.lineno - 1)
            spans.append((start, node.end_lineno))
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            func = dec.func
            if not isinstance(func, ast.Attribute):
                continue
            if not isinstance(func.value, ast.Name) or func.value.id != "app":
                continue
            if not dec.args or not isinstance(dec.args[0], ast.Constant):
                continue
            if (func.attr.lower(), str(dec.args[0].value)) in routes:
                spans.append((dec.lineno - 1, node.end_lineno))
                break
    return spans


def remove_consts(src: str, consts: list[str]) -> str:
    """删掉模块级常量（含它们的注释块）。"""
    lines = src.splitlines(keepends=True)
    for name in consts:
        for i, ln in enumerate(lines):
            if ln.strip().startswith(f"{name} = ") or ln.strip().startswith(f"{name}="):
                # 往上看，把紧挨着的注释行也吃掉
                j = i - 1
                while j >= 0 and lines[j].lstrip().startswith("#"):
                    j -= 1
                start = j + 1
                # 往后吃到匹配的右括号/结尾
                k = i
                depth = 0
                while k < len(lines):
                    depth += lines[k].count("{") + lines[k].count("[")
                    depth -= lines[k].count("}") + lines[k].count("]")
                    if depth <= 0 and "=" in lines[k]:
                        break
                    k += 1
                del lines[start:k + 1]
                break
    return "".join(lines)


def main() -> int:
    batch = sys.argv[1] if len(sys.argv) > 1 else "batch1"
    if batch not in BATCHES:
        print(f"未知批次：{batch}（有 {', '.join(BATCHES)}）")
        return 1
    routes, top_level, consts = BATCHES[batch]

    src = SERVER.read_text("utf-8-sig")
    spans = spans_to_remove(src, routes, top_level, consts)
    if not spans:
        print("没找到要删的路由 —— 可能已经删过了")
        return 1

    lines = src.splitlines(keepends=True)
    drop: set[int] = set()
    for start, end in spans:
        for i in range(start, end):
            drop.add(i)
        j = start - 1
        eaten = 0
        while j >= 0 and lines[j].strip() == "" and eaten < 2:
            drop.add(j)
            j -= 1
            eaten += 1

    kept = [ln for i, ln in enumerate(lines) if i not in drop]
    out = "".join(kept)
    if consts:
        out = remove_consts(out, consts)
    SERVER.write_text(out, encoding="utf-8-sig")
    print(f"删了 {len(spans)} 块，{len(lines)} 行 → {len(out.splitlines())} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
