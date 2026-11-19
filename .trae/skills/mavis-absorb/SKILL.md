---
name: mavis-absorb
description: Borrow features and engineering patterns from the Marvis reference project into the Oryx agent, implementing and verifying each one. Use when the user asks to borrow from Mavis, compare Mavis against Oryx, or close a capability gap versus Marvis. Not for unrelated refactoring.
---

# Mavis → Oryx 能力吸收

把参考项目 `D:\项目\Mavis-main\Mavis-main` 的**可迁移能力与工程模式**，落到 `D:\项目\Oryx`。

## 适用判断

用户说「吸收 Mavis 优点」「跟 Mavis 对比」「补齐 Mavis 有的能力」「Mavis 有而我们没有」时走本流程。

## 前置事实（已核实，直接采信）

- 参考项目根：`D:\项目\Mavis-main\Mavis-main`（Python 包 `marvis/`：17 个 core 模块 + `cli.py` + `mcp/` + `utils/` + `scripts/` + `tests/` + `samples/`）。
- Oryx 根：`D:\项目\Oryx`（Python 内核 `core/oryx/` 67 个模块；零构建单文件前端 `core/web/index.html`；Tauri 壳 `apps/desktop/src-tauri`）。
- **形态不同**：Mavis 是**库**（CLI + MCP server），Oryx 是**桌面应用**（三进程：Tauri 壳 → FastAPI 控制面 `server.py` → agentd 执行面 `agentd.py`）。
- 差距清单与落点：`references/gap-matrix.md`。
- 可抄模式的实现骨架与验收断言：`references/patterns.md`。
- Mavis 更新后先跑 `scripts/scan_mavis.py` 重新盘点，别把这份静态清单当唯一事实。

## 阶段 1 — 读源码核实，不靠印象

按优先级核实，**每条结论都要有 `文件:行号`**：

1. Mavis 侧：`marvis/core/` 各模块的入口函数与返回结构、`marvis/utils/config.py` 的模式裁决、`marvis/core/workflow.py` 的模板注册与只读分级、`marvis/core/log_summary.py` 与 `system_manager.py` 的打码实现、`marvis/core/disk_inspect.py` 的符号链接防护、`marvis/tests/` 的安全断言写法。
2. Oryx 侧：`core/oryx/policy.py`（风险刻度）、`core/oryx/sandbox.py`（命令沙箱）、`core/oryx/localfs.py`（浏览护栏）、`core/oryx/sanitize.py`（子代理输出去毒）、`core/oryx/indexer.py`（全盘扫描）、`core/oryx/workflow.py`（编排引擎）、`core/oryx/loop.py`（工具注册入口）、`core/oryx/config.py`（配置作用域）。
3. 差距清单里每一条都要在两边各定位一次，**确认缺口真的存在**。已被 Oryx 解决的条目直接从清单里划掉，不要重复实现。

产出：一份按「已存在 / 部分存在 / 真缺失」分三档的表格，每条带落点文件。

## 阶段 2 — 筛选：抄什么、不抄什么

用这两把尺子筛，**不要把 Mavis 的功能清单无脑平移**：

- **形态适配**：Mavis 的能力要落到 Oryx 的既有抽象上（工具注册表 `core/oryx/tools.py` + `loop.py` 的 `_register_*`；后端 API 走 `core/oryx/server.py` 的 `@app.*`；前端视图走 `core/web/index.html` 的 `setView`）。库里的一行代码进 Oryx 常常要拆成「工具 + API + 前端三处」。
- **已有等价物不重复造**：Oryx 有 `ocr.py`、`office.py`、`indexer.py`、`imagegallery.py`、`browser.py`（Playwright，比 Mavis 的网页抓取强），Mavis 的 OCR/格式转换/图库/网页搜索**一律不抄**。Mavis 的 MCP server、系统管理、任务计划程序、pip 安装向导**不抄**（方向相反 / 超出定位）。

判定口径：**Mavis 值得抄的是安全工程手法与工程纪律，不是功能数量。**

## 阶段 3 — 排序：投入产出比

优先级从高到低：

1. **安全缺口**（能导致真实泄密或越权）：统一密钥打码层、符号链接/越界逃逸防护、输入侧提示注入隔离。
2. **可验证性**：断言式安全测试、只读自证字段、扫描三重上限与 `partial` 自述。
3. **体验与工程纪律**：`oryx doctor` 自检 + hints、预览清单模式、workflow 模板只读分级与别名、单一输出出口、CHANGELOG 写法。

