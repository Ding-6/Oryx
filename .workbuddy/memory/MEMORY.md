# Oryx 项目长期笔记

> 这里装**长期**知识：约定、决策、教训。
> **逐轮的工作过程**在两个 daily log 里：
> · `archive/2026-11-02.md` —— 第 2~16 轮（功能真实性审查 → 扩展体系）
> · `2026-11-10.md` —— 第 17~31 轮（扩展闭环 → 联网搜索），**仍在追加**
>
> **「做了什么」不用记这里** —— 那些已经在代码、测试、`CHANGELOG.md`、
> `docs/` 里了。记忆文件只留「**为什么**」和「**下次别再踩**」。
>
> *（早期日志已归档而非删除 —— 规则说 30 天后才可归档，这里提前一步
> 是因为它的知识已全部提炼进本文件，且移出活跃目录便于查阅。）*

---

## 一、项目是什么

`D:\项目\Oryx` —— 一个**离线优先**的桌面 AI 助手内核（Python + Rust 侧），
内核在 `core/`，Web 前端在 `core/web/`。

**三条产品底线**（改动不能违反）：
1. **纯本地可用** —— 不联网、不配 key 也能跑。所有依赖外网的能力
   （联网搜索、AI 生图）默认**关**，要用户自己开、自己配。
2. **不可信内容一律打标记** —— 网页/文件/搜索结果里可能写着
   「忽略之前的指令」。统一在**出口级**做（`oryx/taint.py`），
   不逐个函数包 —— 逐个包一定会漏，漏的正好是能读到外部文本的那几个。
3. **声明与实际要一致** —— 工具说「我没动过任何东西」就得真没动。
   `mutates` 有清单 + `doctor.check_mutates()` 启发式复核双保险。

---

## 二、验证标准（怎么算「做完」）

| 层 | 命令 | 通过标准 |
|---|---|---|
| 全量单测 | `python core/tests/run_pytest.py` | **0 failed**（缺 matplotlib 的那个不算 —— 装上就没了） |
| 浏览器验证 | `python core/tests/run_web_checks.py` | **11/11 通过** |
| 项目体检 | `python -c "from oryx.doctor import run_doctor; print(run_doctor())"` | **0 个问题**（提示类不算） |
| 文档一致性 | `python core/scripts/gen_tool_docs.py --check` | 清单与运行时一致 |

**三条纪律**：
* **浏览器验证必须后台跑** —— 一轮 10~11 分钟，**Bash 工具的 timeout
  上限是 600 秒**，前台跑必被砍。`run_in_background: true`。
* **测试失败先看是不是真问题** —— 「单跑全绿 + 全量全挂」= 跨用例污染
  （查 fixture scope）；「每次挂的脚本不同、单独跑都过」= 负载时序问题。
* **改完要真跑一遍** —— 不要看脚本输出「已替换 N 处」就信。

---

## 三、工具类教训（最容易造成真损坏）

1. **删公共符号前必须全项目扫引用** —— `core/` + `apps/` + `tests/` 都要扫。
   正则**不能**排除 `模块.函数()` 形式（`(?<![\w.])` 是错的）。
2. **测试里不能裸替换模块级单例**（如 `oryx.server.store`）——
   `domains/*` 的路由在 app 装配时就绑好了。必须用 `monkeypatch.setattr`；
   `tests/conftest.py` 有 autouse 守卫钉死身份。
3. **顺序依赖要按最坏顺序验证** —— 把两个污染源都排在受害者前面跑局部测试。
4. **批量改文件前先备份**；**写文件脚本里不要用 `break`** 跳出循环
   —— 后面没处理的部分就静默丢了。
5. **写文件用 heredoc 有转义坑** —— 我连续三次「字符串明明在却匹配不上」，
   最后发现是 shell 把 `\n` 改坏了。**用 Write 工具写脚本文件**最稳。
6. **Read 工具的输出可能与实际不一致**（缩进/行尾）—— 改代码前
   用 Python 读一遍确认。

## 四、测试基础设施的坑（Chrome / CDP / 进程）

1. **Chrome 的启动器进程会先退出** —— `subprocess.Popen` 拿到的 PID 很快失效，
   靠它杀进程树**杀不到真正的浏览器**。要让浏览器自己退（CDP `Browser.close`）。
2. **`subprocess.run` 超时只杀直接子进程** —— 孙进程（Chrome）持有 stdout 管道
   → `communicate()` 无限等 → **整批卡死**，脚本级超时形同虚设。
   超时后必须**杀进程树 + 关管道**。
3. **用 `connect()` 探测端口会误判 TIME_WAIT** —— 该用 `bind()`：
   **能 bind = 没人监听**。
