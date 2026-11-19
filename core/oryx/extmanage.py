"""扩展管理 —— 启用、停用、安装、删除。

为什么要有
----------
扩展装好之后，用户想「先试试」或「这玩意儿有问题先关掉」，
不该去翻目录改 `oryx.json`。这是「可自由支配」的最后一块拼图：
**装、改、开关、卸，都在界面上点**。

三条设计原则
------------
1. **程序不改用户的扩展文件** —— 启停状态存在 Oryx 自己的配置里
   （`disabled_extensions` 黑名单）。扩展文件是用户的东西，
   程序擅自改它，用户下次 git diff 会一脸问号。

2. **安装后默认禁用** —— 「装」和「让它跑」是两个独立决定。
   扩展是在**你的机器上执行代码**的东西，装完就跑太草率。

3. **删除是移进回收区，不是真删** —— 误删了能找回来。
   真删要在回收区里再点一次「彻底删除」。

信任模型
--------
装到用户目录的扩展 = **USER 信任** = 可以执行代码。
这是「安装」这个动作隐含的权限，所以：
  · 安装界面上要写清楚「这会在你的机器上执行代码」
  · 安装后默认禁用，用户显式启用才生效
  · 仓库里自带的扩展（PROJECT 信任）**永远不执行**，不管怎么开关
"""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 安装时允许的扩展清单文件名 —— 有它才认得这是一个扩展
MANIFEST_NAMES = ("oryx.json",)

# 扩展目录里不允许有的东西（防止把整个目录复制进来）
IGNORED_NAMES = {"__pycache__", ".git", ".venv", "node_modules", ".pytest_cache"}

# 回收区在扩展目录下的子目录名
TRASH_DIR = ".trash"

# 回收区的元数据文件名（放在回收区目录**根部**）
#
# 为什么元数据不写进每个扩展目录里：
# 回收区里放的是**用户的扩展文件**。往里面塞我们自己的文件，
# 恢复时又得记得删干净 —— 忘了就等于往用户的扩展里掺了垃圾。
# 放一份在回收区根部，两边都干净：用户的文件一个字节没动，
# 我们的账本单独记着。
TRASH_META_NAME = "_meta.json"

# 默认保留天数 —— 删掉的扩展在回收区里留多久。
#
# 30 天是「足够找回」与「不至于无限堆积」之间的折中
# （Windows 回收站是 30 天、macOS 是 30 天）。
DEFAULT_RETENTION_DAYS = 30


class ExtensionError(RuntimeError):
    """扩展管理出错。

    单独一个异常类型，让调用方能区分「用户操作有问题」（该报 400）
    与「平台出错了」（该报 500）。
    """


@dataclass
class InstallResult:
    """一次安装的结果。"""

    id: str
    path: str
    name: str = ""
    enabled: bool = False      # 装完默认禁用
    copied: list[str] = None   # 复制了哪些文件（相对路径）

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": self.path,
            "name": self.name,
            "enabled": self.enabled,
            "copied": list(self.copied or []),
        }


# ── 启停 ──────────────────────────────────────────────────────────────

def disabled_ids(config_data: dict[str, Any]) -> set[str]:
    """配置里被停用的扩展 id。"""
    raw = config_data.get("disabled_extensions")
    if not isinstance(raw, list):
        return set()
    return {str(x).strip() for x in raw if str(x).strip()}


def set_disabled(config_data: dict[str, Any], ext_id: str, disabled: bool) -> bool:
    """设置某个扩展的停用状态。

    返回是否真的改了（没变化也返回 False，界面上就不用提示）。
    """
    raw = config_data.get("disabled_extensions")
    cur = list(raw) if isinstance(raw, list) else []
    ext_id = str(ext_id).strip()
    if not ext_id:
        return False
    if disabled:
        if ext_id in cur:
            return False
        cur.append(ext_id)
    else:
        if ext_id not in cur:
            return False
        cur = [x for x in cur if str(x) != ext_id]
    config_data["disabled_extensions"] = cur
    return True


# ── 安装 ──────────────────────────────────────────────────────────────

def _find_manifest(root: Path) -> Path | None:
    for name in MANIFEST_NAMES:
        p = root / name
        if p.exists():
            return p
    return None


def read_extension_id(root: Path) -> str:
    """读扩展目录里的 id。"""
    manifest = _find_manifest(root)
    if manifest is None:
        raise ExtensionError(f"这个目录里没有 {MANIFEST_NAMES[0]}，不是扩展")
    try:
        data = json.loads(manifest.read_text("utf-8"))
    except Exception as exc:
        raise ExtensionError(f"清单读不了：{exc}") from None
    if not isinstance(data, dict):
        raise ExtensionError("清单格式不对")
    ext_id = str(data.get("id") or root.name).strip()
    if not ext_id:
        raise ExtensionError("清单里没有 id")
    return ext_id


