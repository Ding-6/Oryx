# Oryx 新前端（React 19 + TS + Tailwind v4 + shadcn/ui）

> 这是**新**前端，与现有的 `core/web/`（原生 ES Module）**并存**。
> 老前端没有删 —— 它现在仍然是能跑的、已上线的版本。
> 新前端逐页替换，替换完再撤老的。

## 为什么新起一个，而不是在老的上面改

体检（阶段一）的结论：

| 现状 | 数据 |
|---|---|
| `models.js` 单文件 | **5806 行**，占前端 JS 的 68%，148 个函数，16 个分区被合并 |
| 状态管理 | 一个全局可变对象 `mut` + 事件总线，没有 store |
| 组件 | 卡片/按钮/弹窗全是手写，hover/focus/loading/disabled 各处不一致 |
| 响应式 | 只适配桌面壳（min-width 880） |

5806 行的单文件里做「重构 UI」，等于在雷区里改线 —— 改一处不知道影响谁。
新起一个、按域逐个迁移，是唯一可控的做法。

## 关键决定

### 1. 不重新发明配色 —— 直接复用现有 token

`src/index.css` **import 现有的** `core/web/styles/tokens.css`，
再用 Tailwind v4 的 `@theme inline` 把 CSS 变量映射成工具类：

```
--accent → bg-accent / text-accent / border-accent
```

现有那套 token 是几轮迭代调出来的（`accent` 从 `#6366F1` 调到 `#565AE0`，
就是为了白字在紫底上过 4.5:1 的 WCAG AA）。**直接继承，不要重画。**

好处：新旧前端同一个视觉语言，迁移时不会出现「换了个皮」；
改一个 token 两边同时变，不会漂移。

### 2. API 客户端收口 —— 修「104 个调用不检查状态码」

老前端 195 个 `api()` 调用里**只有 91 个**检查了 `.ok`，
剩下 104 个把失败当成功处理 —— 用户看到的是「点了没反应」。

新前端只有一个联网入口 `src/lib/api.ts`：

- 状态码**必检**，非 2xx 一律抛 `ApiError`
- 错误文案统一提取（`message` → `detail` → 状态码兜底），兼容新旧两种错误体
- 写操作自动带 `X-Oryx-Request: 1`（后端的跨源闸门要求这个头）

于是调用方只要 `try/catch`，不用再到处捞 `x.detail`。

### 3. 组件用 shadcn/ui 的**源码级**形态

不是 npm 包，是**放在我们仓库里的代码**（`src/components/ui/`）。
这样配色完全跟着 Oryx 的 token 走 —— 用 npm 版组件库的话，
配色就成了它的品牌色，改不动。

## 现在到哪一步了

- ✅ 骨架：顶栏 + 左栏 + 主区 + 右栏，与老前端的信息架构对齐
- ✅ `api.ts` 统一客户端
- ✅ `button.tsx` 组件（default/outline/ghost/danger × sm/md/lg/icon）
- ✅ Tailwind v4 接上现有 token（明暗两套）
- ⬜ 按域迁移 22 个后端域的页面
- ⬜ 大屏（多 Agent 一屏总览）接口化后做页面

## 命令

```bash
cd apps/web
npm install
npm run dev          # 开发（5173，/api 代理到 8765 的内核）
npm run build        # 构建（产物 dist/）
npm run typecheck    # 类型检查
```

## 与内核的集成

开发时 `npm run dev` 起在 5173，`/api` 代理到 `127.0.0.1:8765` 的内核。

生产时产物由 FastAPI 直接托管（`core/oryx/server.py` 的 `/web` 路由），
不经过 Vite —— 跟现在老前端的部署方式一致。

**令牌**：走 HttpOnly cookie，由内核的 `GET /` 下发。
JS 读不到，这是有意设计（老前端也这样，见 `core/web/modules/core.js` 的注释）。
