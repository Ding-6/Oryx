"""工作流模板的只读分级、别名表与报告独占创建。

抄 Marvis 的模板元数据纪律：
- 模板带**只读标记**，界面上一眼能看出这份编排会不会改东西；
- 模板带**别名表**，用户不用背 id；
- 产出报告**不覆盖**旧文件。

三组测试，每组测一个东西：
1. 只读分级 —— 从子 Agent 工具白名单**反推**，用户改不了这个结论；
2. 别名 —— id / 名字 / 别名三种键都能取到同一份模板；
3. 报告 —— 目标已存在就失败，且**原文件一个字节都不变**。

第 3 组是这批里最重要的：报告覆盖掉上一份 = 用户再也找不回上次那份。
所以测的不只是「报错了」，还要测「原文件还在、内容没变」。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oryx.workflow import plan_read_only, write_new_report


# ------------------------------------------------------------------ 只读分级


def _plan(agent: str) -> dict:
    return {
        "name": "测试编排",
        "goal": "做点事",
        "stages": [{"name": "阶段", "steps": [{"id": "s1", "agent": agent, "goal": "干活"}]}],
    }


def test_plan_with_only_read_tools_is_read_only():
    """工具全是只读的 → 这份编排只读。"""
    agent_tools = {"reviewer": ["fs_read", "fs_list", "index_search", "doc_read"]}
    assert plan_read_only(_plan("reviewer"), agent_tools) is True


def test_plan_with_write_tool_is_not_read_only():
    """有写工具 → 不是只读。

    这是最关键的一条：判据必须是**工具白名单**，不是模板自己声明什么。
    模板声明「我保证只读」不作数 —— 判成只读却其实能写文件，
    比误判成可写危险得多。
    """
    agent_tools = {"file-agent": ["fs_read", "fs_write", "fs_list"]}
    assert plan_read_only(_plan("file-agent"), agent_tools) is False


def test_unknown_agent_is_not_read_only():
    """查不到的子 Agent 按**不可知即不安全**处理。

    白名单是子 Agent 身份的一部分，是唯一可信的判据。
    查不到就判成只读 = 把「可能能写」当成「一定不能写」，
    那个方向错了比反过来更糟。
    """
    assert plan_read_only(_plan("查无此代理"), {}) is False
    assert plan_read_only(_plan("查无此代理"), {"别的代理": ["fs_read"]}) is False


def test_empty_plan_is_read_only():
    """空编排没有 step → 没有能改东西的地方 → 只读。"""
    assert plan_read_only({}, {}) is True
    assert plan_read_only({"stages": []}, {}) is True


def test_every_step_is_checked_not_just_the_first():
    """所有 step 都要查 —— 只查第一个的话，第二个 step 写文件就漏了。"""
    plan = {
        "name": "两段",
        "goal": "先读后写",
        "stages": [
            {"name": "读", "steps": [{"id": "a", "agent": "reviewer", "goal": "读"}]},
            {"name": "写", "steps": [{"id": "b", "agent": "file-agent", "goal": "写"}]},
        ],
    }
    agent_tools = {
        "reviewer": ["fs_read"],
        "file-agent": ["fs_read", "fs_write"],
    }
    assert plan_read_only(plan, agent_tools) is False


def test_read_only_ignores_non_mutating_tools():
    """非改动的工具不算写。

    `browser_screenshot` 会落文件，但它在 MUTATING_TOOLS 里 ——
    所以这里只测真正无害的那几个。
    """
    agent_tools = {"r": ["fs_read", "fs_list", "index_search", "system_info", "term_status"]}
    assert plan_read_only(_plan("r"), agent_tools) is True


def test_read_only_is_recomputed_not_read_from_storage(tmp_path):
    """只读标记**重算**，不读存下来的那份。

    场景：模板存的时候 reviewer 只有只读工具（存成只读），
    后来给 reviewer 加了 fs_write —— 这份模板立刻不再只读。
    存下来的标记不会更新，重算的会。
    """
    from oryx.config import DEFAULT_CONFIG, Config
    from oryx.events import EventBus
    from oryx.loop import AgentRuntime
    from oryx.store import Store

    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    config = Config(data)
    store = Store(tmp_path / "oryx.db")
    runtime = AgentRuntime(config, store, EventBus())

    plan = _plan("reviewer")
    saved = runtime.save_workflow_template(
        {"id": "tpl_ro", "name": "只读模板", "plan": plan, "aliases": ["巡检"]}
    )
    assert saved["ok"] is True
    assert saved["content"]["read_only"] is True, "存的时候就是只读"

    # 给 reviewer 加写工具 —— 模拟用户改了子 Agent 的白名单
    runtime.agents.agents["reviewer"].tools = list(
        runtime.agents.agents["reviewer"].tools
    ) + ["fs_write"]

    listed = runtime.list_workflow_templates()
    item = next(x for x in listed if x["id"] == "tpl_ro")
    assert item["read_only"] is False, "白名单变了之后只读标记必须跟着变"

    detail = runtime.get_workflow_template("tpl_ro")
    assert detail is not None
    assert detail["read_only"] is False, "详情接口也要重算，不能拿存的那份"


def test_save_rejects_broken_plan(tmp_path):
    """坏定义不留到运行时才炸。"""
    from oryx.config import DEFAULT_CONFIG, Config
    from oryx.events import EventBus
    from oryx.loop import AgentRuntime
    from oryx.store import Store

    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    runtime = AgentRuntime(Config(data), Store(tmp_path / "oryx.db"), EventBus())

    out = runtime.save_workflow_template(
        {"id": "tpl_bad", "name": "坏的", "plan": {"name": "x", "goal": "y", "stages": []}}
    )
    assert out["ok"] is False
    assert "编排定义有问题" in str(out["content"])


# ------------------------------------------------------------------ 别名


def test_alias_is_stripped_and_deduped(tmp_path):
    """别名去空去重 —— 空别名会让按别名查模板时匹配到任何东西。"""
    from oryx.config import DEFAULT_CONFIG, Config
    from oryx.events import EventBus
    from oryx.loop import AgentRuntime
    from oryx.store import Store

    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    runtime = AgentRuntime(Config(data), Store(tmp_path / "oryx.db"), EventBus())

    out = runtime.save_workflow_template(
        {
            "id": "tpl_a",
            "name": "巡检模板",
            "plan": _plan("reviewer"),
            "aliases": ["  巡检  ", "", "巡检", " doc-check "],
        }
    )
    assert out["ok"] is True
    assert out["content"]["aliases"] == ["巡检", "doc-check"]


def test_template_can_be_fetched_by_alias(tmp_path):
    """别名能当键用 —— 用户不用背 id。"""
    from oryx.config import DEFAULT_CONFIG, Config
    from oryx.events import EventBus
    from oryx.loop import AgentRuntime
    from oryx.store import Store

    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    runtime = AgentRuntime(Config(data), Store(tmp_path / "oryx.db"), EventBus())

    runtime.save_workflow_template(
        {"id": "tpl_x", "name": "文档巡检", "plan": _plan("reviewer"), "aliases": ["巡检"]}
    )

    by_id = runtime.get_workflow_template("tpl_x")
    by_name = runtime.get_workflow_template("文档巡检")
    by_alias = runtime.get_workflow_template("巡检")
    assert by_id is not None and by_name is not None and by_alias is not None
    assert by_id["id"] == by_name["id"] == by_alias["id"] == "tpl_x"


def test_alias_lookup_is_exact_not_prefix(tmp_path):
    """别名是精确匹配 —— 前缀不该命中。

    不精确的话，「巡」会命中「巡检」，用户以为自己跑的是另一份模板。
    """
    from oryx.config import DEFAULT_CONFIG, Config
    from oryx.events import EventBus
    from oryx.loop import AgentRuntime
    from oryx.store import Store

    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    runtime = AgentRuntime(Config(data), Store(tmp_path / "oryx.db"), EventBus())

    runtime.save_workflow_template(
        {"id": "tpl_y", "name": "巡检", "plan": _plan("reviewer"), "aliases": ["巡检"]}
    )
    assert runtime.get_workflow_template("巡") is None


def test_aliases_survive_a_round_trip(tmp_path):
    """别名存进去要能读回来 —— 存的是 JSON 数组，读的时候要容错。"""
    from oryx.config import DEFAULT_CONFIG, Config
    from oryx.events import EventBus
    from oryx.loop import AgentRuntime
    from oryx.store import Store

    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    store = Store(tmp_path / "oryx.db")
    runtime = AgentRuntime(Config(data), store, EventBus())

    runtime.save_workflow_template(
        {"id": "tpl_z", "name": "带别名", "plan": _plan("reviewer"), "aliases": ["a", "b"]}
    )
    detail = store.get_workflow_template("tpl_z")
    assert detail is not None
    assert detail["aliases"] == ["a", "b"]
    assert isinstance(json.dumps(detail["aliases"]), str)


def test_corrupt_aliases_column_does_not_crash(tmp_path):
    """aliases 列被写坏时不能炸 —— 读出来当空列表。"""
    from oryx.store import Store

    store = Store(tmp_path / "oryx.db")
    store.save_workflow_template(
        {"id": "tpl_c", "name": "坏的别名", "plan": _plan("reviewer"), "aliases": ["x"]}
    )
    with store._lock, store._conn() as conn:
        conn.execute("UPDATE workflow_templates SET aliases=? WHERE id=?", ("{不是json", "tpl_c"))

    detail = store.get_workflow_template("tpl_c")
    assert detail is not None
    assert detail["aliases"] == []


# ------------------------------------------------------------------ 报告独占创建


def test_report_is_written(tmp_path):
    """报告写到新文件。"""
    path = tmp_path / "run_1.md"
    out = write_new_report(str(path), "结论一二三")
    assert out == str(path)
    assert path.read_text("utf-8").strip() == "结论一二三"


def test_report_ends_with_newline(tmp_path):
    """末尾补换行 —— 文本文件没有结尾换行会让别的工具读着别扭。"""
    path = tmp_path / "run_2.md"
    write_new_report(str(path), "结论")
    assert path.read_text("utf-8").endswith("\n")


def test_existing_report_is_not_overwritten(tmp_path):
    """已存在就失败，且**原文件一个字节都不变**。

    这条是这批的核心：覆盖掉上一份 = 用户再也找不回上次那份。
    所以不光要断言「报错」，还要断言「原内容还在」。
    """
    path = tmp_path / "run_3.md"
    path.write_text("上一次的报告", encoding="utf-8")
    before = path.read_bytes()

    with pytest.raises(ValueError) as excinfo:
        write_new_report(str(path), "这一次的报告")

    assert "已存在" in str(excinfo.value)
    assert path.read_bytes() == before, "原报告被覆盖了"
    assert path.read_text("utf-8") == "上一次的报告"


def test_report_refuses_to_write_into_missing_directory(tmp_path):
    """父目录不存在就失败 —— 不替用户猜目录。"""
    with pytest.raises(ValueError) as excinfo:
        write_new_report(str(tmp_path / "没有这个目录" / "run.md"), "结论")
    assert "报告目录不存在" in str(excinfo.value)


def test_report_rejects_path_with_newline(tmp_path):
    """路径带换行要挡 —— 换行能骗过日志。"""
    with pytest.raises(ValueError) as excinfo:
        write_new_report(str(tmp_path / "a\nb.md"), "结论")
    assert "报告路径无效" in str(excinfo.value)


def test_report_rejects_empty_path():
    with pytest.raises(ValueError):
        write_new_report("", "结论")


def test_report_refuses_directory_target(tmp_path):
    """目标是目录就失败。"""
    with pytest.raises(ValueError) as excinfo:
        write_new_report(str(tmp_path), "结论")
    assert "是目录" in str(excinfo.value)


def test_report_uses_exclusive_create(tmp_path):
    """底层用 ``open(dest, "x")`` 独占创建 —— 不是先查再写。

    「先 exists 再写」有个竞态窗口：两个进程同时过了检查，
    后写的那个就把前一个的覆盖了。独占创建是内核级的原子操作。
    """
    path = tmp_path / "run_4.md"
    write_new_report(str(path), "第一次")
    with pytest.raises(FileExistsError):
        open(path, "x", encoding="utf-8")
    # 再写一次：还是被挡住
    with pytest.raises(ValueError):
        write_new_report(str(path), "第二次")
    assert path.read_text("utf-8").strip() == "第一次"


def test_report_path_survives_the_agentd_boundary(tmp_path):
    """报告路径要能穿过 agentd 的返回收口。

    这一层会把引擎的返回收成 `{ok, content, run_id, status}` 四个字段 ——
    多出来的字段会被**静默丢掉**。丢在哪个字段上，
    报告就变成一份谁也找不到的文件。
    """
    from oryx.agentd import AgentDaemon

    daemon = AgentDaemon.__new__(AgentDaemon)  # 不起进程，只借这个方法
    out = AgentDaemon._workflow_result(
        daemon,
        {"ok": True, "content": "产出", "meta": {"run_id": "wfr_x", "status": "done",
                                          "report_path": str(tmp_path / "r.md")}},
    )
    assert out["report_path"] == str(tmp_path / "r.md"), "报告路径被收口层丢了"
    assert out["run_id"] == "wfr_x"
    assert out["status"] == "done"


# ------------------------------------------------------------------ 端到端


def test_successful_run_writes_a_report_file(tmp_path, monkeypatch):
    """编排跑成功 → 落一份报告文件，且路径回传给调用方。

    这是「报告独占创建」这条规则的接线测试：
    单元测试只证 write_new_report 自己守规矩，
    这条证内核真的调了它、并且把路径放进 meta。
    """
    from oryx import loop as loop_mod
    from oryx.config import DEFAULT_CONFIG, Config
    from oryx.events import EventBus
    from oryx.loop import AgentRuntime
    from oryx.providers import ProviderReply, ToolCall
    from oryx.store import Store

    class FakeProvider:
        def __init__(self):
            self.calls = 0

        def chat(self, system, messages, tools, on_text=None, max_tokens=4096):
            self.calls += 1
            return ProviderReply(text="看完了", tool_calls=[], stop_reason="end_turn")

    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    data["workspace"] = str(tmp_path)
    config = Config(data)
    store = Store(tmp_path / "oryx.db")
    runtime = AgentRuntime(config, store, EventBus())
    fake = FakeProvider()
    loop_mod.build_provider = lambda cfg: fake  # type: ignore[assignment]

    # 报告落到临时目录，不碰用户的数据目录
    monkeypatch.setattr("oryx.config.app_dir", lambda: tmp_path)

    plan = {
        "name": "只读编排",
        "goal": "读点东西",
        "stages": [{"name": "读", "steps": [{"id": "s1", "agent": "reviewer", "goal": "读一遍"}]}],
    }
    out = runtime.run_workflow(plan)

    assert out["ok"] is True
    report_path = out["meta"].get("report_path")
    assert report_path, "成功的编排应该产出报告文件"
    assert str(tmp_path) in str(report_path), "报告不该落在用户真实数据目录"
    assert Path(report_path).exists()
    assert Path(report_path).read_text("utf-8").strip()


def test_two_runs_produce_two_distinct_reports(tmp_path, monkeypatch):
    """两次跑各有一份报告 —— 互不覆盖。"""
    from oryx import loop as loop_mod
    from oryx.config import DEFAULT_CONFIG, Config
    from oryx.events import EventBus
    from oryx.loop import AgentRuntime
    from oryx.providers import ProviderReply
    from oryx.store import Store

    class FakeProvider:
        def chat(self, system, messages, tools, on_text=None, max_tokens=4096):
            return ProviderReply(text="看完了", tool_calls=[], stop_reason="end_turn")

    data = dict(DEFAULT_CONFIG)
    data["allowed_roots"] = [str(tmp_path)]
    data["workspace"] = str(tmp_path)
    runtime = AgentRuntime(Config(data), Store(tmp_path / "oryx.db"), EventBus())
    loop_mod.build_provider = lambda cfg: FakeProvider()  # type: ignore[assignment]
    monkeypatch.setattr("oryx.config.app_dir", lambda: tmp_path)

    plan = {
        "name": "只读编排",
        "goal": "读点东西",
        "stages": [{"name": "读", "steps": [{"id": "s1", "agent": "reviewer", "goal": "读一遍"}]}],
    }
    first = runtime.run_workflow(plan)
    second = runtime.run_workflow(plan)

    p1 = Path(first["meta"]["report_path"])
    p2 = Path(second["meta"]["report_path"])
    assert p1 != p2, "两次跑必须两份报告，不能互相覆盖"
    assert p1.exists() and p2.exists()
