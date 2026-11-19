"""配置刷新：长驻进程不能覆盖用户手改的 config.json。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from oryx.config import Config


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ORYX_HOME", str(tmp_path))
    # 让模块级常量指向临时目录
    import oryx.config as cfgmod

    monkeypatch.setattr(cfgmod, "CONFIG_PATH", tmp_path / "config.json")
    return tmp_path


def write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")


def test_refresh_picks_up_external_provider(home):
    path = home / "config.json"
    write(path, {"providers": {"a": {"type": "openai", "base_url": "x", "model": "m"}}})

    config = Config.load()
    assert "a" in config.data["providers"]

    # 外部新增一个 provider
    write(
        path,
        {
            "providers": {
                "a": {"type": "openai", "base_url": "x", "model": "m"},
                "b": {"type": "anthropic", "base_url": "y", "model": "n"},
            }
        },
    )
    # mtime 精度可能不够，手动改一下
    import os as _os
    import time

    _os.utime(path, (time.time() + 5, time.time() + 5))

    assert config.refresh() is True
    assert "b" in config.data["providers"]


def test_refresh_keeps_in_memory_only_keys(home):
    path = home / "config.json"
    write(path, {"providers": {}})
    config = Config.load()

    # 内存里有磁盘上没有的键（本次运行新增的）
    config.data["interaction"] = "ask"
    config.save()

    import time

    _os_utime = time.time() + 10
    import os as _os

    _os.utime(path, (_os_utime, _os_utime))
    config.refresh()
    assert config.data["interaction"] == "ask"


def test_refresh_is_noop_when_unchanged(home):
    path = home / "config.json"
    write(path, {"providers": {}})
    config = Config.load()
    assert config.refresh() is False


def test_refresh_handles_missing_file(home):
    config = Config.load()
    assert config.refresh() is False


def test_refresh_handles_corrupt_file(home):
    path = home / "config.json"
    path.write_text("{ 这不是 json", "utf-8")
    config = Config.load()
    import time

    t = time.time() + 10
    import os as _os

    _os.utime(path, (t, t))
    assert config.refresh() is False
    assert config.data["providers"] is not None


def test_save_updates_mtime_tracking(home):
    path = home / "config.json"
    write(path, {"providers": {}})
    config = Config.load()
    config.data["theme"] = "dark"
    config.save()
    assert json.loads(path.read_text("utf-8"))["theme"] == "dark"
    # 保存后不应再触发一次 refresh
    assert config.refresh() is False


def test_unbound_config_never_touches_disk(home):
    """直接构造的 Config 不许往磁盘写。

    这不是洁癖 —— 之前 test_domains 里一句 Config({"providers": {}})
    加一次 save()，就把用户真实的 %APPDATA%\\Oryx\\config.json 里的
    模型服务整个覆盖成空字典，active_provider 还悬空指着已删除的服务，
    表现为「工作流一步都跑不通」。
    """
    path = home / "config.json"
    write(path, {"providers": {"mine": {"type": "openai", "model": "m"}}, "theme": "light"})

    config = Config({"providers": {}})
    config.data["theme"] = "dark"
    config.save()

    disk = json.loads(path.read_text("utf-8"))
    assert disk["providers"] == {"mine": {"type": "openai", "model": "m"}}, disk
    assert disk["theme"] == "light"
    # 内存里该改的还是要改 —— 只是不落盘
    assert config.data["theme"] == "dark"


def test_unbound_config_refresh_is_noop(home):
    """没绑定路径的实例 refresh() 也不该去读磁盘。"""
    path = home / "config.json"
    write(path, {"providers": {"mine": {"type": "openai", "model": "m"}}})
    config = Config({"providers": {}})
    assert config.refresh() is False
    assert config.data["providers"] == {}
