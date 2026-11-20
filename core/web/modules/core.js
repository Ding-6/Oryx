/* core.js —— 分区 0。
   由 core/scripts/split_web_modules.py 从 index.html 拆出。
   改动前先看 docs/FRONTEND-MODULES.md 的依赖说明。
 */

/* core.js 是依赖根：不 import 任何业务模块，因此不会有循环依赖。 */

/* ── 可变容器 ──
   被重新赋值的 let/var 不能用 import 传递（import 是只读绑定），
   统一放进这个对象，用属性访问读写。初值都是字面量，安全。 */
/** @type {Record<string, any>} */
export const mut = {
  SUG: [],
  S_REASON: null,
  _msgSeq: 0,
  es: null,
  lastSeq: 0,
};


export const $ = s => document.querySelector(s);
export const $$ = s => Array.from(document.querySelectorAll(s));
/* 令牌不在 JS 里。
 *
 * 以前这里是 `export const ORYX_TOKEN = "__ORYX_TOKEN__"`，服务端下发时替换成真令牌。
 * 那个做法有致命漏洞：
 *   · `/web/modules/core.js` 不需要令牌就能读（同源资源）
 *   · CORS 放开任意 localhost:PORT 来源
 *   → 同机任意网页 fetch 一下就能拿到令牌，再带令牌调任意 API
 *   → 前端扩展 `import { ORYX_TOKEN } from '/web/modules/core.js'` 也拿得到
 *
 * 现在令牌走 **HttpOnly cookie**（服务端在 `/` 那条路由下发），
 * JS **读不到** —— `document.cookie` 里没有它。
 * 请求靠 cookie 自动携带（见下面的 api）。
 */
export async function api(path, o = {}) {
  // X-Oryx-Request 让写操作变成"非简单请求"，浏览器会先做 CORS 预检，
  // 非白名单来源在请求送达前就被服务端挡掉（403）。
  //
  // credentials:'same-origin' —— 让浏览器**自动带上 HttpOnly cookie**。
  // 这是页面自己能鉴权的唯一途径（令牌不在 JS 里）。
  const opt = {
    ...o,
    credentials: 'same-origin',
    headers: { 'X-Oryx-Request': '1', ...(o.headers || {}) },
  };
  if (opt.body && typeof opt.body === 'string') opt.headers['Content-Type'] = 'application/json';
  return fetch(path, opt);
}
export const esc = t => { const d = document.createElement('div'); d.textContent = t == null ? '' : String(t); return d.innerHTML; };

/** v2 统一响应 → 领域数据。
 *
 *  v2 的成功响应是 `{code, data, message}`，领域数据在 `data` 里。
 *  还没改造的旧接口返回裸对象 —— 这个 helper 两种都吃，
 *  迁移期不用关心哪个接口改过、哪个还没改。
 *
 *  数组原样返回（`data` 本身可能是数组，不能再往下剥一层）。
 */
/* ── 默认权限（记住的那一档） ──────────────────────────────────
   放在 core.js 是因为：models.js 和 policy.js 都要用它，
   而这两个模块**互相 import** —— 再加一层就成了循环依赖。
   core.js 不依赖任何模块，放这儿最安全。

   为什么单独记一份
   ----------------
   单轮的 S.policy 发完消息就清掉了；「默认权限」得**一直管着后面的每一轮**。
   这是个纯偏好，不必上后端，localStorage 足够。 */
export const DEFAULT_POLICY_KEY = 'oryx-default-policy';

/** 记住的默认权限档（空 = 跟随交互模式）。 */
export function getDefaultPolicy() {
  try {
    return localStorage.getItem(DEFAULT_POLICY_KEY) || '';
  } catch (_) {
    return '';
  }
}

/** 记住默认权限档。传空串 = 清掉记忆，回到「跟随交互模式」。 */
export function setDefaultPolicy(key) {
  const v = key || '';
  try {
    if (v) localStorage.setItem(DEFAULT_POLICY_KEY, v);
    else localStorage.removeItem(DEFAULT_POLICY_KEY);
  } catch (_) { /* 存不住也不影响本次 */ }
  return v;
}

/** 从错误响应里取出**人能看懂**的原因。
 *
 * 错误响应有好几种形状：
 *   · v2 规范   {code, data, message, detail}
 *   · 老接口     {content: "..."}
 *   · 纯文本     "xxx"
 *
 * 以前各处只读 `content` —— v2 接口出错时那个字段**压根不存在**，
 * 于是所有失败都显示成一句没信息量的「执行失败」，
 * 用户既不知道哪儿错了，也不知道该改什么。
 *
 * 这里把几种形状都认上。
 */
