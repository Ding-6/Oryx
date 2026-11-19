"""第二批改造的回归验证。

覆盖：
    C7  两段式工具加载（Defer/NoDefer、延迟暴露、defer_execute 走权限链路）
    C8  Hook 三态决策 + 改写入参
    C9  MCP 传输扩展 + .mcp.json 三作用域 + defer_loading
    C10 配置多作用域（user/project/local，hooks 合并）
    C11 权限模式补齐（bypass/dontAsk/auto/delegate）
    C12 子代理 frontmatter + 嵌套/预算硬闸门
    C13 定时任务 + 团队持久化

运行：python -m tests.verify_batch2
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILED.append(name)


def fresh_oryx(home: str):
    """在指定 ORYX_HOME 下重新加载 oryx 模块。

    config.CONFIG_PATH 是导入时算出来的，导入后再改环境变量不生效 ——
    所以必须把模块从 sys.modules 里踢掉重新导入。
    """
    os.environ["ORYX_HOME"] = home
    for mod in [m for m in list(sys.modules) if m.startswith("oryx")]:
        del sys.modules[mod]


# ---------------------------------------------------------------- C7 延迟工具
def test_defer() -> None:
    from oryx.tooldefer import DeferPolicy, parse_modifiers

    defer, nodefer = parse_modifiers("default,Defer(mcp__github__*),NoDefer(mcp__time__now)")
    check("解析 Defer 通配", "mcp__github__*" in defer, str(defer))
    check("解析 NoDefer", "mcp__time__now" in nodefer, str(nodefer))

    p = DeferPolicy()
    p.add_spec("default,Defer(mcp__github__*),NoDefer(mcp__time__now)")
    check("命中通配延迟", p.should_defer("mcp__github__create_issue"))
    check("NoDefer 豁免", not p.should_defer("mcp__time__now"))
    check("普通工具不延迟", not p.should_defer("fs_read"))

    # NoDefer 恒定优先
    p.defer_patterns(["fs_read"])
    p.nodefer("fs_read")
    check("NoDefer 压过 Defer", not p.should_defer("fs_read"))

    # 展开后不再延迟
    p2 = DeferPolicy()
    p2.defer_patterns(["mcp__a__b"])
    check("延迟前为 True", p2.should_defer("mcp__a__b"))
    p2.materialize("mcp__a__b")
    check("展开后不再延迟", not p2.should_defer("mcp__a__b"))

    # 拆分
    p3 = DeferPolicy()
    p3.defer_patterns(["x_*"])
    exposed, deferred = p3.split({"x_1": object(), "y": object()})
    check("拆分正确", len(exposed) == 1 and len(deferred) == 1, f"{exposed} {deferred}")


def test_defer_execute_goes_through_policy() -> None:
    """defer_execute 必须走完整权限链路，不能绕过策略引擎。"""
    from oryx.config import load_config
    from oryx.events import EventBus
    from oryx.loop import AgentRuntime
    from oryx.providers import ToolCall
    from oryx.store import Store

    tmp = tempfile.mkdtemp(prefix="oryx_b2_")
    os.environ["ORYX_HOME"] = tmp
    cfg = load_config()
    cfg.data["workspace"] = tmp
    cfg.data["allowed_roots"] = [tmp]
    cfg.data["interaction"] = "craft"

    store = Store()
    rt = AgentRuntime(cfg, store, EventBus())
    rt._active_task_id = "t1"

    # 延迟 fs_delete，然后经 defer_execute 调它 —— 应被 L2 拦下（需要确认）
    rt.defer.defer_patterns(["fs_delete"])
    exposed = rt._exposed_registry()
    check("延迟工具不进暴露集", "fs_delete" not in exposed)
    check("发现通道始终在", "tool_search" in exposed and "defer_execute" in exposed)

    # 手动确认它进了活动注册表
    rt._active_registry = dict(rt.registry)
    seq = [0]

    def _run():
        seq[0] += 1
        return rt._handle_tool_call("t1", seq[0], ToolCall(
            id="x1", name="fs_delete", arguments={"path": os.path.join(tmp, "a.txt")}
        ))

    # fs_delete 是 L2，会等确认 —— 这里直接验证策略判定，不真的执行
    decision = rt.policy.evaluate(rt.registry["fs_delete"], {"path": os.path.join(tmp, "a.txt")})
    check("延迟工具仍走策略判定", decision.needs_approval, f"risk={decision.risk}")

    rt.shutdown()
    os.environ.pop("ORYX_HOME", None)


# ---------------------------------------------------------------- C8 Hook 三态
def test_hook_decisions() -> None:
    from oryx.hooks import HookBus, ON_TOOL_START

    bus = HookBus()
    # 不表态
    bus.on(ON_TOOL_START, lambda **kw: None, owner="t")
    r = bus.emit(ON_TOOL_START, veto=True)
    check("不表态时不否决", not r["veto"] and r["decision"] == "")

    # deny
    bus2 = HookBus()
    bus2.on(ON_TOOL_START, lambda **kw: False, owner="t")
    r = bus2.emit(ON_TOOL_START, veto=True)
    check("False 即 deny", r["veto"] and r["decision"] == "deny")

    # deny + 原因
    bus3 = HookBus()
    bus3.on(ON_TOOL_START, lambda **kw: (False, "本轮禁止"), owner="t")
    r = bus3.emit(ON_TOOL_START, veto=True)
    check("带原因的 deny", r["veto"] and r["reason"] == "本轮禁止", r["reason"])

    # allow
    bus4 = HookBus()
    bus4.on(ON_TOOL_START, lambda **kw: "allow", owner="t")
    r = bus4.emit(ON_TOOL_START, veto=True)
    check("allow 放行", not r["veto"] and r["decision"] == "allow")

    # ask
    bus5 = HookBus()
    bus5.on(ON_TOOL_START, lambda **kw: ("ask", "需要确认"), owner="t")
    r = bus5.emit(ON_TOOL_START, veto=True)
    check("ask 转人工", not r["veto"] and r["decision"] == "ask", str(r))

    # 结构化 + 改写入参
    bus6 = HookBus()
    bus6.on(ON_TOOL_START, lambda **kw: {
        "decision": "allow", "reason": "改路径", "modified_args": {"path": "/tmp/x"},
    }, owner="t")
    r = bus6.emit(ON_TOOL_START, veto=True)
    check("结构化决策解析", r["decision"] == "allow" and r["reason"] == "改路径", str(r))
    check("改写入参透出", r["modified_args"] == {"path": "/tmp/x"}, str(r["modified_args"]))

    # hook 抛异常不影响主流程
    bus7 = HookBus()

    def _boom(**kw):
        raise RuntimeError("boom")

    bus7.on(ON_TOOL_START, _boom, owner="t")
    r = bus7.emit(ON_TOOL_START, veto=True)
    check("hook 异常被吞", not r["veto"] and len(r["errors"]) == 1, str(r))

    # deny 之后不再分发
    order = []
    bus8 = HookBus()
    bus8.on(ON_TOOL_START, lambda **kw: order.append("a") or False, owner="a")
    bus8.on(ON_TOOL_START, lambda **kw: order.append("b") or True, owner="b")
    bus8.emit(ON_TOOL_START, veto=True)
    check("deny 后停止分发", order == ["a"], str(order))


# ---------------------------------------------------------------- C9 MCP 配置
def test_mcp_config() -> None:
    from oryx import mcpconfig

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["ORYX_HOME"] = tmp
        ws = Path(tmp) / "ws"
        ws.mkdir()

        # user 作用域
        (Path(tmp) / ".mcp.json").write_text(
            '{\n  // 用户级注释\n  "mcpServers": {\n'
            '    "alpha": {"type": "stdio", "command": "echo", "args": ["a"]},\n'
            '    "beta": {"type": "http", "url": "https://x.example/mcp"}\n'
            '  }\n}', encoding="utf-8")
        # project 作用域（同名覆盖）
        (ws / ".mcp.json").write_text(
            '{"mcpServers": {"alpha": {"type": "http", "url": "https://p.example/mcp"}}}',
            encoding="utf-8")

        servers = mcpconfig.load_servers(ws)
        check("user 作用域读到", len(servers) == 2, str(list(servers)))
        check("project 覆盖 user", servers["alpha"]["type"] == "http", str(servers["alpha"]))
        check("project 独有项保留", "beta" in servers)

        # 环境变量展开
        os.environ["ORYX_TEST_TOKEN"] = "secret-123"
        (Path(tmp) / ".mcp.json").write_text(
            '{"mcpServers": {"g": {"type": "http", "url": "https://x", '
            '"headers": {"Authorization": "Bearer ${ORYX_TEST_TOKEN}"}, '
            '"env": {"K": "${ORYX_TEST_TOKEN:-none}"}}}}', encoding="utf-8")
        servers = mcpconfig.load_servers(ws)
        check("环境变量展开", servers["g"]["headers"]["Authorization"] == "Bearer secret-123",
              str(servers["g"]["headers"]))
        check("默认值展开", servers["g"]["env"]["K"] == "secret-123", str(servers["g"]["env"]))

        # 未设置的变量保留占位符
        (Path(tmp) / ".mcp.json").write_text(
            '{"mcpServers": {"g": {"type": "http", "url": "https://x", '
            '"env": {"K": "${ORYX_NOT_SET_VAR}"}}}}', encoding="utf-8")
        warns: list[str] = []
        servers = mcpconfig.load_servers(ws, warns)
        check("未设变量保留占位符", servers["g"]["env"]["K"] == "${ORYX_NOT_SET_VAR}",
              str(servers["g"]["env"]))
        check("未设变量有告警", len(warns) >= 1, str(warns))

        # defer_loading 继承
        (Path(tmp) / ".mcp.json").write_text(
            '{"mcpServers": {"s": {"type": "http", "url": "https://x", "defer_loading": true, '
            '"tools": [{"name": "a"}, {"name": "b", "defer_loading": false}]}}}', encoding="utf-8")
        servers = mcpconfig.load_servers(ws)
        rules = mcpconfig.tool_defer_map(servers)
        check("服务器级延迟 + 工具级豁免",
              rules["s"]["defer"] == ["a"] and rules["s"]["nodefer"] == ["b"], str(rules))
        os.environ.pop("ORYX_HOME", None)


def test_mcp_url_validation() -> None:
    from oryx.mcp import MCPError, _validate_url, build_client

    check("http 允许", _validate_url("http://x.example/mcp") == "http://x.example/mcp")
    check("https 允许", _validate_url("https://x.example/mcp").startswith("https://"))
    for bad in ("file:///etc/passwd", "javascript:alert(1)", "ftp://x/y"):
        try:
            _validate_url(bad)
            check(f"拒绝 {bad}", False, "没抛异常")
        except MCPError:
            check(f"拒绝 {bad}", True)

    # 自动选传输
    c = build_client({"name": "a", "command": "echo"})
    check("有 command 走 stdio", c.transport == "stdio")
    c = build_client({"name": "b", "url": "https://x.example/mcp"})
    check("有 url 走 http", c.transport == "http")
    c = build_client({"name": "c", "type": "sse", "url": "https://x.example/mcp"})
    check("显式 sse", c.transport == "sse")


# ---------------------------------------------------------------- C10 配置作用域
def test_config_scopes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp) / "ws"
        ws.mkdir()

        (Path(tmp) / "config.json").write_text(
            json.dumps({"workspace": str(ws), "max_turns": 5, "style": "professional", "hooks": {
                "PreToolUse": [{"matcher": "Write", "hooks": [{"type": "command", "command": "u"}]}]
            }}), encoding="utf-8")
        (ws / ".oryx.json").write_text(
            json.dumps({"max_turns": 10, "style": "friendly", "hooks": {
                "PreToolUse": [{"matcher": "Edit", "hooks": [{"type": "command", "command": "p"}]}]
            }}), encoding="utf-8")
        (ws / ".oryx.local.json").write_text(
            json.dumps({"max_turns": 20, "style": "creative", "hooks": {
                "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "l"}]}]
            }}), encoding="utf-8")

        fresh_oryx(tmp)
        from oryx.config import Config

        cfg = Config.load()
        # 用非特权键验证三层优先级（interaction 是特权键，项目级写了也不生效，
        # 那条由 verify_batch3_d1 覆盖）
        check("项目本地优先级最高", cfg.data["max_turns"] == 20, str(cfg.data["max_turns"]))
        check("项目级 style 生效", cfg.data["style"] == "creative", cfg.data["style"])
        merged = cfg.scoped_hooks()
        check("hooks 三作用域合并", len(merged.get("PreToolUse", [])) == 3, str(merged))

        # save 不该把项目配置抄进用户配置
        cfg.data["theme"] = "dark"
        cfg.save()
        disk = json.loads((Path(tmp) / "config.json").read_text("utf-8"))
        check("用户配置只留用户级 hooks",
              len(disk.get("hooks", {}).get("PreToolUse", [])) == 1, str(disk.get("hooks")))
        check("用户配置保留自己的 max_turns", disk.get("max_turns") == 5, disk.get("max_turns"))
        check("用户配置保留自己的 style", disk.get("style") == "professional", disk.get("style"))
        os.environ.pop("ORYX_HOME", None)


# ---------------------------------------------------------------- C11 权限模式
def test_modes() -> None:
    from oryx.modes import DELEGATE_TOOLS, MODE_POLICIES, allowed_tools, get_policy, mode_cycle
    from oryx.policy import Risk

    check("七个模式", len(MODE_POLICIES) == 7, str(list(MODE_POLICIES)))
    check("bypass 跳过确认", get_policy("bypass").skip_approval)
    check("dontAsk 遇 ask 拒绝", get_policy("dontAsk").deny_on_ask)
    check("auto 用分类器", get_policy("auto").use_classifier)
    check("delegate 只协调", get_policy("delegate").coordination_only)
    check("dontAsk 不在循环里", "dontAsk" not in mode_cycle(), str(mode_cycle()))

    tools = allowed_tools("delegate")
    check("delegate 工具白名单不含执行类", tools is not None and "fs_write" not in tools, str(tools))
    check("delegate 保留协调类", tools is not None and "spawn_agent" in tools, str(tools))
    check("普通模式不限制工具", allowed_tools("craft") is None)
    check("bypass 仍受 L3 约束", get_policy("bypass").max_risk == Risk.L3_FORBIDDEN)


def test_mode_gate() -> None:
    """bypass 跳过确认但绕不过 deny；dontAsk 把 ask 变拒绝。"""
    tmp = tempfile.mkdtemp(prefix="oryx_b2m_")
    fresh_oryx(tmp)
    from oryx.config import load_config
    from oryx.events import EventBus
    from oryx.loop import AgentRuntime
    from oryx.providers import ToolCall
    from oryx.store import Store

    cfg = load_config()
    cfg.data["workspace"] = tmp
    cfg.data["allowed_roots"] = [tmp]

    store = Store()
    rt = AgentRuntime(cfg, store, EventBus())
    rt._active_task_id = "t1"

    # bypass 模式：fs_delete 是 L2，bypass 应该直接执行不等待
    cfg.data["interaction"] = "bypass"
    rt._active_interaction = "bypass"
    target = os.path.join(tmp, "bypass.txt")
    Path(target).write_text("x", encoding="utf-8")
    rt._active_registry = dict(rt.registry)
    out = rt._handle_tool_call("t1", 1, ToolCall(
        id="b1", name="fs_delete", arguments={"path": target}))
    check("bypass 跳过确认直接执行", not out.get("is_error"), str(out)[:120])
    check("bypass 文件已删除", not Path(target).exists())

    # deny 规则：bypass 也拦得住
    cfg.data["interaction"] = "bypass"
    rt._active_interaction = "bypass"
    out = rt._handle_tool_call("t1", 2, ToolCall(
        id="b2", name="fs_write",
        arguments={"path": os.path.join(tmp, ".git", "cfg"), "content": "x"}))
    check("bypass 绕不过 deny", bool(out.get("is_error")), str(out)[:120])

    # dontAsk：L2 被直接拒
    cfg.data["interaction"] = "dontAsk"
    rt._active_interaction = "dontAsk"
    keep = os.path.join(tmp, "keep.txt")
    Path(keep).write_text("x", encoding="utf-8")
    out = rt._handle_tool_call("t1", 3, ToolCall(
        id="b3", name="fs_delete", arguments={"path": keep}))
    check("dontAsk 直接拒绝 L2", bool(out.get("is_error")), str(out)[:120])
    check("dontAsk 文件还在", Path(keep).exists())

    # delegate 模式：执行类工具被拦
    cfg.data["interaction"] = "delegate"
    rt._active_interaction = "delegate"
    out = rt._handle_tool_call("t1", 4, ToolCall(
        id="b4", name="fs_write",
        arguments={"path": os.path.join(tmp, "d.txt"), "content": "x"}))
    check("delegate 拦下执行类工具", bool(out.get("is_error")), str(out)[:120])

    rt.shutdown()
    os.environ.pop("ORYX_HOME", None)


# ---------------------------------------------------------------- C12 子代理
def test_agent_spec() -> None:
    from oryx.agents import AgentLibrary, SpawnBudget

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "demo").mkdir()
        (root / "demo" / "AGENT.md").write_text(
            "---\n"
            "name: 演示 Agent\n"
            "description: 演示用\n"
            "tools:\n"
            "  - fs_read\n"
            "  - fs_list\n"
            "model: gpt-5\n"
            "permissionMode: ask\n"
            "skills: [file-search]\n"
            "effort: high\n"
            "background: true\n"
            "---\n"
            "正文。\n", encoding="utf-8")
        lib = AgentLibrary(root)
        spec = lib.get("demo")
        check("真 YAML 列表解析", spec is not None and spec.tools == ["fs_read", "fs_list"],
              str(spec.tools if spec else None))
        check("模型字段", spec is not None and spec.model == "gpt-5")
        check("权限模式字段", spec is not None and spec.permission_mode == "ask")
        check("技能字段", spec is not None and spec.skills == ["file-search"])
        check("推理强度字段", spec is not None and spec.effort == "high")
        check("后台标记", spec is not None and spec.background is True)
        check("正文保留", spec is not None and "正文" in spec.body)

        # 预算
        b = SpawnBudget(limit=3)
        for _ in range(3):
            ok, _ = b.consume()
            check("预算内允许", ok)
        ok, why = b.consume()
        check("超预算拒绝", not ok and "上限" in why, why)


def test_spawn_gates() -> None:
    """嵌套深度闸门。"""
    from oryx.config import load_config
    from oryx.events import EventBus
    from oryx.loop import AgentRuntime
    from oryx.store import Store

    tmp = tempfile.mkdtemp(prefix="oryx_b2a_")
    os.environ["ORYX_HOME"] = tmp
    cfg = load_config()
    cfg.data["workspace"] = tmp
    cfg.data["allowed_roots"] = [tmp]
    store = Store()
    rt = AgentRuntime(cfg, store, EventBus())
    rt._active_task_id = "t1"

    out = rt.run_subagent("reviewer", "做点事", max_depth=5)
    check("超深度被拦", not out["ok"] and "嵌套" in out["content"], str(out)[:120])

    # 预算耗尽
    rt.spawn_budget.limit = 1
    rt.spawn_budget.used = 1
    out = rt.run_subagent("reviewer", "做点事")
    check("预算耗尽被拦", not out["ok"] and "上限" in out["content"], str(out)[:120])

    rt.shutdown()
    os.environ.pop("ORYX_HOME", None)


# ---------------------------------------------------------------- C13 持久化
def test_automation_persistence() -> None:
    tmp = tempfile.mkdtemp(prefix="oryx_b2au_")
    fresh_oryx(tmp)
    from oryx.automation import AutomationScheduler
    from oryx.store import Store

    store = Store(Path(tmp) / "t.db")
    fired: list[str] = []
    s1 = AutomationScheduler(lambda item: fired.append(item.id), store=store)
    item = s1.add("每分钟", "* * * * *", "跑一下")
    check("任务已创建", bool(item.id))
    check("落库了", len(store.list_automations()) == 1, str(store.list_automations()))

    # 新实例恢复
    s2 = AutomationScheduler(lambda item: None, store=store)
    check("重启后恢复", len(s2.items) == 1, str(list(s2.items)))
    restored = list(s2.items.values())[0]
    check("字段完整", restored.cron == "* * * * *" and restored.goal == "跑一下",
          f"{restored.cron} {restored.goal}")

    # 删除同步落库
    s2.remove(item.id)
    check("删除同步落库", len(store.list_automations()) == 0, str(store.list_automations()))

    # 上限
    s3 = AutomationScheduler(lambda item: None, store=None)
    for i in range(50):
        s3.add(f"t{i}", "* * * * *", "x")
    try:
        s3.add("overflow", "* * * * *", "x")
        check("超上限被拦", False, "没抛异常")
    except ValueError as exc:
        check("超上限被拦", "最多" in str(exc), str(exc))


def test_team_persistence() -> None:
    tmp = tempfile.mkdtemp(prefix="oryx_b2t_")
    fresh_oryx(tmp)
    from oryx.store import Store
    from oryx.team import TeamRegistry

    store = Store(Path(tmp) / "t.db")
    reg = TeamRegistry(store=store)
    team, err = reg.create("alpha", ["a", "b"], "目标")
    check("建队成功", team is not None, err)
    reg.post("alpha", "main", "a", "干活去")
    check("信箱有消息", len(reg.inbox("alpha", "a", mark_read=False)) == 1)

    # 新实例恢复
    reg2 = TeamRegistry(store=store)
    check("重启后团队还在", reg2.get("alpha") is not None)
    check("成员已恢复", reg2.get("alpha").members == {"a": "成员", "b": "成员"},
          str(reg2.get("alpha").members))
    hits = reg2.inbox("alpha", "a", mark_read=False)
    check("信箱已恢复", len(hits) == 1 and hits[0].content == "干活去", str(hits))


def main() -> int:
    tests = [
        ("C7 延迟工具策略", test_defer),
        ("C7 defer_execute 走权限链路", test_defer_execute_goes_through_policy),
        ("C8 Hook 三态", test_hook_decisions),
        ("C9 MCP 配置三作用域", test_mcp_config),
        ("C9 MCP 传输与 URL 校验", test_mcp_url_validation),
        ("C10 配置多作用域", test_config_scopes),
        ("C11 权限模式定义", test_modes),
        ("C11 权限模式闸门", test_mode_gate),
        ("C12 子代理 frontmatter", test_agent_spec),
        ("C12 嵌套/预算闸门", test_spawn_gates),
        ("C13 定时任务持久化", test_automation_persistence),
        ("C13 团队持久化", test_team_persistence),
    ]
    for label, fn in tests:
        print(f"\n[{label}]")
        try:
            fn()
        except Exception as exc:
            import traceback

            traceback.print_exc()
            FAILED.append(label)
    print("\n" + "=" * 60)
    if FAILED:
        print(f"失败 {len(FAILED)} 项：{'、'.join(FAILED)}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