4. **固定 sleep 是「猜多久」，轮询 `readyState` 是「等到好」** ——
   负载高时猜不够，探针会落在没渲染完的空表上。
   逻辑要**共用**（`_webdrive.wait_page_ready`），各处各写各的等于没修。
5. **Edge 会在系统 temp 里造 `HeadlessEdge*` / `msedge_url_fetcher_*`
   （每个 140MB）** —— 项目本来有 `paths.redirect_env()`，但**测试脚本起
   Chrome 时没走它** → 攒了 14.5GB 到 C 盘。跑测试前先 `setup_browser_temp()`。

## 五、架构决策（为什么这样，不那样）

* **扩展的信任级别按**根目录**设** —— `Registry.add_root(kind, path, trust=)`。
  **仓库自带的扩展不能执行代码**（只声明元数据）。
* **profile 三原则**：只收窄不放宽 / 不能绕过信任 / 循环安全。
* **工具表冲突优先级**：内置 > MCP > 扩展 —— 扩展不能覆盖内置工具。
* **同名工具都跑而不是互相覆盖**（用户级/项目级配置分层）。
* **统一工具表**：三种来源一张表 + 冲突检测 + 声明问题。
* **工具注册按能力拆到 `oryx/toolsets/`** —— 19 个 mixin，一个能力一个文件
  （`loop.py` 从 4859 行降到 2913）。**用 mixin 不用「函数 + runtime 参数」**：
  后者要把方法体里所有 `self.` 换成 `runtime.`（`mcp` 那个有 164 处），
  机械替换容易改坏；mixin 把方法**原样搬走**，`self.` 一个字不用改。
* **能力表是唯一来源** —— 开关 → 工具组的映射集中在 `features.FEATURE_GATES`。
* **MCP 工具一律当不可信**（server 侧返回什么我们不知道）。
* **外部来源用 nonce 边界**（agent-browser 的做法）—— 不只是打标记。
* **值得抄的参考实现**：ECC（manifest + 上下文 profile + 三级钩子）、
  claude-mem（统一记忆条目 + 三策略检索 + 幂等标记）、
  auto-ontology（概念层与实现层解耦）、agent-browser（能力集与信任分级）。

## 六、文档纪律

* **诊断文档会过期** —— 每轮做完要**回头核实**「仍存边界」里的条目。
  已知的过期项我已逐条改过（跨端看屏、多标签页、A2A、Prompt 外置、
  知识库集合）。
* **文档说的比实际保守，会让人重复劳动** —— 这跟夸大一样有害。
* **自动生成的文档要有测试守着** —— 否则照样腐烂
  （`docs/TOOLS.md` ← `core/scripts/gen_tool_docs.py` + `tests/test_tool_docs.py`）。
* 例子要**能跑**，并由测试守着（`tests/test_write_extension_guide.py`）。

---

## 七、当前状态（2026-11-19）

**全绿**：1337 单测 0 failed ／ 浏览器验证 11/11 ／ doctor 0 问题。

**已做**：扩展体系闭环（发现→声明→依赖→启停→回收区→热重载）、
统一工具表 + 语义索引、令牌改 HttpOnly cookie + Origin 闸门、
frontmatter 统一解析器（曾有五份）、浏览器 Agent 的等待元素与下载、
联网搜索（可选）、工具清单文档。

**还没做**：
* `core/oryx/loop.py` 4200+ 行里有 21 个 `_register_*` —— 该拆成独立模块
  （**大重构，风险高**）
* AI 生图/生视频、IM 渠道（`features.py` 里 `DisableChannels` 明说本项目暂无）
* 搜索只支持 `serper` 一个后端
* 浏览器还缺「等待元素」的更多用法与下载的断点续传

**注意**：任务列表会过期 —— **「pending」不等于「没做」**。
S9 那两项（扩展工具进语义索引、mutates 声明校验）挂着 pending，
实际早已实现。隔段时间要**重新取证**。

---

## 八、工具使用备忘

| 场景 | 用什么 |
|---|---|
| 查 WorkBuddy 功能 | `https://www.workbuddy.cn/docs/workbuddy/Overview` |
| 读/改本地 Office 文件 | `workbuddy-local-office-edit`（先 `edsdk.py schema` 查参数） |
| 表格 | `workbuddy-sheetagent` |
| PPT / 脑图 | `workbuddy-pptx` / `tencent-xmind` |
| 文档创作 | `workbuddy-docx`（经 `workbuddy-office-routing` 分流） |
| 建站/小程序 | 先 `cloud-service`，发布用 `sites` |

**MCP 配置**写 `~/.workbuddy/mcp.json`（**不是** `.workbuddy/.mcp.json`）。
