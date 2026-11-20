r"""配置：多 provider、双模式、权限边界。

配置文件位置：%APPDATA%\Oryx\config.json（首次运行自动生成模板）。

密钥不在配置里裸奔：api_key 优先存系统凭据库（Windows 凭据管理器 /
macOS 钥匙串 / Linux 密钥环），config.json 只留一个引用标记。
读到旧版明文配置时会自动迁移一次，迁移完把明文抹掉。
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any

from . import credentials as cred_store


def app_dir() -> Path:
    """Oryx 的应用数据目录（配置、索引库、模型、粘贴的临时文件都在这儿）。

    **默认落在 D 盘**，不是 C 盘。

    为什么：C 盘经常被塞满，而这个目录会长到好几 GB ——
    index.db 单独就 1.26GB，向量模型 205MB。用户明确要求「所有路径都在 D 盘」。

    找法：
      1. ORYX_HOME 环境变量（桌面端启动时会设成 D:\\OryxData\\data）
      2. OYX_DATA_ROOT 环境变量 → 取它的 data 子目录
      3. 固定位置 D:\\OryxData\\data（D 盘在的话）
      4. 退回 %APPDATA%\\Oryx（没有 D 盘的机器，保证还能用）

    注意第 3 条要检查 D 盘**真的存在** —— 有的机器只有 C 盘，
    硬写到 D:\\OryxData 会直接建目录失败，然后整个应用起不来。
    """
    override = os.environ.get("ORYX_HOME")
    if override:
        return Path(override)
    root = os.environ.get("OYX_DATA_ROOT")
    if root:
        return Path(root) / "data"
    if sys.platform == "win32":
        # 优先 D 盘 —— 用户要的就是这个。
        # 只探测盘符在不在，不在这里建目录：app_dir() 是导入期就算的
        # （CONFIG_PATH = app_dir() / "config.json"），带副作用地建目录
        # 会让「只是 import 一下」也留下痕迹。真正要写的时候再建。
        if Path("D:/").exists():
            return Path("D:/OryxData/data")
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "Oryx"
    return Path.home() / ".oryx"


CONFIG_PATH = app_dir() / "config.json"
FEATURES_PATH = app_dir() / "features.yaml"

# 测试环境下把模块级路径指到临时目录。
#
# 为什么要在模块级做（而不是在 load() 里判断）：测试用 monkeypatch 改的是
# `cfgmod.CONFIG_PATH`，load() 里另算一套路径会把 monkeypatch 架空 ——
# 测试写 tmp_path/config.json，load() 却去读别处，断言必然失败。
#
# 保护的是用户真配置：测试往配置里写 provider / 改 allowed_roots，
# 落回用户真配置就把用户的模型服务改了（这个坑出过事）。
#
# 判据用「pytest 模块在 sys.modules 里」而不是 PYTEST_CURRENT_TEST 环境变量：
# 后者只在单个测试执行期间有值，而模块导入发生在收集阶段 ——
# 那时环境变量还没设，判断会落空。
if "pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST"):
    import tempfile as _tempfile

    _TEST_HOME = Path(_tempfile.gettempdir()) / "oryx-test-store"
    _TEST_HOME.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH = _TEST_HOME / "config.json"
    FEATURES_PATH = _TEST_HOME / "features.yaml"
# 项目级配置（跟着工作区走，可以随仓库分发）
PROJECT_CONFIG_NAME = ".oryx.json"
# 项目本地配置（私有，不该进版本库）
PROJECT_LOCAL_CONFIG_NAME = ".oryx.local.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "active_provider": "anthropic",
    # 本地 provider 由用户自己登记（设置里「添加模型 → 本地模型」）。
    # 不再预置一条写死的 ollama —— 写死的模型名（比如 qwen3:8b）
    # 早就不在本机了，留着只会让「本机已装模型」列表凭空多一条假的。
    "local_provider": "",
    # 云端 provider 单独记一个 —— 切计算模式时按 local/cloud 两个槽位取，
    # 来回切不会莫名其妙换模型。
    "cloud_provider": "",
    # 联网搜索（可选能力）。api_key 里的明文会被搬进系统凭据库
    # —— 跟 providers[*].api_key 一个待遇。
    "search": {
        "provider": "serper",
        "api_key": "",
    },
    "providers": {
        "anthropic": {
            "type": "anthropic",
            "base_url": "https://api.anthropic.com",
            "api_key": "",
            "model": "claude-sonnet-5-5",
        },
        "deepseek": {
            "type": "openai",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "",
            "model": "deepseek-chat",
        },
        # MiniMax 的 Anthropic 兼容端点。
        # 密钥不进仓库 —— 用户在设置里填，密钥走系统凭据库。
        "minimax": {
            "type": "anthropic",
            "base_url": "https://api.minimax.cn/anthropic",
            "api_key": "",
            "model": "MiniMax-M3.1-Flash",
        },
    },
    # 允许 Agent 触碰的根目录；空列表 = 只允许用户主目录
    "allowed_roots": [],
    # 禁区：永远不读不写
    "forbidden_paths": [
        "C:\\Windows",
        "C:\\Program Files",
        "C:\\Program Files (x86)",
        "C:\\ProgramData",
    ],
    "max_turns": 12,
    "theme": "light",
    "background_image": "",
    "ocr": True,
    "embeddings": True,
    "browser_headless": True,
    "interaction": "craft",
    "style": "professional",
    "expert": "",
    "mcp_servers": {},
    "remote_relay": "",
    "remote_fps": 8,
    "workspace": "",
    # ── 扩展系统 ──────────────────────────────────────────────────
    # 技能目录这一段能占多少 token。技能装多了全量列出来就是几千 token
    # 的纯浪费，而且大部分这轮用不上。设 0 = 不限制（全量列出）。
    "skill_catalog_budget": 600,
    # 当前生效的扩展 profile（见 profiles.json）。
    # 空 = 不排除任何扩展；profile 只是**收窄**加载范围。
    "extension_profile": "",
    # 回收区保留天数 —— 删除的扩展在回收区里留多久。
    # 0 = 永不自动清理（只能手动清）。默认 30 天，
    # 与 Windows / macOS 回收站的默认一致。
    "extension_trash_retention_days": 30,
    "host": "127.0.0.1",
    "port": 8765,
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# 只有用户级配置能授予的键。项目级配置写了也一律忽略并回退到用户值。
#
# 道理很直白：项目配置跟着仓库走，分发给别人时会连着代码一起到一个陌生机器上。
# 如果仓库里放个 .oryx.json 就能把 allowed_roots 放宽、interaction 提到 bypass，
# 那"clone 下来打开看看"就等于"把机器交出去了"。
#
# 对齐 WorkBuddy 的规则：defaultMode:auto 只能由 user settings 或 CLI 授予，
# 项目级写了会被忽略并回退 default。
PRIVILEGED_KEYS = frozenset({
    # 权限边界
    "allowed_roots",
    "forbidden_paths",
    "interaction",
    "disable_bypass_mode",
    "mcp_servers",
    # 凭据与连接
    "providers",
    # 选哪个 provider —— 与 providers 同源，必须一起锁住。
    # 否则项目级删掉真 provider、留下自己的，再把 active_provider 指过去即可劫持。
    "active_provider",
    "local_provider",
    "cloud_provider",
    "mode",
    "api_token",
    "mcp_token",
    "remote_relay",
    "host",
    "port",
    # 会被远端触发的能力
    "remote_fps",
    # 委派/子代理可以放宽自身权限的字段
    "defer_spec",
    "defer_tools",
})

# 注意：项目级 rules / user_rules **不在**上面的名单里。
# 项目规则是要生效的，只是走 policy 的「不可信层」——
# 能收紧（deny/ask 照常拦），不能放宽（allow 压不下风险等级）。
# 它由 policy._load_user_rules 单独读取，不走普通配置合并路径。

# 提示：项目配置里写了这些键时打一行告警，让用户知道为什么不生效
_PRIVILEGED_HINT = (
    "[oryx] 项目级配置里的 {keys} 已被忽略 —— 这些只能由用户级配置授予。"
    " 详见 docs/security.md 的「项目配置不能提权」。"
)


def _read_json_file(path: Path) -> dict[str, Any]:
    """读一个 JSON 文件。坏文件返回空字典，不阻塞启动。"""
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        print(f"[oryx] {path.name} 解析失败，已忽略：{exc}", file=sys.stderr)
        return {}


def _is_local_spec(spec: dict[str, Any]) -> bool:
    """这条 provider 配置指的是本机跑的吗？

    判据是 base_url 指向回环地址。不依赖 oryx.localmodels ——
    那个模块会反过来读配置，在这里互相 import 会成环。
    """
    base = str((spec or {}).get("base_url") or "").strip().lower()
    if not base:
        return False
    return any(host in base for host in ("127.0.0.1", "localhost", "::1", "0.0.0.0"))


class Config:
    def __init__(self, data: dict[str, Any], path: Path | None = None):
        self.data = data
        # 落盘路径。只有 load() 读出来的实例才写用户的真实配置；
        # 直接 Config({...}) 构造的实例（测试、临时拼装）落到内存里，
        # save() 不碰磁盘 —— 否则一次测试就能把用户的模型服务整个抹掉。
        self.path = path
        self._mtime: float | None = None
        if path is not None:
            try:
                self._mtime = path.stat().st_mtime
            except OSError:
                self._mtime = None
        # 项目级配置（跟着工作区走）
        self.project_data: dict[str, Any] = {}
        self.project_local_data: dict[str, Any] = {}
        # 用户级 hooks 单独留一份：data["hooks"] 是三作用域合并的结果，
        # 直接写回会把项目配置抄进用户配置。
        self.user_hooks: dict[str, list] = {}

    @classmethod
    def load(cls) -> "Config":
        data = dict(DEFAULT_CONFIG)
        if CONFIG_PATH.exists():
            try:
                data = _deep_merge(DEFAULT_CONFIG, json.loads(CONFIG_PATH.read_text("utf-8")))
            except Exception as exc:  # 配置坏了不应该阻塞启动
                print(f"[oryx] config.json 解析失败，使用默认值：{exc}", file=sys.stderr)
        else:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_PATH.write_text(
                json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), "utf-8"
            )
        cfg = cls(data, path=CONFIG_PATH)
        cfg._load_project_scopes()
        cfg._migrate_plaintext_keys()
        return cfg

    # ---- 项目级作用域 ----
    def _load_project_scopes(self) -> None:
        """读项目级与项目本地配置。

        三层优先级：项目本地 > 项目 > 用户。
        合并方式对齐 WorkBuddy：**hooks 段合并，其余段覆盖**——
        项目配置不该把用户配好的 hook 整块顶掉。
        """
        workspace = str(self.data.get("workspace") or "").strip()
        if not workspace:
            return
        root = Path(workspace)
        self.project_data = _read_json_file(root / PROJECT_CONFIG_NAME)
        self.project_local_data = _read_json_file(root / PROJECT_LOCAL_CONFIG_NAME)
        self._apply_project_scopes()

    def _apply_project_scopes(self) -> None:
        """把项目级配置叠到用户配置上。

        两件事同时做：
        1. hooks 段合并（项目配置不该顶掉用户配好的 hook）
        2. 特权键过滤 —— 项目级写的 allowed_roots / interaction / providers
           一律忽略，用户没配过的回退到默认值。
        """
        # 用户级 hooks 单独存一份，写回时只写这一份
        self.user_hooks = {
            k: v for k, v in (self.data.get("hooks") or {}).items() if isinstance(v, list)
        }
        # 用户级特权键的原值，被项目级覆盖时要恢复回去
        user_privileged = {k: self.data[k] for k in PRIVILEGED_KEYS if k in self.data}

        rejected: set[str] = set()
        for scope in (self.project_data, self.project_local_data):
            if not scope:
                continue
            hooks = scope.get("hooks")
            # rules 不走普通合并：它由 policy 单独读，走「不可信层」求值。
            # 混进 data 会被读两遍，且绕过分层逻辑。
            rest = {
                k: v for k, v in scope.items()
                if k not in ("hooks", "rules", "user_rules")
            }
            # 特权键直接丢掉，不进合并流程
            for key in list(rest):
                if key in PRIVILEGED_KEYS:
                    rest.pop(key)
                    rejected.add(key)
            if rest:
                self.data = _deep_merge(self.data, rest)
            if hooks:
                merged = dict(self.data.get("hooks") or {})
                for event, entries in hooks.items():
                    if isinstance(entries, list):
                        merged[event] = list(merged.get(event) or []) + entries
                    else:
                        merged[event] = entries
                self.data["hooks"] = merged

        # 恢复用户级特权键原值（_deep_merge 可能已让项目值覆盖上来）
        for key, value in user_privileged.items():
            self.data[key] = value

        if rejected:
            print(_PRIVILEGED_HINT.format(keys="、".join(sorted(rejected))), file=sys.stderr)

    def reload_project_scopes(self) -> bool:
        """工作区切换后重新读项目级配置。"""
        before = (json.dumps(self.project_data, sort_keys=True),
                  json.dumps(self.project_local_data, sort_keys=True))
        self._load_project_scopes()
        after = (json.dumps(self.project_data, sort_keys=True),
                 json.dumps(self.project_local_data, sort_keys=True))
        return before != after

    def scoped_hooks(self) -> dict[str, list]:
        """各作用域的 hook 配置。

        data["hooks"] 已经是三作用域合并过的结果（用户 → 项目 → 项目本地），
        这里直接返回它，不再重复合并。

        结构对齐 WorkBuddy：
            {事件名: [{matcher, hooks: [{type, command, timeout, ...}]}]}
        """
        merged: dict[str, list] = {}
        for event, entries in (self.data.get("hooks") or {}).items():
            if isinstance(entries, list):
                merged[event] = list(entries)
        return merged

    # ---- 密钥迁移 ----
    def _migrate_plaintext_keys(self) -> None:
        """把 providers[*].api_key 里的明文搬进系统凭据库。

        只搬一次：搬成功后 api_key 字段被替换成 {"__credential__": true} 标记，
        真实值从凭据库读。搬不进去（没有凭据库）就保持原样，不做任何破坏性改动。
        """
        if not cred_store.available():
            return
        providers = self.data.get("providers") or {}
        changed = False
        for name, spec in providers.items():
            if not isinstance(spec, dict):
                continue
            key = spec.get("api_key")
            if not isinstance(key, str) or not key or key in ("ollama",):
                continue
            if cred_store.get_secret(f"provider:{name}") == key:
                # 已经搬过了（配置里残留旧明文，清掉）
                spec["api_key"] = {"__credential__": True}
                changed = True
                continue
            if cred_store.migrate_plaintext(name, key):
                spec["api_key"] = {"__credential__": True}
                changed = True
        # 联网搜索的 key 同样搬进凭据库 —— 一个待遇。
        search = self.data.get("search")
        if isinstance(search, dict):
            key = search.get("api_key")
            if isinstance(key, str) and key:
                if cred_store.get_secret("search") == key:
                    search["api_key"] = {"__credential__": True}
                    changed = True
                elif cred_store.migrate_plaintext("search", key):
                    search["api_key"] = {"__credential__": True}
                    changed = True
        if changed:
            self.save()

    def search_api_key(self) -> str:
        """取联网搜索服务的密钥：先查凭据库，再退回配置里的明文。"""
        search = self.data.get("search") or {}
        raw = search.get("api_key")
        if isinstance(raw, dict):
            return cred_store.get_secret("search")
        if isinstance(raw, str) and raw:
            return raw
        return ""

    def provider_api_key(self, name: str) -> str:
        """取某个 provider 的真实密钥：先查凭据库，再退回配置里的明文。"""
        providers = self.data.get("providers") or {}
        spec = providers.get(name) or {}
        raw = spec.get("api_key")
        if isinstance(raw, dict):
            return cred_store.get_secret(f"provider:{name}")
        if isinstance(raw, str) and raw:
            return raw
        return ""

    def set_provider_api_key(self, name: str, key: str) -> None:
        """写入 provider 密钥：优先进凭据库，失败才退回明文。"""
        providers = self.data.setdefault("providers", {})
        spec = providers.setdefault(name, {})
        if key and cred_store.available() and cred_store.set_secret(f"provider:{name}", key):
            spec["api_key"] = {"__credential__": True}
        else:
            spec["api_key"] = key
        self.save()

    def refresh(self) -> bool:
        """重新读磁盘配置。

        运行中的进程会长时间持有 config 的内存副本。用户在这期间手动改了
        config.json（比如加了个 provider），如果不刷新，进程下一次 save()
        就会用旧数据把外部改动整块覆盖掉。这里以磁盘为准。
        """
        target = self.path
        if target is None:
            return False
        try:
            stat = target.stat()
        except OSError:
            return False
        if self._mtime is not None and abs(stat.st_mtime - self._mtime) < 1e-6:
            return False
        try:
            disk = json.loads(target.read_text("utf-8"))
        except Exception:
            return False
        if not isinstance(disk, dict):
            return False
        self._mtime = stat.st_mtime
        merged = _deep_merge(DEFAULT_CONFIG, disk)
        # 内存里已改过的键保留（本次请求内的修改不能丢）
        for key, value in self.data.items():
            if key not in disk:
                merged[key] = value
        self.data = merged
        # 项目级配置重新叠一遍（工作区可能变了）
        self._apply_project_scopes()
        return True

    # ---- 便捷访问 ----
    @property
    def disk_mtime(self) -> float | None:
        """磁盘上配置文件当前的 mtime（**不**动内存状态）。

        为什么需要它
        ------------
        `refresh()` 的返回值（「配置变了没」）**不能**用来判断要不要通知内核：
        这个信号会被**别人先消费掉** ——
          · `save()` 自己写完文件就更新了 `_mtime`
          · HTTP 中间件每个请求开头都调 `config.refresh()`

        等 AgentLink 想去同步内核时，mtime 早就一致了 → `refresh()` 返回 False
        → **内核永远收不到「配置变了」**。
        表现就是：用户在设置里填好 api_key，聊天仍报「缺少 api_key」
        —— 内核一直拿着启动时那份配置在跑。

        所以内核同步要自己记账：拿这个 mtime 跟「上次推给内核的那个」比。
        """
        if self.path is None:
            return None
        try:
            return self.path.stat().st_mtime
        except OSError:
            return None

    @property
    def mode(self) -> str:
        return self.data.get("mode", "efficiency")

    @property
    def api_token(self) -> str:
        """本地 API 的访问令牌。

        服务只监听 127.0.0.1，但**同机的网页可以向 127.0.0.1 发请求**，
        CORS 只挡读响应不挡发请求。所以凡是能改变系统的接口都必须带这个令牌，
        外部网页猜不到它。
        """
        token = self.data.get("api_token")
        if not token:
            token = secrets.token_urlsafe(32)
            self.data["api_token"] = token
            self.save()
        return token

    def save(self) -> None:
        """把当前配置写回磁盘（切换模式/风格后调用，重启仍然生效）。

        只写用户级 config.json。项目级配置留在它自己的文件里 ——
        把项目配置抄进用户配置会让「换个工作区」的行为变得不可预测。

        没绑定路径的实例（直接 Config({...}) 构造的）只改内存、不落盘。
        """
        target = self.path
        if target is None:
            return
        # 用户级配置 = 当前配置去掉 hooks（hooks 合并了三作用域）
        # 再把项目级覆盖掉的键恢复成用户自己的值
        user_disk = _read_json_file(target)
        payload = {k: v for k, v in self.data.items() if k not in ("hooks", "rules", "user_rules")}
        # 项目级覆盖过的键不该写回用户配置
        for scope in (self.project_data, self.project_local_data):
            for key in scope:
                if key in ("hooks", "rules", "user_rules"):
                    continue
                if key in user_disk:
                    payload[key] = user_disk[key]
                else:
                    payload.pop(key, None)
        user_hooks = self.user_hooks
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            existing = _read_json_file(target)
            if user_hooks:
                existing["hooks"] = user_hooks
            else:
                existing.pop("hooks", None)
            for key, value in payload.items():
                existing[key] = value
            target.write_text(
                json.dumps(existing, ensure_ascii=False, indent=2), "utf-8"
            )
            try:
                self._mtime = target.stat().st_mtime
            except OSError:
                self._mtime = None
        except Exception as exc:  # 写不进去不该影响正在进行的操作
            print(f"[oryx] 配置保存失败：{exc}", file=sys.stderr)

    def provider_config(self) -> dict[str, Any]:
        """效率模式用 active_provider，本地模式用 local_provider。"""
        return self.provider_config_for(self.active_provider_name)

    def provider_config_for(self, name: str) -> dict[str, Any]:
        """按名字取一份可直接给 build_provider 的完整配置。

        运行时模型路由会临时选另一个 provider，但不能绕过凭据解析 ——
        所以这里和 provider_config 共用同一套取值逻辑，不另抄一份。
        """
        providers = self.data.get("providers", {})
        if name not in providers:
            raise KeyError(f"未知 provider：{name}")
        spec = {**providers[name]}
        # api_key 可能是凭据库标记，这里换回真实值给 provider 用
        spec["api_key"] = self.provider_api_key(name)
        return {"name": name, **spec}

    @staticmethod
    def features_path() -> Path:
        """返回 features.yaml 应在的位置。"""
        return FEATURES_PATH

    @property
    def active_provider_name(self) -> str:
        if self.mode == "local":
            name = self.data.get("local_provider") or ""
            if name:
                return name
            # local_provider 还没登记，但用户可能已经加了本地模型 ——
            # 按地址（回环）找一个，别让本地模式跑云端模型。
            for pname, spec in (self.data.get("providers") or {}).items():
                if isinstance(spec, dict) and _is_local_spec(spec):
                    return pname
            return ""
        return self.data.get("active_provider", "anthropic")

    @property
    def allowed_roots(self) -> list[str]:
        roots = self.data.get("allowed_roots") or []
        if not roots:
            roots = [str(Path.home())]
        return roots

    @property
    def forbidden_paths(self) -> list[str]:
        return self.data.get("forbidden_paths") or []

    @property
    def max_turns(self) -> int:
        return int(self.data.get("max_turns", 12))


def load_config() -> Config:
    return Config.load()
