"""项目文档：把需求、决策、变更记到**同一个文件**里。

为什么是单文件：用户要的是"所有修改都在这个文件里"。多文件会在真实使用中
很快散架——没人知道该看哪个。所以固定一份 `.oryx/PROJECT.md`，按区块追加。

结构：
    # 项目文档
    ## 概况
    ## 需求（REQ-001 …）
    ## 决策（DEC-001 …）
    ## 变更记录（CHG-001 …）
    ## 待办
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

DOC_RELATIVE = Path(".oryx") / "PROJECT.md"


TEMPLATE = """# 项目文档

> 这份文件由 Oryx 自动维护：需求、决策、每一次改动都记录在这里。
> 区块顺序固定，编号只增不减。

## 概况

（项目目标、技术栈、运行方式）

## 需求

## 决策

## 变更记录

## 待办
"""


@dataclass
class Entry:
    kind: str
    id: str
    title: str
    detail: str
    ts: float

    def render(self) -> str:
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(self.ts))
        lines = [f"### {self.id} {self.title}", f"*{stamp}*", ""]
        if self.detail:
            lines.append(self.detail.strip())
            lines.append("")
        return "\n".join(lines)


class ProjectDoc:
    def __init__(self, workspace: str | Path):
        self.root = Path(workspace).expanduser()
        self.path = self.root / DOC_RELATIVE
        self._lock = threading.Lock()

    # ---------------- 基础 ----------------
    def exists(self) -> bool:
        return self.path.exists()

    def _read(self) -> str:
        if not self.path.exists():
            return TEMPLATE
        try:
            return self.path.read_text("utf-8")
        except Exception:
            return TEMPLATE

    def _write(self, text: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(text, encoding="utf-8")

    # ---------------- 编号 ----------------
    def _next_id(self, text: str, prefix: str) -> str:
        found = [int(m) for m in re.findall(rf"{prefix}-(\d{{3,}})", text)]
        return f"{prefix}-{(max(found) + 1) if found else 1:03d}"

    # ---------------- 追加 ----------------
    def _append(self, section: str, entry: Entry) -> str:
        text = self._read()
        if f"## {section}" not in text:
            text = text.rstrip() + f"\n\n## {section}\n"
        head, _, tail = text.partition(f"## {section}")
        # 找到该区块末尾（下一个 ## 之前）
        next_section = re.search(r"\n## ", tail)
        body, rest = (tail[: next_section.start()], tail[next_section.start() :]) if next_section else (tail, "")
        body = body.rstrip() + "\n\n" + entry.render()
        self._write(head + f"## {section}" + body + rest)
        return entry.id

    def add_requirement(self, title: str, detail: str = "") -> str:
        with self._lock:
            text = self._read()
            entry = Entry("requirement", self._next_id(text, "REQ"), title, detail, time.time())
            return self._append("需求", entry)

    def add_decision(self, title: str, detail: str = "") -> str:
        with self._lock:
            text = self._read()
            entry = Entry("decision", self._next_id(text, "DEC"), title, detail, time.time())
            return self._append("决策", entry)

    def log_change(self, summary: str, files: list[str] | None = None, rationale: str = "") -> str:
        detail_parts = []
        if files:
            detail_parts.append("改动文件：\n" + "\n".join(f"- `{f}`" for f in files))
        if rationale:
            detail_parts.append("原因：" + rationale.strip())
        with self._lock:
            text = self._read()
            entry = Entry(
                "change", self._next_id(text, "CHG"), summary, "\n\n".join(detail_parts), time.time()
            )
            return self._append("变更记录", entry)

    def add_todo(self, title: str, detail: str = "") -> str:
        with self._lock:
            text = self._read()
            entry = Entry("todo", self._next_id(text, "TODO"), title, detail, time.time())
            return self._append("待办", entry)

    # ---------------- 读取 ----------------
    def read(self, max_chars: int = 20000) -> str:
        text = self._read()
        return text[:max_chars]

    def stats(self) -> dict:
        text = self._read()
        return {
            "path": str(self.path),
            "exists": self.path.exists(),
            "requirements": len(re.findall(r"REQ-\d{3,}", text)),
            "decisions": len(re.findall(r"DEC-\d{3,}", text)),
            "changes": len(re.findall(r"CHG-\d{3,}", text)),
            "todos": len(re.findall(r"TODO-\d{3,}", text)),
            "bytes": len(text.encode("utf-8")),
        }
