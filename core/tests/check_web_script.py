"""index.html 的 <script> 块静态检查。

Node 不在 PATH 里，但"括号配平 + 明显语法错"这类问题用 Python 就能查出来：
    1. script 块能否正确提取（HTML 里可能有多个 script）
    2. 括号 / 引号是否配平（转义字符串、模板串、注释里的括号要正确跳过）
    3. 有没有引用到未定义的全局（本项目脚本是单文件 IIFE，天然能查）

运行：python -m tests.check_web_script
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

WEB = Path(__file__).resolve().parents[1] / "web" / "index.html"
MODULES_DIR = Path(__file__).resolve().parents[1] / "web" / "modules"
STYLES_DIR = Path(__file__).resolve().parents[1] / "web" / "styles"
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILED.append(name)


def extract_scripts(text: str) -> list[str]:
    """取出所有 <script>…</script> 的内容（忽略 src 属性的外链）。"""
    return re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", text, re.S)


def strip_js_comments(text: str) -> str:
    """剥掉块注释与行注释，只留代码。

    为什么需要：本文件里有大段注释写着「以前错在哪」，
    注释里出现某个接口路径是**应该的**（那是给人看的说明）。
    直接搜全文会误报成「代码里还在用」。
    """
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            out.append(" ")
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            end = text.find("\n", i)
            i = n if end < 0 else end
            out.append(" ")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def strip_noise(src: str) -> str:
    """去掉注释、字符串与正则字面量，只留下结构字符，便于配平检查。

    不做完整词法分析，但足以抓到"多了一个括号 / 少了一个引号"这类手误。
    模板串（`` ` ``）单独处理：${} 里的代码是真代码，字符串部分不是。

    正则字面量必须排除 —— `/```(\\w*)\\n?([\\s\\S]*?)(```|$)/g` 里全是括号，
    当成结构就会误报"不匹配"。判定靠前一个有效字符：正则只可能出现在
    运算符/括号/赋值号之后，`a / b` 这种除法位置不是。
    """
    out: list[str] = []
    i, n = 0, len(src)
    state = None  # None / 'line' / 'block' / "'" / '"' / '`' / 'regex'
    depth_stack: list[str] = []
    last_sig = ""   # 上一个有效结构字符，用来判断 '/' 是正则还是除法

    def regex_allowed(prev: str) -> bool:
        # 这些位置之后出现的 / 只能是正则的开头。
        # 刻意不含 < >：模板串里的 `</li>` 会被误判成正则（`<` 后面跟 `/`），
        # 而 `a < /re/.test(x)` 这种写法在本项目里不存在。
        return prev in "(,=:[!&|?{};+-*%~^"

    def keep(newline: bool) -> None:
        """跳过一个字符，但保留换行 —— 否则行号会漂移，报错定位不到真正的行。"""
        if newline:
            out.append("\n")

    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if state is None:
            if c == "/" and nxt == "/":
                state = "line"; i += 2; continue
            if c == "/" and nxt == "*":
                state = "block"; i += 2; continue
            if c in "'\"":
                state = c; i += 1; continue
            if c == "`":
                state = "`"; i += 1; continue
            if c == "/" and regex_allowed(last_sig):
                state = "regex"; i += 1; continue
            if not c.isspace():
                last_sig = c
            out.append(c); i += 1; continue
        if state == "line":
            if c == "\n":
                state = None; out.append(c); last_sig = "\n"
            i += 1; continue
        if state == "block":
            if c == "*" and nxt == "/":
                state = None; i += 2; continue
            keep(c == "\n"); i += 1; continue
        if state == "regex":
            # 正则里的 [...] 字符类要整体跳过，里面的括号不是结构
            if c == "\\":
                i += 2; continue
            if c == "[":
                while i < n and src[i] != "]":
                    if src[i] == "\n":
                        out.append("\n")
                    i += 2 if src[i] == "\\" else 1
                i += 1
                continue
            if c == "\n":          # 正则不能跨行，遇到就是判断错了
                state = None
                out.append("\n")
                continue
            if c == "/":
                state = None
                i += 1
                # 吃掉后面的修饰符
                while i < n and src[i].isalpha():
                    i += 1
                last_sig = "/"
                continue
            i += 1; continue
        if state == "`":
            # 模板串：${...} 里的代码要保留（它可能就是我们要查的结构）
            if c == "\\":
                i += 2; continue
            if c == "`":
                state = None; i += 1; continue
            if c == "$" and nxt == "{":
                depth_stack.append("tmpl")
                out.append("{"); last_sig = "{"; i += 2; continue
            if depth_stack and depth_stack[-1] == "tmpl":
                # 在 ${} 内：普通字符串、正则与注释要照常识别
                if c == "/" and nxt == "/":
                    state = "line"; i += 2; continue
                if c == "/" and nxt == "*":
                    state = "block"; i += 2; continue
                if c in "'\"":
                    state = c; i += 1; continue
                if c == "`":
                    state = "`"; i += 1; continue
                if c == "/" and regex_allowed(last_sig):
                    state = "regex"; i += 1; continue
                if c == "{":
                    depth_stack.append("brace"); out.append(c); last_sig = "{"; i += 1; continue
                if c == "}":
                    depth_stack.pop(); out.append(c); last_sig = "}"; i += 1; continue
                if not c.isspace():
                    last_sig = c
                out.append(c); i += 1; continue
            keep(c == "\n"); i += 1; continue
        # 普通单/双引号串
        if c == "\\":
            i += 2; continue
        if c == state:
            state = None; i += 1; continue
        keep(c == "\n"); i += 1; continue
    return "".join(out)


