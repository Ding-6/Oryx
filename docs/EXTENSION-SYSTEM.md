# 扩展系统 —— Oryx 怎么做到「改一处不影响别处」

> 第六轮（2026-11-09）：把散落的扩展发现逻辑收成一条总线，
> 让「加一种扩展 / 改一个扩展」不再牵动其他部分。

## 为什么要做

改造前的现状（审计结果）：

| # | 写死点 | 位置 | 后果 |
|---|---|---|---|
| 1 | 三套扩展各写一遍「扫目录 + 解析 frontmatter」 | `skills.py` / `experts.py` / `agents.py` | 加一种扩展类型 = **复制一遍**整套发现代码 |
| 2 | 技能路由是朴素字符串匹配 | `skills.py` 的 `match()` | 中文**根本不生效**（按空格分词 → 整句成一个词） |
| 3 | 工具全硬编码 | `tools.py` 的 `build_registry()` | 加工具必须改 Python |
| 4 | 钩子事件名写死 | `hooks.py` 的 `HOOK_NAMES` | 加事件 = 改代码；且无法精确禁用某个钩子 |
| 5 | 无扩展信任分级 | 全局 | clone 别人的仓库，他的 skills/ 就能声明高危能力 |
| 6 | 无上下文预算 | 提示词全量注入 | 技能一多，目录段就吃掉几千 token |

## 现在的形态

```
                    ┌─────────────────────────────────────┐
  声明层             │ oryx.json（可选） + SKILL.md/EXPERT.md │
  (Declaration)     └──────────────┬──────────────────────┘
                                   │  Registry.discover()  ← 唯一发现入口
  注册层  ┌────────────────────────▼────────────────────────┐
          │ ExtensionRecord {kind,id,version,description,     │
          │   triggers,eager,caps,contracts,trust,source}     │
          └──────────────┬───────────────────────────────────┘
                         │
  消费方  skills.py │ experts.py │ agents.py │ 提示词组装 │ 路由 │ 预算
```

**关键**：所有消费方只认 `ExtensionRecord`，不关心它是技能还是专家。

## 一、扩展清单：oryx.json

放在扩展目录根部，**可选**。没有它照样能加载（老目录一行不用改）。

```jsonc
{
  "apiVersion": "oryx/v1",
  "kind": "skill",                    // skill|expert|agent|workflow|policy|plugin
  "id": "web-research",
  "version": "1.1.0",
  "displayName": "联网调研",
  "description": "用浏览器打开网页、翻页、抓取结构化数据…",
  "triggers": ["联网", "查一下", "上网", "调研"],   // 显式触发词，路由用
  "eager": false,                     // true = 常驻上下文
  "allowedTools": ["browser_open", "browser_extract"],
  "caps": ["net.egress", "net.fetch"],// 能力声明（受信任级别裁剪）
  "requires": { "skills": [], "experts": [] },
  "hooks": [{ "on": "on_tool_end", "action": "log" }],
  "contracts": {
    "gates": ["至少两个独立来源交叉验证"],          // 什么条件下算完成
    "fail_closed_when": ["来源全部不可访问"],      // 什么情况必须拒绝
    "handoffs": ["doc-writer"]                     // 该转交给谁
  }
}
```

### 字段从哪来

| 来源 | 优先级 | 说明 |
|---|---|---|
| `oryx.json` | 最高 | 显式声明 |
| `SKILL.md` frontmatter | 兜底 | `triggers:` / `eager:` / `allowed-tools:` / `caps:` |
| 目录名 | 最后 | `id` 缺省时用目录名 |

### 扩展点清单

`oryx/registry.py` 的 `KIND_SPECS` 显式列出全部扩展点：

| kind | 目录 | 入口文件 |
|---|---|---|
| `skill` | `skills/` | `SKILL.md` |
| `expert` | `experts/` | `EXPERT.md` |
| `agent` | `agents/` | `AGENT.md` |
| `workflow` | `workflows/` | `workflow.json` |
| `policy` | `policies/` | `policy.yaml` |
| `plugin` | `plugins/` | `plugin.json` |

**加一种扩展类型**：在 `KIND_SPECS` 加一行 + 在 `registry.py` 传一个 root。
不用改 `skills.py` / `experts.py` / `agents.py` —— 有一测试专门盯这条
（`test_new_kind_needs_no_other_change`）。

## 二、信任分级

| 级别 | 来源 | 能力上限 |
|---|---|---|
| `builtin` | 产品自带目录 | 全部 |
| `user` | 用户自己的目录（`app_dir()/skills`） | 全部 |
| `project` | **仓库里带的** | 只有 `UNTRUSTED_ALLOWED` 白名单 |
| `plugin` | 插件带来 | 按插件声明 |

不可信来源声明 `proc.spawn` / `credential.read` 会被**在加载时剔除**，
不是等到运行期才拦 —— 防线在入口，不在深处。

> 为什么要这条：`git clone` 别人的仓库，他的 `skills/` 目录里的东西
> 不该有提权能力。clone 本身不该等于授权。

## 三、上下文预算

扩展一多，全量注入就炸上下文。现在技能目录**按本轮 query + 预算裁剪**：

```
core/oryx/context_budget.py
  ContextProfile(eager=…, required=…, remainder="routed", limit_tokens=…, mode=…)
```

- `eager` —— 永远常驻（数量要很少，都是「怎么用这个平台」这类元技能）
- `required` —— 命中 query 就载
- `routed` —— 按触发词/语义路由加载（绝大多数扩展在这一档）
- `limit_tokens` —— 这段能占多少 token
- `mode` —— `blocking`（超了拒绝）/ `report-only`（超了只告警）

预算值从配置读：`skill_catalog_budget`（默认 600）。设 0 = 不限制。

**裁剪后仍列出未展开技能的「名字」**（只省描述的 token）——
模型得知道它们存在，否则不会想到去 `use_skill` 读。

## 四、路由（三级策略）

`registry.route()`：

1. **triggers** —— 显式触发词，命中即用（最准）
2. **description** —— 描述与查询的 **bigram** 重合度
3. **name token** —— 名称分词命中

### 为什么用 bigram

中文没有空格。按空格切分的话，
「按类型、时间或主题整理文件目录」会变成**一个**长 token，
永远匹配不上 query 里的「整理」「文件夹」。

> 这是老 `SkillLibrary.match()` 一直存在的缺陷 —— 中文场景下它**完全无效**。
> 现在的 `tokenize()` 对中文切二元组，对英文仍按单词切。

## 五、钩子三级开关

```bash
ORYX_HOOKS_ENABLED=0                    # 总闸：全关
ORYX_HOOK_PROFILE=minimal|standard|strict # 档位
ORYX_DISABLED_HOOKS="pre:tool:net-guard" # 单点精确禁用
```

钩子有**稳定 id**（`hook_id_for(name, owner)` → `pre:tool:net-guard`），
这是能精确禁用某一个的前提。被跳过的钩子会记在 `emit()` 结果的
`skipped` 里 —— 不是静默消失。

## 六、前端质量门禁

界面代码也有门禁：`oryx/ui_audit.py` + `oryx/rules/ui-quality.yaml`。

```bash
python -m oryx.ui_audit              # 扫 core/web/
python -m oryx.ui_audit --json       # 机器可读
python -m oryx.ui_audit --strict     # 有 block 就退出码非 0
python -m oryx.ui_audit --min-severity info   # 连风格建议一起看
```

规则格式与 `rules/dangerous.yaml` 同构（`id/category/description/pattern`），
所以加载、解析、分级都能复用 policy 那套。

### 规则支持的三种上下文能力

这是「规则要准」的关键 —— 纯正则必然误报：

| 能力 | 写法 | 用途 |
|---|---|---|
| `requires_absent` | `requires_absent: ":focus-visible"` | 文件里已有替代方案时不算违规 |
| `ignore_var_defs` | `ignore_var_defs: true` | CSS 变量定义处的字面值不是硬编码 |
| `exclude` | `exclude: "\\.scrollTop\\s*="` | 行内排除 |

例：`outline: none` 本身不是 bug —— 前提是**没有**别处的焦点样式。
界面上已有 `:focus-visible { outline: 2px solid var(--accent) }`，
那条用 border 做焦点态的 `outline:none` 就不是问题。

### 规则来源

移植自本地项目的现成规则集：

| 来源 | 提供了什么 |
|---|---|
| `taste-lint` | 149 条 YAML 规则，schema 与本文件同构 |
| `web-interface-guidelines` | 104 条界面规范（可静态化的部分） |
| `interface-proof` | 14 类静态检查 + 扣分模型 |
| `Frontend-Quality` | 响应式 / 可访问性 / 四态 / 性能 |
| `hallmark` | 反「AI 味」判据 |
| `argus` / `impeccable` | 设计令牌与一致性预算 |

### 分级

`block` / `warn` / `info` 三档。日常只看 warn 以上 ——
info 级是风格建议，一次几百条会把真问题淹掉。

### 自举

门禁扫的就是自己的界面，所以 `tests/test_ui_gate.py` 断言
**自家界面没有 block 级问题**。

> 用没验证过的工具去验证别人，等于没验证。
> 这条断言失败 = 门禁本身失效了。

## 七、配置分层（待做）

借鉴 worktrunk：用户级配置（信任）与项目级配置（入库共享，首次需审批）
分开，同名时**都跑**而不是覆盖。项目级配置只能**收紧**策略不能放宽。

## 八、边界与已知取舍

- **扩展总线目前只覆盖发现与元数据**。加载/执行仍由各自的 library 负责
  （`SkillLibrary` / `ExpertLibrary` / `AgentLibrary`）——
  这是刻意的：一次只改一层，出问题好定位。
- **工具注册仍是硬编码的**（`tools.py` 的 `build_registry()`），
  还没接进总线。下一轮做。
- **工作流与策略扩展点已登记但未接线** —— `KIND_SPECS` 里有，
  但没有对应的 loader。
- **`oryx.json` 只有一份，没有 profile 机制**（ECC 的
  `install-profiles.json` 那种「装哪套」的组合）。下一轮。
- **上下文预算只作用于技能目录**。专家、Agent 清单还是全量注入。
- **门禁是静态的**。对比度、触摸目标、渲染类问题要运行时才算得出
  （argus 那 49 条）—— 需要接浏览器（Oryx 有 `browser.py`），下一轮。

