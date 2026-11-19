# 写扩展指南

面向**要写一个扩展的人**。架构与设计取舍见 `EXTENSION-SYSTEM.md`，
这份只讲「怎么写」「怎么装」「为什么我的没加载」。

---

## 三分钟起步

### 一个后端扩展（提供工具）

```
<用户数据目录>/extensions/my-tool/
├── oryx.json      ← 声明层：可审计，不含代码
└── tools.py       ← 实现层：只有函数
```

`oryx.json`：

```json
{
  "apiVersion": "oryx/v1",
  "kind": "tool",
  "id": "my-tool",
  "version": "1.0.0",
  "displayName": "我的工具",
  "description": "做一件别人没做的事",
  "caps": ["fs.read"],
  "tools": [
    {
      "name": "my_action",
      "description": "干一件事",
      "parameters": {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "要处理的东西"}},
        "required": ["query"]
      },
      "risk": "L0_READ",
      "entry": "tools.py:my_action"
    }
  ]
}
```

`tools.py`：

```python
def my_action(query: str) -> dict:
    """实现层只提供函数。返回约定：ok / content / meta。"""
    return {"ok": True, "content": f"处理了 {query}", "meta": {}}
```

**为什么声明与实现要分开**：风险等级写在**清单**里而不是代码里 ——
这样「这个工具会干什么、有多危险」可以在**不执行任何代码**的情况下审阅。
实现层只提供 `fn`。

`entry` 的写法：`"文件.py:函数名"`（也支持 `"文件.py:类.方法"`）。
不写 `entry` = **只声明元数据**，不注册成可调用的工具 ——
这是「项目仓库里带来的扩展」的合法形态（它们不被允许执行代码）。

### 一个界面扩展（往界面上加东西）

```
<用户数据目录>/ui-extensions/my-panel/
├── oryx.json
└── index.js
```

`oryx.json`：

```json
{
  "id": "my-panel",
  "name": "我的面板",
  "mount": "rail",
  "enabled": true,
  "caps": ["fs.read"]
}
```

`index.js`：

```js
export default {
  setup(ctx) {
    const el = document.createElement('div');
    el.textContent = '我的面板';
    ctx.mount.append(el);
  }
};
```

### 一个技能（SKILL.md）

```
<用户数据目录>/skills/my-skill/
└── SKILL.md          ← 必需
```

`SKILL.md` = **YAML frontmatter + 正文**：

```markdown
---
name: 我的技能
description: 什么场景该用它
triggers: 触发词A,触发词B
caps: fs.read
---

# 我的技能

## 什么时候用

……

## 步骤

1. ……
2. ……
```

### 一个专家（EXPERT.md）

```
<用户数据目录>/experts/my-expert/
└── EXPERT.md         ← 必需
```

```markdown
---
name: 我的专家
description: 一句话说明这个专家擅长什么
scenarios: 架构设计, 代码审查
tags: 冷静的技术搭档
personality: 严谨,克制
speaking_style: 不做多余寒暄，直奔问题核心
use_cases: 适合技术方案、代码审查
self_intro: 我是你的技术搭档。……
---

## 工作方式

- …
```

---

## 装在哪、怎么启用

| 类型 | 位置（相对用户数据目录） | 装完的状态 |
|---|---|---|
| 后端扩展（工具） | `extensions/<id>/` | **默认不启用** —— 要在设置页点「启用」 |
| 界面扩展 | `ui-extensions/<id>/` | `enabled: true` 就会加载 |
| 技能 | `skills/<id>/SKILL.md` | 装上就有 |
| 专家 | `experts/<id>/EXPERT.md` | 装上就有 |
| Agent | `agents/<id>/AGENT.md` | 装上就有 |

**用户数据目录在哪**：设置页 → 「扩展」标签 → 底部写着实际路径。
这个路径**不是固定的** —— Windows 上优先落在 D 盘
（`D:\OryxData\data`），没有 D 盘才退回 `%APPDATA%\Oryx`。

> **仓库里自带的扩展不会自动加载。**
> clone 一个仓库不该等于让它在你的机器上执行代码。
> 只有你亲手放进用户目录的才算数。

设置页 → 「扩展」标签能看到你装的每一个扩展，
以及它**要什么能力**、**缺什么依赖**。

---

## 清单能声明什么

### 后端扩展的 `oryx.json`

| 字段 | 是什么 |
|---|---|
| `id` | 唯一标识。**不写就用目录名** |
| `kind` | `tool`（提供工具）/ `skill` / `expert` / `agent` / … |
| `displayName` / `name` | 界面上显示的名字 |
| `description` | 一句话说明 |
| `version` | 版本号 —— **依赖的版本约束靠它验证**，不写就验证不了 |
| `triggers` | 触发词（技能用），逗号分隔 |
| `eager` | 是否常驻 |
| `allowedTools` | 允许调用的工具 |
| `caps` | 需要哪些能力，见下 |
| `requires` | 依赖谁，见下 |
| `tools` | 工具声明（schema + 风险等级 + 入口） |
| `hooks` | 钩子 |
| `contracts` | 可执行契约：完成条件、失败时的行为 |
| `apiVersion` | 清单格式版本，目前是 `oryx/v1` |

