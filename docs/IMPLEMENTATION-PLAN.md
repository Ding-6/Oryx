# Oryx 实施规划：融合 Marvis 与 WorkBuddy

> 依据：[逆向工程报告](REVERSE-ENGINEERING.md)
> 原则：功能对等，实现独立。不复制代码、提示词、加密资产与品牌。

---

## 一、目标形态

一个**桌面级 AI 工作台**，页面结构对齐两个参考产品：

```
┌──────────┬────────────────────────────┬──────────────────┐
│ 左栏      │ 中间：对话                  │ 右栏：执行面板    │
│          │                            │                  │
│ ＋ 新建   │  消息流（流式）             │  [步骤][任务]     │
│ ───────  │                            │  [终端][产物]     │
│ 会话列表  │  ┌──────────────────────┐  │                  │
│ · 会话1   │  │ 执行计划确认卡        │  │  实时步骤         │
│ · 会话2   │  └──────────────────────┘  │  风险徽章         │
│          │                            │  耗时             │
│ ───────  │  ┌──────────────────────┐  │                  │
│ 技能      │  │ 输入框  优化 发送     │  │                  │
│ 设置      │  └──────────────────────┘  │                  │
└──────────┴────────────────────────────┴──────────────────┘
```

顶栏：模式（问答/规划/执行）· 专家 · 风格 · 模型 · 用量 · 外观。

---

## 二、阶段划分

### P1 会话体系与三栏布局（本轮）

| 项 | 说明 | 依据 |
|---|---|---|
| 多会话 | 新建/切换/删除，列表显示首条消息摘要 | 两者都有会话列表 |
| 三栏布局 | 左导航 + 中对话 + 右执行面板（标签页） | Marvis 状态板 / WorkBuddy 侧栏 |
| 执行面板标签 | 步骤 / 任务清单 / 终端 / 产物 | Marvis 的四要素 + WorkBuddy Artifact |
| 消息持久化 | 切换会话能恢复历史 | 已有 messages 表 |

### P2 设置中心（本轮）

| 项 | 说明 | 依据 |
|---|---|---|
| 模型能力矩阵 | 每个 provider 显示：上下文窗口 / 工具调用 / 视觉 / 计费 | WorkBuddy 24 模型 × 4 维 |
| 自定义模型 | 表单新增 provider，不手改 JSON | 已有 API |
| 外观 | 明暗主题 + 自定义背景 | 本轮已做 |
| 工作区 | 切换目录、查看允许范围 | 已有 API |
| MCP / 插件 | 已发现插件列表 + 启用开关 | 已有插件发现 |

### P3 能力市场（本轮）

| 项 | 说明 | 依据 |
|---|---|---|
| 技能卡片 | 列出全部技能 + 描述 + 调用入口 | Marvis 技能目录 |
| 专家卡片 | 6 个专家，一键切换 | WorkBuddy curated experts |
| 插件卡片 | 已安装插件 + MCP server 状态 | WorkBuddy 插件市场 |
| 批量导入 | 从本地目录一次导入多个 | 已有 API |

### P4 工具按需加载（本轮）

| 项 | 说明 | 依据 |
|---|---|---|
| `tool_search` | 按关键词检索工具，返回精简 schema | WorkBuddy `ToolSearch` |
| 延迟注入 | 默认只给核心工具，其余靠检索召回 | `DeferToolLoading` |
| 工具清单页 | 设置里可见全部 51+ 工具及风险等级 | — |

### P5 多 Agent 团队 —— 2026-10-21 完成 ✅

落地在 `core/oryx/team.py` + 右栏「团队」标签。7 个工具：`team_create` / `team_delete` / `team_list` / `team_add_member` / `team_send_message` / `team_dispatch` / `team_inbox`。

**关键设计：投递与执行分离。** `team_send_message` 只往信箱里投，不触发模型；`team_dispatch` 才让成员真正干活并把结论寄回 `main`。这样主 Agent 可以先派布完再统一开工，不会一句「收到」就烧掉一堆 token。

