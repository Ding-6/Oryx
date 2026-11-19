"""features 模块：开关命名规则、profile 合并、关闭语义。"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from oryx.features import (
    DEFAULT_FEATURES,
    FeatureFlags,
    PROFILES,
    feature_catalog,
)


class FeatureFlagSemantics(unittest.TestCase):
    def test_enable_default(self):
        f = FeatureFlags(flags=dict(DEFAULT_FEATURES))
        self.assertTrue(f.is_enabled("EnableOffice"))
        self.assertTrue(f.is_enabled("EnableReaMcp") is False)  # 默认关
        self.assertFalse(f.is_enabled("DisableA2A"))  # 真 = 关
        self.assertTrue(f.is_enabled("DisableSandbox"))  # 假 = 开（沙箱已实现）

    def test_explicit_disable_overrides_enable(self):
        f = FeatureFlags(flags={"EnableOffice": True, "DisableOffice": True})
        # 不互相冲突；Disable* 走 Disable 字段
        self.assertFalse(f.is_enabled("DisableOffice"))

    def test_unknown_key_returns_true(self):
        # 不以 Disable/Enable 开头 → 视为真（让特性不被静默关闭）
        f = FeatureFlags(flags={})
        self.assertTrue(f.is_enabled("CustomThing"))


class ProfileMerge(unittest.TestCase):
    def test_team_profile_overrides_personal(self):
        with TemporaryDirectory() as tmp:
            f = FeatureFlags.load(Path(tmp) / "missing.yaml")
            # personal 默认：EnableReaMcp 关闭；DisableA2A 真值 = A2A 关闭
            self.assertFalse(f.is_enabled("EnableReaMcp"))
            self.assertFalse(f.is_enabled("DisableA2A"))  # 闭 → A2A 关
        # team profile 显式把 DisableA2A 设为 False（=开启 A2A）
        from oryx.features import FeatureFlags as FF2

        flags = dict(DEFAULT_FEATURES)
        flags.update(PROFILES["team"])
        f = FF2(flags=flags)
        self.assertTrue(f.is_enabled("EnableReaMcp"))
        self.assertTrue(f.is_enabled("DisableA2A"))
        # team 把 DisableChannels 翻成 False（=开启渠道）
        self.assertTrue(f.is_enabled("DisableChannels"))


class FeatureCatalogShape(unittest.TestCase):
    def test_catalog_returns_cards(self):
        cards = feature_catalog(FeatureFlags(flags=dict(DEFAULT_FEATURES)))
        self.assertGreater(len(cards), 10)
        for c in cards:
            self.assertIn("key", c)
            self.assertIn("description", c)
            self.assertIn("enabled", c)
            self.assertIsInstance(c["enabled"], bool)
