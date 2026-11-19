"""内置工具集。

每个工具有明确的 risk 声明，PolicyEngine 在此基础上做升级判定。
第三方能力后续通过 MCP 接入，不自带风险声明的工具一律按 L2 处理。
"""

from __future__ import annotations

import fnmatch
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .policy import Risk, is_credential_path

MAX_READ_BYTES = 200_000
MAX_LIST_ENTRIES = 200
SHELL_TIMEOUT = 60
# fs_grep 单个文件的上限。超过这个大小直接跳过 ——
# 逐行扫一个几百 MB 的日志会跑很久，而且命中多半是噪声。
MAX_GREP_BYTES = 4_000_000

# fs_glob / fs_grep 遍历时跳过的目录名。
# 与 indexer.SKIP_DIRS 同源：版本库、依赖目录、构建产物里
# 找业务文件只会找到噪声，还把遍历时间拖长了。
SKIP_DIRS = {
    "node_modules", ".git", ".svn", "__pycache__", ".venv", "venv", "env",
    ".idea", ".vscode", "$recycle.bin", "system volume information",
    ".next", ".nuxt", ".cache", "dist", "build", "target", "vendor",
}

# 会改动系统状态的工具名清单。
#
# 为什么用清单而不是每个 ToolSpec 写一个 mutates=True：
# 注册点有 30 多个，散着写一定会有人漏 —— 而漏掉的后果是
# 「这个操作声称自己没动过任何东西」，那比不说更糟。
#
# 与 risk 分开的原因：risk 是「危险等级」，mutates 是「有没有副作用」。
# shell_sandbox 是 L1（可逆写入）但常常只读；browser_open 是 L0 只读
# 但会改变浏览器当前页面。只靠 risk 判断不出「这个操作动没动东西」。
MUTATING_TOOLS = frozenset(
    {
        # 文件与索引
        "fs_write",
        "fs_delete",
        "fs_move",
        "index_scan",
        # 文档生成与多模态交付
        "docx_write",
        "xlsx_write",
        "pptx_write",
        "html_write",
        "pdf_write",
        "image_write",
        # 命令与终端
        "shell",
        "shell_sandbox",
        "term_run",
        "term_interrupt",
        # 浏览器（点击/输入会改页面状态；截图会落文件）
        "browser_click",
        "browser_type",
        # 下载会落文件 —— 漏了就会「声称自己没动过任何东西」
        "browser_download",
        "browser_screenshot",
        # 浏览器会话本身也改状态：打开会切换当前页面，关闭会销毁实例。
        # 以前这两个不在清单里，于是它们的结果带着「未改动」——
        # 那是在说谎（tools.py 顶部的注释说过：漏掉的后果比不说更糟）。
        "browser_open",
        "browser_close",
        # 应用与系统
        # 注意：apps_launch **不在这里** —— 启动应用是用户手势
        # （等价于双击桌面图标），只接受已登记的应用 id，不接受任意路径。
        # 刻意不暴露给模型：否则它就成了「运行任意程序」的通用原语。
        # HTTP 层有 /api/apps/launch，用户点界面上的图标即可。
        "sys_lock_screen",
        "sys_set_screen_timeout",
        "sys_set_dpi_scaling",
        "sys_set_wallpaper",
        # 配置与记忆
        "provider_add",
        "provider_use",
        "memory_save",
        "memory_forget",
        "workspace_switch",
        # 项目文档
        "doc_requirement_add",
        "doc_decision_add",
        "doc_change_log",
        # 团队与工作流（团队与编排都要落 SQLite）
        "team_create",
        "team_delete",
        "team_add_member",
        "team_send_message",
        "team_dispatch",
        "workflow_run",
        "workflow_cancel",
        # 子 Agent 派发：子 Agent 可能持写工具，产出真实落盘
        "spawn_agent",
        # 任务清单（task_create / task_update 是运行时注入的，不在静态注册表里）
        "task_create",
        "task_update",
        "checkpoint_rewind",
        "automation_add",
        "automation_remove",
        "automation_toggle",
        "remote_start",
        "remote_stop",
        "notify",
        "defer_execute",
    }
)


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    risk: Risk
    fn: Callable[..., dict[str, Any]]
    # 语义召回用的补充描述：写「什么情况下该用它」。
    # 留空时由 toolindex.build_embed_text 从 description 自动拼。
    embed_text: str = ""
    # 这个工具会不会改动系统状态（文件、数据库、页面）。
    # 留空（None）表示按 MUTATING_TOOLS 清单判定。
    mutates: bool | None = None

    def __post_init__(self) -> None:
        if self.mutates is None:
            self.mutates = self.name in MUTATING_TOOLS

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }

    def semantic_text(self) -> str:
        """送去做向量化的那段文本。"""
        if self.embed_text:
            return self.embed_text
        from .toolindex import build_embed_text

        return build_embed_text(self.name, self.description, self.risk.label)


