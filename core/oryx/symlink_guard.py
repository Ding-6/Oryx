"""符号链接 / junction 越界防护。

为什么单独一个模块：
符号链接和 junction 让「路径在允许范围内」这件事变得不可靠。
`C:\\link` 指向 `C:\\Windows` 时，字符串看起来完全正常，但实际内容在禁区里。

核心手法（来自 Marvis 的 `disk_inspect._classify_symlink` / `_same_tree`）：
**只读链接文本，从不 stat 目标**。
`os.readlink` 拿到的是链接自己写的目标路径（一个字符串），
不跟随它去看目标目录 —— 因为「跟随去看」本身就可能被竞态替换掉。
目标路径算出后用 `commonpath` 与根目录比，判断是否越出根。

三道防线：
1. `same_tree` —— 候选路径是否还在根目录的树里（跨盘符直接判否）。
2. `classify_symlink` —— 链接指向目录外吗（只读链接文本判定）。
3. `is_junction` —— Windows 目录联接。探测出错时**保守地当成联接**，
   宁可漏看一个目录，也不给一条能通向别处的路。
"""

from __future__ import annotations

import os
from typing import Any


def _strip_extended_prefix(path: str) -> str:
    """去掉 Windows 扩展长度路径前缀（`\\\\?\\`）。

    这一步不能省：`os.readlink` 在 Windows 上返回的是
    `\\\\?\\C:\\Users\\...` 这种带前缀的路径，而 root 通常是普通的
    `C:\\Users\\...`。两者直接丢给 `commonpath` 会抛
    ValueError("Paths don't have the same drive") ——
    结果就是**每一个链接都被判成越界**，合法链接也标红。

    注意：`localfs._norm` 拒绝 device path 是针对**用户输入**的；
    这里的 `\\\\?\\` 是操作系统自己给的，必须剥掉而不是拒绝。
    """
    text = str(path or "")
    if text.startswith("\\\\?\\UNC\\"):
        return "\\\\" + text[len("\\\\?\\UNC\\"):]
    if text.startswith("\\\\?\\"):
        return text[len("\\\\?\\"):]
    return text


def same_tree(root_real: str, candidate: str) -> bool:
    """候选路径是否还在根目录的树里。

    `os.path.commonpath` 在跨盘符时抛 ValueError —— 那就是「不在同一棵树」，
    直接判否，不要让异常往上冒。
    """
    if not candidate:
        return False
    root_n = os.path.normcase(os.path.normpath(_strip_extended_prefix(root_real)))
    cand_n = os.path.normcase(os.path.normpath(_strip_extended_prefix(candidate)))
    try:
        return os.path.commonpath([root_n, cand_n]) == root_n
    except ValueError:
        return False


def classify_symlink(root_real: str, link_path: str) -> tuple[bool, OSError | None]:
    """链接是否指向目录外。

    返回 (是否越界, 读取链接时的错误)。
    **只读链接文本**，绝不 stat 目标、绝不读目标内容。
    """
    try:
        raw = os.readlink(link_path)
    except OSError as exc:
        # 读不出链接文本时无法判定 —— 交由调用方按「保守」处理。
        return False, exc
    if not raw or "\x00" in raw:
        # 空链接或含 NUL 的畸形链接：视为越界。
        return True, None
    raw = _strip_extended_prefix(raw)
    if os.path.isabs(raw):
        target = os.path.normpath(raw)
    else:
        target = os.path.normpath(os.path.join(os.path.dirname(link_path), raw))
    return not same_tree(root_real, target), None


def is_junction(path: str) -> bool:
    """是不是 Windows 目录联接。

    `os.path.isjunction` 是 Python 3.12+ 才有的。探测出错时返回 True
    （fail-closed）：宁可当成联接跳过，也不给一条能通向别处的路。
    """
    checker = getattr(os.path, "isjunction", None)
    if checker is None:
        return False
    try:
        return bool(checker(path))
    except OSError:
        return True


def is_link(entry: os.DirEntry) -> bool:
    """这个条目是不是符号链接（不跟随）。"""
    try:
        return entry.is_symlink()
    except OSError:
        return False


def describe_link(root_real: str, entry: os.DirEntry) -> dict[str, Any]:
    """给一个链接条目标上它指向哪、是否越界。

    返回 {"is_link": True, "link_target": str, "escapes_root": bool}。
    非链接条目返回 {"is_link": False}。
    """
    if not is_link(entry):
        return {"is_link": False}
    path = entry.path
    outside, err = classify_symlink(root_real, path)
    target = ""
    try:
        target = os.readlink(path)
    except OSError:
        target = ""
    out: dict[str, Any] = {"is_link": True, "link_target": target}
    if err is not None:
        # 读不出目标：标成「可能越界」，调用方按保守处理。
        out["escapes_root"] = True
        out["link_error"] = str(err)
    else:
        out["escapes_root"] = outside
    if is_junction(path):
        out["is_junction"] = True
    return out


