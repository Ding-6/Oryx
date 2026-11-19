# 前端模块结构

> 第三轮（2026-11-05）：前端从「9705 行单文件」拆成 ES Module。
> 本文是改完之后的结构说明与依赖规则 —— **改前端前先看这里**。

## 一句话背景

以前 `core/web/index.html` 是一个 **9705 行的单文件**：HTML 壳 + 2746 行 CSS +
6791 行 JS 全塞在一起，254 个顶层符号共享一个全局作用域。

「改一处怕崩一片」的根因不是代码写得差，是**所有东西都在同一个作用域里**，
依赖关系只能靠人脑记。现在拆成模块，依赖变成显式的 import/export。

## 目录结构

```
core/web/
├── index.html          169 行 —— 只留壳：HTML 结构 + 引用标签
├── styles/             8 个样式文件（按区域拆开，见下）
│   ├── tokens.css      设计 token：颜色、字号、圆角、阴影（明暗两套）
│   ├── rail.css        左栏：logo、搜索、导航、会话列表、底部账号区
│   ├── center.css      主区：顶栏、消息流、输入框、附件、模式提示
│   ├── side.css        右栏：七个页签、执行步骤、任务、团队、总览
│   ├── panels.css      面板：团队名册、工作流阶段、统一卡片、产物
│   ├── motion.css      动效、窄屏自适应、背景图
│   ├── overlays.css    管家团队、弹层、多 Agent 团队、本地资料
│   └── composer.css    知识库与图库、输入框芯片行、发送键
├── modules/            23 个 ES Module（原来内联的 <script>）
│   ├── core.js         底座：全局状态 S、HTTP 出口、esc/md、可变容器 mut
│   ├── boot.js         入口：启动流程 + 显式加载副作用模块
│   └── ...             其余 21 个功能模块
├── _source/
│   ├── app.js          拆分基准快照（6790 行）—— 重新拆分时用它
│   └── main.css        CSS 基准快照（拆分前的样子）
└── (诊断临时文件已清理)
```

`index.html` 的关键内容：

```html
<link rel="stylesheet" href="/web/styles/tokens.css">
<link rel="stylesheet" href="/web/styles/rail.css">
…（8 个，顺序即层叠顺序）
<script type="module" src="/web/modules/boot.js"></script>
```

### 视觉设计：淡紫调

配色从**中性灰**换成**淡紫调**，页面底是一层柔光渐变：

```css
background:
  radial-gradient(1100px 560px at 78% 4%, var(--glow) 0%, transparent 62%),
  linear-gradient(132deg, var(--grad-1) 0%, var(--grad-2) 42%, var(--grad-3) 100%);
```

所有品牌色都走 token（`--brand-1/2`、`--on-brand`、`--tint-a~d`）——
**换主题时一处改**。`ui_audit` 的 TOKEN-001 规则会盯这件事：
颜色一旦硬编码成十六进制，审计就报。

> 有两个「前景色」变量，别混：
> `--on-accent` 配 `--accent`（明暗两套明暗相反），
> `--on-brand` 配品牌紫渐变（两套都是够深配白字）。
> 共用一个变量的话，两边必有一边不达标。

### 几个入口都是「真动作」

界面上的卡片/按钮都接到实际功能，不是摆设：

| 入口 | 实际行为 |
|---|---|
| 欢迎页「知识库问答」 | 打开本地资料的知识库 |
| 欢迎页「工作流任务」 | 跳到工作流页 |
| 欢迎页「代码助手」 | 切到执行模式 + 填好起步提示词 |
| 输入框场景预设 | 切交互模式 + **按关键词从用户技能目录里挑技能挂上** |
| 右栏快捷功能 | 分别打开知识库 / 工作流 / 技能页 / 代码模式 |
| 顶栏「⚙」 | 人设、回答风格、计算模式、交互模式（控件 id 全保留） |
| 输入框「更多设置」 | 优化表达 / 调用技能 / 默认权限 |

### 样式为什么按区域拆

原来是**一个 `main.css`，2749 行 / 116KB** —— 改一处要找半天，
而且「左栏」「右栏」「输入框」这些明显不同的区域混在一个文件里。

拆的时候**只按段落标记切、顺序一点不变**：
CSS 的层叠靠加载顺序，顺序变了视觉就会悄悄变样，
而这种回归极难查。`core/scripts/split_css.py` 负责这件事，
`core/tests/_shot_cmp.py` 做**逐像素对比**确认没变。

**两条配套**（拆分时才发现的坑）：

* `oryx/uicss.py` 提取核心样式子集时，改成**按 index.html 的 `<link>` 顺序**
  读所有样式文件 —— 按文件名排序可能和浏览器实际加载的顺序不一样