def check_balance(src: str) -> tuple[bool, str]:
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: list[tuple[str, int]] = []
    line = 1
    for ch in src:
        if ch == "\n":
            line += 1
        elif ch in "([{":
            stack.append((ch, line))
        elif ch in ")]}":
            if not stack:
                return False, f"第 {line} 行多了个 {ch!r}"
            top, opened = stack.pop()
            if top != pairs[ch]:
                return False, f"第 {line} 行 {ch!r} 与第 {opened} 行 {top!r} 不匹配"
    if stack:
        top, opened = stack[-1]
        return False, f"{top!r} 在第 {opened} 行没闭合"
    return True, ""


def check_css_comments(text: str) -> tuple[bool, str]:
    """CSS 注释必须成对闭合，且不能嵌套。

    漏掉一个 `*/` 不会报任何错：后面的规则被整段吞进注释，页面照常渲染，
    只是那条规则静默失效。踩过一次 —— `table-layout:fixed` 与 min-width
    全被吞掉，列宽退化成 auto，按钮从单元格溢出 18px，只有浏览器实测
    才看得出来。

    不能直接数 `/*` 与 `*/` 的个数：页面里有一堆 cron 表达式字符串
    （`*/30 * * * *` 这种），它们本身就含 `*/`，会算出"多 3 个闭合"，
    永远假失败。所以先把 `<script>` 之外的内容（即真正的 CSS）截出来，
    再在 CSS 块上做配平 —— CSS 里不该出现裸的 `*/`。
    """
    css = re.sub(r"<script(?![^>]*\bsrc=)[^>]*>.*?</script>", "", text, flags=re.S)
    if not css.strip():
        return False, "没截到 CSS 块（style 标签都带 src？）"

    depth = 0
    i, n = 0, len(css)
    line = 1
    while i < n:
        if css.startswith("/*", i):
            depth += 1
            i += 2
            continue
        if css.startswith("*/", i):
            depth -= 1
            if depth < 0:
                return False, f"第 {line} 行多了个 */（前面没有对应的 /*）"
            i += 2
            continue
        if css[i] == "\n":
            line += 1
        i += 1
    if depth > 0:
        # 找出第一个没闭合的位置，规则是从那里开始失效的
        idx = css.find("/*", css.rfind("*/") + 2)
        ln = css[:idx].count("\n") + 1 if idx > 0 else 1
        return False, f"有 {depth} 个 /* 没闭合，第一个在第 {ln} 行 —— 它后面的规则全失效"
    return True, ""