排完序**先给用户看清单再动手**。用户没点头，不要一口气铺开所有条目。

## 阶段 4 — 实现

每个条目走同一条流水线：

1. **落点定位**：明确改哪几个文件。`core/oryx/` 加模块 → 在 `loop.py` 或 `server.py` 注册 → 前端若涉及则改 `core/web/index.html` → 在 `core/tests/` 加测试。
2. **最小实现**：只做这一条要的事，不顺手重构别处。
3. **不动既有不变量**（见下方硬约束）。

可抄模式（统一打码、符号链接防护、预览清单、只读自证、扫描上限、workflow 只读分级、doctor、单一出口、断言式测试）的具体骨架见 `references/patterns.md`，**照抄结构，不要重新发明**。

## 阶段 5 — 验证（不许只跑 pytest 就报完成）

每条至少满足：

- **单元/契约测试**：`cd D:\项目\Oryx\core && D:\Anaconda\envs\mcp-mw\python.exe -m pytest -q` 全绿。
- **安全条目额外做运行时验证**：造样本目录 → 跑真实路径 → **逐字节比对样本文件未被改动**；断言输出里不出现 `FORBIDDEN` 清单里的明文密钥。
- **涉及前端必须真点一次**：headless Chrome 里 `click()` 后读状态变量，不能只看元素位置和样式——静态检查与 HTTP 契约测不出界面层语义错配。
- **涉及事件流要直连后端读 SSE**：走代理会堵死长连接，事件接线从未生效的 bug 藏在这里。

## 阶段 6 — 记录

- `CHANGELOG.md` 写「新增了用户能做什么 + 该操作的行为边界」，不写 commit 标题。
- 往 `references/gap-matrix.md` 里回填这条的状态（已吸收 / 已否决 / 待做）与落点文件，下次不用重查。

## 硬约束（Oryx 特有，违反会出事）

- **落盘只在 D 盘**。数据根 `D:\OryxData`（`paths.py` 由 exe 盘符推导）。临时文件、pip/playwright/hf 缓存同理。
- **不许碰用户真实数据**：`config.json`、`oryx.db`、`oryx-metrics.db`、`oryx-memory.db`、`index.db`。测试必须隔离 `ORYX_HOME` 到临时目录，且**在模块导入前**设好（各测试模块导入期就 `from oryx.store import Store`）。
- **`Config` 落盘绑实例**：`Config({...})` 直接构造的实例不许写磁盘。顺手调一次 `save()` 会把用户真实 `config.json` 的 providers 覆盖成空字典，症状是「工作流一步都跑不通」而 pytest 全绿。
- **AGENT.md 用 `utf-8-sig` 读**：带 BOM 的文件用 utf-8 读出来开头是 U+FEFF，frontmatter 正则匹配不上 —— 整个 Agent 被当成没有 frontmatter 的纯正文，名字和工具白名单全丢，**且不报任何错**。写文件别加 BOM。
- **不把 Oryx 降级成库**：不引入 MCP server、不引入 pip 安装向导、不引入 Windows 任务计划程序封装。
- **前端单文件 7700 行**：改动只往里加，别做大规模拆分重构。
- **`loop.py` 单体 3000+ 行**：新工具注册走既有 `_register_*` 模式，不新建平行注册机制。
- **SSE 事件名大写**（`RUN_STARTED` / `RUN_FINISHED` / `RUN_ERROR` / `RUN_CANCELLED` / `RUN_CANCELLING`）：后端 publish 处、`_relay_kernel_event` 的判断、测试断言要连着改三处，**不要在 relay 里自作聪明改大小写**。
- **子 Agent 白名单是硬边界**：团队协作不放宽成员原有工具白名单。

## 常见坑

- **别只做功能不做验证**：Mavis 的核心价值在安全测试的写法，抄能力不抄测试等于没抄。
- **别在测试里真调模型**：本机挂的模型是 Base 模型，不会终止生成，单次调用 4 分钟，测试必超时。凡真调模型的套件一律用 stub provider。
- **别把静态检查当验收**：前端按钮 click 绑定坏了好几个批次都没被发现，因为验收只查了它在不在、对不对。
- **别信「跑通了」**：改完 `cargo build` 再同步资源，否则打包副本比源码旧，双击启动加载旧代码。
