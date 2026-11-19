"""图库：收集「值得看的图片」，把图标和杂物挡在外面。

用户对图库的抱怨是"太多标、还有很多杂乱的图片"。这两类东西的来源很清楚：

    图标    程序目录里的 .ico / .png 图标资源、命名带 icon/logo/avatar 的
            小图。它们占了大头（扫一遍整盘能找出几百个），但对用户毫无意义。
    杂物    缩略图缓存、临时文件、构建产物、安装包目录里的图。

所以这里不是"扫到图片就收"，而是**先判断值不值得收**。

三个来源，按可信度排：

    chat   对话里产出/引用过的图片（截图、生成的图表、用户选的文件）。
           这是用户真正关心的"我聊过的图"。
    common 常用图片目录（图片/图片库/截图/下载/桌面）。
    recent 最近改过的图片（兜底，覆盖放在别处的图）。

对外只暴露 :func:`collect`，它给每张图带上 ``source`` 字段，
前端据此分区展示。
"""

from __future__ import annotations

import mimetypes
import os
import threading
import time
from pathlib import Path
from typing import Any

# 支持的图片格式
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}

# 明确不收的目录名（大小写不敏感）
SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv", "dist", "build",
    ".next", ".nuxt", ".cache", "$recycle.bin", "system volume information",
    "windows", "program files", "program files (x86)", "programdata",
    "temp", "tmp", "site-packages", ".gradle", ".m2", ".cargo",
    "target", "vendor", "assets", "static", "fonts", "icons", "resources",
    "resource", "locales", "themes", "plugins", "browserdata", "gpu",
    "service worker", "webapps", "packages",
}

# AppData 下要**单独对待**的目录。
#
# 为什么不能整块跳过 AppData：它里面确实有用户看过的图（微信收到的图、
# 某些软件的图片缓存）。但更多是缓存与图标（IconCache、Code Cache…）。
# 所以 AppData 不整块跳过，而是靠下面这张"只收这些子目录"的白名单。
#
# 路径是**相对 AppData** 的（实测布局）：
#   Roaming/Tencent/xwechat/…      微信 4.x 的图片
#
# 注意 OneDrive **不在**白名单里：AppData/Local/Microsoft/OneDrive 装的是
# LogoImages、QuotaError 这类 UI 素材（实测 15 张全是），用户的 OneDrive
# 图片在 C:\Users\<name>\OneDrive 下，走用户目录那条路收。
APPDATA_ALLOW = {
    "roaming/tencent/xwechat",
    "roaming/tencent/wechat",
    "roaming/tencent/wework",
    "roaming/wechat",
    "roaming/weixin",
    "roaming/wework",
}

# AppData 白名单子树额外给的深度。AppData 下面套得深：
# Local/Microsoft/OneDrive/版本号/logo.png 就是 5 层。
APPDATA_EXTRA_DEPTH = 5

# 文件名里出现这些词 → 大概率是图标，不收
ICON_NAME_HINTS = (
    "icon", "logo", "favicon", "avatar", "badge", "sprite", "placeholder",
    "loading", "spinner", "blank", "default", "shim", "glyph", "symbol",
    "tray", "arrow", "chevron", "cursor", "caret", "bullet", "divider",
)

# 文件名里出现这些词 → 大概率是杂物，不收
JUNK_NAME_HINTS = (
    "thumbs.db", "desktop.ini", ".crdownload", ".part", ".tmp", ".cache",
    "ehthumbs", "folder", "foldercolor",
)

# 常见图片目录名（相对用户目录）
COMMON_DIR_NAMES = (
    "Pictures", "图片", "图片库", "图像", "Photos", "Screenshots", "截图",
    "屏幕截图", "Camera Roll", "相机", "相机胶卷", "Downloads", "下载",
    "Desktop", "桌面", "Documents", "文档", "OneDrive", "OneDrive - Personal",
)

# 单张图片小于这个字节数 → 图标或占位图
MIN_BYTES = 8 * 1024
# 长边小于这个像素 → 图标
MIN_EDGE = 96


def _is_hidden_or_system(path: Path) -> bool:
    """隐藏文件与系统文件不收。"""
    name = path.name.lower()
    if name in JUNK_NAME_HINTS:
        return True
    try:
        attrs = path.stat().st_file_attributes  # type: ignore[attr-defined]
    except (OSError, AttributeError):
        attrs = 0
    if attrs & 0x2:  # FILE_ATTRIBUTE_HIDDEN
        return True
    if attrs & 0x4:  # FILE_ATTRIBUTE_SYSTEM
        return True
    return False


def _name_says_icon(name: str) -> bool:
    """按文件名判断是不是图标。"""
    low = name.lower()
    stem = Path(low).stem
    # 文件名就是 icon / logo 这种单词
    if stem in ("icon", "logo", "favicon", "avatar", "blank", "default"):
        return True
    return any(hint in low for hint in ICON_NAME_HINTS)


def _edge_size(path: Path) -> tuple[int, int]:
    """读图片尺寸。读不出来就返回 (0, 0)。"""
    try:
        from PIL import Image

        with Image.open(path) as img:
            return int(img.width), int(img.height)
    except Exception:
        return (0, 0)