---

## 九、工具扩展（第七轮）

### 形态

一个扩展目录里放 `oryx.json`（声明）+ `tools.py`（实现）：

```
extensions/word-count/
├── oryx.json          声明层：schema + 风险等级（不含代码）
└── tools.py           实现层：TOOLS 的函数实现
```

```jsonc
{
  "id": "word-count",
  "kind": "tool",
  "version": "1.0.0",
  "caps": ["fs.read"],
  "tools": [
    {
      "name": "text_count",
      "description": "统计文本的字数、字符数、行数。",
      "parameters": {
        "type": "object",
        "properties": {
          "text": {"type": "string"},
          "file": {"type": "string"}
        },
        "required": []
      },
      "risk": "L0_READ",              // 缺失 → 按 L2 处理
      "entry": "tools.py:count_text"  // 实现位置
    }
  ]
}
```

### 为什么元数据与实现分开

风险等级写在**清单**里而不是代码里 —— 这样「这个工具会干什么、有多危险」
可以在**不执行任何代码**的情况下审阅。实现层只提供 `fn`。

### 安全边界（这一层是整个设计的关键）

执行扩展里的 Python = 任意代码执行。所以：

| 信任级别 | 来源 | 能执行代码吗 |
|---|---|---|
| `builtin` | 产品自带 | ✅ |
| `user` | 用户自己的目录 | ✅ |
| `project` | **仓库里带的**（`git clone` 来的） | ❌ **只能声明元数据** |

> 「git clone 一个仓库」不该等于「授权它的代码在你机器上跑」。

信任级别是**按根目录**设的，不是按类型 —— 用户目录和仓库目录可以同时存在，
各自算各自的。

另外三条：

1. **风险等级必须声明** —— 清单里没写 risk 的一律按 `L2`（破坏性）处理。
   与 MCP 的既有约定一致（「第三方能力…一律按 L2 处理」）。
2. **加载要留痕** —— `load_report()` 记录加载了哪些、跳过了哪些、为什么。
3. **失败要说清楚** —— 加载不了就报错，不静默跳过。
   静默跳过会让人以为工具可用。

### 加载报告

```python
specs, report = load_tool_plugins(records)
print(report.summary())
```

```
已加载 1 个扩展工具：
  · text_count（来自 word-count，风险 L0_READ）
跳过 1 个：
  · text_count —— word-count: 信任级别 project 不允许执行扩展代码
```

### 可见性

`GET /api/extensions` 返回：

| 字段 | 内容 |
|---|---|
| `stats` | 各类扩展的数量 |
| `digest` | 注册表内容摘要（检测漂移） |
| `extensions` | 每个扩展的完整记录（含信任级别、能力、契约） |
| `profile` / `profile_active` | 当前 profile 与它包含的扩展 |
| `available_profiles` | 所有可选 profile |
| `plugin_tools` | 从扩展加载进来的工具 |
| `tool_report` | 加载/跳过/错误/代码来源 |

## 十、profile（第七轮）

profile 是**一组扩展的组合**。见仓库根目录的 `profiles.json`：

```jsonc
{
  "default": "",
  "profiles": {
    "minimal":   {"extensions": ["file-search"]},
    "research":  {"extensions": ["web-research", "file-search", "word-count"]},
    "developer": {"includes": ["minimal"],
                  "extensions": ["file-organizer", "receipt-ocr", "system-info"]},
    "full":      {"extensions": ["..."]}
  }
}
```

在配置里选：

```json
{ "extension_profile": "developer" }
```

### 三条设计原则

1. **profile 可以嵌套**（`includes`）—— 否则组合会写得很啰嗦。
2. **默认是「不排除任何东西」** —— profile 是**收窄**工具，不是新的必需项。
   没配 profile 的一切照旧；**profile 名字写错了也不该让扩展全部消失**。
3. **profile 只影响「加载哪些扩展」，不影响「怎么执行」** ——
   信任判定独立进行，**profile 不能用来绕过信任检查**。
   有一条测试专门盯这条。

### 循环 include

`a` include `b`、`b` include `a` 不会无限递归 —— 展开时截断。

---

## 十一、统一工具表（第八轮）

### 为什么不是「把 77 个工具搬进文件」

工具原本有三个来源，各自登记：

| 来源 | 登记在哪 | 命名 |
|---|---|---|
| 内置 | `tools.py` 的 `build_registry()` + `loop.py` 里 70 段注册 | `fs_read` |
| 扩展 | `toolplug.py` 加载后写进 `runtime.registry` | 扩展自定义 |
| MCP | `loop.py` 的 `_register_mcp_tools()` | `mcp__<server>__<name>` |

把 70 段闭包注册搬去重构成纯体力活，且**回归风险高**（那些闭包捕获 runtime 状态），
收益却小。真正缺的是三样：

1. **冲突检测**
2. **统一视图**
3. **统一校验**

所以只加**声明层与校验层**，不动实现。

### 顺带修掉的一个真问题

扩展工具与内置同名时，旧代码是 `self.registry[name] = spec` —— **直接覆盖**。

一个扩展可以把 `fs_read` 换成返回任意内容的实现，
而模型看到的工具清单毫无异常。**这是安全问题，不是代码洁癖。**

现在：**扩展永远不能覆盖内置或 MCP 的工具**。同名时拒绝并记录。

```
fs_read：保留了 builtin，拒绝了 extension/evil
```

### 优先级

```
内置 > MCP > 扩展
```

扩展的优先级最低 —— 无论它声明得多合理。

### 统一表

`core/oryx/tooltable.py`：

```python
table = ToolTable()
table.add_builtin(spec)
table.add_mcp(spec, server="srv")
table.add_extension(spec, ext_id="evil", trust="user")

table.conflicts   # → list[Conflict]（谁被拒了）
table.problems    # → list[str]（声明不合规的）
table.all()       # → list[ToolDecl]
table.to_dict()   # → 完整视图
```

`ToolDecl` 是三种来源的**统一视图** —— 不管原来长什么样，
对外都是这一个结构：名字、描述、来源、信任级别、风险、是否会改动状态、分组。

### 声明校验

每个工具都要过这一关：

| 检查 | 为什么 |
|---|---|
| `parameters` 非空且是对象 | 模型要靠它填参数 |
| `parameters.type == "object"` | 其他类型在这个协议里没意义 |
| 有 `properties` | 同上 |
| `required` 是数组 | 填错会静默丢参数 |
| 有 `description` | **模型要靠它选工具** —— 空描述等于这个工具不存在 |

不合规的工具会在 `/api/doctor` 的「工具声明」检查项里报出来，
写工具的人能第一时间看到。

### 可见性

`GET /api/extensions` 的 `tool_table` 字段：

```jsonc
{
  "total": 81,
  "counts": {"builtin": 79, "extension": 2},
  "conflicts": [{"name": "fs_read", "kept": "builtin", "rejected": "extension/evil"}],
  "problems": [],
  "tools": [{"name": "fs_read", "source": "builtin", "risk": 0, "mutates": false, ...}]
}
```

### 装配时机

工具是**分散注册**的 —— `build_registry()` 给 10 个，
各 `_register_*` 方法再陆续加 60 多个，MCP 的最后到。

所以统一表在**所有登记完成后**重建（`runtime.refresh_tool_table()`）。
建早了会漏 —— 这就是「表在 `__init__` 里建只有 10 个」的原因。
MCP 工具注册时也会同步登记进表。

---

## 十二、工具检索修好了中文（第九轮）

### 问题

关键词路是这么写的：

```python
for token in needle.split():     # ← 中文没有空格
```

「统计字数」整句变成**一个** token，去 `in` 描述里的「统计文本里的字数」——
不连续，匹配不上。**中文查询基本搜不到东西。**

这跟第六轮修的技能路由是同一类毛病：**拿英文的分词思路去处理中文**。

### 修法

复用 `registry.tokenize()`（中文切 bigram，英文按词）：

```python
needle_tokens = tokenize(needle)
for token in needle_tokens:
    if token in name_tokens:      score += 6   # 命中工具名 —— 最强信号
    elif token in desc_tokens:    score += 3   # 命中描述 —— 次强
    elif token in sem_tokens:     score += 1   # 只命中语义补充 —— 最弱
```

`tokenize` 带 LRU 缓存 —— 检索会对每个工具反复切同一段文本。

效果：

| 查询 | 修前 | 修后 |
|---|---|---|
| 统计字数 | **0 条** | count_words 排第一 |
| 看电脑配置 | 0 条 | system_info 排第一 |
| 截图 | 0 条 | browser_screenshot、ocr_image |

**这影响所有工具的中文检索**，不只是扩展工具。

### 顺带修掉的两个真问题

**1. 索引构建失败时没清空旧索引**

`ensure()` 在 `_build` 失败（没装向量模型）时只置 `_loaded = False`，
**没清 `_matrix`**。于是 `search()` 拿着一份**上次成功构建的过期矩阵**返回结果 ——
工具集早变了，检索还在按老数据排序。

现在失败时清空 `_matrix` / `_names` / `_signature`，
并且 `search()` 在 `not loaded` 时直接返回空（宁可退回关键词路）。

**2. 关键词 0 命中时语义阈值太松**

「zzzqqqxyz」这种乱词，所有工具的余弦分都在 0.35-0.40（**平顶，没区分度**）；
真查询「看电脑配置」的 system_info 是 0.57，明显高出其余。

所以：**关键词路一条都没命中时，语义单独扛，此时要求语义更强**（阈值 0.45）：

| 查询 | 修前 | 修后 |
|---|---|---|
| zzzqqqxyz | 6 条（全噪音） | **0 条** |
| 看电脑配置 | 6 条 | 6 条（system_info 第一） |
| 写个文档 | 6 条 | 6 条（docx_write 第一） |

### 召回分数可见

结果里现在带 `keyword_score` 与 `semantic_score`（分开给，因为量纲不同：
关键词是整数分，语义是 0-1 的余弦值）—— 用户能知道「为什么召回它」。

## 十三、mutates 声明复核（第九轮）

### 为什么重要

```python
result["mutated"] = bool(spec.mutates)
```

工具结果里那句「我没动过任何东西」是真是假，全看这个声明。
**漏声明就是在说谎** —— 比不说更糟。

