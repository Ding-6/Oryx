"""核心样式子集 —— 让前端扩展能复用核心的控件样式。

问题
----
上一轮给前端扩展做了 Shadow DOM 隔离 —— 效果很好，但有个副作用：
**核心的类名在扩展里用不了了**（`.iconbtn`、`.btn`、`.inp` 这些）。
扩展想放一个跟核心一样样的按钮，就得把样式抄一遍。

抄样式的问题：核心改了样式，扩展抄的那份不会跟着变 ——
过一阵两边就不一样了，界面看着就「不是一套的」。

解法
----
从 `main.css` 里**按类名提取子集**，注入扩展的 shadow root。
扩展声明「我要用 `.btn` 和 `.inp`」，后端就把相关的规则挑出来给它。

关键：提取的是**核心的原文**，不是复制品 —— 核心改了什么，
扩展下次拿到的就是新的。

实现
----
不引第三方 CSS 库（项目要保持零额外依赖），手写一个够用的扫描器：

* 逐字符扫，跟踪花括号深度
* 深度 0 的普通规则 → 选择器 + 声明体
* 深度 0 的 `@media` / `@supports` → **递归**处理其内容（保留包裹）
* `@keyframes` / `@font-face` / `@import` → 整体跳过或原样保留

「某个规则属于哪些类」：取选择器里所有 `.name`，一个都不少地记下来。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
STYLES_DIR = WEB_DIR / "styles"
MAIN_CSS = STYLES_DIR / "main.css"   # 拆分前的单文件；拆完它就不在了


def linked_css() -> list[Path]:
    """按 **index.html 里的 <link> 顺序**返回样式文件。

    为什么按 link 顺序而不是文件名排序
    --------------------------------
    CSS 的层叠靠**加载顺序**。样式拆成多个文件后，
    「哪个规则覆盖哪个」取决于 link 的先后 ——
    按文件名排序读出来的顺序可能和浏览器实际加载的不一样，
    提取出来的子集就会和页面实际长得不一样。

    所以**从 index.html 解析**，那才是真正被加载的顺序。
    """
    try:
        html = (WEB_DIR / "index.html").read_text("utf-8")
    except OSError:
        return []
    out: list[Path] = []
    for m in re.finditer(r'href="/web/styles/([^"]+\.css)"', html):
        p = STYLES_DIR / m.group(1)
        if p.exists():
            out.append(p)
    if not out and MAIN_CSS.exists():
        out.append(MAIN_CSS)
    return out


def all_css() -> str:
    """所有样式文件的内容，按加载顺序拼起来。"""
    parts = []
    for p in linked_css():
        try:
            parts.append(p.read_text("utf-8"))
        except OSError:
            continue
    return "\n".join(parts)

# 匹配选择器里的类名（`.foo`）
_CLASS_RE = re.compile(r"\.([A-Za-z_][\w-]*)")


@dataclass
class Rule:
    """一条 CSS 规则。"""

    selector: str
    body: str
    classes: frozenset[str]

    @property
    def text(self) -> str:
        return f"{self.selector} {{ {self.body} }}"


def _scan_rules(css: str, depth: int = 0) -> list[tuple[str, str, frozenset[str]]]:
    """扫出 (选择器, 声明体, 类集合) 列表。@media 的内容会递归展开并保留包裹。

    返回的每项要么是普通规则，要么是 `@media ... { ... }` 整块。

    两个容易出错的地方：

    1. @media 的声明体里**必须保留内层规则的选择器** ——
       只留声明体的话，`.btn { font-size: 11px }` 会变成
       `@media (...) { font-size: 11px }`，那是**无效 CSS**，整块被浏览器丢弃。
    2. @media 块的类集合必须是**内部规则的类并集** ——
       否则「这个 @media 块涉及哪些类」永远是空集，
       里面的规则一条都提取不出来。
    """
    out: list[tuple[str, str, frozenset[str]]] = []
    i = 0
    n = len(css)
    buf_start = 0

    while i < n:
        ch = css[i]
        if ch == "{":
            head = css[buf_start:i].strip()
            # 找匹配的右括号
            j = i + 1
            inner_start = j
            nest = 1
            while j < n and nest:
                if css[j] == "{":
                    nest += 1
                elif css[j] == "}":
                    nest -= 1
                j += 1
            inner = css[inner_start:j - 1] if j > inner_start else ""
            j = min(j, n)

            if head.startswith("@media") or head.startswith("@supports"):
                inner_rules = _scan_rules(inner, depth + 1)
                if inner_rules:
                    body = " ".join(
                        f"{sel} {{ {body} }}" for sel, body, _ in inner_rules
                    )
                    # 内部规则的类并集 —— 这个 @media 块涉及哪些类
                    classes: set[str] = set()
                    for _sel, _body, _cls in inner_rules:
                        classes |= _cls
                    out.append((head, body, frozenset(classes)))
            elif head.startswith("@keyframes") or head.startswith("@font-face"):
                # 动画与字体：原样保留（扩展很少用到，但保留不会错）
                out.append((head, inner, frozenset()))
            elif head and not head.startswith("@"):
                out.append((head, inner, frozenset(_CLASS_RE.findall(head))))

            i = j
            buf_start = j
            continue
        i += 1
    return out


def parse_css(css: str) -> list[Rule]:
    """把 CSS 解析成规则列表（@media 整块作为一条）。"""
    rules: list[Rule] = []
    for sel, body, classes in _scan_rules(css):
        rules.append(Rule(selector=sel, body=body, classes=classes))
    return rules


def _rule_matches(rule: Rule, wanted: set[str]) -> bool:
    """规则是否涉及目标类。

    注意 **不要求**目标类出现在选择器的**最后一个**位置 ——
    `.btn.primary` 匹配 `.btn` 时要整条保留（声明体是共用的）。
    """
    if not wanted:
        return False
    return bool(rule.classes & wanted)


def extract_by_class(css: str, wanted: list[str]) -> str:
    """按类名提取 CSS 子集。

    参数
    ----
    css
        完整 CSS 文本。
    wanted
        类名列表（不带点），如 `["btn", "inp"]`。
    """
    want = {w.strip().lstrip(".") for w in wanted if w and w.strip()}
    if not want:
        return ""
    parts: list[str] = []
    for rule in parse_css(css):
        if _rule_matches(rule, want):
            parts.append(rule.text)
    return "\n".join(parts)


def extract_variables(css: str) -> str:
    """提取 `:root` 里的 CSS 变量声明。

    **用不到** —— CSS 自定义属性是继承的，shadow root 里直接就有。
    保留这个函数是因为「万一将来要在 shadow root 里重新定义变量」
    （比如给扩展单独一套主题）时不用再写一遍解析。
    """
    out: list[str] = []
    for rule in parse_css(css):
        if rule.selector.strip() in (":root", "html") and "--" in rule.body:
            out.append(rule.text)
    return "\n".join(out)


def core_classes() -> list[str]:
    """核心 CSS 里出现过的所有类名（供扩展作者参考）。"""
    css = all_css()
    if not css:
        return []
    names: set[str] = set()
    for rule in parse_css(css):
        names |= rule.classes
    return sorted(names)


def subset_for(wanted: list[str]) -> str:
    """从核心 main.css 提取指定类名的子集。"""
    css = all_css()
    if not css:
        return ""
    return extract_by_class(css, wanted)
