"""记忆分层与条件规则。

对齐 WorkBuddy 的 memory 体系，但按 Oryx 的实际需要做了三处取舍：

1. **五层作用域**
       user      用户个人偏好（所有项目通用）
       project   项目团队约定（跟仓库走）
       local     项目个人偏好（不入库）
       rule      模块化的项目规范，按文件类型触发
       auto      运行中自动沉淀的经验

2. **条件规则精准注入**（最关键的省 token 手段）
       一份 300 行的编码规范整篇塞进上下文，每个 token 都白花。
       标了 `paths: ["src/**/*.ts"]` 的规则只在 Agent 真正碰 TS 文件时注入，
       而且**注入过就不重复注入**。

3. **子目录记忆按需加载**
       仓库里 `packages/foo/CONVENTIONS.md` 只有在动 packages/foo 下的文件时
       才进上下文。跟 WorkBuddy 的做法一致。
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

# frontmatter 解析 —— **全项目唯一一份**（oryx/frontmatter.py）。
# 以前这份解析器只对 `paths` 认行内列表与多行列表 —— 硬编码一个键，
# 别的键想用同样写法就不行。现在统一了。
from .frontmatter import as_bool, as_list, parse_frontmatter
from typing import Any, Iterable


# 记忆作用域
SCOPES = ("user", "project", "local", "rule", "auto")

# 项目约定的文件名（对齐 WorkBuddy 的 CODEBUDDY.md）
PROJECT_MEMORY_NAMES = ("ORYX.md", "AGENTS.md", ".oryx/CONVENTIONS.md")

# 条件规则 frontmatter
# @import 语法
_IMPORT = re.compile(r"@([A-Za-z0-9_./\\-]+\.md)")



def _match_segments(path_parts: list[str], pat_parts: list[str]) -> bool:
    """按路径段递归匹配，支持 ** 跨任意层。

    fnmatch 不认 ** —— `src/**/*.ts` 里的 `**/` 会被当成字面量，
    结果规则"看起来写了却永远不生效"。这里自己走一遍段匹配。
    """
    if not pat_parts:
        return not path_parts
    head = pat_parts[0]
    if head == "**":
        # 匹配零层或多层
        for i in range(len(path_parts) + 1):
            if _match_segments(path_parts[i:], pat_parts[1:]):
                return True
        return False
    if not path_parts:
        return False
    if not fnmatch.fnmatchcase(path_parts[0], head):
        return False
    return _match_segments(path_parts[1:], pat_parts[1:])


def glob_match(path: str, pattern: str) -> bool:
    """按 glob 匹配文件路径。

    WorkBuddy 的 matchBase 语义：**不含路径分隔符的模式匹配任意目录** ——
    `*.ts` 能命中 `src/utils/helper.ts`。这一点必须一致，否则用户写的
    规则会"看起来没生效"。

    另外支持大括号展开（`*.{ts,tsx}`）与逗号多模式。
    """
    if not pattern:
        return False
    norm_path = path.replace("\\", "/").lstrip("./")
    pat = pattern.replace("\\", "/").lstrip("./")

    # 大括号展开：src/**/*.{ts,tsx}
    if "{" in pat and "}" in pat:
        head, _, rest = pat.partition("{")
        opts, _, tail = rest.partition("}")
        for opt in opts.split(","):
            if glob_match(norm_path, f"{head}{opt}{tail}"):
                return True
        return False

    # 逗号组合多模式
    if "," in pat:
        return any(glob_match(norm_path, p.strip()) for p in pat.split(","))

    if "/" not in pat:
        # matchBase：不含 / 的模式只看文件名
        return fnmatch.fnmatchcase(os.path.basename(norm_path), pat)

    path_parts = [p for p in norm_path.split("/") if p]
    pat_parts = [p for p in pat.split("/") if p]
    return _match_segments(path_parts, pat_parts)


class ConditionalRule:
    """一条带触发条件的规则。"""

    def __init__(
        self,
        id: str,
        body: str,
        paths: Iterable[str] = (),
        always: bool = False,
        source: str = "",
    ):
        self.id = id
        self.body = body.strip()
        self.paths = list(paths)
        self.always = always
        self.source = source

    def triggers(self, file_path: str) -> bool:
        """这个文件路径会不会触发这条规则。"""
        if self.always or not self.paths:
            return self.always
        norm = file_path.replace("\\", "/")
        return any(glob_match(norm, p) for p in self.paths)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "paths": self.paths,
            "always": self.always,
            "source": self.source,
            "chars": len(self.body),
        }


class MemoryService:
    """分层记忆 + 条件规则。"""

    # 单条记忆的注入上限：超长的规则说明用户自己都写飞了
    MAX_RULE_CHARS = 4000
    # 单次注入的规则总上限
    MAX_INJECT_CHARS = 8000
    # @import 递归深度
    MAX_IMPORT_DEPTH = 5
    # 记忆目录名
    USER_DIR = "memory"

    def __init__(self, store: Any = None, home: Path | None = None,
                 workspace: str | Path | None = None):
        self.store = store
        self.home = Path(home) if home else None
        self.workspace = Path(workspace) if workspace else None
        self._injected: set[str] = set()   # 已注入过的规则 id
        self._loaded_dirs: set[str] = set()

    # ---------------------------------------------------------------- 载入

    def load_all(self) -> dict[str, Any]:
        """载入各作用域的记忆与规则。"""
        result: dict[str, Any] = {
            "user": [],
            "project": [],
            "local": [],
            "rules": [],
        }
        if self.home:
            result["user"] = self._read_dir(self.home / self.USER_DIR, "*.md")
        if self.workspace:
            result["project"] = self._read_project_memory(self.workspace)
            result["rules"] = self._read_rules(self.workspace)
        return result

    def _read_dir(self, directory: Path, pattern: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        if not directory.exists():
            return items
        try:
            for path in sorted(directory.glob(pattern)):
                try:
                    text = path.read_text("utf-8")
                except Exception:
                    continue
                text = self._expand_imports(text, directory, 0)
                items.append({"path": str(path), "text": text.strip()})
        except Exception:
            pass
        return items

    def _read_project_memory(self, workspace: Path) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for name in PROJECT_MEMORY_NAMES:
            path = workspace / name
            if not path.exists():
                continue
            try:
                text = path.read_text("utf-8")
            except Exception:
                continue
            items.append({
                "path": str(path),
                "text": self._expand_imports(text, workspace, 0).strip(),
            })
            # 项目记忆有 ORYX.md 就够了，AGENTS.md 是兼容位
            if name == "ORYX.md":
                break
        return items

    def _read_rules(self, workspace: Path) -> list[ConditionalRule]:
        """读 .oryx/rules/ 下的条件规则。"""
        rules: list[ConditionalRule] = []
        directory = workspace / ".oryx" / "rules"
        if not directory.exists():
            return rules
        for path in sorted(directory.rglob("*.md")):
            try:
                text = path.read_text("utf-8")
            except Exception:
                continue
            meta, body = parse_frontmatter(text)
            if not body.strip():
                continue
            paths = as_list(meta.get("paths"))
            always = as_bool(meta.get("alwaysApply"), default=not paths)
            rules.append(ConditionalRule(
                id=meta.get("id") or path.stem,
                body=body,
                paths=paths,
                always=always,
                source=str(path),
            ))
        return rules

    def _expand_imports(self, text: str, base: Path, depth: int) -> str:
        """展开 @import 引用。深度封顶，代码块内的 @ 不解析。"""
        if depth >= self.MAX_IMPORT_DEPTH or "@" not in text:
            return text
        # 去掉代码块，避免 `@tencent/x` 这类被误当导入
        stripped = re.sub(r"```.*?```", "", text, flags=re.S)

        def repl(match: re.Match[str]) -> str:
            rel = match.group(1)
            target = base / rel
            if not target.exists():
                target = Path(os.path.expanduser(rel))
            try:
                sub = target.read_text("utf-8")
            except Exception:
                return ""
            return self._expand_imports(sub, target.parent, depth + 1)

        expanded = _IMPORT.sub(repl, stripped)
        # 代码块还原
        return text.replace(stripped, expanded) if stripped != text else expanded

    # ---------------------------------------------------------------- 注入

    def rules_for_path(self, file_path: str) -> list[ConditionalRule]:
        """哪些条件规则会被这个路径触发（已排除注入过的）。"""
        result = []
        for rule in self._all_rules:
            if rule.id in self._injected:
                continue
            if rule.triggers(file_path):
                result.append(rule)
        return result

    def render_rules(self, rules: list[ConditionalRule]) -> str:
        """渲染要注入的规则文本。用完把 id 记进已注入集合。"""
        if not rules:
            return ""
        parts: list[str] = []
        used = 0
        for rule in rules:
            body = rule.body
            if len(body) > self.MAX_RULE_CHARS:
                body = body[: self.MAX_RULE_CHARS] + "\n…（规则过长已截断）"
            if used + len(body) > self.MAX_INJECT_CHARS:
                break
            parts.append(body)
            used += len(body)
            self._injected.add(rule.id)
        if not parts:
            return ""
        return "以下规范适用于你刚碰过的文件：\n" + "\n\n".join(parts)

    def remember(self, file_path: str) -> str:
        """在读写文件类操作前调：把适用的规范拼出来。

        已经注入过的不会重复注入 —— 一个会话里同一份规范出现一次就够。
        """
        try:
            rules = self.rules_for_path(file_path)
        except Exception:
            return ""
        return self.render_rules(rules)

    def always_rules(self) -> str:
        """alwaysApply 的规则，进系统提示（每次 run 都带）。"""
        try:
            rules = [r for r in self._all_rules if r.always and r.id not in self._injected]
        except Exception:
            return ""
        return self.render_rules(rules)

    def static_block(self, limit: int = 15, owner_id: str | None = None) -> str:
        """用户级 + 项目级记忆拼成的常驻块。"""
        data = self.load_all()
        lines: list[str] = []
        for scope, title in (("user", "用户偏好"), ("project", "项目约定")):
            for item in data.get(scope) or []:
                text = item["text"]
                if not text:
                    continue
                lines.append(f"【{title}】{text}")
        if self.store is not None:
            try:
                for m in self.store.search_memories("", limit, owner_id):
                    lines.append(f"- {m['text']}")
            except Exception:
                pass
        if not lines:
            return ""
        return "长期记忆：\n" + "\n".join(lines)

    # ---------------------------------------------------------------- 状态

    @property
    def _all_rules(self) -> list[ConditionalRule]:
        if not hasattr(self, "_rules_cache"):
            self._rules_cache = self.load_all().get("rules") or []
        return self._rules_cache

    def refresh(self, workspace: str | Path | None = None) -> None:
        """工作区变了就重载。"""
        if workspace is not None:
            self.workspace = Path(workspace)
        self._rules_cache = self.load_all().get("rules") or []
        self._injected.clear()

    def status(self) -> dict[str, Any]:
        data = self.load_all()
        return {
            "user": len(data.get("user") or []),
            "project": len(data.get("project") or []),
            "rules": [r.to_dict() for r in (data.get("rules") or [])],
            "injected": sorted(self._injected),
        }