而 `mutates` 靠手工维护的 `MUTATING_TOOLS` 清单，散着写一定会有人漏。

### 启发式复核

`ToolTable.check_mutates()`：名字里带「明显会改动」的词、却没声明 mutates → 报出来。

**第一版是 100% 误报** —— 因为用了子串匹配：

| 工具 | 命中的子串 | 实际行为 |
|---|---|---|
| `use_skill` | kill | 读技能 |
| `sys_input_methods` | put | **列出**输入法 |
| `sys_display_settings` | set | **读取**分辨率 |
| `automation_runs` | run | 列运行记录 |

**误报率 100% 的检查比没有检查更糟** —— 用户会养成忽略它的习惯。

改成**按 `_` 分词后比对完整词**：

| 工具 | 分词后 | 结果 |
|---|---|---|
| `use_skill` | use, skill | 不命中 ✓ |
| `sys_input_methods` | sys, input, methods | 不命中 ✓ |
| `sys_display_settings` | sys, display, settings | 不命中 ✓ |
| `fs_write` | fs, write | **命中** ✓ |
| `term_run` | term, run | **命中** ✓ |
| `browser_click` | browser, click | **命中** ✓ |

当前 80 个内置工具：**0 误报**。

### 接进 doctor

`/api/doctor` 的「改动声明」检查项 —— 有人加了新工具却忘了声明 mutates，
自检会指出来。

> 启发式**不替代人判断**。它的价值是把可疑项推到人眼前，
> 而不是替人决定。真的只读但名字像写工具的，可以加进 `READONLY_NAME_HINTS`。

---

## 十四、前端扩展（第十轮）

### 之前是什么样

界面是零构建的 ES Module，入口只有一个：

```
index.html  →  /web/modules/boot.js  →  静态 import 12 个模块
```

所以加一个界面模块 = **改 boot.js 加一行 import**。核心文件每加一个功能就被碰一次，
改多了互相干扰。

### 现在

扩展放用户目录，**不用碰任何核心文件**：

```
%APPDATA%/Oryx/ui-extensions/<id>/
├── oryx.json     声明：id / 名称 / 挂载点 / 顺序
└── index.js      实现：export default { mount, label, setup(ctx) }
```

后端只做两件事：**发现**与**给清单**（`GET /api/ui/modules`）。
前端动态 `import()` —— 仍然是零构建。

### 扩展长什么样

```js
export default {
  id: 'my-ext',
  mount: 'rail',              // rail | railfoot | topbar | side
  label: '我的面板',
  icon: '<svg ...>...</svg>',

  setup(ctx) {
    // ctx.$     选择器
    // ctx.api   fetch 封装（自动带令牌）
    // ctx.on    订阅事件
    // ctx.emit  发事件
    // ctx.mount 挂载点元素
  },
};
```

### 安全边界（与 Python 扩展不同，但要同样严）

Python 扩展在**你的机器上**执行代码；前端扩展在**浏览器里**跑，
拿不到系统权限，但能调后端 API（带 token）、读页面内容、改界面。

所以：

1. **只从用户目录加载** —— 仓库里自带的**不自动加载**。
   clone 一个仓库不该等于让它往你的界面里插代码。
   （与 Python 扩展的 project 信任规则一致。）
2. **扩展拿不到令牌** —— 后端服务扩展资源时**不注入** `__ORYX_TOKEN__`。
   它只能通过 `ctx.api` 调后端，**绕不过 ctx**，能力是受限的。
3. **核心保留名不能占用** —— 顶掉核心模块会让功能「莫名其妙没了」，
   比扩展加载失败更难查。
4. **坏的扩展要说清楚** —— 记进 `skipped`，不静默跳过。

### 挂载点

| mount | 落点 | 适合 |
|---|---|---|
| `rail` | `.railgroup` | 左栏的功能入口 |
| `railfoot` | `.railfoot` | 左栏底部（默认按钮区） |
| `topbar` | `.topbar` | 顶栏动作 |
| `side` | `#side` | 右栏面板 |

> `rail` 指向 `.railgroup` 而不是 `.railnav` —— `.railnav` 里是
> `<div class="nav" role="tablist">`，tablist 只能放 tab，
> 塞普通按钮是语义错误（读屏器会当成「多了一个未选中的标签页」）。

### 验证

`tests/check_web_uiext.py` —— **真实浏览器**里验证：

| 检查 | 为什么 |
|---|---|
| 清单接口列出扩展 | 后端这一层 |
| 扩展的按钮出现在 DOM 里 | 真的 import 成功了 |
| 按钮有可访问名 | 有可访问名 |
| 点按钮后面板出现 | **setup() 真跑了、事件真绑上了** |
| 面板内容来自后端 | **扩展真能调 API**（ctx.api 通了） |
| 核心界面照常渲染 | 扩展没搞坏主界面 |

第 4、5 条是关键 —— 只检查「元素存在」的话，一个 setup() 里抛了异常、
按钮挂上了但没绑事件的扩展也能蒙混过关。

### 样例

`core/web/ui-extensions/sample-panel/` —— 最小样例，默认 `enabled: false`。
想试就把目录复制到 `%APPDATA%/Oryx/ui-extensions/` 并打开 enabled。

---

## 十五、样式隔离与事件总线（第十一轮）

上一轮把前端扩展通道做通了，但有两个「不敢用」的地方：
扩展的 CSS 会漏进核心、核心的 CSS 会漏进扩展；扩展之间能通信，
但核心不知道扩展的事。这轮补上。

### 样式隔离：Shadow DOM

扩展的 DOM 与样式都放进**自己的 Shadow DOM**：

```
宿主 div.uiext-host  →  attachShadow({mode:'open'})  →  扩展的 DOM 与 <style>
```

| 效果 | 说明 |
|---|---|
| 扩展的 CSS 不漏到核心 | Shadow DOM 的边界是硬的，选择器再通用也出不去 |
| 核心的 CSS 不漏进扩展 | 扩展想用核心的类名（`.iconbtn`）是不行的 —— 得自己写 |
| **CSS 变量照常继承** | `var(--ink)`、`var(--bg-2)` 在 shadow root 里直接可用 |

> **变量继承是白拿的**：CSS 自定义属性是**继承**的，不依赖选择器匹配。
> 变量定义在 `html` 上 → 继承到 shadow host → 进 shadow root。
> 所以暗色主题也自动跟随，不需要额外桥接。

扩展写样式用 `ctx.addStyle(css)` —— 样式进 shadow root，不影响核心。

### 事件总线：core.js 的 bus

总线放在 `core.js`（**依赖根**，不 import 任何业务模块）。

**为什么必须是 core.js**：扩展是动态 `import()` 的独立模块图，
但它们 import 的 `/web/modules/core.js` 是**同一个 URL** ——
浏览器模块缓存保证是**同一个模块实例**。总线放这里，核心与扩展拿到的是同一个对象。

放别处（比如 `uiext.js`）就不行：扩展 import core.js 拿到的是 core 那份，
uiext.js 自己那份是另一个 —— 两套监听器，谁也收不到谁的事件。

**核心 → 扩展**：

| 事件 | 时机 | 载荷 |
|---|---|---|
| `state:changed` | 状态刷新后 | `{mode, interaction, style, expert, provider, model}` |
| `view:changed` | 视图切换 | `{view}` |
| `session:changed` | 会话切换 | `{id, title}` |
| `theme:changed` | 主题切换 | `{theme}` |
| `uiext:ready` | 扩展都挂好了 | `{ids}` |

**扩展 → 核心**（核心在 boot.js 里接线）：

| 事件 | 效果 |
|---|---|
| `ui:view` `{view}` | 切换视图 |
| `ui:session` `{id}` | 切到某个会话 |
| `ui:toast` `{text}` | 弹一条提示 |

接线放在 `boot.js`（启动入口）—— 那是「谁负责响应」的天然位置，
不用让 `core.js` 去 import 业务模块（core.js 要保持依赖根）。

监听器抛异常**不影响别人** —— 一个扩展的错不能拖垮核心。

### 真实浏览器验证（14 项）

`tests/check_web_uiext.py` 覆盖：

| 检查 | 验证什么 |
|---|---|
| 扩展被挂到隔离容器 | host + shadowRoot 都在 |
| 按钮在自己的 shadow root 里 | 真的 import 成功了 |
| **扩展的元素没泄漏进核心 DOM** | 隔离生效 |
| **核心样式没渗进扩展** | 隔离生效 |
| **CSS 变量继承到扩展** | 隔离**不**挡变量 |
| 点按钮面板出现 | setup() 真跑了、事件真绑上了 |
| 面板内容来自后端 | ctx.api 通了 |
| **核心切视图 → 扩展收到** | 正向总线 |
| **扩展请求切视图 → 核心响应** | 反向总线 |
| 核心界面照常渲染 | 扩展没搞坏主界面 |

> **检测「核心样式是否渗入」的坑**：不能只看「高度 > 5」——
> 浏览器默认的 `<button>` 本来就有 21px 高，那样恒为真。
> 也不能用 `.iconbtn`（扩展自己就定义了它）。
> 正确做法：用**核心独有的类名**（`.navitem`）在 shadow root 里放元素，
> 跟普通 div 比 computed style —— 有差异才说明渗入了。

---

## 十六、核心样式子集（第十二轮）

### 问题

Shadow DOM 隔离有个副作用：**核心的类名在扩展里用不了**。
扩展想放一个跟核心一样样的按钮，就得把 `.btn` 的样式抄一遍。

抄样式的问题：核心改了样式，扩展抄的那份**不会跟着变** ——
过一阵两边就不一样了，界面看着就「不是一套的」。

### 解法

扩展声明「我要用哪些核心类」，后端从 `main.css` 里**挑出原文**给它：

```jsonc
{
  "id": "my-ext",
  "baseCss": ["btn", "inp", "iconbtn"]   // 要复用的核心类
}
```

```bash
GET /api/ui/base-css?classes=btn,inp   # → 只给这些类的规则
```

**给的是核心原文，不是复制品** —— 核心改了什么，扩展下次拿到的就是新的。

### 效果

| | 核心 main.css | `.btn` 子集 |
|---|---|---|
| 大小 | 103,501 字节 | **1,611 字节** |

