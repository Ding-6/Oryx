"""落盘位置：把缓存与临时目录统一指到数据根目录。

为什么要有这个模块：
桌面端（Rust 侧 `apps/desktop/src-tauri/src/paths.rs`）已经把这些环境变量设好了，
但 Oryx 也支持直接跑 `python -m oryx` —— 开发、脚本、调试都走这条路。
那条路上没人设这些变量，于是：

  · Playwright 的 Chromium 落到 %LOCALAPPDATA%\\ms-playwright（**701MB**）
  · pip 缓存落到 %LOCALAPPDATA%\\pip\\cache（72MB 起）
  · torch / transformers 的权重落到 %USERPROFILE%\\.cache（几百 MB）
  · 临时文件落到 %TEMP%（C 盘）

Rust 侧与 Python 侧维护的是同一份清单 —— 改一边记得改另一边。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# 环境变量清单：键 → 相对数据根目录的子路径。
#
# 键名必须与 Rust 侧 paths.rs 一致（那边叫 safe_set）。
# 注意这里面**故意没有** ORYX_HOME / OYX_DATA_ROOT：
# 那两个决定应用数据目录（配置、索引库、模型），
# 由 config.app_dir() 单独负责。这里动了会让两者对不上 ——
# 实测教训：app_dir() 的 fallback 分支返回的是 %APPDATA%\Oryx（Roaming），
# 而缓存应该在 %LOCALAPPDATA%\Oryx（Local），不是同一个目录。
_ENV_LAYOUT: dict[str, tuple[str, ...]] = {
    # TMPDIR 必须一起设 —— 它在 tempfile 的候选目录里**排在 TEMP/TMP 前面**
    # （见标准库 tempfile._candidate_tempdir_list：先试 TMPDIR，再 TEMP，最后 TMP）。
    # 只设 TEMP/TMP 的话，Python 会先用 TMPDIR 里的旧值，
    # 临时文件照样漏到 C 盘 —— 这个模块存在的意义就没了。
    "TMPDIR": ("temp",),
    "TEMP": ("temp",),
    "TMP": ("temp",),
    "XDG_CACHE_HOME": ("cache",),
    "PIP_CACHE_DIR": ("cache", "pip"),
    "HF_HOME": ("cache", "huggingface"),
    "HUGGINGFACE_HUB_CACHE": ("cache", "huggingface", "hub"),
    "TRANSFORMERS_CACHE": ("cache", "huggingface", "transformers"),
    "TORCH_HOME": ("cache", "torch"),
    "PLAYWRIGHT_BROWSERS_PATH": ("cache", "playwright"),
    "npm_config_cache": ("cache", "npm"),
}


def data_root() -> Path:
    """数据根目录：缓存、临时文件都在它下面。

    找法（与 Rust 侧 paths::data_root 同优先级）：
      1. OYX_DATA_ROOT —— 桌面端启动时设的
      2. ORYX_HOME 的上级 —— 桌面端把它设成 <root>\\data
      3. D:\\OryxData —— D 盘在的话（用户要求所有东西都在 D 盘）
      4. %LOCALAPPDATA%\\Oryx —— 没有 D 盘的机器，保证还能用

    第 2 条要判断名字是不是 "data"：ORYX_HOME 在生产上是 <root>\\data，
    但测试里它是一个独立目录（oryx-test-home），上级不是数据根目录 ——
    弄错的话缓存会落到临时目录的上一级，跟测试隔离目录混在一起。
    """
    root = os.environ.get("OYX_DATA_ROOT")
    if root and root.strip():
        return Path(root)

    home = os.environ.get("ORYX_HOME")
    if home and home.strip():
        p = Path(home)
        return p.parent if p.name.lower() == "data" else p

    if os.name == "nt" and Path("D:/").exists():
        return Path("D:/OryxData")

    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / "Oryx"


def redirect_env(root: Path | None = None) -> dict[str, str]:
    """把缓存与临时目录的环境变量指到数据根目录。

    必须在**读这些变量的库加载之前**调用：
    · tempfile 在第一次调用 gettempdir() 时就把结果缓存了 —— 之后改 TEMP 不生效
    · Playwright 的 driver 子进程在 launch 时才读 PLAYWRIGHT_BROWSERS_PATH

    返回实际设置的环境变量（键 → 值），便于测试与排障。
    """
    root = Path(root) if root is not None else data_root()
    cache = root / "cache"
    temp = root / "temp"

    applied: dict[str, str] = {}
    for key, parts in _ENV_LAYOUT.items():
        target = root.joinpath(*parts) if parts else root
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError:
            # 建不出来就别指过去 —— 指过去的话那个库会往一个不存在的目录写，
            # 报错反而更难查。让它退回默认位置更实在。
            continue
        value = str(target)
        os.environ[key] = value
        applied[key] = value

    # 缓存根目录本身也要有 —— 有些库直接写 XDG_CACHE_HOME 的子目录
    try:
        cache.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    # tempfile 的坑：gettempdir() 在**第一次调用时**就把结果缓存进
    # tempfile.tempdir，之后改 TEMP/TMP 环境变量**不生效**。
    # 所以要手动清掉这个缓存，让下一次 gettempdir() 重新读环境变量。
    # 不清的话 tempfile.mkstemp 还在 C 盘建文件（实测 .port 文件就是这么漏的）。
    tempfile.tempdir = None

    return applied
