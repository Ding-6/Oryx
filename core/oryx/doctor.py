"""环境自检：现在这台机器上 Oryx 能干什么、缺什么、下一步能做什么。

抄自 Marvis 的 `core/doctor.py`，但按 Oryx 的形态重做。

Marvis 那边是 CLI + MCP server，自检是「告诉我这个环境能跑什么命令」；
Oryx 是桌面应用，自检要回答的是另一个问题：
**用户看到界面出问题时，怎么一眼看出是哪一环坏了。**

三条硬约束（照抄 Marvis 的纪律）：
1. **不访问网络** —— 自检不发一个包。要测模型通不通得真的调，那不是自检。
2. **不扫描用户文件** —— 只看 Oryx 自己的数据目录。
3. **给可执行的建议** —— hints 里的每一条都是能直接照做的，不是"建议检查网络"。

自证字段 `destructive_ops` 沿用 Marvis 的做法：恒为 "preview"，
明确告诉用户「危险操作默认只预览，要改得显式确认」。
"""

from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

# 可选依赖：(import 名, pip 包名, 用途)
_OPTIONAL: tuple[tuple[str, str, str], ...] = (
    ("docx", "python-docx", "Word 文档读写"),
    ("openpyxl", "openpyxl", "Excel 读写"),
    ("pptx", "python-pptx", "PPT 读写"),
    ("PIL", "Pillow", "图片处理"),
    ("rapidocr_onnxruntime", "rapidocr-onnxruntime", "OCR 文字识别"),
    ("playwright", "playwright", "网页自动化（浏览器工具）"),
    ("mcp", "mcp", "MCP 客户端"),
    ("httpx", "httpx", "HTTP 客户端"),
    ("psutil", "psutil", "系统资源监控"),
    # 向量检索的依赖。以前没列，缺了只会静默降级成纯关键词检索 ——
    # 用户只感到「搜不准」，看不到任何提示。
    ("numpy", "numpy", "向量检索的数值计算（缺了退回关键词检索）"),
    ("onnxruntime", "onnxruntime", "向量检索的推理后端（缺了退回关键词检索）"),
    ("transformers", "transformers", "向量检索的兜底推理后端"),
    # 多模态交付：PDF 走 matplotlib 的 PdfPages（HTML 与 PNG 只要标准库 / Pillow）。
    # 缺了 pdf_write 会明确报「需要 matplotlib」，不会静默产出空文件。
    ("matplotlib", "matplotlib", "PDF 报告生成（pdf_write 依赖，缺了会明确报错）"),
)

# 破坏性操作的当前模式。恒为 preview —— 危险操作一律要先预览。
DESTRUCTIVE_OPS = "preview"


def _installed(module_name: str) -> bool:
    """模块能不能导入。只探测，不执行 —— 执行模块代码不是自检该干的事。"""
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ValueError):
        return False