省了 98%。`btn + inp + iconbtn` 三个类一起提也只有 3KB。

`GET /api/ui/core-classes` 能列出核心所有类名（411 个），扩展作者写清单时可以参考。

### 实现要点（两个容易错的地方）

**1. @media 里的规则要连选择器一起保留**

只留声明体的话：

```css
@media (max-width: 600px) { .btn { font-size: 11px; } }
```

会变成：

```css
@media (max-width: 600px) { font-size: 11px; }
```

那是**无效 CSS**，浏览器整块丢弃 —— 媒体查询里的样式就全没了。

**2. @media 块的类集合必须是内部规则的并集**

否则「这个 @media 块涉及哪些类」永远是空集，
**里面的规则一条都提取不出来**。

### 注入顺序

核心子集**先注入**，扩展自己的样式**后注入** —— 同一段里后写的覆盖先写的，
所以扩展能覆盖核心的样式。

## 十七、扩展热重载（第十二轮）

### 怎么做到「改了代码就生效」

浏览器会缓存 ES Module —— **同一个 URL 第二次 `import()` 拿到的还是旧代码**。
所以光刷新页面没用（除非清了缓存）。

解法：**URL 带内容指纹**。

```
/web/ui-extensions/my-ext/index.js?v=abd154ece9d8
                                  └── index.js + oryx.json 的内容 hash
```

文件改了 → 指纹变 → URL 变 → `import()` 新 URL → 浏览器当成新资源，重新执行。

清单接口同时发 `Cache-Control: no-store` —— 清单里带指纹，
**缓存的清单会给旧指纹**，改了也不生效。

前端每 5 秒轮询一次清单，比对指纹，变了就重载。

### 卸载与清理

重载时先卸载旧版本：跑扩展注册的清理函数 + 摘掉宿主节点。

`ctx.onCleanup(fn)` 是给扩展用的 —— 扩展常在 `document` 上挂监听器：

```js
const onKey = (ev) => { /* ... */ };
document.addEventListener('keydown', onKey);
ctx.onCleanup(() => document.removeEventListener('keydown', onKey));
```

不清理的话，重新 setup 会**再挂一遍** —— 按一次键触发两次，
热重载几次就放大几次。

### 验证

`tests/check_web_uiext_reload.py` —— **真实浏览器**里：

| 检查 | 验证什么 |
|---|---|
| 清单里有扩展 | 后端这一层 |
| **扩展 URL 带内容指纹** | 绕过模块缓存 |
| **清单接口禁缓存** | 不会拿到旧指纹 |
| 扩展挂上了（版本 A） | 初始加载正常 |
| **改代码后不刷新页面也生效（版本 B）** | 热重载真的在工作 |
| **扩展没有叠加（只有一个宿主）** | 卸载干净 |

> **验证设计上的一个坑**：一开始我开了**两个浏览器会话** ——
> 第二个会话是新页面，它会直接加载最新代码。
> 那测的是「浏览器缓存没命中」，**不是热重载**。
> 热重载的定义是**同一个页面**里代码被换掉。
> 正确做法：一个会话里，页面在等「版本 B」，
> Python 侧在 `on_send` 回调里改文件。

---

## 十八、后端扩展热重载（第十三轮）

### 问题

前端扩展上一轮已经能热重载了，但**后端扩展**（Python）改了还要重启内核。
开发时改一行 Python 就得重启服务，太打断。

### 怎么做

Python 比前端难三点：

| 难点 | 怎么处理 |
|---|---|
| 模块一旦 import 就缓存在 `sys.modules`，改了文件不重新执行 | 用 `spec_from_file_location` **完全重新执行** |
| 旧 `ToolSpec` 里的 `fn` 指向旧函数对象 | **重建 ToolSpec** —— 不重建等于没生效 |
| 改了工具名，旧名字会留在表里变成残骸 | 按「工具属于哪个扩展」摘掉旧的 |

### 指纹

扩展的**内容指纹**（目录里所有 `.py`/`.json`/`.md` 的 hash）：

- `ExtensionRecord.fingerprint` —— 记录它
- `reload_extensions()` 重新扫描后比对 → 找出变了的

### 完全重新执行，而不是 importlib.reload()

`importlib.reload()` 会**保留模块的全局状态** —— 扩展改了代码但旧状态还在，
行为可能对不上。完全重新执行是干净的，代价是模块里的状态归零
（对工具扩展来说，这通常正是想要的）。

### 工具归属标记

每个扩展提供的 ToolSpec 上挂一个 `_uiext_owner` 标记。
它让代码能区分：

- registry 里已有的是**扩展自己上次加载的** → 直接替换
- registry 里已有的是**内置或 MCP** → **拒绝**（扩展不能覆盖内置）

> 这里踩过一个坑：最初我判断「要不要覆盖」用的是
> `old.fn is not new.fn` —— 扩展自己的新旧 fn 当然不同，
> 于是每次热重载都被当成「冲突」跳过了，**重载了但没生效**。
> 正确做法是看「这个工具是不是扩展提供的」。

### 接口

```bash
POST /api/extensions/reload
→ {"reloaded": ["ext1"], "errors": [], "tools": 3}
```

界面上的「扩展」小节里有个「重新加载」按钮就是调它。

### 信任边界不变

热重载**只对可信来源**（builtin / user）生效 —— project 信任的扩展
本来就不执行代码，热重载对它们无意义。**热重载不能绕过信任检查**
（有测试专门盯这条）。

## 十九、扩展管理界面（第十三轮）

设置页新增「扩展」小节（**在 MCP 那一节旁边，但分开** —— MCP 是
「挂载第三方服务」，扩展是「装在 Oryx 里的模块」，信任模型与生命周期
都不一样，混在一起会让人以为能互相覆盖）。

### 展示什么

| 块 | 内容 |
|---|---|
| 后端扩展 | 名称 / 类型 / **来源（信任级别）** / 说明 / 触发词 |
| 界面扩展 | 名称 / 挂载点 / 状态 / 说明 / **复用哪些核心类** |
| 命名冲突 | 谁被保留、谁被拒绝 |
| 声明不完整的工具 | schema 缺字段、没写 description |
| 加载出错 | 扩展代码报错的具体信息 |
| 「重新加载」按钮 | 改了代码点一下就生效，不用重启内核 |

### 两条规则**始终可见**（不只在出错时显示）

- **仓库里自带的扩展不会自动加载** —— clone 一个仓库不该等于让它在你的机器上执行代码
- **扩展不能覆盖内置工具** —— 这是安全边界，不是 bug

> 安全规则只在出错时才显示是不够的 —— 用户看不到就等于没有。

### 重载后只重画这一节

重画整个设置页会把用户**拉回第一页**，丢失上下文。
所以「扩展」节的渲染被抽成独立函数 `renderExtensionsInto(main)`，
重载后只重画它。

> 这里也踩过一个坑：最初重载后调 `renderSettings()` + `paint('extensions')`，
> 但 `paint` 是 `renderSettings` 的**局部函数**，外部访问不到 ——
> 结果重载后整页没重画，表格消失。**局部函数的作用域问题**，
> 在浏览器里表现为「代码看起来对但没效果」。

---

## 二十、扩展启停与安装（第十五轮）

「可自由支配」的最后一块拼图：**装、改、开关、卸，都在界面上点**，
不用去翻目录改文件。

### 三条设计原则

**1. 程序不改用户的扩展文件**

启停状态存在 Oryx 自己的配置里（`disabled_extensions` 黑名单）。
扩展文件是用户的东西 —— 程序擅自改它，用户下次 `git diff` 会一脸问号。

**2. 安装后默认禁用**

「装」和「让它跑」是**两个独立决定**。扩展是在**你的机器上执行代码**的东西，
装完就跑太草率。界面上会明确写出这一点。

**3. 删除是移进回收区，不是真删**

误删了能找回来。回收区在 `extensions/.trash/`，
要彻底删除得在回收区里再点一次。

### 停用优先于 profile

| 机制 | 含义 | 优先级 |
|---|---|---|
| `profile` | 「默认装哪套」 | 低 |
| `disabled_extensions` | 「用户主动关掉的」 | **高** |

用户主动关掉的，profile 也救不回来。

### 安装

```bash
POST /api/extensions/install  {"source": "D://我的扩展//my-ext"}
```

- 源目录必须有 `oryx.json`
- 目标 id 不能与已有扩展重名（避免悄悄覆盖）
- 复制时跳过 `__pycache__` / `.git` / `node_modules`
- **不执行源目录里的任何代码** —— 只是复制文件（有测试盯这条）
- 复制到一半失败会**收拾干净**，不留半个扩展

### 接口

| 接口 | 作用 |
|---|---|
| `GET /api/extensions` | 总览（含停用状态、界面扩展、冲突、声明问题） |
| `POST /api/extensions/{id}/enable` | 启用 |
| `POST /api/extensions/{id}/disable` | 停用 |
| `POST /api/extensions/install` | 从本地目录安装 |
| `DELETE /api/extensions/{id}` | 删除（移进回收区） |
| `GET /api/extensions/trash` | 回收区列表 |

## 二十一、后端热重载自动化（第十五轮）

上一轮的手动重载（点按钮）改成**自动**：agentd 起一个后台监视线程，
每 8 秒比对一次扩展指纹，变了就重载并发事件。

关掉：`ORYX_EXT_AUTORELOAD=0`。

周期比前端（5 秒）长 —— 后端扩展是 Python 代码，重载有代价
（模块重新执行、模块里的状态归零）。

重载后会发 `extensions.reloaded` 事件 —— 界面可以据此提示。

---

## 二十二、界面扩展的启停与删除（第十六轮）

上一轮只做了**后端扩展**的启停与安装，界面扩展（前端）没有 ——
两侧体验不一致。这一轮补齐。

### 一套停用名单管所有扩展

界面扩展的启停**不再单独一套机制**，而是和后端扩展共用
`disabled_extensions` 黑名单：

> 用户在界面上关掉的，**不管是后端还是界面扩展，都不加载**。

「一套机制管两边」比「两套机制各自一致」更不容易漂移 ——
后者改了一边忘了另一边，就会出现「关了后端的，界面还在跑」。

### 两个回收区

| 类型 | 回收区 |
|---|---|
| 后端扩展 | `extensions/.trash/` |
| 界面扩展 | `ui-extensions/.trash/` |