* `oryx/ui_audit.py` 的 `requires_absent` 上下文检查，改成查**所有样式文件的合并文本** ——
  「有没有别处的焦点样式」本来就可能写在别的文件里。
  另外审计现在**匹配前剥掉注释**（用等长空格替换，行号不变）——
  否则注释里提一句「别硬编码 #fff」都会被当成硬编码颜色报出来

## 怎么重新拆分

拆分是**幂等**的：基准快照在 `web/_source/app.js`，不依赖 index.html 里还有没有内联脚本。

```bash
cd core
python scripts/split_web_modules.py --analyze   # 只看依赖分析
python scripts/split_web_modules.py             # 重新生成全部模块
```

改模块划分要改脚本里的 `ZONE_TO_MODULE` 表 —— 分区边界用的是源码里本来就有的
`/* ══════ xxx ══════ */` 标记，不是新造的。

## 三条硬规则

### 1. 模块之间不 import 业务模块

只有 `core.js` 是例外（它是依赖根）。跨模块调用一律走 `core.js` 导出的符号：

```js
import { api, $, S } from './core.js';   // ✅ 可以
import { renderFiles } from './sidebar.js';  // ❌ 不行
```

**为什么**：依赖分析发现分区之间有 **21 个循环依赖环**。根因是 UI 事件处理天然双向
（点按钮 → 渲染 → 列表项再响应点击）。ES Module 一旦成环就触发 TDZ（暂时性死区），
页面直接白屏；而要消环就得把事件回调改成发布订阅 —— 那是**改产品行为**，
不是整理代码。

打破环的办法是**合并模块**（`split_web_modules.py` 自动做）：合并后两边的符号在
同一个文件里，不需要 import，也就不可能成环。代价是那个文件变大。

> 踩过的坑：**把符号「下沉到 core」会造出新的环**。
> 例：`tlAgentStarted` 原本在 `agentflow`、被 `agui` 用；下沉到 core 后变成
> `core → agui`，而 `agui` 又用 core 的 `$` / `S`，于是 `core ↔ agui` 成环。
> 「往共同祖先搬符号」只对「没有反向边」成立，这里不成立。

### 2. 会被重新赋值的变量放进 `mut`

`import` 绑定是**只读**的，不能重新赋值。运行期会被改值的变量放进 `core.js` 的
可变容器：

```js
export const mut = { SUG: [], S_REASON: null, _msgSeq: 0, es: null, lastSeq: 0 };
```

用法：`mut.lastSeq = ev.seq;`

当前成员：`SUG` / `S_REASON` / `_msgSeq` / `es` / `lastSeq`。
初值必须是字面量 —— 不能放依赖模块上下文的表达式。

### 3. 副作用模块由入口显式加载

有些模块不导出任何符号 —— 它们在顶层直接 `addEventListener`（接线模块）。
这种模块没人 import 是**正常的**，但必须被执行一次，否则「按钮点了没反应」。

`boot.js` 会显式 `import './xxx.js';` 把它们都加载上。当前 5 个：
`promptopt` / `search` / `terminal` / `topbar-ui` / `wiring`。

## 依赖图

当前 22 个模块的依赖是 **DAG**（无环）：

- `core.js` ← 所有模块都依赖它（依赖根）
- `boot.js` → 入口，依赖各功能模块
- 其余模块之间：单向，无环

改完模块后跑这个确认：

```bash
cd core
python tests/check_web_modules.py
```

它会检查：有向无环、无向无环、import/export 一一对应、没有孤立模块，
并用 Chrome 真实加载页面确认无运行期错误。

## 服务端配套

ES Module 需要浏览器按 `import` 去取多个文件，所以 `server.py` 加了静态资源路由：

```
GET /web/{path}   → 从 core/web/ 取文件
```

安全边界：
- 路径穿越（`../`、编码变体）一律 403 —— resolve 后必须仍在 WEB_DIR 之下；
- 只放行 `WEB_MIME` 里登记的扩展名；
- 超过 8MB 拒绝；
- 不要求令牌 —— `<script type="module" src>` 带不了自定义头，要求令牌前端直接加载失败。
  静态资源里没有用户数据，与首页（本来就公开）是同一套信任模型。

**令牌注入**：拆分前令牌占位符在 index.html 里，服务端替换字符串即可。
拆分后在 `core.js` 里，所以 `/web/*.js` 也会做占位符替换。

## 验证脚本