### 工具声明的字段

| 字段 | 是什么 |
|---|---|
| `name` | 工具名（模型看到的就是它） |
| `description` | 这个工具干什么 —— **模型靠它决定要不要用**，写清楚点 |
| `parameters` | JSON Schema |
| `risk` | 风险等级，见下 |
| `entry` | `文件.py:函数名` —— 代码在哪 |

**风险等级**：

| 值 | 含义 |
|---|---|
| `L0_READ` | 只读 |
| `L1_WRITE` | 写 |
| `L2_DESTRUCTIVE` | 破坏性 |
| `L3_FORBIDDEN` | 禁止 |

> **不写 `risk` 一律按 `L2_DESTRUCTIVE` 处理** —— 宁可严一点：
> 把未知工具当成破坏性操作，它就会被审批流程拦一下。

### 界面扩展的 `oryx.json`

| 字段 | 是什么 |
|---|---|
| `id` | 唯一标识 |
| `name` / `description` | 显示用 |
| `version` | 版本号 |
| `mount` | 挂在哪：`rail` / `railfoot` / `topbar` / `side` |
| `icon` | 图标（SVG 字符串） |
| `order` | 排序，小的在前 |
| `enabled` | 默认是否加载 |
| `baseCss` | 要复用核心的哪些类（写 `btn`、`inp`…，不带点） |
| `caps` / `requires` | 同上 |

### 技能 / 专家 / Agent：frontmatter 为主

这三类的入口是 **`.md` 文件**（`SKILL.md` / `EXPERT.md` / `AGENT.md`），
声明写在 **frontmatter** 里。

| 类型 | 文件 | frontmatter 字段 |
|---|---|---|
| 技能 | `SKILL.md` | `name`、`description`、`triggers`、`eager`、`allowed-tools`、`caps`、`requires` |
| 专家 | `EXPERT.md` | `name`、`description`、`scenarios`、`tags`、`personality`、`speaking_style`、`use_cases`、`self_intro` |
| Agent | `AGENT.md` | `name`、`subtitle`、`icon`、`description`、`kind`、`tools`、`skills`、`disallowedTools`、`model`、`permissionMode`、`max_turns`、`memory`、`initialPrompt` |

**同目录放一个 `oryx.json` 是可选增强** —— 放了就能多声明：

```jsonc
// <用户数据目录>/skills/my-skill/oryx.json
{
  "id": "my-skill",
  "version": "1.0.0",
  "triggers": ["触发词A", "触发词B"],
  "caps": ["fs.read"],
  "requires": { "skills": ["web-research"] }
}
```

清单里的值**优先**于 frontmatter。

### frontmatter 的两个限制

**1. 只认单行 `key: value`。**

描述里带冒号没问题（`description: 时间 12:30 开会` 整句都是值），
但**列表**要写成逗号分隔的字符串，不能写成 YAML 列表：

```yaml
triggers: 触发词A,触发词B      # ✅ 认
```

**2. `requires` 是嵌套结构，得单独写。**

frontmatter 里的 `requires` 认三种写法（**清单优先**）：

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

> 以前 frontmatter 里的 `requires` 是**静默失效**的 ——
> 用户写了依赖、注册表里却是空的，依赖保护形同虚设。
> 现在认了（`oryx.json` 仍然优先）。

---

## caps：声明你需要什么能力

```json
{ "caps": ["fs.read", "net.egress"] }
```

**能力是一份「特权清单」** —— 声明了哪些能力，
调用需要别的能力的接口时会被**明确拒绝**并说清缺哪个。

### 完整的词表

| 能力 | 管什么 | 不可信来源能声明吗 |
|---|---|---|
| `fs.read` | 读文件、查索引、读记忆 | 能 |
| `fs.write` | 写文件、建索引、写记忆 | 能 |
| `fs.delete` | 删文件、删记忆 | **否** |
| `net.egress` | 把数据送出去、装扩展、开远程访问 | 能 |
| `net.fetch` | 联网抓取 | 能 |
| `proc.exec` | 执行命令、调度 Agent | **否** |
| `proc.spawn` | 启动进程 | **否** |
| `credential.read` | 读凭据、账号、剪贴板 | **否** |
| `ui.render` | 往界面上渲染 | 能 |
| `mcp.connect` | 连接第三方 MCP server | 能 |

**「不可信来源」** = 项目仓库里带来的扩展（PROJECT 信任）。
它们声明的高危能力在**加载时**就被裁掉了 —— 你会在设置页看到
「已按信任级别裁掉：xxx」。