**不合并** —— 两者内容形态不同（Python 代码 vs JS 模块），
混在一个列表里会看不清。界面上用「类型」列区分。

### 界面上的操作

| 操作 | 位置 |
|---|---|
| 启用/停用 | 每一行 |
| 删除 | 每一行 |
| 安装后端扩展 | 顶部按钮 |
| 安装界面扩展 | 顶部按钮 |
| 恢复 / 彻底删除 | 回收区 |

删除前会 `confirm` 提示「会移进回收区，可以恢复」；
彻底删除会再提示一次「这一步不可逆」。

### 两个测试上的坑

**1. CDP evaluate 里访问不到模块作用域的变量**

`S`、`api` 这些是 ES Module 的导出，**不是 window 上的属性** ——
在 `Runtime.evaluate` 里直接用会报 `api is not defined` / `S is not defined`。
只能用 DOM，或者在页面里用 `fetch`（但要自己带令牌）。

**2. 启停是持久的，会污染后续测试**

启停写进 `config.json`，同一次测试运行里后面的用例看到的就是**被改过**的状态。
所以启停用例要**点两次**（第二次恢复原状），或者每个用例前复位。

> 另一个坑：复位代码里想用 `api()`，结果它访问不到 ——
> 于是改用「点两次恢复」这个更笨但可靠的办法。

## 二十三、扩展依赖（`requires` 真的生效了）

### 以前：`requires` 只是一句空话

`oryx.json` 里一直有 `requires` 字段，但**只被解析，从不被使用**。

```json
{ "id": "receipt-ocr", "requires": { "skills": ["web-research"] } }
```

写这句话的时候作者以为「我声明了依赖，系统会帮我检查」——
实际上扩展照样加载、照样出现在技能目录里、照样被路由到。
缺了 `web-research` 的 `receipt-ocr` 会跑到一半才卡住，
而没有任何地方告诉用户**为什么**。

> 声明了却不校验的依赖，比没有这个字段更糟：
> 没声明是「不知道」，声明了不校验是「说了假话」。

### 现在：依赖不满足 = 不加载

```
core/oryx/extdeps.py
  Requirement      一条依赖（类型 + id + 版本约束）
  DependencyIssue  一条问题（缺失 / 停用 / 版本 / 环 / 版本未知）
  DependencyReport 解析结果（谁被阻塞、为什么）
```

### 写法

`requires` 的键是**扩展类型**（单数复数都认），值是**依赖的扩展 id**：

```json
{
  "requires": {
    "skills":  ["web-research"],
    "experts": ["data-analyst"],
    "tools":   ["pdf-reader"]
  }
}
```

`extensions` 这个键表示**任意类型**（按 id 在所有类型里找）。

版本约束写在 id 后面，用 `@` 分隔：

| 写法 | 含义 |
|---|---|
| `"web-research"` | 任意版本 |
| `"web-research@>=1.1.0"` | 版本下限 |
| `"web-research@1.1.0"` | 精确版本 |
| `"web-research@<2.0.0"` | 版本上限 |

### 判定结果分两级

**致命 —— 该扩展不加载：**

| 情形 | 提示 |
|---|---|
| 依赖的扩展不存在 | `依赖的扩展 X 不存在` |
| 依赖的扩展被停用 | `依赖的扩展 X 已被停用` |
| 依赖的扩展版本不满足 | `依赖的扩展 X 版本为 A，不满足要求 >=B` |
| 依赖形成环 | `依赖形成环：A → B → A` |

**警告 —— 加载，但要说清楚：**

| 情形 | 提示 |
|---|---|
| 依赖的扩展没写版本号 | `依赖的扩展 X 没有版本号，无法验证是否满足 >=B` |

版本未知只警告不致命 —— 对方没标版本不等于它不能用。

### 传递闭包

A 依赖 B、B 依赖 C、C 缺失 → **B 与 A 都不加载**。

「C 没了所以 B 半残」是不够的 —— B 缺了它依赖的东西，
A 依赖 B 也就不成立。这条链要**一路推到底**。

实现上用**反向图 + 广度优先**（不是反复迭代整张表）：
迭代法每轮只能往前推一层，200 层的链要跑 200 轮，
而且轮数一多就是 O(n²)。

### 被阻塞的扩展**仍然列出**

不删除记录，只打一个 `blocked_reason` 标记。

删了的话用户在界面上就**看不到它**，也就不知道
「我装了个东西但它没跑」。界面上那行会写明原因：

```
⚠ 依赖的扩展 no-such-skill 不存在
```

### 拦在哪几层

| 层 | 行为 |
|---|---|
| `toolplug.load_tool_plugins` | **一个工具都不注册**，报告里写明「依赖不满足」 |
| `skills.catalog()` | 不列进目录 —— 让模型去用它只会卡住 |
| `use_skill` | 明确回「缺什么」，而不是让它按流程走一半 |
| 子 Agent 的技能注入 | 不注入正文，塞一句「该技能不可用」 |
| 设置页 | 扩展行上标原因 + 一张「依赖没满足」的卡片 |

### 为什么在注册表这一层解析

依赖是**跨类型**的（技能依赖专家、工具扩展依赖技能），
只有把全部类型的记录放在一起才看得出谁缺谁。

放在各自的 library 里解析，就得让每个 library 都认识其他类型 ——
那正是「加一种扩展类型要改一堆地方」的老毛病。

## 二十四、回收区会自动清理

### 以前：回收区只进不出

「删除是移进回收区，不是真删」这条规则是对的，但它有个漏洞 ——
**回收区自己不会清**。

装几十个扩展试一遍、每个都删掉，回收区就攒下几十份 Python 代码，
而用户根本没意识到那些还在占地方。它就是个**只涨不消的垃圾桶**。

### 现在：记一笔账，过期的自动清

```
core/oryx/extmanage.py
  move_to_trash()      移进回收区 + 记一笔账
  list_trash()         列出（含删除时间、已放多少天）
  purge_expired()      清掉超过保留期的
  trash_stats()        统计（几个、多大、多久会过期）
```

**账本放在回收区根部**（`.trash/_meta.json`），不往用户的扩展目录里塞文件 ——
恢复时又得记得删干净，忘了就等于往用户的扩展里掺了垃圾。

### 保留期

配置项 `extension_trash_retention_days`，默认 **30 天**
（与 Windows / macOS 回收站一致），设 `0` = **永不自动清理**。

### 什么时候清

| 时机 | 说明 |
|---|---|
| **启动时** | 覆盖「关机很久之后回来，回收区已经瘦身」这个真实场景 |
| 手动「清理过期项」 | 界面上一个按钮 |
| 手动「清空回收区」 | 一次清光，需要二次确认 |

不在每次监视轮询里清 —— 清理是**不可逆**的，
每 8 秒跑一次太频繁。

### 保留期内的**绝不**动

只有**账本里有删除时间**且**超过保留期**的条目才会被清。

- 保留期内的条目 —— 那是「误删能找回」的前提
- 没有删除时间的条目（老版本留下的）—— 不知道它活多久，
  **不替用户决定它该活多久**

### 界面上看得见

回收区那一节会显示：

- 每个条目的**删除时间**（3 天前 / 2 个月前）
- **保留期是多久**（超过 30 天的会被自动清理）
- **当前有几项已到期**（自动清理前给个预警）

自动清理不是后台悄悄发生的事 —— 判据摆在界面上。

## 二十五、注册表真的是唯一入口了

### 以前：注册表外面还有三条旁路

上一轮修好了 `requires` 依赖解析，但审计发现「注册表是唯一发现入口」
这个主张**实际上没做到** —— 有三条路绕过它：

| # | 旁路 | 后果 |
|---|---|---|
| 1 | `experts.py` / `agents.py` 各自扫目录，**从不问注册表** | 界面上点「停用」，注册表里没了，但这两个库照常加载 —— 停用**完全无效** |
| 2 | `SkillLibrary` 只扫仓库 `skills/`，注册表却还挂着用户目录 | 装在用户目录的技能**设置页显示着、模型用不了** |
| 3 | 插件技能只 `skills.add_root(...)`，**不进注册表** | 它的触发词、契约、`requires` 依赖全都不生效，依赖解析看不见它 |

还有一个更隐蔽的：`oryx/domains/skill.py` 在 server 侧**自己 new 一个
SkillLibrary** —— 于是技能编辑页看到的清单与模型看到的清单**不是同一份**。

### 现在：所有发现都走注册表

```
                    ┌──────────────────────────────────┐
  发现               │ Registry.discover()             │
  (Discovery)        │  · 扫哪些目录 → roots_of_kind()  │
                    │  · 什么被停用 → is_disabled()     │
                    │  · 什么缺依赖 → blocked_reason    │
                    └──────────────┬───────────────────┘
                                   │
        ┌──────────────────────────┼──────────────────────────┐
        ▼                          ▼                          ▼
  SkillLibrary              ExpertLibrary              AgentLibrary
  （扫注册表说的目录）        （问注册表停用没）           （问注册表停用没）
```

### 三条旁路各自的修法

**1. 停用对专家 / Agent 无效**

两个 library 改成接收 registry，加载时问一句：

```python
if self._registry is not None and self._registry.is_disabled(
        expert_id, path.parent.name):
    continue
```

**2. 用户目录的技能加载不了**

目录来源改由注册表说了算：

```python
roots = self._roots
if self._registry is not None:
    for p in self._registry.roots_of_kind("skill"):
        if p not in roots:
            roots = roots + [p]
```

这样「新增一个技能安装位置」**只改注册表一处**。

**3. 插件技能不进注册表**

`_init_plugins` 拆成两段：

| 时机 | 做什么 | 为什么 |
|---|---|---|
| `__init__` 早期 | `_register_plugin_skills()` —— 技能挂进注册表 | 技能也能声明工具，**晚一步注册，那部分工具就永远加载不到** |
| `_init_plugins` | 只管 MCP server 启动 | 职责分开，各自清楚 |

插件技能按 **PROJECT** 信任对待 —— clone 别人的仓库不该等于
授权它的插件在你机器上执行代码。

### 停用名单按**清单 id** 匹配

原来 `_load_root` 比的是**目录名**：

```python
if child.name in self._disabled:   # 目录名
```

