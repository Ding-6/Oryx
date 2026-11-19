r"""本地目录浏览：给「此电脑」视图用。

与工具层的 fs_list 的分工：

    工具层 fs_list  受 PolicyEngine 的 allowed_roots 约束 —— Agent 只能在
                    授权目录里读文件，越界要用户确认（L2）。
    本模块         受独立的浏览护栏约束 —— 「此电脑」这个视图要让用户
                    看到整盘的结构（Marvis 就是这样），所以它不受
                    allowed_roots 限制，但有自己的一层防护。

关键安全约束（这是本模块存在的全部意义）：

    1. 路径必须 resolve 之后再判定。字符串前缀判断挡不住 Windows 的
       junction（目录联接）与符号链接逃逸 —— `C:\link` 指向 `C:\Windows`
       时，字符串看起来在允许范围内，resolve 之后就不在了。
    2. 不许 device path（`\\?\`、`\\.\`）与 UNC（`\\server\share`）。
       它们能绕过路径语义，前缀判断全部失效。
    3. forbidden_paths 里的目录一律不给看（Windows、Program Files…）。
    4. 只列一层，绝不递归 —— 递归扫整盘会把接口卡死。

**浏览目录树 ≠ 能读文件内容**。文件内容仍然只从 allowed_roots 里取
（见 server.py 的 _under_allowed_roots）。这里只暴露"目录结构"这一层元信息。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .symlink_guard import describe_link

# 单次扫描最多看多少条目。超过就停并标 truncated —— 一个 20 万项的目录
# 一次读完会把内存吃光。
MAX_SCAN = 20000
# 默认一次返回多少条
DEFAULT_LIMIT = 500
MAX_LIMIT = 2000


class BrowseError(Exception):
    """目录不可浏览。code 决定 HTTP 层回什么状态。"""

    def __init__(self, message: str, code: int = 400) -> None:
        super().__init__(message)
        self.code = code


def _norm(path: str) -> str:
    """把用户给的路径规整成绝对路径。

    Windows 上 ``C:\\foo`` 与 ``c:/foo`` 等价，``\\?\\C:\\foo`` 是 device path。
    """
    text = str(path or "").strip().strip('"')
    if not text:
        raise BrowseError("路径不能为空")
    # device path 与 UNC：路径语义特殊，一律挡掉
    if text.startswith("\\\\?\\") or text.startswith("\\\\.\\"):
        raise BrowseError("不支持这种路径写法", 403)
    if text.startswith("\\\\"):
        raise BrowseError("不支持网络路径", 403)
    try:
        return str(Path(text).expanduser())
    except (OSError, ValueError) as exc:
        raise BrowseError(f"路径不合法：{exc}") from None


def _is_under_drive_root(real: Path) -> bool:
    """必须在盘符根之下（C:\\ 或 D:\\）。

    这挡的是"跳出本机文件系统"：相对路径解析到别处、盘符换成别的卷等。
    """
    try:
        anchor = real.anchor
    except (OSError, ValueError):
        return False
    if not anchor:
        return False
    # Windows 下 anchor 形如 "C:\\"，判定要求 root 与 anchor 同一个卷
    root = str(real).upper()
    return root.startswith(anchor.upper())


def _forbidden_hit(real: Path, forbidden: list[str] | None) -> str:
    """落在哪个禁区里（没有则空串）。"""
    for raw in forbidden or []:
        try:
            block = Path(raw).resolve()
        except (OSError, ValueError):
            continue
        try:
            if real == block or real.is_relative_to(block):
                return str(block)
        except (OSError, ValueError):
            continue
    return ""


def safe_browse(path: str, forbidden: list[str] | None = None) -> Path:
    """把用户给的路径变成一个**可以安全浏览**的目录。

    抛 BrowseError 表示不该看。
    """
    raw = _norm(path)
    try:
        real = Path(raw).resolve()
    except (OSError, ValueError) as exc:
        raise BrowseError(f"路径不存在：{exc}", 404) from None
    if not real.exists():
        raise BrowseError("路径不存在", 404)
    if not real.is_dir():
        raise BrowseError("不是目录", 400)
    if not _is_under_drive_root(real):
        raise BrowseError("只能浏览本机磁盘下的目录", 403)
    hit = _forbidden_hit(real, forbidden)
    if hit:
        raise BrowseError(f"该目录受保护，不提供浏览：{hit}", 403)
    return real


def _is_hidden(entry: os.DirEntry) -> bool:
    """隐藏判定：Windows 用文件属性，POSIX 用点开头。

    Windows 的 FILE_ATTRIBUTE_HIDDEN 是 0x2；不跟随符号链接去 stat 目标，
    只看链接本身 —— 否则一个指向隐藏目录的链接会把它"洗白"。
    """
    name = entry.name
    if name.startswith("."):
        return True
    try:
        attrs = entry.stat(follow_symlinks=False).st_file_attributes
    except (OSError, AttributeError):
        return False
    return bool(attrs & 0x2)  # FILE_ATTRIBUTE_HIDDEN


def list_dir(
    path: str,
    forbidden: list[str] | None = None,
    show_hidden: bool = False,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    """列一个目录的内容（单层）。

    返回：{path, parent, entries, count, total, truncated, offset, limit}
    """
    real = safe_browse(path, forbidden)
    limit = max(1, min(int(limit), MAX_LIMIT))
    offset = max(0, int(offset))

    entries: list[dict[str, Any]] = []
    total = 0
    scanned = 0
    truncated = False

    try:
        with os.scandir(real) as it:
            for entry in it:
                scanned += 1
                if scanned > MAX_SCAN:
                    truncated = True
                    break
                try:
                    hidden = _is_hidden(entry)
                    is_dir = entry.is_dir(follow_symlinks=False)
                    try:
                        st = entry.stat(follow_symlinks=False)
                        size = 0 if is_dir else int(st.st_size)
                        mtime = int(st.st_mtime)
                    except OSError:
                        size, mtime = 0, 0
                except OSError:
                    continue
                if hidden and not show_hidden:
                    continue
                total += 1
                if total <= offset:
                    continue
                if len(entries) >= limit:
                    continue
                item = {
                    "name": entry.name,
                    "path": str(entry.path).replace("\\", "/"),
                    "is_dir": is_dir,
                    "size": size,
                    "mtime": mtime,
                    "ext": Path(entry.name).suffix.lower().lstrip("."),
                    "hidden": hidden,
                }
                # 链接条目要标出它指向哪、是否越出当前目录树。
                # 前端据此决定要不要给「打开 / 读内容」这类动作 ——
                # 一个指向 C:\Windows 的链接，路径看着在范围内，实际不是。
                try:
                    item.update(describe_link(str(real), entry))
                except OSError:
                    pass
                entries.append(item)
    except PermissionError as exc:
        raise BrowseError(f"没有权限读取该目录：{exc}", 403) from None
    except OSError as exc:
        raise BrowseError(f"读取目录失败：{exc}", 400) from None

    # 目录在前，同名按字母序 —— 跟文件管理器的手感一致
    entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))

    parent = ""
    try:
        p = real.parent
        if p != real:
            parent = str(p).replace("\\", "/")
    except (OSError, ValueError):
        parent = ""

    return {
        "path": str(real).replace("\\", "/"),
        "parent": parent,
        "entries": entries,
        "count": len(entries),
        "total": total,
        "truncated": truncated,
        "offset": offset,
        "limit": limit,
    }
