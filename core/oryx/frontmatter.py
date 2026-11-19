"""frontmatter 解析 —— **全项目只有这一份**。

背景
----
项目里曾经有**五份**几乎一样的 frontmatter 解析器：

    memory.py / registry.py / skills.py / experts.py / agents.py

而且**行为不一致**：

* `memory.py` 那份认行内列表 `[a, b]`、认布尔、认多行列表（但只对 `paths` 生效）
* 其余四份只认单行 `key: value`
* `agents.py` 更是先试严格 YAML、失败才回退宽松

后果是同一个 `triggers: [a, b]` 在不同代码路径下被解析成不同结果 ——
`["[a", "b]"]` 这种脏数据会一路流到运行时。

现在这里有**一份**，行为统一。

设计取舍：宽松，不引入 YAML
----------------------------
**为什么不用 PyYAML**（项目其实已经依赖它，`policy.py` 与 `agents.py` 都在用）：

严格 YAML 会**静默截断**带 `#` 的描述 ——

    description: 有 # 号 的文字      →  YAML 解析成 {'description': '有'}

`#` 前有空格就是 YAML 注释。这比解析失败更危险：**失败会报错，
截断只是悄悄丢内容**。

而「描述里带冒号」其实**不是问题**：`12:30` 的冒号后面没有空格，
YAML 认得它是值的一部分。所以当年那条「严格 YAML 会让老技能解析失败」
的顾虑，一半是不成立的。

宽松解析器保证：

* 描述里的 `#`、冒号、引号都**原样保留**
* 标量值一律取「第一个冒号之后的全部」

同时补上 YAML 本来能做的事：一层嵌套、行内列表、多行列表、布尔。

支持的写法
----------
    name: my-skill                  →  str
    description: 有 # 号 的文字        →  str（# 不当注释）
    description: 时间 12:30 开会       →  str
    triggers: a, b                  →  str（用 as_list 拆开）
    triggers: [a, b]                →  list[str]
    eager: true                     →  bool
    max_turns: 8                    →  str（保持字符串，见下）
    requires:                       →  dict（嵌套块）
      skills: web-research
      ui: chart-panel
    triggers:                       →  list[str]（多行列表）
      - a
      - b

> `max_turns` 这类**数字**故意保持字符串：
> 消费方一直是 `int(str(value))`，改成 int 也能过，但保持字符串
> 就不会让任何依赖「值是 str」的调用方突然拿到 int。
"""
from __future__ import annotations

import re
from typing import Any

__all__ = ["FRONTMATTER", "parse_frontmatter", "as_list", "as_bool",
           "parse_scalar"]

# frontmatter 的边界：`---` 开头，`---` 结束。
#
# 五处一模一样的正则，现在只有这一份。
FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.S)

# 列表分隔符：英文逗号、中文逗号、顿号。
# 与技能/专家代码里一直在用的 `re.split(r"[,，、]", ...)` 保持一致。
_SEPARATORS = re.compile(r"[,，、]")


def parse_scalar(value: str) -> Any:
    """把一个标量值转成合适的 Python 类型。

    * `[a, b]` → list[str]
    * `true` / `false` → bool
    * 其余 → str（去掉首尾引号）
    """
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [v.strip().strip("'\"") for v in _SEPARATORS.split(inner) if v.strip()]
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    return value.strip("'\"")


def _parse_children(children: list[tuple[int, str]]) -> Any:
    """解析缩进块（可能有多级）。

    * 全是 `- xxx` → list[str]
    * 否则 → dict（每个 `k: v`，无值的键递归解析它的子块）
    """
    if not children:
        return {}
    items = [c for c in children if c[1].strip()]
    if not items:
        return {}
    if all(it[1].startswith("- ") or it[1] == "-" for it in items):
        return [it[1][1:].strip().strip("'\"") for it in items if it[1] != "-"]
    return _parse_group(items)


def _parse_group(items: list[tuple[int, str]]) -> dict[str, Any]:
    """解析同一缩进层级里的 `k: v`。

    `k:` 后面没有值 → 它的子块（更深缩进）递归解析。
    """
    out: dict[str, Any] = {}
    i = 0
    n = len(items)
    while i < n:
        indent, content = items[i]
        if content.startswith("- "):
            # 顶层的列表项 —— 没有归属的键，忽略
            i += 1
            continue
        if ":" not in content:
            i += 1
            continue
        key, _, value = content.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            i += 1
            continue
        if value:
            out[key] = parse_scalar(value)
            i += 1
            continue
        # 无值 → 收集更深缩进的子项，递归
        base = items[i][0]
        j = i + 1
        deeper: list[tuple[int, str]] = []
        while j < n and items[j][0] > base:
            deeper.append(items[j])
            j += 1
        out[key] = _parse_children(deeper)
        i = j
    return out


def parse_block(raw: str) -> dict[str, Any]:
    """解析 frontmatter 的元数据部分（不含分隔符）。"""
    lines = raw.splitlines()
    items = [(len(ln) - len(ln.lstrip()), ln.strip()) for ln in lines if ln.strip()]
    return _parse_group(items)


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """解析 frontmatter，返回 `(元数据, 正文)`。

    没有 frontmatter 时返回 `({}, 原文)` —— 全文当正文。
    """
    match = FRONTMATTER.match(text)
    if not match:
        return {}, text
    return parse_block(match.group(1)), match.group(2)


def as_bool(value: Any, default: bool = False) -> bool:
    """把 frontmatter 的值读成布尔。

    认 `true` / `1` / `yes` / `on`（大小写不限）。

    为什么不直接用 `bool(value)` —— 那会把字符串 `"false"` 读成 True，
    「关掉这个开关」反而变成「打开」。
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "on"):
        return True
    if text in ("false", "0", "no", "off"):
        return False
    return default


def as_list(value: Any) -> list[str]:
    """把 frontmatter 的值统一成字符串列表。

    * `None` → `[]`
    * `"a, b"` → `["a", "b"]`（英文逗号 / 中文逗号 / 顿号都算分隔符）
    * `"[a, b]"` → `["a", "b"]`
    * `["a", "b"]` → `["a", "b"]`
    * `{"skills": [...]}` → **取它的值**（嵌套块里的列表）
    """
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("[") and text.endswith("]"):
            text = text[1:-1]
        return [v.strip().strip("'\"") for v in _SEPARATORS.split(text) if v.strip()]
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for v in value:
            out.extend(as_list(v) if isinstance(v, (list, tuple)) else [str(v)])
        return out
    if isinstance(value, dict):
        # 嵌套块：把里面的值摊平（`requires: {skills: [...]}` → 那串列表）
        out = []
        for v in value.values():
            out.extend(as_list(v))
        return out
    if isinstance(value, bool):
        return [str(value).lower()]
    return [str(value)]
