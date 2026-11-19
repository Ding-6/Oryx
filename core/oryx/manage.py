"""管理工作区：自定义模型接入、批量安装扩展、切换工作目录。

这一层把"配置"变成"可操作的东西"：
- provider 可以在运行时增删改，不必手改 JSON；
- 插件 / 专家 / 技能 支持从本地目录批量导入；
- 工作目录随时切换，切换后索引、文档、允许路径一起跟着走。
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Config
from .policy import PolicyEngine


# 已安装应用列表的缓存。扫一遍注册表 + 开始菜单要 1.4 秒，
# 而图标接口要给 200 多个应用各要一次 —— 不缓存就是 200 多秒。
# 缓存 60 秒：够挡住同一批图标的并发请求，又不至于太陈旧。
_APPS_CACHE_TTL = 60.0
_apps_cache: dict[str, Any] = {"at": 0.0, "data": None}
_apps_cache_lock = threading.Lock()


def _cached_installed_apps(limit: int) -> dict[str, Any]:
    """带 TTL 的应用列表缓存。"""
    now = time.time()
    with _apps_cache_lock:
        hit = _apps_cache["data"]
        if hit is not None and now - _apps_cache["at"] < _APPS_CACHE_TTL:
            apps = hit["apps"]
            items = apps[:limit]
            return {"count": len(items), "apps": items}
    data = list_installed_apps(limit=1000)
    with _apps_cache_lock:
        _apps_cache["at"] = now
        _apps_cache["data"] = data
    items = data["apps"][:limit]
    return {"count": len(items), "apps": items}


def invalidate_app_cache() -> None:
    """应用列表变了（新增/删除自定义应用）就让缓存失效。"""
    with _apps_cache_lock:
        _apps_cache["at"] = 0.0
        _apps_cache["data"] = None


# ---------------------------------------------------------------- provider


PROVIDER_TYPES = ("anthropic", "openai")


def provider_template(kind: str = "openai", name: str = "") -> dict[str, Any]:
    """新建一个 provider 的默认配置。"""
    if kind == "anthropic":
        return {
            "type": "anthropic",
            "base_url": "https://api.anthropic.com",
            "api_key": "",
            "model": "claude-sonnet-5-5",
        }
    return {
        "type": "openai",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "",
        "model": "deepseek-chat",
    }


def upsert_provider(config: Config, name: str, spec: dict[str, Any]) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise ValueError("provider 名称不能为空")
    kind = spec.get("type") or "openai"
    if kind not in PROVIDER_TYPES:
        raise ValueError(f"type 只能是 {PROVIDER_TYPES} 之一")

    providers = config.data.setdefault("providers", {})
    current = providers.get(name, {})
    merged = {**provider_template(kind, name), **current, **{k: v for k, v in spec.items() if v is not None}}
    merged["type"] = kind
    # api_key 单独处理：走凭据库，不进 config.json 明文
    incoming_key = spec.get("api_key")
    providers[name] = merged
    if isinstance(incoming_key, str) and incoming_key:
        config.set_provider_api_key(name, incoming_key)
    else:
        config.save()
    return {"name": name, **merged}


def delete_provider(config: Config, name: str) -> bool:
    providers = config.data.get("providers") or {}
    if name not in providers:
        return False
    if config.data.get("active_provider") == name:
        raise ValueError("正在使用的 provider 不能删除，先切换到别的")
    # 本地模式下用的是 local_provider —— 那条也在跑，不能删
    if config.mode == "local" and config.data.get("local_provider") == name:
        raise ValueError("本地模式正在用这个 provider，不能删除，先切换到别的")
    del providers[name]
    # local_provider 指向一条没了的配置 = 悬空指针。
    # 清掉它（而不是拦着不让删）—— 否则「登记完再删掉」这种正常操作会被卡住。
    if config.data.get("local_provider") == name:
        config.data["local_provider"] = ""
    # 凭据库里那份也要一起清掉，否则密钥会残留在系统里
    from . import credentials as cred_store

    cred_store.delete_secret(f"provider:{name}")
    config.save()
    return True


def switch_provider(config: Config, name: str) -> dict[str, Any]:
    """切到某个 provider。

    本地模型要同时写进 local_provider，云端模型写进 cloud_provider ——
    切计算模式时按这两个槽位各取一个，两个模式来回切不会乱跳。
    """
    providers = config.data.get("providers") or {}
    if name not in providers:
        raise ValueError(f"没有这个 provider：{name}")
    from . import localmodels

    config.data["active_provider"] = name
    if localmodels.is_local_provider(providers[name]):
        config.data["local_provider"] = name
    else:
        config.data["cloud_provider"] = name
    config.save()
    return {
        "active_provider": name,
        "local_provider": config.data.get("local_provider"),
        "cloud_provider": config.data.get("cloud_provider"),
    }


def pick_provider_for_mode(config: Config, mode: str) -> str:
    """挑一个该模式下该用的 provider 名。

    优先取用户**上次在这一类里选过的那个**（local_provider / cloud_provider
    两个槽位）—— 取"第一个云端 provider"的话，用户切个计算模式就莫名换了模型。

    槽位是空的（第一次用这一类）→ 按地址找同类里能用的。
    """
    from . import localmodels

    providers = config.data.get("providers") or {}
    slot = "local_provider" if mode == "local" else "cloud_provider"
    name = config.data.get(slot) or ""
    if name in providers:
        return name
    want_local = mode == "local"
    return next(
        (n for n, s in providers.items()
         if isinstance(s, dict) and localmodels.is_local_provider(s) == want_local),
        "",
    )


def mask_key(spec: dict[str, Any]) -> dict[str, Any]:
    """给界面看的版本：api_key 打码。

    api_key 现在可能是凭据库标记（{"__credential__": true}），
    这时只报"已存到系统凭据库"，不回显任何内容。
    """
    out = dict(spec)
    raw = out.get("api_key")
    if isinstance(raw, dict):
        out["api_key"] = ""
        out["has_key"] = True
        out["key_storage"] = "system"
        return out
    key = str(raw or "")
    if key:
        out["api_key"] = f"{key[:6]}…{key[-4:]}" if len(key) > 12 else "…"
        out["has_key"] = True
        out["key_storage"] = "plaintext"
    else:
        out["api_key"] = ""
        out["has_key"] = False
        out["key_storage"] = "none"
    return out


# 能力矩阵的默认值。参考 WorkBuddy 的模型目录：上下文 / 工具 / 视觉 / 计费四个维度。
CAPABILITY_DEFAULTS: dict[str, dict[str, Any]] = {
    "anthropic": {
        "context_window": 200_000,
        "max_output": 8_192,
        "supports_tools": True,
        "supports_vision": True,
        "cost_hint": "按 token 计费",
    },
    "openai": {
        "context_window": 128_000,
        "max_output": 8_192,
        "supports_tools": True,
        "supports_vision": False,
        "cost_hint": "取决于服务商",
    },
}


def provider_capabilities(spec: dict[str, Any]) -> dict[str, Any]:
    """读 provider 的能力声明，缺的用类型默认值补齐。"""
    kind = (spec.get("type") or "openai").lower()
    base = dict(CAPABILITY_DEFAULTS.get(kind, CAPABILITY_DEFAULTS["openai"]))
    for key in ("context_window", "max_output", "supports_tools", "supports_vision", "cost_hint"):
        if key in spec and spec[key] is not None:
            base[key] = spec[key]
    return base


# ---------------------------------------------------------------- 批量安装


@dataclass
class InstallResult:
    installed: list[str]
    skipped: list[str]
    errors: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "installed": self.installed,
            "skipped": self.skipped,
            "errors": self.errors,
            "count": len(self.installed),
        }


def install_extensions(
    source: str,
    project_root: Path,
    kinds: tuple[str, ...] = ("plugin", "expert", "skill"),
) -> InstallResult:
    """从本地目录批量导入扩展。

    目录里每一项应该长这样：
        <名字>/            plugin  →  含 .claude-plugin/plugin.json 或 .codebuddy-plugin/plugin.json
                         expert  →  含 EXPERT.md
                         skill   →  含 SKILL.md
    """
    result = InstallResult([], [], [])
    src = Path(source).expanduser()
    if not src.exists():
        result.errors.append(f"源目录不存在：{src}")
        return result

    targets = {
        "plugin": project_root / "plugins",
        "expert": project_root / "experts",
        "skill": project_root / "skills",
    }
    for path in targets.values():
        path.mkdir(parents=True, exist_ok=True)

    for item in sorted(src.iterdir()):
        if not item.is_dir() or item.name.startswith("."):
            continue
        kind = detect_kind(item)
        if kind is None or kind not in kinds:
            result.skipped.append(f"{item.name}：不是可识别的 {kinds} 格式")
            continue
        destination = targets[kind] / item.name
        try:
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(item, destination)
            result.installed.append(f"{kind}:{item.name}")
        except Exception as exc:
            result.errors.append(f"{item.name}：{exc}")
    return result


def detect_kind(directory: Path) -> str | None:
    if (directory / "EXPERT.md").exists():
        return "expert"
    if (directory / "SKILL.md").exists():
        return "skill"
    for manifest_dir in (".claude-plugin", ".codebuddy-plugin", ".workbuddy-plugin"):
        if (directory / manifest_dir / "plugin.json").exists():
            return "plugin"
    if (directory / "plugin.json").exists():
        return "plugin"
    return None


# ---------------------------------------------------------------- 工作目录


def switch_workspace(config: Config, path: str) -> dict[str, Any]:
    """切换工作目录：项目文档、项目索引、允许路径都跟着走。"""
    target = Path(path).expanduser()
    if not target.exists():
        raise ValueError(f"目录不存在：{target}")
    if not target.is_dir():
        raise ValueError(f"不是目录：{target}")

    config.data["workspace"] = str(target.resolve())
    # 允许路径改为新工作目录（保留用户原有的其他允许项）
    roots = [str(target.resolve())]
    for extra in config.data.get("allowed_roots") or []:
        if str(extra) != str(target):
            roots.append(str(extra))
    config.data["allowed_roots"] = roots
    config.save()
    return {"workspace": config.data["workspace"], "allowed_roots": roots}


def workspace_summary(config: Config) -> dict[str, Any]:
    return {
        "workspace": config.data.get("workspace") or str(Path.cwd()),
        "allowed_roots": config.allowed_roots,
        "forbidden_paths": config.forbidden_paths,
    }


# ---------------------------------------------------------------- 本机应用库


def list_installed_apps(limit: int = 300) -> dict[str, Any]:
    """列出本机已安装的程序。来源：桌面快捷方式 + 卸载注册表 + 开始菜单。

    桌面排在最前 —— 用户自己放在桌面上的才是"我常用的"。

    过滤掉**装不了的东西**：VC++ 运行时、Windows SDK、IntelliSense 这类
    只是注册表里留了一行卸载记录的组件包，占了本机 390 个条目的三分之一。
    判据是能不能解析出真实的启动目标（见 appicon.launch_target）——
    点了打不开的应用比不显示更糟。
    """
    import os
    import winreg  # type: ignore

    from .appicon import launch_target, resolve_shortcuts

    apps: dict[str, dict[str, Any]] = {}

    def _add(name: str, version: str, publisher: str, source: str, path: str = "") -> None:
        name = (name or "").strip()
        if not name or name.lower() in ("windows installer", "update", "uninstall"):
            return
        key = name.lower()
        if key not in apps:
            apps[key] = {
                "name": name,
                "version": version or "",
                "publisher": publisher or "",
                "source": source,
                "path": path,
            }

    # 1) 桌面快捷方式 —— 用户主动放的，优先级最高
    desktop_dirs = []
    user_home = os.environ.get("USERPROFILE")
    if user_home:
        desktop_dirs.append(Path(user_home) / "Desktop")
        desktop_dirs.append(Path(user_home) / "OneDrive" / "Desktop")
    desktop_dirs.append(Path("C:/Users/Public/Desktop"))
    desktop_links: list[str] = []
    for d in desktop_dirs:
        try:
            if d.is_dir():
                desktop_links.extend(str(p) for p in d.glob("*.lnk"))
        except OSError:
            continue
    targets = resolve_shortcuts(desktop_links)
    for link in desktop_links:
        _add(Path(link).stem, "", "", "desktop", link)
    for link, target in targets.items():
        # 快捷方式指向的目标才是真正能启动的东西。
        # 名字可能被 _add 过滤掉（update/uninstall 之类），所以要判存在
        key = Path(link).stem.lower()
        if key in apps:
            apps[key]["target"] = target

    # 2) 卸载信息（版本和发布者最准）
    for hive, prefix in (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ):
        try:
            with winreg.OpenKey(hive, prefix) as root:
                count = winreg.QueryInfoKey(root)[0]
                for index in range(count):
                    try:
                        sub = winreg.EnumKey(root, index)
                        with winreg.OpenKey(root, sub) as item:
                            def _val(name: str) -> str:
                                try:
                                    return str(winreg.QueryValueEx(item, name)[0])
                                except OSError:
                                    return ""
                            display_name = _val("DisplayName")
                            _add(
                                display_name,
                                _val("DisplayVersion"),
                                _val("Publisher"),
                                "registry",
                                _val("InstallLocation"),
                            )
                            # DisplayIcon 是最权威的图标声明（Steam 游戏直接给 .ico）
                            key = (display_name or "").strip().lower()
                            if key and key in apps and not apps[key].get("display_icon"):
                                apps[key]["display_icon"] = _val("DisplayIcon")
                    except OSError:
                        continue
        except OSError:
            continue

    # 3) 开始菜单（覆盖没写卸载信息的绿色软件）
    for env in ("APPDATA", "PROGRAMDATA"):
        base = os.environ.get(env)
        if not base:
            continue
        for folder in ("Microsoft\\Windows\\Start Menu\\Programs",):
            root = Path(base) / folder
            if not root.exists():
                continue
            for shortcut in root.rglob("*.lnk"):
                _add(shortcut.stem, "", "", "startmenu", str(shortcut))

    # 自定义应用排在最后，但标记出来 —— 前端要能单独渲染它们
    for item in load_custom_apps():
        key = item["name"].lower()
        if key not in apps:
            apps[key] = {
                "name": item["name"],
                "version": "",
                "publisher": "",
                "source": "custom",
                "path": item.get("target", ""),
                "target": item.get("target", ""),
                "note": item.get("note", ""),
                "custom_id": item["id"],
            }

    # 4) 筛掉装不了的组件包。桌面/开始菜单/自定义的一律保留 ——
    #    它们都有真实的启动目标，用户自己放的更不该被过滤。
    visible = []
    for item in apps.values():
        if item["source"] in ("desktop", "startmenu", "custom"):
            visible.append(item)
            continue
        if launch_target(item):
            visible.append(item)

    items = sorted(visible, key=lambda a: a["name"].lower())[:limit]
    return {"count": len(items), "apps": items}


# ---------------------------------------------------------------- 自定义应用

CUSTOM_APPS_FILE = "apps.json"


def custom_apps_path() -> Path:
    """自定义应用的存储位置。

    用 JSON 文件而不是 SQLite 表：零 schema 迁移、人类可读可手改
    （features.yaml 是同样的思路）。
    """
    from .config import app_dir

    return app_dir() / CUSTOM_APPS_FILE


def load_custom_apps() -> list[dict[str, Any]]:
    """读用户自己添加的应用。文件不存在或坏了都当空列表。"""
    path = custom_apps_path()
    try:
        raw = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        target = str(item.get("target") or "").strip()
        if not name or not target:
            continue
        out.append(
            {
                "id": str(item.get("id") or "").strip() or _custom_id(name),
                "name": name,
                "target": target,
                "note": str(item.get("note") or ""),
                "created_at": float(item.get("created_at") or 0),
            }
        )
    return out


def _custom_id(name: str) -> str:
    import re
    import time

    text = re.sub(r"[^\w\-一-鿿]+", "-", name, flags=re.UNICODE).strip("-").lower()
    return text[:48] or f"app-{int(time.time() * 1000) % 1000000}"


def save_custom_apps(items: list[dict[str, Any]]) -> None:
    path = custom_apps_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(items, ensure_ascii=False, indent=2), "utf-8"
    )


def save_custom_app(item: dict[str, Any]) -> dict[str, Any]:
    """新增或更新一个自定义应用。id 为空就按名字生成。"""
    name = str(item.get("name") or "").strip()
    target = str(item.get("target") or "").strip()
    if not name:
        raise ValueError("应用名不能为空")
    if not target:
        raise ValueError("目标路径不能为空")
    if not Path(target).exists():
        raise ValueError(f"目标不存在：{target}")

    items = load_custom_apps()
    app_id = str(item.get("id") or "").strip() or _custom_id(name)
    record = {
        "id": app_id,
        "name": name,
        "target": target,
        "note": str(item.get("note") or ""),
        "created_at": float(item.get("created_at") or time.time()),
    }
    replaced = False
    for i, old in enumerate(items):
        if old["id"] == app_id:
            record["created_at"] = old.get("created_at") or record["created_at"]
            items[i] = record
            replaced = True
            break
    if not replaced:
        items.append(record)
    save_custom_apps(items)
    invalidate_app_cache()
    return record


def delete_custom_app(app_id: str) -> bool:
    items = load_custom_apps()
    kept = [x for x in items if x["id"] != app_id]
    if len(kept) == len(items):
        return False
    save_custom_apps(kept)
    invalidate_app_cache()
    return True


def launch_app(app_id: str) -> dict[str, Any]:
    """按 id 启动一个应用。

    **只接受 id，不接受任意路径** —— 接受路径的话，这个端点就退化成了
    "运行任意程序"的通用原语，任何能打这个接口的人都能在自己机器上
    跑任何 exe。id 必须能在已登记的应用里查到。

    这是用户手势（等价于双击桌面图标），不进 Agent 工具白名单，
    因此不占 L 级 —— 它不是模型能触发的动作。
    """
    import os
    import subprocess

    from .appicon import launch_target

    target = ""
    name = ""
    for item in load_custom_apps():
        if item["id"] == app_id:
            target = item["target"]
            name = item["name"]
            break
    if not target:
        # 也允许启动已安装应用（按名字匹配）
        for item in _cached_installed_apps(1000)["apps"]:
            if item.get("name", "").lower() == str(app_id).lower():
                name = item.get("name", "")
                # 走 launch_target 解析真实的主程序 —— 注册表记的
                # InstallLocation 可能只是目录，甚至指向已卸载的旧版本
                target = launch_target(item) or item.get("target") or item.get("path") or ""
                break
    if not target:
        raise ValueError(f"没有这个应用：{app_id}")

    path = Path(target)
    if not path.exists():
        raise ValueError(f"目标不存在：{target}")

    try:
        if os.name == "nt":
            os.startfile(str(path))  # noqa: S606 — 用户手势，等价双击
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError as exc:
        raise ValueError(f"启动失败：{exc}") from None
    return {"ok": True, "launched": name or str(path), "target": str(path)}