def _should_skip_dir(name: str) -> bool:
    low = name.lower()
    if low in SKIP_DIRS:
        return True
    # 隐藏目录
    return name.startswith(".")


def _appdata_ok(rel_parts: list[str]) -> bool:
    """路径落在 AppData 下时，判断它是不是"值得收"的地方。

    AppData 里的 18000 张图绝大多数是缓存（IconCache、Code Cache、GPUCache…），
    但也有真的（OneDrive 同步的图、微信收到的图）。所以靠白名单而不是整块跳过。

    ``rel_parts`` 是相对授权根的路径分段，第一段是 "AppData"。

    判定方式：**路径前缀命中白名单**。
    关键是不能在中间层就下结论 —— AppData/Local 本身要放行，
    因为 Local/Microsoft/OneDrive 在它下面。等走到 Local/Microsoft 这一层，
    才知道 Microsoft 是不是白名单里的那个 Microsoft。
    """
    low = [p.lower() for p in rel_parts]
    if low and low[0] == "appdata":
        low = low[1:]
    # AppData 本身：放行，让子目录各自判定
    if not low:
        return True
    current = "/".join(low)
    for entry in APPDATA_ALLOW:
        if current == entry:
            return True              # 正好命中
        if entry.startswith(current + "/"):
            return True              # 还没走到那一层，继续往下
        if current.startswith(entry + "/"):
            return True              # 已经命中过，这是在它的子树里
    # 跟所有白名单都岔开了 → 缓存子树
    return False


def _is_appdata_path(rel_parts: list[str]) -> bool:
    return bool(rel_parts) and rel_parts[0].lower() == "appdata"


def _describe(path: Path, mtime: int, size_bytes: int, source: str) -> dict[str, Any]:
    return {
        "name": path.name,
        "path": str(path).replace("\\", "/"),
        "dir": str(path.parent).replace("\\", "/"),
        "ext": path.suffix.lower().lstrip("."),
        "mime": mimetypes.guess_type(path.name)[0] or "",
        "bytes": size_bytes,
        "mtime": mtime,
        "source": source,
    }


def _collect_dir(
    base: Path,
    source: str,
    deadline: float,
    found: dict[str, dict[str, Any]],
    depth: int = 3,
    root_prefix: list[str] | None = None,
    limits: set[str] | None = None,
) -> None:
    """扫一个目录（有限深度），收下够格的图片。

    ``root_prefix`` 是这个目录相对授权根的路径分段，用来判断 AppData 白名单。
    ``limits`` 收集触发了哪些上限（时间 / 深度 / 条目），供调用方自述不完整。
    """
    if not base.is_dir():
        return
    try:
        base_resolved = base.resolve()
    except OSError:
        return
    prefix = list(root_prefix or [])
    hits = limits if limits is not None else set()
    try:
        for dirpath, dirnames, filenames in os.walk(base_resolved):
            if time.monotonic() > deadline:
                hits.add("时间")
                return
            depth_left = depth - dirpath[len(str(base_resolved)) :].count(os.sep)
            rel = prefix + dirpath[len(str(base_resolved)) :].strip("\\/").split(os.sep)
            rel = [p for p in rel if p]
            in_appdata = _is_appdata_path(rel)
            if in_appdata:
                if not _appdata_ok(rel):
                    dirnames[:] = []
                    continue
                # 白名单里的子树多给几层：AppData 下面套得深，
                # OneDrive 的图在 Local/Microsoft/OneDrive/版本号/… 里（5 层+）
                depth_left += APPDATA_EXTRA_DEPTH
            if depth_left <= 0:
                hits.add("深度")
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]
            for fn in filenames:
                if time.monotonic() > deadline:
                    hits.add("时间")
                    return
                p = Path(dirpath) / fn
                if p.suffix.lower() not in IMAGE_SUFFIXES:
                    continue
                key = str(p).lower()
                if key in found:
                    continue
                try:
                    stat = p.stat()
                except OSError:
                    continue
                if _is_hidden_or_system(p):
                    continue
                if stat.st_size < MIN_BYTES:
                    continue
                if _name_says_icon(fn):
                    continue
                found[key] = _describe(p, int(stat.st_mtime), stat.st_size, source)
            if len(found) >= 2000:
                hits.add("条目")
                return
    except OSError:
        return


