# 插件目录

把插件放进这个目录，Oryx 启动时会自动识别。

## 支持的清单格式

两种都认，目录结构也一样：

```
plugins/
└─ my-plugin/
   ├─ .codebuddy-plugin/plugin.json     # WorkBuddy 格式
   ├─ .claude-plugin/plugin.json        # Claude Code 格式
   ├─ skills/                           # 技能（SKILL.md）
   ├─ agents/                           # 子 Agent 定义
   ├─ commands/                         # 斜杠命令
   └─ mcp/                              # 自带 MCP server
```

`plugin.json` 最小示例：

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "description": "一句话说明",
  "skills": "./skills",
  "mcpServers": {
    "my-server": {
      "command": "node",
      "args": ["mcp/start.mjs"],
      "env": { "FOO": "bar" }
    }
  }
}
```

## 会被加载什么

| 字段 | 行为 |
|---|---|
| `skills/` | 每个子目录的 `SKILL.md` 进入技能目录，模型可 `use_skill` 调用 |
| `mcpServers` | 发现但不启动，**必须在配置里显式 enable** |
| `agents/` `commands/` | 目前只识别路径，尚未接入 |

## 启用 MCP server

外部 MCP server 是可执行程序，默认一律不启动。在 `%APPDATA%\Oryx\config.json` 里写：

```json
{
  "mcp_servers": {
    "my-server": { "enabled": true }
  }
}
```

启用后，它声明的工具会以 `mcp__<server>__<tool>` 的名字出现，风险等级按清单里的注解推断：

| 注解 | 判定 |
|---|---|
| `readOnlyHint: true` | L0，直接执行 |
| `destructiveHint: true` | L2，需要确认 |
| 都没有 | L1，执行并留痕 |

## 安全提醒

- 插件里的代码会以你的用户权限运行，只装你信任的。
- 技能文本会进入模型上下文，第三方技能可以写任何指令——装之前先读一遍 `SKILL.md`。
- 禁用的 server 不会占用任何进程或端口。