def _ok(content: Any, **meta: Any) -> dict[str, Any]:
    return {"ok": True, "content": content, "meta": meta}


def _err(message: str, **meta: Any) -> dict[str, Any]:
    return {"ok": False, "content": message, "meta": meta}


# ------------------------------------------------------------------ 工具实现


def system_info() -> dict[str, Any]:
    info: dict[str, Any] = {
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "machine": platform.machine(),
        "hostname": platform.node(),
        "cpu": platform.processor() or "unknown",
        "cpu_count": os.cpu_count(),
    }

    try:
        import psutil  # type: ignore

        vm = psutil.virtual_memory()
        info["memory_total_gb"] = round(vm.total / 1024**3, 1)
        info["memory_used_percent"] = vm.percent
    except Exception as exc:
        # **别静默吞掉** —— 原来这里只留个 None，
        # psutil 装没装、坏了没坏，全都看不出来（排查时只能猜）。
        # 带上原因，谁都能一眼看出是「没装」还是「坏了」。
        info["memory_total_gb"] = None
        info["memory_error"] = str(exc)[:120]

    disks = []
    for drive in ("C:\\", "D:\\", "E:\\"):
        if os.path.exists(drive):
            try:
                usage = shutil.disk_usage(drive)
                disks.append(
                    {
                        "drive": drive,
                        "total_gb": round(usage.total / 1024**3, 1),
                        "free_gb": round(usage.free / 1024**3, 1),
                        "used_percent": round(usage.used / usage.total * 100, 1),
                    }
                )
            except Exception:
                continue
    info["disks"] = disks

    if sys.platform == "win32":
        info["gpu"] = _windows_gpu()
    return _ok(info)


def _windows_gpu() -> list[str]:
    try:
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        names = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        return names[:4]
    except Exception:
        return []


def fs_list(path: str = ".") -> dict[str, Any]:
    target = Path(os.path.expandvars(os.path.expanduser(path)))
    if not target.exists():
        return _err(f"路径不存在：{target}")
    if target.is_file():
        return _ok([_file_entry(target)], count=1)

    entries = []
    try:
        for item in sorted(
            target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())
        )[:MAX_LIST_ENTRIES]:
            entries.append(_file_entry(item))
    except PermissionError:
        return _err(f"没有权限读取：{target}")
    return _ok(entries, count=len(entries), truncated=len(entries) >= MAX_LIST_ENTRIES)


def _file_entry(p: Path) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": p.name, "path": str(p), "type": "dir" if p.is_dir() else "file"}
    try:
        st = p.stat()
        entry["size"] = st.st_size
        entry["mtime"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))
    except Exception:
        pass
    return entry