而界面上显示的、写进 `disabled_extensions` 的是**清单里的 id**。
两者不同时，停用**完全失效**。

现在两个口径都认：

```python
if self._is_disabled(record.id, child.name):
    continue
```

### 信任级别按**根目录**给

`trust_map` 是**按 kind**给的 —— 同一个 kind 挂两个根时，
两个根会拿到同一个级别：

```python
# 以前：repo 里的技能被误标成「用户信任」
trust_map={"skill": Trust.USER if user_skills.exists() else Trust.BUILTIN}

# 现在：信任看的是「东西从哪来」
reg.add_root("skill", user_skills, Trust.USER)
reg.add_root("skill", root,       Trust.BUILTIN)
```

### 运行时切换也要重载 library

`_apply_disabled` 以前只重扫注册表 —— 技能库/专家库/Agent 库
**照旧留着那些扩展**，用户点了跟没点一样。

现在三个库都跟着 `reload()`。

### 域层要用内核那份权威清单

`oryx/domains/skill.py` 在 server 侧自己建库 → 绕过注册表。
现在改成走 RPC 问内核：

```
catalog.skills_detail → 内核那份以注册表为准的 skills 库
```

内核问不到时**退回本地读盘** —— 不能因为内核没起来就整页空白。
返回 `None`（问不到）与 `[]`（真的没有）要区分开。

### 一条铁律

> **发现只有一个入口，可见性只有一个裁决者。**

注册表说「这个扩展被停用了 / 依赖没满足」，那它就不该在任何地方
被用上 —— 技能目录、专家清单、Agent 清单、工具表、界面列表，
全都得看注册表的脸色。

## 二十六、界面扩展也能声明依赖

### 以前：界面扩展不能声明依赖

后端扩展有 `requires`，界面扩展**没有** —— 它能声明 `caps`、`baseCss`，
唯独不能声明「我需要什么」。

一个面板要靠另一个面板提供的组件、或者要靠某个后端技能供数据，
它只能**默默挂上去**，然后显示一个空壳。
用户看到的只是「怎么是空的」，没有任何地方说得出原因。

### 现在：同一套写法，跨端也能判

界面扩展的 `oryx.json` 里写 `requires`：

```jsonc
{
  "id": "research-panel",
  "requires": {
    "ui":      ["chart-panel"],       // 依赖另一个**界面扩展**
    "backend": ["web-research"],      // 依赖某个**后端扩展**
    "skills":  ["web-research"]       // 也可以写具体类型
  }
}
```

| 键 | 含义 |
|---|---|
| `ui` / `panels` | 另一个**界面扩展** |
| `backend` | 某个**后端扩展**（不分类型） |
| `skills` / `experts` / `tools` … | 具体类型的后端扩展 |
| `extensions` | 任意类型（界面或后端都行） |

版本约束照旧：`"web-research@>=1.1.0"`。

### 依赖由**后端**判定

界面扩展跑在浏览器里，它看不见后端扩展的存在。
只有后端知道「web-research 装了没」「另一个面板装了没」。

所以判定放在后端，前端只拿到三样东西：

```json
{
  "extensions": [ ...该挂的... ],
  "skipped": [
    {"id": "needs-missing", "reason": "依赖的界面扩展 no-such-panel 不存在"}
  ]
}
```

被拦下的扩展**不挂载**，但**看得见** —— 用户在设置页能拿到原因。

### server 进程怎么知道后端扩展

注册表在**内核进程**里，server 进程看不到。
新增一个轻量 RPC：

```
catalog.ext_index → [{kind, id, version}]
```

只给 id 与版本，不给工具清单与报告 —— 那是为了判依赖，不是给界面看的。

内核没起来时这个接口返回空列表 → **跨端依赖判不了**，
但界面扩展之间的依赖照判（那不需要内核）。

### 与后端扩展一致的判定语义

| 情形 | 结果 |
|---|---|
| 依赖不存在 | 拦下，说明缺什么 |
| 依赖被停用 | 拦下，说明「已被停用」（不是「不存在」） |
| 版本不满足 | 拦下，说明实际版本与要求 |
| 依赖成环 | 拦下，说明环 |
| 依赖没写版本号 | **只警告不拦** —— 没标版本不等于不能用 |

传递闭包、环检测、反向图 BFS —— 与 `extdeps` 同一套思路，
不写第二套解析器。

### 类型要对得上

「依赖 skills/web-research」不该被一个**同名的 tool 扩展**顶替：

```
依赖的扩展 web-research 是 tool 类型，不是 skill
```

写 `backend` 的才是「不分类型」。

## 二十七、版本号只有一个来源

以前版本号散在三处，各写各的：

| 位置 | 以前 |
|---|---|
| `core/oryx/__init__.py` | `0.1.0` |
| `core/oryx/mcp.py` 的 clientInfo | `0.1.0`（写死两处） |
| `CHANGELOG.md` 最新条目 | 1.9.0 |

`__init__.py` 那个早就该跟着走了 —— 用户报 bug 时说的版本号对不上，
排查的人按错版本找；MCP server 看到的客户端版本也是错的。

现在：

* `core/oryx/__init__.py` 的 `__version__` 是**唯一来源**
* `mcp.py` 从那里取，不写死
* `doctor.py` 从那里取
* **CHANGELOG 最新条目必须与它一致** —— 由测试守着

```
tests/test_version_consistency.py
  · 版本号格式合法
  · __version__ == CHANGELOG 最新条目
  · CHANGELOG 版本从新到旧排列
  · MCP 客户端报的是包版本，不是写死的
```

改了版本忘了写 CHANGELOG（或反过来），测试会红。

## 二十八、令牌不再出现在任何 JS 里

### 怎么发现的

审计前端扩展依赖时，顺手验证「扩展拿不到令牌」这条承诺 —— **不成立**：

```js
// 扩展自己的 index.js
import { ORYX_TOKEN } from '/web/modules/core.js';
```

真实浏览器里一跑，拿到的是完整令牌。

顺着这条线查下去，漏洞比扩展大得多：

```
· 令牌明文在 /web/modules/core.js 里
· 而 /web/** 路径**不需要令牌**就能读
· CORS 又放开任意 localhost:PORT 来源
  → 同机任意网页 fetch 一下就拿到令牌
  → 再带令牌调任意 API（含 /api/chat，那能间接执行命令）
```

也就是说，令牌的整个安全假设 ——「同机网页拿不到令牌」——
**从来没成立过**。

### 根因：令牌放在 JS 文本里

JS 文本是**任何能读到那个文件的东西**都能读的。
扩展能 import，同机网页能 fetch，浏览器缓存能存，
devtools 一看就有。

### 修法：令牌走 HttpOnly cookie

cookie 是唯一 JS 读不到的凭据通道：

```
服务端 GET / → Set-Cookie: oryx_token=…; HttpOnly; SameSite=Strict
前端 fetch 同源 → 浏览器自动带上，JS 碰不到值
```

于是：

* 令牌**不在任何 JS 响应里**（`core.js` 里那个导出直接删掉）
* 扩展 `import { ORYX_TOKEN }` → 抛 SyntaxError，压根拿不到
* 扩展 `document.cookie` → 看不到 `oryx_token`（HttpOnly）
* 扩展想外传凭据 → 没有凭据可传

前端那几处 `?t=` 参数（`<img>`、CSS `url()`、EventSource）
也一并去掉 —— 那些都是同源请求，浏览器自动带 cookie。

### 还差一道：服务端自己查 Origin

`SameSite=Strict` 挡不住**同站**的请求 ——
`localhost:9999` 与 `localhost:8765` 是同一个 registrable domain，算同站。

而 cookie 是浏览器自动带的，服务端看到「有 cookie」分不清是谁发的。

所以服务端自己看 `Origin`：

```python
if cookie_token and not _same_origin(request):
    return JSONResponse({"detail": "跨源请求不接受 cookie 鉴权"}, status_code=403)
```

**令牌走 header / `?t=` 的不受此限** ——
知道令牌就等于有凭据，那种调用本来就是给命令行与 relay 用的。

### 现在令牌从哪来

| 用法 | 怎么带 | 谁能用 |
|---|---|---|
| 页面自己 | cookie（浏览器自动） | 只有同源的页面 |
| 命令行 / 测试 | `X-Oryx-Token: <token>` | 知道令牌的人 |
| 外部工具 | `?t=<token>` | 知道令牌的人 |

### 记这个坑

> **凭据不能放在任何「读得到文件就能读到」的地方。**

JS 文件、Web Storage、URL 参数、DOM 属性 —— 全都是「读得到就能拿到」。
唯一读不到的是 HttpOnly cookie 与服务端会话。

## 二十九、caps 不再是装饰

### 以前：声明了没人管

`uiext.py` 一直写着「扩展可以声明 `caps`，前端据此决定给不给某些 API」，
但前端 `uiext.js` **从头到尾没读过 caps**。

这跟之前修的 `requires` 是同一类毛病：**声明了却不生效**，
比不声明更糟 —— 它让人以为有约束。

### 现在：声明了什么就给什么

`ctx.api` 被按扩展自己声明的能力收窄：

```js
// 扩展声明 caps: ["fs.read"]
await ctx.api('/api/tools/search', { method: 'POST' });
// → 403 {"detail":"该扩展没有「proc.exec」能力，拒绝调用 /api/tools/search"}
```

### caps 是「特权清单」不是「许可清单」

落进能力表的路径 + 方法需要对应能力；**其余路径默认放行**。

反过来写（没声明就全禁）会把没写 caps 的老扩展全弄坏 ——
caps 是后加的字段，老扩展没写，不该因为它就全哑了。

没声明的扩展**不限制**，但设置页会标出「未声明（等同全权）」，
让人知道它是什么。

### 判定要看**方法**

同一个路径不同方法，性质完全不同：

```
GET  /api/memory   读记忆
POST /api/memory   写记忆
DELETE /api/memory/{id}  删
```

只按路径判会把「读」也当成「写」——
扩展声明一个 `fs.read` 结果连读都被拒，那不是约束，那是刁难。

### 能力表

**这张表只有一份 —— 在后端**（`oryx/registry.py` 的 `UI_CAP_RULES`），
随 `/api/ui/modules` 的清单一起下发，前端直接用。

