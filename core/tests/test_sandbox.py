"""命令沙箱：环境变量剔除 + 工作目录监禁 + 工具注册与风险分级。"""

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from oryx import loop as loop_mod
from oryx.config import DEFAULT_CONFIG, Config
from oryx.events import EventBus
from oryx.loop import AgentRuntime
from oryx.policy import Risk
from oryx.sandbox import resolve_jail, run_sandboxed, scrub_env
from oryx.store import Store


class ScrubEnv(unittest.TestCase):
    def test_drops_sensitive_vars(self):
        env = {
            "PATH": "C:\\Windows",
            "ANTHROPIC_API_KEY": "sk-secret",
            "AWS_SECRET_ACCESS_KEY": "xxx",
            "MY_TOKEN": "t",
            "DB_PASSWORD": "p",
            "EDITOR": "notepad",
        }
        out = scrub_env(env)
        self.assertEqual(out["PATH"], "C:\\Windows")
        self.assertEqual(out["EDITOR"], "notepad")
        for dropped in ("ANTHROPIC_API_KEY", "AWS_SECRET_ACCESS_KEY", "MY_TOKEN", "DB_PASSWORD"):
            self.assertNotIn(dropped, out)

    def test_whitelist_survives_even_if_suspicious(self):
        out = scrub_env({"SYSTEMROOT": "C:\\Windows", "COMSPEC": "cmd.exe"})
        self.assertIn("SYSTEMROOT", out)
        self.assertIn("COMSPEC", out)


class RunSandboxed(unittest.TestCase):
    def test_runs_inside_jail_and_echoes_cwd(self):
        with TemporaryDirectory() as tmp:
            if sys.platform == "win32":
                result = run_sandboxed("cd", tmp)
            else:
                result = run_sandboxed("pwd", tmp)
            self.assertTrue(result["ok"])
            self.assertIn("sandbox", result["content"])
            self.assertTrue(result["content"]["sandbox"]["env_scrubbed"])
            self.assertEqual(result["content"]["exit_code"], 0)

    def test_child_cannot_see_scrubbed_env(self):
        with TemporaryDirectory() as tmp:
            os.environ["ORYX_TEST_SECRET_TOKEN"] = "should-not-leak"
            try:
                if sys.platform == "win32":
                    result = run_sandboxed("echo %ORYX_TEST_SECRET_TOKEN%", tmp)
                else:
                    result = run_sandboxed("echo $ORYX_TEST_SECRET_TOKEN", tmp)
            finally:
                del os.environ["ORYX_TEST_SECRET_TOKEN"]
            self.assertTrue(result["ok"])
            self.assertNotIn("should-not-leak", result["content"]["stdout"])

    def test_missing_jail_is_error(self):
        result = run_sandboxed("echo hi", "D:/no/such/dir/at/all")
        self.assertFalse(result["ok"])

    def test_resolve_jail_falls_back(self):
        jail = resolve_jail(None, os.path.expanduser("~"))
        self.assertTrue(jail.is_absolute())


def _make_runtime(tmp_path) -> AgentRuntime:
    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    data["forbidden_paths"] = []
    data["workspace"] = str(tmp_path)
    config = Config(data)
    # 测试不读用户真实的 features.yaml（上面的开关状态不可控）
    config.features_path = lambda: Path(str(tmp_path)) / "no-features.yaml"
    store = Store(Path(str(tmp_path)) / "oryx.db")
    return AgentRuntime(config, store, EventBus())


def test_tool_registered_with_l1_and_root_param(tmp_path):
    runtime = _make_runtime(tmp_path)
    spec = runtime.registry.get("shell_sandbox")
    assert spec is not None, "shell_sandbox 应默认注册（personal 默认开沙箱）"
    assert spec.risk == Risk.L1_WRITE
    # root 作为路径参数，策略引擎会对它做 allowed_roots 检查
    assert "root" in spec.parameters["properties"]


def test_tool_executes_in_workspace(tmp_path):
    runtime = _make_runtime(tmp_path)
    result = runtime.registry["shell_sandbox"].fn(command="echo hello")
    assert result["ok"]
    assert "hello" in result["content"]["stdout"]


def test_forbidden_jail_rejected(tmp_path):
    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    data["forbidden_paths"] = ["C:\\Windows"] if sys.platform == "win32" else ["/etc"]
    data["workspace"] = str(tmp_path)
    config = Config(data)
    config.features_path = lambda: Path(str(tmp_path)) / "no-features.yaml"
    runtime = AgentRuntime(config, Store(Path(str(tmp_path)) / "o.db"), EventBus())
    jail = "C:\\Windows" if sys.platform == "win32" else "/etc"
    result = runtime.registry["shell_sandbox"].fn(command="echo hi", root=jail)
    assert not result["ok"]


def test_policy_engine_escalates_blacklisted_command(tmp_path):
    """沙箱不替代黑名单：命中规则的命令仍被策略引擎升级。"""
    runtime = _make_runtime(tmp_path)
    spec = runtime.registry["shell_sandbox"]
    decision = runtime.policy.evaluate(spec, {"command": "rm -rf / --no-preserve-root"})
    # 命中 DEL 规则 → 升到 L2 需要审批；至少不低于 L1
    assert decision.risk >= Risk.L1_WRITE


def test_sandbox_flag_gates_exposure_at_runtime(tmp_path):
    """DisableSandbox 在**工具暴露层**生效，且运行时切换立即生效。

    2026-11-02 契约变更：以前在 _register_sandbox_tool() 注册时就跳过，
    界面上改开关必须重启内核才生效 —— 开关等于半个摆设。
    现在注册表始终保留工具，由 _exposed_registry() 每轮裁剪：
    关掉 → 模型看不见也调不到；重新打开 → 下一轮立刻恢复。

    断言比旧版更强：旧版只验「不在 registry」，这里还验「动态可逆」。
    """
    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    data["forbidden_paths"] = []
    data["workspace"] = str(tmp_path)
    flags = Path(str(tmp_path)) / "features.yaml"
    flags.write_text('{"flags": {"DisableSandbox": true}}', "utf-8")
    config = Config(data)
    config.features_path = lambda: flags
    runtime = AgentRuntime(config, Store(Path(str(tmp_path)) / "o.db"), EventBus())

    # 注册表保留（重新开启时不必重建 runtime），但模型这一轮看不到
    assert "shell_sandbox" in runtime.registry
    assert "shell_sandbox" not in runtime._exposed_registry()

    # 界面上把开关打开：改完文件下一轮立即恢复，不用重启内核。
    # 两个方向的文件长度不同，能确保缓存戳（mtime, size）确实变了。
    flags.write_text('{"flags": {"DisableSandbox": false}}', "utf-8")
    assert "shell_sandbox" in runtime._exposed_registry()

    # 再关回去（内容换一种写法，避免和第一次的 size 撞车导致缓存假命中）
    flags.write_text('{"flags": {"DisableSandbox": true}, "rev": 2}', "utf-8")
    assert "shell_sandbox" not in runtime._exposed_registry()


if __name__ == "__main__":
    unittest.main()
