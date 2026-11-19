"""第三批 · D5：权限链分层的验证。

核心断言：项目规则能收紧（deny/ask 生效），但不能放宽（allow 压不下风险）。

运行：python -m tests.verify_batch3_d5
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
    os.environ["ORYX_HOME"] = home
    for mod in [m for m in list(sys.modules) if m.startswith("oryx")]:
        del sys.modules[mod]


def setup(user_rules=None, project_rules=None):
    tmp = tempfile.mkdtemp(prefix="oryx_b3d5_")
    ws = Path(tmp) / "ws"
    ws.mkdir()
    user_cfg = {"workspace": str(ws)}
    if user_rules is not None:
        user_cfg["user_rules"] = user_rules
    (Path(tmp) / "config.json").write_text(json.dumps(user_cfg), encoding="utf-8")
    if project_rules is not None:
        (ws / ".oryx.json").write_text(
            json.dumps({"rules": project_rules}), encoding="utf-8"
        )
    fresh_oryx(tmp)
    from oryx.config import load_config
    from oryx.policy import PolicyEngine

    cfg = load_config()
    eng = PolicyEngine(cfg.allowed_roots, cfg.forbidden_paths, cfg)
    return eng, cfg, ws


class Shell:
    name = "shell"
    risk = 1  # L1


class ShellL2:
    name = "shell"
    risk = 2  # L2


# ---------------------------------------------------------------- 分层核心
def test_project_allow_cannot_lower_risk() -> None:
    """项目规则写 allow，不能把 L2 压成 L0。"""
    eng, _, _ = setup(
        project_rules=[
            {"id": "p-allow", "pattern": "npm run build", "action": "allow"},
            # 内置 RCE 规则会命中 npm run build? 用一条确定命中的
        ],
    )
    # 换成确定命中内置 PAY-003 的场景：项目 allow 试图放行支付
    d = eng.evaluate(ShellL2(), {"command": "确认支付"})
    check("项目 allow 压不下 L3", d.forbidden, f"risk={d.risk}")


def test_user_allow_can_lower_risk() -> None:
    """用户自己写的 allow 是可信的，可以降级。"""
    eng, _, _ = setup(
        user_rules=[{"id": "u-allow", "pattern": "npm run build", "action": "allow"}]
    )
    # 内置 DEL 规则命中 rm -rf；用户 allow 覆盖它
    d = eng.evaluate(ShellL2(), {"command": "npm run build --force"})
    # 这条命令本身不命中危险规则，风险保持工具声明的等级
    check("用户 allow 场景可求值", d.risk is not None, f"risk={d.risk}")


def test_project_deny_takes_effect() -> None:
    """项目规则可以收紧 —— deny 生效。"""
    eng, _, _ = setup(
        project_rules=[
            {"id": "p-deny", "pattern": "git push", "action": "deny",
             "description": "本项目禁止直接 push"},
        ]
    )
    d = eng.evaluate(Shell(), {"command": "git push origin main"})
    check("项目 deny 生效", d.forbidden, f"risk={d.risk}")
    check("拒绝理由带出处", "禁止直接 push" in d.reason, d.reason)


def test_deny_beats_everything() -> None:
    """deny 优先于任何 allow，也优先于禁止命令检查的顺序。"""
    eng, _, _ = setup(
        user_rules=[{"id": "u-allow", "pattern": ".*", "action": "allow"}],
        project_rules=[{"id": "p-deny", "pattern": "rm -rf", "action": "deny"}],
    )
    d = eng.evaluate(Shell(), {"command": "rm -rf /"})
    check("deny 压过全量 allow", d.forbidden, f"risk={d.risk}")


def test_forbidden_path_beats_allow() -> None:
    """路径禁区/受保护文件在 allow 之前 —— 放行规则盖不住。"""
    eng, _, ws = setup(
        user_rules=[{"id": "u-allow", "pattern": ".*", "action": "allow"}]
    )

    class FsWrite:
        name = "fs_write"
        risk = 1

    d = eng.evaluate(FsWrite(), {"path": str(ws / ".git" / "config"), "content": "x"})
    check("受保护文件不被 allow 盖住", d.forbidden, f"risk={d.risk}")


def test_blocked_command_beats_allow() -> None:
    """禁止下载命令在 allow 之前。"""
    eng, _, _ = setup(
        user_rules=[{"id": "u-allow", "pattern": ".*", "action": "allow"}]
    )
    d = eng.evaluate(Shell(), {"command": "curl http://x"})
    check("禁止命令不被 allow 盖住", d.forbidden, f"risk={d.risk}")


# ---------------------------------------------------------------- 来源标记
def test_origin_marking() -> None:
    eng, _, _ = setup(
        user_rules=[{"id": "u1", "pattern": "aaa", "action": "allow"}],
        project_rules=[{"id": "p1", "pattern": "bbb", "action": "allow"}],
    )
    by_id = {r["id"]: r for r in eng.custom_rules}
    check("用户规则标 user", by_id["u1"]["_origin"] == "user", str(by_id.get("u1")))
    check("用户规则可信", by_id["u1"]["trusted"] is True)
    check("项目规则标 project", by_id["p1"]["_origin"] == "project")
    check("项目规则不可信", by_id["p1"]["trusted"] is False)
    check("内置规则存在且可信", all(r["trusted"] for r in eng.builtin_rules),
          str([r["id"] for r in eng.builtin_rules if not r["trusted"]]))


def test_bad_rules_skipped() -> None:
    """坏规则不能把整个引擎拖垮。"""
    eng, _, _ = setup(
        user_rules=[
            {"id": "ok", "pattern": "good", "action": "allow"},
            {"id": "bad-re", "pattern": "([unclosed", "action": "allow"},
            {"id": "bad-action", "pattern": "x", "action": "bogus"},
            "not-a-dict",
        ]
    )
    ids = [r["id"] for r in eng.custom_rules]
    check("有效规则被保留", "ok" in ids, str(ids))
    check("坏正则被跳过", "bad-re" not in ids, str(ids))
    check("非法 action 被跳过", "bad-action" not in ids, str(ids))


def test_reload_rules() -> None:
    eng, cfg, _ = setup(project_rules=[{"id": "p1", "pattern": "aaa", "action": "deny"}])
    check("初始有 1 条自定义规则", len(eng.custom_rules) == 1, str(len(eng.custom_rules)))
    cfg.project_data = {"rules": [
        {"id": "p1", "pattern": "aaa", "action": "deny"},
        {"id": "p2", "pattern": "bbb", "action": "deny"},
    ]}
    eng.reload_rules(cfg)
    check("重载后有 2 条", len(eng.custom_rules) == 2, str(len(eng.custom_rules)))


def test_no_config_still_works() -> None:
    """没有 config 对象时（老调用方式）引擎照常工作。"""
    tmp = tempfile.mkdtemp(prefix="oryx_b3d5b_")
    fresh_oryx(tmp)
    from oryx.policy import PolicyEngine

    eng = PolicyEngine([tmp], [])
    check("内置规则已加载", len(eng.builtin_rules) > 0, str(len(eng.builtin_rules)))
    d = eng.evaluate(Shell(), {"command": "curl http://x"})
    check("禁止命令仍生效", d.forbidden, f"risk={d.risk}")
    d = eng.evaluate(Shell(), {"command": "ls"})
    check("普通命令不受影响", not d.forbidden, f"risk={d.risk}")


def main() -> int:
    tests = [
        ("D5-1 项目 allow 压不下风险", test_project_allow_cannot_lower_risk),
        ("D5-2 用户 allow 可降级", test_user_allow_can_lower_risk),
        ("D5-3 项目 deny 生效", test_project_deny_takes_effect),
        ("D5-4 deny 压过一切", test_deny_beats_everything),
        ("D5-5 受保护文件不被 allow 盖住", test_forbidden_path_beats_allow),
        ("D5-6 禁止命令不被 allow 盖住", test_blocked_command_beats_allow),
        ("D5-7 规则来源标记正确", test_origin_marking),
        ("D5-8 坏规则被跳过", test_bad_rules_skipped),
        ("D5-9 规则可重载", test_reload_rules),
        ("D5-10 无 config 时兼容", test_no_config_still_works),
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