def collect(
    allowed_roots: list[str] | None = None,
    chat_images: list[str] | None = None,
    limit: int = 400,
    time_budget: float = 4.0,
) -> dict[str, Any]:
    """收集图库内容。

    ``chat_images`` 是对话里产出/引用过的图片路径（绝对路径）。
    ``allowed_roots`` 是授权目录。

    返回 ``{"images": [...], "counts": {"chat": n, "common": n, "recent": n}}``。
    ``counts`` 描述的是**实际返回的这份列表**里的分布，不是扫描时一共遇见了多少 ——
    前端要按这个数画分区标签，报原始扫描数会显示成「最近图片 41447」。
    """
    deadline = time.monotonic() + time_budget
    found: dict[str, dict[str, Any]] = {}
    limits: set[str] = set()

    # ---- 1) 对话里的图片 ----
    chat_count = 0
    for raw in chat_images or []:
        p = Path(str(raw))
        try:
            if not p.exists() or not p.is_file():
                continue
            stat = p.stat()
        except OSError:
            continue
        if p.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        if stat.st_size < MIN_BYTES:
            continue
        key = str(p).lower()
        if key in found:
            continue
        found[key] = _describe(p, int(stat.st_mtime), stat.st_size, "chat")
        chat_count += 1

    # ---- 2) 常用图片目录 ----
    common_count = 0
    for root in allowed_roots or []:
        base = Path(root)
        if not base.is_dir():
            continue
        for name in COMMON_DIR_NAMES:
            d = base / name
            if d.is_dir():
                before = len(found)
                _collect_dir(d, "common", deadline, found, root_prefix=[name], limits=limits)
                common_count += len(found) - before
            if time.monotonic() > deadline:
                limits.add("时间")
                break

    # ---- 3) 最近改过的（兜底）----
    recent_count = 0
    if time.monotonic() < deadline:
        cutoff = time.time() - 90 * 86400  # 90 天内改过的
        for root in allowed_roots or []:
            base = Path(root)
            if not base.is_dir():
                continue
            try:
                for dirpath, dirnames, filenames in os.walk(base):
                    if time.monotonic() > deadline:
                        limits.add("时间")
                        break
                    depth_left = 4 - dirpath[len(str(base)) :].count(os.sep)
                    rel = [p for p in dirpath[len(str(base)) :].strip("\\/").split(os.sep) if p]
                    # AppData 走白名单：里面的缓存图不要，用户看过的图要
                    if _is_appdata_path(rel):
                        if not _appdata_ok(rel):
                            dirnames[:] = []
                            continue
                        depth_left += APPDATA_EXTRA_DEPTH
                    if depth_left <= 0:
                        limits.add("深度")
                        dirnames[:] = []
                        continue
                    dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]
                    for fn in filenames:
                        p = Path(dirpath) / fn
                        if p.suffix.lower() not in IMAGE_SUFFIXES:
                            continue
                        key = str(p).lower()
                        if key in found:
                            continue
                        try:
                            stat = p.stat()
                        except OSError:
                            continue
                        if stat.st_mtime < cutoff or stat.st_size < MIN_BYTES:
                            continue
                        if _is_hidden_or_system(p) or _name_says_icon(fn):
                            continue
                        found[key] = _describe(p, int(stat.st_mtime), stat.st_size, "recent")
                        recent_count += 1
            except OSError:
                continue

    images = sorted(found.values(), key=lambda x: x["mtime"], reverse=True)[:limit]
    # 统计**返回列表里**的分布，不是扫描时遇见的总数
    counts = {"chat": 0, "common": 0, "recent": 0}
    for img in images:
        counts[img.get("source", "recent")] = counts.get(img.get("source", "recent"), 0) + 1
    # 触发了上限就自述不完整 —— 前端要能说「只扫了一部分」，
    # 否则用户会以为这是全部图片。
    limit_hits = [name for name in ("深度", "条目", "时间") if name in limits]
    return {
        "images": images,
        "counts": counts,
        "total": len(found),
        "partial": bool(limit_hits),
        "limit_hits": limit_hits,
    }


# ------------------------------------------------------------------------ 缓存
#
# 扫盘要几秒，而用户可能反复进图库。缓存一份带 TTL 的结果，
# TTL 到了自动重扫 —— 图库内容随时间变化（用户新存了图），不能永久缓存。

CACHE_TTL = 60.0
_cache: dict[str, Any] = {"at": 0.0, "key": None, "data": None}
_cache_lock = threading.Lock()


def invalidate_cache() -> None:
    """作废图库缓存，下次 collect_cached 重新扫盘。

    测试要在冷缓存下比较批量/逐个耗时，UI 在切换工作区后也需要它。
    这是公共入口，不是死代码。
    """
    with _cache_lock:
        _cache["at"] = 0.0
        _cache["key"] = None
        _cache["data"] = None


def collect_cached(
    allowed_roots: list[str] | None = None,
    chat_images: list[str] | None = None,
    limit: int = 400,
    time_budget: float = 4.0,
) -> dict[str, Any]:
    """带 TTL 的 :func:`collect`。

    缓存键包含授权目录与对话图片列表 —— 任一变化就重新扫。
    """
    roots = tuple(sorted(allowed_roots or []))
    chat_key = tuple(sorted(chat_images or []))
    key = (roots, chat_key, limit)

    now = time.monotonic()
    with _cache_lock:
        hit = _cache["data"]
        if hit is not None and _cache["key"] == key and now - _cache["at"] < CACHE_TTL:
            return hit

    data = collect(
        allowed_roots=allowed_roots,
        chat_images=chat_images,
        limit=limit,
        time_budget=time_budget,
    )
    with _cache_lock:
        _cache["at"] = time.monotonic()
        _cache["key"] = key
        _cache["data"] = data
    return data