def fs_read(path: str, max_bytes: int = MAX_READ_BYTES) -> dict[str, Any]:
    """读文本文件。

    文件内容是**外部来源** —— 可能含伪装成指令的文本，
    所以套上边界标记（taint.wrap）再进上下文。
    同时过一遍打码层：文件里可能有 .env 里的密钥。
    """
    from .redact import redact
    from .taint import wrap

    target = Path(os.path.expandvars(os.path.expanduser(path)))
    if not target.exists():
        return _err(f"文件不存在：{target}")
    if target.is_dir():
        return _err(f"这是目录，请用 fs_list：{target}")
    size = target.stat().st_size
    try:
        raw = target.read_bytes()[: max_bytes]
    except PermissionError:
        return _err(f"没有权限读取：{target}")

    for encoding in ("utf-8", "gb18030", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        return _err(f"无法解码为文本（可能是二进制文件）：{target}", size=size)

    return _ok(wrap(redact(text), str(target)), path=str(target), size=size, truncated=size > max_bytes)


def fs_write(path: str, content: str) -> dict[str, Any]:
    target = Path(os.path.expandvars(os.path.expanduser(path)))
    existed = target.exists()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except Exception as exc:
        return _err(f"写入失败：{exc}")
    return _ok(f"已写入 {target}", path=str(target), bytes=len(content.encode("utf-8")), existed=existed)


def fs_delete(path: str) -> dict[str, Any]:
    target = Path(os.path.expandvars(os.path.expanduser(path)))
    if not target.exists():
        return _err(f"目标不存在：{target}")
    try:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    except Exception as exc:
        return _err(f"删除失败：{exc}")
    return _ok(f"已删除 {target}", path=str(target))


# ------------------------------------------------------------------ 移动 / 匹配 / 搜索
#
# 这三个工具以前**只存在于语义标注里**（loop.py 的 embed_text 提示词里
# 写着「重命名文件」「按模式批量查找」「正则搜内容」），但注册表里根本没有它们 ——
# 于是语义检索永远搜不到，模型只能退到 shell 跑 mv / findstr，
# 而 shell 是 L2 危险级，每次都要弹审批框。
#
# 补上以后：同样的事，走专用工具是 L0/L1，不用审批。


def fs_move(src: str, dst: str, overwrite: bool = False) -> dict[str, Any]:
    """移动 / 重命名文件或目录。

    用 shutil.move 而不是 os.rename：跨盘符时 os.rename 会直接失败
    （EXDEV），而移动文件到另一个盘是日常操作。
    """
    source = Path(os.path.expandvars(os.path.expanduser(src)))
    target = Path(os.path.expandvars(os.path.expanduser(dst)))
    if not source.exists():
        return _err(f"源不存在：{source}")
    if source == target:
        return _err("源与目标是同一个路径")
    # 目标已存在时默认拒绝 —— 静默覆盖会丢掉用户的数据
    if target.exists() and not overwrite:
        return _err(f"目标已存在：{target}（要覆盖请显式传 overwrite=true）")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
    except Exception as exc:
        return _err(f"移动失败：{exc}")
    return _ok(
        f"已移动 {source} → {target}",
        src=str(source),
        dst=str(target),
        # 自证字段：移动是「改动了东西」——源位置没了，目标位置多了
        moved=True,
    )


def fs_glob(
    root: str = ".",
    pattern: str = "*",
    limit: int = 100,
    include_dirs: bool = False,
) -> dict[str, Any]:
    """按文件名模式批量查找。

    pattern 用 fnmatch 语法：``*.py``、``test_*.md``、``*_v2.json``。
    递归遍历，深度有上限 —— 没有上限的话扫到 C 盘根会跑几十分钟。
    """
    base = Path(os.path.expandvars(os.path.expanduser(root)))
    if not base.exists():
        return _err(f"路径不存在：{base}")
    if not base.is_dir():
        return _err(f"不是目录：{base}")
    limit = max(1, min(int(limit), MAX_LIST_ENTRIES))
    hits: list[dict[str, Any]] = []
    truncated = False
    try:
        for dirpath, dirnames, filenames in os.walk(base):
            # 不跟随符号链接，也不进版本库与依赖目录
            dirnames[:] = [
                d for d in dirnames
                if d not in SKIP_DIRS and not (Path(dirpath) / d).is_symlink()
            ]
            # 凭据目录整个不进（.ssh / .aws / .kube）
            dirnames[:] = [
                d for d in dirnames if not is_credential_path(Path(dirpath) / d)
            ]
            if include_dirs:
                for d in dirnames:
                    full = Path(dirpath) / d
                    if fnmatch.fnmatch(d, pattern):
                        hits.append({"name": d, "path": str(full), "is_dir": True})
            for filename in filenames:
                full = Path(dirpath) / filename
                # 凭据文件不进结果 —— 路径名本身就是线索
                # （「我的密钥在哪」这个问题，答案不该由一个只读工具直接交出去）
                if is_credential_path(full):
                    continue
                if fnmatch.fnmatch(filename, pattern):
                    try:
                        size = full.stat().st_size
                    except OSError:
                        size = 0
                    hits.append({"name": filename, "path": str(full), "is_dir": False, "size": size})
                if len(hits) >= limit:
                    truncated = True
                    break
            if truncated:
                break
    except PermissionError as exc:
        return _err(f"没有权限读取：{exc}")
    return _ok(hits, count=len(hits), truncated=truncated, pattern=pattern)


def fs_grep(
    root: str = ".",
    pattern: str = "",
    glob: str = "",
    ignore_case: bool = False,
    limit: int = 100,
    max_hits_per_file: int = 20,
) -> dict[str, Any]:
    """在文件里按正则搜文字，返回命中行与行号。

    与 search_files 的区别：那个是**子串**匹配（而且大小写不敏感），
    遇到二进制或大文件直接跳过；这个走正则，能定位到行。
    """
    base = Path(os.path.expandvars(os.path.expanduser(root)))
    if not base.exists():
        return _err(f"路径不存在：{base}")
    if not pattern:
        return _err("要搜的正则不能为空")
    try:
        regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as exc:
        return _err(f"正则不合法：{exc}")
    limit = max(1, min(int(limit), MAX_LIST_ENTRIES))
    max_hits_per_file = max(1, min(int(max_hits_per_file), 200))

    hits: list[dict[str, Any]] = []
    files_scanned = 0
    files_matched = 0
    truncated = False
    try:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [
                d for d in dirnames
                if d not in SKIP_DIRS and not (Path(dirpath) / d).is_symlink()
            ]
            for filename in filenames:
                if glob and not fnmatch.fnmatch(filename, glob):
                    continue
                full = Path(dirpath) / filename
                # 凭据类文件不进搜索结果 —— root 参数不长得像凭据文件，
                # 但遍历会撞上目录里的 .env，把密钥内容读出来就等于没设防。
                if is_credential_path(full):
                    continue
                try:
                    if full.stat().st_size > MAX_GREP_BYTES:
                        continue
                    raw = full.read_bytes()
                except OSError:
                    continue
                # 二进制文件跳过：解出来的多半是乱码，命中也是噪声
                if b"\x00" in raw[:4096]:
                    continue
                text = raw.decode("utf-8", errors="ignore")
                files_scanned += 1
                matched = False
                for lineno, line in enumerate(text.splitlines(), start=1):
                    if not regex.search(line):
                        continue
                    matched = True
                    if len(hits) < limit:
                        hits.append({
                            "path": str(full),
                            "line": lineno,
                            "text": line.strip()[:400],
                        })
                    if sum(1 for h in hits if h["path"] == str(full)) >= max_hits_per_file:
                        break
                if matched:
                    files_matched += 1
                if len(hits) >= limit:
                    truncated = True
                    break
            if truncated:
                break
    except PermissionError as exc:
        return _err(f"没有权限读取：{exc}")
    return _ok(
        hits,
        count=len(hits),
        truncated=truncated,
        files_scanned=files_scanned,
        files_matched=files_matched,
    )


def shell(command: str) -> dict[str, Any]:
    """裸 shell。走审批（L2），但环境变量照样剔密钥、输出照样打码。

    这里不监禁工作目录（裸 shell 的语义就是「用户明确批准的那条命令」），
    但 env 剔除与输出打码两条不能省 —— 否则一句 `env` 就能把本机密钥读出来。
    """
    from .redact import redact
    from .sandbox import scrub_env

    try:
        env = scrub_env()
        if sys.platform == "win32":
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                timeout=SHELL_TIMEOUT,
                env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            encoding = "gb18030"
        else:
            proc = subprocess.run(
                command, shell=True, capture_output=True, timeout=SHELL_TIMEOUT, env=env
            )
            encoding = "utf-8"
        stdout = proc.stdout.decode(encoding, errors="replace")[:20000]
        stderr = proc.stderr.decode(encoding, errors="replace")[:5000]
        return _ok(
            {"stdout": redact(stdout), "stderr": redact(stderr), "exit_code": proc.returncode},
            command=redact(command),
        )
    except subprocess.TimeoutExpired:
        return _err(f"命令超时（>{SHELL_TIMEOUT}s）：{redact(command)}")
    except Exception as exc:
        return _err(f"执行失败：{redact(str(exc))}")


def search_files(
    root: str = ".", name_contains: str = "", content_contains: str = "", limit: int = 30
) -> dict[str, Any]:
    base = Path(os.path.expandvars(os.path.expanduser(root)))
    if not base.exists():
        return _err(f"路径不存在：{base}")
    hits: list[dict[str, Any]] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", "__pycache__")]
        for filename in filenames:
            if name_contains and name_contains.lower() not in filename.lower():
                continue
            full = Path(dirpath) / filename
            # 凭据类文件不参与内容搜索 —— 同 fs_grep：root 不像凭据文件，
            # 但遍历会撞上目录里的 .env。
            if is_credential_path(full):
                continue
            if content_contains:
                try:
                    if full.stat().st_size > 2_000_000:
                        continue
                    text = full.read_bytes().decode("utf-8", errors="ignore")
                except Exception:
                    continue
                if content_contains.lower() not in text.lower():
                    continue
            hits.append({"name": filename, "path": str(full), "size": full.stat().st_size})
            if len(hits) >= limit:
                return _ok(hits, count=len(hits), truncated=True)
    return _ok(hits, count=len(hits), truncated=False)


# ------------------------------------------------------------------ 注册表


def build_registry() -> dict[str, ToolSpec]:
    specs = [
        ToolSpec(
            name="system_info",
            description="读取本机操作系统、CPU、内存、磁盘、显卡信息。用于回答“我的电脑配置如何”。",
            parameters={"type": "object", "properties": {}, "required": []},
            risk=Risk.L0_READ,
            fn=lambda **_: system_info(),
        ),
        ToolSpec(
            name="fs_list",
            description="列出目录下的文件与子目录。",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string", "description": "目录路径"}},
                "required": ["path"],
            },
            risk=Risk.L0_READ,
            fn=lambda path=".": fs_list(path),
        ),
        ToolSpec(
            name="fs_read",
            description="读取文本文件内容。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "max_bytes": {"type": "integer", "description": "最多读取字节数"},
                },
                "required": ["path"],
            },
            risk=Risk.L0_READ,
            fn=lambda path, max_bytes=MAX_READ_BYTES: fs_read(path, max_bytes),
        ),
        ToolSpec(
            name="fs_write",
            description="写入文本文件（覆盖已有内容）。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
            risk=Risk.L1_WRITE,
            fn=lambda path, content: fs_write(path, content),
        ),
        ToolSpec(
            name="fs_delete",
            description="删除文件或目录（不可撤销）。",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            risk=Risk.L2_DESTRUCTIVE,
            fn=lambda path: fs_delete(path),
        ),
        ToolSpec(
            name="search_files",
            description="在目录中按文件名或文件内容搜索文件。",
            parameters={
                "type": "object",
                "properties": {
                    "root": {"type": "string", "description": "搜索根目录"},
                    "name_contains": {"type": "string", "description": "文件名包含的字符串"},
                    "content_contains": {"type": "string", "description": "文件内容包含的字符串"},
                    "limit": {"type": "integer"},
                },
                "required": ["root"],
            },
            risk=Risk.L0_READ,
            fn=lambda root=".", name_contains="", content_contains="", limit=30: search_files(
                root, name_contains, content_contains, limit
            ),
        ),
        ToolSpec(
            name="fs_move",
            description=(
                "移动或重命名文件/目录。比 shell 的 mv 好：跨盘符也能用，"
                "目标已存在时默认拒绝而不是静默覆盖。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "src": {"type": "string", "description": "源路径"},
                    "dst": {"type": "string", "description": "目标路径"},
                    "overwrite": {
                        "type": "boolean",
                        "description": "目标已存在时是否覆盖，默认 false",
                    },
                },
                "required": ["src", "dst"],
            },
            # L1 写入而不是 L2 危险：移动是可逆的（移回去就行），
            # 且默认不覆盖。不像删除那样不可撤销。
            risk=Risk.L1_WRITE,
            fn=lambda src, dst, overwrite=False: fs_move(src, dst, overwrite),
        ),
        ToolSpec(
            name="fs_glob",
            description=(
                "按文件名模式批量查找文件，比如找出所有 .py 文件、"
                "所有 test_ 开头的用例。模式用 * ? [] 语法。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "root": {"type": "string", "description": "搜索根目录"},
                    "pattern": {"type": "string", "description": "文件名模式，如 *.py"},
                    "limit": {"type": "integer", "description": "最多返回多少条"},
                    "include_dirs": {"type": "boolean", "description": "是否也返回匹配的目录"},
                },
                "required": ["root"],
            },
            risk=Risk.L0_READ,
            fn=lambda root=".", pattern="*", limit=100, include_dirs=False: fs_glob(
                root, pattern, limit, include_dirs
            ),
        ),
        ToolSpec(
            name="fs_grep",
            description=(
                "在文件内容里按正则搜索，返回命中的文件路径、行号与那行文字。"
                "比 search_files 强：走正则、能定位到行、能按文件名过滤。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "root": {"type": "string", "description": "搜索根目录"},
                    "pattern": {"type": "string", "description": "正则表达式"},
                    "glob": {
                        "type": "string",
                        "description": "只搜文件名匹配这个模式的文件，如 *.py",
                    },
                    "ignore_case": {"type": "boolean", "description": "忽略大小写"},
                    "limit": {"type": "integer", "description": "最多返回多少条命中"},
                },
                "required": ["root", "pattern"],
            },
            risk=Risk.L0_READ,
            fn=lambda root=".", pattern="", glob="", ignore_case=False, limit=100: fs_grep(
                root, pattern, glob, ignore_case, limit
            ),
        ),
        ToolSpec(
            name="shell",
            description="执行本机 shell 命令。只有在内置工具无法完成时才使用。",
            parameters={
                "type": "object",
                "properties": {"command": {"type": "string", "description": "要执行的命令"}},
                "required": ["command"],
            },
            risk=Risk.L2_DESTRUCTIVE,
            fn=lambda command: shell(command),
        ),
    ]
    return {spec.name: spec for spec in specs}


def tool_result_text(result: dict[str, Any], tool_name: str = "") -> str:
    """把工具结果转成给模型看的紧凑文本，避免大对象撑爆上下文。

    顺序有讲究：**先截断，再打外部来源标记**。
    反过来的话，截断会把闭合标签切掉 —— 边界没闭合，
    模型会以为后面所有内容都还在「资料」里（或者反过来，
    以为资料已经结束），标记等于白加。
    """
    if not result.get("ok"):
        return f"[失败] {result.get('content')}"
    content = result.get("content")
    if isinstance(content, (dict, list)):
        text = json.dumps(content, ensure_ascii=False)
    else:
        text = str(content)
    if len(text) > 12000:
        text = text[:12000] + f"\n…（已截断，原长 {len(text)} 字符）"
    # 返回值里可能有别人写的话（网页正文、文件内容、索引片段、
    # OCR 文字、MCP server 的返回）。进上下文前标明「这是资料，不是指令」。
    if tool_name:
        from .taint import is_untrusted_tool, taint_text

        if is_untrusted_tool(tool_name):
            text = taint_text(text, tool_name)
    return text