以前前端 `uiext.js` 里**自己也有一份** —— 两边各写各的，
改了后端前端不知道，界面上「这个能力到底管哪些接口」没人说得准。

| 能力 | 覆盖什么 |
|---|---|
| `fs.read` | `GET/HEAD /api/files/list`、`/api/files/preview`、`/api/fs/list`、`/api/image/thumb`、`/api/doc`、`/api/index/libraries`、`/api/index/library`、`/api/index/status`、`/api/memory`；`POST /api/index/search` |
| `fs.write` | `POST/PUT/PATCH /api/files/*`、`/api/fs/*`、`/api/index/scan`、`/api/index/clear`、`/api/memory` |
| `fs.delete` | `DELETE /api/files/*`、`/api/fs/*`、`/api/memory/*` |
| `proc.exec` | `POST/PUT/DELETE /api/chat`、`/api/agents`、`/api/teams`、`/api/approve`、`/api/interaction`、`/api/tools/search`、`/api/prompt/optimize`、`/api/apps/launch` |
| `net.egress` | `POST/PUT /api/paste`、`/api/install`、`/api/extensions/install`、`/api/ui-extensions/install`、`/api/remote/start` |
| `mcp.connect` | `POST/PUT /api/mcp` |
| `credential.read` | `GET/HEAD /api/drives`、`/api/apps`、`/api/clipboard`、`/api/remote/status`；`POST /api/sys/query` |

词表与后端 `registry.CAPABILITY_TIERS` 一致 ——
同一套词，界面扩展与后端扩展讲的是同一种话。

### 表是「能力 → 规则列表」，不是一个 methods + 一个 paths

因为**同一个路径，不同方法，不同能力**是真的：

```
GET    /api/memory   → 读记忆 → fs.read
POST   /api/memory   → 写记忆 → fs.write
DELETE /api/memory/x → 删记忆 → fs.delete
```

塞进一个 methods+paths 就必然歧义：`fs.read` 的 methods 里要加 POST
（`/api/index/search` 是 POST 的搜索），那 `POST /api/memory`
就同时落进 `fs.read` 和 `fs.write` —— 要两个能力都得声明，
「写记忆」变成了「还得会读记忆」。

拆成规则列表，每条规则自己带方法，歧义就没了。

### 有一份测试守着它不漂

`tests/test_caps_consistency.py` 锁住六件事：

1. 界面侧映射里的能力，后端词表得认识
2. 后端词表里的每个能力，**要么有映射，要么写清楚为什么没有**
3. 两边不能既映射又说明
4. 映射里的每个路径前缀，都得**匹配得到真实端点**
5. 前端源码里**不许再硬编码**能力路径表
6. 一份**高危端点清单**，每个都至少被一个能力覆盖

第 4 条立刻抓到了真问题：表里写着 `net.egress: /api/browser`，
而**根本没有任何 `/api/browser` 端点**（浏览器是 MCP 提供的）——
一条永远匹配不到的规则，看着专业，实际没用。

第 6 条则防「表没漏」：新加的敏感端点忘了归类，一样是漏。
漏掉的那些已经补上 —— 剪贴板、装扩展、启动应用、启动远程服务、搜索索引。

### 后端有、界面侧**不需要声明**的

| 能力 | 为什么界面侧不用声明 |
|---|---|
| `ui.render` | 界面扩展本来就在渲染界面，这个能力天然拥有 |
| `net.fetch` | 界面扩展不直接联网；要联网得通过后端扩展提供的工具 |
| `proc.spawn` | 界面扩展不能起进程；起进程是后端扩展的事 |

写清楚比「后端多了三个没人用的能力」有用 ——
后者只让人困惑，前者说明**每个能力在界面侧怎么办**。


### 但要诚实：caps 是**契约**，不是安全边界

扩展与核心**同源**。它铁了心绕开 `ctx.api` 直接 `fetch`，
前端拦不住 —— cookie 是浏览器自动带的。

caps 真正防的是：

* 「作者不小心越界」（从别的扩展复制粘贴代码）
* 「用户看不出这个扩展要干什么」—— 设置页显示声明的能力

真正的边界是那三条：**只从用户目录加载**、**要显式启用**、
**拿不到凭据本身**。

> 硬要把它做成安全边界，只有一条路：给每个扩展签发**独立凭据**，
> 服务端按凭据限权。但扩展与核心同源、主 cookie 又自动带 ——
> 除非把主 cookie 从扩展的环境里彻底隔开，否则这条路走不通。
> 与其做个假的，不如把真的那条守住。

## 三十、能力词典：写扩展的人不用翻源码

### 以前：能力到底是什么，只能猜

扩展在清单里写 `caps: ["fs.read"]`，那 `fs.read` 到底能干什么？

* 后端侧的语义散在各处注释里
* 界面侧只有一张路径映射（在文档里）
* 两边拼不出全貌 —— 写扩展的人只能挨个试

「声明了会不会被裁」「界面侧管不管这个能力」「界面侧管哪些接口」，
三个问题都没有一个地方能一次答完。

### 现在：一份词典，接口下发，界面摊开

`registry.capability_catalog()` 把散在几处的信息收成一处：

| 字段 | 是什么 |
|---|---|
| `tier` | 风险分级（`normal` / `elevated`） |
| `summary` | 一句话：这个能力能干什么 |
| `detail` | 具体覆盖什么、有什么后果 |
| `untrusted_allowed` | **不可信来源能不能声明** |
| `ui_paths` | 界面侧覆盖的 API 路径 |
| `ui_note` | 界面侧为什么不需要声明 |

它随 `/api/extensions` 一起下发，设置页上摊成一张**「能力速查表」**：

```
能力          风险    说明                      不可信来源   界面侧
fs.read       常规    读本机的文件与索引          能          /api/files/list、…
proc.exec     高危    执行命令                   否          /api/chat、…
credential.read 高危   读凭据与账号信息           否          /api/drives、…
```

另外每行扩展的「能力：xxx」上挂了**悬停说明** ——
鼠标停上去就能看到每个能力的一句话解释。

### 词典也是**被测出来的**

`tests/test_caps_consistency.py` 锁住：

* 每个能力都得有人话说明（summary 不许空）
* 词典条目与词表**一一对应**（不多不少）
* `untrusted_allowed` 必须与 `UNTRUSTED_ALLOWED` 一致
* `ui_paths` 必须与 `UI_CAP_RULES` 一致
* 每个能力要么有界面侧路径，要么写清为什么不用声明

也就是说，**词典和执行口径不会各说各话** —— 它们由同一份常量组装出来。

## 三十一、浏览器验证怎么跑

以前是手敲一长串 for 循环。一次跑十一个脚本，
中间哪个卡住就**整批卡死**（前面跑过的结果全丢）。

现在有汇总器：

```bash
python tests/run_web_checks.py            # 跑全部
python tests/run_web_checks.py --fast     # 跳过最慢的两个
python tests/run_web_checks.py caps uiext # 只跑名字里含这些词的
```

它做三件以前靠人盯着的事：

| 以前 | 现在 |
|---|---|
| 哪个卡住了整批陪葬 | 每个脚本有**超时保护**，卡住的判失败、不拖死整批 |
| 得盯着屏幕才知道谁过了 | 跑完给**汇总**：谁过、谁没过、谁被超时干掉、各花多久 |
| 端口被占 → 连的是别人的服务 | 跑之前先查端口，**提前说**谁会被跳过 |

### 一个真实的 hang

批量跑时整批卡死，查下来是 `websockets.connect` **没给超时**：

```python
async with websockets.connect(target["webSocketDebuggerUrl"]) as ws:
```

连接不上时它会**一直挂着** —— 批量跑时上一个 Chrome 还没退干净，
`json/new` 拿到的可能是已经死掉的 target。

现在所有 CDP 连接都带 `open_timeout`（8 处）。

> 网络连接一律给超时。
> 没有超时的连接 = 没有失败 = 永远在等 —— 这在测试里表现为「整批卡死」。

### 后来挖到的另外几个（都是「看起来像玄学」的那种）

**1. Chrome 的启动器进程会先退出**

Windows 上 Edge/Chrome 启动时，**启动器进程会很快退出**（把参数转交给真正的进程），
所以 `subprocess.Popen` 拿到的 PID 很快失效 → `taskkill /T` 找不到进程树 →
**真正的浏览器进程成了孤儿**（实测一轮残留 20 个 msedge，占着 CPU
把后面的脚本顶到超时）。

修法：走 CDP 发 **`Browser.close`**，让 Chrome **自己退出** ——
子进程一起走、端口干净释放。比杀进程树干净得多。

**2. `subprocess.run` 超时只杀直接子进程**

那个 python 脚本被杀了，但它起的 **Chrome 是孙进程**，还持有 stdout 管道 →
父进程的 `communicate()` 永远等不到 EOF → **整批卡死**，
脚本级的超时形同虚设（实测卡了 1800+ 秒）。

修法：超时后**杀进程树 + 关掉管道**。

**3. 端口探测用 `connect()` 会误判 TIME_WAIT**

刚用完的端口处于 TIME_WAIT 时，`connect()` 有时也会成功 → 误判成「被占用」→
脚本被白白跳过。改用 `bind()` 试：**能 bind = 没人监听**。

**4. `--dump-dom` 在 Edge 上输出为空**

`find_chrome()` 找到的是 Edge，而 Edge 的 `--dump-dom`
**returncode 0、stdout 0 字节** —— 语法检查于是把所有模块都误报成语法错误。

改用 CDP（`new Function(src)` 捕获 SyntaxError），顺带**一个 Chrome 查完所有模块**。

**5. 探针用固定 sleep 等页面**

`await asyncio.sleep(8)` 是**猜**页面要多久加载完。机器负载高时猜不够 ——
页面还没渲染完探针就查元素 → 落在空表上 → 断言失败。

表现为「**单独跑通过、批量跑失败**」，最难查的那种。
改成**轮询等 `readyState`** —— 等到好，不是猜多久。

### 偶发失败的排查法

批量跑时**每次挂的脚本都不一样**、单独跑每一个都过 —— 这种现象
**基本可以断定是负载相关的时序问题，不是功能 bug**。三条判据：

1. 失败点**每次不同** → 不是确定性问题
2. **单独跑都过** → 不是功能 bug
3. 批量跑才出现 → 与**系统负载**相关

