# 可抄模式：实现骨架与验收断言

每条给出 Mavis 原型、Oryx 落点、可直接用的实现骨架、验收断言。
**照抄结构，不要重新发明。**

---

## 1. 统一密钥打码层

**Mavis 原型**
- `marvis/core/log_summary.py:77-92` —— 9 条正则常量。
- `marvis/core/log_summary.py:430-443` `redact_log_text(text) -> str`：先跑 8 条定向正则替换成 `[redacted]`，最后 `_LEFTOVER` 兜底正则若仍命中疑似凭据，**整行返回 `"[redacted]"`**（宁可全抹也不漏）。
- `marvis/core/system_manager.py:27-30` `_redact(text)`：JWT + `key=value` 两轮。

**Oryx 落点**
- 新增 `core/oryx/redact.py`，导出 `redact(text: str) -> str` 与 `redact_obj(obj) -> Any`（递归处理 dict/list）。
- 接入点（至少三处）：
  1. `core/oryx/sandbox.py` —— shell stdout/stderr 返回前。
  2. `core/oryx/loop.py` `run_subagent()` —— 子代理输出并入主上下文前（与 `sanitize.sanitize()` 并列调用）。
  3. `core/oryx/loop.py` `_handle_tool_call()` —— 工具返回值的字符串字段。

**实现骨架**

```python
_ASSIGN = re.compile(
    r"(?i)\b([A-Za-z0-9_.-]*(?:password|passwd|pwd|secret|token|api[_-]?key|"
    r"access[_-]?key|authorization)[A-Za-z0-9_.-]*)\s*[=:]\s*\S+"
)
_BEARER = re.compile(r"(?i)\bBearer\s+\S+")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b")
_AWS = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_GITHUB = re.compile(r"\bghp_[A-Za-z0-9]{20,}\b")
_OPENAI = re.compile(r"\bsk-[A-Za-z0-9]{16,}\b")
_URL_PASS = re.compile(r"(?i)(://[^:/\s]+:)([^@\s/]+)(@)")
_PRIVATE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_LEFTOVER = re.compile(
    r"(?i)(sk-[A-Za-z0-9]{8,}|ghp_[A-Za-z0-9]{8,}|AKIA[0-9A-Z]{8,}|"
    r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.|-----BEGIN |"
    r"://[^/\s]+:(?!\[redacted\])[^/\s@]+@)"
)

def redact(text: str) -> str:
    if not text:
        return text
    out = _PRIVATE.sub("[redacted]", text)
    out = _URL_PASS.sub(r"\1[redacted]\3", out)
    out = _JWT.sub("[redacted]", out)
    out = _AWS.sub("[redacted]", out)
    out = _GITHUB.sub("[redacted]", out)
    out = _OPENAI.sub("[redacted]", out)
    out = _BEARER.sub("Bearer [redacted]", out)
    out = _ASSIGN.sub(lambda m: m.group(1) + "=[redacted]", out)
    if _LEFTOVER.search(out):
        return "[redacted]"
    return out
```

**验收断言**

```python
FORBIDDEN = [
    "sk-abcdefghijklmnopqrstuvwxyz0123456789",
    "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    "AKIAIOSFODNN7EXAMPLE",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIn0.abcdefghijklmno",
    "-----BEGIN RSA PRIVATE KEY-----",
    "supersecretpassword",
]
# 1. 上述每一串经过 redact 后都不得出现在结果里
# 2. "password=hunter2" → "password=[redacted]"
# 3. "https://user:pw@host/x" → "https://user:[redacted]@host/x"
# 4. 正常文本 "error: file not found" 不被改写
```

---

## 2. 符号链接 / 越界逃逸防护

**Mavis 原型**
- `marvis/core/disk_inspect.py:415-427` `_classify_symlink(root_real, link_path)` —— **只读链接文本**（`os.readlink`），绝对路径直接 normpath，相对路径拼 dirname 后 normpath，再用 `_same_tree()` 判断是否越出根。**从不 stat 链接目标**。
- `marvis/core/disk_inspect.py:447-455` `_same_tree(root_real, candidate)` —— `os.path.normcase(os.path.normpath())` 后 `os.path.commonpath([root_n, cand_n]) == root_n`；`commonpath` 抛 ValueError（不同盘符）时返回 False。
- `marvis/core/disk_inspect.py:430-437` `_is_junction(path)` —— Windows junction 用 `os.path.isjunction`（Python 3.12+）识别，没有则 False，识别到就计数跳过。
- 遍历一律 `entry.is_dir(follow_symlinks=False)` / `entry.stat(follow_symlinks=False)`（`:126,158,167`）。
- `marvis/core/log_summary.py:728-760` `_read_regular_tail()` —— `os.open` + `O_NOFOLLOW` + `fstat` 确认是普通文件再 `lseek` 读尾部 N 字节；含 `\x00` 判为二进制直接跳过。