### 两个必须知道的坑

**1. 没声明 caps = 不限制。**

不是「没声明就啥也不能干」。caps 是后加的字段，
老扩展没写，不该因为它就全哑了。
但设置页会标出「未声明（等同全权）」。

**2. caps 是契约，不是安全边界。**

界面扩展与核心**同源** —— 它铁了心绕开 `ctx.api` 直接 `fetch`，
前端拦不住。caps 防的是「作者不小心越界」和「从别的扩展复制粘贴代码」。

界面扩展侧，每个能力管哪些 API 路径，见设置页的「能力速查表」。

---

## requires：声明你依赖谁

```json
{
  "requires": {
    "skills": ["web-research"],
    "ui": ["chart-panel"],
    "backend": ["my-tool"]
  }
}
```

| 键 | 依赖什么 |
|---|---|
| `skills` / `experts` / `agents` / `tools` | 那一类的扩展 |
| `ui` | 界面扩展 |
| `backend` | 任意后端扩展 |
| `extensions` | 任意类型 |

### 版本约束

```json
{ "requires": { "skills": ["web-research@>=1.2.0"] } }
```

* 对方**没有版本号** → 只警告，不拦（没法验证）
* 对方版本**不满足** → 拦下，并说清「实际是 X，要求 Y」

### 依赖没满足会怎样

依赖没满足的扩展**不会被加载** —— 缺了依赖还硬跑，只会跑一半就卡住。
设置页会告诉你：

* 缺的是**什么类型**（技能？专家？界面扩展？）
* **该怎么做**（装上它 / 重新启用它 / 升级它）

界面扩展依赖另一个界面扩展时，依赖没满足就不挂载。

---

## 界面扩展的 ctx

```js
export default {
  setup(ctx) {
    ctx.$('selector')    // 只在扩展自己的 shadow root 里查
    ctx.$$('selector')   // 同上，返回数组
    ctx.api('/api/state')// 调后端（带 cookie，按 caps 收窄）
    ctx.on('view:changed', fn)  // 订阅核心事件
    ctx.emit('my:event', data)  // 发事件给核心与其他扩展
    ctx.addStyle(css)    // 注入扩展自己的样式
    ctx.mount            // 扩展的根节点（在 shadow root 里）
    ctx.onCleanup(fn)    // 卸载时跑 —— 不清理的话监听器会重复注册
  }
};
```

**样式是隔离的** —— 你的 CSS 不会漏到核心界面，核心的 CSS 也不会漏进来
（除了 CSS 自定义属性，它们照常继承，所以 `var(--ink)` 在 shadow root 里直接能用）。

**令牌拿不到** —— 令牌在 HttpOnly cookie 里，JS 碰不到值。
所以扩展没法把凭据外传给第三方。

---

## 为什么我的扩展没加载

按这个顺序查：

| 现象 | 多半是 |
|---|---|
| 设置页里根本看不到 | 目录放错了 —— 应该在用户数据目录的 `extensions/` 或 `ui-extensions/` 下 |
| 看到了但状态是「未启用」 | **装完默认不启用** —— 点「启用」 |
| 技能/专家/Agent 装上不生效 | 文件名写错了 —— 要叫 `SKILL.md` / `EXPERT.md` / `AGENT.md`（大小写敏感） |
| frontmatter 里的触发词不生效 | 写成了 YAML 列表 —— 要写成逗号分隔的字符串 |
| frontmatter 里的 `requires` 没生效 | 现在认了（单行/嵌套/列表三种写法）—— 如果还是不生效，八成是 `oryx.json` 里写了空的 `requires` 把 frontmatter 的盖掉了 |
| 界面扩展没出现在界面上 | `enabled: false`，或者 `mount` 写的挂载点不存在 |
| 界面扩展挂了个空壳 | 看设置页的「依赖没满足」 —— 它在等某个依赖 |
| 后端工具没出现 | 设置页 → 扩展 → 看「工具加载报告」，那儿写了为什么被拒 |
| 工具报「找不到代码文件」 | `entry` 写的文件名不对（要带 `.py`） |
| 工具报「入口找不到」 | `entry` 的函数名不对，或那个文件里没这个函数 |
| 扩展加载报「缺少依赖」 | 依赖没装上 / 被停用 / 版本不够 —— 设置页有修复建议 |
| 声明的 caps 没生效 | 项目仓库带来的扩展，高危能力**会被裁掉** —— 设置页有标注 |
| 改了代码不生效 | 刷新页面 —— URL 上带内容指纹，改了就换 |

---

## 一条底线

> **扩展是在你的机器上执行代码的东西。**
>
> 所以：装完默认不启用、只从用户目录加载、
> 高危能力要显式声明、声明了不等于给了 —— 全都收在这套机制里。
>
> 你装进来的每个扩展，都是你自己授权的。
