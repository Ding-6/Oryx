"""领域装配：把各域路由挂到 app。

对齐 Marvis 的 bootstrap —— 集中登记，业务代码不散落在框架文件里。
新增一个领域 = 在这里加一行 register(app, ...)。

**依赖怎么给**
--------------
* `store` —— 数据库（会话、记忆、待办…）
* `config` —— 配置（主题、开关、扩展白名单…）
* `agent` —— 内核 RPC 代理（执行面能力）

**域之间不互相依赖** —— 需要跨域时用**延迟导入**
（如 `ui` 域要用扩展域的索引函数，就在函数体里 import）。
"""
from __future__ import annotations

from . import (
    app as app_domain,
    appearance,
    chat,
    checkpoint,
    extension,
    feature,
    file,
    index,
    mcp,
    memory,
    model_config,
    overview,
    persona,
    preference,
    remote,
    schedule,
    session,
    skill,
    system,
    team,
    tools,
    ui,
    workflow,
)

# 领域清单：(模块, 说明)。装配顺序不影响路由匹配，但按前缀排好读。
DOMAINS = (
    (session, "会话"),
    (model_config, "模型配置"),
    (skill, "技能"),
    (schedule, "自动任务与终端"),
    (checkpoint, "检查点"),
    (workflow, "工作流编排"),
    (appearance, "外观：主题与背景"),
    (feature, "特性开关"),
    (memory, "长期记忆"),
    (remote, "跨端远控"),
    (ui, "界面扩展与核心样式子集"),
    (extension, "扩展：安装、启停、回收区"),
    (tools, "工具检索与语义索引状态"),
    (mcp, "MCP 服务器清单与启停"),
    (file, "文件产物、工作区、项目文档"),
    (persona, "人设与子 Agent 的增删改查"),
    (team, "多 Agent 团队编队"),
    (index, "本地索引、磁盘、图库"),
    (preference, "交互模式、回答风格、计算模式"),
    (chat, "对话、剪贴板、粘贴、审批"),
    (app_domain, "应用安装、启动、图标、自定义应用"),
    (system, "系统状态、自检、提示词优化、环境查询"),
    (overview, "大屏总览：编队、运行、主动建议"),
)


def register_all(app, *, store, config, agent, bus, api_token: str = "") -> None:
    """装配全部领域。

    agent 在这里展开成各域真正需要的依赖（目前只有 RPC 代理），
    而不是把整个 AgentLink 到处传 —— 领域与领域之间不互相依赖。
    """
    session.register(app, store)
    model_config.register(app, config)
    skill.register(app, agent)
    schedule.register(app, agent)
    checkpoint.register(app, agent)
    workflow.register(app, agent)
    appearance.register(app, config, api_token)
    feature.register(app)
    memory.register(app, store)
    remote.register(app, agent)
    ui.register(app, config, agent)
    extension.register(app, agent)
    tools.register(app, agent)
    mcp.register(app, agent)
    file.register(app, config, agent)
    persona.register(app, config, agent)
    team.register(app, agent, bus)
    index.register(app, config, agent, store)
    preference.register(app, config)
    chat.register(app, agent, store, config, bus)
    app_domain.register(app, agent)
    system.register(app, config, agent, store, bus)
    overview.register(app, agent, store)