**Oryx 落点**
- 新增 `core/oryx/symlink_guard.py`：`same_tree(root, candidate) -> bool`、`classify_symlink(root, path) -> (bool, exc)`、`is_junction(path) -> bool`、`safe_scandir(root, ...)`。
- `core/oryx/localfs.py` 的 `list_dir()` —— 已有 `follow_symlinks=False`（`:164-166`），**补上越界判定**：条目是链接且指向禁区/根外时，标记 `{"escapes_root": True}` 并默认不展示内容类操作。
- `core/oryx/indexer.py` —— 全盘扫描时对链接只记路径不跟随。

**实现骨架**

```python
def same_tree(root_real: str, candidate: str) -> bool:
    if not candidate:
        return False
    root_n = os.path.normcase(os.path.normpath(root_real))
    cand_n = os.path.normcase(os.path.normpath(candidate))
    try:
        return os.path.commonpath([root_n, cand_n]) == root_n
    except ValueError:
        return False

def classify_symlink(root_real: str, link_path: str) -> tuple[bool, OSError | None]:
    """只读链接文本判断是否指向目录外。绝不 stat 目标。"""
    try:
        raw = os.readlink(link_path)
    except OSError as exc:
        return False, exc
    if not raw or "\x00" in raw:
        return True, None
    if os.path.isabs(raw):
        target = os.path.normpath(raw)
    else:
        target = os.path.normpath(os.path.join(os.path.dirname(link_path), raw))
    return not same_tree(root_real, target), None
```

**验收断言**

```python
# 1. 造 3 个符号链接，其中 2 个指向根目录外
#    → largest/entries 只含真实文件，symlink_count == 3，outside_count == 2
# 2. 外部目标文件的内容与 mtime 在扫描前后逐字节不变
# 3. 权限错误目录：monkeypatch os.scandir 抛 PermissionError → 该子树整体跳过，
#    error_count >= 1，目录内文件未变
# 4. commonpath 对不同盘符抛 ValueError → same_tree 返回 False（不炸）
```

---

## 3. 断言式安全测试

**Mavis 原型**
- `marvis/tests/test_safety_and_cli.py`
  - `:59` —— 巡检输出不含明文密钥。
  - `:181-183` —— 进程列表不含 `supersecretvalue`，且含 `[redacted]`。
  - `:265-267` —— 跑完卫生模板后**逐字节比对所有样本文件未变**。
  - `:280-288` —— monkeypatch `subprocess.run` 抛断言，只读模板必须零调用。
  - `:304-307` —— 命令注入：`<path>; touch pwned` → 退出码 1 且 `pwned` 文件不存在。
  - `:319-325` —— 报告不覆盖：第二次写同一路径 → 退出码 1 且提示「未覆盖」。
- `marvis/tests/test_log_summary.py:15-28` —— 12 个 FORBIDDEN 字符串逐个断言不在 stdout 也不在 JSON 里。
- `marvis/tests/test_log_summary.py:128-131` —— 签名归一化：两条不同 IP 的同款错误合并为 count=2，signature == `"connection to <ip>:# refused"`。
- `marvis/tests/test_log_summary.py:197-207` —— 只读尾部：`max_bytes=80` 时尾部新错误保留、头部旧错误不可见。

**Oryx 落点**
- `core/tests/samples/` —— 建样本目录（`secrets/`、`links/`、`logs/`），样本内容固定。
- `core/tests/test_redaction.py` —— 打码断言。
- `core/tests/test_symlink_guard.py` —— 符号链接断言。
- `core/tests/test_readonly_tools.py` —— 子 Agent 输出 / 只读工具不改文件的断言。

**验收断言（写入测试文件的骨架）**

