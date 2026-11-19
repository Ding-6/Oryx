"""扩展与技能带来的额外工具。

（从 `loop.py` 原样搬来 —— `self.` 一个字都没改，语义零变动。）
"""

from __future__ import annotations

from pathlib import Path


class PluginSkillsToolsMixin:
    def _register_plugin_skills(self) -> None:
        """把插件自带的技能挂进注册表。

        必须在**加载工具扩展之前**做 ——
        技能也能声明工具（`oryx.json` 里的 `tools`），
        插件技能晚一步注册，那部分工具就永远加载不到。

        信任级别：插件来自**仓库目录**（plugins/ 在仓库里），
        所以按 PROJECT 对待 —— clone 别人的仓库不该等于
        授权它的插件在你机器上执行代码。
        """
        from ..plugins import discover
        from ..registry import Trust

        self.plugin_errors: list[str] = []
        # 仓库根 —— 本文件比 `loop.py` 多了一层目录，
        # `parents` 索引要跟着加一（否则会指到 `core/` 而非仓库根）。
        self.plugins = discover(Path(__file__).resolve().parents[3] / "plugins")

        # 插件技能**要进注册表**，不能只挂进 SkillLibrary。
        #
        # 以前只 `self.skills.add_root(...)` ——
        # 于是插件技能在注册表里是隐形的：它的 oryx.json 里的触发词、
        # 契约、`requires` 依赖全都不生效，依赖解析也看不见它。
        added = False
        for plugin in self.plugins:
            if plugin.skills_dir:
                self.extension_registry.add_root(
                    "skill", plugin.skills_dir, Trust.PROJECT)
                added = True
        if added:
            # 挂了新根 → 重新发现，让依赖解析把插件技能一起算进去
            self.extension_registry.discover()
            # 技能库也要重扫 —— 目录由注册表说了算，得重新对齐一次。
            self.skills.reload()
