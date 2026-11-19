"""桌面壳与源码的一致性守卫。

背景：`tauri.conf.json` 会把 `core/`、`skills/`、`agents/`、`experts/`
拷进 `target/release/`。exe 早期版本从 exe 往上找 `core/`，会先撞上那份
**过期的拷贝**——于是源码改了、双击图标却看不到变化。

现在 `main.rs` 的 `core_dir` 改成「仓库根优先」，但那份拷贝仍然存在，
所以这里加两条测试把意图钉死：
1. 源码目录里必须存在桌面运行所需的关键产物；
2. target/release 下的过期拷贝一旦出现就应该被识别出来（而不是被当成主源）。

**清单是动态的，不是手写的**
--------------------------------
以前这里是手写的模块名清单（`team.py`、`features.py`…）。
问题很实际：**新增的模块不会自动进清单** —— 比如后来加的
`frontmatter.py`、`search.py`、`toolsets/`，清单里都没有，
于是拷贝比源码旧了测试也照样绿。

现在改成**扫描源码目录**：`core/oryx/` 下所有 `.py`，
副本里必须都有。**新增模块自动被覆盖**，不用改测试。
"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CORE = REPO / "core"
DESKTOP = REPO / "apps" / "desktop" / "src-tauri"
RELEASE = DESKTOP / "target" / "release"


def _source_modules() -> list[str]:
    """源码 `core/oryx/` 下所有模块的相对路径（相对 core/）。"""
    out: list[str] = []
    root = CORE / "oryx"
    for p in sorted(root.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        out.append(p.relative_to(CORE).as_posix())
    return out


def test_core_entrypoint_exists():
    assert (CORE / "oryx" / "__main__.py").exists()


def test_repo_root_is_recognizable():
    """main.rs::is_repo_root 的判定条件，Python 侧复刻一份防漂移。"""
    assert (CORE / "oryx" / "__main__.py").exists()
    assert (REPO / "apps" / "desktop").is_dir()


def test_release_core_copy_is_in_sync_or_absent():
    """target/release/core 是 `cargo build` 拷出来的打包副本。

    两条都要守住：
    - 副本不存在 → 干净，桌面壳会回落到仓库源码，没问题；
    - 副本存在 → **源码里的每个模块都得在**，否则说明构建产物比源码旧，
      一旦哪天把 exe 单独拷走（脱离仓库运行）就会跑起旧代码。

    这里**扫描源码目录**而不是比手写清单 —— 新增模块自动被覆盖。
    """
    stale = RELEASE / "core"
    if not stale.exists():
        return  # 没生成过拷贝：桌面壳会用仓库源码

    missing = [rel for rel in _source_modules() if not (stale / rel).exists()]
    assert not missing, (
        f"target/release/core 缺少这些模块（打包副本比源码旧）：{missing[:8]}"
        " —— 在 apps/desktop/src-tauri 下跑一次 `cargo build --release` 重新同步。"
    )

    stale_agents = RELEASE / "agents"
    if stale_agents.exists():
        for name in ("doc-manager", "researcher", "reviewer"):
            assert (stale_agents / name / "AGENT.md").exists(), (
                f"target/release/agents/{name} 缺失，重新 `cargo build --release`"
            )


def test_tauri_resources_include_new_modules():
    """打包配置必须带上新增的模块，否则 NSIS 安装包会缺东西。"""
    conf = json.loads((DESKTOP / "tauri.conf.json").read_text("utf-8"))
    resources = conf["bundle"]["resources"]
    for key in ("../../../core/oryx", "../../../core/web", "../../../skills",
                "../../../experts", "../../../agents"):
        assert key in resources, f"tauri.conf.json 少了 {key}"


def test_seed_agents_exist_in_repo():
    """桌面壳启动时按 cwd 找 skills/agents，必须在仓库里就位。"""
    for name in ("doc-manager", "researcher", "reviewer"):
        assert (REPO / "agents" / name / "AGENT.md").exists(), f"缺 agents/{name}"