```python
FORBIDDEN = [ ... ]  # 见模式 1

def test_readonly_scan_does_not_touch_files(tmp_path, monkeypatch):
    root = tmp_path / "samples"
    root.mkdir()
    (root / "a.txt").write_text("hello", encoding="utf-8")
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    # 跑只读能力
    ...
    after = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert before == after

def test_secrets_never_appear_in_output():
    blob = run_capability_with_secrets()
    for secret in FORBIDDEN:
        assert secret not in blob
```

**注意**
- 测试要隔离 `ORYX_HOME` 到临时目录，且**在模块导入前**设好。
- 断言式测试不许真调模型 —— 用 stub provider。

---

## 4. 只读自证字段 + 扫描三重上限

**Mavis 原型**
- 每个只读结果都带 `{"preview": True, "deleted": False, "modified": False, "moved": False}` —— 用返回值自证没动过。
- `marvis/core/disk_inspect.py` —— 三重上限：`depth` / `entries` / `timeout`，各自触发时置 `partial=True` 且 `limit_hits` 记 `"深度"` / `"条目"` / `"时间"`。BFS + visited 集合防环。

**Oryx 落点**
- `core/oryx/tools.py` 的 `ToolSpec` 加字段 `mutates: bool = False`。
- `core/oryx/loop.py` `_handle_tool_call()` —— 返回结果统一注入 `"mutated": spec.mutates`。
- `core/oryx/indexer.py` / `core/oryx/imagegallery.py` —— 加 `depth` / `max_entries` / `timeout_s` 参数与 `partial` / `limit_hits` 返回字段。

**验收断言**

```python
# 1. 触发深度上限 → partial is True 且 "深度" in limit_hits
# 2. 触发条目上限 → partial is True 且 "条目" in limit_hits
# 3. 触发时间上限 → partial is True 且 "时间" in limit_hits
# 4. 只读工具的返回体含 mutated is False
```

---

## 5. 预览清单模式

**Mavis 原型**
- `marvis/core/system_manager.py:327` `net_repair(execute=False)` —— 不执行时返回 6 条 `status:"preview"` 的步骤说明，`note` 写「未执行。确认后使用 …」。
- `marvis/core/batch_processor.py` —— `batch_rename` / `compress_images` / `convert_encoding` 全部 `dry_run=True` 默认，只算不写。
- MCP 侧 schema 里 `execute` / `dry_run` 的 `default: False/True`。

**Oryx 落点**
- `core/oryx/policy.py:404` `evaluate()` 判定为 L2 时，生成的 `ExecutionPlan` 增加人类可读的 `steps: [{title, detail, impact}]`。
- 前端 `core/web/index.html` 的审批弹窗渲染这份清单（用户看得见再决定）。

**验收断言**

```python
# 1. L2 工具在未确认时返回 plan.steps 且非空
# 2. plan.steps 每项含 title / detail
# 3. 确认前该工具的副作用函数零调用（monkeypatch 抛断言）
```

---

## 6. `oryx doctor` 自检 + hints

**Mavis 原型**
- `marvis/core/doctor.py:29-96` —— 不访问网络、不扫描用户文件，只报告：版本 / Python / 平台 / **当前模式 + network_allowed + 原因** / **`destructive_ops: "preview"`** / 外部依赖是否就绪 / **`hints` 列表**（可复制的下一步命令）/ 缺依赖时给安装命令。

**Oryx 落点**
- 新增 `core/oryx/doctor.py`，导出 `run_doctor() -> dict`：
  - `python` 版本 / 平台
  - `active_provider` / `local_provider` 是否可达（**只做配置检查，不真发请求**）
  - `index.db` 是否存在与大小、`models/` 是否就绪
  - OCR 引擎、Playwright 浏览器是否就绪
  - `D:\OryxData` 各子目录可写性
  - `destructive_ops` 当前模式（是否 L2 需审批）
  - `hints: [可直接执行的建议]`
- `core/oryx/server.py` 加 `@app.get("/api/doctor")`。
- 前端 `core/web/index.html` 设置页加一个「环境体检」入口。

**验收断言**

```python
# 1. run_doctor() 返回含 version / checks / hints
# 2. 检查过程不发任何网络请求（monkeypatch urlopen 抛断言）
# 3. 不扫描用户文件（monkeypatch os.scandir 只允许扫 OryxData）
```

---

## 7. workflow 模板只读分级 + 别名 + 报告不覆盖