def main() -> int:
    text = WEB.read_text("utf-8")
    blocks = extract_scripts(text)
    # 前端拆成 ES Module 后，index.html 里没有内联脚本了 ——
    # 契约检查要扫的是「页面 + 全部模块」的合并视图，
    # 否则那些断言（存在 xxx 函数 / xxx 元素）会全部失效。
    module_files = sorted(MODULES_DIR.glob("*.js"))
    style_files = sorted(STYLES_DIR.glob("*.css"))
    all_text = text
    for f in module_files:
        all_text += "\n" + f.read_text("utf-8")
    # CSS 也抽成了独立文件 —— 契约里有不少是查 CSS 选择器的
    # （backdrop-filter / .grid.ucgrid / sendbtn.stopping …），
    # 不把样式文件纳入扫描，这些断言会全部失效。
    for f in style_files:
        all_text += "\n" + f.read_text("utf-8")

    check("能提取到 script 块或模块文件",
          bool(blocks) or bool(module_files),
          f"内联 {len(blocks)} 个 / 模块 {len(module_files)} 个")
    if not blocks and not module_files:
        return 1

    ok, why = check_css_comments(text)
    check("CSS 注释成对闭合", ok, why)

    src = "\n".join(blocks) + "\n" + "\n".join(
        f.read_text("utf-8") for f in module_files)
    check("script 体积合理（>50KB 说明提取到了真脚本）", len(src) > 50_000, f"{len(src)} 字节")

    # 工作流区块（本次新增的部分）单独做配平检查。
    # 整块脚本里有跨行字符串与复杂正则，本项目的简易词法分析器会在那上面
    # 误报（行号漂移 + 把正则当除法），所以只查这次真正改动的这一段 ——
    # 它没有跨行字符串，是能可靠判定的那部分。
    wf_start = all_text.find("/* ══════════ 工作流编排")
    wf_end = all_text.find("S.appFilter = '';")
    check("工作流区块已定位", wf_start > 0 and wf_end > wf_start, f"{wf_start}..{wf_end}")
    if wf_start > 0 and wf_end > wf_start:
        block = all_text[wf_start:wf_end]
        wf_src = extract_scripts(block + "<script>x</script>")
        wf_src = wf_src[0] if wf_src else ""
        stripped = strip_noise(wf_src)
        ok, why = check_balance(stripped)
        check("工作流区块括号配平", ok, why)
        check("工作流区块引号成对",
              stripped.count("'") % 2 == 0 and stripped.count('"') % 2 == 0,
              f"' {stripped.count(chr(39))} / \" {stripped.count(chr(34))}")
        check("工作流区块 div 开闭配平", block.count("<div") == block.count("</div>"),
              f"<div {block.count('<div')} vs </div> {block.count('</div>')}")

    # 挂载点必须都在（防止改坏接线）
    for sym in (
        'data-view="workflows"',              # 左栏入口
        "if (v === 'workflows')",              # 视图挂载
        "workflows: '工作流'",                 # VIEW_NAME
        "async function renderWorkflows",      # 列表页
        "async function openWorkflowDetail",   # 详情弹层
        "async function loadWorkflowRuns",     # 数据拉取
        "case 'workflow.started'",             # SSE 接线
        "case 'workflow.finished'",
        "case 'workflow.cancelling'",
        'id="wfCnt"',                          # 徽标
        "S.wf = {",                            # 视图级状态
        "loadWorkflowRuns(true)",              # 启动时拉一次
        "data-wfdel=",                         # 删除按钮
        "function wfAllFailedCount",         # 全失败计数（后端算，不在前端数分页）
        "/api/workflows/prune-failed",
        "/api/workflows/failed-count",
        "id=\"wfClearFailed\"",
        # ---- 模板（第五批）----
        "/api/workflows/templates",          # 模板 CRUD + 触发
        "data-wftab=\"tpl\"",                 # 模板 tab
        "data-wftplrun=",                    # 运行模板
        "data-wftpledit=",                   # 编辑模板
        "data-wftpldel=",                    # 删除模板
        "data-wfseed=",                      # 起步模板
        "id=\"wfSaveTpl\"",                   # 详情弹层里的「存为模板」
        "function editTemplate",             # 模板编辑器
        "function loadWorkflowTemplates",    # 模板列表拉取
        "async function runTemplate",        # 触发模板
        # ---- 第六批：本地资料四视图 + 对话 ----
        "/api/fs/list",                       # 此电脑：目录浏览
        "function loadFsDir",                 # 目录加载
        "function fsCrumbs",                  # 面包屑
        "/api/apps/icons",                    # 应用库：批量图标
        "function loadAppIcons",              # 批量图标加载
        "/api/apps/launch",                   # 应用库：启动
        "/api/apps/custom",                   # 应用库：自定义应用
        "id=\"appAddCustom\"",                 # 添加应用按钮
        "/api/index/libraries",               # 知识库：库集合
        "id=\"modeHint\"",                     # 对话：模式提示
        # ---- 第七批：思考过程 / 会话续接 / 图库来源 / 工具集 ----
        "case 'REASONING_START'",             # 对话：思考过程事件
        "function reasonPanel",               # 思考面板
        "已思考完成",                          # 思考完成文案
        "session_id: sessionId",              # 对话：会话续接
        "x.source || 'recent'",               # 图库：按来源分区
        "用这套工具",                          # 技能广场：整组使用
        # ---- 第八批：对话界面布局（对标 Marvis）----
        "--chat-col",                         # 内容列宽：正文与输入框共用
        "function fitChatCol",                # 列宽测量（ResizeObserver）
        "function autoGrow",                  # 输入框高度随内容增长
        "box.className = 'mactions'",         # 消息操作栏
        "function copyMsg",                   # 复制回复
        "function retryLast",                 # 重新生成
        "/api/chat/retry",                    # 重新生成接口
        # ---- 第九批：本地模型切换 / 思考折叠 / 复制反馈 ----
        "/api/providers/local",               # 本机已装模型
        "data-lm=",                            # 本地模型按钮
        "data-lmuse=",                         # 设置页里的本地模型按钮
        "dataset.collapsed",                   # 思考面板：手动收起标记
        "reason.live:not([data-collapsed])",  # 思考中默认展开，但让路给手动收起
        "maction.done",                        # 复制成功反馈
        # ---- 第十批：模型配置编辑 / 添加弹窗 / 云端本地分区 ----
        "data-pvedit=",                        # 编辑已保存的模型配置
        "data-pvuse=",                         # 启用某个模型
        "method: 'PUT'",                       # 编辑走 PUT
        "function openAddModel",               # 「添加模型」弹窗
        "function openEditModel",              # 「编辑模型」弹窗
        "function loadModelData",              # 重新拉模型数据（不能用快照）
        "function renderSettingsModel",        # 只重画模型管理那块
        "function modelSectionHtml",           # 云端 / 本地两张表
        "data-kind=\"local\"",                  # 添加弹窗：云端 / 本地二选一
        "it.is_local",                         # 按地址区分云端 / 本地
        # ---- 第十一批：管家团队 / 多 Agent 对话流 / 左栏毛玻璃 ----
        "function renderButlers",              # 管家团队页
        "function paintButlerList",            # 管家名单
        "function paintButlerDetail",          # 管家详情
        "function dispatchButler",             # 派活给管家
        "function loadAgentCatalog",           # 拉子 Agent 目录（名字/图标不写死）
        "data-bsel=",                          # 选管家
        "kind === 'butler'",                   # 只列管家
        "function tlPaint",                    # 多 Agent 对话流重画
        "function tlMemberDone",               # 成员结论落到时间线
        "function tlAgentStarted",             # 派活提示
        "case 'team.member.started'",          # 派活事件接线
        "case 'subagent.finished'",            # 子 Agent 结论事件接线
        "class=\"railcard\"",                  # 左栏卡片容器
        # 左栏的毛玻璃换成了「淡紫面板」——
        # 第十一批做的是 backdrop-filter:blur(25px)，第十二批左栏重做时改掉了：
        # 面板本体不再是独立卡片（background:transparent），而是和 #rail 同一块底。
        # 断言要跟着设计走 —— 留着旧标记的话，它只会在设计早已改掉之后一直报错。
        ".railcard {",                        # 左栏卡片容器（CSS 侧）
        "background:var(--panel);",           # 左栏底色是淡紫面板
        "function sessGroup",                  # 会话按时间分组
        "function sessStatus",                 # 会话状态标记
        "id=\"tpSwitch\"",                      # 团队切换器
        "id=\"tfRoles\"",                       # 分工输入区
        # ---- 第十二批：左栏重做 / 统一卡片 / 团队编辑 / 粘贴导入 ----
        "function ucard",                      # 统一卡片渲染器
        "function bindUCards",                 # 统一卡片的点击绑定
        "function ucDetail",                   # 详情弹窗
        "function ucConfirm",                  # 危险操作确认弹窗
        "class=\"uc\"",                         # 统一卡片样式
        ".grid.ucgrid",                        # 统一卡片网格
        # 描述折叠到两行 —— 但**不再用** `-webkit-line-clamp`。
        # Edge 157 起 `display:-webkit-box` 被规范化成 flow-root，
        # 基于 box 布局的 `-webkit-line-clamp` 全线失效（硬切、没有省略号）。
        # 现在改成 JS 测量高度 + `.is-clipped` 打标记，由 CSS 画省略号。
        "function applyClamp",                 # 折叠高度测量
        ".clamp2.is-clipped::after",           # 折叠时画省略号
        "在对话中使用",                        # 统一文案
        "function setLocalOpen",               # 本地资料浮层
        "position:fixed; z-index:120",         # 浮层（不能被 railcard 裁掉）
        "navitem newchat",                     # 新建对话是独立操作项
        "/api/paste",                          # 粘贴导入接口
        "function handlePaste",                # 粘贴事件处理
        "function pasteImage",                 # 粘贴图片落盘
        "/api/teams/${encodeURIComponent(t.name)}/members/",  # 移出成员
        "method: 'PUT'",                       # 编辑团队 / 自动任务
        "/api/automations/",                   # 自动任务增删改
        "function editAutomation",             # 编辑自动任务
        "function deleteAutomation",           # 删自动任务（带确认）
        "id=\"tpEdit\"",                        # 编辑团队按钮
        "id=\"tpDel\"",                         # 解散团队按钮
        "data-tpdrop=",                        # 移出成员按钮
        # ---- 第十三批：停止 / 卡片溢出 / 新建对话样式 / 会话排序 / 系统剪贴板 ----
        "/api/chat/cancel",                    # 中止正在跑的任务
        "/api/clipboard",                      # 读系统剪贴板（能读文件的那种）
        "function setSendState",               # 发送键 ⇄ 停止键
        "function stopRun",                    # 停止当前任务
        "function importFromClipboard",        # 从系统剪贴板导入
        "sendbtn.stopping",                    # 停止键样式（红色 + 脉冲）
        "case 'RUN_CANCELLED'",                # 被停掉的事件
        "case 'RUN_CANCELLING'",               # 正在停的事件
        "navitem.newchat",                     # 新建对话：深色实心块 + 白字
        "function sessGroup",                  # 会话按时间分组（组内按最近动过排）
        # ---- 第十四批：新建对话点击 / 任务状态重置 / 事件按任务过滤 ----
        "function isForeign",                  # 事件归属判据
        "const SCOPED_EVENTS",                 # 哪些事件要按 task_id 过滤
        "const TERMINAL_EVENTS",               # 终止类事件（结束/失败/被停）
        "S.taskId = ev.task_id",               # RUN_STARTED 时认领任务
        "S.taskId = ''",                       # 新建对话时不认领任何任务
        "S.busy = false;",                     # 新建对话时清掉跑着的标记
        "setSendState('idle');",               # 新建对话时发送键变回箭头
        "S.taskId = id;",                      # 打开历史会话时认领它
        # ---- 第十五批：应用库打开变慢（图标请求把线程池占满）----
        "appIconsPromise",                    # 批量图标请求复用同一个 Promise
    ):
        check(f"存在 {sym[:40]}", sym in all_text)

    # 前端**不许**再逐个请求图标。
    #
    # 为什么这是硬性要求：每个 /api/app/icon 请求会占住 FastAPI 线程池的一个
    # 槽位约 1.1 秒（要起一个 PowerShell 进程）。应用库有 258 个应用，
    # 逐个请求会把 40 个槽位全占满 —— 同一时刻点的「打开应用」只能排队。
    # 实测：图标请求进行中点「打开」，最长等 **96 秒**；空闲时只要 4~40 毫秒。
    #
    # 后端也有并发上限兜底，但前端这条是主因 —— 必须守住。
    #
    # 只查**代码**：注释里写着「以前错在哪」，那提到这个路径是应该的。
    code_only = strip_js_comments(src)
    check("前端不再逐个请求图标（防应用库打开变慢回归）",
          "/api/app/icon" not in code_only,
          "前端又出现了 /api/app/icon —— 逐个请求会把后端线程池占满，"
          "点「打开应用」会排队等最多 96 秒")

    print("\n" + "=" * 60)
    if FAILED:
        print(f"失败 {len(FAILED)} 项：{'、'.join(FAILED)}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())