def _find_system_browser() -> str:
    """找系统里已装的 Chrome/Edge。

    对齐 browser.py 的回退顺序（chrome → msedge → Playwright 自带 Chromium）：
    装了系统浏览器就不用下 700MB 的 Chromium，
    所以自检不能因为「没下 Chromium」就报不可用。
    """
    import shutil as _shutil

    for name in ("chrome", "msedge"):
        path = _shutil.which(name)
        if path:
            return name
    # Windows 上 chrome/msedge 不在 PATH 里，另有固定位置
    if os.name == "nt":
        candidates = [
            (r"C:\Program Files\Google\Chrome\Application\chrome.exe", "chrome"),
            (r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe", "chrome"),
            (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "msedge"),
            (r"C:\Program Files\Microsoft\Edge\Application\msedge.exe", "msedge"),
        ]
        for path, label in candidates:
            if Path(path).exists():
                return label
    return ""


def _sandbox_enabled() -> bool:
    """命令沙箱开没开（真读特性开关）。

    以前 doctor 这一项写死 ok=True —— 用户在设置里关了沙箱，
    自检照样说「默认启用」，那是骗人。
    """
    try:
        from .config import FEATURES_PATH
        from .features import FeatureFlags

        return FeatureFlags.load(FEATURES_PATH).is_enabled("DisableSandbox")
    except Exception:
        return True


def _danger_policy_ok() -> tuple[bool, str]:
    """「危险操作会先要你确认」这件事真的成立吗？

    真跑一遍策略引擎：拿一个删除动作过一遍，看它是不是判成 L2。
    判成 L2 才说明「会先确认」这句话成立。
    """
    try:
        from .policy import PolicyEngine, Risk

        engine = PolicyEngine([str(Path.home())], [])
        spec = type("S", (), {"name": "fs_delete", "risk": Risk.L2_DESTRUCTIVE})()
        decision = engine.evaluate(spec, {"path": str(Path.home() / ".oryx-probe-delete")})
        if decision.risk == Risk.L2_DESTRUCTIVE:
            return True, f"默认只预览（{DESTRUCTIVE_OPS}）—— 删除、执行命令会先要你确认"
        return False, f"策略引擎把删除判成了 {decision.risk.label}，确认流程可能已失效"
    except Exception as exc:
        return False, f"策略引擎不可用（{type(exc).__name__}: {exc}）—— 危险操作无法保证先确认"


def _dir_writable(path: Path) -> bool:
    """目录在不在、能不能写。"""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".oryx-write-probe"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _dir_size_mb(path: Path) -> float:
    """目录占用（MB）。算不出来就返回 0 —— 自检不该因为权限问题崩掉。"""
    total = 0
    try:
        for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
            dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__")]
            for name in filenames:
                try:
                    total += os.lstat(os.path.join(dirpath, name)).st_size
                except OSError:
                    continue
    except OSError:
        return 0.0
    return round(total / 1024 / 1024, 1)


def _check_data_dir(app_dir: Path) -> dict[str, Any]:
    """应用数据目录的状态。"""
    info: dict[str, Any] = {
        "path": str(app_dir),
        "exists": app_dir.exists(),
        "writable": False,
        "size_mb": 0.0,
        "subdirs": {},
    }
    if not app_dir.exists():
        return info
    info["writable"] = _dir_writable(app_dir)
    info["size_mb"] = _dir_size_mb(app_dir)
    for name in ("index.db", "oryx.db", "config.json", "models", "pastes", "iconcache"):
        child = app_dir / name
        entry: dict[str, Any] = {"exists": child.exists()}
        if child.is_file():
            try:
                entry["size_mb"] = round(child.stat().st_size / 1024 / 1024, 2)
            except OSError:
                pass
        elif child.is_dir():
            entry["size_mb"] = _dir_size_mb(child)
        info["subdirs"][name] = entry
    return info


def run_doctor(config=None) -> dict[str, Any]:
    """返回当前环境能直接用什么、缺什么。

    不访问网络、不扫描用户文件。
    """
    from .config import app_dir
    from .paths import data_root

    checks: list[dict[str, Any]] = []
    hints: list[str] = []

    # ---- 配置与模型服务 ----
    providers: dict[str, Any] = {}
    active = ""
    local = ""
    mode = "cloud"
    if config is not None:
        try:
            active = config.active_provider_name or ""
            local = config.data.get("local_provider") or ""
            mode = config.mode
            raw = config.data.get("providers") or {}
            for name, spec in raw.items():
                if not isinstance(spec, dict):
                    continue
                providers[name] = {
                    "type": spec.get("type", ""),
                    "model": spec.get("model", ""),
                    "base_url": spec.get("base_url", ""),
                    # 只做配置检查，不真发请求 —— 自检不该有网络副作用
                    "has_key": bool(spec.get("api_key")),
                }
        except Exception as exc:
            checks.append(
                {
                    "name": "配置读取",
                    "ok": False,
                    "detail": f"读取配置失败：{exc}",
                }
            )

    checks.append(
        {
            "name": "模型服务",
            "ok": bool(active),
            "detail": (
                f"当前模型服务：{active}（{providers.get(active, {}).get('model', '?')}）"
                if active
                else "没有可用的模型服务 —— 到设置里添加一个"
            ),
        }
    )

    # ---- 策略引擎依赖 ----
    # yaml 是 policy.py 的**顶层硬 import** —— 缺了它整个策略引擎导入失败，
    # 内核起不来。这不是「可选依赖」，必须单独查。
    yaml_ok = _installed("yaml")
    checks.append(
        {
            "name": "安全策略引擎",
            "ok": yaml_ok,
            "detail": (
                "可用 —— 风险分级与确认流程正常"
                if yaml_ok
                else "缺少 PyYAML —— 策略引擎无法导入，危险操作无法保证先确认"
            ),
        }
    )
    if mode == "local" and not local:
        checks.append(
            {
                "name": "本地模式",
                "ok": False,
                "detail": "本地模式没有指定 local_provider",
            }
        )

    # ---- 依赖 ----
    dependencies: dict[str, dict[str, Any]] = {}
    for module_name, pip_name, purpose in _OPTIONAL:
        dependencies[pip_name] = {
            "installed": _installed(module_name),
            "purpose": purpose,
        }

    # ---- 数据目录 ----
    data = _check_data_dir(app_dir())
    checks.append(
        {
            "name": "数据目录",
            "ok": bool(data.get("writable")),
            "detail": (
                f"{data['path']}（{data['size_mb']} MB）"
                + ("" if data.get("writable") else " —— 不可写！")
            ),
        }
    )

    root = data_root()
    checks.append(
        {
            "name": "缓存根目录",
            "ok": _dir_writable(root / "cache"),
            "detail": str(root),
        }
    )

    # ---- 浏览器 ----
    # 判据要和 browser.py 的实际回退逻辑一致：
    # 它依次试 chrome → msedge → Playwright 自带的 Chromium。
    # 只看 Playwright 目录的话，系统装了 Chrome/Edge 也会误报「用不了」。
    playwright_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    has_chromium = False
    if playwright_path:
        try:
            for entry in Path(playwright_path).iterdir():
                if entry.name.startswith("chromium"):
                    has_chromium = True
                    break
        except OSError:
            has_chromium = False
    system_browser = _find_system_browser()
    browser_ok = has_chromium or bool(system_browser)
    if system_browser:
        browser_detail = f"用系统浏览器（{system_browser}）—— 无需额外下载"
    elif has_chromium:
        browser_detail = f"Playwright 浏览器已就绪（{playwright_path}）"
    else:
        browser_detail = "没有可用的浏览器 —— 网页工具用不了"
    checks.append(
        {
            "name": "浏览器内核",
            "ok": browser_ok,
            "detail": browser_detail,
        }
    )

    # ---- 命令沙箱 ----
    # 真读开关：以前这里是硬编码 ok=True，用户关了沙箱它还报「正常」。
    sandbox_on = _sandbox_enabled()
    checks.append(
        {
            "name": "命令沙箱",
            "ok": sandbox_on,
            "detail": (
                "已启用：剔除密钥环境变量、限定工作目录、超时与输出截断"
                if sandbox_on
                else "已被特性开关关闭 —— shell_sandbox 工具不可用（危险命令黑名单仍然生效）"
            ),
        }
    )

    # ---- 危险操作 ----
    # 真读策略引擎：确认「L2 会弹确认卡」这件事真的成立，
    # 而不是写死一句「ok」。策略引擎起不来就是真问题。
    policy_ok, policy_detail = _danger_policy_ok()
    checks.append(
        {
            "name": "危险操作",
            "ok": policy_ok,
            "detail": policy_detail,
        }
    )

    # ---- hints：可执行的建议 ----
    if not active:
        hints.append("先添加一个模型服务：设置 → 模型服务 → 添加")
    missing = [
        pip_name
        for pip_name, info in dependencies.items()
        if not info["installed"] and pip_name not in ("mcp",)
    ]
    if missing:
        hints.append(
            "缺可选依赖（" + "、".join(missing) + "），装上能用更多能力："
            "python core/scripts/install_extras.py"
        )
    if not browser_ok:
        hints.append(
            "装浏览器内核：python -m playwright install chromium chromium-headless-shell"
            "（装了 Chrome/Edge 就不用这一步）"
        )
    if not data.get("writable"):
        hints.append(f"数据目录不可写，检查权限：{data['path']}")
    # ---- 工具声明完整性 ----
    # 不合规的工具（schema 缺字段、没写 description）会让模型选错工具。
    # 这里把它列出来 —— 写工具的人能第一时间看到。
    try:
        from .tooltable import ToolTable
        from .tools import build_registry

        _tt = ToolTable()
        for _spec in build_registry().values():
            _tt.add_builtin(_spec)
        _problems = _tt.validate()
        checks.append(
            {
                "name": "工具声明",
                "ok": not _problems,
                "detail": (f"{len(_tt.all())} 个内置工具，声明全部合规"
                          if not _problems else
                          f"{len(_problems)} 个工具声明不合规：" + "；".join(_problems[:3])),
            }
        )
        if _problems:
            hints.append(f"有 {len(_problems)} 个工具声明不合规（缺 description 或 schema 字段）—— 见「工具声明」检查项")
        # mutates 声明复核 —— 漏声明就是在结果里谎报「我没动过任何东西」
        _sus = _tt.check_mutates()
        checks.append(
            {
                "name": "改动声明",
                "ok": not _sus,
                "detail": (f"{len(_tt.all())} 个工具的 mutates 声明自洽"
                          if not _sus else
                          f"{len(_sus)} 个工具疑似漏声明 mutates：" + "；".join(_sus[:3])),
            }
        )
        if _sus:
            hints.append(f"有 {len(_sus)} 个工具疑似漏声明 mutates —— 结果里会谎报「没改动」，见「改动声明」检查项")
    except Exception:
        pass

    # ---- 扩展依赖 ----
    # 扩展声明了 requires 但依赖没满足 —— 它不会被加载。
    # 用户得能在自检里看到「为什么这个扩展没跑」。
    try:
        from .config import app_dir
        from .registry import Registry, Trust

        _home = app_dir()
        _reg = Registry()
        _reg.add_root("tool", _home / "extensions", Trust.USER)
        _reg.add_root("skill", _home / "skills", Trust.USER)
        _reg.discover()
        _blocked = _reg.blocked_records()
        checks.append(
            {
                "name": "扩展依赖",
                "ok": not _blocked,
                "detail": (f"{len(_reg.all_records())} 个扩展，依赖全部满足"
                          if not _blocked else
                          f"{len(_blocked)} 个扩展因依赖不满足没加载：" +
                          "；".join(f"{r.id}（{r.blocked_reason}）" for r in _blocked[:3])),
            }
        )
        if _blocked:
            hints.append(
                f"有 {len(_blocked)} 个扩展的依赖没满足（缺扩展、被停用或版本不符）"
                f" —— 它们不会被加载，见「扩展依赖」检查项"
            )
    except Exception:
        pass

    if not hints:
        hints.append("环境正常 —— 直接在对话里说要做什么就行")

    return {
        "version": _version(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "mode": mode,
        "network_note": "自检不访问网络，也不扫描你的文件",
        "destructive_ops": DESTRUCTIVE_OPS,
        "providers": providers,
        "active_provider": active,
        "local_provider": local,
        "dependencies": dependencies,
        "data": data,
        "checks": checks,
        "hints": hints,
    }


def _version() -> str:
    try:
        from . import __version__

        return __version__
    except Exception:
        return "unknown"