团队只活在内存（临时编队，不进 DB），成员工具白名单仍是硬边界——组队不会放宽任何权限。成员回报的收件人 `main` 是编排者本人，不算团队成员。

### P6 系统设置工具集 —— 2026-10-21 完成 ✅

落地在 `core/oryx/syssettings.py`：8 个工具覆盖电源/显示/输入设备/个性化；所有写操作标 L2，对齐 Marvis `toolkit.{power,display,input_device}`。

### P7 特性开关与部署形态 —— 2026-10-21 完成 ✅

落地在 `core/oryx/features.py` + `config/features.example.yaml`：21 个 `Enable* / Disable*`，四种 profile（personal/team/selfhosted/cloud），受 `EnableFeatureFlags` 守门；API：`GET /api/features`、`POST /api/features/toggle`。

### P8 沙箱与跨端策略分级 —— 2026-10-21 完成 ✅

落地：

- **命令沙箱** `core/oryx/sandbox.py`：子进程环境变量剔除密钥（KEY/TOKEN/SECRET/PASSWORD…，系统变量白名单保留）+ 工作目录监禁（默认工作区，root 参数受策略引擎 allowed_roots 检查）+ 超时与输出截断。工具 `shell_sandbox` 定级 L1——危险命令黑名单仍会把命中项升级 L2 走审批，沙箱只缩爆半径不替代审批。受 `DisableSandbox` 开关控制（全 profile 默认开）。
- **跨端策略分级**（对齐 Marvis `security_policy`）：`RemoteHost` 三级——`1 只读`（仅看屏，输入全拒）/ `2 可控`（指针+文本+安全键放行，win/ctrl/alt/delete 系统键拦截）/ `3 完整`。`remote_start` 加 `policy` 参数，配置键 `remote_policy`，状态与 `remote.event`（`input_blocked`）均带策略信息。

### Bonus：内置 REA 预设 —— 2026-10-21 完成 ✅

`loop.py` 的 `_init_plugins` 在 `EnableReaMcp` 打开时拉起 `npx -y rea-agents mcp`，把 `rea` 作为 MCP server 接进 Oryx——任何工具都会以 `mcp__rea__<tool>` 出现在模型面前。这是真正把 reverse-engineering 能力融进 Oryx 的最小动作。

---

## 三、数据模型增量

```sql
-- 已有
tasks(id, goal, mode, provider, status, created_at, updated_at)
messages(id, task_id, role, content, ts)
steps(id, task_id, seq, tool, args, state, risk, result, error, ...)
todos(id, task_id, seq, title, status, ...)
audit / usage / memory
```

本轮只加查询，不加表：

- 会话列表 = `tasks` + 首条 `messages` 摘要 + `steps` 计数
- 会话详情 = `messages` + `steps` + `todos`

---

## 四、API 增量

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/sessions` | 会话列表（含摘要、消息数、时间） |
| GET | `/api/sessions/{id}` | 会话详情（消息 + 步骤 + 待办） |
| DELETE | `/api/sessions/{id}` | 删除会话 |
| GET | `/api/capabilities` | 工具清单（分组 + 风险 + 来源） |
| POST | `/api/tools/search` | 工具检索（对应 `ToolSearch`） |

---

## 五、验证方式

```bash
cd core && python -m pytest tests -q       # 全量
python -m oryx                              # 起服务，浏览器验收
```

验收清单：

1. 新建会话 → 列表出现 → 切换能恢复历史
2. 执行面板四个标签都能切换且有内容
3. 设置里能新增 provider 并显示能力矩阵
4. 能力市场能看到技能/专家/插件并切换
5. 工具检索能按关键词找到工具
6. 明暗主题与自定义背景保持可用

---

## 六、不做

- 不复制两个产品的 UI 视觉、图标、文案
- 不接入腾讯云服务、微信 SDK、应用宝
- 不实现原生 CEF/Qt 壳（继续用 Tauri + Web）