def install_extension(source: Path, target_root: Path) -> InstallResult:
    """从本地目录安装一个扩展。

    步骤：读 id → 复制到目标目录 → **默认禁用**。

    安全边界：
      · 源目录必须存在且含清单 —— 否则不是扩展
      · 目标 id 不能与已有扩展重名（避免悄悄覆盖）
      · 复制时跳过 `__pycache__` / `.git` 之类
      · **不执行源目录里的任何代码** —— 只是复制文件
    """
    source = Path(source)
    if not source.is_dir():
        raise ExtensionError(f"目录不存在：{source}")
    manifest = _find_manifest(source)
    if manifest is None:
        raise ExtensionError(f"这个目录里没有 {MANIFEST_NAMES[0]}，不是扩展")

    ext_id = read_extension_id(source)
    target = Path(target_root) / ext_id
    if target.exists():
        raise ExtensionError(f"已经有同名的扩展了：{ext_id}（先删掉再装）")

    copied: list[str] = []
    target.mkdir(parents=True, exist_ok=True)
    try:
        for item in sorted(source.iterdir()):
            if item.name in IGNORED_NAMES:
                continue
            dst = target / item.name
            if item.is_dir():
                shutil.copytree(item, dst, dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns(*IGNORED_NAMES))
                copied.append(item.name + "/")
            else:
                shutil.copy2(item, dst)
                copied.append(item.name)
    except Exception as exc:
        # 复制到一半失败 → 收拾干净，别留半个扩展
        shutil.rmtree(target, ignore_errors=True)
        raise ExtensionError(f"复制失败：{exc}") from None

    name = ""
    try:
        data = json.loads(manifest.read_text("utf-8"))
        name = str(data.get("name") or "")
    except Exception:
        pass

    return InstallResult(
        id=ext_id, path=str(target), name=name, enabled=False, copied=copied
    )


# ── 删除（移进回收区）────────────────────────────────────────────────

def trash_dir(target_root: Path) -> Path:
    return Path(target_root) / TRASH_DIR


def _meta_path(target_root: Path) -> Path:
    return trash_dir(target_root) / TRASH_META_NAME


