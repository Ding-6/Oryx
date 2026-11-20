"""配置同步：用户改了设置，内核必须跟着改。

这个 bug 的样子
--------------
用户在设置页填好 api_key，聊天仍报：

    任务失败: provider [minimax] 缺少 api_key，请在配置文件中填写

配置里明明有值（而且确实写进了系统凭据库）——
因为**内核进程**还拿着启动时那份配置。

为什么内核没跟着改
------------------
内核是独立进程，它启动时读了一次 config.json 就一直拿着。
控制面（server）改完配置后要通知内核重读，走 `AgentLink._sync_config`。

而那个通知的判据用错了：**拿 `Config.refresh()` 的返回值当「配置变了没」**。
但这个信号会被别人先消费掉：

  · `Config.save()` 写完文件顺手更新了 `_mtime`
  · HTTP 中间件每个请求开头都调 `config.refresh()`

等 `_sync_config` 去问时，mtime 早就一致了 → 返回 False → **内核永远收不到通知**。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[1]
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))


@pytest.fixture()
def cfg(tmp_path):
    from oryx.config import Config

    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "api_token": "t",
                "active_provider": "minimax",
                "providers": {
                    "minimax": {
                        "name": "minimax",
                        "type": "anthropic",
                        "base_url": "https://api.minimax.cn/anthropic",
                        "model": "MiniMax-M3.1-Flash",
                        "api_key": "",
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return Config(json.loads(path.read_text("utf-8")), path=path)


class _FakeLink:
    """只借 AgentLink 的 _sync_config —— 不碰真 IPC。"""

    def __init__(self, cfg):
        from oryx.agentlink import AgentLink

        self.calls: list[str] = []
        self._link = AgentLink(on_event=lambda *a, **k: None, config_provider=lambda: cfg)

        def _call(method, timeout=600.0, **params):
            self.calls.append(method)
            return {"ok": True}

        self._link.call = _call  # type: ignore[method-assign]

    def sync(self):
        self._link._sync_config()


def test_config_reload_is_sent_after_save_even_when_refresh_already_consumed(cfg):
    """**核心回归**：save() 之后必须通知内核，哪怕 refresh() 已经返回过 False。

    这里刻意复现真实顺序：
      1. 用户在设置里保存 api_key → save() 更新了 _mtime
      2. HTTP 中间件下一个请求开头 refresh() → 返回 False（已经是最新的）
      3. AgentLink._sync_config() → **这里必须**发出 runtime.config_reload
    """
    link = _FakeLink(cfg)
    link.sync()  # 首次同步（对齐启动时的配置）

    # 用户保存 api_key
    cfg.set_provider_api_key("minimax", "sk-test-123")
    assert cfg.provider_api_key("minimax") == "sk-test-123"

    # HTTP 中间件抢先 refresh 掉了「变了没」这个信号
    assert cfg.refresh() is False, "中间件已经把信号消费掉了（这正是 bug 的成因）"

    link.calls.clear()
    link.sync()

    assert "runtime.config_reload" in link.calls, (
        "内核没收到重载通知 —— 用户改了设置，内核还拿着旧配置"
    )


def test_no_spurious_reload_when_config_unchanged(cfg):
    """配置没变时不该反复通知内核（每次 chat 都发一次 RPC 太吵）。"""
    link = _FakeLink(cfg)
    link.sync()
    link.calls.clear()

    # 模拟中间件 refresh（不改内容）
    cfg.refresh()
    link.sync()

    assert "runtime.config_reload" not in link.calls, "配置没变却通知了内核"


def test_disk_mtime_does_not_touch_internal_state(cfg):
    """`disk_mtime` 只是「看一眼」，不能顺手把 refresh 的信号吃掉。"""
    from oryx.config import Config

    c = Config(json.loads(json.dumps(cfg.data)), path=cfg.path)
    before = c._mtime

    mtime = c.disk_mtime
    assert mtime is not None
    assert c._mtime == before, "disk_mtime 改了内部状态 —— 信号又被吃掉了"

    # 读完之后 refresh 仍然应该报告「变了」
    cfg.path.write_text(
        json.dumps({**cfg.data, "mode": "local"}, ensure_ascii=False), encoding="utf-8"
    )
    assert c.refresh() is True


def test_disk_mtime_none_when_not_persisted():
    from oryx.config import Config

    c = Config({"mode": "craft"})
    assert c.disk_mtime is None


def test_settings_api_triggers_kernel_sync(tmp_path):
    """设置页保存 api_key → 磁盘 mtime 变 → 内核会被通知。

    这一条把**整条链**串起来：HTTP 接口 → 配置落盘 → 同步判据成立。
    少了最后一环（mtime 变了但没人通知内核），内核就一直拿着旧配置 ——
    用户填好 api_key，聊天仍报「缺少 api_key」。
    """
    from oryx.config import Config
    from oryx.domains.model_config import ProviderEditRequest, edit_provider

    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "api_token": "t",
                "active_provider": "probe_test",
                "providers": {
                    "probe_test": {
                        "name": "probe_test",
                        "type": "anthropic",
                        "base_url": "http://127.0.0.1:9/never",
                        "model": "probe-model",
                        "api_key": "",
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cfg = Config(json.loads(path.read_text("utf-8")), path=path)

    link = _FakeLink(cfg)
    link.sync()  # 首次同步
    before_mtime = link._link._synced_mtime

    # 设置页保存 api_key —— 走域里的 action（路由就是调它）。
    # **不经过 HTTP**：TestClient 用的是 server 模块里那份真实配置的 app，
    # 走 HTTP 会把 probe_test 写进用户的真实 config.json。
    edit_provider(cfg, "probe_test", ProviderEditRequest(api_key="sk-probe-fake"))

    # 配置确实落盘了
    on_disk = json.loads(path.read_text("utf-8"))
    assert on_disk["providers"]["probe_test"].get("api_key"), "api_key 没落盘"

    # 同步判据成立：磁盘 mtime 变了
    assert cfg.disk_mtime != before_mtime, "磁盘 mtime 没变"

    # → 内核会被通知
    link.calls.clear()
    link.sync()
    assert "runtime.config_reload" in link.calls
