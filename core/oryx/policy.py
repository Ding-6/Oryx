"""策略引擎：把每一次工具调用判定成 L0-L3，并为高风险动作生成执行计划。

设计原则（对齐 Marvis 的信任模型）：
- L0 只读，直接执行；
- L1 可逆写入，执行但留痕；
- L2 破坏性/系统级，必须先出「执行计划」等用户确认；
- L3 支付与凭据，永不代执行。

判定顺序：工具声明的风险 → 路径禁区 → 受保护文件 → 危险命令规则 → 升级（取最高级）。

受保护文件与禁止下载命令参照 WorkBuddy 的做法：仓库里的版本控制、shell 配置、
凭据文件一律不碰，下载命令默认拦下——它们是供应链攻击的常见入口。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, Iterable

import yaml

RULE_DIR = Path(__file__).parent / "rules"


# 受保护的文件/目录名。命中即 L3 硬拒绝，不接受模式放行。
# 这些位置一旦被改写，攻击者就能在下次启动时接管整个 Agent。
# 清单对齐 WorkBuddy 的受保护路径：版本控制内部、shell 配置、
# 包管理凭据、IDE 配置。
PROTECTED_NAMES = frozenset({
    # 版本控制
    ".git", ".gitconfig", ".gitmodules",
    # shell 配置（可注入命令）
    ".bashrc", ".bash_profile", ".zshrc", ".zprofile", ".profile", ".envrc",
    # 包管理器凭据
    ".npmrc", ".yarnrc", ".pnpmrc", ".netrc", ".git-credentials",
    # IDE / 钩子配置
    ".vscode", ".idea", ".husky",
    # Agent 自身配置
    ".oryx", ".codebuddy", ".mcp.json",
    # 密钥文件
    "id_rsa", "id_ed25519", "credentials",
})

# 凭据类文件/目录：默认**不碰**，但允许用户显式授权。
#
# 为什么不是 L3 硬拒绝：主控提示词的绝对红线写的是
# 「不读取或修改凭据类敏感文件（.env / .ssh / .aws / .kube），
#   **除非用户明确授权并说明用途**」。
# L3 是任何模式都放行不了的（bypass 也不行），那样用户连
# 「看一下我的 .env」这句话都兑现不了 —— 规则比需求更硬。
#
# 所以走 L2：进确认通道，由用户点「授权」放行。
# 确认卡片会明说「这个文件里是密钥，授权后模型能看到（值仍会打码）」。
#
# 与 PROTECTED_NAMES 的分工：
#   PROTECTED_NAMES   改写后会被利用来接管 Agent（.gitconfig / .npmrc）→ L3 永不
#   CREDENTIAL_NAMES 装着密钥但改写不构成接管（.env / .ssh）→ L2 需用户授权
CREDENTIAL_NAMES = frozenset({
    # 环境变量与云凭据目录
    ".env",
    ".ssh",
    ".aws",
    ".kube",
    ".gnupg",
    ".docker",
    # 单文件凭据
    ".pgpass",
    ".htpasswd",
    ".pypirc",
})

# `.env` 家族：`.env`、`.env.local`、`.env.production`… 一律按凭据处理。
# 用前缀匹配而不是把每种变体都列进 CREDENTIAL_NAMES —— 变体是无限的。
CREDENTIAL_PREFIXES = (".env.",)

# 受保护目录的例外：worktrees 目录里的同名目录是工作区副本，可以改。
_PROTECTED_EXEMPT_DIRS = frozenset({"worktrees", "worktree"})

# 默认禁止的下载命令。网络出口是最难事后审计的通道。
BLOCKED_DOWNLOAD_COMMANDS = frozenset({
    "curl", "wget", "Invoke-WebRequest", "iwr", "certutil",
})

# 危险子命令：即使前面套了别的命令也要拦。
BLOCKED_SUB_COMMANDS = frozenset({
    "sudo", "doas", "eval", "exec", "iex", "Invoke-Expression",
})

# 复合命令分隔符：逐段判定时按这些切开。
_COMMAND_SEPARATORS = re.compile(r"(?:\|\||&&|[;&|])")


def _protected_hit(target: Path) -> str | None:
    """检查路径里有没有受保护的组件，返回命中的组件名。

    worktrees 目录是例外：里面是工作区副本，.git 之类可以正常读写。
    """
    parts = [p.lower() for p in target.parts]
    in_worktree = False
    for part in parts:
        if in_worktree:
            # 已经在 worktrees 里，后面出现什么都放行
            continue
        if part in _PROTECTED_EXEMPT_DIRS:
            in_worktree = True
            continue
        if part in PROTECTED_NAMES:
            return part
    return None


def _credential_hit(target: Path) -> str | None:
    """检查路径里有没有凭据类组件，返回命中的组件名。

    与 _protected_hit 同构，但判定的是「装着密钥」而不是「改写会被利用」。
    worktrees 例外同样适用。
    """
    parts = [p.lower() for p in target.parts]
    in_worktree = False
    for part in parts:
        if in_worktree:
            continue
        if part in _PROTECTED_EXEMPT_DIRS:
            in_worktree = True
            continue
        if part in CREDENTIAL_NAMES:
            return part
        if part.startswith(CREDENTIAL_PREFIXES):
            return part
    return None


def is_credential_path(path: str | Path) -> bool:
    """这个路径是不是凭据类文件/目录。

    给**按目录遍历**的工具用（fs_grep / search_files / 索引器）：
    策略引擎只看得到工具的入参（root / path），看不到遍历过程中
    撞上的每一个文件 —— `fs_grep(root="项目目录", pattern="SECRET")`
    会把目录里 `.env` 的内容读出来，而 root 本身不长得像凭据文件。

    所以遍历型工具要在**每个文件**上再判一次。
    """
    try:
        target = Path(path)
    except Exception:
        return False
    return _credential_hit(target) is not None


class Risk(IntEnum):
    L0_READ = 0
    L1_WRITE = 1
    L2_DESTRUCTIVE = 2
    L3_FORBIDDEN = 3

    @property
    def label(self) -> str:
        return {
            Risk.L0_READ: "只读",
            Risk.L1_WRITE: "可逆写入",
            Risk.L2_DESTRUCTIVE: "破坏性操作",
            Risk.L3_FORBIDDEN: "禁止操作",
        }[self]


@dataclass
class Finding:
    rule_id: str
    category: str
    description: str
    risk: Risk


@dataclass
class PlannedAction:
    kind: str
    target: str
    detail: str
    risk: Risk


@dataclass
class ExecutionPlan:
    """L2 动作执行前交给用户确认的计划。"""
    tool: str
    actions: list[PlannedAction]
    summary: str
    reversible: bool
    findings: list[Finding] = field(default_factory=list)
    # 人类可读的「将要发生什么」清单。
    # actions 是结构化事实（给程序看），steps 是给人看的说明（给用户看）——
    # 前者说「删除 D:\\x」，后者说「删除 D:\\x 下 12 个文件，不可撤销」。
    steps: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "summary": self.summary,
            "reversible": self.reversible,
            "actions": [
                {
                    "kind": a.kind,
                    "target": a.target,
                    "detail": a.detail,
                    "risk": int(a.risk),
                    "risk_label": a.risk.label,
                }
                for a in self.actions
            ],
            "steps": list(self.steps),
            "findings": [
                {
                    "rule_id": f.rule_id,
                    "category": f.category,
                    "description": f.description,
                    "risk": int(f.risk),
                }
                for f in self.findings
            ],
        }


@dataclass
class Decision:
    risk: Risk
    findings: list[Finding] = field(default_factory=list)
    plan: ExecutionPlan | None = None
    reason: str = ""

    @property
    def needs_approval(self) -> bool:
        return self.risk == Risk.L2_DESTRUCTIVE

    @property
    def forbidden(self) -> bool:
        return self.risk >= Risk.L3_FORBIDDEN


_CATEGORY_RISK = {
    "RCE": Risk.L2_DESTRUCTIVE,
    "NET": Risk.L2_DESTRUCTIVE,
    "DEL": Risk.L2_DESTRUCTIVE,
    "SYS": Risk.L2_DESTRUCTIVE,
    "PAY": Risk.L3_FORBIDDEN,
}


def _load_rules() -> list[dict[str, Any]]:
    """加载内置规则（随代码分发，视为可信）。"""
    rules: list[dict[str, Any]] = []
    if not RULE_DIR.exists():
        return rules
    for path in sorted(RULE_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text("utf-8")) or {}
        except Exception:
            continue
        for rule in data.get("rules", []):
            try:
                rule["_re"] = re.compile(rule["pattern"], re.IGNORECASE)
            except re.error:
                continue
            # 内置规则默认可信；yaml 里可以显式写 trusted: false
            rule.setdefault("trusted", True)
            rule["_origin"] = "builtin"
            rules.append(rule)
    return rules


# 规则动作
ACTION_ALLOW = "allow"
ACTION_ASK = "ask"
ACTION_DENY = "deny"



def _load_user_rules(config: Any) -> list[dict[str, Any]]:
    """从配置里加载用户规则与项目规则。

    用户级（config.json 的 user_rules）标 trusted；
    项目级（.oryx.json 的 rules）标 untrusted。
    """
    rules: list[dict[str, Any]] = []
    if config is None:
        return rules

    def _add(items: Any, origin: str) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict) or not item.get("pattern"):
                continue
            action = str(item.get("action") or ACTION_ASK).lower()
            if action not in (ACTION_ALLOW, ACTION_ASK, ACTION_DENY):
                continue
            try:
                compiled = re.compile(str(item["pattern"]), re.IGNORECASE)
            except re.error:
                continue
            rules.append({
                "_re": compiled,
                "id": str(item.get("id") or f"{origin}-{len(rules)}"),
                "category": str(item.get("category") or "SYS"),
                "description": str(item.get("description") or ""),
                "action": action,
                "_origin": origin,
                # 项目规则不可信 —— 这是整套分层里最要紧的一条
                "trusted": origin != "project",
            })

    data = getattr(config, "data", {}) or {}
    _add(data.get("user_rules"), "user")
    _add(getattr(config, "project_data", {}).get("rules"), "project")
    return rules


class PolicyEngine:
    def __init__(self, allowed_roots: Iterable[str], forbidden_paths: Iterable[str],
                 config: Any = None):
        self.allowed_roots = [Path(p).expanduser() for p in allowed_roots]
        self.forbidden_paths = [Path(p).expanduser() for p in forbidden_paths]
        # 内置规则（可信）
        self.builtin_rules = _load_rules()
        # 用户规则 + 项目规则（后者不可信）
        self.custom_rules = _load_user_rules(config)
        self.rules = self.builtin_rules + self.custom_rules

    def reload_rules(self, config: Any = None) -> None:
        """配置变了以后重载规则。"""
        self.builtin_rules = _load_rules()
        self.custom_rules = _load_user_rules(config)
        self.rules = self.builtin_rules + self.custom_rules

    # ---------- 路径判定 ----------
    def _norm(self, path: str) -> Path:
        try:
            return Path(os.path.expandvars(os.path.expanduser(path))).resolve()
        except Exception:
            return Path(path)

    def path_forbidden(self, path: str) -> bool:
        target = self._norm(path)
        for f in self.forbidden_paths:
            try:
                if target == f or f in target.parents:
                    return True
            except Exception:
                continue
        return False

    def path_outside_roots(self, path: str) -> bool:
        target = self._norm(path)
        for root in self.allowed_roots:
            try:
                if target == root or root in target.parents:
                    return False
            except Exception:
                continue
        return True

    # ---------- 命令扫描 ----------
    def scan_text(self, text: str) -> list[Finding]:
        findings: list[Finding] = []
        if not text:
            return findings
        for rule in self.rules:
            if not rule["_re"].search(text):
                continue
            cat = rule.get("category", "SYS")
            # 有 action 的自定义规则按 action 给风险；内置规则按 category
            action = rule.get("action")
            if action == ACTION_ALLOW:
                risk = Risk.L0_READ
            elif action == ACTION_DENY:
                risk = Risk.L3_FORBIDDEN
            else:
                risk = _CATEGORY_RISK.get(cat, Risk.L2_DESTRUCTIVE)
            findings.append(
                Finding(
                    rule_id=rule.get("id", "?"),
                    category=cat,
                    description=rule.get("description", ""),
                    risk=risk,
                )
            )
        return findings

    def _trusted_findings(self, findings: list[Finding]) -> list[Finding]:
        """筛出可信规则命中的部分。

        不可信（项目级）规则的 allow 不能用来降低风险等级 ——
        它只能"保持原判"或"往上抬"，不能往下压。
        """
        allowed_ids = {
            r["id"] for r in self.custom_rules
            if not r.get("trusted") and r.get("action") == ACTION_ALLOW
        }
        if not allowed_ids:
            return findings
        return [f for f in findings if f.rule_id not in allowed_ids]

    def _deny_rules(self) -> list[dict[str, Any]]:
        return [r for r in self.rules if r.get("action") == ACTION_DENY]

    def _has_deny_match(self, text: str) -> dict[str, Any] | None:
        """deny 规则优先于一切 —— 命中就是拒绝，allow 也盖不住。"""
        for rule in self._deny_rules():
            if rule["_re"].search(text):
                return rule
        return None

    def _escalate(self, current: Risk, finding_risks: Iterable[Risk]) -> Risk:
        risk = current
        for r in finding_risks:
            risk = max(risk, r)
        return risk

    # ---------- 受保护文件 / 禁止命令 ----------
    def protected_path(self, path: str) -> str | None:
        """返回命中的受保护组件名；没命中返回 None。"""
        try:
            target = self._norm(path)
        except Exception:
            return None
        return _protected_hit(target)

    def credential_path(self, path: str) -> str | None:
        """返回命中的凭据类组件名；没命中返回 None。

        命中不等于拒绝 —— 它把风险抬到 L2，让它进确认通道由用户授权。
        """
        try:
            target = self._norm(path)
        except Exception:
            return None
        return _credential_hit(target)

    def scan_command(self, command: str) -> list[Finding]:
        """扫命令里的禁止下载命令与危险子命令。

        复合命令按 && || ; | 切开逐段判定——一段命中就算命中。
        """
        findings: list[Finding] = []
        if not command:
            return findings
        for segment in _COMMAND_SEPARATORS.split(command):
            tokens = segment.strip().split()
            if not tokens:
                continue
            head = tokens[0].strip('"\'@').lower()
            # head 可能带路径，取最后一段
            head = head.replace("\\", "/").rsplit("/", 1)[-1]
            if head in {c.lower() for c in BLOCKED_DOWNLOAD_COMMANDS}:
                findings.append(
                    Finding(
                        rule_id="NET-DOWNLOAD",
                        category="NET",
                        description=f"禁止使用下载命令：{head}",
                        risk=Risk.L3_FORBIDDEN,
                    )
                )
            for token in tokens[1:]:
                base = token.strip('"\'@').replace("\\", "/").rsplit("/", 1)[-1].lower()
                if base in {c.lower() for c in BLOCKED_SUB_COMMANDS}:
                    findings.append(
                        Finding(
                            rule_id="SYS-ELEVATE",
                            category="SYS",
                            description=f"禁止使用提权/动态执行命令：{base}",
                            risk=Risk.L3_FORBIDDEN,
                        )
                    )
                    break
        return findings

    # ---------- 主入口 ----------
    def evaluate(self, tool_spec, args: dict[str, Any]) -> Decision:
        """tool_spec 需带 .risk 与 .name。

        求值次序（对齐 WorkBuddy 的分层权限链）：
            0. deny 规则        —— 命中即拒，任何 allow 都盖不住
            1. 路径禁区/受保护  —— L3，模式放行不了
            2. 禁止命令          —— L3
            3. 可信规则降级      —— 只有可信来源的 allow 能压低风险
            4. 越界路径          —— 升到 L2
            5. 取最高级
        """
        base: Risk = tool_spec.risk
        findings: list[Finding] = []
        reason_bits: list[str] = []

        # 1. 命令/文本规则（浏览器点击、输入的内容同样扫描，防止点"确认支付"）
        text_fields = [
            str(v)
            for k, v in args.items()
            if k in ("command", "cmd", "content", "query", "text", "value", "selector", "target")
        ]
        scan_text = "\n".join(text_fields)

        # 0. deny 规则优先 —— 先于一切，连不可信来源的 deny 也认
        #    （deny 只会收紧不会放松，接纳不可信来源的 deny 不构成提权）
        denied = self._has_deny_match(scan_text)
        if denied:
            return Decision(
                risk=Risk.L3_FORBIDDEN,
                findings=[Finding(
                    rule_id=denied.get("id", "?"),
                    category=denied.get("category", "SYS"),
                    description=denied.get("description", ""),
                    risk=Risk.L3_FORBIDDEN,
                )],
                reason=f"命中禁止规则：{denied.get('description') or denied.get('id')}",
            )

        findings.extend(self.scan_text(scan_text))

        # 2. 禁止下载命令 / 危险子命令（命中即 L3，不可被模式放行）
        command = args.get("command") or args.get("cmd")
        if isinstance(command, str) and command:
            blocked = self.scan_command(command)
            if blocked:
                return Decision(
                    risk=Risk.L3_FORBIDDEN,
                    findings=findings + blocked,
                    reason="；".join(f.description for f in blocked),
                )

        # 3. 路径检查：禁区 → 受保护文件 → 凭据文件 → 越界
        credential_paths: list[str] = []
        for key in ("path", "src", "dst", "root"):
            value = args.get(key)
            if not isinstance(value, str) or not value:
                continue
            if self.path_forbidden(value):
                return Decision(
                    risk=Risk.L3_FORBIDDEN,
                    findings=findings,
                    reason=f"路径位于禁区：{value}",
                )
            hit = self.protected_path(value)
            if hit:
                return Decision(
                    risk=Risk.L3_FORBIDDEN,
                    findings=findings,
                    reason=f"路径命中受保护文件「{hit}」：{value}",
                )
            # 凭据类文件：升到 L2 让用户授权，而不是直接拒绝。
            # 只读工具也一样 —— 「看一眼 .env」也要用户点头，
            # 因为模型看到的内容就是密钥本身（值会打码，但结构与用途会泄）。
            cred = self.credential_path(value)
            if cred:
                credential_paths.append(value)
                base = max(base, Risk.L2_DESTRUCTIVE)
                reason_bits.append(f"路径命中凭据类文件「{cred}」：{value}")
            if self.path_outside_roots(value):
                base = max(base, Risk.L2_DESTRUCTIVE)
                reason_bits.append(f"路径不在允许范围内：{value}")

        risk = self._escalate(base, [f.risk for f in self._trusted_findings(findings)])
        decision = Decision(risk=risk, findings=findings, reason="；".join(reason_bits))

        if risk == Risk.L2_DESTRUCTIVE:
            decision.plan = self._build_plan(tool_spec, args, findings)
            # 凭据路径要单独标出来 —— 确认卡片上要专门说这一条
            if credential_paths:
                decision.plan.steps.insert(
                    0,
                    {
                        "title": "凭据类文件，需你授权",
                        "detail": "、".join(credential_paths),
                        "impact": (
                            "授权后模型会看到这个文件的内容"
                            "（密钥值本身仍会被打码，但文件结构与用途会进入上下文）"
                        ),
                    },
                )
        return decision

    # ---------- 计划生成 ----------
    def build_plan(
        self, tool_spec, args: dict[str, Any], findings: list[Finding] | None = None
    ) -> ExecutionPlan:
        """生成执行计划（对外接口）。hook 强制要求确认时要用。"""
        return self._build_plan(tool_spec, args, findings or [])

    def _build_plan(
        self, tool_spec, args: dict[str, Any], findings: list[Finding]
    ) -> ExecutionPlan:
        actions: list[PlannedAction] = []
        name = tool_spec.name

        if name == "fs_delete":
            target = str(args.get("path", ""))
            actions.append(
                PlannedAction(
                    kind="删除",
                    target=target,
                    detail=self._describe_delete_target(target),
                    risk=Risk.L2_DESTRUCTIVE,
                )
            )
        elif name == "fs_write":
            target = str(args.get("path", ""))
            size = len(str(args.get("content", "")).encode("utf-8"))
            exists = Path(target).exists() if target else False
            actions.append(
                PlannedAction(
                    kind="覆盖文件" if exists else "新建文件",
                    target=target,
                    detail=f"写入 {size} 字节" + ("（目标已存在，将被覆盖）" if exists else ""),
                    risk=Risk.L1_WRITE,
                )
            )
        elif name in ("shell", "shell_exec"):
            command = str(args.get("command", ""))
            actions.append(
                PlannedAction(
                    kind="执行命令",
                    target="本机 shell",
                    detail=command,
                    risk=Risk.L2_DESTRUCTIVE,
                )
            )
        else:
            actions.append(
                PlannedAction(
                    kind="高风险操作",
                    target=name,
                    detail=str(args)[:500],
                    risk=Risk.L2_DESTRUCTIVE,
                )
            )

        summary = "；".join(f"{a.kind} {a.target}" for a in actions)
        if findings:
            summary += "（命中规则：" + "、".join(f.rule_id for f in findings) + "）"

        return ExecutionPlan(
            tool=name,
            actions=actions,
            summary=summary,
            reversible=name == "fs_write",
            findings=findings,
            steps=self._build_steps(name, actions, findings),
        )

    @staticmethod
    def _build_steps(
        name: str, actions: list[PlannedAction], findings: list[Finding]
    ) -> list[dict[str, str]]:
        """人类可读的「将要发生什么」清单。

        抄 Marvis 的 `net_repair(execute=False)` —— 不执行时返回一份
        步骤说明（`status: "preview"`），用户看得懂再决定。
        这里每步给三样东西：
          title  —— 这一步叫什么
          detail —— 具体做什么（路径、大小、命令）
          impact —— 后果（能不能撤销）
        """
        steps: list[dict[str, str]] = []
        for action in actions:
            if name == "fs_delete":
                impact = "不可撤销"
                detail = action.detail
                if action.target:
                    detail = f"{action.target} —— {action.detail}"
            elif name == "fs_write":
                impact = "可撤销（原内容会被覆盖）"
                detail = f"{action.target} —— {action.detail}"
            elif name in ("shell", "shell_exec"):
                impact = "取决于命令本身"
                detail = action.detail
            else:
                impact = "不可撤销"
                detail = f"{action.target} —— {action.detail}"
            steps.append(
                {
                    "title": action.kind,
                    "detail": detail,
                    "impact": impact,
                }
            )
        # 命中了哪些规则也要说清楚 —— 用户判断「该不该批」靠的就是这个
        for finding in findings:
            steps.append(
                {
                    "title": f"命中规则 {finding.rule_id}",
                    "detail": finding.description,
                    "impact": finding.risk.label,
                }
            )
        return steps

    @staticmethod
    def _describe_delete_target(path: str) -> str:
        p = Path(os.path.expandvars(os.path.expanduser(path)))
        if p.is_dir():
            try:
                count = sum(1 for _ in p.rglob("*"))
            except Exception:
                count = -1
            return f"目录，递归删除（约 {count} 个条目）" if count >= 0 else "目录，递归删除"
        if p.exists():
            try:
                return f"文件，{p.stat().st_size} 字节"
            except Exception:
                return "文件"
        return "目标不存在"