**Mavis 原型**
- `marvis/core/workflow.py:474-495` —— 内置模板是**纯 dict**：`{"description", "runnable", "aliases", "usage", "steps"}`。`runnable: True` 才能走 `--template` 直跑。
- `:429-437` `run_builtin_template()` —— 显式拒绝并列出可跑清单：「模板「X」不能直接运行（可能包含会改动文件或系统的步骤）。可直接运行: …」。
- `:101` `_TEMPLATE_ALIASES` —— 别名归一（`hygiene` / `sensitive-hygiene` → 敏感文件卫生）。
- `:145-160` `_write_new_report()` —— 路径校验（空 / `\x00` / `\n` / `\r`）、父目录必须存在、是目录则拒、**已存在则 `ValueError("报告文件已存在，未覆盖")`**、最后 `open(dest, "x")` 独占创建。

**Oryx 落点**
- `core/oryx/workflow.py` —— 给内置模板加 `read_only: bool` 字段与 `aliases: list[str]`；`workflow_runs` 落库时，报告写文件用独占创建（不覆盖已有报告）。
- `core/oryx/store.py` —— `workflow_templates` 表加 `aliases` / `read_only` 列（注意：schema 无版本号，靠 `CREATE TABLE IF NOT EXISTS`，加列要写幂等迁移）。

**验收断言**

```python
# 1. 含删除步骤的模板 read_only is False
# 2. 直跑非只读模板 → 报错并列出可跑清单，subprocess.run 零调用
# 3. 别名解析：resolve_template_name("hygiene") == 规范名
# 4. 报告已存在 → 抛错提示「未覆盖」，原文件内容不变
```

---

## 8. 单一输出出口 + dict-error 契约

**Mavis 原型**
- `marvis/cli.py:427` `_output(result, args)` —— dict 且含 `"error"` → stderr + 返回 1，否则 stdout + 返回 0。**错误永远是结果里的一个 key，不是异常**。
- `marvis/cli.py:156` `_dispatch(args)` —— 长 if-chain，每分支**函数内延迟 import** core 模块。
- `marvis/cli.py:143` —— `--local` 是全局前置参数，解析后立刻写环境变量。
- `marvis/mcp/tools/__init__.py:831-849` `execute_tool()` —— 统一 try/except 包装，KeyError → 「缺少参数: x」。

**Oryx 落点**
- `core/oryx/tools.py` —— 统一工具结果构造器：`ok(data)` / `err(message)`，所有工具返回 `{"ok": bool, "content": Any, "error": str | None}`。
- `core/oryx/loop.py` `_handle_tool_call()` —— 统一包装，不再每个工具各写各的异常分支。
- `core/oryx/server.py` —— HTTP 层把 `{"ok": False, "error": ...}` 映射成合适的 4xx/5xx。

**验收断言**

```python
# 1. 任意工具抛异常时，_handle_tool_call 不外泄异常，返回 error 字段
# 2. 缺少必填参数 → error 文本包含「缺少参数」
```

---

## 9. CHANGELOG 写法

**Mavis 原型**（`Mavis-main/CHANGELOG.md` 2.1.3 条目）

> 整条在描述 `marvis workflow run --template 日志错误摘要 --root samples/logs` 的完整语义：别名、默认 top、只读尾部、不跟随符号链接、不解压、四类上限。

**Oryx 落点**
- `D:\项目\Oryx\CHANGELOG.md` —— **该文件目前不存在，需新建**。每条写「新增了用户能做什么 + 该操作的行为边界（读还是写、要不要审批、边界在哪）」，不写 commit 标题。

---

## 10. samples 样本目录 + 只读冒烟

**Mavis 原型**
- `Mavis-main/samples/{disk,hygiene,logs}` —— 固定内容的样本目录，测试逐字节比对。
- `Mavis-main/.github/workflows/ci.yml` —— pytest + 8 条 CLI 冒烟命令（`--help` / `doctor` / `workflow templates` / 三个只读模板 / `system repair` / `search`）。**把「只读能力能跑」当 CI 门禁**。

**Oryx 落点**
- `core/tests/samples/` —— 对应样本。
- `core/tests/test_smoke_readonly.py` —— 冒烟：跑所有只读模板/工具，断言不抛异常且 `mutated is False`。

**验收断言**

```python
# 1. 样本目录内容在测试前后逐字节不变
# 2. 每个只读能力都能跑通（不抛）
# 3. 只读能力的返回体 mutated is False
```