export function errText(body, fallback) {
  const fb = fallback || '未知错误';
  if (body === null || body === undefined) return fb;
  if (typeof body === 'string') return body.trim().slice(0, 200) || fb;
  if (typeof body !== 'object') return String(body).slice(0, 200) || fb;
  const msg = body.detail || body.message || body.content || body.error || body.reason || '';
  return String(msg).trim().slice(0, 200) || fb;
}

/** 错误响应 + 兜底文案，拼成一句能直接给用户看的话。 */
export function errOf(resp, fallback) {
  return resp.text().then(t => {
    let body = t;
    try { body = JSON.parse(t); } catch (_) { /* 纯文本 */ }
    return errText(body, fallback);
  }).catch(() => fallback || '未知错误');
}

export const dataOf = j => {
  if (!j || typeof j !== 'object' || Array.isArray(j)) return j;
  return ('data' in j) ? j.data : j;
};
/* 属性值专用：esc() 只转义 & < >，**不转义引号**（textContent 序列化如此）。
   所以 title="${esc(x)}" 遇到 x 里带双引号就会提前闭合属性、整个标签结构乱掉。
   塞进属性的值一律走这个。 */
export const escAttr = t => esc(t).replace(/"/g, '&quot;').replace(/'/g, '&#39;');

/** 标出「被截断了」的容器 —— 配合 CSS 的 `.clamp2.is-clipped::after` 显示省略号。
 *
 * 为什么不直接用 `-webkit-line-clamp`？
 * ----------------------------------
 * Edge 157 起，`display:-webkit-box` 被规范化成了 `flow-root`，
 * 基于 box 布局的 `-webkit-line-clamp` **全线失效** ——
 * 文字被硬切，末尾**没有省略号**，用户不知道后面还有内容。
 *
 * 这里自己判断有没有溢出，溢出才补省略号：
 * 短文本不会平白多一个「…」，那比硬切更让人烦。
 */
export function applyClamp(root) {
  const scope = root || document;
  const list = scope.querySelectorAll ? scope.querySelectorAll('.clamp2') : [];
  for (const el of list) {
    el.classList.toggle('is-clipped', el.scrollHeight > el.clientHeight + 1);
  }
}

/* ══════ 中文映射：后端标识保持英文（改了就调不动工具），展示层一律译成中文 ══════ */
const GROUP_CN = {
  browser: '浏览器操作', sys: '系统设置', team: '多 Agent 协作', automation: '自动任务',
  doc: '项目文档', fs: '文件读写', index: '内容索引', list: '清单查询',
  memory: '长期记忆', remote: '跨端远控', term: '终端命令', provider: '模型服务',
  shell: '命令执行', workspace: '工作区', xlsx: '表格处理', docx: 'Word 文档',
  notify: '通知提醒', ocr: '图片识字', pptx: '幻灯片', prompt: '提示词优化',
  search: '文件搜索', spawn: '派生 Agent', system: '系统信息', tool: '工具检索',
  use: '技能调用', mcp: 'MCP 扩展',
};
const TOOL_CN = {
  browser_click: '点击网页元素', browser_close: '关闭网页', browser_extract: '提取网页正文',
  browser_links: '列出网页链接', browser_open: '打开网页', browser_screenshot: '网页截图',
  browser_snapshot: '网页结构快照', browser_type: '填写网页表单',
  sys_display_settings: '显示设置', sys_input_methods: '输入法设置', sys_lock_screen: '锁定屏幕',
  sys_mouse_settings: '鼠标设置', sys_power_status: '电源状态', sys_set_dpi_scaling: '设置缩放比例',
  sys_set_screen_timeout: '设置屏保超时', sys_set_wallpaper: '设置壁纸',
  team_add_member: '添加团队成员', team_create: '创建团队', team_delete: '删除团队',
  team_dispatch: '派发任务', team_inbox: '团队收件箱', team_list: '团队名单',
  team_send_message: '团队发消息',
  automation_add: '添加自动任务', automation_list: '自动任务列表', automation_remove: '删除自动任务',
  automation_toggle: '启停自动任务',
  doc_change_log: '记录变更', doc_decision_add: '记录决策', doc_read: '读取项目文档',
  doc_requirement_add: '记录需求',
  fs_delete: '删除文件', fs_list: '列出目录', fs_read: '读取文件', fs_write: '写入文件',
  index_scan: '重建内容索引', index_search: '检索内容索引', index_status: '索引状态',
  list_agents: '列出子 Agent', list_apps: '列出已安装应用', list_providers: '列出模型服务',
  memory_forget: '删除记忆', memory_save: '写入记忆', memory_search: '检索记忆',
  remote_start: '启动远控', remote_status: '远控状态', remote_stop: '停止远控',
  term_interrupt: '中断终端命令', term_run: '执行终端命令', term_status: '终端状态',
  provider_add: '添加模型服务', provider_use: '切换模型服务',
  shell: '执行 Shell 命令', shell_sandbox: '沙箱执行',
  workspace_info: '工作区信息', workspace_switch: '切换工作区',
  xlsx_read: '读取表格', xlsx_write: '写入表格',
  docx_write: '写Word 文档',
  notify: '发送通知',
  ocr_image: '识别图片文字',
  pptx_write: '写幻灯片',
  prompt_optimize: '优化提示词',
  search_files: '搜索文件内容',
  spawn_agent: '派生子 Agent',
  system_info: '查看系统信息',
  tool_search: '检索工具',
  use_skill: '调用技能',
  task_create: '创建任务', task_update: '更新任务', task_list: '列出任务',
};
/* 技能目录名 → 中文显示名 */
const SKILL_CN = {
  'file-organizer': '文件整理', 'file-search': '文件检索', 'receipt-ocr': '票据识别',
  'system-info': '系统信息', 'web-research': '联网调研',
  'doc-writer': '文档写作', 'ppt-maker': '幻灯片制作', 'data-analyst': '数据分析',
  'code-reviewer': '代码评审', 'product-manager': '产品经理',
};
/* 子Agent id → 中文显示名。
   这里是兜底表 —— 真正的名字从后端目录来（AGENT.md 的 name 字段），
   见 S.agentNames。写死的表会跟实际定义脱节。 */
const AGENT_CN = {
  'doc-manager': '文档管理员', researcher: '调研员', reviewer: '审校',
};
/* 项目文档统计项 → 中文 */
export const DOC_STAT_CN = {
  requirements: '需求', decisions: '决策', changes: '变更', todos: '待办', notes: '笔记',
};
export function cnTool(t) {
  const n = String(t || '');
  if (TOOL_CN[n]) return TOOL_CN[n];
  const tail = n.split('__').pop();          // mcp__server__tool 形态
  return TOOL_CN[tail] || tail || n;
}
export function cnGroup(g) {
  const k = String(g || '');
  return GROUP_CN[k] || k;
}
export function cnSkill(s) {
  return SKILL_CN[String(s || '')] || String(s || '');
}
export function cnAgent(a) {
  const k = String(a || '');
  // 后端目录里的名字优先 —— 写死的映射表只是兜底
  if (S.agentNames && S.agentNames[k]) return S.agentNames[k];
  return AGENT_CN[k] || k;
}

/* markdown-lite：先转义再变换，永远不引入未转义 HTML */
export function md(src) {
  const blocks = [];
  let text = String(src == null ? '' : src).replace(/```(\w*)\n?([\s\S]*?)(```|$)/g, (_, lang, code) => {
    blocks.push(`<pre class="mdpre"><code>${esc(code.replace(/\n$/, ''))}</code></pre>`);
    return '\u0001' + (blocks.length - 1) + '\u0001';
  });
  text = esc(text);
  text = text.replace(/`([^`\n]+)`/g, '<code class="mdcode">$1</code>');
  text = text.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
  text = text.replace(/^#{1,3}\s+(.+)$/gm, '<span class="mdh">$1</span>');
  text = text.replace(/((?:^[ \t]*[-*]\s+.+(?:\n|$))+)/gm, m => {
    const items = m.trimEnd().split('\n').map(l => `<li>${l.replace(/^[ \t]*[-*]\s+/, '')}</li>`).join('');
    return `<ul class="mdul">${items}</ul>`;
  });
  text = text.replace(/((?:^[ \t]*\d+[.)]\s+.+(?:\n|$))+)/gm, m => {
    const items = m.trimEnd().split('\n').map(l => `<li>${l.replace(/^[ \t]*\d+[.)]\s+/, '')}</li>`).join('');
    return `<ol class="mdol">${items}</ol>`;
  });
  text = text.replace(/\u0001(\d+)\u0001/g, (_, i) => blocks[+i]);
  return text;
}

export const S = {
  view: 'chat', sessions: [], current: null, steps: new Map(), todos: [],
  // 对话里那条任务栏是不是展开着（默认收起，只占一行）
  todoPanelOpen: false,
  // 当前会话挂着的团队（名字）。每个对话可以有自己的团队。
  sessionTeam: '',
  artifacts: [], busy: false, term: null, assistant: null, scene: 'office',
  scenePreset: 'chat',   // 输入框顶部的场景预设
  interaction: 'craft', style: 'professional', expert: '', mode: 'efficiency', provider: '',
  // 当前界面认哪个任务的事件。
  // 空串 = 不认任何任务的事件（刚点「新建对话」时的状态）。
  // 用来挡住旧任务的流式内容跑进新会话 —— 那个任务后台还在跑，
  // 它的回答凭空冒到新对话里，用户会以为见鬼了。
  taskId: '',
  // 子 Agent / 管家 的 id → 中文名与图标。从后端目录拉，不写死。
  agentNames: {}, agentIcons: {}, agentSubs: {},
  // 内核起没起来（后端 /api/state 的 kernel_ready）。
  // 没就绪时，那些走 RPC 的接口（团队、工作流…）会 503 ——
  // 调它们只会刷出一排控制台错误。该等内核起来再补。
  kernelReady: true,
  teamPoll: null,
  // 检查点：这个会话可回退的位置。key 是 checkpoint id。
  checkpoints: [], cpLoadedFor: '',
  // 模型管理的数据。每次改动后重新拉 —— 不能用快照，
  // 否则删掉的 provider 还显示着、切换后也看不到变化。
  modelProviders: [], modelActive: '', localModels: [],
  // 这一轮发起的序号。点「发送」时 +1，点「停止」时带上它 ——
  // 后端靠它判断「这次中止是冲哪一轮来的」。
  //
  // 为什么需要：「发送」和「停止」是两条独立的 HTTP 请求，可以并发。
  // 用户点发送后立刻点停止时，中止请求完全可能先到内核 ——
  // 那时内核还没登记任务，那一下就被丢掉了（按了停止，任务照跑到底）。
  // 带上序号，后端就知道该把这笔中止算到哪一轮头上。
  sendSeq: 0,
  // 正在发起的这一轮的序号。0 = 没有在途的发起。
  // 中止请求到达时它非 0，就说明「有个发起还没落地」——
  // 后端要等它落地再把中止转给内核（见 server.py 的 _dispatch_* ）。
  inflightSeq: 0,
  // 忙碌态的计时。busySince = 忙碌开始的时刻（用于失败时判断该不该清），
  // busyTimer = 超时兜底的定时器句柄。
  //
  // 为什么要有兜底：SSE 断线且漏掉 RUN_FINISHED 时，busy 会永远停在 true，
  // 发送键永久显示成「停止」，用户再也发不了消息 —— 界面彻底卡死。
  busySince: 0,
  busyTimer: null,
  // 用量快照。RUN_FINISHED 后刷新，面板数字才不会停留在旧值。
  usage: null,
  // MCP 服务器清单。启停后要重画设置页的「扩展工具」那一节。
  mcp: { servers: [], errors: [], warnings: [] },
};

export function toast(t) {
  const n = $('#notice'); n.textContent = t; n.classList.add('on');
  clearTimeout(toast._t); toast._t = setTimeout(() => n.classList.remove('on'), 2400);
}

/* ══════ 事件总线 ══════
 *
 * 为什么放在 core.js
 * ----------------
 * core.js 是**依赖根**（不 import 任何业务模块）。前端扩展是动态
 * import 的独立模块图，但它们 import 的 `/web/modules/core.js` 是
 * **同一个 URL** —— 浏览器模块缓存保证是**同一个模块实例**。
 *
 * 所以总线放这里，核心与扩展拿到的是**同一个对象**。
 * 放别处（比如 uiext.js）就不行了：扩展 import core.js 会拿到 core 的
 * 那份，uiext.js 自己那份是另一个 —— 两套监听器，谁也收不到谁的事件。
 *
 * 事件方向
 * --------
 * 核心 → 扩展：
 *   state:changed     状态刷新后（带状态对象）
 *   view:changed      视图切换（带 view 名）
 *   session:changed   会话切换（带 session id）
 *   task:done         任务跑完
 *   theme:changed     主题切换（带主题名）
 *   uiext:ready       扩展加载完成（带已挂载的扩展 id 列表）
 *
 * 扩展 → 核心（核心监听这几个）：
 *   ui:view           请求切视图 {view}
 *   ui:session        请求切会话 {id}
 *   ui:toast          请求弹提示 {text}
 *
 * 监听器抛异常**不影响别人** —— 一个扩展的错不能拖垮核心。
 */

const busListeners = new Map();

export const bus = {
  /** 订阅事件。返回取消订阅的函数。 */
  on(name, fn) {
    if (!busListeners.has(name)) busListeners.set(name, new Set());
    busListeners.get(name).add(fn);
    return () => busListeners.get(name)?.delete(fn);
  },

  /** 只订阅一次。 */
  once(name, fn) {
    const off = bus.on(name, (d) => { off(); fn(d); });
    return off;
  },

  /** 发事件。监听器抛异常会被吞掉 —— 一个扩展的错不能拖垮核心。 */
  emit(name, detail) {
    const set = busListeners.get(name);
    if (!set || !set.size) return;
    for (const fn of [...set]) {
      try { fn(detail); }
      catch (e) { console.warn('[bus] 监听器出错', name, e); }
    }
  },

  /** 当前注册了多少个监听器（调试用）。 */
  count(name) { return busListeners.get(name)?.size || 0; },
};

