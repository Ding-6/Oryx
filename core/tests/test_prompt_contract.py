"""提示词契约与凭据闸门的断言式测试。

这一份测的是**接线**，不是函数本身：
- 系统提示词里该有的段落真的在（而不是写在文档里没人看）；
- 凭据类文件的闸门真的在策略引擎里接上了（而不是只在提示词里写了一句「不要碰」）。

为什么单独开一份：这两类东西的共同点是「看起来有，实际没有」。
提示词段落写错了模型照样干活；闸门没接上则 `.env` 里密钥
会经由 index_search / fs_grep 直接进上下文 —— 而这两条路径的
入参（root / pattern）根本长得不像凭据文件，策略引擎看不见。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oryx.config import DEFAULT_CONFIG, Config
from oryx.events import EventBus
from oryx.loop import AgentRuntime
from oryx.policy import CREDENTIAL_NAMES, PolicyEngine, Risk, is_credential_path
from oryx.store import Store
from oryx.tools import build_registry

SECRET = "sk-abcdefghijklmnopqrstuvwxyz0123456789"


@pytest.fixture()
def engine(tmp_path: Path) -> PolicyEngine:
    return PolicyEngine(allowed_roots=[str(tmp_path)], forbidden_paths=["C:\\Windows"])


@pytest.fixture()
def registry():
    return build_registry()


# ------------------------------------------------------------------ 一、提示词契约


def test_system_prompt_has_eight_principle_sections():
    """八条原则都要在系统提示词里落成实际的段落。

    为什么要测：提示词是「写了就以为做了」的重灾区 ——
    段落名写对了、内容是空壳，模型照样按老习惯干活。
    """
    from oryx.loop import SYSTEM_PROMPT

    required = [
        "任务闭环",       # 第一原则
        "能力分层",       # 第二原则
        "执行纪律",       # 第三原则
        "安全与确认",     # 第四原则
        "输出规范",       # 第五原则
        "记忆",           # 第六原则
        "失败处理",       # 第七原则
        "交付前自检",     # 第八原则
    ]
    for keyword in required:
        assert keyword in SYSTEM_PROMPT, f"系统提示词缺了「{keyword}」这一段"


def test_system_prompt_states_failure_classifications():
    """失败必须分类 —— E1 到 E5 五个类目都要在提示词里。"""
    from oryx.loop import SYSTEM_PROMPT

    for code in ("E1", "E2", "E3", "E4", "E5"):
        assert code in SYSTEM_PROMPT, f"提示词缺失败分类 {code}"
    assert "重试纪律" in SYSTEM_PROMPT
    assert "降级阶梯" in SYSTEM_PROMPT


def test_system_prompt_states_red_lines():
    """绝对红线那几条必须逐条在提示词里 —— 少一条就少一个拦截点。"""
    from oryx.loop import SYSTEM_PROMPT

    for keyword in ("支付", "验证码", "编造", "编码混淆", "系统核心目录"):
        assert keyword in SYSTEM_PROMPT, f"提示词缺红线条款：{keyword}"
    # 凭据类文件那条必须带「授权」出口 —— 否则用户合法诉求也做不了
    assert ".env" in SYSTEM_PROMPT
    assert "授权" in SYSTEM_PROMPT


def test_system_prompt_requires_three_part_output():
    """输出规范的三段式标记要写死。"""
    from oryx.loop import SYSTEM_PROMPT

    for marker in ("【结果】", "【证据】", "【下一步】"):
        assert marker in SYSTEM_PROMPT, f"输出规范缺 {marker}"


def test_system_prompt_requires_language_to_follow_user():
    """语言必须跟随用户输入 —— 不能写死「中文回答」。

    写死中文时，用户用英文提问会得到英文需求、中文回答的错配结果。
    """
    from oryx.loop import SYSTEM_PROMPT

    assert "跟随用户输入" in SYSTEM_PROMPT
    assert "中文回答" not in SYSTEM_PROMPT, "提示词里还写死了「中文回答」"


def test_agent_catalog_reaches_the_system_prompt(tmp_path):
    """专职 Agent 目录要真的进系统提示词。

    提示词要求「L1 能闭环就不许拆成 L3/L4 的碎步」，
    但模型不知道有哪些 L1 可用 —— 规则就落不了地。
    """
    from oryx import loop as loop_mod
    from oryx.providers import ProviderReply, ToolCall

    class CapturingProvider:
        def __init__(self):
            self.systems: list[str] = []

        def chat(self, system, messages, tools, on_text=None, max_tokens=4096):
            self.systems.append(system)
            return ProviderReply(text="完成", tool_calls=[], stop_reason="end_turn")

    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    provider = CapturingProvider()
    loop_mod.build_provider = lambda cfg: provider  # type: ignore[assignment]

    runtime = AgentRuntime(Config(data), Store(tmp_path / "oryx.db"), EventBus())
    runtime.run("看看目录")

    assert provider.systems, "provider 没拿到系统提示词"
    system = provider.systems[0]
    assert "可用的专职 Agent" in system
    # 管家必须出现在目录里 —— 它们才是日常会派的那几个
    for agent_id in ("file-agent", "computer-agent", "browser-agent", "search-agent"):
        assert agent_id in system, f"系统提示词里没有 {agent_id}"
    # 工具白名单也要写出来 —— 主 Agent 才知道派什么活合适
    assert "可用工具：" in system


def test_spawn_agent_accepts_acceptance_criteria(tmp_path):
    """派发契约要能带验收判据，并落到子 Agent 的提示词里。"""
    from oryx import loop as loop_mod
    from oryx.providers import ProviderReply

    class CapturingProvider:
        def __init__(self):
            self.systems: list[str] = []

        def chat(self, system, messages, tools, on_text=None, max_tokens=4096):
            self.systems.append(system)
            return ProviderReply(text="完成", tool_calls=[], stop_reason="end_turn")

    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    provider = CapturingProvider()
    loop_mod.build_provider = lambda cfg: provider  # type: ignore[assignment]

    runtime = AgentRuntime(Config(data), Store(tmp_path / "oryx.db"), EventBus())
    runtime.run_subagent(
        "file-agent",
        "把这个目录下的文档整理一遍",
        acceptance="3 个文件均已落盘且返回绝对路径",
    )

    assert provider.systems, "子 Agent 没拿到提示词"
    assert "3 个文件均已落盘且返回绝对路径" in provider.systems[0]


def test_spawn_agent_tool_declares_acceptance_parameter():
    """spawn_agent 的 schema 里要有 acceptance —— 模型看得见才能填。"""
    spec = build_registry()  # 占位，真正要的是 runtime 里注册的那份
    assert spec  # 注册表本身能构造

    from oryx.config import DEFAULT_CONFIG, Config as Cfg
    from oryx.events import EventBus as Bus
    from oryx.loop import AgentRuntime as RT
    from oryx.store import Store as ST

    runtime = RT(Cfg(dict(DEFAULT_CONFIG)), ST(Path(".").parent / "unused.db"), Bus())
    schema = runtime.registry["spawn_agent"].schema()
    props = schema["input_schema"]["properties"]
    assert "acceptance" in props, "spawn_agent 的参数里没有 acceptance"


# ------------------------------------------------------------------ 二、凭据闸门


@pytest.mark.parametrize(
    "name",
    [".env", ".env.local", ".env.production", ".ssh", ".aws", ".kube", ".pgpass"],
)
def test_credential_names_are_detected(name):
    """凭据类路径要能认出来。"""
    assert is_credential_path(Path("D:/proj") / name) is True


@pytest.mark.parametrize("name", ["readme.md", "config.json", "main.py", "notes.env"])
def test_normal_files_are_not_credential_paths(name):
    """普通文件不能被误判 —— 误判会让用户的正常文件全都要授权。"""
    assert is_credential_path(Path("D:/proj") / name) is False


def test_env_family_variants_are_detected():
    """`.env` 的变体（.env.local / .env.production）也要算。"""
    assert is_credential_path(Path("D:/proj/.env.local")) is True
    assert is_credential_path(Path("D:/proj/.env.production")) is True
    # 但 foo.env 不是（那是文件名以 .env 结尾，不是 .env 家族）
    assert is_credential_path(Path("D:/proj/foo.env")) is False


def test_credential_read_escalates_to_l2(engine, registry, tmp_path):
    """读凭据文件要升到 L2 进确认通道 —— 不是 L0 直放行。"""
    target = tmp_path / ".env"
    target.write_text(f"API_KEY={SECRET}\n", encoding="utf-8")
    decision = engine.evaluate(registry["fs_read"], {"path": str(target)})
    assert decision.risk == Risk.L2_DESTRUCTIVE
    assert decision.needs_approval
    assert decision.plan is not None


def test_credential_plan_names_the_file(engine, registry, tmp_path):
    """确认卡片上要明说命中了哪个凭据文件 —— 用户靠这个判断该不该批。"""
    target = tmp_path / ".env"
    target.write_text(f"API_KEY={SECRET}\n", encoding="utf-8")
    decision = engine.evaluate(registry["fs_read"], {"path": str(target)})
    plan = decision.plan
    assert plan is not None
    # 凭据那一步要排在最前面 —— 用户先看标题，再看细节
    first = plan.steps[0]
    assert "凭据" in first["title"]
    assert str(target) in first["detail"]
    # 还要说清后果（能不能撤销、授权后会看到什么）
    assert first["impact"]


def test_credential_write_is_also_gated(engine, registry, tmp_path):
    """写凭据文件同样要授权 —— 覆盖 .env 等于改掉用户的密钥。"""
    target = tmp_path / ".env"
    decision = engine.evaluate(registry["fs_write"], {"path": str(target), "content": "x"})
    assert decision.risk == Risk.L2_DESTRUCTIVE
    assert decision.needs_approval


def test_gitconfig_is_still_hard_forbidden(engine, registry, tmp_path):
    """受保护文件仍是 L3 硬拒 —— 凭据闸门不能把它放宽了。"""
    target = tmp_path / ".gitconfig"
    decision = engine.evaluate(registry["fs_write"], {"path": str(target), "content": "x"})
    assert decision.forbidden
    assert decision.risk >= Risk.L3_FORBIDDEN


def test_grep_does_not_return_credential_content(tmp_path):
    """fs_grep 不许把 .env 的内容搜出来。

    这是关键的一条：grep 的入参是 root + pattern，
    root 根本不长得像凭据文件 —— 策略引擎看不见目录里藏着 .env，
    所以工具内部必须自己判。
    """
    from oryx.tools import fs_grep

    (tmp_path / ".env").write_text(f"API_KEY={SECRET}\n", encoding="utf-8")
    (tmp_path / "normal.txt").write_text(f"这里也有 {SECRET}\n", encoding="utf-8")

    result = fs_grep(str(tmp_path), "API_KEY|这里也有")
    assert result["ok"] is True
    blob = json.dumps(result, ensure_ascii=False)
    assert ".env" not in blob, "fs_grep 把 .env 匹配出来了"
    assert "normal.txt" in blob, "fs_grep 连普通文件都搜不到了"


def test_search_files_does_not_return_credential_content(tmp_path):
    """search_files 的内容搜索同样要挡住凭据文件。"""
    from oryx.tools import search_files

    (tmp_path / ".env").write_text(f"API_KEY={SECRET}\n", encoding="utf-8")
    (tmp_path / "normal.txt").write_text("公开内容\n", encoding="utf-8")

    result = search_files(str(tmp_path), name_contains="", content_contains="API_KEY")
    assert result["ok"] is True
    blob = json.dumps(result, ensure_ascii=False)
    assert ".env" not in blob
    assert SECRET not in blob


def test_glob_does_not_return_credential_files(tmp_path):
    """fs_glob 不许把 .env / .ssh 里的东西列出来。

    路径名本身就是线索 —— 「我的密钥在哪」不该由一个只读工具直接交出去。
    """
    from oryx.tools import fs_glob

    (tmp_path / ".env").write_text("x", encoding="utf-8")
    (tmp_path / "normal.txt").write_text("x", encoding="utf-8")
    ssh = tmp_path / ".ssh"
    ssh.mkdir()
    (ssh / "id_rsa").write_text("x", encoding="utf-8")

    result = fs_glob(str(tmp_path), "*")
    assert result["ok"] is True
    blob = json.dumps(result, ensure_ascii=False)
    assert ".env" not in blob
    assert "id_rsa" not in blob
    assert "normal.txt" in blob


def test_indexer_skips_credential_files(tmp_path):
    """索引器不收凭据文件 —— 收了之后任何一次 index_search 都能捞出密钥。"""
    from oryx.indexer import FileIndex

    (tmp_path / ".env").write_text(f"API_KEY={SECRET}\n", encoding="utf-8")
    (tmp_path / ".env.local").write_text(f"TOKEN={SECRET}\n", encoding="utf-8")
    (tmp_path / "normal.md").write_text("公开内容\n", encoding="utf-8")

    index = FileIndex(forbidden=[], ocr=False, embeddings=False)
    index.scan([str(tmp_path)], max_files=100)
    stats = index.stats()
    assert stats["files"] >= 1, "正常文件都没索引进去"

    # 凭据文件一个都不许进库
    rows = index.search("API_KEY", limit=10)
    blob = json.dumps(rows, ensure_ascii=False, default=str)
    assert SECRET not in blob, "索引里出现了凭据内容"
    assert ".env" not in blob


def test_credential_names_cover_the_prompt_red_line():
    """清单要覆盖提示词里点名的四类（.env / .ssh / .aws / .kube）。"""
    for name in (".env", ".ssh", ".aws", ".kube"):
        assert name in CREDENTIAL_NAMES