根因通常是「页面加载用**固定 sleep**」—— 那是**猜**多久。
批量跑时前一个脚本的 Chrome 还在退出，系统慢，页面没加载完探针就查元素。

修法是「**等到好**而不是猜多久」：

```python
await wait_page_ready(ws, timeout=25.0)   # 轮询 document.readyState
await asyncio.sleep(6.0)                  # 保留原来的余量
```

余量**只加不减** → 不会变慢也不会变快，只是更稳。

> **同一件事有多处实现时，修一处等于没修。**
> `uiext_reload` 有自己的 CDP 实现（不走 `_webdrive._probe`），
> 单独给它修没用 —— 别的脚本也会犯同样的错。
> 所以要把逻辑抽到 `_webdrive.py` 让**所有**实现点共用。

另外：失败时**把脚本输出的末尾几行带出来** ——
「没有『全部通过』的结论」这句话不说明任何问题，光看它没法定位。

### 最后收口：所有脚本共用一套 Chrome 启停

以前每个脚本各写一份自己的 Chrome 启停 —— **每份都有自己的坑**。
现在统一在 `tests/_webdrive.py`：

| 函数 | 干什么 |
|---|---|
| `close_browser(cdp_port)` | CDP 的 `Browser.close` —— 让 Chrome 自己退 |
| `kill_tree(proc)` | `taskkill /T /F` 杀整棵进程树 |
| `cleanup_profile(profile)` | 删 profile；删不掉就改名脱钩 |
| `stop_chrome(proc, profile, cdp_port)` | **统一收工** —— 优雅退出 + 杀进程树 + 清 profile |

还有两个配套：

* 端口检查**只看服务端口** —— CDP 端口本来就该由 `alloc_cdp_port` 动态分配，
  固定端口被占就跳过整个脚本是错的
* 分配器的端口区间**避开各脚本的固定端口** —— 否则一个脚本的第 N 个探针
  会抢走另一个脚本的固定端口（实测 ext_deps 的第 3 个探针抢了 9349，
  正好是 ext_toggle 的）

> 顺带一提：**Bash 工具的超时上限是 600 秒**。
> 跑完整一轮浏览器验证要 10 分钟以上 → 得后台跑。

## 三十二、依赖没满足时，得说清「该做什么」

### 以前：只说现象

依赖没满足，设置页上写的是：

```
依赖的扩展 web-research 不存在
```

说了现象，没说**那是什么东西**（技能？专家？界面扩展？），
更没说**用户该做什么**。

用户看到这句话，还是不知道该去哪儿找它、装上还是启用。

### 现在：类型 + 修法

`DependencyIssue` 多了三个字段：

| 字段 | 是什么 |
|---|---|
| `required_kind` | **被依赖方**的类型（`skill` / `expert` / `ui` / …） |
| `required_kind_label` | 这类东西叫什么（「技能」「界面扩展」…） |
| `required_kind_hint` | 这类东西是干什么的 |
| `fix` | **用户该做什么** |

设置页上现在长这样：

```
blocked-ext（技能扩展）—— 依赖的扩展 web-research 不存在
（提供可复用的流程与知识）
→ 装一个叫「web-research」的技能（设置页 → 扩展 → 安装后端）
```

### 修法按「缺的原因」分

| 原因 | 建议 |
|---|---|
| `missing` 不存在 | 装一个叫「X」的{类型} |
| `disabled` 被停用 | 在设置页的扩展列表里重新启用「X」 |
| `version` 版本不够 | 把「X」升级到 `>=1.2.0` |
| `version-unknown` 没版本号 | 给「X」补上版本号（否则没法验证） |
| `cycle` 成环 | 改掉环上的任意一个依赖 |

注意 `disabled` 与 `missing` 的建议**不一样**：
被停用的东西**已经装着了**，建议「装上」是错的 —— 该建议「启用」。

### 界面扩展的依赖也是同一套

界面扩展声明 `requires` 时，缺依赖时同样会说清缺的是什么、
以及该做什么（`uiext.py` 的 `resolve_ui_dependencies` 里是同一套语义）。

## 三十三、设置页告诉用户扩展往哪放

以前设置页上写的是「用户数据目录/extensions」—— 一个**占位说法**。
用户不知道「用户数据目录」到底是哪儿。

而这个路径**本来就不能写死**：Windows 上优先落在 D 盘
（`D:/OryxData/data`），没有 D 盘才退回 `%APPDATA%\Oryx`。

所以只能运行时告诉：`/api/extensions` 现在下发 `user_dir`，
设置页底部直接写出真实路径与两个子目录。

> 文档里写死路径 = 文档在说谎。
> 路径会随机器变的东西，只能运行时给。

## 三十四、frontmatter 里的 requires 以前是**静默失效**的

### 问题

技能 / 专家 / Agent 的入口是 `.md` 文件，声明写在 frontmatter 里：

```yaml
---
name: my-skill
requires:
  skills: web-research
---
```

frontmatter 的解析器是**宽松**的 —— 只认单行 `key: value`：

```python
for line in meta_text.splitlines():
    if ":" in line:
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip("'\"")
```

这是**故意的**：描述里带冒号的老技能（「时间 12:30 开会」）不能用严格 YAML，
否则会解析失败。所以解析器一直保持宽松。

但 `requires` 是**嵌套**结构，宽松解析器认不出来 ——
`requires:` 后面那行被当成「没有值」，`skills: web-research` 成了另一个顶层键。

结果是：

```python
rec.requires   # → {}    空
```

**用户写了依赖、注册表里却是空的。**

### 为什么这比「没写」更坏

依赖没满足时扩展**不会被加载** —— 这是依赖保护。

用户以为「我声明了依赖，缺了它就不会跑」，实际保护**形同虚设**：
依赖照常被加载、照常在缺依赖的情况下跑一半就卡住。

「声明了却不生效」—— 跟之前修的 `requires`/`caps` 是同一类病，
但后果更重（一个是保护没生效，一个是装饰）。

### 修法：单独处理，不动既有解析器

`registry._parse_frontmatter_requires()` 专门抠 `requires` 块，
认三种写法：

```yaml
requires: skills=web-research, ui=chart-panel    # 单行
```
```yaml
requires:                                      # 嵌套
  skills: web-research
  ui: chart-panel
```
```yaml
requires:                                      # 嵌套列表
  skills:
    - web-research
    - file-search
```

**清单（oryx.json）仍然优先** —— 它是显式配置。

不动宽松解析器本身 → 零回归风险（老技能的描述照旧能带冒号）。

## 三十五、无注册表时，技能的触发词丢了

### 问题

技能库的元数据改成由注册表统一提供之后：

```python
if self._registry is not None:
    rec = self._registry.get("skill", name)
    if rec is not None:
        skill.triggers = rec.triggers   # ← 只有这一条路
```

**不带注册表**的调用方，触发词就没人给了 —— 技能永远匹配不上，
等于没有。而 `SkillLibrary` 的 docstring 写着「没有它（老调用方）
行为与改造前完全一致」—— 改完并不一致。

### 修法

没有注册表时**自己从 frontmatter 取**（改造前就是这样）：

```python
else:
    skill.triggers = tuple(
        t.strip() for t in re.split(r"[,，、]", meta.get("triggers", "")) if t.strip()
    )
    skill.eager = ...
    skill.allowed_tools = ...
```

> 专家库没这个问题 —— 它自己解析 frontmatter（scenarios / tags / personality…），
> 不依赖注册表。**Agent 库同理。**
> 只有技能库把元数据全交给了注册表，所以只有它退化。

### 是怎么发现的

写扩展指南时，给「技能的例子」补了验证测试 ——
它加载出来了，但**触发词是空的**。

> 文档里的例子如果跑不通，那份文档就是误导。
> 而「跑通了但少了一项」更隐蔽 —— 测试得**逐项断言**，不能只断言「加载成功」。

## 三十六、frontmatter 解析器只有一份

以前有**五份**（`memory` / `registry` / `skills` / `experts` / `agents`），
外加五个一模一样的正则 —— 而且**行为不一致**。

| 文件 | 认什么 |
|---|---|
| `memory.py` | 行内列表 `[a, b]`、布尔、多行列表 —— 但**硬编码只对 `paths` 生效** |
| 其余四份 | 只认单行 `key: value` |
| `agents.py` | 先试严格 YAML，失败才回退 |

后果是同一个 `triggers: [a, b]` 在不同代码路径下解析成不同结果，
`["[a", "b]"]` 这种脏数据会一路流到运行时。

现在统一在 `oryx/frontmatter.py`。

### 为什么不用 YAML（项目其实已经依赖它）

严格 YAML 会把带 `#` 的描述**静默截断**：

```yaml
description: 有 # 号 的文字     →   YAML 解析成 {'description': '有'}
```

`#` 前有空格就是 YAML 注释。**失败会报错，截断只是悄悄丢内容** ——
后者更危险。

而当年那条「严格 YAML 会让「描述里带冒号」的老技能解析失败」的顾虑，
**一半不成立**：`12:30` 的冒号后面没有空格，YAML 认得它是值的一部分。

所以现在用**宽松但支持一层嵌套**的自研解析器：

| 写法 | 结果 |
|---|---|
| `description: 有 # 号 的文字` | 完整保留（`#` 不当注释） |
| `description: 时间 12:30 开会` | 完整保留 |
| `triggers: a, b` | 字符串（`as_list` 拆开） |
| `triggers: [a, b]` | list |
| `eager: true` | bool |
| `requires:` + 缩进 | dict（嵌套块） |
| `triggers:` + `- a` | list（多行列表） |

### 取值口径统一

`as_list()` —— 把任何形态的值摊成字符串列表；
`as_bool()` —— 认 `true`/`1`/`yes`/`on`，
**不直接用 `bool(value)`**（那会把字符串 `"false"` 读成 True，
「关掉这个开关」反而变成「打开」）。

> `alwaysApply: false` 以前就是这样被读成 True 的。

### 有一份测试守着

`tests/test_frontmatter.py` 锁住两件事：
**该支持的写法都支持**，以及 **宽松语义不许丢**（`#`、冒号、引号原样保留）。
第二件更重要 —— 合并就是为了让行为一致，而不是为了变严格。