def read_trash_meta(target_root: Path) -> dict[str, Any]:
    """读回收区的账本（删掉的时间、原 id 等）。

    读不出来就返回空 —— 账本坏了不该让回收区整个打不开。
    """
    path = _meta_path(target_root)
    try:
        data = json.loads(path.read_text("utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def write_trash_meta(target_root: Path, meta: dict[str, Any]) -> None:
    """写回收区的账本。"""
    path = _meta_path(target_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")
    except OSError:
        pass  # 账本写不进去不该阻塞删除流程


def move_to_trash(target_root: Path, ext_id: str) -> str:
    """把扩展移进回收区。

    **不是真删** —— 误删了能找回来。返回回收区里的路径。

    同时记一笔账（删除时间、原 id）——
    没有账本就分不清「刚删的」与「三个月前删的」，
    自动清理也就无从判断该不该动手。
    """
    src = Path(target_root) / ext_id
    if not src.exists():
        raise ExtensionError(f"没有这个扩展：{ext_id}")
    trash = trash_dir(target_root)
    trash.mkdir(parents=True, exist_ok=True)
    dst = trash / ext_id
    # 同名冲突时加序号，不覆盖已有的
    n = 1
    while dst.exists():
        dst = trash / f"{ext_id}.{n}"
        n += 1
    shutil.move(str(src), str(dst))

    # 记一笔账。**先移动再记账** —— 移动失败就不该有账，
    # 否则账上会出现一个回收区里根本不存在的条目。
    meta = read_trash_meta(target_root)
    meta[dst.name] = {
        "id": ext_id,                 # 原 id（恢复时用）
        "name": str(ext_id),
        "trashed_at": time.time(),    # 删除时刻（Unix 秒）
        "version": _read_version(dst),
    }
    write_trash_meta(target_root, meta)
    return str(dst)


def _read_version(ext_dir: Path) -> str:
    manifest = _find_manifest(ext_dir)
    if manifest is None:
        return ""
    try:
        data = json.loads(manifest.read_text("utf-8"))
        return str(data.get("version") or "")
    except Exception:
        return ""


def list_trash(target_root: Path) -> list[dict[str, Any]]:
    """回收区里的扩展列表。

    带上删除时间与「还剩几天会被自动清理」——
    用户得能看见自动清理的判据，不能是黑箱。
    """
    trash = trash_dir(target_root)
    if not trash.exists():
        return []
    meta = read_trash_meta(target_root)
    now = time.time()
    out: list[dict[str, Any]] = []
    for child in sorted(trash.iterdir()):
        if not child.is_dir():
            continue
        entry = meta.get(child.name) or {}
        item: dict[str, Any] = {
            "id": child.name,
            "path": str(child),
            # 原 id —— 回收区里的目录名可能带 .1 后缀，
            # 那是「同名又删了一次」的重名处理，不是扩展真名。
            "original_id": str(entry.get("id") or child.name.split(".")[0]),
        }
        trashed_at = float(entry.get("trashed_at") or 0.0)
        if trashed_at:
            age_days = max(0.0, (now - trashed_at) / 86400.0)
            item["trashed_at"] = trashed_at
            item["age_days"] = round(age_days, 2)
        try:
            manifest = _find_manifest(child)
            if manifest:
                data = json.loads(manifest.read_text("utf-8"))
                item["name"] = str(data.get("name") or "")
                item["version"] = str(data.get("version") or "")
        except Exception:
            pass
        out.append(item)
    return out


def trash_stats(target_root: Path, retention_days: int = DEFAULT_RETENTION_DAYS) -> dict[str, Any]:
    """回收区的统计：几个、多大、多久会过期。

    给界面用 —— 用户得知道「再过 12 天这 3 个会自动清掉」。
    """
    items = list_trash(target_root)
    now = time.time()
    total_bytes = 0
    for item in items:
        path = Path(item.get("path") or "")
        if not path.exists():
            continue
        for p in path.rglob("*"):
            try:
                if p.is_file():
                    total_bytes += p.stat().st_size
            except OSError:
                continue
    expiring = []
    if retention_days > 0:
        limit = now - retention_days * 86400.0
        for item in items:
            ts = float(item.get("trashed_at") or 0.0)
            if ts and ts < limit:
                expiring.append(item["id"])
    return {
        "count": len(items),
        "bytes": total_bytes,
        "retention_days": retention_days,
        "expiring": expiring,
    }


def purge_expired(target_root: Path, retention_days: int = DEFAULT_RETENTION_DAYS,
                  *, now: float | None = None) -> list[str]:
    """清掉超过保留期的回收区条目。返回被清掉的 id 列表。

    `retention_days <= 0` 表示**永不自动清理** —— 用户可以手动清。

    为什么要有这个：
    回收区只进不出的话，它就是个**只涨不消的垃圾桶**。
    装几十个扩展试一遍、每个都删掉，回收区就攒下几十份
    Python 代码 —— 而用户根本没意识到那些还在占地方。

    保留期让「误删能找回」与「不会无限堆积」两件事同时成立。
    """
    if retention_days <= 0:
        return []
    moment = now if now is not None else time.time()
    limit = moment - retention_days * 86400.0
    meta = read_trash_meta(target_root)
    purged: list[str] = []
    trash = trash_dir(target_root)
    if not trash.exists():
        return []
    for child in sorted(trash.iterdir()):
        if not child.is_dir():
            continue
        entry = meta.get(child.name) or {}
        ts = float(entry.get("trashed_at") or 0.0)
        if not ts:
            # 没有删除时间的（老版本留下的）—— **不自动清**。
            # 不知道它什么时候删的，就不该替用户决定它该活多久。
            continue
        if ts >= limit:
            continue
        shutil.rmtree(child, ignore_errors=True)
        meta.pop(child.name, None)
        purged.append(child.name)
    if purged:
        write_trash_meta(target_root, meta)
    return purged


def purge_from_trash(target_root: Path, name: str) -> bool:
    """从回收区彻底删除。"""
    target = trash_dir(target_root) / name
    if not target.exists():
        raise ExtensionError(f"回收区里没有：{name}")
    shutil.rmtree(target, ignore_errors=True)
    meta = read_trash_meta(target_root)
    if meta.pop(name, None) is not None:
        write_trash_meta(target_root, meta)
    return True


def restore_from_trash(target_root: Path, name: str) -> str:
    """从回收区恢复。"""
    src = trash_dir(target_root) / name
    if not src.exists():
        raise ExtensionError(f"回收区里没有：{name}")
    meta = read_trash_meta(target_root)
    entry = meta.get(name) or {}
    ext_id = str(entry.get("id") or name.split(".")[0])
    dst = Path(target_root) / ext_id
    if dst.exists():
        raise ExtensionError(f"已经有同名的扩展了：{ext_id}")
    shutil.move(str(src), str(dst))
    meta.pop(name, None)
    write_trash_meta(target_root, meta)
    return str(dst)
