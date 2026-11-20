"""把构建/打包过程中的缓存路径钉到 D 盘。

**为什么**
----------
用户明确要求「所有内容放 D 盘」。但构建过程本身会往 C 盘写一堆东西：

    npm install @tauri-apps/cli   → %LOCALAPPDATA%\\npm-cache      （~500MB）
    cargo build                   → %USERPROFILE%\\.cargo\\registry（~850MB）
    pip install                   → %LOCALAPPDATA%\\pip\\Cache      （~560MB）
    各种临时目录                  → %TEMP%

这些路径**不归应用管**（应用运行时已经由 `paths.rs` 钉到 D 盘了），
但它们是「装这个项目」过程中产生的，一样占用户的 C 盘。

**怎么做的**
------------
用户级环境变量已经设好了（PIP_CACHE_DIR / npm_config_cache / CARGO_HOME /
RUSTUP_HOME / XDG_CACHE_HOME），新开的 shell 会自动继承。

但脚本不能只指望那个 —— 从旧 shell 启动、或者别人 unset 了环境变量时，
就又漏回 C 盘。所以这里**显式再设一遍**，并在覆盖不到时给出警告。

用法::

    import d_env
    env = d_env.apply(dict(os.environ))
    subprocess.run(cmd, env=env)
"""
from __future__ import annotations

import os
from pathlib import Path

# D 盘上的缓存根。
#
# 跟 Rust 侧的 `paths.rs` 用同一个根（D:\\OryxData）——
# 两边各钉各的会散成两堆，反而不容易清。
CACHE_ROOT = Path("D:/OryxData/cache")

# 环境变量 → 相对 CACHE_ROOT 的子目录。
#
# npm_config_cache 是 npm 认的（小写）；PIP_CACHE_DIR 是 pip 认的；
# CARGO_HOME / RUSTUP_HOME 是 rustup/cargo 认的；
# XDG_CACHE_HOME 兜住其它照 XDG 规范找缓存的程序。
_ENV_MAP = {
    "PIP_CACHE_DIR": "pip",
    "npm_config_cache": "npm",
    "CARGO_HOME": "cargo",
    "RUSTUP_HOME": "rustup",
    "XDG_CACHE_HOME": "",
    "HF_HOME": "hf",
    "TORCH_HOME": "torch",
    "PLAYWRIGHT_BROWSERS_PATH": "playwright",
}


def d_drive_available() -> bool:
    """D 盘在不在。

    不在就**不能**改这些变量 —— 否则会把缓存指到一个不存在的位置，
    反而让构建失败。
    """
    return Path("D:/").exists()


def apply(env: dict[str, str] | None = None) -> dict[str, str]:
    """把缓存相关的环境变量指到 D 盘。返回传进来的那个 env（就地改）。"""
    env = os.environ if env is None else env
    if not d_drive_available():
        return env
    for key, sub in _ENV_MAP.items():
        target = CACHE_ROOT / sub if sub else CACHE_ROOT
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        env[key] = str(target)
    return env


def describe() -> str:
    """人话版说明，给构建日志用。"""
    if not d_drive_available():
        return "没有 D 盘 —— 缓存会落在系统默认位置（C 盘）"
    lines = ["构建缓存已指向 D 盘："]
    for key, sub in _ENV_MAP.items():
        lines.append(f"  {key} = {CACHE_ROOT / sub if sub else CACHE_ROOT}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(describe())
