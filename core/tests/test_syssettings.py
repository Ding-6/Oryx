"""system_settings 工具：表层校验 + 实际调用的 mock 测。

不真的去锁屏或改壁纸（会破坏测试环境）；只校验参数校验与包装层。
"""

import unittest
from unittest import mock

from oryx import syssettings


class SysSettingsArgs(unittest.TestCase):
    def test_screen_timeout_bounds(self):
        self.assertFalse(syssettings.set_screen_timeout_seconds(0).get("applied"))
        self.assertFalse(syssettings.set_screen_timeout_seconds(10).get("applied"))
        self.assertFalse(syssettings.set_screen_timeout_seconds(24 * 3600 + 1).get("applied"))

    def test_dpi_invalid_scale(self):
        self.assertFalse(syssettings.set_dpi_scaling(110).get("applied"))
        self.assertFalse(syssettings.set_dpi_scaling(500).get("applied"))

    def test_wallpaper_missing(self):
        r = syssettings.set_wallpaper(r"C:\NOPE\not-exist.png")
        self.assertFalse(r.get("ok"))
        self.assertIn("图片不存在", r.get("content", ""))

    @mock.patch("oryx.syssettings._run_ps", return_value=(True, "applied"))
    def test_wallpaper_runs_ps_when_file_exists(self, mock_run):
        with mock.patch("os.path.exists", return_value=True):
            r = syssettings.set_wallpaper(r"C:\fake.png")
        # _ok 包了一层，里层 content 是 {"applied": True, ...}
        self.assertTrue(r.get("ok"))
        inner = r.get("content", {})
        self.assertTrue(inner.get("applied"))


class SwitchesSchema(unittest.TestCase):
    def test_groups_present(self):
        s = syssettings.switches()
        self.assertIn("power", s)
        self.assertIn("display", s)
        self.assertIn("input", s)


if __name__ == "__main__":
    unittest.main()
