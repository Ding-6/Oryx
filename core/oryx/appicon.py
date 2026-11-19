"""Windows 应用图标提取。

为什么不引第三方库：项目只依赖 Pillow，图标提取要的是 Windows 自带能力。

实现走 **PowerShell + .NET / Win32**，而不是 ctypes 直接调 GDI。试过 ctypes 那条路，
坑比想象的多：`ExtractIconEx` 在 shell32 不在 user32、64 位句柄被 ctypes 默认的
c_int 截断、GetDIBits 对 HICON 直接调用返回 0……每一步都能静默拿到空图。
.NET + P/Invoke 这条路一次成功，而且它自己处理了 DPI 与 alpha 通道。

三种提取途径，按适用场景分工（实测覆盖 390 个应用中的 266 个有线索的）：

    assoc  Icon.ExtractAssociatedIcon      exe / lnk / 目录 的关联图标。
                                            命中最多（实测 175/266），对 .lnk
                                            尤其好用 —— 快捷方式指向的目标
                                            已经卸载了也能取到图标。
    ex     shell32.ExtractIconEx(索引)      需要按图标序号取时用。能读 DLL
                                            资源（imageres.dll 这类系统图标），
                                            也能读带索引的 exe（实测 79/266）。
    sh     shell32.SHGetFileInfo             目录图标（取目录里没有 exe 的
                                            安装文件夹）。实测只兜底 1 个，
                                            但留着便宜。

**批量是性能的关键。** 单个图标起一个 PowerShell 进程要 1.1 秒，300 个应用
就是 5 分半；把任务清单写成文件、一次 PowerShell 进程里循环处理，390 个应用
10.5 秒跑完。所以这里的入口是 :func:`extract_batch`，而不是逐个提取。

任何一步失败都返回 None，前端回退到首字母色块 —— 图标是锦上添花，
不该成为功能可用性的前提。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

_IS_WINDOWS = sys.platform == "win32"

# 缓存：lnk 路径 → (mtime, target)
_lnk_cache: dict[str, tuple[float, str]] = {}
_lnk_lock = threading.Lock()
# 缓存：目标路径+mtime → PNG 字节
_icon_cache: dict[str, bytes] = {}
_icon_lock = threading.Lock()
# 图标缓存上限 —— 太多会白占内存
_ICON_CACHE_MAX = 512

# 单图标提取的并发上限。
#
# 为什么要有：单图标提取要起一个 PowerShell 进程（约 1.1 秒），
# 跑在 FastAPI 的线程池里。200 多个并发请求会把 40 个槽位全占住，
# 同一时刻点的「打开应用」只能排队 —— 实测最长等 96 秒。
#
# **关键：不能阻塞地排队。**
# 试过用信号量（`with sem:`）—— 更糟，实测 194 秒。
# 原因是：40 个线程全卡在信号量上等着，只有 4 个在干活，
# 而等待本身**占着线程池的槽位** —— 「打开应用」还是排不进去。
#
# 正确做法是**快速失败**：忙的时候直接返回「取不到」，
# 让调用方退回首字母色块。图标是锦上添花，不值得让整个后端排队。
# 4 个并发时 258 个图标只要 1~2 秒就消化完（其余秒退）。
_EXTRACT_CONCURRENCY = 4
_extract_active = 0
_extract_active_lock = threading.Lock()


def _extract_try_begin() -> bool:
    """占一个提取名额。忙的时候返回 False（调用方该直接放弃）。"""
    global _extract_active
    with _extract_active_lock:
        if _extract_active >= _EXTRACT_CONCURRENCY:
            return False
        _extract_active += 1
        return True


def _extract_end() -> None:
    global _extract_active
    with _extract_active_lock:
        if _extract_active > 0:
            _extract_active -= 1

# 磁盘缓存目录：批量图标结果落这里。
#
# 为什么要有：批量提取 258 个图标要 6.5 秒（一次 PowerShell 进程循环）。
# 用户每次打开应用库都要等这 6.5 秒 —— 第二次打开完全可以直接读缓存。
# 图标是**只读资源**（应用图标不会变，除非用户重装了那个应用），
# 落盘缓存的收益远大于风险。
_icon_disk_dir: Path | None = None
_icon_disk_lock = threading.Lock()


def _icon_cache_dir() -> Path | None:
    """磁盘图标缓存目录。建不出来就返回 None（退回每次现算）。"""
    global _icon_disk_dir
    with _icon_disk_lock:
        if _icon_disk_dir is not None:
            return _icon_disk_dir if _icon_disk_dir.name else None
        try:
            from .config import app_dir

            d = app_dir() / "iconcache"
            d.mkdir(parents=True, exist_ok=True)
        except Exception:
            _icon_disk_dir = Path("")
            return None
        _icon_disk_dir = d
        return d


def _disk_icon_path(key: str) -> Path | None:
    """某个键的磁盘缓存文件路径。"""
    d = _icon_cache_dir()
    if d is None:
        return None
    return d / (hashlib.sha1(key.encode("utf-8")).hexdigest() + ".png")


def disk_icon_get(key: str) -> bytes | None:
    """读磁盘缓存的图标。"""
    p = _disk_icon_path(key)
    if p is None or not p.exists():
        return None
    try:
        data = p.read_bytes()
    except OSError:
        return None
    return data if data[:8] == b"\x89PNG\r\n\x1a\n" else None




def disk_icon_put(key: str, png: bytes) -> None:
    """写磁盘缓存。失败不算错 —— 图标只是锦上添花。"""
    p = _disk_icon_path(key)
    if p is None:
        return
    try:
        p.write_bytes(png)
    except OSError:
        pass


def clear_cache() -> None:
    """清内存里的 .lnk / 图标缓存。"""
    with _lnk_lock:
        _lnk_cache.clear()
    with _icon_lock:
        _icon_cache.clear()


def clear_disk_cache() -> None:
    """清磁盘图标缓存 + 内存缓存。

    `clear_cache()` 只清内存；图标还落盘在 app_dir()/iconcache，
    不清的话「批量 vs 逐个」这类冷缓存基准测出来的其实是热缓存。
    这是性能回归测试的公共入口，不是死代码。
    """
    clear_cache()
    d = _icon_cache_dir()
    if d is None:
        return
    try:
        for p in d.glob("*.png"):
            try:
                p.unlink()
            except OSError:
                pass
    except OSError:
        pass


# ---------------------------------------------------------------- PowerShell
def _powershell() -> str | None:
    """找一个能用的 powershell。

    PATH 里未必有（精简过的 Windows 环境常见），所以再试几个绝对路径。
    """
    found = shutil.which("powershell") or shutil.which("pwsh")
    if found:
        return found
    for path in (
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        r"C:\Windows\SysWOW64\WindowsPowerShell\v1.0\powershell.exe",
    ):
        if os.path.exists(path):
            return path
    return None


def _run_ps(script: str, timeout: float = 30.0) -> str:
    """跑一段 PowerShell，返回 stdout。失败返回空串。"""
    exe = _powershell()
    if not exe:
        return ""
    try:
        proc = subprocess.run(
            [exe, "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            # 中文 Windows 上 PowerShell 默认输出 GBK，强制 UTF-8 后仍可能混进
            # 别的编码字节 —— 宁可丢字符也不要整个进程崩掉
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return proc.stdout or ""
    except Exception:
        return ""


# ---------------------------------------------------------------- .lnk 解析
def resolve_shortcuts(paths: list[str]) -> dict[str, str]:
    """批量把 .lnk 解析成目标路径。返回 {lnk 路径: target}。

    解析不出来的不出现在结果里 —— 调用方据此判断"这不是快捷方式"。
    """
    if not paths or not _IS_WINDOWS:
        return {}
    todo: list[tuple[str, float]] = []
    with _lnk_lock:
        for p in paths:
            try:
                mtime = os.path.getmtime(p)
            except OSError:
                continue
            hit = _lnk_cache.get(p)
            if hit and hit[0] == mtime:
                continue
            todo.append((p, mtime))
    if not todo:
        with _lnk_lock:
            wanted = set(paths)
            return {p: t for p, (_, t) in _lnk_cache.items() if p in wanted}

    # 路径用 JSON 数组传进去再 ConvertFrom-Json 展开 —— 试过 @{} 哈希表数组
    # 的写法，PowerShell 在 -Command 模式下解析不出来（静默输出空），
    # JSON 数组这条路稳得多。路径里的反斜杠交给 JSON 转义处理。
    payload = json.dumps([p for p, _ in todo], ensure_ascii=False)
    script = (
        "$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n"
        "$sh = New-Object -ComObject WScript.Shell\n"
        f"$paths = '{payload}' | ConvertFrom-Json\n"
        "foreach ($k in $paths) {\n"
        "  try {\n"
        "    $sc = $sh.CreateShortcut([string]$k)\n"
        "    $t = $sc.TargetPath\n"
        "    if ($t) { Write-Output ([string]$k + \"`t\" + $t) }\n"
        "  } catch {}\n"
        "}\n"
    )
    out = _run_ps(script)

    found: dict[str, str] = {}
    for line in out.splitlines():
        if "\t" not in line:
            continue
        src, target = line.split("\t", 1)
        src = src.strip()
        target = target.strip()
        if src and target:
            found[src] = target
    with _lnk_lock:
        for p, mtime in todo:
            t = found.get(p)
            if t:
                _lnk_cache[p] = (mtime, t)
        result = {}
        for p in paths:
            hit = _lnk_cache.get(p)
            if hit:
                result[p] = hit[1]
    return result




# ---------------------------------------------------------------- 候选链
def split_spec(spec: str) -> tuple[str, int]:
    """拆 ``DisplayIcon`` 的两种写法。

        'D:\\App\\app.exe,0'      → ('D:\\App\\app.exe', 0)
        '"D:\\App\\app.exe",-3'   → ('D:\\App\\app.exe', -3)

    负数是**资源 ID**，非负是**图标序号** —— 两者在 shell32 里的取法不同。
    """
    text = str(spec or "").strip()
    if not text:
        return ("", 0)
    text = text.strip('"')
    head, sep, tail = text.rpartition(",")
    if sep and tail.strip().lstrip("-").isdigit():
        return (os.path.expandvars(head.strip().strip('"')), int(tail.strip()))
    return (os.path.expandvars(text), 0)


def exe_in_dir(directory: str, depth: int = 2) -> str:
    """在一个安装目录里找主程序 exe。

    卸载注册表给的 InstallLocation 常常是**目录**而不是 exe —— 直接拿它
    去提图标必然失败。所以得在目录里找一个最像主程序的 exe。

    挑选顺序：
        1. 跟目录同名的（``D:\\7-ZIP\\7-ZIP.exe`` 这类）
        2. 不是卸载器/更新器的最大的那个
        3. 排除卸载器后最大的

    第 2、3 条里**必须排除卸载器**：安装目录里体积最大的 exe 经常就是
    ``Uninstaller.exe``（本机 AntiCheatExpert 目录里 3 个 exe，
    最大的就是 ``Uninstaller.exe``）。选它等于把用户带到卸载程序前面。

    深度默认 2 层：安装目录里常见 ``App\\app.exe`` 或 ``App\\bin\\app.exe``，
    再深就容易抓到帮助程序了。
    """
    base = Path(directory)
    if not base.is_dir():
        return ""
    prefix = str(base)
    hits: list[str] = []
    try:
        for root, dirs, files in os.walk(base):
            if root[len(prefix) :].count(os.sep) >= depth:
                dirs[:] = []
                continue
            for name in files:
                if name.lower().endswith(".exe"):
                    hits.append(str(Path(root) / name))
    except OSError:
        return ""
    if not hits:
        return ""
    same = [h for h in hits if Path(h).stem.lower() == base.name.lower()]
    if same:
        return same[0]

    def _size(path: str) -> int:
        try:
            return os.path.getsize(path)
        except OSError:
            return 0

    real = [h for h in hits if not _is_uninstaller(h)]
    pool = real or hits
    try:
        return max(pool, key=_size)
    except (OSError, ValueError):
        return pool[0]


# 这些名字的 exe 一律不作为主程序 —— 点了它们等于打开了卸载/更新流程
_UNINSTALLER_HINTS = (
    "uninstall",
    "uninst",
    "uninstaller",
    "setup",
    "installer",
    "update",
    "updater",
    "repair",
    "remove",
    "modifier",
)


def _is_uninstaller(path: str) -> bool:
    """判断一个 exe 是不是卸载器/安装器/更新器。"""
    stem = Path(path).stem.lower()
    return any(hint in stem for hint in _UNINSTALLER_HINTS)


def resolve_target(path: str) -> str:
    """把一个"可能是程序"的路径解析成**真实存在**的那个。

    Windows 上同一个程序常有好几个变体名，注册表里记的未必是实际在用的：
    Android Studio 的 DisplayIcon 写 ``studio.exe``，磁盘上却是
    ``studio64.exe``。所以找不到时要试变体。

    返回空串表示这条线索指向的目标已经不在了（游戏卸载了、程序删了）。
    """
    text = str(path or "").strip()
    if not text:
        return ""
    p = Path(text)
    if p.exists():
        return str(p)
    stem = p.stem
    suffixes = ("64", "32", "x64", "x86")
    for suffix in suffixes:
        for variant in (f"{stem}{suffix}", f"{stem}-{suffix}", f"{stem}_{suffix}"):
            for ext in (".exe", ".bat", ".cmd", ".ico", ".lnk"):
                cand = p.with_name(variant + ext)
                try:
                    if cand.exists():
                        return str(cand)
                except OSError:
                    continue
    return ""


def candidates(item: dict[str, Any]) -> list[tuple[str, str, int]]:
    """给一个应用条目列出图标候选：(途径, 路径, 索引)。

    顺序即优先级 —— 命中第一个就用它。
    """
    out: list[tuple[str, str, int]] = []
    seen: set[tuple[str, str, int]] = set()

    def add(kind: str, path: str, index: int = 0) -> None:
        if not path:
            return
        key = (kind, path.lower(), index)
        if key in seen:
            return
        seen.add(key)
        out.append((kind, path, index))

    # 1) 注册表的 DisplayIcon —— 最权威的图标声明（Steam 游戏甚至直接给 .ico）
    spec = str(item.get("display_icon") or "").strip()
    if spec:
        path, index = split_spec(spec)
        if path:
            real = resolve_target(path)
            if real:
                add("ex", real, index)
                add("assoc", real)
            else:
                # 目标没了（游戏卸载了）—— 图标还是留着，图标本身可能还在磁盘上
                add("ex", path, index)
                add("assoc", path)

    # 2) 条目自己的路径 / 目标
    for key in ("path", "target"):
        raw = item.get(key)
        if not raw:
            continue
        path = str(raw)
        if path.lower().endswith(".lnk"):
            # 快捷方式：assoc 能绕过"目标已被卸载"的情况
            add("assoc", path)
            add("sh", path)
            continue
        try:
            is_dir = Path(path).is_dir()
        except OSError:
            is_dir = False
        if is_dir:
            exe = exe_in_dir(path)
            if exe:
                add("assoc", exe)
                add("ex", exe)
            add("sh", path)
        else:
            real = resolve_target(path)
            add("assoc", real or path)
            add("ex", real or path)
    return out


def launch_target(item: dict[str, Any]) -> str:
    """这个应用**实际能启动的东西**是什么。空串 = 装不了。

    应用库里的每个条目都得能打开 —— 点了没反应比不显示更糟。所以先用
    这条判据把"只是注册表里还留着一行记录"的组件包筛掉。
    """
    # 快捷方式最直接：双击它总是有效
    for key in ("path", "target"):
        raw = item.get(key)
        if raw and str(raw).lower().endswith(".lnk") and Path(str(raw)).exists():
            return str(raw)
    # 注册表 DisplayIcon 指向的程序
    spec = str(item.get("display_icon") or "").strip()
    if spec:
        path, _ = split_spec(spec)
        real = resolve_target(path) if path else ""
        if real:
            return real
    # 安装目录里挑主程序
    for key in ("path", "install_location"):
        raw = item.get(key)
        if raw and Path(str(raw)).is_dir():
            exe = exe_in_dir(str(raw))
            if exe:
                return exe
    return ""


# ---------------------------------------------------------------- 批量提取
_PS_BATCH = r"""
$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class OryxIcon {
  [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
  public static extern uint ExtractIconEx(string f, int idx, IntPtr[] big, IntPtr[] small, uint n);
  [DllImport("user32.dll", SetLastError = true)]
  public static extern bool DestroyIcon(IntPtr h);
  [StructLayout(LayoutKind.Sequential)]
  public struct SHFILEINFO {
    public IntPtr hIcon; public int iIcon; public uint dwAttributes;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)] public string szDisplayName;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 80)] public string szTypeName;
  }
  [DllImport("shell32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
  public static extern IntPtr SHGetFileInfo(string p, uint a, out SHFILEINFO fi, uint cb, uint flags);
}
"@
function SaveH([IntPtr]$h) {
  if ($h -eq [IntPtr]::Zero) { return $null }
  try {
    $ico = [System.Drawing.Icon]::FromHandle($h)
    $bmp = $ico.ToBitmap(); $ms = New-Object System.IO.MemoryStream
    $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
    $b64 = [Convert]::ToBase64String($ms.ToArray())
    [OryxIcon]::DestroyIcon($h) | Out-Null
    return $b64
  } catch { return $null }
}
function TryEx($p, $idx) {
  try {
    $big = New-Object IntPtr[] 1; $small = New-Object IntPtr[] 1
    [OryxIcon]::ExtractIconEx([string]$p, [int]$idx, $big, $small, 1) | Out-Null
    if ($big[0] -ne [IntPtr]::Zero) { $b = SaveH $big[0]; if ($b) { return $b } }
    if ($small[0] -ne [IntPtr]::Zero) { $b = SaveH $small[0]; if ($b) { return $b } }
  } catch {}
  return $null
}
function TryAssoc($p) {
  try {
    $ico = [System.Drawing.Icon]::ExtractAssociatedIcon([string]$p)
    if ($ico) {
      $bmp = $ico.ToBitmap(); $ms = New-Object System.IO.MemoryStream
      $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
      return [Convert]::ToBase64String($ms.ToArray())
    }
  } catch {}
  return $null
}
function TrySh($p) {
  try {
    $fi = New-Object OryxIcon+SHFILEINFO
    [OryxIcon]::SHGetFileInfo([string]$p, 0, [ref]$fi, [Runtime.InteropServices.Marshal]::SizeOf($fi), 0x100) | Out-Null
    return (SaveH $fi.hIcon)
  } catch {}
  return $null
}
$jobs = Get-Content -LiteralPath 'JOBFILE' -Raw -Encoding UTF8 | ConvertFrom-Json
$out = New-Object System.Collections.Generic.List[string]
foreach ($j in $jobs) {
  $b64 = $null; $via = ''
  foreach ($c in $j.cands) {
    $path = [Environment]::ExpandEnvironmentVariables([string]$c.p)
    if (-not (Test-Path -LiteralPath $path)) { continue }
    $idx = 0
    if ($c.PSObject.Properties.Name -contains 'i') { $idx = [int]$c.i }
    if ($c.k -eq 'ex') { $b64 = TryEx $path $idx; if ($b64) { $via='ex'; break } }
    elseif ($c.k -eq 'assoc') { $b64 = TryAssoc $path; if ($b64) { $via='assoc'; break } }
    elseif ($c.k -eq 'sh') { $b64 = TrySh $path; if ($b64) { $via='sh'; break } }
  }
  if ($b64) { $out.Add([string]$j.k + "`t" + $via + "`t" + $b64) }
  else { $out.Add([string]$j.k + "`tFAIL`t") }
}
[System.IO.File]::WriteAllLines('OUTFILE', $out, (New-Object System.Text.UTF8Encoding($false)))
"""


def extract_batch(
    items: list[tuple[str, dict[str, Any]]],
) -> dict[str, bytes]:
    """一次 PowerShell 进程提取一批图标。返回 {键: PNG 字节}。

    ``items`` 是 [(键, 应用条目)]。没有取到的键不会出现在结果里。

    为什么必须批量：单图标起一个 PowerShell 进程要 1.1 秒，300 个应用就是
    5 分半钟，界面根本没法用。批量版 390 个应用 10.5 秒。

    走磁盘缓存：已经取过的图标直接读文件，不重跑 PowerShell。
    缓存键含目标文件的 mtime —— 应用重装了（文件变了）会自动失效重取。
    """
    if not items or not _IS_WINDOWS:
        return {}

    # 先查磁盘缓存：命中的直接用，只把没命中的丢给 PowerShell
    out: dict[str, bytes] = {}
    todo: list[tuple[str, dict[str, Any]]] = []
    for key, item in items:
        cached = disk_icon_get(_icon_cache_key(item))
        if cached is not None:
            out[key] = cached
        else:
            todo.append((key, item))
    if not todo:
        return out

    plan: list[dict[str, Any]] = []
    keys: dict[str, str] = {}
    for key, item in todo:
        cands = candidates(item)
        if not cands:
            continue
        plan.append(
            {
                "k": key,
                "cands": [{"k": kind, "p": path, "i": index} for kind, path, index in cands],
            }
        )
        keys[key] = _icon_cache_key(item)
    if not plan:
        return out

    tmpdir = tempfile.mkdtemp(prefix="oryxicon_")
    job_file = os.path.join(tmpdir, "jobs.json")
    out_file = os.path.join(tmpdir, "out.tsv")
    try:
        with open(job_file, "w", encoding="utf-8") as handle:
            json.dump(plan, handle, ensure_ascii=False)
        script = _PS_BATCH.replace("JOBFILE", job_file.replace("'", "''")).replace(
            "OUTFILE", out_file.replace("'", "''")
        )
        exe = _powershell()
        if not exe:
            return out
        try:
            subprocess.run(
                [exe, "-NoProfile", "-Command", script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=600,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            return out
        if not os.path.exists(out_file):
            return out
        with open(out_file, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                parts = line.rstrip("\r\n").split("\t")
                if len(parts) < 3 or parts[1] == "FAIL":
                    continue
                try:
                    data = base64.b64decode(parts[2])
                except Exception:
                    continue
                if data[:8] == b"\x89PNG\r\n\x1a\n":
                    out[parts[0]] = data
                    ck = keys.get(parts[0])
                    if ck:
                        disk_icon_put(ck, data)
        return out
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _icon_cache_key(item: dict[str, Any]) -> str:
    """图标磁盘缓存的键。

    含目标文件的 mtime 与大小 —— 应用重装（文件变了）会自动失效。
    只用名字做键的话，重装的应用会一直显示旧图标。
    """
    parts = [str(item.get("name") or "")]
    for field in ("display_icon", "path", "target", "install_location"):
        raw = str(item.get(field) or "").strip()
        if not raw:
            continue
        parts.append(raw)
        try:
            st = os.stat(raw)
            parts.append(f"{int(st.st_mtime)}:{st.st_size}")
        except OSError:
            pass
    return "|".join(parts)


# ---------------------------------------------------------------- 单个提取
def _extract_icon_png(target: str) -> bytes | None:
    """从可执行文件抽图标，转成 PNG 字节。失败返回 None。"""
    if not _IS_WINDOWS or not target:
        return None
    # 单图标提取要起一个 PowerShell 进程（约 1.1 秒）。
    #
    # **忙的时候直接放弃，不排队。**
    # 这个函数跑在 FastAPI 的线程池里（默认 40 个槽位）。
    # 200 多个图标请求一起来就把池子占满了 —— 同一时刻点的「打开应用」
    # 只能排队，实测最长等 96 秒。
    #
    # 排队等名额更糟（实测 194 秒）：等待的线程**占着池子里的槽位**，
    # 「打开应用」还是排不进去。图标是锦上添花 —— 取不到就退回首字母色块，
    # 让线程立刻还给池子。
    if not _extract_try_begin():
        return None
    try:
        return _extract_icon_png_unlocked(target)
    finally:
        _extract_end()


def _extract_icon_png_unlocked(target: str) -> bytes | None:
    """真正干活的版本。调用方要自己占名额（_extract_try_begin）。"""
    script = (
        "$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n"
        "Add-Type -AssemblyName System.Drawing\n"
        "$p = '" + str(target).replace("'", "''") + "'\n"
        "try {\n"
        "  $ico = [System.Drawing.Icon]::ExtractAssociatedIcon($p)\n"
        "  if ($ico) {\n"
        "    $bmp = $ico.ToBitmap()\n"
        "    $ms = New-Object System.IO.MemoryStream\n"
        "    $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)\n"
        "    $bytes = $ms.ToArray()\n"
        "    Write-Output ([Convert]::ToBase64String($bytes))\n"
        "  }\n"
        "} catch {}\n"
    )
    out = _run_ps(script, timeout=30)
    payload = ""
    for line in out.splitlines():
        line = line.strip()
        if line and not line.startswith(("SIZE=", "ERR=", "NOICON")):
            payload = line
            break
    if not payload:
        return None
    try:
        data = base64.b64decode(payload)
    except Exception:
        return None
    return data if data[:8] == b"\x89PNG\r\n\x1a\n" else None


def icon_png(target: str) -> bytes | None:
    """取目标路径的图标 PNG，带内存缓存 + 磁盘缓存。"""
    target = str(target or "").strip()
    if not target:
        return None
    try:
        st = os.stat(target)
        key = hashlib.sha1(
            f"{target}|{int(st.st_mtime)}|{st.st_size}".encode()
        ).hexdigest()
    except OSError:
        key = hashlib.sha1(target.encode()).hexdigest()

    with _icon_lock:
        hit = _icon_cache.get(key)
    if hit is not None:
        return hit

    # 磁盘缓存：进程重启后不用再起 PowerShell 进程
    disk_key = f"file|{target}|{key}"
    cached = disk_icon_get(disk_key)
    if cached is not None:
        with _icon_lock:
            if len(_icon_cache) > _ICON_CACHE_MAX:
                _icon_cache.clear()
            _icon_cache[key] = cached
        return cached

    png = _extract_icon_png(target)
    if png:
        with _icon_lock:
            if len(_icon_cache) > _ICON_CACHE_MAX:
                _icon_cache.clear()
            _icon_cache[key] = png
        disk_icon_put(disk_key, png)
    return png


def app_icon_png(item: dict[str, Any]) -> bytes | None:
    """给一个应用条目取图标：按候选链逐个尝试。

    路径可能是三种东西：exe、lnk 快捷方式、安装目录 —— 都要能落到图标上。

    先查**条目级**磁盘缓存（与批量提取同一个键）——
    这样启动时的预热对单图标请求也有效，不用再起 PowerShell 进程。
    """
    item_key = _icon_cache_key(item)
    cached = disk_icon_get(item_key)
    if cached is not None:
        return cached

    for kind, path, index in candidates(item):
        png = None
        if kind == "ex" and index < 0:
            # 负索引是资源 ID，ExtractIconEx 的序号语义对不上，走 assoc
            png = icon_png(path)
        elif kind == "ex":
            png = icon_png(path)
        elif kind == "assoc":
            png = icon_png(path)
        elif kind == "sh":
            png = icon_png(path)
        if png:
            # 写回条目级缓存 —— 之后单图标请求直接命中，不用再起 PowerShell
            disk_icon_put(item_key, png)
            return png
    return None