| 脚本 | 作用 | 怎么跑 |
|---|---|---|
| `tests/check_web_modules.py` | import/export 一致性、无环、无孤立模块、**Chrome 真实加载无报错** | `python tests/check_web_modules.py` |
| `tests/check_web_functional.py` | **功能性回归**：切视图、搜索、设置按钮真的能用 | `python tests/check_web_functional.py` |
| `tests/check_web_script.py` | 前端契约断言（130+ 条） | `python tests/check_web_script.py` |
| `tests/check_web_syntax.py` | **逐模块**用 Chrome 编译验语法 | `python tests/check_web_syntax.py` |
| `tests/check_web_console.py` | **逐页走一遍**，收集未捕获异常与 `console.error` | `python tests/check_web_console.py` |

`check_web_console.py` 会被 `tests/run_web_checks.py` **自动纳入**
（它 glob `check_web_*.py`）—— 以后新增页面，JS 报错会被自动抓住，
不用记得补调用。

### 体检脚本（`scripts/`）

这几个不在回归套件里，是**改界面时用来查问题**的：

| 脚本 | 查什么 | 怎么跑 |
|---|---|---|
| `scripts/check_contrast.py` | 每个视图页的文字对比度（WCAG AA 4.5:1） | `python scripts/check_contrast.py [light\|dark]` |
| `scripts/check_clamp.py` | 两行截断 + 省略号是否正常 | `python scripts/check_clamp.py` |
| `scripts/shot_views.py` | 一次截完九个视图页（可加 `dark` 参数） | `python scripts/shot_views.py <输出目录> [宽] [高] [dark]` |
| `scripts/screenshot.py` | 截当前界面的单张图 | `python scripts/screenshot.py <输出路径>` |
| `scripts/shot_cmp.py` | 两张图**逐像素**对比 | `python scripts/shot_cmp.py <前> <后>` |

**对比度那个值得单说** —— 「颜色好像有点淡」是视觉问题里最难说清的一种：
肉眼看得出不对，但说不清差多少、差在哪。
脚本把每个可见文字节点的前景色和**实际合成后的背景色**算一遍对比度，
直接告诉你哪个选择器、哪个字号、差多少、样例文字是什么。
一个 `--muted` 就揪出了 77 处不达标。

### 一个浏览器变更：两行截断**不再靠 `-webkit-line-clamp`**

Edge 157 起，`display:-webkit-box` 被规范化成了 `flow-root`，
基于 box 布局的 `-webkit-line-clamp` **全线失效** ——
文字被硬切，末尾没有省略号。

现在用 `.clamp2` + `applyClamp()`：
CSS 固定高度保证不撑破布局，JS 判断有没有溢出，溢出才补省略号。
不依赖任何已被移除的浏览器特性。


后两个脚本读前端源码时要用 `tests/webread.py` 的 `page_source()` ——
它是「index.html + 全部模块 + 全部样式」的合并视图。
直接 `read_text("index.html")` 只能看到壳，所有断言都会失效。

### 为什么不用 --dump-dom

Oryx 界面开着 **EventSource 长连接**（SSE 收 Agent 事件）。
`chrome --headless --dump-dom` 会等页面「网络静止」，长连接让它**永远等不到** ——
表现为命令挂起直到超时，而不是拿到错误信息。`--screenshot` 同样会挂。

所以真实加载检查走 **CDP（Chrome DevTools Protocol）**：
订阅 `Runtime.exceptionThrown` / `Runtime.consoleAPICalled`，几秒后主动断开。
项目后端本来就依赖 websockets，不引入新依赖。

## 桌面端（Tauri）

`apps/desktop/dist/index.html` 只是**启动闪屏**（探测内核起来没，然后跳转到
`http://127.0.0.1:8765`）。真正的界面始终由 Python 服务提供，
所以 Tauri 侧不需要同步模块文件。

`tauri.conf.json` 的 `resources` 里已有 `"../../../core/web": "core/web"` ——
整个 web 目录（含 modules/styles）会自动打进安装包。

## 边界与已知取舍

- **模块合并的代价**：为了破环，有 15 个分区被合并进相邻模块，
  最大的两个文件约 1400 行。比原来的 6791 行单文件好，但不算理想。
  下一步若要更细，得先把 UI 事件改成发布订阅 —— 那是产品行为改动，
  要单独立项。
- **没有 TypeScript**：类型保护靠 JSDoc（`core.js` 里已有 `@type` 标注），
  零构建成本，VS Code 有补全。
- **CSS 没拆**：2746 行仍在一个文件里。CSS 没有作用域冲突风险，
  拆分收益远小于 JS，暂不动。
- **Google Fonts 仍是外链 CDN**：`index.html` 里的 `<link href="https://fonts.googleapis.com/...">`
  没改。断网时界面会退回系统字体（`Inter Tight` / `JetBrains Mono` 有 fallback），
  不影响功能，但严格说离线时不是「完全自包含」。
