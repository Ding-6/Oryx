
const $ = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));
const ORYX_TOKEN = "__ORYX_TOKEN__";
async function api(path, o = {}) {
  // X-Oryx-Request 让写操作变成"非简单请求"，浏览器会先做 CORS 预检，
  // 非白名单来源在请求送达前就被服务端挡掉（403）。
  const opt = { ...o, headers: { 'X-Oryx-Token': ORYX_TOKEN, 'X-Oryx-Request': '1', ...(o.headers || {}) } };
  if (opt.body && typeof opt.body === 'string') opt.headers['Content-Type'] = 'application/json';
  return fetch(path, opt);
}
const esc = t => { const d = document.createElement('div'); d.textContent = t == null ? '' : String(t); return d.innerHTML; };
/* 属性值专用：esc() 只转义 & < >，**不转义引号**（textContent 序列化如此）。
   所以 title="${esc(x)}" 遇到 x 里带双引号就会提前闭合属性、整个标签结构乱掉。
   塞进属性的值一律走这个。 */
const escAttr = t => esc(t).replace(/"/g, '&quot;').replace(/'/g, '&#39;');

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
const DOC_STAT_CN = {
  requirements: '需求', decisions: '决策', changes: '变更', todos: '待办', notes: '笔记',
};
function cnTool(t) {
  const n = String(t || '');
  if (TOOL_CN[n]) return TOOL_CN[n];
  const tail = n.split('__').pop();          // mcp__server__tool 形态
  return TOOL_CN[tail] || tail || n;
}
function cnGroup(g) {
  const k = String(g || '');
  return GROUP_CN[k] || k;
}
function cnSkill(s) {
  return SKILL_CN[String(s || '')] || String(s || '');
}
function cnAgent(a) {
  const k = String(a || '');
  // 后端目录里的名字优先 —— 写死的映射表只是兜底
  if (S.agentNames && S.agentNames[k]) return S.agentNames[k];
  return AGENT_CN[k] || k;
}

/* markdown-lite：先转义再变换，永远不引入未转义 HTML */
function md(src) {
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

const S = {
  view: 'chat', sessions: [], current: null, steps: new Map(), todos: [],
  artifacts: [], busy: false, term: null, assistant: null, scene: 'office',
  interaction: 'craft', style: 'professional', expert: '', mode: 'efficiency', provider: '',
  // 当前界面认哪个任务的事件。
  // 空串 = 不认任何任务的事件（刚点「新建对话」时的状态）。
  // 用来挡住旧任务的流式内容跑进新会话 —— 那个任务后台还在跑，
  // 它的回答凭空冒到新对话里，用户会以为见鬼了。
  taskId: '',
  // 子 Agent / 管家 的 id → 中文名与图标。从后端目录拉，不写死。
  agentNames: {}, agentIcons: {}, agentSubs: {},
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

function toast(t) {
  const n = $('#notice'); n.textContent = t; n.classList.add('on');
  clearTimeout(toast._t); toast._t = setTimeout(() => n.classList.remove('on'), 2400);
}

/* ══════ 视图 ══════ */
function setView(v) {
  S.view = v;
  // 顶栏标题跟着页面走。
  //
  // 以前只有对话页会更新标题，切到工作台/技能广场等页面时
  // 顶栏还留着上一个会话的名字 —— 用户分不清自己在哪。
  // VIEW_NAME 这张表以前定义了却没人用，就是为这个。
  if (v !== 'chat') {
    const el = $('#pageTitle');
    if (el) el.textContent = VIEW_NAME[v] || '';
  }
  $$('.navitem[data-view]').forEach(b => {
    // 「新建对话」是个动作，不是当前位置 —— 不参与高亮切换，
    // 否则它跟当前页面的选中项长得一模一样，用户分不清自己在哪。
    if (b.classList.contains('newchat')) { b.classList.remove('on'); return; }
    const on = b.dataset.view === v;
    b.classList.toggle('on', on);
    b.setAttribute('aria-selected', String(on));
  });
  // 子项跟着高亮：进入知识库时「知识库」也点亮
  $$('#localSub .subitem').forEach(b => b.classList.toggle('on', v === 'local' && b.dataset.lv === S.localView));
  const chat = v === 'chat';
  $('#center').style.display = chat ? 'flex' : 'none';
  $('#side').style.display = chat ? 'flex' : 'none';
  $('#page').classList.toggle('on', !chat);
  if (v === 'work') renderWork();
  if (v === 'market') renderMarket();
  if (v === 'persona') renderPersona();
  if (v === 'automations') renderAutomations();
  // 工作流页有活动 run 时列表会随事件变；离开期间攒下的脏标记在这里补一次
  if (v === 'workflows') {
    // 模板列表也要拉一次 —— 模板可能在别处被创建/删除过
    loadWorkflowTemplates(true);
    if (S.wf.dirty) { S.wf.dirty = false; loadWorkflowRuns().then(renderWorkflows); }
    else renderWorkflows();
  }
  if (v === 'local') renderLocal();
  if (v === 'settings') renderSettings();
  if (v === 'dashboard') renderDashboard();
  if (v === 'butlers') renderButlers();
}
$$('.navitem').forEach(b => {
  b.addEventListener('click', () => {
    // 「本地资料」是纯分组入口：只展开/收纳四个子项，不跳内容页
    if (b.dataset.view === undefined) {
      const open = b.getAttribute('aria-expanded') === 'true';
      setLocalOpen(!open);
      return;
    }
    if (b.dataset.view === 'chat') { newChat(); return; }
    gotoView(b.dataset.view);
  });
  // div 不会自己响应 Enter/Space，补上让键盘可用
  b.addEventListener('keydown', e => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); b.click(); }
  });
});

/* 本地资料分组：弹浮层。
   浮层挂到 body 上（fixed 定位）—— .railcard 有 overflow:hidden，
   放在导航里会被裁掉。而且这样导航高度不变，
   点它不会把下面的「对话」列表往下顶。 */
function setLocalOpen(open) {
  const head = $('.navitem[aria-controls="localSub"]');
  const sub = $('#localSub');
  if (!sub) return;
  if (open) {
    if (head) {
      head.setAttribute('aria-expanded', 'true');
      // 贴着头部右边缘弹出
      const r = head.getBoundingClientRect();
      sub.style.left = Math.round(r.right + 6) + 'px';
      sub.style.top = Math.round(r.top - 4) + 'px';
    }
    sub.hidden = false;
    if (!sub.parentElement || sub.parentElement !== document.body) document.body.appendChild(sub);
  } else {
    if (head) head.setAttribute('aria-expanded', 'false');
    sub.hidden = true;
    // 收回原位（放回 .navgroup 里，下次展开才不会跑飞）
    const group = $('.navgroup');
    if (group && sub.parentElement !== group) group.appendChild(sub);
  }
  localStorage.setItem('local-open', open ? '1' : '0');
}

/* 点别处收起浮层 */
document.addEventListener('click', e => {
  const sub = $('#localSub');
  if (!sub || sub.hidden) return;
  if (sub.contains(e.target)) return;
  if (e.target.closest('.navitem[aria-controls="localSub"]')) return;
  setLocalOpen(false);
});
/* 窗口尺寸变了，浮层位置会错 —— 重算一次 */
window.addEventListener('resize', () => {
  const sub = $('#localSub');
  if (sub && !sub.hidden) setLocalOpen(true);
});

/* 本地资料子项：切视图（会顺带展开分组） */
S.localView = 'kb';
$$('#localSub .subitem').forEach(b => {
  const go = () => {
    S.localView = b.dataset.lv;
    setLocalOpen(false);
    gotoView('local');
  };
  b.addEventListener('click', go);
  b.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
});
$('#setBtn').addEventListener('click', () => setView('settings'));

/* ══════ 侧栏拖拽 / 折叠 ══════ */
const RAIL_MIN = 200, RAIL_MAX = 460;
const SIDE_MIN = 300, SIDE_MAX = 620;
function initResize(handleId, sideId, cls, varName, min, max, invert) {
  const h = $(handleId);
  if (!h) return;
  h.addEventListener('mousedown', e => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = $(sideId).getBoundingClientRect().width;
    document.body.classList.add('rz-drag');
    h.classList.add('dragging');
    const move = ev => {
      const delta = (ev.clientX - startX) * (invert ? -1 : 1);
      const w = Math.max(min, Math.min(max, startW + delta));
      document.documentElement.style.setProperty(varName, w + 'px');
      h.setAttribute('aria-valuenow', Math.round(w));
      fitTopbar();
    };
    const up = () => {
      document.removeEventListener('mousemove', move);
      document.removeEventListener('mouseup', up);
      document.body.classList.remove('rz-drag');
      h.classList.remove('dragging');
      localStorage.setItem(varName, document.documentElement.style.getPropertyValue(varName));
    };
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', up);
  });
  // 键盘等价操作：← → 每次 16px，Shift 加速到 48px（WCAG 2.5.7）
  h.addEventListener('keydown', e => {
    const dir = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
    if (!dir) return;
    e.preventDefault();
    const step = e.shiftKey ? 48 : 16;
    const cur = $(sideId).getBoundingClientRect().width;
    const w = Math.max(min, Math.min(max, cur + dir * step * (invert ? -1 : 1)));
    document.documentElement.style.setProperty(varName, w + 'px');
    h.setAttribute('aria-valuenow', Math.round(w));
    localStorage.setItem(varName, w + 'px');
    fitTopbar();
  });
  // 双击复位
  h.addEventListener('dblclick', () => {
    document.documentElement.style.removeProperty(varName);
    localStorage.removeItem(varName);
  });
}
function togglePanel(sideId, cls, tabId, auto) {
  const on = document.body.classList.toggle(cls);
  const t = $(tabId); if (t) t.style.display = on ? 'grid' : 'none';
  // 同步 aria-expanded：折叠按钮与展开页签互为开关
  const panel = $(sideId);
  if (panel) panel.setAttribute('aria-hidden', String(!on));
  $$(`[aria-controls="${sideId.slice(1)}"]`).forEach(b => b.setAttribute('aria-expanded', String(on)));
  localStorage.setItem(cls, on ? '1' : '0');
  if (!auto && cls === 'side-collapsed') localStorage.setItem('side-user', on ? '1' : '0');
  fitTopbar();
}
/* 顶栏自适应：量主区实际宽度决定收起哪几项，而不是看整个窗口 */
function fitTopbar() {
  const bar = $('.topbar'), center = $('#center');
  if (!bar || !center) return;
  const w = center.getBoundingClientRect().width;
  bar.dataset.fit = w < 420 ? 'mini' : w < 620 ? 'ultra' : w < 860 ? 'tight' : '';
}

/* 内容列宽度：正文列与输入框共用同一个宽度和同一个左边距，两边严格对齐。
   对齐 Marvis 的做法（ResizeObserver 量容器 → 写 CSS 变量）。
   上限 1004px —— 再宽正文行长就超过一行能舒服读完的长度了。 */
const CHAT_COL_MAX = 1004;
const CHAT_GUTTER_MIN = 12;
const CHAT_GUTTER_MAX = 44;
function fitChatCol() {
  const center = $('#center');
  if (!center) return;
  // 可用宽度取两边的较小值：#scroll 有滚动条（8px）会比 #composer 窄，
  // 用 #center 的宽度算会让正文列溢出、出现横向滚动条。
  const scroll = $('#scroll'), composer = $('#composer');
  const widths = [center.getBoundingClientRect().width];
  if (scroll) widths.push(scroll.clientWidth);
  if (composer) widths.push(composer.clientWidth);
  const avail = Math.min(...widths.filter(n => n > 0));
  if (!avail) return;
  // 先定留白：窄窗时留白让位给内容，宽窗时留白跟着长
  const gutter = Math.max(CHAT_GUTTER_MIN, Math.min(CHAT_GUTTER_MAX, Math.round(avail * 0.035)));
  // 列宽永远不超过可用宽度 —— 窄窗时宁可让列变窄，也不能撑出横向滚动条
  const col = Math.min(CHAT_COL_MAX, Math.max(0, avail - gutter * 2));
  center.style.setProperty('--chat-col', col + 'px');
  center.style.setProperty('--chat-gutter', gutter + 'px');
}
if (window.ResizeObserver) {
  const _ro = new ResizeObserver(() => { fitChatCol(); fitTopbar(); });
  const _c = $('#center');
  if (_c) _ro.observe(_c);
}

/* 窄窗时右侧面板自动收起：主区要保住可读宽度。
   用户手动收起/展开的意图记在 side-user 里，别跟自动逻辑打架。 */
const NARROW = 900;
function autoCollapse() {
  const side = $('#side'), rail = $('#rail');
  if (!side || !rail) return;
  const narrow = window.innerWidth < NARROW;
  document.body.classList.toggle('narrow', narrow);
  if (localStorage.getItem('side-user') === '1') return;   // 用户明确收起了，不抢
  if (localStorage.getItem('side-user') === '0' && !narrow) return;
  const want = !narrow && $('#side').style.display !== 'none';
  if (want !== !document.body.classList.contains('side-collapsed')) togglePanel('#side', 'side-collapsed', '#sideToggle', true);
}
window.addEventListener('resize', autoCollapse);

$('#railRz') && initResize('#railRz', '#rail', 'rail-collapsed', '--rail-w', RAIL_MIN, RAIL_MAX, false);
$('#sideRz') && initResize('#sideRz', '#side', 'side-collapsed', '--side-w', SIDE_MIN, SIDE_MAX, true);
$('#railCollapse') && $('#railCollapse').addEventListener('click', () => togglePanel('#rail', 'rail-collapsed', '#railToggle'));
$('#railToggle') && $('#railToggle').addEventListener('click', () => togglePanel('#rail', 'rail-collapsed', '#railToggle'));
$('#sideToggle') && $('#sideToggle').addEventListener('click', () => togglePanel('#side', 'side-collapsed', '#sideToggle'));
$('#sideCollapse') && $('#sideCollapse').addEventListener('click', () => togglePanel('#side', 'side-collapsed', '#sideToggle'));

/* ══════ composer 芯片折叠 ══════ */
function paintChipToggle() {
  const bar = $('#chipsBar'), btn = $('#chipToggle');
  if (!bar || !btn) return;
  const hidden = bar.classList.contains('hidden');
  const label = btn.querySelector('span');
  if (label) label.textContent = hidden ? '展开' : '收起';
  btn.setAttribute('aria-expanded', String(!hidden));
}
$('#chipToggle') && $('#chipToggle').addEventListener('click', () => {
  $('#chipsBar').classList.toggle('hidden');
  paintChipToggle();
});
// 输入时自动展开，芯片不可见时用户没法操作
$('#input').addEventListener('input', () => {
  $('#chipsBar').classList.remove('hidden');
  paintChipToggle();
  autoGrow();
});
$('#dashBtn').addEventListener('click', () => setView('dashboard'));

/* 输入框高度随内容增长（对齐 Marvis 的收起↔展开思路）。
   textarea 固定 54px 时，长任务描述只能在框内滚 —— 写着写着看不见自己写了什么。
   这里按 scrollHeight 调整高度，到 200px 封顶后转为内部滚动。 */
const INPUT_MIN_H = 54;
const INPUT_MAX_H = 200;
function autoGrow() {
  const el = $('#input');
  if (!el) return;
  el.style.height = 'auto';                 // 先归零，scrollHeight 才是真实内容高
  const h = Math.min(INPUT_MAX_H, Math.max(INPUT_MIN_H, el.scrollHeight));
  el.style.height = h + 'px';
}

/* ══════ 全局搜索（会话 / 技能 / 工具） ══════ */
let searchTimer = null;
function closeSearch() {
  const box = $('#searchBox');
  if (box) { box.remove(); $('#search').value = ''; }
  $('#search').setAttribute('aria-expanded', 'false');
  searchIdx = -1;
}
let searchIdx = -1;   // 当前高亮项下标，-1 = 无高亮
function moveSearchIdx(step) {
  const rows = [...document.querySelectorAll('#searchBox [data-go]')];
  if (!rows.length) return;
  searchIdx = (searchIdx + step + rows.length) % rows.length;
  rows.forEach((r, i) => {
    r.classList.toggle('on', i === searchIdx);
    r.setAttribute('aria-selected', String(i === searchIdx));
  });
  rows[searchIdx].scrollIntoView({ block: 'nearest' });
}
function renderSearchResults(q, sessions, skills, tools) {
  let box = $('#searchBox');
  if (!box) {
    box = document.createElement('div');
    box.id = 'searchBox';
    box.className = 'searchbox';
    box.setAttribute('role', 'listbox');
    box.setAttribute('aria-label', '搜索结果');
    $('.railbox').appendChild(box);
  }
  searchIdx = -1;
  $('#search').setAttribute('aria-expanded', 'true');
  const rows = [];
  if (q) {
    const ms = sessions.filter(s => (s.title || '').toLowerCase().includes(q));
    rows.push(...ms.slice(0, 5).map(s =>
      `<div class="srow" role="option" aria-selected="false" tabindex="-1" data-go="session" data-id="${esc(s.id)}"><span class="sic" aria-hidden="true">💬</span><span class="stx">${esc(s.title || '（空对话）')}</span><span class="smeta">对话</span></div>`));
    const sk = skills.filter(x => (x.name + x.description).toLowerCase().includes(q));
    rows.push(...sk.slice(0, 5).map(x =>
      `<div class="srow" role="option" aria-selected="false" tabindex="-1" data-go="skill"><span class="sic" aria-hidden="true">⚡</span><span class="stx">${esc(x.name)}</span><span class="smeta">技能</span></div>`));
    const tl = tools.filter(x => (x.name + (x.description || '')).toLowerCase().includes(q));
    rows.push(...tl.slice(0, 6).map(x =>
      `<div class="srow" role="option" aria-selected="false" tabindex="-1" data-go="tool" data-tool="${esc(x.name)}"><span class="sic" aria-hidden="true">◈</span><span class="stx">${esc(x.name)}</span><span class="smeta">工具</span></div>`));
  }
  box.innerHTML = rows.length
    ? rows.join('')
    : '<div class="sempty" role="status">没有匹配的结果</div>';
  box.querySelectorAll('[data-go]').forEach(r => r.addEventListener('click', async () => {
    const kind = r.dataset.go;
    closeSearch();
    if (kind === 'session') { openSession(r.dataset.id); return; }
    if (kind === 'skill') { viewSkill(r.querySelector('.stx').innerText); return; }
    if (kind === 'tool') { $('#input').value = '用 ' + r.dataset.tool + ' 工具'; setView('chat'); $('#input').focus(); }
  }));
}
$('#search').addEventListener('input', e => {
  const q = e.target.value.trim().toLowerCase();
  clearTimeout(searchTimer);
  if (!q) { closeSearch(); return; }
  searchTimer = setTimeout(async () => {
    const [ss, sk, cp] = await Promise.all([
      api('/api/sessions').then(r => r.json()).catch(() => ({ sessions: [] })),
      api('/api/skills').then(r => r.json()).catch(() => ({ skills: [] })),
      api('/api/capabilities').then(r => r.json()).catch(() => ({ groups: [] })),
    ]);
    renderSearchResults(q, ss.sessions || [], sk.skills || [], (cp.groups || []).flatMap(g => g.tools || []));
  }, 220);
});
$('#search').addEventListener('keydown', e => {
  const box = $('#searchBox');
  const open = box && box.querySelector('[data-go]');
  if (e.key === 'ArrowDown') { e.preventDefault(); if (open) moveSearchIdx(searchIdx < 0 ? 1 : 1); return; }
  if (e.key === 'ArrowUp')   { e.preventDefault(); if (open) moveSearchIdx(-1); return; }
  if (e.key === 'Enter') {
    const rows = [...document.querySelectorAll('#searchBox [data-go]')];
    const pick = rows[searchIdx] || rows[0];
    if (pick) { e.preventDefault(); pick.click(); }
    return;
  }
  if (e.key === 'Escape') closeSearch();
});
document.addEventListener('click', e => {
  if (!e.target.closest('#searchBox') && !e.target.closest('.search')) closeSearch();
});

/* ══════ 消息 ══════ */
/* 每条消息在库里有一个序号（0 起，按写入顺序）。
   检查点靠 msg_count 认领它 —— 检查点打在用户消息**之前**，
   msg_count 就是那条提问在库里的行序。

   所以这里的序号要数**所有**消息（用户和助手都占位），
   只数用户消息的话就跟 msg_count 对不上 ——
   「回退到这里」会挂到错的检查点上（退错位置）。 */
let _msgSeq = 0;
/* ── 空会话引导 ──────────────────────────────────────────────
   Marvis 式的「明确的下一步」：新对话不该是一片空白，用户不知道
   这个 Agent 到底能干什么。卡片按三种权限姿态分组，点一下既把
   任务填进输入框、也把交互模式切过去 —— 顺带教会用户三态的差别。*/
const WELCOME_GROUPS = [
  { mode: 'craft', label: '直接动手', hint: '自主规划 · 调工具 · 交付文件', items: [
    '把这个目录里的文件按类型整理好',
    '把这些数据做成一份 Excel 报表',
    '总结我发你的这份文档',
  ] },
  { mode: 'plan', label: '先看方案', hint: '复杂任务先出步骤，确认后再动手', items: [
    '梳理这个项目结构，给我一份改造方案',
    '规划一次数据迁移，先别改任何文件',
  ] },
  { mode: 'ask', label: '只问不改', hint: '只读分析，不碰文件也不写盘', items: [
    '这段报错是什么意思',
    '这份合同有哪些风险点',
  ] },
];

function paintWelcome() {
  const box = $('#messages');
  if (!box || box.querySelector('.msg') || box.querySelector('.welcome')) return;
  const el = document.createElement('div');
  el.className = 'welcome';
  el.innerHTML = `
    <div class="whead">
      <div class="wlogo">OX</div>
      <div><h2>你好，我是 Oryx</h2>
        <p>直接说要做什么。我会自己拆解步骤、调用工具，把结果做成能打开的文件交给你。</p></div>
    </div>
    ${WELCOME_GROUPS.map(g => `
      <div class="wgroup">
        <div class="wgtitle"><span class="wgtag ${g.mode}">${esc(g.label)}</span><span class="wghint">${esc(g.hint)}</span></div>
        <div class="wcards">${g.items.map(t =>
          `<button class="wcard" data-wgoal="${escAttr(t)}" data-wmode="${g.mode}" type="button">
             <span>${esc(t)}</span><span class="wgo">↗</span></button>`).join('')}</div>
      </div>`).join('')}
    <div class="wfoot">也可以把文件拖进窗口，或 <span class="kbd">Ctrl+V</span> 粘贴截图。破坏性动作会先出执行计划等你确认。</div>`;
  box.appendChild(el);
  el.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-wgoal]');
    if (!b) return;
    const mode = b.dataset.wmode;
    // 与顶栏切换走同一条路：把模式写回内核，避免「界面显示 plan / 请求却发 craft」
    if (mode && mode !== (S.interaction || 'craft')) {
      try {
        const r = await api('/api/interaction', {
          method: 'POST', body: JSON.stringify({ interaction: mode }),
        });
        if (!r.ok) { toast('切换模式失败，仍按当前模式发送'); }
        else {
          S.interaction = mode;
          $$('#interSeg button').forEach(x => x.classList.toggle('on', x.dataset.i === mode));
        }
      } catch (_) { toast('切换模式失败，仍按当前模式发送'); }
    }
    $('#input').value = b.dataset.wgoal;
    autoGrow();
    $('#input').focus();
  });
}

function addMsg(role, text) {
  // 引导卡只在空会话出现 —— 有第一条消息就收起
  const wel = $('#messages .welcome');
  if (wel) wel.remove();
  // 历史消息 role 是 'assistant'，实时流是 'bot'——统一成 bot，否则恢复的消息丢样式
  const r = role === 'assistant' ? 'bot' : role;
  const w = document.createElement('div');
  w.className = 'msg ' + r;
  w.innerHTML = `<div class="av">${r === 'user' ? '你' : 'OX'}</div><div class="body"><div class="bubble"></div></div>`;
  const bubble = w.querySelector('.bubble');
  if (r === 'bot') bubble.innerHTML = md(text); else bubble.textContent = text;
  $('#messages').appendChild(w);
  $('#scroll').scrollTop = 1e9;
  // 序号取「即将写入库里的位置」—— 每条消息都占一个号，
  // 助手消息也算（它同样进 messages 表）。
  const seq = _msgSeq++;
  w.dataset.msgSeq = String(seq);
  // 助手消息挂操作栏（复制 / 重新生成）。用户消息不挂 —— 自己的话没什么可重生成的。
  // 放进 .body 列里、正文下方（不是 .msg 的第三个子元素 —— 那样会被挤到行尾）
  if (r === 'bot') w.querySelector('.body').appendChild(msgActions(bubble));
  else w.querySelector('.body').appendChild(rewindBar(seq));
  return bubble;
}

/* 「回退到这里」操作条。
   点了弹一个范围选择（只退代码 / 只退对话 / 都退）——
   默认「都退」太粗，误点一次就把用户的对话也削了；
   分开选让人自己决定要撤什么。 */
const ICON_REWIND = '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/></svg>';
function rewindBar(msgSeq) {
  const box = document.createElement('div');
  box.className = 'rewindbar';
  box.innerHTML = `<button class="rbtn" data-rewind="${msgSeq}" title="回退到这一轮之前" aria-label="回退到这一轮之前" type="button">${ICON_REWIND}<span>回退到这里</span></button>`;
  box.querySelector('[data-rewind]').addEventListener('click', e => {
    e.stopPropagation();
    openRewindDialog(+e.currentTarget.dataset.rewind);
  });
  return box;
}

/* 消息操作栏（对齐 Marvis 的 .semi-ai-chat-dialogue-action）。
   复制走剪贴板；重新生成让后端把最后一轮助手回复删掉重跑。

   文本**不**在创建时抓一份 —— 流式回复是之后才灌进气泡的，
   抓快照的话复制到的会是空字符串。点击时才从气泡里读。 */
const ICON_COPY = '<svg aria-hidden="true" viewBox="0 0 24 24"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>';
const ICON_RETRY = '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/></svg>';

function msgActions(bubble) {
  const box = document.createElement('div');
  box.className = 'mactions';
  box.innerHTML =
    `<button class="maction" data-act="copy" title="复制回复" aria-label="复制这条回复" type="button">${ICON_COPY}</button>` +
    `<button class="maction" data-act="retry" title="重新生成" aria-label="重新生成这条回复" type="button">${ICON_RETRY}</button>`;
  box.addEventListener('click', e => {
    const btn = e.target.closest('[data-act]');
    if (!btn) return;
    e.stopPropagation();
    // 从气泡里读当前文本 —— 流式结束后才是最终内容
    const text = bubble ? bubble.textContent : '';
    if (btn.dataset.act === 'copy') copyMsg(text, btn);
    else if (btn.dataset.act === 'retry') retryLast(btn);
  });
  return box;
}

/* 复制成功要在按钮上给反馈。
   只弹 toast 不够 —— 用户点了之后鼠标往往已经移开，
   按钮闪一下绿色才说明"确实复制了"。 */
async function copyMsg(text, btn) {
  const body = (text || '').trim();
  if (!body) { toast('这条回复是空的'); return; }
  let ok = false;
  try {
    await navigator.clipboard.writeText(body);
    ok = true;
  } catch (_) {
    // 剪贴板不可用时（权限/非安全上下文）退回 execCommand
    const ta = document.createElement('textarea');
    ta.value = body;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { ok = document.execCommand('copy'); } catch (__) { ok = false; }
    ta.remove();
  }
  if (ok) {
    toast('已复制');
    if (btn) {
      btn.classList.add('done');
      setTimeout(() => btn.classList.remove('done'), 1200);
    }
  } else {
    toast('复制失败，请手动选中');
  }
}

/* 重新生成：让后端删掉最后一轮助手回复、用同一提问重跑。
   失败要给出可见反馈 —— 静默失败用户会以为按钮坏了。 */
async function retryLast(btn) {
  if (S.busy) { toast('还有任务在跑'); return; }
  if (!S.current) { toast('这条消息没有对应的会话'); return; }
  // 按钮进禁用态：重跑要几秒，不拦住用户连点
  if (btn) { btn.disabled = true; btn.classList.add('done'); }
  try {
    const r = await api('/api/chat/retry', {
      method: 'POST',
      body: JSON.stringify({ task_id: S.current }),
    });
    if (!r || r.ok === false) {
      toast('重新生成失败：' + ((r && r.detail) || '未知原因'));
      return;
    }
    toast('正在重新生成…');
  } catch (e) {
    toast('重新生成失败：' + e.message);
  } finally {
    // 重跑成功后旧消息会被换掉，按钮随之消失；
    // 失败时要把按钮恢复可用，否则用户没法再试一次
    if (btn && document.body.contains(btn)) {
      btn.disabled = false;
      btn.classList.remove('done');
    }
  }
}
function agentLine(name, tool, state) {
  const el = document.createElement('div');
  el.className = 'agentline';
  el.innerHTML = `<span class="nm">${esc(name)}</span>` +
    (state ? `<span class="dots"><i></i><i></i><i></i></span>` : '') +
    (tool ? `<span class="tool">${esc(tool)}</span><span>${esc(state || '')}</span>` : '');
  return el;
}

/* ══════ 会话 ══════ */
/** 会话时间分组：今天 / 昨天 / 本周 / 更早。 */
function sessGroup(ts) {
  const d = new Date((ts || 0) * 1000);
  const now = new Date();
  const day = 86400;
  const t0 = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000;
  const v = d.getTime() / 1000;
  if (v >= t0) return '今天';
  if (v >= t0 - day) return '昨天';
  if (v >= t0 - day * 7) return '本周';
  if (v >= t0 - day * 30) return '本月';
  return '更早';
}

/** 会话状态标记。跑着的转圈，失败/完成给个色点 ——
    对标 Marvis 的 statusIndicator：一眼看出哪条还在跑、哪条卡住了。 */
function sessStatus(s) {
  const st = (s.status || '').toLowerCase();
  if (st === 'running' || st === 'in_progress') {
    return '<span class="sstat run" title="运行中"><i></i></span>';
  }
  if (st === 'failed' || st === 'error') {
    return '<span class="sstat bad" title="失败">✕</span>';
  }
  if (st === 'done' || st === 'completed') {
    return '<span class="sstat ok" title="已完成"><i></i></span>';
  }
  if (s.todo_open) return '<span class="sstat wait" title="有待确认事项">•</span>';
  return '';
}

/* ══════ 检查点：可回退的位置 ══════ */
/* 拉这个会话的检查点。
   检查点靠 msg_count 认领它对应第几条消息 ——
   msg_count 是「打点那一刻库里已有几条消息」，所以它正好等于
   那条提问在消息序列里的下标。 */
async function loadCheckpoints(taskId) {
  if (!taskId) { S.checkpoints = []; S.cpLoadedFor = ''; return []; }
  try {
    const r = await api(`/api/checkpoints/${encodeURIComponent(taskId)}`);
    const d = await r.json();
    S.checkpoints = d.checkpoints || [];
    S.cpLoadedFor = taskId;
  } catch (_) {
    S.checkpoints = [];
    S.cpLoadedFor = taskId;
  }
  return S.checkpoints;
}

/** 找到对应「第 idx 条用户提问」的检查点。
    检查点按 msg_count 认领消息：msg_count = idx 的那个就是。 */
function checkpointForMsg(idx) {
  return S.checkpoints.find(c => c.msg_count === idx) || null;
}

/* 回退弹窗：选范围（只退代码 / 只退对话 / 都退）。
   为什么不让用户直接点按钮就退 —— 两种范围的破坏性差别很大：
   只退代码是改文件，都退还会删掉后面的对话。让人自己选。 */
const REWIND_SCOPES = [
  { id: 'Code', label: '只退代码', desc: '把这一轮改过的文件还原，不动对话' },
  { id: 'Conversation', label: '只退对话', desc: '删掉这一轮之后的问答，不动文件' },
  { id: 'CodeAndConversation', label: '代码和对话都退', desc: '文件还原 + 删掉这一轮之后的问答' },
];

function openRewindDialog(msgSeq) {
  const cp = checkpointForMsg(msgSeq);
  if (!cp) { toast('这一轮还没有检查点'); return; }
  const files = cp.files || 0;
  openModal({
    title: '回退到这一轮之前',
    body: `<div class="picklist">
      ${REWIND_SCOPES.map(s => `<button class="pick" data-scope="${s.id}" type="button">
        <span class="pk">${s.id === 'Code' ? '⌘' : s.id === 'Conversation' ? '☰' : '↺'}</span>
        <span><span class="pt">${s.label}</span><div class="pd">${s.desc}</div></span>
      </button>`).join('')}
    </div>
    <div class="hint">${files ? `这一轮之后改过 ${files} 个文件` : '这一轮没有改动文件'}${cp.label ? ' · ' + esc(cp.label) : ''}</div>`,
    foot: `<button class="btn" data-close type="button">取消</button>`,
  });
  $$('#modal .pick').forEach(b => b.addEventListener('click', async () => {
    const scope = b.dataset.scope;
    const myGen = _modalGen;
    b.disabled = true;
    try {
      await doRewind(cp.id, scope);
      if (_modalGen === myGen) closeModal();
    } catch (e) {
      b.disabled = false;
      toast('回退失败：' + (e && e.message ? e.message : e));
    }
  }));
}

/* 执行回退。回退完要重新拉会话内容 —— 消息和步骤都被裁掉了，
   界面还显示着旧内容的话，用户会以为回退没生效。 */
async function doRewind(checkpointId, scope) {
  const r = await api(`/api/checkpoints/${encodeURIComponent(checkpointId)}/rewind`, {
    method: 'POST',
    body: JSON.stringify({ scope }),
  });
  if (!r.ok) {
    const e = await r.json().catch(() => ({}));
    throw new Error(e.detail || r.status);
  }
  const d = await r.json();
  const c = d.content || {};
  const parts = [];
  if ((c.restored || []).length) parts.push(`还原 ${c.restored.length} 个文件`);
  if ((c.removed || []).length) parts.push(`删除 ${c.removed.length} 个新建文件`);
  if (c.messages_removed) parts.push(`删掉 ${c.messages_removed} 条消息`);
  if ((c.skipped || []).length) parts.push(`${c.skipped.length} 个跳过`);
  if ((c.errors || []).length) parts.push(`${c.errors.length} 个失败`);
  toast('已回退' + (parts.length ? '：' + parts.join('、') : ''));
  // 重新拉这个会话 —— 消息、步骤都被裁了
  if (S.current) await openSession(S.current);
  renderDetail();
}

async function loadSessions() {
  const d = await (await api('/api/sessions')).json();
  S.sessions = d.sessions || [];
  $('#sessCnt').textContent = S.sessions.length || '';
  const box = $('#sessList');
  if (!S.sessions.length) { box.innerHTML = '<div class="empty">还没有对话</div>'; return; }
  // 按时间分组 —— 一堆平铺的会话找不到「昨天那个」
  // 组内按最近动过的时间排：刚聊完的那条在最上面。
  const groups = new Map();
  for (const s of S.sessions) {
    const g = sessGroup(s.updated_at || s.created_at);
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(s);
  }
  for (const list of groups.values()) {
    list.sort((a, b) => (b.updated_at || b.created_at || 0) - (a.updated_at || a.created_at || 0));
  }
  // 分组本身也要按最新时间排 —— 「今天」永远在最上面
  const order = [...groups.entries()].sort((a, b) => {
    const t = list => Math.max(...list.map(s => s.updated_at || s.created_at || 0));
    return t(b[1]) - t(a[1]);
  });
  box.innerHTML = order.map(([g, list]) => `
    <div class="sessgroup">
      <div class="sgh">${esc(g)}</div>
      ${list.map(s => `
        <div class="sess ${s.id === S.current ? 'on' : ''}" role="button" tabindex="0" aria-label="打开对话 ${esc(s.title || '（空对话）')}" data-id="${s.id}">
          ${sessStatus(s)}
          <span class="tx">${esc(s.title || '（空对话）')}</span>
          <span class="ren" data-ren="${s.id}" role="button" tabindex="0" aria-label="重命名对话 ${esc(s.title || '（空对话）')}" title="重命名">✎</span>
          <span class="del" data-del="${s.id}" role="button" tabindex="0" aria-label="删除对话 ${esc(s.title || '（空对话）')}">✕</span>
        </div>`).join('')}
    </div>`).join('');
  box.querySelectorAll('.sess').forEach(el => {
    el.addEventListener('click', e => {
      if (e.target.dataset.del || e.target.dataset.ren) return;
      openSession(el.dataset.id);
    });
    el.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openSession(el.dataset.id); }
    });
  });
  box.querySelectorAll('[data-ren]').forEach(el => {
    const ren = el.dataset.ren;
    const doRen = async e => {
      e.stopPropagation();
      const item = S.sessions.find(s => s.id === ren);
      openRenameDialog(ren, (item && item.title) || '');
    };
    el.addEventListener('click', doRen);
    el.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); doRen(e); }
    });
  });
  box.querySelectorAll('[data-del]').forEach(el => {
    const del = el.dataset.del;
    const kill = async e => {
      e.stopPropagation();
      if (!confirm('删除这个会话？消息和步骤记录会一并清除。')) return;
      await api(`/api/sessions/${del}`, { method: 'DELETE' });
      if (S.current === del) newChat();
      loadSessions();
    };
    el.addEventListener('click', kill);
    el.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); kill(e); }
    });
  });
}

/* 会话改名弹窗。
   改名只改显示名 —— 会话 id、消息、步骤、检查点全都不动。 */
function openRenameDialog(taskId, current) {
  openModal({
    title: '重命名对话',
    body: `<div class="fld">
      <label for="rnTitle">名字</label>
      <input class="inp" id="rnTitle" value="${escAttr(current)}" placeholder="给这个对话起个名字" maxlength="60" />
      <div class="hint">留空则用首条提问当标题。</div>
    </div>`,
    foot: `<button class="btn" data-close type="button">取消</button>
           <button class="btn primary" id="rnOk" type="button">保存</button>`,
  });
  const input = $('#rnTitle');
  if (input) { input.focus(); input.select(); }
  const ok = $('#rnOk');
  const save = async () => {
    const title = (input.value || '').trim();
    if (ok) { ok.disabled = true; ok.textContent = '保存中…'; }
    try {
      const r = await api(`/api/sessions/${encodeURIComponent(taskId)}`, {
        method: 'PUT',
        body: JSON.stringify({ title }),
      });
      if (!r.ok) {
        const e = await r.json().catch(() => ({}));
        throw new Error(e.detail || r.status);
      }
      closeModal();
      // 标题改了，当前会话的顶栏也要跟着变
      if (S.current === taskId) {
        const s = S.sessions.find(x => x.id === taskId);
        $('#pageTitle').textContent = (title || (s && s.title) || '对话').slice(0, 30);
      }
      await loadSessions();
      toast(title ? '已重命名' : '已恢复默认标题');
    } catch (e) {
      if (ok) { ok.disabled = false; ok.textContent = '保存'; }
      toast('改名失败：' + (e && e.message ? e.message : e));
    }
  };
  if (ok) ok.addEventListener('click', save);
  if (input) {
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); save(); }
    });
  }
}
async function openSession(id) {
  S.current = id;
  // 认领这个会话的任务事件 —— 续接提问时 RUN_STARTED 带的就是这个 id。
  // 不认领的话，正在跑的那个任务的内容会被当成「别的任务」挡掉，
  // 用户点了历史会话就看不到正在流出的回答。
  S.taskId = id;
  const d = await (await api(`/api/sessions/${id}`)).json();
  $('#messages').innerHTML = '';
  S_REASON = null;
  // 换会话也要清时间线 —— 那是上一轮的协作记录
  S.tl = [];
  // 消息序号重置 —— 下面的序号要跟库里的一致，
  // 否则「回退到这里」会挂到错的提问上。
  _msgSeq = 0;
  (d.messages || []).forEach(m => {
    if (m.role === 'user' || m.role === 'assistant') addMsg(m.role, m.content);
  });
  // 空历史会话（比如刚建还没问）给引导，别让人对着一片空白发愣
  paintWelcome();
  S.steps.clear(); S.artifacts = [];
  renderTodos(d.todos || []);
  renderSteps(d.steps || []);
  renderFiles();
  // 产物要重建 —— 以前 S.artifacts 是纯前端内存态，
  // 换个会话就没了：用户回看历史会话时，找不到「上次那个文件在哪」。
  // 审计记录里有每次工具调用的参数，从那里能捞回产物路径。
  await rebuildArtifacts(id);
  $('#pageTitle').textContent = (d.title || d.task?.title || d.task?.goal || '对话').slice(0, 30);
  // 检查点要在消息渲染之后拉 —— 按钮要点开才知道自己对应哪个点
  await loadCheckpoints(id);
  await loadSessions();
  setView('chat');
}

/**
 * 从审计记录里重建产物列表。
 *
 * 审计表记了每次工具调用的名字与参数，而产物就是「哪些工具写了文件」。
 * 所以重开历史会话时不用另建一张表 —— 审计里已经有了。
 */
async function rebuildArtifacts(taskId) {
  if (!taskId) return;
  try {
    const d = await (await api(`/api/audit/${taskId}`)).json();
    const rows = d.audit || [];
    const found = [];
    const seen = new Set();
    for (const a of rows) {
      if (!FILE_TOOLS.has(a.tool)) continue;
      const args = typeof a.args === 'string' ? safeParse(a.args) : (a.args || {});
      const path = args.path || args.image_path || args.output || args.file;
      if (!path || seen.has(path)) continue;
      seen.add(path);
      found.push({ tool: a.tool, path });
    }
    if (found.length) {
      S.artifacts = found.reverse().slice(0, 50);
      renderFiles();
    }
  } catch (e) {
    // 审计拿不到就算了 —— 不影响主流程
  }
}

/** JSON.parse 但不抛异常。 */
function safeParse(text) {
  try { return JSON.parse(text); } catch (e) { return {}; }
}
function newChat() {
  S.current = null; S.steps.clear(); S.artifacts = []; S.assistant = null;
  // 换会话就重置事件水位，否则新任务的事件会被单调校验当重复丢掉
  lastSeq = 0;
  S_REASON = null;
  // 多 Agent 时间线也一起清 —— 那是上一轮的协作记录
  S.tl = [];
  // 总览面板与建议卡也归零（#messages 下面会被清空，SUG 要同步）
  ovReset(); renderOverview(); SUG = [];
  // 跑着的任务状态也要清。
  // 不清的话：界面是空的（新会话），发送键却还显示成红色「停止」，
  // 用户一点就触发 stopRun() 而不是发消息 —— 看起来像按钮坏了。
  S.busy = false;
  S.streaming = false;
  setSendState('idle');
  hideThinking();
  // 新会话不认旧任务的事件了 —— 后台那个任务还在跑，
  // 它的内容流进来会污染这个新会话（用户会看到陌生的回答凭空冒出来）。
  S.taskId = '';
  // 检查点也清 —— 新会话没有可退的点，留着上一个的会让「回退到这里」挂错
  S.checkpoints = []; S.cpLoadedFor = '';
  _msgSeq = 0;
  $('#messages').innerHTML = '';
  paintWelcome();
  $('#pageTitle').textContent = '新对话';
  $('#pageSub').textContent = '';
  $('#p-steps').innerHTML = '<div class="empty">还没有执行记录</div>';
  renderTodos([]); renderFiles(); loadTeams();
  $('#input').value = '';
  autoGrow();
  setView('chat');
  loadSessions();
  $('#input').focus();
}
/* ══════ 顶栏状态 ══════ */
async function refreshState() {
  try {
    const s = await (await api('/api/state')).json();
    S.interaction = s.interaction; S.style = s.style; S.expert = s.expert || ''; S.mode = s.mode;
    S.provider = s.provider || '';
    $('#modelChip').textContent = `${s.provider || '未配置'} · ${s.model || '—'}`;
    $('#modeSel').value = s.mode;
    // 副标题写当前跑的是本地还是云端 —— 不看 provider 名字猜
    $('#pageSub').textContent = s.provider ? (s.provider_is_local ? '本地模型' : '云端模型') : '';
    $$('#interSeg button').forEach(b => b.classList.toggle('on', b.dataset.i === s.interaction));
    const es = $('#expertSel');
    if (es.dataset.n !== String((s.experts || []).length)) {
      es.dataset.n = String((s.experts || []).length);
      es.innerHTML = '<option value="">无专家</option>' + s.experts.map(e => `<option value="${e.id}">${esc(e.name)}</option>`).join('');
    }
    es.value = s.expert || '';
    const ss = $('#styleSel');
    if (ss.dataset.n !== String((s.styles || []).length)) {
      ss.dataset.n = String((s.styles || []).length);
      ss.innerHTML = s.styles.map(x => `<option value="${x.name}">${esc(x.label)}</option>`).join('');
    }
    ss.value = s.style;
    paintModeHint();
  } catch (_) {}
}

/* 当前模式提示 —— 让"分工"可见。
   用户能在发送前就知道这一轮 Oryx 只能读、还是能动手。 */
const MODE_HINT = {
  ask:      { label: '问答',   desc: '只读调研，不会改任何东西' },
  plan:     { label: '规划',   desc: '只出方案，不动手' },
  craft:    { label: '执行',   desc: '可以动手，破坏性操作会先问你' },
  bypass:   { label: '开发者', desc: '跳过确认，禁止规则仍然生效' },
  dontAsk:  { label: '无人值守', desc: '需要确认的操作一律拒绝' },
  auto:     { label: '自动判定', desc: '由系统判断要不要问你' },
  delegate: { label: '委派',   desc: '只做协调，动手交给子 Agent' },
};
function paintModeHint() {
  const el = $('#modeHint');
  if (!el) return;
  const mode = S.policy ? POLICY_MODE[S.policy] : (S.interaction || 'craft');
  const info = MODE_HINT[mode] || MODE_HINT.craft;
  el.innerHTML = `<span class="mh ${esc(mode)}">${esc(info.label)}</span><span>${esc(info.desc)}</span>`;
}
/**
 * 刷新用量数字。
 *
 * 以前这个函数是**空的** —— 但它在 RUN_FINISHED 之后被调用，
 * 于是跑完一轮，界面上的 token 数字全是旧的（直到手动切走再切回用量面板）。
 * 空函数比没有这个调用更糟：调用点看起来"已经处理过了"。
 */
async function refreshUsage() {
  try {
    const u = await (await api('/api/usage')).json();
    const t = u.today || {};
    const total = (t.input_tokens || 0) + (t.output_tokens || 0);
    S.usage = u;
    // 用量面板开着的话顺手刷新它
    if (S.view === 'dashboard') renderDashboard();
  } catch (e) {
    // 用量拿不到不影响主流程，静默即可（下次还会刷）
  }
}

/* ══════ 右栏 ══════ */
const RISK = ['只读', '写入', '危险', '禁止'];
function badge(risk) { return `<span class="badge L${risk}">${RISK[risk] || '?'}</span>`; }
function stepCard(ev) {
  let c = S.steps.get(ev.step_id);
  if (c) return c;
  const p = $('#p-steps');
  if (p.querySelector('.empty')) p.innerHTML = '';
  c = document.createElement('div');
  c.className = 'step';
  // 带上 step_id：验收脚本要精确定位到某一张卡（同名工具会跑好几张）
  if (ev.step_id) c.dataset.proofStep = ev.step_id;
  const argsText = JSON.stringify(ev.args || {});
  c.innerHTML = `<div class="t"><span class="tool">${esc(ev.tool || '')}</span>${badge(ev.risk ?? 0)}<span class="badge run">执行中</span></div>
    <div class="a">${esc(argsText)}</div><div class="r"></div><div class="proof"></div>`;
  // args 太长默认折叠，点头行展开/收起
  if (argsText.length > 120) c.classList.add('clps');
  c.querySelector('.t').addEventListener('click', () => c.classList.toggle('clps'));
  p.prepend(c);
  S.steps.set(ev.step_id, c);
  return c;
}
function setBadge(c, cls, text) {
  const b = c.querySelector('.badge.run,.badge.done,.badge.failed,.badge.denied');
  if (b) { b.className = 'badge ' + cls; b.textContent = text; }
}
/* 只读自证徽章：把内核算出来的 proof 字段显示出来。
   抄 Marvis 的做法 —— 每个只读结果都自述「我没动过」，
   用户不必靠猜工具名去判断这次操作改了什么。

   字段是**声明式**的：只读工具恒为「未改动」，
   有副作用的工具标「已改动」。真正的明细去审计记录里看。 */
function markProof(c, ev) {
  if (!c || typeof ev.mutated !== 'boolean') return;
  const slot = c.querySelector('.proof');
  if (!slot) return;
  if (ev.mutated) {
    slot.innerHTML = '<span class="badge L1">已改动</span>';
    return;
  }
  slot.innerHTML = '<span class="badge L0">未改动</span>';
}
/* 自动判定模式的判定结果徽章。
   用户在 auto 模式下会看到「这次为什么没弹确认卡」——
   分类器判了 allow/ask/deny，得让人看得见，否则 auto 就成了黑箱。 */
function markAutoclass(ev) {
  if (!ev || !ev.step_id) return;
  const c = S.steps.get(ev.step_id);
  if (!c) return;
  const label = { allow: '自动放行', ask: '仍需确认', deny: '自动拒绝' }[ev.verdict] || ev.verdict;
  const cls = { allow: 'L0', ask: 'L1', deny: 'L2' }[ev.verdict] || 'L1';
  let tag = c.querySelector('.autoclass');
  if (!tag) {
    tag = document.createElement('span');
    tag.className = 'autoclass badge';
    c.querySelector('.t').appendChild(tag);
  }
  tag.className = 'autoclass badge ' + cls;
  tag.textContent = label;
  if (ev.reason) tag.title = ev.reason;
}

/* 生命周期 hook 上报的耗时：给步骤卡补一个「耗时 xx ms」尾标 */
function fmtMs(ms) {  const n = Number(ms) || 0;
  return n >= 1000 ? (n / 1000).toFixed(1) + ' s' : n + ' ms';
}
function markElapsed(ev) {
  if (ev.kind !== 'tool' || !ev.step_id) return;
  const c = S.steps.get(ev.step_id);
  if (!c) return;
  let tag = c.querySelector('.spent');
  if (!tag) {
    tag = document.createElement('span');
    tag.className = 'spent';
    c.querySelector('.t').appendChild(tag);
  }
  tag.textContent = fmtMs(ev.elapsed_ms);
}
function renderSteps(steps) {
  const p = $('#p-steps');
  if (!steps.length) { p.innerHTML = '<div class="empty">还没有执行记录</div>'; return; }
  p.innerHTML = ''; S.steps.clear();
  steps.slice().reverse().forEach(s => {
    // 历史 args 可能损坏或为空，解析失败要降级而不是让整栏渲染中断
    let sArgs = {};
    try { sArgs = JSON.parse(s.args || '{}') || {}; } catch (_) { sArgs = { _raw: String(s.args || '') }; }
    const c = stepCard({ step_id: s.id, tool: s.tool, args: sArgs, risk: s.risk });
    const map = { done: ['done', '完成'], failed: ['failed', '失败'], denied: ['denied', '已取消'] };
    const m = map[s.state];
    if (m) setBadge(c, m[0], m[1]);
    const r = c.querySelector('.r');
    if (s.error) r.textContent = s.error; else if (s.result) r.textContent = String(s.result).slice(0, 400);
  });
}
function renderTodos(todos) {
  S.todos = todos || [];
  const p = $('#p-todos');
  if (!S.todos.length) { p.innerHTML = '<div class="empty">没有待办</div>'; return; }
  const mk = { done: '✓', in_progress: '▸', pending: '○' };
  p.innerHTML = S.todos.map(t => `<div class="todo ${t.status}"><span class="mk">${mk[t.status] || '○'}</span><span class="tx">${esc(t.title)}</span></div>`).join('');
}
/**
 * 会产出文件的工具 —— **仅作兜底**。
 *
 * 主判定已经搬到后端：step.done 事件带 artifact_path，由工具结果的
 * 元数据（确实写了字节 + 带路径）判定，新增落盘工具不用改前端。
 * 这里只用于两类老路径：
 *   1. fs_write 这类结果只有一句文本、没有 bytes/path 元数据的工具；
 *   2. 会话重开时从审计记录重建产物列表（审计里只有工具名和参数）。
 * 新交付格式也登记进来，保证历史会话重建不漏。
 */
const FILE_TOOLS = new Set([
  'fs_write', 'docx_write', 'xlsx_write', 'pptx_write', 'browser_screenshot',
  'ocr_image', 'browser_extract', 'shell', 'shell_sandbox',
  'html_write', 'pdf_write', 'image_write',
]);
const PREVIEW_EXTS = new Set(['.html', '.htm', '.svg', '.png', '.jpg', '.jpeg',
  '.gif', '.webp', '.pdf', '.txt', '.md', '.csv', '.json']);
function previewable(path) {
  const dot = String(path || '').lastIndexOf('.');
  return dot >= 0 && PREVIEW_EXTS.has(String(path).slice(dot).toLowerCase());
}

/**
 * 产物面板。
 *
 * 以前是**纯展示**：一串路径，用户看到「生成好了 D:\\...\\报表.xlsx」之后
 * 只能自己去找文件 —— 交付的最后一公里没通。
 *
 * 现在每个产物都能：
 *   · 点开      —— 用系统默认程序打开
 *   · 定位      —— 在文件管理器里选中它
 *   · 复制路径  —— 贴到别处用
 *   · 交给 Oryx —— 把它作为附件带回对话，继续加工
 */
function renderFiles() {
  const p = $('#p-files');
  if (!S.artifacts.length) {
    p.innerHTML = '<div class="empty">还没有产物<br><span style="font-size:11.5px;opacity:.7">'
      + 'Agent 写文件后会出现在这里，可以直接点开</span></div>';
    return;
  }
  p.innerHTML = S.artifacts.map((a, i) => `
    <div class="artifact" data-art="${i}">
      <span class="badge L1">${esc(a.tool)}</span>
      <div class="p" title="${esc(a.path)}">${esc(a.path)}</div>
      <div class="acts">
        ${previewable(a.path) ? `<button class="mini" data-act="preview" data-i="${i}" type="button" title="在界面里预览">预览</button>` : ''}
        <button class="mini" data-act="open" data-i="${i}" type="button" title="用默认程序打开">打开</button>
        <button class="mini" data-act="reveal" data-i="${i}" type="button" title="在文件管理器中定位">定位</button>
        <button class="mini" data-act="copy" data-i="${i}" type="button" title="复制路径">复制</button>
        <button class="mini" data-act="send" data-i="${i}" type="button" title="交给 Oryx 继续处理">交给 Oryx</button>
      </div>
    </div>`).join('');
}

// 产物按钮用**事件委托**绑定：面板内容是重渲染出来的，
// 逐个 addEventListener 的话每次重渲染都要重新绑，漏了就点了没反应。
(function bindArtifactActions() {
  const p = document.getElementById('p-files');
  if (!p) return;
  p.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-act]');
    if (!btn) return;
    e.stopPropagation();
    artifactAction(btn.dataset.act, Number(btn.dataset.i));
  });
})();

/** 产物操作：打开 / 定位 / 复制 / 交给 Oryx。 */
async function artifactAction(act, i) {
  const a = S.artifacts[i];
  if (!a) return;
  if (act === 'preview') {
    // 直接开一个同源新窗口看内容。令牌走 ?t=（GET 带不了自定义头，
    // 服务端的闸门本来就允许这么做，EventSource 也是这么传的）。
    // 后面对 HTML/SVG 加了 CSP sandbox，模型写的脚本拿不到同源权限。
    const url = '/api/files/preview?t=' + encodeURIComponent(ORYX_TOKEN)
      + '&path=' + encodeURIComponent(a.path);
    const win = window.open(url, '_blank', 'noopener');
    if (!win) toast('浏览器拦截了新窗口，请允许弹窗后重试');
    return;
  }
  if (act === 'copy') {
    try {
      await navigator.clipboard.writeText(a.path);
      toast('路径已复制');
    } catch (e) {
      toast('复制失败，可以手动选中路径复制');
    }
    return;
  }
  if (act === 'send') {
    // 交给 Oryx：把文件挂到输入框的附件栏，用户可以接着说「把这个再汇总一下」。
    // 走的是和「添加附件」一样的路径（S.files + paintAttbar），
    // 不另起一套 —— 两套附件机制迟早会对不上。
    if (!S.files.includes(a.path)) S.files.push(a.path);
    paintAttbar();
    const input = $('#input');
    if (input && !input.value.trim()) {
      input.value = '请继续处理这个文件：' + a.path;
    }
    if (input) input.focus();
    toast('已加入附件，可以继续说了');
    return;
  }
  const reveal = act === 'reveal';
  try {
    const r = await api('/api/files/open', {
      method: 'POST',
      body: JSON.stringify({ path: a.path, reveal }),
    });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      toast('打不开：' + (e.detail || r.status));
    }
  } catch (e) {
    toast('打不开：' + e.message);
  }
}

/* 失败分类的中文说明。放在前端是因为要给人看 ——
   分类代码（E1…E5）是给模型看的，鼠标悬停时给用户看人话。 */
const ERR_CLASS_HINT = {
  E1: '环境不可达 —— 检查网络/服务是否在运行，别重复试',
  E2: '权限不足 —— 需要更高权限，或换用有权限的账号',
  E3: '工具不支持 —— 换个工具或换个做法',
  E4: '结果与预期不符 —— 确认一下需求是不是理解错了',
  E5: '依赖缺失 —— 缺文件/缺包/缺配置，先补齐',
  E0: '失败原因不明 —— 看报错原文，同一操作最多再试一次',
};

/* ══════ 右栏「详情」：用量 / 审计 / 导出 ══════ */
function download(name, text, type) {
  const blob = new Blob([text], { type: type || 'application/json;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function renderDetail() {
  const p = $('#p-detail');
  if (!p) return;
  if (!S.current) { p.innerHTML = '<div class="empty">打开一个会话后这里显示用量与审计</div>'; return; }
  const id = S.current;
  const [ud, sd, cps] = await Promise.all([
    api(`/api/audit/${encodeURIComponent(id)}`).then(r => r.json()).catch(() => ({ audit: [] })),
    api(`/api/sessions/${encodeURIComponent(id)}`).then(r => r.json()).catch(() => null),
    api(`/api/checkpoints/${encodeURIComponent(id)}`).then(r => r.json()).catch(() => ({ checkpoints: [] })),
  ]);
  const audit = ud.audit || [];
  const u = (sd && sd.usage) || {};
  const inTok = (u.input_tokens || 0), outTok = (u.output_tokens || 0);
  const RISK = ['只读', '写入', '危险', '禁止'];
  const checkpointList = cps.checkpoints || [];
  p.innerHTML = `
    <div class="statpills" style="margin-bottom:14px">
      <div class="statpill"><div class="sv">${u.calls || 0}</div><div class="sl">模型调用</div></div>
      <div class="statpill"><div class="sv">${((inTok + outTok) / 1000).toFixed(1)}k</div><div class="sl">Token 合计</div></div>
      <div class="statpill"><div class="sv">${audit.length}</div><div class="sl">审计条目</div></div>
    </div>
    ${checkpointList.length ? `<div style="margin-bottom:14px">
      <div class="sgh" style="margin-bottom:6px">检查点</div>
      <div class="cplist">
        ${checkpointList.map(c => `<div class="cpitem ${c.current ? 'cur' : ''}">
          <span class="cpdot"></span>
          <span class="cplabel" title="${escAttr(c.label || '')}">${esc(c.label || ('第 ' + ((c.seq ?? 0) + 1) + ' 轮'))}</span>
          <span class="cpmeta">${c.files ? c.files + ' 个文件' : '无改动'}</span>
        </div>`).join('')}
      </div>
    </div>` : ''}
    <div class="row" style="margin-bottom:14px">
      <button class="btn" id="dAudit" type="button">导出审计</button>
      <button class="btn" id="dConv" type="button">导出对话</button>
      <button class="btn" id="dPlan" type="button">导出执行计划</button>
    </div>
    ${audit.length ? `<div class="member" style="flex-direction:column;align-items:stretch">
      ${audit.slice(-40).reverse().map(a => `
        <div class="ahrow">
          <span class="badge L${a.risk ?? 0}" style="flex:0 0 auto">${RISK[a.risk] || '?'}</span>
          <span style="font-family:var(--font-mono);color:var(--ink);flex:1;min-width:0;word-break:break-all">${esc(a.tool || '')}${a.decision ? ` <span style="color:var(--muted)">· ${esc(a.decision)}</span>` : ''}${a.outcome ? ` <span style="color:var(--muted)">· ${esc(a.outcome)}</span>` : ''}${a.mutated === 1 ? ' <span style="color:var(--warn)">· 已改动</span>' : (a.mutated === 0 ? ' <span style="color:var(--ok)">· 未改动</span>' : '')}</span>
          <span style="color:var(--muted);flex:0 0 auto">${esc((a.ts ? new Date(a.ts * 1000).toLocaleTimeString('zh-CN', { hour12: false }) : ''))}</span>
        </div>`).join('')}
    </div>` : '<div class="empty">这个会话还没有审计记录</div>'}`;
  $('#dAudit').addEventListener('click', () => download(`audit-${id}.json`, JSON.stringify(audit, null, 2)));
  $('#dConv').addEventListener('click', () => {
    const lines = ((sd && sd.messages) || []).map(m => `## ${m.role === 'user' ? '我' : 'Oryx'}\n\n${m.content}`);
    download(`conversation-${id}.md`, lines.join('\n\n---\n\n') || '# 空对话', 'text/markdown;charset=utf-8');
  });
  $('#dPlan').addEventListener('click', () => {
    const steps = ((sd && sd.steps) || []).map(s => `- [${s.state}] ${s.tool} ${(s.args || '').slice(0, 200)}`);
    download(`steps-${id}.md`, `# 执行记录 ${id}\n\n` + (steps.join('\n') || '- 无'), 'text/markdown;charset=utf-8');
  });
}

/* ══════ 多 Agent 团队（右栏摘要 + 全屏协作面板） ══════ */
S.teamBusy = new Set();
/* 管家团队页的状态。选中哪个管家、它那边跑出来的对话。 */
S.butlers = [];
S.butlerSel = '';
S.butlerTalk = [];   // [{who:'oryx'|'agent', agent, name, icon, text, ts}]

/** 拉一次子 Agent / 管家目录，填好名字与图标的映射。
    界面上所有显示名字的地方都从这里取值 —— 不写死，
    免得加了个新管家还得改前端。 */
async function loadAgentCatalog() {
  try {
    const caps = await (await api('/api/capabilities')).json();
    const all = caps.agents || [];
    S.agentNames = {}; S.agentIcons = {}; S.agentSubs = {};
    for (const a of all) {
      if (a.name) S.agentNames[a.id] = a.name;
      if (a.icon) S.agentIcons[a.id] = a.icon;
      if (a.subtitle) S.agentSubs[a.id] = a.subtitle;
    }
    return all;
  } catch (_) {
    return [];
  }
}

/** 管家团队页。左边名单，右边选中管家的详情与派活区。 */
async function renderButlers() {
  const all = await loadAgentCatalog();
  // 只列管家（kind=butler）—— 普通子 Agent（reviewer 之类）在编队时出现，不占这个位置
  S.butlers = all.filter(a => a.kind === 'butler');
  if (!S.butlerSel || !S.butlers.some(b => b.id === S.butlerSel)) {
    S.butlerSel = S.butlers.length ? S.butlers[0].id : '';
  }
  $('#page').innerHTML = `
    <div class="phead"><h1>管家团队</h1><span class="sub">Oryx 统筹，管家各管一块</span><div class="spacer"></div>
      <button class="btn" id="backBtn" type="button">返回对话</button></div>
    <div class="teamwrap">
      <aside class="teamaside">
        <div class="th">我的团队</div>
        <div class="alist" id="bList"></div>
      </aside>
      <div class="bdetail" id="bDetail"></div>
    </div>`;
  $('#backBtn').addEventListener('click', () => setView('chat'));
  paintButlerList();
  paintButlerDetail();
}

function paintButlerList() {
  const box = $('#bList');
  if (!box) return;
  if (!S.butlers.length) {
    box.innerHTML = '<div class="empty">没有可用管家<br><span style="font-size:11px">检查 agents/ 目录下的 AGENT.md</span></div>';
    return;
  }
  box.innerHTML = S.butlers.map(b => `
    <button class="bitem ${b.id === S.butlerSel ? 'on' : ''}" data-bsel="${escAttr(b.id)}" type="button">
      <span class="bav">${esc(b.icon || b.name.slice(0, 1))}</span>
      <span class="binfo"><span class="bname">${esc(b.name)}</span>
        <span class="bsub">${esc(b.subtitle || b.description || '')}</span></span>
    </button>`).join('');
  box.querySelectorAll('[data-bsel]').forEach(el => el.addEventListener('click', () => {
    S.butlerSel = el.dataset.bsel;
    S.butlerTalk = [];
    paintButlerList();
    paintButlerDetail();
  }));
}

function paintButlerDetail() {
  const box = $('#bDetail');
  if (!box) return;
  const b = S.butlers.find(x => x.id === S.butlerSel);
  if (!b) { box.innerHTML = '<div class="empty">选一个管家</div>'; return; }
  const talk = S.butlerTalk.filter(t => t.agent === b.id);
  box.innerHTML = `
    <div class="bhead">
      <span class="bav">${esc(b.icon || b.name.slice(0, 1))}</span>
      <span style="flex:1;min-width:0">
        <div class="bn">${esc(b.name)}</div>
        <div class="bs">${esc(b.subtitle || '')} · @${escAttr(b.id)}</div>
        <div class="bdesc">${esc(b.description || '')}</div>
        <div class="btools">${(b.tools || []).map(t => `<span class="btool">${esc(t)}</span>`).join('')}</div>
      </span>
    </div>
    <div class="bbody">
      <div class="btask">
        <textarea id="bTask" placeholder="交给${esc(b.name)}的任务…（Enter 换行，点「派活」发送）" aria-label="给${escAttr(b.name)}派活"></textarea>
        <div class="row">
          <button class="btn primary" id="bSend" type="button">派活</button>
          <span style="font-size:11.5px;color:var(--muted)">${b.tools.length} 个工具 · 最多 ${b.max_turns} 轮</span>
        </div>
      </div>
      <div class="btalk" id="bTalk">
        ${talk.length ? talk.map(t => `
          <div class="btmsg ${t.who === 'oryx' ? 'me' : ''}">
            <span class="bav">${esc(t.who === 'oryx' ? 'OX' : (t.icon || t.name.slice(0, 1)))}</span>
            <span class="bc">
              <span class="bh"><span class="bn">${esc(t.name)}</span><span class="bt">${esc(t.tag || '')}</span></span>
              <span class="bx">${esc(t.text)}</span>
            </span>
          </div>`).join('') : '<div class="empty" style="border:0;padding:18px">还没有派过活</div>'}
      </div>
    </div>`;
  const send = $('#bSend');
  if (send) send.addEventListener('click', () => dispatchButler(b.id));
  const ta = $('#bTask');
  if (ta) {
    ta.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); dispatchButler(b.id); }
    });
  }
}

/** 派活给管家：走团队机制，主 Agent 统一调度。 */
async function dispatchButler(agentId) {
  const ta = $('#bTask');
  const goal = (ta && ta.value || '').trim();
  if (!goal) { toast('先说要做什么'); return; }
  const btn = $('#bSend');
  if (btn) { btn.disabled = true; btn.textContent = '执行中…'; }
  S.butlerTalk.push({ who: 'oryx', agent: agentId, name: 'Oryx', icon: 'OX', text: goal, tag: '派活' });
  paintButlerDetail();
  try {
    // 复用团队：建一个只含该管家的临时队，投递 + 派发。
    // 不新建队的话，team_dispatch 会因为「不在队里」直接拒。
    const teamName = 'butler-' + agentId;
    const teams = (await (await api('/api/teams')).json()).teams || [];
    if (!teams.some(t => t.name === teamName)) {
      const cr = await api('/api/teams', { method: 'POST', body: JSON.stringify({ name: teamName, goal, members: [agentId] }) });
      if (!cr.ok) {
        const x = await cr.json().catch(() => ({}));
        toast('建队失败：' + (x.detail || cr.status));
        if (btn) { btn.disabled = false; btn.textContent = '派活'; }
        return;
      }
    }
    const mr = await api(`/api/teams/${encodeURIComponent(teamName)}/message`, { method: 'POST', body: JSON.stringify({ to: agentId, content: goal }) });
    if (!mr.ok) {
      const x = await mr.json().catch(() => ({}));
      toast('投递失败：' + (x.detail || mr.status));
      if (btn) { btn.disabled = false; btn.textContent = '派活'; }
      return;
    }
    const dr = await api(`/api/teams/${encodeURIComponent(teamName)}/dispatch`, { method: 'POST', body: JSON.stringify({ agent: agentId }) });
    const txt = await dr.text();
    let content = '';
    try { content = JSON.parse(txt).content || ''; } catch (_) { content = txt; }
    if (!dr.ok) {
      toast('执行失败：' + (content || '').slice(0, 90));
      if (btn) { btn.disabled = false; btn.textContent = '派活'; }
      return;
    }
    const spec = S.butlers.find(x => x.id === agentId);
    S.butlerTalk.push({ who: 'agent', agent: agentId, name: spec ? spec.name : agentId, icon: spec ? spec.icon : '', text: content, tag: '完成' });
    if (btn) { btn.disabled = false; btn.textContent = '派活'; }
    paintButlerDetail();
  } catch (err) {
    toast('派活失败：' + err.message);
    if (btn) { btn.disabled = false; btn.textContent = '派活'; }
  }
}

async function loadTeams() {
  const p = $('#p-team');
  if (!p) return;
  let teams = [];
  try { teams = (await (await api('/api/teams')).json()).teams || []; } catch (_) {}
  if (!teams.length) {
    p.innerHTML = '<div class="empty">还没有团队<br><span style="font-size:11px">点顶栏「团队」组建多 Agent 协作</span></div>';
    return;
  }
  p.innerHTML = teams.map(t => `
    <div class="team">
      <div class="th"><span class="tn">${esc(t.name)}</span><span class="tc">${(t.members || []).length} 人 · ${t.message_count || 0} 条</span></div>
      ${t.goal ? `<div class="tg">${esc(t.goal)}</div>` : ''}
      ${(t.members || []).map(m => `
        <div class="member">
          <span class="mk">${esc(S.agentIcons[m.agent_id] || (m.agent_id || '?').slice(0, 1).toUpperCase())}</span>
          <span class="mn">${esc(cnAgent(m.agent_id))}</span>
          <span class="mr">${esc(m.role || '')}</span>
          <span class="mstate ${S.teamBusy.has(t.name + '/' + m.agent_id) ? 'busy' : ''}">${S.teamBusy.has(t.name + '/' + m.agent_id) ? '执行中' : '就绪'}</span>
        </div>`).join('')}
      ${(t.thread || []).slice(-4).map(m => `
        <div class="msgline ${m.sender === 'main' ? 'me' : ''}"><span class="who">${esc(m.sender === 'main' ? 'Oryx' : cnAgent(m.sender))}</span><span class="what">${esc((m.content || '').slice(0, 90))}</span></div>`).join('')}
      <div class="act"><button class="btn" data-openpanel="${esc(t.name)}" type="button">打开协作面板</button></div>
    </div>`).join('');
  p.querySelectorAll('[data-openpanel]').forEach(b => b.addEventListener('click', () => openTeamPanel(b.dataset.openpanel)));
}

/* ── 团队创建/编辑表单（复用） ── */
async function teamForm({ id = '', name = '', goal = '', members = [] } = {}) {
  const caps = await (await api('/api/capabilities')).json();
  const agents = caps.agents || [];
  openModal({
    title: id ? '编辑团队' : '组建多 Agent 团队',
    wide: true,
    body: `<div class="fld"><label for="tfName">团队名</label><input class="inp"  id="tfName" value="${esc(name)}" placeholder="如 release、research"/></div>
      <div class="fld"><label for="tfGoal">目标（会写进每个成员的简报）</label><input class="inp"  id="tfGoal" value="${esc(goal)}" placeholder="这一轮团队要一起完成什么"/></div>
      <div class="fld"><label>成员（勾选后可在右侧写分工）</label>
        <div class="picklist">${agents.map(a => `
          <button class="pick ${members.includes(a.id) ? 'on' : ''}" data-tfm="${escAttr(a.id)}" type="button">
            <span class="pk">${esc(a.icon || a.name.slice(0, 1))}</span>
            <span><span class="pt">${esc(a.name)}</span><div class="pd">${esc(a.description || '')}</div></span>
            <span class="pd2"><span class="chip">${a.tools.length} 工具</span><span class="chip">${a.max_turns} 轮</span></span>
          </button>`).join('')}</div>
        <div class="hint">分工写在下一栏。留空的话，该成员就按自己的职责来。</div></div>
      <div class="fld"><label>分工（谁干什么）</label>
        <div id="tfRoles"></div>
        <div class="hint">每个成员一句话说明它这一轮负责什么。这句话会跟着任务一起派给它。</div></div>`,
    foot: `<button class="btn" data-close type="button">取消</button><button class="btn primary" id="tfSave" type="button">${id ? '保存' : '创建'}</button>`,
  });
  const picked = members.slice();
  // 分工用「id:角色」的形式传（后端 parse_members 认这个格式）
  const roles = {};
  const paintRoles = () => {
    const box = $('#tfRoles');
    if (!box) return;
    box.innerHTML = picked.length ? picked.map(aid => {
      const a = agents.find(x => x.id === aid);
      return `<div class="row" style="margin-bottom:6px">
        <span style="width:110px;font-size:12.5px;color:var(--ink-2)">${esc(a ? a.name : aid)}</span>
        <input class="inp" data-role="${escAttr(aid)}" value="${escAttr(roles[aid] || '')}" placeholder="这一轮负责什么" aria-label="${escAttr(aid)}的分工" style="flex:1"/>
      </div>`;
    }).join('') : '<div class="hint">先勾选成员</div>';
    box.querySelectorAll('[data-role]').forEach(inp => inp.addEventListener('input', () => {
      roles[inp.dataset.role] = inp.value.trim();
    }));
  };
  $$('#modal [data-tfm]').forEach(b => b.addEventListener('click', () => {
    const a = b.dataset.tfm;
    const i = picked.indexOf(a);
    if (i >= 0) picked.splice(i, 1); else picked.push(a);
    b.classList.toggle('on', picked.includes(a));
    paintRoles();
  }));
  paintRoles();
  $('#tfSave').addEventListener('click', async () => {
    const nm = $('#tfName').value.trim();
    const gl = $('#tfGoal').value.trim();
    if (!nm) { toast('给团队起个名'); return; }
    if (!picked.length) { toast('至少勾一个成员'); return; }
    // 分工要一起传 —— 之前只传 id 数组，role 从来没写进去过，
    // 每个成员的角色一律是字面量「成员」。
    const memberList = picked.map(aid => (roles[aid] ? `${aid}:${roles[aid]}` : aid));
    const r = await api('/api/teams', { method: 'POST', body: JSON.stringify({ name: nm, goal: gl, members: memberList }) });
    if (!r.ok) { const e = await r.json().catch(() => ({})); toast('失败：' + (e.detail || r.status)); return; }
    closeModal(); loadTeams(); toast('团队已组建');
  });
}

/* ── 团队协作面板 ── */
async function openTeamPanel(teamName) {
  let d;
  try { d = await (await api(`/api/teams/${encodeURIComponent(teamName)}`)).json(); }
  catch (_) { toast('团队不存在'); return; }
  let t = d;
  await loadAgentCatalog();
  const caps = await (await api('/api/capabilities')).json();
  const agentNames = (caps.agents || []).map(a => a.id);
  // 有几个团队时，面板顶部给一个切换器 ——
  // 之前顶栏「团队」只打开第一个团队，另外几个根本进不去。
  const allTeams = ((await (await api('/api/teams')).json()).teams || []);
  const paint = () => {
    openModal({
      title: `团队 · ${t.name}`,
      wide: true,
      body: `${allTeams.length > 1 ? `<div class="row" style="margin-bottom:14px">
          <span style="font-size:12px;color:var(--muted)">切换团队</span>
          <select class="inp" id="tpSwitch" style="flex:1;max-width:280px">
            ${allTeams.map(x => `<option value="${escAttr(x.name)}" ${x.name === t.name ? 'selected' : ''}>${esc(x.name)}</option>`).join('')}
          </select>
        </div>` : ''}
        ${t.goal ? `<div class="teamcard" style="margin-bottom:14px"><div class="tgoal" style="margin:0">${esc(t.goal)}</div></div>` : ''}
        <div class="sect" style="margin-bottom:16px"><h2>成员与分工</h2>
          <div id="tpMembers">${(t.members || []).map(m => `
            <div class="member">
              <span class="mk">${esc(S.agentIcons[m.agent_id] || (m.agent_id || '?').slice(0, 1).toUpperCase())}</span>
              <span class="mn">${esc(cnAgent(m.agent_id))}</span>
              ${S.agentSubs[m.agent_id] ? `<span class="msub" title="该 Agent 的职责">${esc(S.agentSubs[m.agent_id])}</span>` : ''}
              <span class="mr">${esc(m.role || '')}</span>
              <span class="mstate ${S.teamBusy.has(t.name + '/' + m.agent_id) ? 'busy' : ''}">${S.teamBusy.has(t.name + '/' + m.agent_id) ? '执行中' : '就绪'}</span>
              <span class="mact">
                <button class="btn" data-pmsg="${escAttr(m.agent_id)}" type="button">派活</button>
                <button class="btn" data-disp="${escAttr(m.agent_id)}" ${S.teamBusy.has(t.name + '/' + m.agent_id) ? 'disabled' : ''} type="button">开工</button>
                <button class="btn" data-tpedit="${escAttr(m.agent_id)}" type="button">改分工</button>
                <button class="btn" data-tpdrop="${escAttr(m.agent_id)}" type="button">移出</button>
              </span>
            </div>`).join('') || '<div class="empty">还没有成员</div>'}</div>
          <div class="row" style="margin-top:10px">
            <select class="inp" id="tpAddAgent" style="flex:1">${agentNames.filter(n => !(t.members || []).some(m => m.agent_id === n)).map(n => `<option value="${escAttr(n)}">${esc(cnAgent(n))}</option>`).join('') || '<option>（所有 Agent 都已在队）</option>'}</select>
            <input class="inp" id="tpAddRole" placeholder="分工，如：负责实现" aria-label="新成员的分工" style="width:170px"/>
            <button class="btn" id="tpAdd" type="button">加入团队</button>
          </div>
        </div>
        <div class="sect" style="margin-bottom:12px"><h2>协作记录</h2>
          <div class="thread" id="tpThread" style="max-height:none">${(t.thread || []).map(m => `
            <div class="msgline ${m.sender === 'main' ? 'me' : ''}"><span class="who">${esc(m.sender === 'main' ? 'Oryx' : cnAgent(m.sender))}</span><span>${esc(m.content || '')}</span></div>`).join('') || '<div class="empty">还没有消息</div>'}</div>
        </div>`,
      foot: `<button class="btn" id="tpDel" type="button">解散团队</button>
             <div class="spacer"></div>
             <button class="btn" data-close type="button">关闭</button>
             <button class="btn" id="tpEdit" type="button">编辑团队</button>
             <button class="btn primary" id="tpAuto" type="button">按顺序跑完全部成员</button>`,
    });
    // 编辑团队：改名 + 改目标
    const editBtn = $('#tpEdit');
    if (editBtn) editBtn.addEventListener('click', () => {
      closeModal();
      openModal({
        title: '编辑团队', wide: true,
        body: `<div class="fld"><label for="tgName">团队名字</label><input class="inp" id="tgName" value="${esc(t.name)}"/>
          <div class="hint">改名之后原来的协作记录还在（团队 id 没变）。</div></div>
          <div class="fld"><label for="tgGoal">团队目标</label><textarea class="inp" id="tgGoal">${esc(t.goal || '')}</textarea></div>`,
        foot: `<button class="btn" data-close type="button">取消</button><button class="btn primary" id="tgSave" type="button">保存</button>`,
      });
      $('#tgSave').addEventListener('click', async () => {
        const name = $('#tgName').value.trim();
        const goal = $('#tgGoal').value.trim();
        if (!name) { toast('团队名字不能为空'); return; }
        const r = await api(`/api/teams/${encodeURIComponent(t.name)}`, {
          method: 'PUT', body: JSON.stringify({ name, goal }),
        });
        if (!r.ok) { const e = await r.json().catch(() => ({})); toast('失败：' + (e.detail || r.status)); return; }
        closeModal();
        toast('已保存');
        const res = await (await api('/api/teams')).json();
        const list2 = res.teams || [];
        const idx = list2.findIndex(x => x.name === name);
        if (idx >= 0) { allTeams[idx] = list2[idx]; }
        openTeamPanel(name);
        loadTeams();
      });
    });
    // 解散团队
    const delBtn = $('#tpDel');
    if (delBtn) delBtn.addEventListener('click', () => {
      closeModal();
      ucConfirm({
        title: '解散团队',
        message: `确定解散团队「<b>${esc(t.name)}</b>」？<br/>团队里的 ${(t.members || []).length} 个成员和全部协作记录都会一起清掉，撤不回来。`,
        okLabel: '解散',
        onOk: async () => {
          const r = await api(`/api/teams/${encodeURIComponent(t.name)}`, { method: 'DELETE' });
          if (!r.ok) throw new Error('解散失败');
          toast('已解散');
          await loadTeams();
          const rest = (await (await api('/api/teams')).json()).teams || [];
          if (rest.length) openTeamPanel(rest[0].name);
        },
      });
    });
    const sw = $('#tpSwitch');
    if (sw) sw.addEventListener('change', async () => {
      closeModal();
      openTeamPanel(sw.value);
    });
    const bind = () => {
      $$('#modal [data-pmsg]').forEach(b => b.addEventListener('click', () => askTask(b.dataset.pmsg)));
      // 改某个成员的分工
      $$('#modal [data-tpedit]').forEach(b => b.addEventListener('click', () => {
        const agent = b.dataset.tpedit;
        const m = (t.members || []).find(x => x.agent_id === agent);
        openModal({
          title: `改 ${cnAgent(agent)} 的分工`,
          body: `<div class="fld"><label for="trRole">分工</label><input class="inp" id="trRole" value="${esc(m ? m.role : '')}" placeholder="如：负责实现"/>
            <div class="hint">这段话会随派活一起发给成员，它才知道自己这一轮干什么。</div></div>`,
          foot: `<button class="btn" data-close type="button">取消</button><button class="btn primary" id="trSave" type="button">保存</button>`,
        });
        $('#trSave').addEventListener('click', async () => {
          const role = $('#trRole').value.trim();
          const r = await api(`/api/teams/${encodeURIComponent(t.name)}`, {
            method: 'PUT', body: JSON.stringify({ member: agent, role }),
          });
          if (!r.ok) { const e = await r.json().catch(() => ({})); toast('失败：' + (e.detail || r.status)); return; }
          closeModal();
          toast('分工已更新');
          try { t = await (await api(`/api/teams/${encodeURIComponent(t.name)}`)).json(); } catch (_) {}
          paint(); loadTeams();
        });
      }));
      // 把成员移出团队
      $$('#modal [data-tpdrop]').forEach(b => b.addEventListener('click', () => {
        const agent = b.dataset.tpdrop;
        const m = (t.members || []).find(x => x.agent_id === agent);
        ucConfirm({
          title: '移出团队',
          message: `把 <b>${esc(cnAgent(agent))}</b>${m && m.role ? '（' + esc(m.role) + '）' : ''} 移出「${esc(t.name)}」？<br/>它还能重新加入，但这一轮不再参与派活。`,
          okLabel: '移出',
          onOk: async () => {
            const r = await api(`/api/teams/${encodeURIComponent(t.name)}/members/${encodeURIComponent(agent)}`, { method: 'DELETE' });
            if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || '移出失败'); }
            toast('已移出');
            try { t = await (await api(`/api/teams/${encodeURIComponent(t.name)}`)).json(); } catch (_) {}
            paint(); loadTeams();
          },
        });
      }));
      $$('#modal [data-disp]').forEach(b => b.addEventListener('click', async () => {
        const a = b.dataset.disp;
        const key = t.name + '/' + a;
        S.teamBusy.add(key);
        b.disabled = true; b.textContent = '执行中…';
        const r = await api(`/api/teams/${encodeURIComponent(t.name)}/dispatch`, { method: 'POST', body: JSON.stringify({ agent: a }) });
        S.teamBusy.delete(key);
        const txt = await r.text();
        let content = '';
        try { content = JSON.parse(txt).content || ''; } catch (_) { content = txt; }
        if (!r.ok) { toast((content || '执行失败').slice(0, 90)); paint(); return; }
        toast(`${a} 已回话`);
        try { t = await (await api(`/api/teams/${encodeURIComponent(t.name)}`)).json(); } catch (_) {}
        paint(); loadTeams();
      }));
      const add = $('#tpAdd');
      if (add) add.addEventListener('click', async () => {
        const agent = $('#tpAddAgent').value, role = $('#tpAddRole').value.trim();
        if (!agent) return;
        const r = await api(`/api/teams/${encodeURIComponent(t.name)}/members`, { method: 'POST', body: JSON.stringify({ agent, role }) });
        if (!r.ok) { const e = await r.json().catch(() => ({})); toast('失败：' + (e.detail || r.status)); return; }
        try { t = await (await api(`/api/teams/${encodeURIComponent(t.name)}`)).json(); } catch (_) {}
        paint(); loadTeams(); toast(agent + ' 已加入');
      });
      $('#tpAuto').addEventListener('click', async () => {
        for (const m of (t.members || []).slice()) {
          const key = t.name + '/' + m.agent_id;
          if (S.teamBusy.has(key)) continue;
          S.teamBusy.add(key);
          toast('派活给 ' + cnAgent(m.agent_id) + '…');
          const c = (t.members || []).find(x => x.agent_id === m.agent_id);
          // 简报里带上分工 —— 成员才知道自己这一轮干什么
          const brief = (t.goal || '按你的分工完成这一轮任务') + (c && c.role ? `（分工：${c.role}）` : '');
          await api(`/api/teams/${encodeURIComponent(t.name)}/message`, { method: 'POST', body: JSON.stringify({ to: m.agent_id, content: brief }) });
          const r = await api(`/api/teams/${encodeURIComponent(t.name)}/dispatch`, { method: 'POST', body: JSON.stringify({ agent: m.agent_id }) });
          S.teamBusy.delete(key);
          if (!r.ok) { const e = await r.json().catch(() => ({ content: '' })); toast(cnAgent(m.agent_id) + ' 失败：' + (e.content || '').slice(0, 70)); }
        }
        try { t = await (await api(`/api/teams/${encodeURIComponent(t.name)}`)).json(); } catch (_) {}
        paint(); loadTeams(); toast('团队跑完一轮');
      });
    };
    bind();
  };
  const askTask = async (agent) => {
    openModal({
      title: `给 ${cnAgent(agent)} 派活`,
      body: `<div class="fld"><label for="pmText">任务内容</label><textarea class="inp"  id="pmText" placeholder="这个成员要做什么"></textarea>
        <div class="hint">投递只进信箱不会自动执行；回到面板点「开工」或「按顺序跑完」。</div></div>`,
      foot: `<button class="btn" data-close type="button">取消</button><button class="btn primary" id="pmSend" type="button">投递</button>`,
    });
    $('#pmSend').addEventListener('click', async () => {
      const content = $('#pmText').value.trim();
      if (!content) { toast('写点内容'); return; }
      const r = await api(`/api/teams/${encodeURIComponent(t.name)}/message`, { method: 'POST', body: JSON.stringify({ to: agent, content }) });
      if (!r.ok) { const e = await r.json().catch(() => ({})); toast('失败：' + (e.detail || r.status)); return; }
      try { t = await (await api(`/api/teams/${encodeURIComponent(t.name)}`)).json(); } catch (_) {}
      paint(); loadTeams(); toast('已投递（点「开工」执行）');
    });
  };
  paint();
  // 面板开着的时候，成员自己说的话要实时进来 ——
  // 之前 SSE 只刷新右栏摘要，面板里的协作记录一直停在旧状态。
  // 用一个定时器轮询：面板关掉时定时器自然没有意义。
  clearInterval(S.teamPoll);
  S.teamPoll = setInterval(async () => {
    if (!$('#tpThread')) { clearInterval(S.teamPoll); S.teamPoll = null; return; }
    try {
      const fresh = await (await api(`/api/teams/${encodeURIComponent(t.name)}`)).json();
      if (fresh.message_count !== (t.message_count || 0)) {
        t = fresh;
        paint();
      }
    } catch (_) {}
  }, 1500);
}

$('#teamBtn').addEventListener('click', async () => {
  const teams = (await (await api('/api/teams')).json()).teams || [];
  if (!teams.length) { teamForm(); return; }
  // 多个团队时给个选择器 —— 之前只打开第一个，其余团队进不去
  if (teams.length === 1) { openTeamPanel(teams[0].name); return; }
  openModal({
    title: '选择团队',
    body: `<div class="picklist">
      ${teams.map(t => `<button class="pick" data-tp="${escAttr(t.name)}" type="button">
        <span class="pk">◈</span>
        <span><span class="pt">${esc(t.name)}</span>
          <div class="pd">${(t.members || []).length} 人 · ${t.message_count || 0} 条${t.goal ? ' · ' + esc(t.goal.slice(0, 40)) : ''}</div></span>
      </button>`).join('')}
    </div>`,
    foot: `<button class="btn" data-close type="button">取消</button><button class="btn primary" id="tpNew" type="button">新建团队</button>`,
  });
  $$('#modal [data-tp]').forEach(b => b.addEventListener('click', () => {
    closeModal();
    openTeamPanel(b.dataset.tp);
  }));
  $('#tpNew').addEventListener('click', () => { closeModal(); teamForm(); });
});
$$('.stabs button[data-p]').forEach(b => b.addEventListener('click', () => {
  $$('.stabs button[data-p]').forEach(x => x.classList.toggle('on', x === b));
  $$('.pane').forEach(p => p.classList.toggle('on', p.id === 'p-' + b.dataset.p));
  if (b.dataset.p === 'detail') renderDetail();
  if (b.dataset.p === 'overview') renderOverview();
}));

/* ══════ 发送 ══════ */
async function send() {
  const input = $('#input');
  let goal = input.value.trim();
  if (S.files.length) {
    const list = S.files.join('、');
    if (!goal.includes(list)) goal = (goal ? goal + '\n' : '') + '涉及文件：' + list;
  }
  if (S.skills.length) {
    const list = S.skills.join('、');
    if (!goal.includes(list)) goal = (goal ? goal + '\n' : '') + '请先调用技能：' + list;
  }
  if (S.policy) {
    goal = (goal ? goal + '\n' : '') + `[权限：${POLICY_LABEL[S.policy]}——${POLICY_DESC[S.policy]}]`;
  }
  if (!goal || S.busy) return;
  // 交互模式要在**清掉 S.policy 之前**取。
  //
  // 以前 S.policy 在上一行就被清成 null，这里再读永远是 undefined ——
  // 于是「只读权限」之类的选择**从来没生效过**，实际跑的一直是顶栏模式。
  // 这是安全语义错误：用户以为自己锁了权限，Agent 却照常写文件。
  const interaction = S.policy ? POLICY_MODE[S.policy] : (S.interaction || 'craft');
  input.value = '';
  autoGrow();          // 发完把输入框缩回一行
  S.files = []; S.skills = []; S.policy = null; paintAttbar();
  addMsg('user', goal);
  S.busy = true;
  S.busySince = Date.now();
  armBusyTimeout();
  setSendState('running');
  $('#p-steps').innerHTML = '<div class="empty">准备中…</div>';
  S.steps.clear(); renderTodos([]); renderFiles();
  // 模式必须随请求一起发出去。光改顶栏的 #interSeg 是不够的 ——
// 那只是把模式写进 config，换完立刻发送会读到还没落盘的值。
  // 对话续接：用户没点「新建对话」就一直在同一个会话里。
  // 把当前会话 id 发过去，内核会把这一轮挂到同一串对话下。
  const sessionId = S.current || '';
  // 记下这一轮的序号。发起在途期间用户点「停止」，
  // 中止请求会带着它 —— 后端据此知道该把这笔中止算到哪一轮头上。
  const seq = ++S.sendSeq;
  S.inflightSeq = seq;
  try {
    const r = await api('/api/chat', { method: 'POST', body: JSON.stringify({ goal, interaction, session_id: sessionId, seq }) });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      addMsg('bot', '发起失败：' + (e.detail || r.status));
      S.busy = false; setSendState('idle');
    }
  } catch (e) {
    addMsg('bot', '无法连接内核：' + e.message);
    S.busy = false; setSendState('idle');
  } finally {
    // 发起落地了（不管成功还是失败）—— 清掉「在途」标记。
    // 注意别在这里把 S.busy 清掉：任务已经在跑了，界面要保持忙碌态。
    if (S.inflightSeq === seq) S.inflightSeq = 0;
  }
}

/**
 * 发送键的两种形态。
 * 跑着的时候它变成「停止」—— Marvis 就是这么做的：
 * 同一个位置、同一个键，跑的时候能按停。
 */
function setSendState(state) {
  const btn = $('#sendBtn');
  const icon = $('#sendIcon');
  if (!btn || !icon) return;
  if (state === 'running') {
    btn.classList.add('stopping');
    btn.disabled = false;
    btn.title = '停止';
    btn.setAttribute('aria-label', '停止当前任务');
    icon.innerHTML = '<rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor" stroke="none"/>';
  } else {
    btn.classList.remove('stopping');
    btn.disabled = false;
    btn.title = '发送';
    btn.setAttribute('aria-label', '发送任务');
    icon.innerHTML = '<path d="M12 19V5M5 12l7-7 7 7"/>';
  }
}

/** 停止当前任务。 */
async function stopRun() {
  const btn = $('#sendBtn');
  if (btn) btn.disabled = true;
  // 带上这一轮的序号。
  // 「发送」和「停止」是两条独立的请求，可能并发 —— 中止请求先到时，
  // 后端要靠这个序号知道「有个发起还没落地」，等它落地再把中止转给内核。
  // 不带的话那一下会被当成「没有正在跑的任务」丢掉（按了停止，任务照跑到底）。
  const seq = S.inflightSeq || S.sendSeq;
  try {
    const r = await api('/api/chat/cancel', { method: 'POST', body: JSON.stringify({ seq }) });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      toast(e.detail || '没有正在跑的任务');
    } else {
      toast('已停止');
    }
  } catch (e) {
    toast('停止失败：' + (e && e.message ? e.message : e));
  } finally {
    if (btn) btn.disabled = false;
  }
}
$('#sendBtn').addEventListener('click', () => {
  // 跑着的时候这个键是「停止」
  if (S.busy) { stopRun(); return; }
  send();
});
$('#input').addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } });

/* ══════ 事件 ══════ */
function showThinking(label) {
  hideThinking();
  const el = document.createElement('div');
  el.className = 'msg bot thinking';
  el.id = 'thinkingRow';
  el.innerHTML = `<div class="av">OX</div>
    <div class="body"><div class="thinkrow">
      <span class="tdots"><i></i><i></i><i></i></span>
      <span class="tlabel">${esc(label || '正在思考')}</span>
    </div></div>`;
  $('#messages').appendChild(el);
  $('#scroll').scrollTop = 1e9;
  return el;
}
function setThinking(label) {
  const el = $('#thinkingRow');
  if (el && label) el.querySelector('.tlabel').textContent = label;
}
function hideThinking() {
  const el = $('#thinkingRow');
  if (el) el.remove();
}

/* ══════ 思考过程面板 ══════
   对齐 Marvis 的 ai-chat-dialogue-reasoning：
     - 标题写「正在思考中…」/「已思考完成」
     - 思考时自动展开，结束后折叠（用户点一下能再展开）
     - 面板插在助手消息上方，跟正文分开

   思考内容是模型的思维链。多数服务端模型不返回这一段，
   那就没有面板 —— 界面不能因为缺了它就空着。 */
let S_REASON = null;

function reasonPanel() {
  const el = document.createElement('div');
  el.className = 'reason live open';
  el.innerHTML = `<div class="reason-head" role="button" tabindex="0" aria-expanded="true">
      <span class="ric" aria-hidden="true"><svg class="reason-caret" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18l6-6-6-6"/></svg></span>
      <span class="reason-title">正在思考中…</span>
    </div>
    <div class="reason-body"></div>`;
  const toggle = () => {
    const open = el.classList.toggle('open');
    el.querySelector('.reason-head').setAttribute('aria-expanded', String(open));
    // 记下用户动过：思考结束时不该把用户的选择覆盖掉
    el.dataset.touched = '1';
    // 收起时打一个标记 —— 思考中「默认展开」的规则靠它让路，
    // 否则用户点了收起，思考内容还会被 CSS 顶回来。
    if (open) delete el.dataset.collapsed;
    else el.dataset.collapsed = '1';
  };
  const head = el.querySelector('.reason-head');
  head.addEventListener('click', toggle);
  head.addEventListener('keydown', e => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
  });
  // 挂在消息流末尾。助手正文是之后才创建的（addMsg 会追加到末尾），
  // 所以面板自然落在正文的上方 —— 顺序是「思考 → 回答」。
  //
  // 每轮思考都是**独立一块**面板：续接对话时第二轮不能覆盖第一轮的思考。
  const host = $('#messages');
  host.appendChild(el);
  S_REASON = el;
  return el;
}

function reasonStart() {
  const el = reasonPanel();
  el.classList.add('live', 'open');
  el.querySelector('.reason-title').textContent = '正在思考中…';
  el.querySelector('.reason-body').textContent = '';
  $('#scroll').scrollTop = 1e9;
}

function reasonDelta(delta) {
  if (!delta || !S_REASON || !document.body.contains(S_REASON)) return;
  const body = S_REASON.querySelector('.reason-body');
  body.textContent += delta;
  body.scrollTop = body.scrollHeight;
  $('#scroll').scrollTop = 1e9;
}

function reasonEnd() {
  if (!S_REASON || !document.body.contains(S_REASON)) { S_REASON = null; return; }
  S_REASON.classList.remove('live');
  // 清掉「思考中强制展开」的让路标记 —— 现在由 open 类决定展开与否
  delete S_REASON.dataset.collapsed;
  // 用户手动展开过就保持展开 —— 跟 Marvis 一致：
  // 它的 useState(status !== 'completed') 只决定初始值，
  // 后续用户点开的状态不会被"思考完成"覆盖掉。
  if (S_REASON.dataset.touched !== '1') {
    S_REASON.classList.remove('open');
    S_REASON.querySelector('.reason-head').setAttribute('aria-expanded', 'false');
  }
  S_REASON.querySelector('.reason-title').textContent = '已思考完成';
}

/* ══════ 多 Agent 对话流 ══════
   管家之间、或管家与 Oryx 之间的往来，都在这条时间线里显示。

   之前这些消息只躺在团队面板的弹层里（用户得手动打开才看得到），
   `team.member.done` 明明带着子 Agent 的完整结论，前端却把它丢了 ——
   界面上只剩一个"执行中"的转圈，跑完了也不知道说了什么。 */
S.tl = [];       // [{agent, name, icon, kind:'start'|'done'|'poke', text, ts}]

/** 取（或建）某个 Agent 在时间线里的段。 */
function tlSection(agent, name, icon) {
  let sec = S.tl.find(s => s.agent === agent);
  if (!sec) {
    sec = { agent, name, icon, items: [] };
    S.tl.push(sec);
  }
  return sec;
}

/** 时间线容器（在消息流里）。 */
function tlBox() {
  let el = $('#tl');
  if (!el) {
    el = document.createElement('div');
    el.id = 'tl';
    el.className = 'tl';
    $('#messages').appendChild(el);
  }
  return el;
}

/** 重画整条时间线。 */
function tlPaint() {
  const box = tlBox();
  box.innerHTML = S.tl.map(sec => `
    <div class="tlsec">
      <div class="tlhead">
        <span class="av">${esc(sec.icon || sec.name.slice(0, 1))}</span>
        <span class="nm">${esc(sec.name)}</span>
        ${sec.busy ? '<span class="dots"><i></i><i></i><i></i></span>' : ''}
      </div>
      ${sec.items.map(it => {
        if (it.kind === 'poke') return `<div class="tlpoke">${esc(it.text)}</div>`;
        return `<div class="tlmsg ${it.who === 'main' ? 'me' : ''} ${it.empty ? 'dim' : ''}">${it.tag ? `<span class="tag">${esc(it.tag)}</span>` : ''}${esc(it.text)}</div>`;
      }).join('')}
    </div>`).join('');
  $('#scroll').scrollTop = 1e9;
}

/** 管家被派活了。 */
function tlAgentStarted(ev) {
  const name = ev.agent_name || ev.agent || '';
  const sec = tlSection(ev.agent, name, ev.agent_icon || '');
  sec.busy = true;
  sec.items.push({ kind: 'poke', text: `Oryx 派活给 ${name}：${(ev.goal || '').slice(0, 60)}${(ev.goal || '').length > 60 ? '…' : ''}` });
  tlPaint();
}

/** 子 Agent 开跑（team_dispatch 之外的路径，比如 spawn_agent）。 */
function tlSubStarted(ev) {
  const name = ev.agent_name || ev.agent || '';
  const sec = tlSection(ev.agent, name, ev.agent_icon || '');
  sec.busy = true;
  sec.items.push({ kind: 'poke', text: `${name} 开始执行` });
  tlPaint();
}

/** 子 Agent 跑完了 —— 结论要显示出来。 */
function tlSubFinished(ev) {
  const name = ev.agent_name || ev.agent || '';
  const sec = tlSection(ev.agent, name, ev.agent_icon || '');
  sec.busy = false;
  const text = (ev.text || '').trim();
  if (text) sec.items.push({ who: 'agent', tag: ev.empty ? '没有产出' : '', text, empty: !!ev.empty });
  tlPaint();
}

/** 团队成员跑完了。team.member.done 带着完整结论 ——
    之前这个 text 被整个丢掉，界面上只剩一个转圈。 */
function tlMemberDone(ev) {
  const name = ev.agent_name || ev.agent || '';
  const sec = tlSection(ev.agent, name, ev.agent_icon || '');
  sec.busy = false;
  const text = (ev.text || '').trim();
  if (text) {
    const empty = !!ev.empty || text === '（子 Agent 没有产出文本）';
    sec.items.push({ who: 'agent', tag: ev.ok === false ? '失败' : (empty ? '没有产出' : ''), text, empty });
  }
  tlPaint();
}

/* ══════ 多 Agent 一屏总览 + 运行中主动建议 ══════
   事件本来就在 SSE 流里，缺的是「聚合成一屏能看懂的状态」。
   这里旁路聚合，不改各 case 原有的渲染逻辑 —— 新增事件只要加进
   OV_EVENTS 就能进总览，不会出现两套状态各说各话。 */
const OV = {
  agents: new Map(), stages: new Map(), steps: [], progress: null,
  workflow: null, startedAt: 0,
};
let SUG = [];
const OV_ST_LABEL = { running: '进行中', done: '已完成', failed: '失败', waiting: '等待中' };
const OV_EVENTS = new Set([
  'RUN_STARTED', 'step.created', 'step.done', 'step.failed', 'step.denied',
  'run.progress',
  'workflow.started', 'workflow.stage.started', 'workflow.stage.finished',
  'workflow.step.started', 'workflow.step.finished', 'workflow.finished',
  'team.member.started', 'team.member.done',
  'subagent.started', 'subagent.finished',
]);

function ovReset() {
  OV.agents.clear(); OV.stages.clear(); OV.steps = [];
  OV.progress = null; OV.workflow = null; OV.startedAt = Date.now();
}

function ovOn(ev) {
  const t = ev.type;
  if (t === 'RUN_STARTED') { ovReset(); renderOverview(); return; }
  if (!OV.startedAt) OV.startedAt = Date.now();

  if (t === 'workflow.started') {
    OV.workflow = { name: ev.goal || '工作流', total: Number(ev.total_steps) || 0, status: 'running' };
    (ev.stages || []).forEach(s => {
      const name = String((s && s.name) || s || '阶段');
      OV.stages.set(name, { name, status: 'waiting', steps: Number((s && s.steps) || 0) });
    });
  } else if (t === 'workflow.stage.started') {
    const name = String(ev.stage || '阶段');
    OV.stages.set(name, { name, status: 'running', steps: Number(ev.steps) || 0 });
  } else if (t === 'workflow.stage.finished') {
    const name = String(ev.stage || '阶段');
    const cur = OV.stages.get(name) || { name, steps: 0 };
    OV.stages.set(name, { ...cur, status: 'done' });
  } else if (t === 'workflow.step.started' || t === 'workflow.step.finished') {
    const key = String(ev.step_id || ev.agent || ev.step || 'step');
    const done = t === 'workflow.step.finished';
    const status = done ? (ev.status === 'failed' ? 'failed' : 'done') : 'running';
    OV.agents.set(key, {
      name: String(ev.step || ev.agent || key),
      agent: String(ev.agent || ''),
      stage: String(ev.stage || ''),
      status,
    });
  } else if (t === 'workflow.finished') {
    if (OV.workflow) OV.workflow.status = ev.status || 'done';
  } else if (t === 'team.member.started' || t === 'subagent.started') {
    const key = String(ev.agent || ev.agent_name || 'agent');
    OV.agents.set(key, { name: String(ev.agent_name || ev.agent || key),
      agent: String(ev.agent || ''), stage: String(ev.team || ''), status: 'running' });
  } else if (t === 'team.member.done' || t === 'subagent.finished') {
    const key = String(ev.agent || ev.agent_name || 'agent');
    const cur = OV.agents.get(key) || { name: key, agent: key, stage: '' };
    OV.agents.set(key, { ...cur, status: 'done' });
  } else if (t === 'step.created') {
    OV.steps.push({ tool: String(ev.tool || '工具'), status: 'running', at: Date.now() });
    if (OV.steps.length > 60) OV.steps.shift();
  } else if (t === 'step.done' || t === 'step.failed' || t === 'step.denied') {
    const status = t === 'step.done' ? 'done' : 'failed';
    const tool = String(ev.tool || '');
    for (let i = OV.steps.length - 1; i >= 0; i--) {
      if (OV.steps[i].tool === tool && OV.steps[i].status === 'running') {
        OV.steps[i].status = status; break;
      }
    }
  } else if (t === 'run.progress') {
    OV.progress = ev;
  }
  renderOverview();
}

function renderOverview() {
  const p = $('#p-overview');
  if (!p) return;
  const agents = [...OV.agents.values()];
  const stages = [...OV.stages.values()];
  const steps = OV.steps.slice(-8).reverse();
  if (!agents.length && !stages.length && !steps.length && !OV.progress && !OV.workflow) {
    p.innerHTML = '<div class="empty">还没有并行任务<br><span style="font-size:11.5px;opacity:.7">'
      + '跑工作流、组队派活或执行多步任务时，这里会显示一屏总览</span></div>';
    return;
  }
  const done = agents.filter(a => a.status === 'done').length
    + OV.steps.filter(s => s.status === 'done').length;
  const total = agents.length + OV.steps.length;
  const pct = total ? Math.round(done / total * 100) : 0;
  const secs = OV.startedAt ? Math.max(0, Math.round((Date.now() - OV.startedAt) / 1000)) : 0;
  const prog = OV.progress || {};
  const stageNames = stages.length ? stages : [];
  const current = stageNames.find(s => s.status === 'running');
  const head = `
    <div class="ovhead">
      <div class="big">${esc(OV.workflow ? OV.workflow.name : (current ? current.name : '本轮任务'))}</div>
      <div class="sub">${OV.workflow ? '工作流编排' : (current ? '当前阶段' : '执行中')} · 已运行 ${secs}s</div>
      <div class="ovstats">
        <span class="ovstat"><b>${done}</b>/ ${total || 0} 已完成</span>
        <span class="ovstat"><b>${prog.steps ?? OV.steps.length}</b>执行步骤</span>
        <span class="ovstat"><b>${prog.artifacts ?? 0}</b>产物</span>
      </div>
      <div class="ovbar"><i style="width:${pct}%"></i></div>
    </div>`;
  const agentHtml = agents.length ? `
    <div class="ovsect">成员 / 子 Agent（${agents.length}）</div>
    <div class="ovgrid">${agents.map(a => `
      <div class="ovcard"><div class="nm" title="${esc(a.name)}">${esc(a.name)}</div>
        <div class="ag">${esc(a.agent || a.stage || '子任务')}</div>
        <span class="ovst ${esc(a.status)}">${OV_ST_LABEL[a.status] || a.status}</span>
      </div>`).join('')}</div>` : '';
  const stageHtml = stageNames.length ? `
    <div class="ovsect">阶段</div>
    ${stageNames.map(s => `<div class="ovrow"><span class="dot ${esc(s.status)}"></span>
      <span class="tx">${esc(s.name)}</span>
      <span class="tm">${s.steps ? s.steps + ' 步 · ' : ''}${OV_ST_LABEL[s.status] || s.status}</span></div>`).join('')}` : '';
  const stepHtml = steps.length ? `
    <div class="ovsect">最近活动</div>
    ${steps.map(s => `<div class="ovrow"><span class="dot ${esc(s.status)}"></span>
      <span class="tx">${esc(s.tool)}</span>
      <span class="tm">${OV_ST_LABEL[s.status] || s.status}</span></div>`).join('')}` : '';
  p.innerHTML = head + agentHtml + stageHtml + stepHtml;
}

/** 主动建议：只替换上一组卡，不在对话流里越堆越多。 */
function renderSuggestions(list, phase) {
  const box = $('#messages');
  if (!box) return;
  $$('#messages .sugbox').forEach(el => el.remove());
  SUG = Array.isArray(list) ? list : [];
  if (!SUG.length) return;
  const el = document.createElement('div');
  el.className = 'sugbox';
  el.innerHTML = `<div class="sugh">下一步建议<span>${phase === 'final'
    ? '本轮已完成，可以继续加工' : '根据刚完成的工作'}</span></div>
    <div class="suglist">${SUG.map((s, i) =>
      `<button class="sug" data-sug="${i}" type="button">${esc(s.label || '继续')}</button>`).join('')}</div>`;
  box.appendChild(el);
  $('#scroll').scrollTop = 1e9;
}

(function bindSuggestionClicks() {
  const box = document.getElementById('messages');
  if (!box) return;
  box.addEventListener('click', e => {
    const b = e.target.closest('[data-sug]');
    if (!b) return;
    const s = SUG[Number(b.dataset.sug)];
    if (!s) return;
    if (s.action === 'preview' && s.path) {
      const url = '/api/files/preview?t=' + encodeURIComponent(ORYX_TOKEN)
        + '&path=' + encodeURIComponent(s.path);
      if (!window.open(url, '_blank', 'noopener')) toast('浏览器拦截了新窗口，请允许弹窗后重试');
      return;
    }
    const input = $('#input');
    if (input) { input.value = s.prompt || ''; autoGrow(); input.focus(); }
    if (S.busy) { toast('任务还在跑，建议已放进输入框'); return; }
    send();
  });
})();

/* ══════ 能力降级可见化 ══════
   向量检索退回关键词、OCR 引擎缺失，以前都是静默发生的：
   用户以为自己在用语义检索，结果差了也不知道原因。
   这里把 capability.degraded 事件变成一条可关闭的常驻提示。 */
const DEGRADED = new Map();
function renderDegraded() {
  const box = $('#degradeBar');
  if (!box) return;
  box.innerHTML = [...DEGRADED.values()].map(d => `
    <div class="degrade"><span class="ic">!</span>
      <div class="tx"><b>${esc(d.capability)}</b>：${esc(d.reason)}
        ${d.action ? `<span class="ac">建议：${esc(d.action)}</span>` : ''}</div>
      <button type="button" data-degclose="${esc(d.key)}" title="知道了" aria-label="忽略提示">×</button>
    </div>`).join('');
}
(function bindDegradeClose() {
  const box = document.getElementById('degradeBar');
  if (!box) return;
  box.addEventListener('click', e => {
    const b = e.target.closest('[data-degclose]');
    if (!b) return;
    DEGRADED.delete(b.dataset.degclose);
    renderDegraded();
  });
})();

/* ══════ AG-UI 事件处理 ══════
   后端已升级为标准事件族（seq 单调 + trace_id），这里做两件事：
   1) 把 AG-UI 事件翻译成界面动作
   2) 维护 lastSeq，重连时带 ?since= 让后端补发断连期间的事件 */
let lastSeq = 0;
let es = null;
let esRetry = 0;

/* 只影响「当前对话区内容」的事件 —— 这些必须按 task_id 过滤。
   团队、工作流、终端这些是全局的，不归某个任务管，不能过滤
   （过滤了团队时间线就不更新了）。
   判据是事件**会不会往对话区/步骤区写东西**。 */
const SCOPED_EVENTS = new Set([
  'REASONING_START', 'REASONING_CONTENT', 'REASONING_END',
  'TEXT_MESSAGE_START', 'TEXT_MESSAGE_CONTENT', 'TEXT_MESSAGE_END',
  'TOOL_CALL_START', 'TOOL_CALL_ARGS', 'TOOL_CALL_END', 'TOOL_CALL_RESULT',
  'step.created', 'step.awaiting_approval', 'step.running',
  'step.done', 'step.failed', 'step.denied', 'step.autoclass',
  'todos.updated', 'hook.metrics',
  'run.suggestion', 'run.progress',
  'RUN_CANCELLING',
]);

/* 终止类事件：一轮跑完了 / 失败了 / 被停掉了。
   这几个会改发送键状态、清思考面板 —— 别的任务发来的不能改当前界面。 */
const TERMINAL_EVENTS = new Set(['RUN_FINISHED', 'RUN_ERROR', 'RUN_CANCELLED']);

/** 这个事件是不是属于别的任务（不该进当前界面）。 */
function isForeign(ev) {
  const t = ev.type;
  if (SCOPED_EVENTS.has(t) || TERMINAL_EVENTS.has(t)) {
    // 没有 task_id 的事件无从判断 —— 放行，别误伤
    if (!ev.task_id) return false;
    // 界面还没认领任何任务（新会话刚开）：任何任务的事件都不该进来
    if (!S.taskId) return true;
    return ev.task_id !== S.taskId;
  }
  return false;
}

function handle(ev) {
  // 控制帧不带业务语义，且 seq 等于当前水位，必须放行后再做单调校验
  if (ev.type === 'ready' || ev.type === 'resync') {
    $('#live').classList.add('on');
    if (ev.type === 'resync') {
      // 缓冲已滚过 lastSeq，补不齐了：整页刷新比错序渲染安全
      toast('连接中断，正在重新同步');
      refreshState().then(loadSessions);
    }
    return;
  }
  // 乱序保护：seq 倒退说明补发与实时流撞车了，直接丢弃避免重复渲染
  if (typeof ev.seq === 'number') {
    if (ev.seq <= lastSeq) return;
    lastSeq = ev.seq;
  }
  // 别的任务的内容不往当前对话区写。
  // 用户点了「新建对话」，但后台那个任务还在跑 ——
  // 它的回答流进来，用户会看到一条跟自己无关的回答凭空冒出来。
  if (isForeign(ev)) return;
  // 旁路聚合到「总览」面板：不改下面各 case 的原有渲染逻辑
  if (OV_EVENTS.has(ev.type)) ovOn(ev);
  switch (ev.type) {
    case 'RUN_STARTED':
      // 新任务开跑，界面认领它
      S.taskId = ev.task_id;
      S.current = ev.task_id; S.assistant = null;
      $('#pageTitle').textContent = (ev.goal || '').slice(0, 30);
      showThinking('正在思考');
      // 这一轮刚打了一个检查点 —— 拉到本地，
      // 「回退到这里」按钮才知道自己对应哪个点。
      // 事件里直接带了 checkpoint id，不用再查一遍。
      //
      // msg_count 要跟库里一致：检查点打在**这条提问之前**，
      // 所以它的 msg_count = 这条提问在消息序列里的序号 = _msgSeq - 1
      // （addMsg 已经把序号占掉了）。
      if (ev.checkpoint) {
        S.checkpoints = (S.checkpoints || []).filter(c => c.id !== ev.checkpoint);
        S.checkpoints.push({
          id: ev.checkpoint,
          seq: S.checkpoints.length,
          label: (ev.goal || '').slice(0, 60),
          files: 0,
          msg_count: _msgSeq - 1,
          current: true,
        });
      }
      break;
    // 思考过程：独立于正文的通道（对齐 Marvis）
    case 'REASONING_START':
      hideThinking();
      reasonStart();
      break;
    case 'REASONING_CONTENT':
      reasonDelta(ev.delta || '');
      break;
    case 'REASONING_END':
      reasonEnd();
      break;
    case 'TEXT_MESSAGE_START':
      S.assistant = null; S.streaming = true;
      hideThinking();
      break;
    case 'TEXT_MESSAGE_CONTENT': {
      if (!S.assistant) {
        S.assistant = addMsg('bot', '');
        // 状态行要进 .body 这一列（气泡上方），不能塞进 flex 行里 ——
        // 之前插到 .msg 里，跑到头像和正文中间去了
        S.assistant.parentElement.prepend(agentLine('Oryx', '', ''));
      }
      S.assistant.textContent += ev.delta || '';
      $('#scroll').scrollTop = 1e9;
      break;
    }
    case 'TEXT_MESSAGE_END':
      // 流式期间用 textContent 追加（快），完成后整体渲染一次 markdown
      if (S.assistant) S.assistant.innerHTML = md(S.assistant.textContent);
      S.assistant = null; S.streaming = false;
      break;
    case 'TOOL_CALL_START':
      hideThinking();
      break;
    case 'step.created': setThinking('正在执行：' + (ev.tool || '')); stepCard(ev); break;
    case 'step.autoclass': { markAutoclass(ev); break; }
    case 'step.awaiting_approval': { hideThinking(); const c = stepCard(ev); setBadge(c, 'run', '等待确认'); showPlan(ev); break; }
    case 'step.running': { const c = stepCard(ev); setBadge(c, 'run', '执行中'); break; }
    case 'step.done': {
      hideThinking();
      const c = stepCard(ev); setBadge(c, 'done', '完成');
      markProof(c, ev);
      if (ev.result != null) c.querySelector('.r').textContent = typeof ev.result === 'string' ? ev.result.slice(0, 600) : JSON.stringify(ev.result).slice(0, 600);
      // 产物路径主判定来自后端 artifact_path（按「真的写了字节」的结果元数据），
      // 新增落盘工具不用改这里。FILE_TOOLS 只给老工具做兜底 —— 之前
      // step.done 根本不带 args，ev.args?.path 永远取不到，产物面板实时收集是断的。
      if (ev.artifact_path || FILE_TOOLS.has(ev.tool)) {
        const path = ev.artifact_path || ev.args?.path || ev.args?.image_path
          || ev.args?.output || ev.args?.file || ev.args?.src || ev.args?.dest;
        if (path) {
          const key = String(path);
          if (!S.artifacts.some(x => x.path === key)) {
            S.artifacts.unshift({ tool: ev.tool, path: key });
            S.artifacts = S.artifacts.slice(0, 50);
            renderFiles();
          }
        }
      }
      break;
    }
    case 'step.failed': {
      hideThinking();
      const c = stepCard(ev);
      setBadge(c, 'failed', '失败');
      // 失败分类（E1-E5）要露出来 —— 笼统的「失败」用户看不出该做什么
      const cls = ev.error_class;
      if (cls) {
        const tag = document.createElement('span');
        tag.className = 'ecls';
        tag.textContent = cls;
        tag.title = ERR_CLASS_HINT[cls] || '';
        c.querySelector('.t')?.appendChild(tag);
      }
      c.querySelector('.r').textContent = ev.error || '';
      break;
    }
    case 'step.denied': { hideThinking(); const c = stepCard(ev); setBadge(c, 'denied', '已取消'); c.querySelector('.r').textContent = ev.reason || ''; break; }
    case 'hook.metrics': markElapsed(ev); break;
    case 'todos.updated': renderTodos(ev.todos); break;
    // 主动性：模型跑的过程中就给可点的下一步，而不是跑完才留一句「还需要什么」
    case 'run.suggestion': renderSuggestions(ev.suggestions, ev.phase); break;
    case 'run.progress': break;   // 总览已在 ovOn 里更新，这里不重复渲染
    case 'team.created': case 'team.updated': case 'team.message':
    case 'team.deleted':
      loadTeams();
      if (ev.type === 'team.deleted') toast('团队已解散');
      break;
    case 'team.member.done':
      loadTeams();
      // 成员跑完了 —— 结论要落到时间线上，不能只在弹层里躺着
      tlMemberDone(ev);
      break;
    case 'team.member.started': tlAgentStarted(ev); break;
    case 'subagent.started': tlSubStarted(ev); break;
    case 'subagent.finished': tlSubFinished(ev); break;
    case 'workflow.started': wfOnStarted(ev); break;
    case 'workflow.stage.started': case 'workflow.stage.finished': wfOnStage(ev); break;
    case 'workflow.step.started': case 'workflow.step.finished': wfOnStep(ev); break;
    case 'workflow.finished': wfOnFinished(ev); break;
    case 'workflow.cancelling': toast('正在中止工作流'); break;
    case 'term.event': {
      const o = $('#termOut');
      // 有输出了就把空状态引导收起来
      const empty = $('#termEmpty');
      if (empty) empty.remove();
      if (ev.t === 'term.started') { S.term = ev.id; o.textContent = `$ ${ev.command}\n`; }
      else if (ev.t === 'term.output') { o.textContent += ev.line + '\n'; o.scrollTop = o.scrollHeight; }
      else if (ev.t === 'term.finished') { o.textContent += `\n[退出 ${ev.exit_code}]\n`; o.scrollTop = o.scrollHeight; }
      break;
    }
    case 'RUN_FINISHED':
      hideThinking();
      S.busy = false; S.busySince = 0; disarmBusyTimeout();
      setSendState('idle'); S.assistant = null; S.streaming = false;
      // 检查点要重拉 —— 这一轮改了几个文件只有跑完才知道
      loadCheckpoints(S.current);
      loadSessions(); refreshUsage(); renderDetail();
      break;
    case 'RUN_ERROR':
      hideThinking();
      // 内核兜底发的 RUN_ERROR 不带 task_id —— 那说明是内核在准备阶段就炸了
      //（比如模型没配好），跟界面盯着的是不是同一个任务无从得知。
      // 只有界面正盯着某个任务时才提示，否则用户点了「新建对话」之后，
      // 旧任务的报错会凭空插进新会话里。
      if (ev.task_id || S.taskId) addMsg('bot', '任务失败：' + (ev.error || '未知错误'));
      S.busy = false; S.busySince = 0; disarmBusyTimeout();
      setSendState('idle'); S.assistant = null; S.streaming = false;
      break;
    case 'RUN_CANCELLED':
      hideThinking();
      // 被用户停掉的：说明一下，别让人以为出了错
      addMsg('bot', '（已停止）');
      S.busy = false; S.busySince = 0; disarmBusyTimeout();
      setSendState('idle'); S.assistant = null; S.streaming = false;
      loadSessions(); renderDetail();
      break;
    case 'RUN_CANCELLING':
      toast('正在停止…');
      break;
    // 断点续跑 / 上下文裁剪 / 内核自愈：以前这些状态只在后端流转，
    // 用户完全看不到，任务「自己从头开始」还以为界面坏了。
    case 'run.resumed':
      toast(`内核已恢复：跳过 ${ev.completed || 0} 个已完成步骤，从断点继续`);
      break;
    case 'context.trimmed':
      toast(`长会话已压缩：保留 ${ev.after}/${ev.before} 条历史（预算 ${ev.budget} tokens）`);
      break;
    case 'agent.kernel.health':
      toast(ev.detail || '内核状态变化');
      break;
    case 'model.routed':
      // 运行时换模型必须可见：静默切模型会让用户以为答复风格变了是幻觉
      if (ev.switched) toast(`本轮改用 ${ev.name}：${String(ev.reason || '').slice(0, 70)}`);
      break;
    case 'capability.degraded': {
      const key = (ev.capability || '能力') + '|' + (ev.reason || '');
      if (!DEGRADED.has(key)) {
        DEGRADED.set(key, { key, capability: ev.capability || '能力',
          reason: ev.reason || '', action: ev.action || '' });
        renderDegraded();
        toast(`${ev.capability || '能力'}已降级：${String(ev.reason || '').slice(0, 50)}`);
      }
      break;
    }
  }
}

function connect() {
  if (es) { try { es.close(); } catch (_) {} }
  // 首连不带 since；重连带上最后一个 seq，后端据此补发断连期间的事件
  const q = new URLSearchParams({ t: ORYX_TOKEN });
  if (lastSeq > 0) q.set('since', String(lastSeq));
  es = new EventSource('/api/events?' + q.toString());
  es.onopen = () => { esRetry = 0; $('#live').classList.add('on'); };
  es.onerror = () => {
    $('#live').classList.remove('on');
    // EventSource 自带重连，但它不会带 since；这里自己接管，退避重连才能补发
    try { es.close(); } catch (_) {}
    es = null;
    esRetry = Math.min(esRetry + 1, 6);
    setTimeout(connect, 500 * esRetry);
  };
  es.onmessage = e => { try { handle(JSON.parse(e.data)); } catch (_) {} };
}

/* ══════ 全局兜底 ══════ */

/**
 * 未捕获的异常与 Promise 拒绝，**必须让用户看见**。
 *
 * 以前这里什么都没有 —— 任何没被 catch 的 await（人设切换、风格切换、
 * 模型切换…）都只在 DevTools 控制台里冒一下，界面上毫无反应。
 * 用户看到的就是「点了没反应」，然后以为按钮坏了。
 *
 * 这类问题最阴的地方在于：它不报错，只是**什么都不发生**。
 */
window.addEventListener('unhandledrejection', (e) => {
  const msg = (e.reason && (e.reason.message || e.reason.detail)) || String(e.reason || '未知错误');
  console.error('[oryx] 未处理的 Promise 拒绝：', e.reason);
  toast('操作失败：' + msg);
  // 失败后要把忙碌态放掉 —— 否则发送键永远停在「停止」，用户彻底卡死
  releaseBusyOnFailure();
});

window.addEventListener('error', (e) => {
  console.error('[oryx] 未捕获异常：', e.error || e.message);
  // 只提示「脚本错误」这一类 —— 资源加载失败不该弹提示
  if (e.message && e.message !== 'Script error.') {
    toast('出了点问题：' + e.message);
  }
  releaseBusyOnFailure();
});

/**
 * 失败后释放忙碌态。
 *
 * 之前的问题是：任何一条异常路径都可能让 S.busy 停在 true，
 * 于是发送键永远显示成红色的「停止」，用户点它只会得到
 * 「没有正在跑的任务」，然后界面彻底卡住。
 */
function releaseBusyOnFailure() {
  if (!S.busy) return;
  // 给一点缓冲：正在跑的任务马上会自己发 RUN_FINISHED，
  // 那时候不需要再清一次（清了也无害，但要避免误清刚启动的任务）。
  if (S.busySince && Date.now() - S.busySince < 1500) return;
  S.busy = false;
  setSendState('idle');
  S.assistant = null;
  S.streaming = false;
  hideThinking();
  const el = $('#p-steps');
  if (el && el.querySelector('.empty') && el.textContent.includes('准备中')) {
    el.innerHTML = '<div class="empty">执行中断了 —— 可以重试</div>';
  }
}

/**
 * 忙碌态超时兜底。
 *
 * 没有它的话：SSE 断线且漏掉 RUN_FINISHED 时，S.busy 永远停在 true，
 * 界面永久卡在「停止」态，用户再也发不了消息。
 */
const BUSY_TIMEOUT_MS = 30 * 60 * 1000;
function armBusyTimeout() {
  if (S.busyTimer) clearTimeout(S.busyTimer);
  S.busyTimer = setTimeout(() => {
    if (!S.busy) return;
    S.busy = false;
    setSendState('idle');
    S.assistant = null;
    S.streaming = false;
    hideThinking();
    toast('这一轮耗时太久，已经不显示进度了 —— 可以在会话列表里查看结果');
  }, BUSY_TIMEOUT_MS);
}
function disarmBusyTimeout() {
  if (S.busyTimer) { clearTimeout(S.busyTimer); S.busyTimer = null; }
}

/* ══════ 确认卡 ══════ */
function showPlan(ev) {
  const stepId = ev.step_id;              // 每张卡自己持有 step_id，不再用全局变量
  const p = ev.plan || {};
  const el = document.createElement('div');
  el.className = 'plan';
  // 步骤清单（人类可读）优先；没有 steps 时退回 actions。
  // impact 决定徽章颜色：不可撤销是红的，可撤销是绿的。
  const steps = (p.steps && p.steps.length)
    ? p.steps.map(s => {
        const impact = s.impact || '';
        const safe = /可撤销|取决于/.test(impact) && !/不可撤销/.test(impact);
        return `<div class="act">
          <div class="k">${esc(s.title || '')}</div>
          <div class="t">${esc(s.detail || '')}</div>
          ${impact ? `<div class="i${safe ? ' safe' : ''}"><b>${esc(impact)}</b></div>` : ''}
        </div>`;
      }).join('')
    : (p.actions || []).map(a => `<div class="act"><div class="k">${esc(a.kind)}</div><div class="t">${esc(a.target)}</div><div class="d">${esc(a.detail || '')}</div></div>`).join('');
  const revNote = p.reversible === false
    ? `<div class="s" style="margin-bottom:8px">⚠ 这个操作<b>不可撤销</b>。</div>` : '';
  el.innerHTML = `<div class="h">⚠ 需要你确认</div>
    <div class="s">${esc(p.summary || '')}</div>
    ${revNote}
    ${steps}
    <div class="btns"><button class="btn" data-no type="button">取消</button><button class="btn primary" data-yes type="button">确认执行</button></div>`;
  $('#messages').appendChild(el);
  const done = async ok => {
    // 点过就禁用，防止重复提交同一个 step
    el.querySelectorAll('.btns button').forEach(b => { b.disabled = true; b.style.opacity = '.5'; });
    el.querySelector('.h').textContent = ok ? '✓ 已确认' : '已取消';
    await api('/api/approve', { method: 'POST', body: JSON.stringify({ step_id: stepId, approved: ok }) });
  };
  el.querySelector('[data-no]').addEventListener('click', () => done(false));
  el.querySelector('[data-yes]').addEventListener('click', () => done(true));
  $('#scroll').scrollTop = 1e9;
}

/* ══════ 顶栏交互 ══════ */

/**
 * 切换交互模式。
 *
 * 抽出来是因为窄屏抽屉也要用 —— 以前抽屉只能"看"不能"改"，
 * 用户在窄屏下切换模式后抽屉里的状态不变，像是没生效。
 */
async function setInteraction(mode) {
  const r = await api('/api/interaction', { method: 'POST', body: JSON.stringify({ interaction: mode }) });
  if (!r.ok) {
    const x = await r.json().catch(() => ({}));
    toast('切换失败：' + (x.detail || r.status));
    return false;
  }
  S.interaction = mode;
  $$('#interSeg button').forEach(b => b.classList.toggle('on', b.dataset.i === mode));
  paintModeHint();
  await refreshState();
  return true;
}

/** 切换人设。 */
async function setExpert(id) {
  const r = await api('/api/expert', { method: 'POST', body: JSON.stringify({ expert: id }) });
  if (!r.ok) {
    const x = await r.json().catch(() => ({}));
    toast('切换失败：' + (x.detail || r.status));
    return false;
  }
  await refreshState();
  toast(id ? '已切换人设' : '已清除人设');
  return true;
}

/** 切换回答风格。 */
async function setStyle(name) {
  const r = await api('/api/style', { method: 'POST', body: JSON.stringify({ style: name }) });
  if (!r.ok) {
    const x = await r.json().catch(() => ({}));
    toast('切换失败：' + (x.detail || r.status));
    return false;
  }
  await refreshState();
  return true;
}

/** 切换计算模式（效率 / 本地）。 */
async function setComputeMode(mode) {
  const r = await api('/api/mode', { method: 'POST', body: JSON.stringify({ mode }) });
  if (!r.ok) {
    const x = await r.json().catch(() => ({}));
    toast('切换失败：' + (x.detail || r.status));
    return false;
  }
  // 切模式会换掉当前 provider —— 芯片要立刻跟上，
  // 不刷新的话顶栏还显示着上一个模式的模型，看起来像没切。
  await refreshState();
  toast(mode === 'local' ? '已切到本地模式' : '已切到效率模式');
  return true;
}

$$('#interSeg button').forEach(b => b.addEventListener('click', async () => {
  await setInteraction(b.dataset.i);
}));
$('#expertSel').addEventListener('change', async e => {
  await setExpert(e.target.value);
});
$('#styleSel').addEventListener('change', async e => {
  await setStyle(e.target.value);
});
$('#modeSel').addEventListener('change', async e => {
  await setComputeMode(e.target.value);
});

/**
 * 顶栏设置抽屉 —— 窄屏时那些选择器会被隐藏（放不下），
 * 这个抽屉是它们的替代入口。
 *
 * 以前没有它：窄屏（<620px）下模式、风格、人设、模型**全部无法切换**，
 * 而且设置页里也没有这几个的完整入口 —— 用户在这个宽度下就是残废的。
 */
$('#topMoreBtn').addEventListener('click', async () => {
  const s = await api('/api/state').then(r => r.json()).catch(() => ({}));
  const pv = await api('/api/providers').then(r => r.json()).catch(() => ({ items: [], active: '' }));
  const experts = s.experts || [];
  const styles = s.styles || [];
  const models = pv.items || [];
  const curExpert = s.expert || '';
  const curStyle = s.style || 'professional';
  const curMode = s.mode || 'efficiency';

  openModal({
    title: '对话设置',
    body: `<div class="topdrawer">
      <div class="fld"><label>交互模式</label>
        <div class="seg" id="drMode">${[['ask','问答'],['plan','规划'],['craft','执行']]
          .map(([k,n]) => `<button data-v="${k}" class="${(s.interaction||'craft')===k?'on':''}" type="button">${n}</button>`).join('')}</div>
        <div class="hint">问答与规划只给只读工具；执行模式开放全部工具，危险动作仍会弹确认。</div>
      </div>
      <div class="fld"><label for="drExpert">人设 / 场景角色</label>
        <select class="inp" id="drExpert">
          <option value="">无专家</option>
          ${experts.map(x => `<option value="${escAttr(x.id)}" ${curExpert===x.id?'selected':''}>${esc(x.name)}</option>`).join('')}
        </select>
        <div class="hint">${curExpert ? '当前：' + esc((experts.find(x => x.id === curExpert) || {}).name || curExpert) : '未设置人设'}</div>
      </div>
      <div class="fld"><label for="drStyle">回答风格</label>
        <select class="inp" id="drStyle">
          ${styles.map(x => `<option value="${escAttr(x.name)}" ${curStyle===x.name?'selected':''}>${esc(x.label || x.name)}</option>`).join('')}
        </select>
      </div>
      <div class="fld"><label for="drModeSel">计算模式</label>
        <select class="inp" id="drModeSel">
          <option value="efficiency" ${curMode==='efficiency'?'selected':''}>效率模式（云端理解 + 本地索引）</option>
          <option value="local" ${curMode==='local'?'selected':''}>本地模式（全本地，可断网）</option>
        </select>
      </div>
      <div class="fld"><label for="drModel">模型服务</label>
        <select class="inp" id="drModel">
          ${models.map(x => `<option value="${escAttr(x.name)}" ${pv.active===x.name?'selected':''}>${esc(x.name)}${x.model?' · '+esc(x.model):''}</option>`).join('')}
        </select>
        <div class="hint">当前：${esc(pv.active || '（未设置）')}</div>
      </div>
    </div>`,
    foot: `<button class="btn" data-close type="button">关闭</button>`,
  });

  // 模式切换：立即生效，不用等「保存」
  $$('#drMode button').forEach(b => b.addEventListener('click', async () => {
    const ok = await setInteraction(b.dataset.v);
    if (!ok) return;
    $$('#drMode button').forEach(x => x.classList.toggle('on', x === b));
  }));
  const de = $('#drExpert');
  if (de) de.addEventListener('change', async e => { await setExpert(e.target.value); });
  const ds = $('#drStyle');
  if (ds) ds.addEventListener('change', async e => { await setStyle(e.target.value); });
  const dm = $('#drModeSel');
  if (dm) dm.addEventListener('change', async e => { await setComputeMode(e.target.value); });
  const dmo = $('#drModel');
  if (dmo) dmo.addEventListener('change', async e => {
    const r = await api(`/api/providers/${encodeURIComponent(e.target.value)}/activate`, { method: 'POST' });
    if (!r.ok) {
      const x = await r.json().catch(() => ({}));
      toast('切换失败：' + (x.detail || r.status));
      return;
    }
    await loadModelData();
    await refreshState();
    toast('已切换到 ' + e.target.value);
  });
});
$('#modelChip').addEventListener('click', async () => {
  const [pv, loc] = await Promise.all([
    api('/api/providers').then(r => r.json()),
    api('/api/providers/local').then(r => r.json()).catch(() => ({ models: [] })),
  ]);
  const items = pv.items || [];
  const localModels = loc.models || [];
  // 按计算模式分区：本地模式只列本机模型，效率模式只列云端模型。
  // 两个都列出来的话，用户在「本地模式」下点一个云端模型，
  // 切过去还是本地 provider —— 看起来就是"切换没生效"。
  const localMode = S.mode === 'local';
  const cloudItems = items.filter(it => !it.is_local);
  const localItems = items.filter(it => it.is_local);
  const shown = localMode ? localItems : cloudItems;

  openModal({
    title: localMode ? '切换本地模型' : '切换云端模型',
    body: shown.length ? `
      <div class="picklist">
      ${shown.map(it => `
        <button class="pick ${it.name === pv.active ? 'on' : ''}" data-pv="${esc(it.name)}" type="button">
          <span class="pk">${it.is_local ? '◇' : '◈'}</span>
          <span><span class="pt">${esc(it.name)} ${it.name === pv.active ? '<span class="badge done">使用中</span>' : ''}</span>
            <div class="pd">${esc(it.model || '')} · ${esc(it.type)} · ${Math.round((it.capabilities?.context_window || 0) / 1000)}k 上下文</div></span>
        </button>`).join('')}
      </div>
      ${localMode && localModels.length ? `
        <div class="sect" style="margin-top:18px"><h2>本机已装 · 点一下直接用</h2>
        <div class="picklist">
        ${localModels.map(m => `
          <button class="pick ${m.provider && m.provider === pv.active ? 'on' : ''}" data-lm="${esc(m.name)}" type="button">
            <span class="pk">◇</span>
            <span><span class="pt">${esc(m.name)}${m.provider && m.provider === pv.active ? ' <span class="badge done">使用中</span>' : ''}</span>
              <div class="pd">本地 · ${esc(m.parameter_size || m.family || '')} ${esc(m.quantization || '')} · ${(m.size / 1024 / 1024 / 1024).toFixed(1)} GB</div></span>
          </button>`).join('')}
        </div>
        <div class="hint" style="font-size:11.5px;color:var(--muted);margin-top:8px">选一个会自动登记成模型服务并切换过去。</div>
      </div>` : ''}
      <div class="hint" style="font-size:11.5px;color:var(--muted);margin-top:12px">
        ${localMode ? '当前是本地模式，只显示跑在本机的模型。' : '当前是效率模式，只显示云端模型。'}
        要换一类，去顶栏的「计算模式」切。
      </div>`
      : `<div class="empty">${localMode ? '还没有本机模型。去设置里添加，或先用 Ollama 装一个。' : '还没有云端模型服务。去设置里添加。'}</div>`,
    foot: `<button class="btn" data-close type="button">关闭</button>`,
  });
  $$('#modal [data-pv]').forEach(b => b.addEventListener('click', async () => {
    // 失败时**不要关弹窗** —— 以前不管成败都 closeModal()，
    // 切换失败时用户看到的是「弹窗消失了，什么也没变」，
    // 误以为已经切好了。失败要留在原地并说明原因。
    b.disabled = true;
    try {
      const r = await api(`/api/providers/${encodeURIComponent(b.dataset.pv)}/activate`, { method: 'POST' });
      if (!r.ok) {
        const x = await r.json().catch(() => ({}));
        toast('切换失败：' + (x.detail || r.status));
        b.disabled = false;
        return;
      }
      closeModal();
      await refreshState();
      toast('已切换到 ' + b.dataset.pv);
    } catch (e) {
      toast('切换失败：' + (e && e.message ? e.message : e));
      b.disabled = false;
    }
  }));
  // 本机已装模型：登记成 provider 后直接激活
  $$('#modal [data-lm]').forEach(b => b.addEventListener('click', async () => {
    const model = b.dataset.lm;
    b.disabled = true;
    try {
      // api() 返回的是 Response，要自己解 JSON —— 直接读 r.name 永远是 undefined
      const resp = await api('/api/providers/local', {
        method: 'POST',
        body: JSON.stringify({ model }),
      });
      const r = await resp.json().catch(() => ({}));
      if (!resp.ok || !r.name) {
        toast('登记失败：' + (r.detail || resp.status || '未知原因'));
        b.disabled = false;
        return;
      }
      // 登记成功了，但**激活**可能失败（密钥无效、配置冲突）。
      // 失败时别关弹窗 —— 用户要看到到底哪一步没成。
      const act = await api(`/api/providers/${encodeURIComponent(r.name)}/activate`, { method: 'POST' });
      if (!act.ok) {
        const x = await act.json().catch(() => ({}));
        toast('已登记，但切换失败：' + (x.detail || act.status));
        b.disabled = false;
        await loadModelData();
        await refreshState();
        return;
      }
      closeModal();
      await refreshState();
      toast('已切换到 ' + model);
    } catch (e) {
      toast('切换失败：' + e.message);
      b.disabled = false;
    }
  }));
});

/* ══════ 弹层基础设施 ══════ */
let _modalOpener = null;   // 打开弹层前聚焦的元素，关闭时还回去
/* 弹层代号。每开一次 +1。
   用来判断「现在这个弹层还是我开的那一个吗」——
   openModal 复用同一个 #modal 元素，只比元素身份没用（永远是同一个）。
   确认弹窗里 onOk 经常会再开一个弹窗（比如删完团队重开另一个团队的面板），
   那时就不能再 closeModal，否则把刚开的那个也关了。 */
let _modalGen = 0;
const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]):not([type="hidden"]),textarea:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])';
function openModal({ title, body, foot, wide }) {
  _modalOpener = document.activeElement;
  _modalGen += 1;
  const m = $('#modal');
  m.className = 'modal' + (wide ? ' wide' : '');
  m.setAttribute('role', 'dialog');
  m.setAttribute('aria-modal', 'true');
  m.setAttribute('aria-label', title || '对话框');
  m.innerHTML = `<div class="mhead"><h3>${esc(title)}</h3><button class="x" data-close aria-label="关闭对话框" type="button">✕</button></div>
    <div class="mbody">${body}</div>
    ${foot ? `<div class="mfoot">${foot}</div>` : ''}`;
  $('#mask').classList.add('on');
  // 焦点移入弹层：优先正文第一个可用控件，退化到关闭按钮
  // 注意要先进 .mbody 再查，否则逗号分隔的后续选择器会丢掉作用域限制
  const mbody = m.querySelector('.mbody');
  const first = (mbody && mbody.querySelector(FOCUSABLE)) || m.querySelector('[data-close]');
  if (first) first.focus();
  return m;
}
function closeModal() {
  if (!$('#mask').classList.contains('on')) return;
  $('#mask').classList.remove('on');
  $('#modal').innerHTML = '';
  $('#modal').removeAttribute('role');
  $('#modal').removeAttribute('aria-modal');
  if (_modalOpener && document.contains(_modalOpener)) _modalOpener.focus();
  _modalOpener = null;
}
$('#mask').addEventListener('click', e => { if (e.target.id === 'mask' || e.target.closest('[data-close]')) closeModal(); });
document.addEventListener('keydown', e => {
  if (!$('#mask').classList.contains('on')) return;
  if (e.key === 'Escape') { closeModal(); return; }
  // Tab 循环锁在弹层内（WAI-ARIA Dialog Pattern）
  if (e.key !== 'Tab') return;
  // 用 disabled 过滤而不是 offsetParent——弹层是 position:fixed，offsetParent 判断不可靠
  const items = [...$('#modal').querySelectorAll(FOCUSABLE)];
  if (!items.length) return;
  const first = items[0], last = items[items.length - 1];
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  else if (!items.includes(document.activeElement)) { e.preventDefault(); first.focus(); }
});

/* ══════ 统一卡片 ══════
   技能、人设、工具集、自动任务、能力都用这一个渲染器。
   好处是「所有框一样大、按钮在同一个位置、描述多了会折叠」这件事
   只在一个地方定义，不会各页面各写各的又走样。 */

/**
 * 渲染一张统一卡片。
 * @param {object} o
 *   icon     图标字符（可省）
 *   name     标题
 *   id       副标识（目录名 / 工具 id / cron 表达式）
 *   count    右上角计数徽章文字（可省）
 *   badges   徽章 html（可省）
 *   desc     描述文字（长了自己折叠）
 *   chips    标签数组（可省）
 *   primary  主操作按钮 {label, act, data}
 *   actions  次要操作按钮数组 [{label, act, data}]
 *   detail   详情标识：点了标题/描述会弹详情
 */
function ucard(o) {
  const acts = (o.actions || []).map(a =>
    `<button class="btn" data-ucact="${escAttr(a.act)}" data-ucdata="${escAttr(a.data || '')}" type="button">${esc(a.label)}</button>`
  ).join('');
  const primary = o.primary
    ? `<button class="btn primary" data-ucact="${escAttr(o.primary.act)}" data-ucdata="${escAttr(o.primary.data || '')}" type="button">${esc(o.primary.label)}</button>`
    : '';
  // 五个槽位**永远渲染** —— 缺内容也留空位，
  // 这样所有卡片的高度、描述位置、按钮位置完全一致。
  return `<div class="uc"${o.detail ? ` data-ucdetail="${escAttr(o.detail)}"` : ''}>
    <div class="uh">
      ${o.icon ? `<span class="uicon">${esc(o.icon)}</span>` : '<span class="uicon"></span>'}
      <span class="uname"${o.detail ? ` data-ucdetail="${escAttr(o.detail)}" title="点击查看详情"` : ''}>${esc(o.name)}</span>
      ${o.count ? `<span class="ucnt">${esc(o.count)}</span>` : ''}
    </div>
    <div class="umeta">
      ${o.id ? `<span class="uid">${esc(o.id)}</span>` : ''}
      ${o.badges ? `<span class="ubadges">${o.badges}</span>` : ''}
    </div>
    <div class="udesc"${o.detail ? ` data-ucdetail="${escAttr(o.detail)}" title="点击查看完整说明"` : ''}>${esc(o.desc || '（无说明）')}</div>
    <div class="uchips">${(o.chips || []).slice(0, 8).map(c => `<span class="setchip">${esc(c)}</span>`).join('')}</div>
    <div class="ufoot">${acts}<div class="spacer"></div>${primary}</div>
  </div>`;
}

/** 给一整块容器挂上统一卡片的点击行为（详情 + 按钮）。 */
function bindUCards(root, onAction) {
  if (!root) return;
  root.querySelectorAll('[data-ucact]').forEach(b => b.addEventListener('click', e => {
    e.stopPropagation();
    onAction(b.dataset.ucact, b.dataset.ucdata);
  }));
  root.querySelectorAll('[data-ucdetail]').forEach(el => el.addEventListener('click', e => {
    e.stopPropagation();
    const key = el.dataset.ucdetail;
    const fn = UC_DETAIL[key];
    if (fn) fn();
  }));
}
/* 详情弹窗的注册表：key → 打开详情的函数。
   各页面渲染卡片时把自己要看的详情挂上来。 */
const UC_DETAIL = {};

/** 打开详情弹窗。rows 是 [{label, value}]，value 可以是 html。 */
function ucDetail(title, rows, foot) {
  openModal({
    title, wide: true,
    body: `<div class="ucdetail">${rows.map(r => `
      <div class="urow"><div class="ul">${esc(r.label)}</div>
        <div class="uv">${r.html ? r.html : esc(r.value == null ? '' : r.value)}</div></div>`).join('')}</div>`,
    foot: foot || `<button class="btn" data-close type="button">关闭</button>`,
  });
}

/**
 * 危险操作确认弹窗。
 * 之前都是 confirm() —— 丑、不能自定义文案、也不能说清后果。
 */
function ucConfirm({ title, message, okLabel, danger = true, onOk }) {
  openModal({
    title: title || '确认操作',
    body: `<div class="confirm">
      <span class="cico">${danger ? '⚠' : '?'}</span>
      <div class="ctxt">${message}</div>
    </div>`,
    foot: `<button class="btn" data-close type="button">取消</button>
           <button class="btn ${danger ? 'primary' : ''}" id="ucOk" type="button">${esc(okLabel || '确认')}</button>`,
  });
  $('#ucOk').addEventListener('click', async () => {
    const btn = $('#ucOk');
    // 记下这个确认弹窗的代号 —— onOk 里可能又开了新弹层
    // （比如删完团队重开另一个团队的面板），那时就不能再 closeModal，
    // 否则把刚开的那个也关了。
    const myGen = _modalGen;
    if (btn) { btn.disabled = true; btn.textContent = '处理中…'; }
    try {
      await onOk();
      if (_modalGen === myGen) closeModal();
    } catch (e) {
      if (btn) { btn.disabled = false; btn.textContent = okLabel || '确认'; }
      toast('失败：' + (e && e.message ? e.message : e));
    }
  });
}

/* ══════ composer 筹码：附件 / 技能 / 权限 ══════ */
S.files = [];      // 选中的文件绝对路径
S.skills = [];     // 本轮要绑定的技能
S.policy = null;   // 本轮权限覆盖：'readonly' | 'guarded' | 'full'

function paintAttbar() {
  const bar = $('#attbar');
  const chips = [];
  S.files.forEach((p, i) => chips.push(
    `<span class="attchip"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/></svg><span class="path" title="${esc(p)}">${esc(p.split(/[\\/]/).pop())}</span><button class="rm" data-rmfile="${i}" type="button">✕</button></span>`));
  S.skills.forEach((n, i) => chips.push(
    `<span class="attchip"><span>⚡ ${esc(n)}</span><button class="rm" data-rmskill="${i}" type="button">✕</button></span>`));
  if (S.policy) chips.push(
    `<span class="attchip"><span>🛡 ${esc(POLICY_LABEL[S.policy])}</span><button class="rm" data-rmperm type="button">✕</button></span>`);
  bar.innerHTML = chips.join('');
  bar.querySelectorAll('[data-rmfile]').forEach(b => b.addEventListener('click', () => { S.files.splice(+b.dataset.rmfile, 1); paintAttbar(); }));
  bar.querySelectorAll('[data-rmskill]').forEach(b => b.addEventListener('click', () => { S.skills.splice(+b.dataset.rmskill, 1); paintAttbar(); }));
  const rm = bar.querySelector('[data-rmperm]');
  if (rm) rm.addEventListener('click', () => { S.policy = null; paintAttbar(); paintModeHint(); });
  // 本轮权限变了，模式提示也要跟着变
  paintModeHint();
}

const POLICY_LABEL = { readonly: '只读', guarded: '受控', full: '完整' };
const POLICY_DESC = {
  readonly: '只允许读取与检索，不会修改任何文件或设置',
  guarded: '可写入文件、执行命令，删除与改设置前先出计划给你确认',
  full: '开放全部工具，仍对支付与凭据类操作硬拦截',
};
const POLICY_MODE = { readonly: 'ask', guarded: 'plan', full: 'craft' };

/* ══════ 选择文件（真实系统对话框） ══════ */
const HAS_TAURI = typeof window.__TAURI__ !== 'undefined';
async function pickFiles() {
  if (HAS_TAURI) {
    try {
      const picked = await window.__TAURI__.dialog.open({ multiple: true, title: '选择要交给 Oryx 的文件' });
      if (Array.isArray(picked)) return picked;
      if (typeof picked === 'string') return [picked];
      return [];
    } catch (e) { toast('系统对话框不可用：' + e.message); return null; }
  }
  // 浏览器环境退回 input[type=file]，只能拿到文件名；让用户补路径
  return new Promise(resolve => {
    const inp = document.createElement('input');
    inp.type = 'file'; inp.multiple = true;
    inp.onchange = () => resolve(Array.from(inp.files).map(f => f.name));
    inp.click();
  });
}
async function addFiles() {
  const picked = await pickFiles();
  if (picked === null) return;
  if (!picked.length) return;
  const fresh = picked.filter(p => !S.files.includes(p));
  S.files = S.files.concat(fresh);
  paintAttbar();
  if (S.files.length) {
    const list = S.files.join('、');
    const input = $('#input');
    if (!input.value.trim()) input.value = '请处理这些文件：' + list;
    else if (!input.value.includes(list)) input.value += '\n' + list;
    input.focus();
  }
  toast('已附加 ' + fresh.length + ' 个文件');
}

/* ══════ 粘贴导入 ══════
   粘贴能拿到什么：
     文件   用户在资源管理器里复制的文件 → **浏览器拿不到**（那是 Win32 的
             CF_HDROP），所以走后端 /api/clipboard 读系统剪贴板
     图片   剪贴板里的位图（截图软件写的）→ 同样走后端读，转 PNG 落盘
     文本   纯文字 → 短的直接进输入框，过长的落盘成文件
   为什么全走后端：浏览器那套 navigator.clipboard.read() 要权限、
   经常被拒，而且**永远读不到文件**。本机 Python 进程直接调 Win32 最稳。 */
const PASTE_TEXT_MAX = 2000;

/**
 * 从系统剪贴板导入。
 * kind: text / image / files / empty
 */
async function importFromClipboard() {
  let d;
  try {
    d = await (await api('/api/clipboard')).json();
  } catch (e) {
    toast('读剪贴板失败：' + (e && e.message ? e.message : e));
    return;
  }
  const kind = d.kind || 'empty';
  if (kind === 'files') {
    const fresh = (d.files || []).filter(p => !S.files.includes(p));
    if (!fresh.length) { toast('这些文件已经在附件栏里了'); return; }
    S.files = S.files.concat(fresh);
    paintAttbar();
    const input = $('#input');
    const list = fresh.map(p => p.split(/[\\/]/).pop()).join('、');
    if (!input.value.trim()) input.value = '请处理这些文件：' + fresh.join('、');
    else if (!input.value.includes(list)) input.value += '\n' + list;
    input.focus();
    toast('已粘贴 ' + fresh.length + ' 个文件');
    return;
  }
  if (kind === 'image') {
    await pasteImageDataUrl(d.data_url);
    return;
  }
  if (kind === 'text') {
    const text = d.text || '';
    if (!text) { toast('剪贴板里没有文本'); return; }
    if (text.length <= PASTE_TEXT_MAX) {
      const input = $('#input');
      const start = input.selectionStart != null ? input.selectionStart : input.value.length;
      const end = input.selectionEnd != null ? input.selectionEnd : input.value.length;
      input.value = input.value.slice(0, start) + text + input.value.slice(end);
      input.focus();
      input.selectionStart = input.selectionEnd = start + text.length;
      autoGrow();
      toast('已粘贴 ' + text.length + ' 字');
      return;
    }
    await pasteText(text);
    return;
  }
  toast(d.detail || '剪贴板里没有可用的内容');
}

/** 处理一次粘贴（Ctrl+V）。走浏览器事件，只为拿图片与文本。 */
async function handlePaste(ev) {
  const cd = ev.clipboardData;
  if (!cd) return;
  const target = ev.target;
  const inInput = target && target.id === 'input';

  // 先找图片 —— 截图粘贴到输入框也要能落盘
  for (const it of (cd.items || [])) {
    if (it.kind !== 'file' || !it.type || !it.type.startsWith('image/')) continue;
    const f = it.getAsFile();
    if (f) { ev.preventDefault(); await pasteImage(f); return; }
  }

  // 输入框里粘贴纯文字 → 交给输入框自己处理（保留撤销栈）
  if (inInput) return;

  ev.preventDefault();
  // 其余情况（尤其是文件）走后端读系统剪贴板
  await importFromClipboard();
}

/** 粘贴的图片落盘成附件。file 可以是 File，也可以是 data URL。 */
async function pasteImage(file) {
  try {
    const dataUrl = typeof file === 'string' ? file : await new Promise((resolve, reject) => {
      const fr = new FileReader();
      fr.onload = () => resolve(fr.result);
      fr.onerror = () => reject(new Error('读取失败'));
      fr.readAsDataURL(file);
    });
    await pasteImageDataUrl(dataUrl, file.name || 'paste.png');
  } catch (e) {
    toast('粘贴失败：' + (e && e.message ? e.message : e));
  }
}

/** 图片 data URL 落盘并挂到附件栏。 */
async function pasteImageDataUrl(dataUrl, name) {
  try {
    const r = await api('/api/paste', {
      method: 'POST',
      body: JSON.stringify({ kind: 'image', data_url: dataUrl, name: name || 'paste.png' }),
    });
    if (!r.ok) { const e = await r.json().catch(() => ({})); toast('粘贴失败：' + (e.detail || r.status)); return; }
    const item = (await r.json()).item;
    if (!S.files.includes(item.path)) S.files.push(item.path);
    paintAttbar();
    const input = $('#input');
    if (!input.value.trim()) input.value = '请看这张图：' + item.path;
    input.focus();
    toast('已粘贴图片 ' + item.name);
  } catch (e) {
    toast('粘贴失败：' + (e && e.message ? e.message : e));
  }
}

/** 粘贴的长文本落盘成文件。 */
async function pasteText(text) {
  try {
    const r = await api('/api/paste', {
      method: 'POST',
      body: JSON.stringify({ kind: 'text', text, name: 'pasted.txt' }),
    });
    if (!r.ok) { const e = await r.json().catch(() => ({})); toast('粘贴失败：' + (e.detail || r.status)); return; }
    const item = (await r.json()).item;
    if (!S.files.includes(item.path)) S.files.push(item.path);
    paintAttbar();
    const input = $('#input');
    if (!input.value.trim()) input.value = '请看这份内容：' + item.path;
    input.focus();
    toast('文本太长，已存成 ' + item.name);
  } catch (e) {
    toast('粘贴失败：' + (e && e.message ? e.message : e));
  }
}

/* ══════ 默认权限 ══════ */
function openPerm() {
  openModal({
    title: '本次任务的权限',
    body: `<div class="picklist">
      ${Object.entries(POLICY_LABEL).map(([k, n]) => `
        <button class="pick ${S.policy === k ? 'on' : ''}" data-pol="${k}" type="button">
          <span class="pk">${k === 'readonly' ? '◔' : k === 'guarded' ? '◑' : '◕'}</span>
          <span><span class="pt">${n}权限</span><div class="pd">${POLICY_DESC[k]}</div></span>
        </button>`).join('')}
    </div>
    <div class="hint" style="font-size:11.5px;color:var(--muted);margin-top:12px;line-height:1.65">
      只影响这一条消息。不选则沿用顶栏的「${({ ask: '问答', plan: '规划', craft: '执行' })[S.interaction] || S.interaction}」模式。
    </div>`,
    foot: `<button class="btn" data-close type="button">取消</button><button class="btn primary" data-pol-apply type="button">应用</button>`,
  });
  let pending = S.policy;
  $$('#modal .pick').forEach(b => b.addEventListener('click', () => {
    pending = b.dataset.pol;
    $$('#modal .pick').forEach(x => x.classList.toggle('on', x === b));
  }));
  $('#modal [data-pol-apply]').addEventListener('click', () => {
    S.policy = pending;
    paintAttbar();
    closeModal();
    toast(pending ? '本次任务权限：' + POLICY_LABEL[pending] : '已恢复默认权限');
  });
}

/* ══════ 调用技能 ══════ */
async function openSkills() {
  const d = await (await api('/api/skills')).json();
  const list = d.skills || [];
  openModal({
    title: '调用技能',
    body: list.length ? `<div class="picklist">
      ${list.map(s => `
        <button class="pick ${S.skills.includes(s.name) ? 'on' : ''}" data-skill="${esc(s.name)}" type="button">
          <span class="pk">⚡</span>
          <span><span class="pt">${esc(s.name)}</span><div class="pd">${esc(s.description || '（无说明）')}</div></span>
        </button>`).join('')}
    </div>` : '<div class="empty">还没有技能。去技能广场新建一个。</div>',
    foot: `<button class="btn" data-close type="button">关闭</button>`,
  });
  $$('#modal [data-skill]').forEach(b => b.addEventListener('click', () => {
    const n = b.dataset.skill;
    const i = S.skills.indexOf(n);
    if (i >= 0) S.skills.splice(i, 1); else S.skills.push(n);
    b.classList.toggle('on', S.skills.includes(n));
    paintAttbar();
  }));
}

/* ══════ 提示词优化（弹层可编辑 + 可自选接入 skill） ══════ */
S.optSkills = null;          // null = 沿用对话已绑的 S.skills；数组 = 本弹层单独选
$('#optBtn').addEventListener('click', async () => {
  const text = $('#input').value.trim();
  if (!text) { toast('先写一句要做什么，我再帮你优化'); $('#input').focus(); return; }

  const sk = await (await api('/api/skills')).json().catch(() => ({ skills: [] }));
  const allSkills = sk.skills || [];
  // 没单独选过时，默认沿用对话侧已绑的技能
  const picked = S.optSkills === null ? S.skills.slice() : S.optSkills.slice();
  let optimized = '', notes = [];
  try {
    const r = await api('/api/prompt/optimize', {
      method: 'POST',
      body: JSON.stringify({ text, skills: picked.join(','), expert: S.expert, style: S.style }),
    });
    const d = await r.json();
    optimized = d.optimized || ''; notes = d.notes || [];
  } catch (_) { optimized = ''; }
  if (!optimized) { toast('优化失败，可手动补充细节'); return; }

  const skillRows = allSkills.map(s => `
    <button class="pick ${picked.includes(s.name) ? 'on' : ''}" data-osk="${esc(s.name)}" type="button">
      <span class="pk">⚡</span>
      <span><span class="pt">${esc(cnSkill(s.name))}</span><div class="pd">${esc(s.description || '（无说明）')}</div></span>
    </button>`).join('');

  openModal({
    title: '优化后的提示词',
    wide: true,
    body: `
      <div class="fld"><label>原文</label><div class="opt-orig">${esc(text)}</div></div>
      <div class="fld">
        <label for="optText">优化版（可直接改）</label>
        <textarea class="inp" id="optText">${esc(optimized)}</textarea>
        <div class="hint">${notes.length ? '优化点：' + esc(notes.join('；')) : '补齐了目标、约束与验收标准。'}</div>
      </div>
      <div class="fld">
        <label>接入的 Skill<button class="lnk" id="optSkillReset" type="button">恢复默认</button></label>
        ${allSkills.length ? `<div class="picklist">${skillRows}</div>
          <div class="hint">勾选的技能会作为参考写进优化结果；优化与对话用的是同一个模型服务。</div>`
        : '<div class="empty">还没有技能。去技能广场新建一个。</div>'}
      </div>
      ${picked.length ? `<div class="fld"><label>本次将绑定</label>
        <div class="chips" id="optPicked">${picked.map(s => `<span class="chip">⚡ ${esc(cnSkill(s))}</span>`).join('')}</div></div>` : ''}`,
    foot: `<button class="btn" data-close type="button">取消</button><button class="btn primary" id="optApply" type="button">用这个版本</button>`,
  });

  // 勾选变化 → 重算优化结果，让用户看到 skill 接入的实际影响
  const repaint = () => {
    const box = $('#optPicked');
    if (box) box.innerHTML = picked.map(s => `<span class="chip">⚡ ${esc(cnSkill(s))}</span>`).join('');
  };
  const reopt = async () => {
    try {
      const r = await api('/api/prompt/optimize', {
        method: 'POST',
        body: JSON.stringify({ text, skills: picked.join(','), expert: S.expert, style: S.style }),
      });
      const d = await r.json();
      if (d.optimized) {
        $('#optText').value = d.optimized;
        const h = $('#optText').parentElement.querySelector('.hint');
        if (h) h.textContent = d.notes && d.notes.length ? '优化点：' + d.notes.join('；') : '补齐了目标、约束与验收标准。';
      }
    } catch (_) {}
  };
  $$('#modal [data-osk]').forEach(b => b.addEventListener('click', async () => {
    const n = b.dataset.osk;
    const i = picked.indexOf(n);
    if (i >= 0) picked.splice(i, 1); else picked.push(n);
    b.classList.toggle('on', picked.includes(n));
    repaint();
    await reopt();
  }));
  const rst = $('#optSkillReset');
  if (rst) rst.addEventListener('click', async () => {
    S.optSkills = S.skills.slice();
    picked.length = 0; picked.push(...S.skills);
    $$('#modal [data-osk]').forEach(x => x.classList.toggle('on', picked.includes(x.dataset.osk)));
    repaint();
    await reopt();
    toast('已恢复为对话侧绑定的技能');
  });

  $('#optApply').addEventListener('click', () => {
    // 勾选结果同步回对话，下一条消息就带着这些技能
    S.optSkills = picked.slice();
    S.skills = picked.slice();
    paintAttbar();
    $('#input').value = $('#optText').value;
    closeModal();
    toast(picked.length ? `已填入输入框 · 接入 ${picked.length} 个技能` : '已填入输入框');
  });
});

/* ══════ 文件 / 权限 / 技能 芯片接线 ══════ */
$('#fileBtn').addEventListener('click', addFiles);
$('#permBtn').addEventListener('click', openPerm);
$('#skillBtn').addEventListener('click', openSkills);

/* 粘贴导入：点按钮与按 Ctrl+V 都走后端读系统剪贴板。
   浏览器那套 navigator.clipboard.read() 读不到文件，还会因为权限被拒 ——
   后端直接调 Win32 一次到位。 */
$('#pasteBtn').addEventListener('click', async () => {
  const btn = $('#pasteBtn');
  if (btn) { btn.disabled = true; }
  try {
    await importFromClipboard();
  } finally {
    if (btn) btn.disabled = false;
    $('#input').focus();
  }
});
/* 全局粘贴：在输入框之外粘贴也能接住 */
document.addEventListener('paste', ev => {
  // 已经在一个输入控件里粘贴 → 交给那个控件
  const tgt = ev.target;
  if (tgt && (tgt.tagName === 'INPUT' || tgt.tagName === 'TEXTAREA' || tgt.isContentEditable)) {
    // 但输入框里的图片粘贴还是要接住（截图场景）
    if (tgt.id !== 'input') return;
    handlePaste(ev);
    return;
  }
  handlePaste(ev);
});

/* ══════ 终端 ══════ */

/**
 * 跑一条终端命令。
 *
 * 三个问题在这里修掉：
 * 1. **失败信息不能混进 stdout** —— 以前 `[失败] …` 直接追加到输出流里，
 *    和真实命令输出视觉上完全一样，用户以为命令跑出了那段内容。
 *    现在失败单独用一个错误样式渲染。
 * 2. **输入框不要先清空** —— 网络失败时命令文本已经丢了，用户无法重试。
 *    现在失败时把命令留在框里。
 * 3. **按钮要禁掉** —— 连点两次会发两条命令。
 */
async function runTerm() {
  const input = $('#termInput');
  const c = input.value.trim();
  if (!c) return;
  const btn = $('#termRun');
  btn.disabled = true;
  try {
    const r = await api('/api/terminal/run', { method: 'POST', body: JSON.stringify({ command: c }) });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      // 失败：命令留在输入框里，用户可以直接改或重试
      appendTermError(e.detail || ('请求失败（' + r.status + '）'));
      toast('命令没发出去');
      return;
    }
    input.value = '';
    autoGrow();
  } catch (err) {
    appendTermError((err && err.message) || '无法连接内核');
    toast('无法连接内核');
  } finally {
    btn.disabled = false;
  }
}

/** 终端里的错误行 —— 与 stdout 视觉上区分开。 */
function appendTermError(msg) {
  const out = $('#termOut');
  if (!out) return;
  const line = document.createElement('div');
  line.style.color = 'var(--danger)';
  line.textContent = '[失败] ' + msg;
  out.appendChild(line);
  out.scrollTop = out.scrollHeight;
}
$('#termRun').addEventListener('click', runTerm);
$('#termInput').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); runTerm(); } });
$('#termKill').addEventListener('click', async () => {
  // 没有运行中的会话时要**说清楚**，不能静默返回 ——
  // 静默返回的话用户点了没反应，以为按钮坏了。
  if (!S.term) { toast('现在没有正在运行的命令'); return; }
  const btn = $('#termKill');
  btn.disabled = true;
  try {
    const r = await api('/api/terminal/interrupt', { method: 'POST', body: JSON.stringify({ id: S.term }) });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      toast('中断失败：' + (e.detail || r.status));
      return;
    }
    toast('已中断');
  } catch (err) {
    toast('中断失败：' + ((err && err.message) || err));
  } finally {
    btn.disabled = false;
  }
});

/* ══════ 页面渲染 ══════ */
/**
 * 页面标题对照表。
 *
 * 以前定义了却没人用 —— 顶栏标题只有对话页会更新，
 * 切到别的页面还留着上一个会话的名字，用户分不清自己在哪。
 * 现在 setView() 会查这张表。
 */
const VIEW_NAME = {
  work: '工作台',
  market: '技能广场',
  persona: '人设管理',
  automations: '自动任务',
  workflows: '工作流',
  local: '本地资料',
  settings: '设置',
  dashboard: '用量面板',
  chat: '对话',
};
async function pageShell(title, lead, body) {
  $('#page').innerHTML = `
    <div class="phead"><h1>${esc(title)}</h1>${lead ? `<span class="sub">${esc(lead)}</span>` : ''}<div class="spacer"></div>
      <button class="btn" id="backBtn" type="button">返回对话</button></div>
    <div class="pbody">${body}</div>`;
  $('#backBtn').addEventListener('click', () => setView('chat'));
}
function gotoView(v) { setView(v); }

const WORK_SCENES = {
  office: { label: '日常办公', icon: '🗂', picks: [
    ['📄', '文档处理', '用 docx 工具新建或改写一份 Word 文档'],
    ['📊', '数据分析', '用 xlsx 读取表格、算指标并出结论'],
    ['🎞', '幻灯片', '用 pptx 生成一份汇报幻灯片'],
    ['🔍', '资料检索', '在本地内容索引里检索相关资料并总结'],
    ['🖼', '图片识别', '识别图片里的文字与表格'],
    ['🌐', '网页操作', '打开网页、点按钮、填表单并抓取内容'],
  ] },
  dev: { label: '代码开发', icon: '💻', picks: [
    ['💻', '代码执行', '在终端里运行命令并解释输出'],
    ['📂', '文件读写', '批量读写工作区里的代码文件'],
    ['🔧', '系统信息', '查看 CPU、内存、磁盘与进程状态'],
    ['🧭', '网页抓取', '抓取指定网址并提取正文'],
    ['🧩', '技能检索', '按关键词找到合适的技能再执行'],
    ['🖥', '屏幕截图', '截图当前窗口并标注问题位置'],
  ] },
  research: { label: '调研分析', icon: '🔬', picks: [
    ['📚', '综述撰写', '围绕一个主题写带出处的综述'],
    ['🧾', '信息抽取', '从给定资料里抽出结构化字段'],
    ['⚖️', '对比评估', '对比多个方案并给出推荐结论'],
    ['🗣', '纪要整理', '把会议记录整理成结论与待办'],
    ['📈', '趋势洞察', '基于数据看趋势并指出异常点'],
    ['✍️', '文案润色', '按指定风格重写一段文案'],
  ] },
};
S.workPicked = [];

async function renderWork() {
  const caps = await (await api('/api/capabilities')).json();
  const sc = WORK_SCENES[S.scene] || WORK_SCENES.office;
  await pageShell('工作台', '', `
    <div class="hero">
      <h1>Oryx，我帮你</h1>
      <p>选场景 → 勾要用的能力 → 直接开工，或自己写一句</p>
      <div class="scenes">
        ${Object.entries(WORK_SCENES).map(([k, v]) => `<div class="scene ${S.scene === k ? 'on' : ''}" data-scene="${k}">${v.icon} ${v.label}</div>`).join('')}
      </div>
      <div class="caps">
        ${sc.picks.map(([i, n, t]) => `<div class="cap ${S.workPicked.includes(t) ? 'on' : ''}" data-tool="${esc(t)}">${i} ${n}</div>`).join('')}
      </div>
      <div id="workPickBar" style="margin-top:22px"></div>
    </div>
    <div class="sect"><h2>能力清单 · ${caps.total} 个工具 · ${caps.agents.length} 个子 Agent</h2>
      <div class="grid">${caps.groups.map(g => `
        <div class="tile"><h3>${esc(cnGroup(g.group))} <span class="chip">${g.count}</span></h3>
        <p>${g.tools.slice(0, 5).map(t => esc(cnTool(t.name))).join('、')}${g.count > 5 ? '…' : ''}</p>
        <div class="foot"><button class="btn" data-group="${esc(g.group)}" type="button">全选这一组</button></div></div>`).join('')}</div>
    </div>`);
  const bar = $('#workPickBar');
  const paintBar = () => {
    if (!S.workPicked.length) { bar.innerHTML = ''; return; }
    bar.innerHTML = S.workPicked.length ? `<div class="pickedbox">
      <div class="pk">已选 ${S.workPicked.length} 项能力</div>
      <div class="pl">${S.workPicked.map(cnTool).map(esc).join('、')}</div>
      <div class="row" style="justify-content:center;margin-top:12px">
        <button class="btn" id="workClear" type="button">清空</button>
        <button class="btn primary" id="workGo" type="button">用这些能力开始</button></div></div>` : '';
    $('#workClear').addEventListener('click', () => { S.workPicked = []; renderWork(); });
    $('#workGo').addEventListener('click', () => {
      const text = S.workPicked.join('；');
      $('#input').value = '请帮我完成：' + text;
      setView('chat'); $('#input').focus();
    });
  };
  paintBar();
  $$('#page [data-scene]').forEach(el => el.addEventListener('click', () => { S.scene = el.dataset.scene; S.workPicked = []; renderWork(); }));
  $$('#page [data-tool]').forEach(el => el.addEventListener('click', () => {
    const t = el.dataset.tool;
    const i = S.workPicked.indexOf(t);
    if (i >= 0) S.workPicked.splice(i, 1); else S.workPicked.push(t);
    el.classList.toggle('on', S.workPicked.includes(t));
    paintBar();
  }));
  $$('#page [data-group]').forEach(el => el.addEventListener('click', () => {
    const g = caps.groups.find(x => x.group === el.dataset.group);
    if (!g) return;
    (g.tools || []).forEach(t => { if (!S.workPicked.includes(t.name)) S.workPicked.push(t.name); });
    renderWork();
  }));
}

function openInstall() {
  openModal({
    title: '批量导入扩展',
    body: `<div class="fld"><label for="inPath">源目录</label>
        <input class="inp" id="inPath" placeholder="D:/downloads/claude-extensions"/>
        <div class="hint">目录里的每一项按内容自动识别：含 <span class="kbd">plugin.json</span> → 插件，含 <span class="kbd">EXPERT.md</span> → 人设，含 <span class="kbd">SKILL.md</span> → 技能。</div></div>
      <div class="fld"><label>只导入（不勾＝全都要）</label>
        <div class="row">
          <button class="pick on" data-kind="plugin" style="width:auto" type="button"><span class="pt">插件</span></button>
          <button class="pick on" data-kind="expert" style="width:auto" type="button"><span class="pt">人设</span></button>
          <button class="pick on" data-kind="skill" style="width:auto" type="button"><span class="pt">技能</span></button>
        </div></div>
      <div style="font-size:11.5px;color:var(--muted);line-height:1.7">
        同名项目会被覆盖。导入后立即生效，不需要重启。</div>`,
    foot: `<button class="btn" data-close type="button">取消</button><button class="btn primary" id="inGo" type="button">开始导入</button>`,
  });
  const kinds = new Set(['plugin', 'expert', 'skill']);
  $$('#modal [data-kind]').forEach(b => b.addEventListener('click', () => {
    const k = b.dataset.kind;
    if (kinds.has(k)) kinds.delete(k); else kinds.add(k);
    b.classList.toggle('on', kinds.has(k));
  }));
  $('#inGo').addEventListener('click', async () => {
    const path = $('#inPath').value.trim();
    if (!path) { toast('先填源目录'); return; }
    const list = kinds.size ? Array.from(kinds) : null;
    const btn = $('#inGo'); btn.disabled = true; btn.textContent = '导入中…';
    const r = await api('/api/install', { method: 'POST', body: JSON.stringify({ source: path, kinds: list }) });
    btn.disabled = false; btn.textContent = '开始导入';
    if (!r.ok) { const e = await r.json().catch(() => ({})); toast('失败：' + (e.detail || r.status)); return; }
    const d = await r.json();
    const parts = [];
    if ((d.installed || []).length) parts.push(`已装 ${d.installed.length} 项`);
    if ((d.skipped || []).length) parts.push(`跳过 ${d.skipped.length} 项`);
    if ((d.errors || []).length) parts.push(`失败 ${d.errors.length} 项`);
    closeModal();
    toast(parts.join(' · ') || '目录里没有可识别的扩展');
    refreshState(); renderMarket();
  });
}

S.marketTab = 'skills';
// 技能广场的搜索词。放在 S 上 —— 以前只存在输入框里，
// 切一次 tab 输入框被重建，搜索词就没了；而且 experts / agents / tools
// 三个 tab 根本不读它，搜索框在那些页签上形同虚设。
S.marketQuery = '';
async function renderMarket() {
  const [sk, caps] = await Promise.all([api('/api/skills').then(r => r.json()), api('/api/capabilities').then(r => r.json())]);
  const skills = sk.skills || [];
  const experts = (await (await api('/api/persona')).json()).available || [];
  const tabs = [['skills', `技能 ${skills.length}`], ['experts', `人设 ${experts.length}`], ['agents', `子 Agent ${caps.agents.length}`], ['tools', `工具 ${caps.total}`]];
  await pageShell('技能广场', '技能、人设、子 Agent 与工具 —— 全部可以在这里增删改', `
    <div class="row" style="margin-bottom:16px">
      <div class="subtabs" style="margin:0">${tabs.map(([k, n]) => `<button class="${S.marketTab === k ? 'on' : ''}" data-mtab="${k}" type="button">${n}</button>`).join('')}</div>
      <div class="spacer"></div>
      <input class="inp" id="mkq" value="${escAttr(S.marketQuery)}" placeholder="搜索当前页签…" aria-label="在技能广场搜索" style="width:190px"/>
      <button class="btn" id="mkImport" type="button">批量导入</button>
      ${S.marketTab === 'skills' ? '<button class="btn primary" id="mkAdd" type="button">新建技能</button>' : ''}
      ${S.marketTab === 'experts' ? '<button class="btn primary" id="mkAddEx" type="button">新建人设</button>' : ''}
    </div>
    <div id="mkBody"></div>`);
  $$('#page [data-mtab]').forEach(b => b.addEventListener('click', () => { S.marketTab = b.dataset.mtab; renderMarket(); }));
  $('#mkImport').addEventListener('click', openInstall);
  // 搜索框：输入即过滤，**四个页签通用**。
  // 以前只有 skills 页签读它 —— 在人设/子 Agent/工具页签上输入，什么都不会发生。
  const mkq = $('#mkq');
  if (mkq) {
    let timer = null;
    const apply = () => {
      clearTimeout(timer);
      // 工具页签走的是语义检索（要调接口），其余页签本地过滤 ——
      // 统一都走重渲染，工具页签内部会调语义搜索。
      renderMarket();
    };
    mkq.addEventListener('input', () => {
      S.marketQuery = mkq.value;
      // 轻微防抖：每敲一个字就重渲染整个列表会卡
      clearTimeout(timer);
      timer = setTimeout(apply, 220);
    });
    mkq.addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); apply(); }
    });
  }
  const q = v => v.toLowerCase();
  const keyword = q(S.marketQuery);
  const body = $('#mkBody');
  if (S.marketTab === 'skills') {
    const hit = skills.filter(x => !keyword || q(x.name + x.description).includes(keyword));
    body.innerHTML = hit.length ? `<div class="grid ucgrid">${hit.map(x => ucard({
      icon: cnSkill(x.name).slice(0, 1),
      name: cnSkill(x.name),
      id: x.name,
      desc: x.description || '（无说明）',
      detail: 'skill:' + x.name,
      actions: [{ label: '编辑', act: 'skedit', data: x.name }],
      primary: { label: '在对话中使用', act: 'skuse', data: x.name },
    })).join('')}</div>` : '<div class="empty">没有匹配的技能</div>';
    // 技能详情：点标题/描述弹出来，能看全文 + 编辑 + 删除
    hit.forEach(x => {
      UC_DETAIL['skill:' + x.name] = async () => {
        const d = await (await api(`/api/skills/${encodeURIComponent(x.name)}`)).json().catch(() => ({}));
        ucDetail(cnSkill(x.name), [
          { label: '目录名', value: x.name },
          { label: '说明', value: x.description || '（无）' },
          { label: '正文', html: `<pre>${esc(d.body || '')}</pre>` },
        ], `<button class="btn" id="skDelNow" type="button">删除</button>
            <div class="spacer"></div>
            <button class="btn" data-close type="button">关闭</button>
            <button class="btn primary" id="skEditNow" type="button">编辑</button>`);
        $('#skEditNow').addEventListener('click', () => editSkill(x.name));
        $('#skDelNow').addEventListener('click', () => {
          closeModal();
          ucConfirm({
            title: '删除技能',
            message: `确定删除技能「<b>${esc(cnSkill(x.name))}</b>」？<br/>技能目录 <span class="kbd">${esc(x.name)}</span> 会一起删掉，删了要重新写。`,
            okLabel: '删除',
            onOk: async () => {
              const r = await api(`/api/skills/${encodeURIComponent(x.name)}`, { method: 'DELETE' });
              if (!r.ok) throw new Error('删除失败');
              toast('已删除'); renderMarket();
            },
          });
        });
      };
    });
    bindUCards(body, (act, data) => {
      if (act === 'skedit') { editSkill(data); return; }
      if (act === 'skuse') {
        if (!S.skills.includes(data)) S.skills.push(data);
        paintAttbar(); setView('chat'); $('#input').focus();
        toast('已绑定技能 ' + cnSkill(data));
      }
    });
    $('#mkAdd').addEventListener('click', () => editSkill(null));
  } else if (S.marketTab === 'experts') {
    const hitEx = experts.filter(e => !keyword
      || q(e.name + (e.description || '') + (e.tags || []).join(' ')).includes(keyword));
    body.innerHTML = hitEx.length ? `<div class="grid ucgrid">${hitEx.map(e => ucard({
      icon: (e.name || '?').slice(0, 1),
      name: e.name,
      id: e.id,
      badges: S.expert === e.id ? '<span class="badge done">使用中</span>' : '',
      desc: e.description || '（无说明）',
      chips: e.tags || [],
      detail: 'expert:' + e.id,
      actions: [
        { label: '编辑', act: 'exedit', data: e.id },
        { label: '删除', act: 'exdel', data: e.id },
      ],
      primary: { label: S.expert === e.id ? '取消使用' : '设为当前', act: 'exuse', data: e.id },
    })).join('')}</div>`
      : (experts.length ? '<div class="empty">没有匹配的人设</div>' : '<div class="empty">还没有人设</div>');
    hitEx.forEach(e => {
      UC_DETAIL['expert:' + e.id] = async () => {
        const d = await (await api(`/api/experts/${encodeURIComponent(e.id)}`)).json().catch(() => ({}));
        const p = d.persona || d;
        ucDetail(e.name, [
          { label: '人设 id', value: e.id },
          { label: '说明', value: e.description || '（无）' },
          { label: '自我介绍', value: p.self_intro || '（无）' },
          { label: '性格关键词', value: p.personality || '（无）' },
          { label: '说话风格', value: p.speaking_style || '（无）' },
          { label: '适用场景', value: p.use_cases || '（无）' },
        ], `<button class="btn" id="exDelNow" type="button">删除</button>
            <div class="spacer"></div>
            <button class="btn" data-close type="button">关闭</button>
            <button class="btn primary" id="exEditNow" type="button">编辑</button>`);
        $('#exEditNow').addEventListener('click', () => editExpert(e.id));
        $('#exDelNow').addEventListener('click', () => {
          closeModal();
          ucConfirm({
            title: '删除人设',
            message: `确定删除人设「<b>${esc(e.name)}</b>」？<br/>删了之后这个角色的性格与说话方式就找不回来了。`,
            okLabel: '删除',
            onOk: async () => {
              const r = await api(`/api/experts/${encodeURIComponent(e.id)}`, { method: 'DELETE' });
              if (!r.ok) throw new Error('删除失败');
              toast('已删除'); refreshState(); renderMarket();
            },
          });
        });
      };
    });
    bindUCards(body, async (act, data) => {
      if (act === 'exedit') { editExpert(data); return; }
      if (act === 'exuse') {
        await api('/api/expert', { method: 'POST', body: JSON.stringify({ expert: S.expert === data ? '' : data }) });
        refreshState(); renderMarket();
        return;
      }
      if (act === 'exdel') {
        const e = experts.find(x => x.id === data);
        ucConfirm({
          title: '删除人设',
          message: `确定删除人设「<b>${esc(e ? e.name : data)}</b>」？<br/>删了之后这个角色的性格与说话方式就找不回来了。`,
          okLabel: '删除',
          onOk: async () => {
            const r = await api(`/api/experts/${encodeURIComponent(data)}`, { method: 'DELETE' });
            if (!r.ok) throw new Error('删除失败');
            toast('已删除'); refreshState(); renderMarket();
          },
        });
      }
    });
    $('#mkAddEx').addEventListener('click', () => editExpert(null));
  } else if (S.marketTab === 'agents') {
    // 子 Agent 页签是**表单式**（下拉选一个 Agent 来编辑），
    // 所以搜索作用在下拉选项上 —— 名字、说明、工具名都参与匹配。
    const hitAgents = caps.agents.filter(a => !keyword
      || q((a.name || '') + ' ' + (a.description || '') + ' ' + (a.id || '')
        + ' ' + (a.tools || []).join(' ')).includes(keyword));
    body.innerHTML = `
      <div class="agentedit">
        <div class="row" style="margin-bottom:12px">
          <select class="inp" id="agSel" style="flex:1">
            ${hitAgents.map(a => `<option value="${esc(a.id)}">${esc(a.name || cnAgent(a.id))}（${esc(cnAgent(a.id))}）</option>`).join('')}
          </select>
          <button class="btn" id="agNew" type="button">新建 Agent</button>
        </div>
        <div id="agForm"></div>
      </div>`;
    if (!hitAgents.length && keyword) {
      $('#agForm').innerHTML = '<div class="empty">没有匹配的子 Agent</div>';
    }
    const sel = $('#agSel');
    const paintAgent = async (id) => {
      if (!id) { $('#agForm').innerHTML = '<div class="empty">还没有子 Agent。点「新建 Agent」创建。</div>'; return; }
      const d = await (await api(`/api/agents/${encodeURIComponent(id)}`)).json();
      const byGroup = caps.groups.map(g => ({ group: g.group, tools: g.tools.map(t => t.name) }));
      $('#agForm').innerHTML = `
        <div class="fld"><label for="agName">显示名</label><input class="inp"  id="agName" value="${esc(d.name)}"/></div>
        <div class="fld"><label for="agDesc">一句话说明（组队与派活时可见）</label><input class="inp"  id="agDesc" value="${esc(d.description || '')}"/></div>
        <div class="fld" style="max-width:200px"><label for="agTurns">最多轮次</label><input class="inp"  id="agTurns" type="number" min="1" max="40" value="${d.max_turns}"/></div>
        <div class="fld"><label>工具白名单 · ${(d.tools || []).length} 个已选（硬约束，未勾的工具它拿不到）</label>
          <div class="subtabs" id="agToolTabs">${byGroup.map((g, i) => `<button class="${i === 0 ? 'on' : ''}" data-agg="${i}" type="button">${esc(g.group)} ${g.tools.length}</button>`).join('')}</div>
          <div id="agToolBox"></div>
          <div class="hint">已选：<span id="agPicked"></span></div>
        </div>
        <div class="fld"><label>职责与工作方式（正文，写清它做什么、边界在哪、产出什么）</label>
          <textarea class="inp" id="agBody" style="min-height:260px">${esc(d.body || '')}</textarea></div>
        <div class="row">
          <button class="btn primary" id="agSave" type="button">保存</button>
          <button class="btn" id="agGroup" type="button">组队协作</button>
          <div class="spacer"></div>
          <button class="btn" id="agDelete" type="button">删除这个 Agent</button>
        </div>`;
      const picked = (d.tools || []).slice();
      let gi = 0;
      const paintPicked = () => { $('#agPicked').innerHTML = picked.map(t => `<span class="chip">${esc(t)}</span>`).join(' ') || '<span style="color:var(--muted)">（无）</span>'; };
      const paintTools = () => {
        const g = byGroup[gi];
        $('#agToolBox').innerHTML = `<div class="toolgrid">${g.tools.map(t => `
          <button class="pick ${picked.includes(t) ? 'on' : ''}" data-agt="${esc(t)}" style="padding:6px 10px" type="button">
            <span class="pt" style="font-size:12px">${esc(t)}</span>
          </button>`).join('')}</div>`;
        $('#agToolBox').querySelectorAll('[data-agt]').forEach(b => b.addEventListener('click', () => {
          const t = b.dataset.agt;
          const i = picked.indexOf(t);
          if (i >= 0) picked.splice(i, 1); else picked.push(t);
          b.classList.toggle('on', picked.includes(t));
          paintPicked();
        }));
      };
      $$('#agToolTabs [data-agg]').forEach(b => b.addEventListener('click', () => {
        gi = +b.dataset.agg;
        $$('#agToolTabs [data-agg]').forEach(x => x.classList.toggle('on', x === b));
        paintTools();
      }));
      paintTools(); paintPicked();
      $('#agSave').addEventListener('click', async () => {
        const payload = {
          name: $('#agName').value.trim(), description: $('#agDesc').value.trim(),
          tools: picked, max_turns: Math.max(1, Math.min(40, +$('#agTurns').value || 6)), body: $('#agBody').value,
        };
        if (!payload.name) { toast('显示名不能为空'); return; }
        const r = await api(`/api/agents/${encodeURIComponent(d.id)}`, { method: 'PUT', body: JSON.stringify(payload) });
        if (!r.ok) { const e = await r.json().catch(() => ({})); toast('失败：' + (e.detail || r.status)); return; }
        toast('已保存'); renderMarket();
      });
      $('#agGroup').addEventListener('click', () => teamForm({ members: [d.id] }));
      $('#agDelete').addEventListener('click', async () => {
        if (!confirm(`删除子 Agent「${d.name}」？`)) return;
        const r = await api(`/api/agents/${encodeURIComponent(d.id)}`, { method: 'DELETE' });
        toast(r.ok ? '已删除' : '删除失败'); renderMarket();
      });
    };
    sel.addEventListener('change', () => paintAgent(sel.value));
    $('#agNew').addEventListener('click', () => editAgent(null, () => renderMarket()));
    paintAgent(caps.agents[0] && caps.agents[0].id);
  } else {
    // 工具：按**工具集**聚合，不是一个一个散着摆。
    // 一组工具（浏览器操作、系统设置、文件读写…）才是一个用户会用的东西，
    // 把 7 个 browser_* 拆成 7 张卡片，用户根本不知道该点哪个。
    // 每张卡片 = 一个工具集，点「用这套工具」把整组能力交给模型。
    body.innerHTML = `<div class="row" style="margin-bottom:12px;align-items:center">
        <label for="mkGroup" style="font-size:12.5px;color:var(--muted)">工具集</label>
        <select class="inp" id="mkGroup" style="width:auto;min-width:170px">
          <option value="">全部工具集</option>
          ${caps.groups.map(g => `<option value="${esc(g.group)}">${esc(cnGroup(g.group))}（${g.count}）</option>`).join('')}
        </select>
        <div class="spacer"></div>
        <span style="font-size:12px;color:var(--muted)">${caps.groups.length} 套 · ${caps.total} 个工具</span>
      </div><div class="grid ucgrid" id="mkToolGrid"></div>`;
    const grid = $('#mkToolGrid');

    // 一个工具集 = 一张卡片，卡片里列出这套工具都有什么
    const paintSets = groups => {
      if (!groups.length) { grid.innerHTML = '<div class="empty">没有匹配的工具集</div>'; return; }
      grid.innerHTML = groups.map(g => ucard({
        icon: cnGroup(g.group).slice(0, 1),
        name: cnGroup(g.group),
        id: g.group,
        count: g.count + ' 个工具',
        desc: g.tools.map(t => cnTool(t.name)).join('、'),
        chips: g.tools.slice(0, 8).map(t => cnTool(t.name)),
        detail: 'set:' + g.group,
        actions: [{ label: '查看明细', act: 'setdetail', data: g.group }],
        primary: { label: '在对话中使用', act: 'setuse', data: g.group },
      })).join('');
      // 工具集详情：列出这一套里的每个工具
      groups.forEach(g => {
        UC_DETAIL['set:' + g.group] = () => {
          ucDetail(cnGroup(g.group), [
            { label: '工具集 id', value: g.group },
            { label: '工具数', value: g.count + ' 个' },
            { label: '包含', html: `<div class="ucdetail" style="gap:8px">${g.tools.map(t => `
                <div class="urow" style="gap:8px">
                  <div class="uv"><b>${esc(cnTool(t.name))}</b> ${badge(t.risk)}
                    <div style="font-size:11.5px;color:var(--muted)">${esc(t.description || '')}</div>
                    <div style="font:11px/1.4 var(--font-mono);color:var(--muted)">${esc(t.name)}</div>
                  </div>
                </div>`).join('')}</div>` },
          ], `<button class="btn" data-close type="button">关闭</button>
              <button class="btn primary" id="setUseNow" type="button">在对话中使用</button>`);
          $('#setUseNow').addEventListener('click', () => { closeModal(); useSet(g.group); });
        };
      });
    };

    // 「用这套工具」：把整组工具的名字都写进输入框，模型自己挑
    const useSet = group => {
      const g = caps.groups.find(x => x.group === group);
      if (!g) return;
      const names = g.tools.map(t => cnTool(t.name)).join('、');
      $('#input').value = `用「${cnGroup(group)}」这套工具（${names}）来处理：`;
      setView('chat'); $('#input').focus();
      toast('已带上「' + cnGroup(group) + '」的 ' + g.count + ' 个工具');
    };

    bindUCards(grid, (act, data) => {
      if (act === 'setuse') { useSet(data); return; }
      if (act === 'setdetail') {
        const fn = UC_DETAIL['set:' + data];
        if (fn) fn();
      }
    });
    // 工具搜索：跟着顶上的搜索框走，**输入即搜**。
    //
    // 以前要另点一个「搜索」按钮才生效，而且按钮只在这一页签有 ——
    // 用户在别的页签输入的词，切过来就没了。
    // 现在统一用 S.marketQuery，四个页签共享同一个搜索词。
    const doToolSearch = async () => {
      const group = $('#mkGroup').value;
      if (group) {
        const g = caps.groups.find(x => x.group === group);
        paintSets(g ? [g] : []);
        return;
      }
      const kw = S.marketQuery.trim();
      if (!kw) { paintSets(caps.groups); return; }
      // 搜索命中的是**单个工具** —— 把它所在的工具集整张展示出来，
      // 用户看到的是「这个工具属于哪套能力」，而不是一个孤零零的工具名
      const d = await (await api('/api/tools/search', { method: 'POST', body: JSON.stringify({ query: kw, limit: 40 }) })).json();
      const hit = new Set((d.tools || []).map(t => t.name));
      const groups = caps.groups
        .map(g => ({ ...g, tools: g.tools.filter(t => hit.has(t.name)) }))
        .filter(g => g.tools.length);
      if (groups.length) { paintSets(groups); return; }
      // 一个都没命中匹配到分组（分组名与工具名不一致）→ 退回平铺
      const loose = d.tools || [];
      grid.innerHTML = loose.length ? `<div class="grid ucgrid">${loose.map(t => ucard({
        icon: cnTool(t.name).slice(0, 1),
        name: cnTool(t.name),
        id: t.name,
        badges: badge(t.risk),
        desc: t.description || '（无说明）',
        detail: 'tool:' + t.name,
        primary: { label: '在对话中使用', act: 'tooluse', data: t.name },
      })).join('')}</div>` : '<div class="empty">没有匹配的工具</div>';
      loose.forEach(t => {
        UC_DETAIL['tool:' + t.name] = () => {
          ucDetail(cnTool(t.name), [
            { label: '工具 id', value: t.name },
            { label: '风险等级', html: badge(t.risk) + ' ' + esc(RISK[t.risk] || '') },
            { label: '说明', value: t.description || '（无）' },
          ], `<button class="btn" data-close type="button">关闭</button>
              <button class="btn primary" id="toolUseNow" type="button">在对话中使用</button>`);
          $('#toolUseNow').addEventListener('click', () => {
            closeModal();
            $('#input').value = '用 ' + cnTool(t.name) + ' 工具';
            setView('chat'); $('#input').focus();
          });
        };
      });
      bindUCards(grid, (act, data) => {
        if (act !== 'tooluse') return;
        $('#input').value = '用 ' + cnTool(data) + ' 工具';
        setView('chat'); $('#input').focus();
      });
    };
    // 初次渲染就走搜索 —— 有搜索词就搜，没搜索词就列全部。
    // （以前是 paintSets(caps.groups) + 一个「搜索」按钮，
    //   用户不点按钮就永远看到全部。）
    doToolSearch();
  }
}

async function viewSkill(name) {
  const d = await (await api(`/api/skills/${encodeURIComponent(name)}`)).json();
  openModal({
    title: d.name, wide: true,
    body: `<div class="fld"><label>说明</label><div style="font-size:13px">${esc(d.description || '（无）')}</div></div>
      <div class="fld"><label>正文（SKILL.md）</label><div class="mdpre" style="max-height:52vh;overflow:auto"><code>${esc(d.body || '')}</code></div></div>`,
    foot: `<button class="btn" data-close type="button">关闭</button><button class="btn primary" id="skEditNow" type="button">编辑</button>`,
  });
  $('#skEditNow').addEventListener('click', () => editSkill(d.name));
}

function editSkill(name) {
  const load = name ? api(`/api/skills/${encodeURIComponent(name)}`).then(r => r.json()) : Promise.resolve({ name: '', description: '', body: '' });
  load.then(d => {
    openModal({
      title: name ? '编辑技能' : '新建技能', wide: true,
      body: `<div class="fld"><label for="skName">技能名（目录名，不可含 / \\）</label><input class="inp"  id="skName" value="${esc(d.name)}" ${name ? 'disabled' : ''}/></div>
        <div class="fld"><label for="skDesc">一句话说明（模型据此决定是否调用）</label><input class="inp"  id="skDesc" value="${esc(d.description || '')}" placeholder="这个技能解决什么问题"/></div>
        <div class="fld"><label for="skBody">正文（写清步骤、约束与产出格式）</label><textarea class="inp"  id="skBody">${esc(d.body || '')}</textarea></div>`,
      foot: `<button class="btn" data-close type="button">取消</button><button class="btn primary" id="skSave" type="button">保存</button>`,
    });
    $('#skSave').addEventListener('click', async () => {
      const payload = { name: $('#skName').value.trim(), description: $('#skDesc').value.trim(), body: $('#skBody').value };
      if (!payload.name) { toast('技能名不能为空'); return; }
      const r = name
        ? await api(`/api/skills/${encodeURIComponent(name)}`, { method: 'PUT', body: JSON.stringify(payload) })
        : await api('/api/skills', { method: 'POST', body: JSON.stringify(payload) });
      if (!r.ok) { const e = await r.json().catch(() => ({})); toast('失败：' + (e.detail || r.status)); return; }
      closeModal(); toast(name ? '已保存' : '已创建'); refreshState(); renderMarket();
    });
  });
}

function editAgent(id, done) {
  openModal({
    title: '新建子 Agent', wide: false,
    body: `<div class="fld"><label for="naId">id（目录名，创建后不可改）</label><input class="inp"  id="naId" placeholder="如 data-analyst"/></div>
      <div class="fld"><label for="naName">显示名</label><input class="inp"  id="naName" placeholder="如 数据分析"/></div>
      <div class="fld"><label for="naDesc">一句话说明</label><input class="inp"  id="naDesc" placeholder="它擅长什么"/></div>
      <div class="fld"><label for="naTurns">最多轮次</label><input class="inp"  id="naTurns" type="number" min="1" max="40" value="6"/></div>
      <div class="fld"><label for="naBody">职责与工作方式（正文）</label><textarea class="inp"  id="naBody" placeholder="它做什么、边界在哪、产出什么"></textarea>
        <div class="hint">创建后到「子 Agent」标签里勾选它的工具白名单并补充正文。</div></div>`,
    foot: `<button class="btn" data-close type="button">取消</button><button class="btn primary" id="naSave" type="button">创建</button>`,
  });
  $('#naSave').addEventListener('click', async () => {
    const payload = {
      id: $('#naId').value.trim(), name: $('#naName').value.trim(), description: $('#naDesc').value.trim(),
      tools: [], max_turns: Math.max(1, Math.min(40, +$('#naTurns').value || 6)), body: $('#naBody').value,
    };
    if (!payload.id || !payload.name) { toast('id 和显示名都要填'); return; }
    const r = await api('/api/agents', { method: 'POST', body: JSON.stringify(payload) });
    if (!r.ok) { const e = await r.json().catch(() => ({})); toast('失败：' + (e.detail || r.status)); return; }
    closeModal(); toast('已创建'); if (done) done();
  });
}

function editExpert(id) {
  const load = id ? api(`/api/experts/${encodeURIComponent(id)}`).then(r => r.json()) : Promise.resolve({ id: '', name: '', description: '', tags: [], scenarios: [], personality: '', speaking_style: '', use_cases: '', self_intro: '', body: '' });
  load.then(d => {
    openModal({
      title: id ? '编辑人设' : '新建人设', wide: true,
      body: `<div class="fld"><label for="exId">id（目录名，创建后不可改）</label><input class="inp"  id="exId" value="${esc(d.id || '')}" ${id ? 'disabled' : ''} placeholder="如 code-reviewer"/></div>
        <div class="fld"><label for="exName">名称</label><input class="inp"  id="exName" value="${esc(d.name || '')}"/></div>
        <div class="fld"><label for="exDesc">一句话说明</label><input class="inp"  id="exDesc" value="${esc(d.description || '')}"/></div>
        <div class="row2" style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
          <div class="fld"><label for="exTags">标签（逗号分隔）</label><input class="inp"  id="exTags" value="${esc((d.tags || []).join(', '))}"/></div>
          <div class="fld"><label for="exScen">适用场景（逗号分隔）</label><input class="inp"  id="exScen" value="${esc((d.scenarios || []).join(', '))}"/></div>
        </div>
        <div class="fld"><label for="exPerson">性格关键词</label><input class="inp"  id="exPerson" value="${esc(d.personality || '')}" placeholder="严谨、直接、不绕弯"/></div>
        <div class="fld"><label for="exSpeak">说话风格</label><input class="inp"  id="exSpeak" value="${esc(d.speaking_style || '')}" placeholder="先结论后论据，最多五条"/></div>
        <div class="fld"><label for="exIntro">自我介绍</label><input class="inp"  id="exIntro" value="${esc(d.self_intro || '')}"/></div>
        <div class="fld"><label for="exBody">工作方式（正文，写清做事流程与输出标准）</label><textarea class="inp"  id="exBody">${esc(d.body || '')}</textarea></div>`,
      foot: `<button class="btn" data-close type="button">取消</button><button class="btn primary" id="exSave" type="button">保存</button>`,
    });
    $('#exSave').addEventListener('click', async () => {
      const split = v => v.split(/[,，、]/).map(x => x.trim()).filter(Boolean);
      const payload = {
        id: $('#exId').value.trim(), name: $('#exName').value.trim(),
        description: $('#exDesc').value.trim(), tags: split($('#exTags').value), scenarios: split($('#exScen').value),
        personality: $('#exPerson').value.trim(), speaking_style: $('#exSpeak').value.trim(),
        use_cases: $('#exScen').value.trim(), self_intro: $('#exIntro').value.trim(), body: $('#exBody').value,
      };
      if (!payload.id || !payload.name) { toast('id 和名称都要填'); return; }
      const r = id
        ? await api(`/api/experts/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(payload) })
        : await api('/api/experts', { method: 'POST', body: JSON.stringify(payload) });
      if (!r.ok) { const e = await r.json().catch(() => ({})); toast('失败：' + (e.detail || r.status)); return; }
      closeModal(); toast(id ? '已保存' : '已创建'); refreshState(); renderMarket();
    });
  });
}

S.personaTab = 'persona';
async function renderPersona() {
  const d = await (await api('/api/persona')).json();
  const p = d.persona;
  const list = d.available || [];
  const mem = await (await api('/api/memory?limit=80')).json();
  const memories = mem.memories || [];
  await pageShell('人设管理', '人设决定性格与说话方式；记忆是它长期记住的事 —— 都可以在这里增删改', `
    <div class="subtabs">
      <button class="${S.personaTab === 'persona' ? 'on' : ''}" data-ptab="persona" type="button">人设 ${list.length}</button>
      <button class="${S.personaTab === 'memory' ? 'on' : ''}" data-ptab="memory" type="button">记忆 ${memories.length}</button>
      <div class="spacer"></div>
      ${S.personaTab === 'persona' ? '<button class="btn primary" id="pAddEx" type="button">新建人设</button>' : '<button class="btn primary" id="pAddMem" type="button">手动添加</button><button class="btn" id="pImportMem" type="button">批量导入</button>'}
    </div>
    ${S.personaTab === 'persona' ? `
    <div class="setwrap" style="padding:0;max-width:none">
      <div class="setnav">
        ${list.map(e => `<div class="it ${e.id === d.expert ? 'on' : ''}" data-ex="${e.id}">
          <span style="width:22px;height:22px;border-radius:50%;background:${e.id === d.expert ? 'var(--accent)' : 'var(--panel-2)'};color:${e.id === d.expert ? '#fff' : 'var(--muted)'};display:grid;place-items:center;font-size:11px;font-weight:700">${esc((e.name || '?').slice(0, 1))}</span>
          ${esc(e.name)}</div>`).join('')}
      </div>
      <div class="setmain">
        ${p ? `
          <div class="persona">
            <div class="orb">${esc((p.name || 'O').slice(0, 1))}</div>
            <h3>Hi，我是${esc(p.name)}</h3>
            <div class="tags">${(p.tags || []).map(t => `<span class="tag">${esc(t)}</span>`).join('')}</div>
            <div class="feat">
              <h4>🤝 自我介绍</h4><p>${esc(p.self_intro || p.description || '')}</p>
              <h4>🧠 人设特征</h4>
              <h4>性格关键词</h4><p>${esc(p.personality || '—')}</p>
              <h4>说话风格</h4><p>${esc(p.speaking_style || '—')}</p>
              <h4>适用场景</h4><p>${esc(p.use_cases || '—')}</p>
            </div>
            <div class="row" style="justify-content:center;margin-top:22px">
              <button class="btn" data-pexedit="${esc(p.id)}" type="button">编辑这个人设</button>
              <button class="btn" data-pexclear type="button">取消使用</button>
            </div>
          </div>` : `<div class="empty">还没有选择人设。在左侧挑一个，或新建一个。</div>`}
      </div>
    </div>` : `
    <div class="sect"><h2>长期记忆 · Oryx 记住的事</h2>
      <div class="card" style="margin-bottom:16px">
        <div class="fld"><label>批量导入：一行一条</label>
          <textarea class="inp" id="memBulk" placeholder="我叫张三，团队 5 个人&#10;项目代号 Falcon，下周三交付&#10;所有报告统一用中文，数字保留两位小数"></textarea>
          <div class="hint">粘贴任何你想让 Oryx 长期记住的内容，一行一条，导入后每次对话都会带上。</div></div>
        <button class="btn primary" id="memBulkSave" type="button">导入</button>
      </div>
      ${memories.length ? memories.map(m => `
        <div class="member" style="align-items:flex-start">
          <span class="mk" style="background:var(--panel-2);color:var(--muted)">${esc((m.text || '').slice(0, 1))}</span>
          <span style="flex:1;min-width:0">
            <span class="mn" style="font-weight:400;font-size:13px;display:block;line-height:1.7">${esc(m.text)}</span>
            ${m.tags ? `<span class="mr">标签：${esc(m.tags)}</span>` : ''}
          </span>
          <span class="mact"><button class="btn" data-memdel="${esc(m.id)}" type="button">删除</button></span>
        </div>`).join('') : '<div class="empty">还没有记忆。粘贴一段内容导入，或手动添加一条。</div>'}
    </div>`}`);
  $$('#page [data-ptab]').forEach(b => b.addEventListener('click', () => { S.personaTab = b.dataset.ptab; renderPersona(); }));
  const addEx = $('#pAddEx'); if (addEx) addEx.addEventListener('click', () => editExpert(null));
  const addMem = $('#pAddMem'); if (addMem) addMem.addEventListener('click', () => {
    openModal({
      title: '添加一条记忆',
      body: `<div class="fld"><label for="mOne">内容</label><textarea class="inp"  id="mOne" placeholder="想让 Oryx 记住什么"></textarea></div>
        <div class="fld"><label for="mTags">标签（可选，逗号分隔）</label><input class="inp"  id="mTags" placeholder="如 偏好, 团队"/></div>`,
      foot: `<button class="btn" data-close type="button">取消</button><button class="btn primary" id="mSave" type="button">保存</button>`,
    });
    $('#mSave').addEventListener('click', async () => {
      const text = $('#mOne').value.trim();
      if (!text) { toast('内容不能为空'); return; }
      const r = await api('/api/memory', { method: 'POST', body: JSON.stringify({ text, tags: $('#mTags').value.trim() }) });
      if (!r.ok) { toast('保存失败'); return; }
      closeModal(); renderPersona(); toast('已记住');
    });
  });
  const impMem = $('#pImportMem'); if (impMem) impMem.addEventListener('click', () => {
    $('#memBulk').focus();
    toast('粘贴到上面文本框，点「导入」');
  });
  const bulkSave = $('#memBulkSave'); if (bulkSave) bulkSave.addEventListener('click', async () => {
    const lines = $('#memBulk').value.split('\n').map(x => x.trim()).filter(Boolean);
    if (!lines.length) { toast('先粘贴内容'); return; }
    let ok = 0;
    for (const text of lines) {
      const r = await api('/api/memory', { method: 'POST', body: JSON.stringify({ text }) });
      if (r.ok) ok++;
    }
    renderPersona(); toast('已导入 ' + ok + ' 条');
  });
  $$('#page [data-memdel]').forEach(b => b.addEventListener('click', async () => {
    const r = await api(`/api/memory/${encodeURIComponent(b.dataset.memdel)}`, { method: 'DELETE' });
    toast(r.ok ? '已删除' : '删除失败'); renderPersona();
  }));
  const ed = $('#page [data-pexedit]'); if (ed) ed.addEventListener('click', () => editExpert(ed.dataset.pexedit));
  const cl = $('#page [data-pexclear]'); if (cl) cl.addEventListener('click', async () => {
    await api('/api/expert', { method: 'POST', body: JSON.stringify({ expert: '' }) });
    refreshState(); renderPersona(); toast('已取消人设');
  });
  $$('#page [data-ex]').forEach(el => el.addEventListener('click', async () => {
    await api('/api/expert', { method: 'POST', body: JSON.stringify({ expert: el.dataset.ex }) });
    refreshState(); renderPersona(); toast('人设已切换');
  }));
}

const AUTO_TEMPLATES = [
  ['🌅', '每日晨报', '0 8 * * 1-5', '汇总昨天的重要消息、未完成待办与今天的关键日程', '工作日早上 8 点'],
  ['🌙', '夜间收尾', '0 22 * * *', '整理今天产出文件、列出未完成项并给出明日计划', '每晚 22 点'],
  ['🔄', '周期巡检', '*/30 * * * *', '检查工作区关键目录与服务的运行状态，异常立刻报我', '每 30 分钟'],
  ['🧹', '磁盘清理报告', '0 10 * * 7', '统计磁盘占用最大的目录并给出可安全清理的清单', '每周日 10 点'],
  ['📊', '数据周报', '0 9 * * 1', '汇总本周关键指标的变化并解释异常波动', '每周一 9 点'],
  ['🔔', '待办提醒', '0 18 * * 1-5', '提醒今天还没完成的待办，按优先级排序', '工作日 18 点'],
];
S.autoTpl = null;

/**
 * 立刻执行一个定时任务。
 *
 * 这个按钮以前是坏的：它只发了 {goal}，漏掉 session_id / seq / interaction。
 * 后果是——
 *   · 无 session_id → 任务不挂在任何会话下，S.taskId 认领不到，
 *     流式输出不会出现在任何面板里，用户只看到一个 toast，界面永远不动；
 *   · 无 seq → 后端的「发起在途」对不上号，用户点「停止」停不掉它；
 *   · 无 interaction → 顶栏选的模式被忽略。
 *
 * 现在：切回对话页 + 建一个新会话 + 带齐三个字段，
 * 任务跑起来的过程和产出都回到对话流里，用户看得见、停得掉。
 */
async function runAutomationNow(a) {
  if (S.busy) { toast('有任务正在跑，先停掉它'); return; }
  const title = a.name || '定时任务';
  // 新建一个会话承载这次执行 —— 定时任务的产出不该混进用户正在聊的对话
  const sessionId = 'auto-' + Date.now().toString(36);
  const seq = ++S.sendSeq;
  S.inflightSeq = seq;
  S.busy = true;
  setSendState('running');
  setView('work');                     // 切回对话页，否则输出没地方显示
  S.current = sessionId;
  S.taskId = '';
  S.steps.clear(); renderTodos([]); renderFiles();
  $('#p-steps').innerHTML = '<div class="empty">准备中…</div>';
  addMsg('user', '【' + title + '】' + a.goal);
  try {
    const r = await api('/api/chat', {
      method: 'POST',
      body: JSON.stringify({
        goal: a.goal,
        interaction: S.interaction || 'craft',
        session_id: sessionId,
        seq,
      }),
    });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      addMsg('bot', '发起失败：' + (e.detail || r.status));
      S.busy = false; setSendState('idle');
      toast('发起失败');
    } else {
      toast('已开始执行，过程见对话');
    }
  } catch (e) {
    addMsg('bot', '无法连接内核：' + e.message);
    S.busy = false; setSendState('idle');
    toast('无法连接内核');
  } finally {
    if (S.inflightSeq === seq) S.inflightSeq = 0;
  }
}

async function renderAutomations() {
  const d = await (await api('/api/automations')).json();
  const list = d.automations || [];
  $('#autoCnt').textContent = list.length || '';
  await pageShell('自动任务', '到点自动执行 —— 从模板起手，或自己写 cron', `
    <div class="sect"><h2>模板库 · 点一下自动填好表单</h2>
      <div class="grid ucgrid">${AUTO_TEMPLATES.map(([i, n, cron, goal, when]) => ucard({
        icon: i,
        name: n,
        id: cron,
        desc: goal,
        chips: [when],
        actions: [{ label: '填入表单', act: 'tplfill', data: n }],
        primary: { label: '用这个模板', act: 'tplfill', data: n },
      })).join('')}</div>
    </div>
    <div class="sect"><h2>已建任务 · ${list.length}</h2>
      ${list.length ? `<div class="grid ucgrid">${list.map(a => ucard({
        icon: '⏰',
        name: a.name,
        id: a.cron,
        count: a.run_count ? '已跑 ' + a.run_count + ' 次' : '',
        badges: a.enabled === false ? '<span class="badge">已停用</span>' : '',
        desc: a.goal,
        chips: [a.description || ''].filter(Boolean),
        detail: 'auto:' + (a.id || a.name),
        actions: [
          { label: '编辑', act: 'autoedit', data: a.id || a.name },
          { label: '立刻执行', act: 'autorun', data: a.name },
        ],
        primary: { label: '删除', act: 'autodel', data: a.id || a.name },
      })).join('')}</div>` : '<div class="empty">还没有自动任务，点上面模板一键创建</div>'}
    </div>
    <div class="sect"><h2>新建 / 编辑</h2><div class="card">
      <div class="fld"><label for="anName">任务名</label><input class="inp"  id="anName" placeholder="如 每日晨报" style="width:100%"/></div>
      <div class="fld"><label for="anCron">触发时间（cron）</label><input class="inp"  id="anCron" placeholder="0 9 * * 1-5" style="width:100%"/>
        <div class="hint">分 时 日 月 周。支持 <span class="kbd">*</span> <span class="kbd">0 9 * * 1-5</span> <span class="kbd">*/15 * * * *</span> 与 <span class="kbd">@daily</span> 等别名。
          <span class="spacer"></span><button class="btn" id="anCronHelp" type="button">常用写法</button></div></div>
      <div class="fld"><label for="anGoal">到点做什么</label><input class="inp"  id="anGoal" placeholder="到点执行什么" style="width:100%"/></div>
      <div class="row"><button class="btn primary" id="anAdd" type="button">创建</button></div>
    </div></div>`);
  // 模板卡：点一下把参数填进下面的表单
  const tplMap = {};
  AUTO_TEMPLATES.forEach(([i, n, cron, goal, when]) => { tplMap[n] = { cron, goal }; });
  $$('#page [data-ucact="tplfill"]').forEach(b => b.addEventListener('click', () => {
    const t = tplMap[b.dataset.ucdata];
    if (!t) return;
    $('#anName').value = b.dataset.ucdata;
    $('#anCron').value = t.cron;
    $('#anGoal').value = t.goal;
    toast('模板已填入，点「创建」生效');
    $('#anAdd').scrollIntoView({ behavior: 'smooth', block: 'center' });
  }));
  // 已建任务卡：详情 / 编辑 / 立刻执行 / 删除
  list.forEach(a => {
    const id = a.id || a.name;
    UC_DETAIL['auto:' + id] = () => {
      ucDetail(a.name, [
        { label: 'cron', value: a.cron },
        { label: '说明', value: a.description || '（无）' },
        { label: '到点做什么', value: a.goal },
        { label: '已执行', value: (a.run_count || 0) + ' 次' },
        { label: '状态', value: a.enabled === false ? '已停用' : '启用中' },
      ], `<button class="btn" id="autoDelNow" type="button">删除</button>
          <div class="spacer"></div>
          <button class="btn" data-close type="button">关闭</button>
          <button class="btn primary" id="autoEditNow" type="button">编辑</button>`);
      $('#autoEditNow').addEventListener('click', () => editAutomation(a));
      $('#autoDelNow').addEventListener('click', () => {
        closeModal();
        deleteAutomation(a);
      });
    };
  });
  bindUCards($('#page'), (act, data) => {
    if (act === 'autorun') {
      const a = list.find(x => x.name === data);
      if (!a) return;
      runAutomationNow(a);
      return;
    }
    if (act === 'autoedit') {
      const a = list.find(x => (x.id || x.name) === data);
      if (a) editAutomation(a);
      return;
    }
    if (act === 'autodel') {
      const a = list.find(x => (x.id || x.name) === data);
      if (a) deleteAutomation(a);
    }
  });
  $('#anCronHelp').addEventListener('click', () => {
    openModal({
      title: 'cron 常用写法',
      body: `<div class="picklist">
        ${[['@daily', '每天一次'], ['@hourly', '每小时一次'], ['0 9 * * 1-5', '工作日 9:00'],
           ['0 9 * * 1', '每周一 9:00'], ['*/15 * * * *', '每 15 分钟'], ['0 0 1 * *', '每月 1 号 0:00']]
          .map(([c, d]) => `<div class="pick" style="cursor:default"><span class="pk"><span class="kbd">${c}</span></span><span><span class="pt">${d}</span></span></div>`).join('')}
      </div>
      <div class="hint" style="font-size:11.5px;color:var(--muted);margin-top:12px;line-height:1.7">
        五段依次是 <b>分 时 日 月 周</b>。日与周都填时会取并集 —— 「0 9 1 * 1」= 每月 1 号与每周一各触发一次。</div>`,
      foot: `<button class="btn" data-close type="button">知道了</button>`,
    });
  });
  $('#anAdd').addEventListener('click', async () => {
    const name = $('#anName').value.trim(), cron = $('#anCron').value.trim(), goal = $('#anGoal').value.trim();
    if (!name || !cron || !goal) { toast('名称、cron、动作都要填'); return; }
    const r = await api('/api/automations', { method: 'POST', body: JSON.stringify({ name, cron, goal }) });
    if (r.ok) { toast('已创建'); renderAutomations(); } else { const e = await r.json().catch(() => ({})); toast('失败：' + (e.detail || r.status)); }
  });
}

/** 改一个自动任务。原来只有「新建」，改不了已建的。 */
function editAutomation(a) {
  openModal({
    title: '编辑自动任务', wide: true,
    body: `<div class="fld"><label for="aeName">任务名</label><input class="inp" id="aeName" value="${esc(a.name)}"/></div>
      <div class="fld"><label for="aeCron">触发时间（cron）</label><input class="inp" id="aeCron" value="${esc(a.cron)}"/>
        <div class="hint">分 时 日 月 周，如 <span class="kbd">0 9 * * 1-5</span></div></div>
      <div class="fld"><label for="aeGoal">到点做什么</label><textarea class="inp" id="aeGoal">${esc(a.goal || '')}</textarea></div>`,
    foot: `<button class="btn" data-close type="button">取消</button><button class="btn primary" id="aeSave" type="button">保存</button>`,
  });
  $('#aeSave').addEventListener('click', async () => {
    const name = $('#aeName').value.trim();
    const cron = $('#aeCron').value.trim();
    const goal = $('#aeGoal').value.trim();
    if (!name || !cron || !goal) { toast('名称、cron、动作都要填'); return; }
    const r = await api(`/api/automations/${encodeURIComponent(a.id || a.name)}`, {
      method: 'PUT', body: JSON.stringify({ name, cron, goal }),
    });
    if (!r.ok) { const e = await r.json().catch(() => ({})); toast('失败：' + (e.detail || r.status)); return; }
    closeModal(); toast('已保存'); renderAutomations();
  });
}

/** 删一个自动任务 —— 带确认弹窗，说清后果。 */
function deleteAutomation(a) {
  ucConfirm({
    title: '删除自动任务',
    message: `确定删除自动任务「<b>${esc(a.name)}</b>」？<br/>删掉之后到点就不会再自动执行了。`,
    okLabel: '删除',
    onOk: async () => {
      const r = await api(`/api/automations/${encodeURIComponent(a.id || a.name)}`, { method: 'DELETE' });
      if (!r.ok) throw new Error('删除失败');
      toast('已删除'); renderAutomations();
    },
  });
}

/* ══════════ 工作流编排 ══════════
   编排由模型写（workflow_run 工具），前端只做观察者 —— 不提供编排编辑器。
   事件驱动的关键约束：workflow.* 事件走 SSE，断线重连会**补发**，
   所以状态一律增量更新 DOM 节点，绝不整页 innerHTML 重绘，
   否则补发会把用户正在看的内容刷掉。 */

/* 每个视图各记一份状态，切走再切回不丢（同 S.localView / S.teamBusy 的约定） */
S.wf = { runs: [], live: null, dirty: false, failedCount: 0,
         tab: 'runs', templates: [], pending: new Set() };

const WF_STATUS = {
  running:  { cls: 'run',    text: '执行中' },
  done:     { cls: 'done',   text: '完成' },
  failed:   { cls: 'failed', text: '失败' },
  cancelled:{ cls: 'denied', text: '已中止' },
  skipped:  { cls: '',       text: '未执行' },
};
function wfBadge(status) {
  const m = WF_STATUS[status] || { cls: '', text: status || '—' };
  return `<span class="badge ${m.cls}">${esc(m.text)}</span>`;
}
function wfDur(ms) {
  if (!ms && ms !== 0) return '';
  // 后端给的是浮点秒差，不取整会渲染成 "109.88807678222656ms"
  if (ms < 1000) return Math.round(ms) + 'ms';
  if (ms < 60000) return (ms / 1000).toFixed(1) + 's';
  const m = Math.floor(ms / 60000), s = Math.round((ms % 60000) / 1000);
  return m + '分' + (s ? s + '秒' : '');
}
function wfAgo(ts) {
  if (!ts) return '—';
  const d = Date.now() / 1000 - ts;
  if (d < 60) return '刚刚';
  if (d < 3600) return Math.floor(d / 60) + ' 分钟前';
  if (d < 86400) return Math.floor(d / 3600) + ' 小时前';
  return Math.floor(d / 86400) + ' 天前';
}
/* 落库的 step 主键是 "<run_id>:<step_id>"，展示时只要后半段 */
function shortStepId(sid) {
  const s = String(sid || '');
  const i = s.lastIndexOf(':');
  return i >= 0 ? s.slice(i + 1) : s;
}

/* ── 数据拉取 ── */
async function loadWorkflowRuns(silent) {
  let runs = [], failedCount = 0;
  try {
    runs = (await (await api('/api/workflows?limit=30')).json()).runs || [];
    // 全失败的总数要单独查：列表是分页的，在这一页里数会漏掉更早的记录
    failedCount = await (await api('/api/workflows/failed-count')).json()
      .then(x => x.count || 0).catch(() => 0);
  } catch (_) {}
  S.wf.runs = runs;
  S.wf.failedCount = failedCount;
  // 徽标只显示"正在跑"的数量：编排跑一次几分钟，运行中才是用户要知道的
  const live = runs.filter(r => r.status === 'running').length;
  const el = $('#wfCnt');
  if (el) el.textContent = live || '';
  if (S.view === 'workflows' && !silent) renderWorkflows();
  return runs;
}

/* run 级状态徽标。

   run.status 只说"编排跑完了没有"，不说"成没成功" —— 单 step 失败是隔离的，
   编排照样会走完全程。所以一个全军覆没的 run，status 也是 done。
   这里按 failed_steps 修正：全失败显示红色「失败」，部分失败加副标。 */
function wfRunBadge(r) {
  const total = r.total_steps || 0;
  const failed = r.failed_steps || 0;
  if (r.status === 'running' || !total) return wfBadge(r.status);
  if (failed === total) return `<span class="badge failed">失败</span>`;
  if (failed > 0) return `${wfBadge(r.status)}<span class="badge failed" style="margin-left:5px">${failed} 失败</span>`;
  return wfBadge(r.status);
}

/* step 状态徽标：失败数 / 未执行数。
   数据来自列表接口的 step_counts（后端 GROUP BY 算好），
   不要从 error 文本里正则猜 —— error 可能为空，也可能写着别的原因。 */
function wfStepBadges(r) {
  const c = r.step_counts || {};
  const out = [];
  if (c.failed && (r.failed_steps || 0) !== c.failed) {
    out.push(`<span class="badge failed" style="margin-left:5px">${c.failed} 失败</span>`);
  }
  if (c.skipped) out.push(`<span class="badge" style="margin-left:5px">${c.skipped} 未执行</span>`);
  return out.join('');
}

/* 进度列：成功的 step 数 / 总数。
   全失败时返回 "0/N" 会让人以为是"刚开始"，所以那一律显示 "0/N 失败"。 */
function wfProgress(r) {
  const total = r.total_steps || 0;
  if (!total) return '—';
  const failed = r.failed_steps || 0;
  const skipped = (r.step_counts || {}).skipped || 0;
  const ok = total - failed - skipped;
  const txt = `${ok}/${total}`;
  return failed === total ? `<span style="color:var(--danger)">${txt}</span>` : esc(txt);
}

/* 全失败的 run（没有任何 step 产出）。计数来自后端统计接口 ——
   列表是分页的，前端只在这一页里数会漏掉更早的全失败记录。 */
function wfAllFailedCount() {
  return S.wf.failedCount || 0;
}

/* ── 列表页 ── */
async function renderWorkflows() {
  const runs = S.wf.runs;
  const live = runs.filter(r => r.status === 'running');
  const rest = runs.filter(r => r.status !== 'running');

  await pageShell('工作流', '多 Agent 编排 —— 阶段化推进，中间结果只落库不回灌上下文', `
    ${live.length ? `<div class="sect"><h2>运行中 · ${live.length}</h2>
      <div class="card" id="wfLive">${live.map(r => wfRunCard(r)).join('')}</div></div>` : ''}
    <div class="subtabs">
      <button class="${S.wf.tab === 'runs' ? 'on' : ''}" data-wftab="runs" type="button">运行记录 ${rest.length}</button>
      <button class="${S.wf.tab === 'tpl' ? 'on' : ''}" data-wftab="tpl" type="button">模板 ${S.wf.templates.length}</button>
      <div class="spacer"></div>
      ${S.wf.tab === 'tpl' ? '<button class="btn primary" id="wfTplNew" type="button">新建模板</button>' : ''}
    </div>
    ${S.wf.tab === 'runs' ? `
    <div class="sect"><h2>历史运行 · ${rest.length}
      ${wfAllFailedCount() ? `<span class="spacer"></span>
        <button class="btn" id="wfClearFailed" type="button">清理 ${wfAllFailedCount()} 个全失败的记录</button>` : ''}
      </h2>
      <div class="hint" style="font-size:11.5px;color:var(--muted);margin:-4px 0 12px">
        编排定义从模板功能上线后才开始留档，所以只有带「存为模板」按钮的记录能复用 —— 更早的记录补不回来了。</div>
      ${rest.length ? `<div class="card"><div class="wfscroll"><table class="mx wftbl">
        <thead><tr><th>名称</th><th>状态</th><th>进度</th><th>耗时</th><th>时间</th><th></th></tr></thead><tbody>
        ${rest.map(r => `<tr>
          <td><div class="wfnn">${esc(r.name || '未命名')}</div><div class="wfng">${esc((r.goal || '').slice(0, 60))}</div></td>
          <td><div class="wfbd">${wfRunBadge(r)}${wfStepBadges(r)}</div></td>
          <td>${wfProgress(r)}</td>
          <td>${esc(wfDur(r.ended_at && r.created_at ? (r.ended_at - r.created_at) * 1000 : 0) || '—')}</td>
          <td>${esc(wfAgo(r.created_at))}</td>
          <td><div class="row" style="gap:6px;flex-wrap:nowrap">
            <button class="btn" data-wfopen="${esc(r.id)}" type="button">查看</button>
            ${r.has_plan ? `<button class="btn" data-wfsave="${esc(r.id)}" data-wfsavename="${esc(r.name || '未命名')}" type="button">存为模板</button>` : ''}
            <button class="btn" data-wfdel="${esc(r.id)}" data-wfdelname="${esc(r.name || '未命名')}" type="button">删除</button>
          </div></td>
        </tr>`).join('')}
        </tbody></table></div></div>`
        : `<div class="empty">还没有工作流<br><span style="font-size:11px">在对话里说「用工作流跑这个」，或让 Oryx 对大任务自动编排</span></div>`}
    </div>` : wfTemplatesHtml()}`);

  $$('#page [data-wftab]').forEach(b => b.addEventListener('click', () => {
    S.wf.tab = b.dataset.wftab;
    renderWorkflows();
  }));
  $$('#page [data-wfopen]').forEach(b => b.addEventListener('click', () => openWorkflowDetail(b.dataset.wfopen)));
  $$('#page [data-wfcancel]').forEach(b => b.addEventListener('click', async () => {
    if (!confirm('中止这次工作流？已完成的 step 结果会保留。')) return;
    const r = await api(`/api/workflows/${encodeURIComponent(b.dataset.wfcancel)}/cancel`, { method: 'POST' });
    const d = await r.json().catch(() => ({}));
    toast(d.ok === false ? (d.detail || '中止失败') : '已请求中止');
  }));
  $$('#page [data-wfdel]').forEach(b => b.addEventListener('click', async () => {
    // 连同每个 step 的产出一起删，不可撤销 —— 确认框必须把这件事说清楚
    if (!confirm(`删除「${b.dataset.wfdelname}」？\n这次运行的全部 step 与产出都会一并删除，无法恢复。`)) return;
    const r = await api(`/api/workflows/${encodeURIComponent(b.dataset.wfdel)}`, { method: 'DELETE' });
    if (!r.ok) { const e = await r.json().catch(() => ({})); toast('删除失败：' + (e.detail || r.status)); return; }
    toast('已删除');
    await loadWorkflowRuns(true);
    renderWorkflows();
  }));
  $$('#page [data-wfsave]').forEach(b => b.addEventListener('click', () => {
    saveRunAsTemplate(b.dataset.wfsave, b.dataset.wfsavename);
  }));
  $$('#page [data-wfstep]').forEach(wfBindStep);

  const clearBtn = $('#wfClearFailed');
  if (clearBtn) clearBtn.addEventListener('click', async () => {
    const n = wfAllFailedCount();
    if (!n) return;
    if (!confirm(`清理 ${n} 个全失败的记录？\n它们的 step 与产出会一并删除，无法恢复。`)) return;
    const r = await api('/api/workflows/prune-failed', { method: 'POST' });
    if (!r.ok) { const e = await r.json().catch(() => ({})); toast('清理失败：' + (e.detail || r.status)); return; }
    const d = await r.json().catch(() => ({}));
    toast(d.pruned ? `已清理 ${d.pruned} 个` : '没有可清理的记录');
    await loadWorkflowRuns(true);
    renderWorkflows();
  });

  const newBtn = $('#wfTplNew');
  if (newBtn) newBtn.addEventListener('click', () => editTemplate(null));

  $$('#page [data-wftplrun]').forEach(b => b.addEventListener('click', () => runTemplate(b.dataset.wftplrun)));
  $$('#page [data-wfseed]').forEach(b => b.addEventListener('click', () => {
    const seed = WF_TPL_SEEDS[+b.dataset.wfseed];
    if (seed) editTemplate(null, seed);
  }));
  $$('#page [data-wftpledit]').forEach(b => b.addEventListener('click', () => editTemplate(b.dataset.wftpledit)));
  $$('#page [data-wftpldel]').forEach(b => b.addEventListener('click', async () => {
    if (!confirm(`删除模板「${b.dataset.wftplname}」？`)) return;
    const r = await api(`/api/workflows/templates/${encodeURIComponent(b.dataset.wftpldel)}`, { method: 'DELETE' });
    if (!r.ok) { const e = await r.json().catch(() => ({})); toast('删除失败：' + (e.detail || r.status)); return; }
    toast('已删除');
    await loadWorkflowTemplates(true);
    renderWorkflows();
  }));
}

/* ── 模板 ──
   模板 = 可复用的编排定义存档；运行记录 = 一次执行痕迹。
   两者在 UI 上必须分得清 —— 用户看到"模板"就该知道点运行才会产生一条记录。 */

/* 起步模板：照自动任务页的 AUTO_TEMPLATES 做法，点一下把 JSON 填进编辑器。
   放在模板 tab 顶部当起步阶梯，用户不用自己从零写第一份定义。 */
const WF_TPL_SEEDS = [
  {
    icon: '📚', name: '文档巡检', desc: '并行读几处资料，再汇总要点',
    plan: {
      name: '文档巡检', goal: '把指定范围的资料过一遍，汇总成一份要点清单',
      stages: [
        { name: '并行阅读', steps: [
          { id: 'r1', name: '读文档', agent: 'doc-manager', goal: '读一遍目标目录里的文档，列出关键条目', parallel: true },
          { id: 'r2', name: '做调研', agent: 'researcher', goal: '调研同主题的外部资料，补齐缺失的视角', parallel: true },
        ]},
        { name: '汇总', steps: [
          { id: 's1', name: '合并要点', agent: 'doc-manager', goal: '把上面两份产出合并成一份要点清单', needs: ['r1', 'r2'] },
        ]},
      ],
    },
  },
  {
    icon: '🔍', name: '代码评审', desc: '先让 reviewer 独立看，再交叉复核',
    plan: {
      name: '代码评审', goal: '独立评审一遍改动，找出真实问题',
      stages: [
        { name: '评审', steps: [
          { id: 'a', name: '独立评审', agent: 'reviewer', goal: '独立审一遍当前改动，列出问题清单' },
        ]},
        { name: '复核', steps: [
          { id: 'b', name: '交叉复核', agent: 'reviewer', goal: '对照上一份结论复核，去掉站不住脚的意见', needs: ['a'] },
        ]},
      ],
    },
  },
];

function wfTemplatesHtml() {
  const list = S.wf.templates || [];
  return `
    <div class="sect"><h2>起步模板 · 点一下填进编辑器</h2>
      <div class="grid">${WF_TPL_SEEDS.map((t, i) => `
        <div class="tpl" data-wfseed="${i}">
          <div class="ti">${esc(t.icon)}</div><div class="tn2">${esc(t.name)}</div>
          <div class="td">${esc(t.desc)}</div>
        </div>`).join('')}</div>
    </div>
    <div class="sect"><h2>我的模板 · ${list.length}</h2>
      ${list.length ? `<div class="grid">${list.map(t => {
        const busy = S.wf.pending.has(t.id);
        const aliases = Array.isArray(t.aliases) ? t.aliases : [];
        return `<div class="skillcard">
          <div class="sh"><span class="sname">${esc(t.name || t.id)}</span>
            ${t.read_only ? '<span class="badge ro">只读</span>' : ''}
            <span class="badge">${t.stage_count || 0} 阶段 · ${t.step_count || 0} 步</span></div>
          ${t.description ? `<div class="sdesc">${esc(t.description)}</div>` : '<div class="sdesc"></div>'}
          <div style="font-size:11px;color:var(--muted);margin-top:6px">
            ${t.run_count ? `已跑 ${t.run_count} 次` : '还没跑过'}${t.last_run_at ? ' · 上次 ' + esc(wfAgo(t.last_run_at)) : ''}
          </div>
          <div class="sdesc" style="font-size:11px;color:var(--muted);margin-top:4px">${aliases.length ? '别名：' + esc(aliases.join('、')) : ''}</div>
          <div class="sact">
            <button class="btn primary" data-wftplrun="${esc(t.id)}" type="button" ${busy ? 'disabled' : ''}>${busy ? '运行中…' : '运行'}</button>
            <button class="btn" data-wftpledit="${esc(t.id)}" type="button">编辑</button>
            <button class="btn" data-wftpldel="${esc(t.id)}" data-wftplname="${esc(t.name || t.id)}" type="button">删除</button>
          </div>
        </div>`;
      }).join('')}</div>`
        : `<div class="empty">还没有模板<br><span style="font-size:11px">把一份编排定义存下来，就能一键重跑 —— 也可以从已完成的运行记录直接存</span></div>`}
    </div>`;
}

async function loadWorkflowTemplates(silent) {
  try {
    const d = await (await api('/api/workflows/templates')).json();
    S.wf.templates = d.templates || [];
  } catch (_) {
    S.wf.templates = [];
  }
  if (S.view === 'workflows' && !silent) renderWorkflows();
  return S.wf.templates;
}

/* 编辑器。templateId 为空就是新建。
   校验错误显示在弹层内的 .hint 里 —— toast 会自动消失，用户看不到细节。

   错误行放在**正文最上面**：放末尾的话，textarea 有 260px 高，
   错误就落在滚动区外 —— 用户点了保存却什么都没看见，
   还会以为没反应。放最上面则无论滚动到哪都在眼前。 */
function editTemplate(templateId, preset) {
  const load = templateId
    ? api(`/api/workflows/templates/${encodeURIComponent(templateId)}`).then(r => r.json())
        .then(d => d.content || {}).catch(() => ({}))
    : Promise.resolve({});
  load.then(d => {
    // preset 是 {name, description, plan} —— 取 plan，别把外层包装一起塞进去
    const plan = (preset && preset.plan) || d.plan || WF_TPL_SEEDS[0].plan;
    openModal({
      title: templateId ? '编辑模板' : '新建模板', wide: true,
      body: `<div class="hint" id="wtErr" style="color:var(--danger);display:none;margin-bottom:12px"></div>
        <div class="fld"><label for="wtName">模板名</label>
          <input class="inp" id="wtName" value="${esc(d.name || (preset && preset.name) || '')}" placeholder="如 文档巡检"/></div>
        <div class="fld"><label for="wtDesc">一句话说明（可留空）</label>
          <input class="inp" id="wtDesc" value="${esc(d.description || (preset && (preset.description || preset.desc)) || '')}" placeholder="这份编排解决什么问题"/></div>
        <div class="fld"><label for="wtAliases">别名（可留空，逗号分隔）</label>
          <input class="inp" id="wtAliases" value="${escAttr((Array.isArray(d.aliases) ? d.aliases : []).join('、'))}" placeholder="如 巡检、doc-check"/>
          <div class="hint">除了 id 与模板名，也能用别名取这份模板。</div></div>
        <div class="fld"><label for="wtPlan">编排定义（JSON）</label>
          <textarea class="inp" id="wtPlan" style="min-height:260px;font-family:var(--font-mono);font-size:12px">${esc(JSON.stringify(plan, null, 2))}</textarea>
          <div class="hint">结构：<span class="kbd">name</span> <span class="kbd">goal</span> <span class="kbd">stages[].name</span> <span class="kbd">stages[].steps[].{id,name,agent,goal,needs,parallel}</span>。
            同一阶段里的 step 会并行跑，<span class="kbd">needs</span> 指定依赖的上游 step id。</div></div>`,
      foot: `<button class="btn" data-close type="button">取消</button><button class="btn primary" id="wtSave" type="button">保存</button>`,
    });
    $('#wtSave').addEventListener('click', async () => {
      const name = $('#wtName').value.trim();
      const description = $('#wtDesc').value.trim();
      const aliases = ($('#wtAliases') && $('#wtAliases').value || '')
        .split(/[,，、;；]/)
        .map(s => s.trim())
        .filter(Boolean);
      const raw = $('#wtPlan').value;
      const showErr = msg => {
        const err = $('#wtErr');
        if (!err) return;
        err.textContent = msg;
        err.style.display = '';
        // 错误行在正文顶部，滚到那儿去 —— 否则用户只看到"点了没反应"
        err.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      };
      let plan;
      try {
        plan = JSON.parse(raw);
      } catch (e) {
        showErr('JSON 解析失败：' + e.message);
        return;
      }
      if (!name) { showErr('模板名不能为空'); return; }
      const payload = { name, description, plan, aliases };
      if (templateId) payload.id = templateId;
      const r = templateId
        ? await api(`/api/workflows/templates/${encodeURIComponent(templateId)}`, { method: 'PUT', body: JSON.stringify(payload) })
        : await api('/api/workflows/templates', { method: 'POST', body: JSON.stringify(payload) });
      if (!r.ok) {
        const e = await r.json().catch(() => ({}));
        showErr(e.detail || ('保存失败（' + r.status + '）'));
        return;
      }
      closeModal();
      toast(templateId ? '已保存' : '已创建');
      await loadWorkflowTemplates(true);
      renderWorkflows();
    });
  });
}

/* 把一次跑完的运行存成模板。plan 从后端取 —— 之前没落库，
   现在补存了 workflow_run_plans，所以这里拿得到。 */
async function saveRunAsTemplate(runId, runName) {
  let plan = null;
  try {
    const r = await api(`/api/workflows/${encodeURIComponent(runId)}/plan`);
    if (!r.ok) { const e = await r.json().catch(() => ({})); toast('取不到这次运行的编排：' + (e.detail || r.status)); return; }
    plan = (await r.json()).content;
  } catch (_) { toast('取不到这次运行的编排'); return; }
  editTemplate(null, { name: (runName || '未命名') + ' 副本', description: '', plan: plan });
}

/* 触发模板。阻塞语义（几分钟）—— 后端一次只能跑一个编排。
   期间按钮置 pending，避免用户以为没点上而连点。 */
async function runTemplate(templateId) {
  if (S.wf.pending.has(templateId)) return;
  S.wf.pending.add(templateId);
  renderWorkflows();
  try {
    const r = await api(`/api/workflows/templates/${encodeURIComponent(templateId)}/run`, {
      method: 'POST', body: JSON.stringify({}),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { toast('运行失败：' + (d.detail || r.status)); return; }
    // 报告落盘了就把路径给出来 —— 用户要能找到那份文件。
    // 不给路径等于白写了一份报告。
    if (d.report_path) {
      toast('工作流已完成，报告：' + d.report_path);
    } else if (d.status === 'done') {
      toast('工作流已完成');
    } else if (d.status === 'cancelled') {
      toast('工作流已中止');
    } else {
      toast('工作流结束：' + (d.status || '未知'));
    }
  } catch (e) {
    toast('运行失败：' + e.message);
  } finally {
    S.wf.pending.delete(templateId);
    await loadWorkflowRuns(true);
    await loadWorkflowTemplates(true);
    renderWorkflows();
  }
}

/* 运行中 run 的卡片：阶段 → step 树。用内存态渲染，好接增量事件 */
function wfRunCard(r) {
  const live = S.wf.live && S.wf.live.id === r.id ? S.wf.live : null;
  const stages = live ? live.stages : new Map();
  const total = live ? live.total : 0;
  const done = live ? live.doneCount : 0;
  return `<div data-wfrun="${esc(r.id)}">
    <div class="row" style="justify-content:space-between">
      <div style="min-width:0">
        <div style="font-size:13.5px;font-weight:600;color:var(--ink)">${esc(r.name || '未命名')}${wfBadge(r.status)}</div>
        <div style="font-size:11.5px;color:var(--muted);margin-top:3px">${esc(r.goal || '')}</div>
      </div>
      <button class="btn" data-wfcancel="${esc(r.id)}" type="button">中止</button>
    </div>
    <div class="wfbar"><i style="width:${total ? Math.round(done / total * 100) : 0}%"></i></div>
    <div class="wfsteps" data-wfsteps>${wfStagesHtml(stages)}</div>
  </div>`;
}
function wfStagesHtml(stages) {
  if (!stages || !stages.size) return '<div class="empty" style="padding:14px">正在准备编排…</div>';
  return [...stages.entries()].map(([name, g]) => `
    <div class="wfstage" data-wfstage="${esc(name)}">
      <div class="sh">${esc(name)}<span class="n">${g.doneCount}/${g.steps.size}</span></div>
      ${[...g.steps.values()].map(wfStepHtml).join('')}
    </div>`).join('');
}
function wfStepHtml(s) {
  const body = s.status === 'failed' && s.error
    ? `<div class="er">${esc(s.error)}</div>`
    : (s.summary ? `<div class="sm">${esc(s.summary)}</div>` : '');
  return `<div class="wfstep ${esc(s.status || '')}" data-wfstep="${esc(s.step_id)}" role="button" tabindex="0">
    <span class="dt"></span>
    <div class="bd">
      <div class="nm">${esc(s.name || s.step_id)}${wfBadge(s.status)}<span class="ag">${esc(cnAgent(s.agent))}</span>
        <span class="ms">${esc(wfDur(s.duration_ms))}</span></div>
      ${body}
    </div>
  </div>`;
}
function wfBindStep(el) {
  const toggle = () => el.classList.toggle('open');
  el.addEventListener('click', toggle);
  el.addEventListener('keydown', e => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
  });
}

/* ── SSE：增量更新（关键：绝不整页重绘） ── */
function wfLive_ensure(runId, name, goal, stageNames) {
  if (!S.wf.live || S.wf.live.id !== runId) {
    S.wf.live = {
      id: runId, name: name || '未命名', goal: goal || '',
      stages: new Map(), total: 0, doneCount: 0, status: 'running',
    };
    (stageNames || []).forEach(n => S.wf.live.stages.set(n, { steps: new Map(), doneCount: 0 }));
  }
  return S.wf.live;
}
function wfLive_stage(name) {
  const L = S.wf.live;
  if (!L) return null;
  if (!L.stages.has(name)) L.stages.set(name, { steps: new Map(), doneCount: 0 });
  return L.stages.get(name);
}
/** 事件到了：用户正看着这页就改单个节点，否则只记脏标记。 */
function wfLive_paint() {
  if (S.view !== 'workflows') { S.wf.dirty = true; return; }
  const L = S.wf.live;
  const host = $('#wfLive [data-wfrun="' + CSS.escape(L.id) + '"] [data-wfsteps]');
  if (!host) { S.wf.dirty = true; return; }
  host.innerHTML = wfStagesHtml(L.stages);
  $$('#wfLive [data-wfstep]').forEach(wfBindStep);
  const bar = $('#wfLive [data-wfrun="' + CSS.escape(L.id) + '"] .wfbar > i');
  if (bar) bar.style.width = (L.total ? Math.round(L.doneCount / L.total * 100) : 0) + '%';
}
function wfOnStarted(ev) {
  wfLive_ensure(ev.run_id, ev.run_name, ev.goal, ev.stages);
  S.wf.live.total = ev.total_steps || 0;
  S.wf.dirty = true;
  loadWorkflowRuns(true).then(() => { if (S.view === 'workflows') renderWorkflows(); });
}
function wfOnStage(ev) {
  const L = S.wf.live;
  if (!L || L.id !== ev.run_id) return;
  wfLive_stage(ev.stage);
  wfLive_paint();
}
function wfOnStep(ev) {
  const L = S.wf.live;
  if (!L || L.id !== ev.run_id) return;
  const g = wfLive_stage(ev.stage);
  if (!g) return;
  const prev = g.steps.get(ev.step_id);
  const status = ev.type === 'workflow.step.started' ? 'running' : (ev.status || 'done');
  // 同一 step 重复到达（补发）时不重复计数
  const counted = !prev || prev.status !== status;
  g.steps.set(ev.step_id, {
    step_id: ev.step_id, name: ev.step || ev.step_id, agent: ev.agent || '',
    status, summary: ev.summary || (prev && prev.summary) || '',
    error: ev.error || '', duration_ms: ev.duration_ms || 0,
  });
  if (status === 'done' || status === 'failed') {
    if (counted) { g.doneCount++; L.doneCount++; }
  }
  wfLive_paint();
}
function wfOnFinished(ev) {
  const L = S.wf.live;
  if (L && L.id === ev.run_id) { L.status = ev.status; L.endedAt = Date.now(); }
  S.wf.dirty = true;
  const label = ev.status === 'done' ? '已完成' : ev.status === 'cancelled' ? '已中止' : '失败';
  toast(`工作流${label}${ev.duration_ms ? ' · ' + wfDur(ev.duration_ms) : ''}`);
  loadWorkflowRuns(true).then(() => {
    // 运行中的卡片此时已不在 running 列表里，整页重绘一次把进度条收掉
    if (S.view === 'workflows') renderWorkflows();
  });
}

/* 详情页的 run 徽标：这里能拿到完整 step 列表，判据比列表页更准 */
function wfDetailBadge(d) {
  const steps = d.steps || [];
  const failed = steps.filter(s => s.status === 'failed').length;
  if (d.status === 'running' || !steps.length) return wfBadge(d.status);
  if (failed === steps.length) return `<span class="badge failed">全部失败</span>`;
  if (failed > 0) return `${wfBadge(d.status)}<span class="badge failed" style="margin-left:5px">${failed} 失败</span>`;
  return wfBadge(d.status);
}

/* ── 详情弹层 ── */
async function openWorkflowDetail(runId) {
  let d = null;
  try { d = (await (await api(`/api/workflows/${encodeURIComponent(runId)}`)).json()).content; } catch (_) {}
  if (!d) { toast('没有这次运行'); return; }
  // 按 stage 归组：后端给的是扁平 step 列表，阶段名在每条 step 上
  const groups = new Map();
  (d.steps || []).forEach(s => {
    const k = s.stage || '未分阶段';
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(s);
  });
  const dur = d.ended_at && d.created_at ? (d.ended_at - d.created_at) * 1000 : 0;
  // 这次的编排定义有没有留档 —— 没留档就没有「存为模板」可点。
  // 老记录是补不回来的，硬给个按钮点了只会弹一句报错。
  const hasPlan = !!d.has_plan;
  openModal({
    title: d.name || '工作流详情',
    wide: true,
    body: `<div class="row" style="margin-bottom:14px">
        ${wfDetailBadge(d)}
        <span style="font-size:12px;color:var(--muted)">${esc(wfDur(dur))} · ${esc(wfAgo(d.created_at))}</span>
      </div>
      ${d.goal ? `<div style="font-size:12.5px;color:var(--ink-2);line-height:1.7;margin-bottom:6px">${esc(d.goal)}</div>` : ''}
      ${Object.keys(d.args || {}).length ? `<div class="row" style="margin:8px 0 14px"><span style="font-size:11px;color:var(--muted)">参数</span><span class="tx" style="font-size:11.5px;color:var(--ink-2)">${esc(JSON.stringify(d.args))}</span></div>` : ''}
      ${groups.size ? [...groups.entries()].map(([name, ss]) => `
        <div class="wfstage">
          <div class="sh">${esc(name)}<span class="n">${ss.filter(x => x.status === 'done').length}/${ss.length}</span></div>
          ${ss.map(s => {
            const body = s.status === 'failed' && s.error
              ? `<div class="er">${esc(s.error)}</div>`
              : (s.output ? `<div class="sm">${esc(s.output)}</div>` : '');
            return `<div class="wfstep ${esc(s.status || '')}" role="button" tabindex="0">
              <span class="dt"></span>
              <div class="bd">
                <div class="nm">${esc(s.name || shortStepId(s.id))}${wfBadge(s.status)}<span class="ag">${esc(cnAgent(s.agent))}</span>
                  <span class="ms">${esc(wfDur(s.duration_ms))}</span></div>
                ${s.goal ? `<div class="sm" style="max-height:none;color:var(--muted)">${esc(s.goal)}</div>` : ''}
                ${body}
              </div></div>`;
          }).join('')}
        </div>`).join('')
        : '<div class="empty">这次运行还没有 step 记录</div>'}`,
    foot: `<button class="btn" data-close type="button">关闭</button>
      ${hasPlan ? '<button class="btn" id="wfSaveTpl" type="button">存为模板</button>' : ''}`,
  });
  $$('#modal .wfstep').forEach(el => {
    const t = () => el.classList.toggle('open');
    el.addEventListener('click', t);
    el.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); t(); } });
  });
  const saveBtn = $('#wfSaveTpl');
  if (saveBtn) saveBtn.addEventListener('click', () => {
    closeModal();
    saveRunAsTemplate(runId, d.name || '');
  });
}

S.appFilter = '';
S.localView = 'kb';
S.localCat = { kb: '', img: '', apps: '', pc: '' };   // 每个子视图各记一个分类，切回来不串
S.localGrid = 'grid';

/* Marvis 式页面头：分类 tab 在左，视图切换/搜索在右 */
function localHead(active, cats, rightExtra) {
  return `<div class="lhead">
    <div class="subtabs" role="tablist" aria-label="分类">
      ${cats.map(c => `<button role="tab" aria-selected="${c[0] === active}" class="${c[0] === active ? 'on' : ''}" data-lcat="${esc(c[0])}" type="button">${esc(c[1])}</button>`).join('')}
    </div>
    <div class="spacer"></div>
    ${rightExtra || ''}
  </div>`;
}

async function renderLocal() {
  const view = S.localView || 'kb';
  if (view === 'apps') return renderApps();
  if (view === 'img') return renderImages();
  if (view === 'pc') return renderPC();
  return renderKnowledge();
}

/* ───────── 知识库（Marvis 式：横幅 + 库卡片集合 + 检索） ───────── */
async function renderKnowledge() {
  const [ix, ws, doc, libs] = await Promise.all([
    api('/api/index/status').then(r => r.json()), api('/api/workspace').then(r => r.json()),
    api('/api/doc').then(r => r.json()).catch(() => ({ stats: {}, content: '' })),
    api('/api/index/libraries').then(r => r.json()).catch(() => ({ libraries: [] })),
  ]);
  const roots = ws.allowed_roots || [];
  const libraries = libs.libraries || [];
  const hasContent = (ix.files || 0) > 0 || !!(doc.content || '');
  await pageShell('知识库', '', `
    <div class="banner">
      <div class="bico" aria-hidden="true">📚</div>
      <div class="bmain">
        <h2>知识库</h2>
        <div class="bdesc">从文件中找到线索，然后基于知识库进行问答</div>
        <button class="blink" id="kbScope" type="button">授权范围：${esc(roots[0] || '未设置')}${roots.length > 1 ? ` 等 ${roots.length} 处` : ''} ›</button>
      </div>
      <div class="bact">
        <button class="btn" id="idxClear" type="button">清空索引</button>
        <button class="btn primary" id="rescan" type="button">＋ 重建知识库</button>
      </div>
    </div>

    <div class="lhead">
      <div class="subtabs" role="tablist" aria-label="知识库分类">
        <button role="tab" aria-selected="true" class="on" type="button">自建库 ${libraries.length}</button>
        <button role="tab" aria-selected="false" disabled title="按来源过滤 coming soon" type="button">订阅库</button>
        <button role="tab" aria-selected="false" disabled title="按来源过滤 coming soon" type="button">精选库</button>
      </div>
      <div class="spacer"></div>
      <div class="searchfield" style="max-width:340px">
        <svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>
        <input id="ixQ" placeholder="在知识库里查找…" aria-label="在知识库中检索" style="border:0;background:transparent;outline:none;flex:1;min-width:0;font-size:13px"/>
      </div>
    </div>

    ${!hasContent ? `
      <div class="bigempty">
        <div class="beico" aria-hidden="true">🗂</div>
        <div class="betitle">${roots.length ? '知识库还是空的，先重建一次索引' : '授权本地文件后即可建立知识库'}</div>
        <button class="btn primary" id="rescan2" type="button">＋ 新建知识库</button>
      </div>` : `
      ${libraries.length ? `<div class="sect"><h2>知识库 · ${libraries.length}</h2>
        <div class="grid kblibs">${libraries.map(l => `
          <div class="skillcard kblib">
            <div class="sh"><span class="sname">${esc(l.name)}</span>
              <span class="badge">${l.file_count} 个文件</span></div>
            <div class="sdesc" style="font-size:11.5px;color:var(--muted);word-break:break-all">${esc(l.path)}</div>
            <div class="kblibmeta">${esc(fsSize(l.total_bytes))} · 更新于 ${esc(fsAgo(l.last_mtime))}</div>
            <div class="kblibfiles">${(l.files || []).slice(0, 6).map(f => `
              <div class="kblibfile" title="${escAttr(f.path)}">
                <span class="kfdot" aria-hidden="true"></span>
                <span class="kfname">${esc(f.name)}</span>
                <span class="kfsize">${esc(fsSize(f.size))}</span>
              </div>`).join('')}
              ${l.file_count > 6 ? `<div class="kblibmore">还有 ${l.file_count - 6} 个…</div>` : ''}</div>
            <div class="sact">
              <button class="btn primary" data-kbchat="${escAttr(l.path)}" data-kbname="${escAttr(l.name)}" type="button">基于它提问</button>
              <button class="btn" data-kbchat="${escAttr(l.path)}" data-kbname="${escAttr(l.name)}" type="button" data-kbask="1" title="把这个库里的文件发给 Oryx">发给 Oryx</button>
            </div>
          </div>`).join('')}</div></div>` : ''}

      <div class="idxbar">
        <div class="ixscope">
          <span class="lb">授权范围</span>
          ${roots.map(r => `<span class="kbd">${esc(r)}</span>`).join(' ') || '<span class="kbd">未设置</span>'}
          <span class="hint">AI 功能按模型在您的设备上运行，不会自动上传您的文件。重建在后台进行，大文件多时需几分钟。</span>
        </div>
      </div>`}

    <div id="ixHits"></div>

    <div class="sect" style="margin-top:26px"><h2>项目文档</h2>
      <div class="card">
        <div class="row" style="margin-bottom:12px">
          ${Object.entries(doc.stats || {}).filter(([k]) => !['path', 'exists', 'bytes'].includes(k)).map(([k, v]) => `<span class="chip">${esc(DOC_STAT_CN[k] || k)} ${esc(String(v))}</span>`).join('') || '<span class="chip">还没有记录</span>'}
          <div class="spacer"></div>
          <button class="btn primary" id="docGo" type="button">在对话里维护</button>
          <button class="btn" id="docCopy" type="button">复制全文</button>
        </div>
        <div class="mdpre" style="max-height:320px;overflow:auto"><code>${esc((doc.content || '').slice(0, 4000) || '还没有内容。让 Oryx 在对话里记录需求、决策与变更，它会写到这里。')}</code></div>
      </div>
    </div>`);
  $('#kbScope').addEventListener('click', () => setView('settings'));
  $('#docGo').addEventListener('click', () => {
    $('#input').value = '把这次的需求、决策和改动记录到项目文档';
    setView('chat'); $('#input').focus();
  });
  $('#docCopy').addEventListener('click', async () => {
    const d = await (await api('/api/doc')).json();
    if (!d.content) { toast('项目文档还是空的'); return; }
    try { await navigator.clipboard.writeText(d.content); toast('已复制到剪贴板'); }
    catch (_) { toast('复制失败，可在上面手动选中'); }
  });
  // 知识库卡片：基于它提问 / 发给 Oryx
  $$('#page [data-kbchat]').forEach(b => b.addEventListener('click', () => {
    const path = b.dataset.kbchat;
    const name = b.dataset.kbname || '';
    $('#input').value = b.dataset.kbask
      ? `把「${name}」这个知识库里的文件读一遍并总结要点：${path}`
      : `基于「${name}」这个知识库回答我的问题，先看看里面有什么：${path}`;
    setView('chat'); $('#input').focus();
  }));
  $('#ixQ').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); runIxSearch(); } });
  $('#ixQ').addEventListener('input', () => { if ($('#ixQ').value.trim()) runIxSearch(); });
  async function runIxSearch() {
    const q = $('#ixQ').value.trim();
    const box = $('#ixHits');
    if (!q) { box.innerHTML = ''; return; }
    // 检索模式固定用混合 —— 用户不该被迫理解"全文 vs 语义"的区别，
    // 系统自己按内容挑最合适的路子就行。
    box.innerHTML = '<div class="empty">检索中…</div>';
    const r = await api('/api/index/search', { method: 'POST', body: JSON.stringify({ query: q, limit: 20, mode: 'hybrid' }) });
    if (!r.ok) { box.innerHTML = '<div class="empty">检索失败</div>'; return; }
    const d = await r.json();
    box.innerHTML = d.hits.length ? `<div class="ihits">${d.hits.map(h => `
      <div class="ihit">
        <div class="ih"><span class="ipath">${esc(h.path || h.file || '')}</span>
          ${h.score != null ? `<span class="iscore">得分 ${Number(h.score).toFixed(3)}</span>` : ''}</div>
        <div class="itx">${esc((h.text || h.snippet || '').slice(0, 400))}</div>
      </div>`).join('')}</div>` : '<div class="empty">没有命中</div>';
  }
  const startScan = async () => {
    if (!confirm('重建索引会重新扫描全部可访问目录，文件多时耗时较长。继续？')) return;
    toast('已开始重建…');
    const r = await api('/api/index/scan', { method: 'POST', body: JSON.stringify({}) });
    if (!r.ok) { const e = await r.json().catch(() => ({})); toast('启动失败：' + (e.detail || r.status)); return; }
    const poll = setInterval(async () => {
      try {
        const i = await (await api('/api/index/status')).json();
        if (!i.scanning) { clearInterval(poll); renderLocal(); toast('索引已更新'); }
      } catch (_) { clearInterval(poll); }
    }, 2000);
  };
  $('#rescan').addEventListener('click', startScan);
  $('#rescan2') && $('#rescan2').addEventListener('click', startScan);
  $('#idxClear').addEventListener('click', async () => {
    if (!confirm('清空索引库？之后需要重新扫描才能检索。')) return;
    const r = await api('/api/index/clear', { method: 'POST' });
    toast(r.ok ? '已清空' : '清空失败'); renderLocal();
  });
}

/* ───────── 应用库：图标网格 + 分类 tab ───────── */
/* 按名称猜分类，让「办公学习 / 影像娱乐 / 系统应用」这类 tab 有意义 */
const APP_CATS = [
  { key: 'office', label: '办公学习', words: ['office', 'word', 'excel', 'power', 'pdf', 'acrobat', 'wps', 'notion', 'onenote', 'outlook', 'visual studio', 'vscode', 'idea', 'pycharm', 'webstorm', 'git', 'node', 'python', 'java', 'eclipse', 'navicat', 'sql', 'postman', 'typora', 'obsidian', 'zoom', 'teams', 'slack', 'xmind', 'processon'] },
  { key: 'media', label: '影像娱乐', words: ['photoshop', 'illustrator', 'premiere', 'after effects', 'photos', 'movie', 'video', 'media', 'player', 'music', 'audio', 'netflix', 'bilibili', 'youku', 'qq音乐', '网易云', 'potplayer', 'vlc', 'obs', 'davinci', '剪映', 'filmora'] },
  { key: 'dev', label: '开发工具', words: ['docker', 'git', 'node', 'python', 'java', 'android', 'studio', 'sdk', 'api', 'postman', 'navicat', 'vmware', 'virtualbox', 'wsl', 'terminal', 'powershell', 'cmder', 'vscode', 'idea', 'pycharm', 'webstorm', 'eclipse', 'xcode', 'linux'] },
  { key: 'system', label: '系统应用', words: ['windows', 'microsoft', 'office', 'onedrive', 'edge', 'chrome', 'firefox', 'defender', 'security', 'update', 'installer', 'setup', 'driver', 'nvidia', 'amd', 'intel', 'realtek', 'audio', 'bluetooth'] },
];
function appCat(name) {
  const n = String(name || '').toLowerCase();
  for (const c of APP_CATS) if (c.words.some(w => n.includes(w))) return c.key;
  return 'other';
}
/* 应用图标：优先用真实图标（从 exe 里提取），取不到才退回首字母色块。
   图标一次性批量取（/api/apps/icons）—— 逐个请求的话 200 多个应用
   要发 200 多个请求，每个各起一个 PowerShell 进程，实测要 5 分半钟。

   **绝对不要退回到逐个请求**：
   之前这里给每个卡片挂了一个 /api/app/icon 的 <img>，批量接口只用来「替换」——
   但替换之前那 258 个请求已经发出去了。每个请求占一个线程池槽位约 1.1 秒，
   线程池（40 槽）被占满 → 同一时刻点的「打开应用」要排队等。
   实测：图标请求进行中点「打开」，最长要等 **96 秒**；
   空闲时只要 4~40 毫秒。差的是 2000 倍。

   所以现在只发一个批量请求，期间一律用首字母色块。 */
const APP_ICONS = {};   // 应用名 → data:image/png;base64,...
let appIconsLoaded = false;
let appIconsPromise = null;

function appIcon(a) {
  const letter = (a.name || '?').trim().charAt(0).toUpperCase();
  const hue = [...String(a.name || '')].reduce((s, ch) => s + ch.charCodeAt(0), 0) % 360;
  const data = APP_ICONS[a.name];
  if (data) {
    return `<span class="appicon" style="--h:${hue}"><img src="${escAttr(data)}" alt="" loading="lazy"/></span>`;
  }
  // 批量还没回来（或者这个应用压根没图标）：只显示首字母色块。
  // **不发单图标请求** —— 那会把后端线程池占满，点「打开」全排队。
  return `<span class="appicon" style="--h:${hue}"><span class="fall">${esc(letter)}</span></span>`;
}

/* 批量图标到位后，把还挂着色块的格子换成真图标。

   复用同一个 Promise：**重复进这个页面不要重复拉**。
   之前 appIconsLoaded 一置位就 return，但 appIconsLoaded 是在**请求发出前**
   置的 —— 第二次进来时批量数据可能还没回来，就永远等不到了（图标一直是色块）。
   现在用 appIconsPromise 记住进行中的请求。 */
async function loadAppIcons() {
  if (appIconsPromise) return appIconsPromise;
  appIconsPromise = (async () => {
    let icons = {};
    try {
      const r = await api('/api/apps/icons');
      const j = await r.json();
      icons = j.icons || {};
    } catch (e) {
      return;   // 取不到就维持首字母色块，不影响别的功能
    }
    if (!Object.keys(icons).length) return;
    Object.assign(APP_ICONS, icons);
    const grid = $('#appGrid');
    if (!grid) return;
    Array.from(grid.querySelectorAll('.appcard')).forEach(card => {
      const name = card.dataset.app;
      const data = APP_ICONS[name];
      if (!data) return;
      const span = card.querySelector('.appicon');
      if (!span) return;
      span.innerHTML = `<img src="${escAttr(data)}" alt="" loading="lazy"/>`;
    });
  })();
  return appIconsPromise;
}

async function renderApps() {
  const [d, cd] = await Promise.all([
    api('/api/apps?limit=300').then(r => r.json()).catch(() => ({ apps: [] })),
    api('/api/apps/custom').then(r => r.json()).catch(() => ({ apps: [] })),
  ]);
  const all = d.apps || [];
  const customs = cd.apps || [];
  const counts = {};
  all.forEach(a => { const k = appCat(a.name); counts[k] = (counts[k] || 0) + 1; });
  const cats = [['', `全部应用 ${all.length}`]]
    .concat(APP_CATS.filter(c => counts[c.key]).map(c => [c.key, `${c.label} ${counts[c.key]}`]))
    .concat(counts.other ? [['other', `其他 ${counts.other}`]] : []);

  await pageShell('应用库', '本机已安装的程序 —— 点图标直接打开，也能添加自己的应用', `
    ${localHead(S.localCat.apps || '', cats, `
      <div class="searchfield" style="max-width:200px">
        <svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>
        <input id="appQ" placeholder="搜索…" aria-label="搜索已安装应用" style="border:0;background:transparent;outline:none;flex:1;min-width:0;font-size:12.5px"/>
      </div>
      <button class="btn primary" id="appAddCustom" type="button">＋ 添加应用</button>`)}
    <div id="appCustomBox"></div>
    <div id="appGrid" class="appgrid"></div>`);

  const paint = () => {
    // 视图可能已经切走了（批量图标是异步的，回来时页面早换了）——
    // 这时 #appQ / #appGrid 都不在了，直接返回，别去读 null.value
    const qInput = $('#appQ');
    const grid = $('#appGrid');
    if (!qInput || !grid) return;
    const q = (qInput.value || '').trim().toLowerCase();
    const cat = S.localCat.apps || '';
    const list = all.filter(a =>
      (!cat || appCat(a.name) === cat) &&
      (!q || String(a.name).toLowerCase().includes(q)));
    grid.innerHTML = list.length ? list.map(a => `
      <div class="appcard" data-app="${escAttr(a.name)}" title="${escAttr(a.name)}${a.version ? ' · ' + escAttr(a.version) : ''}">
        ${appIcon(a)}
        <span class="appname">${esc(a.name)}</span>
        <span class="appact">
          <button class="btn" data-applaunch="${escAttr(a.custom_id || a.name)}" type="button" title="打开这个应用">打开</button>
          <button class="btn" data-appchat="${escAttr(a.name)}" type="button" title="让 Oryx 帮你用它">交给 Oryx</button>
        </span>
      </div>`).join('')
      : `<div class="empty">${q ? '没有匹配的应用' : '这个分类下还没有应用'}</div>`;
  };

  // 批量图标：不等它，界面先用色块渲染，图标到了再替换。
  loadAppIcons().then(() => { if (APP_ICONS && Object.keys(APP_ICONS).length) paint(); });

  // 自定义应用区 —— 用户自己添加的
  const paintCustom = () => {
    const box = $('#appCustomBox');
    if (!box) return;
    if (!customs.length) { box.innerHTML = ''; return; }
    box.innerHTML = `<div class="sect"><h2>我添加的 · ${customs.length}</h2>
      <div class="grid">${customs.map(c => `
        <div class="skillcard">
          <div class="sh"><span class="sname">${esc(c.name)}</span></div>
          <div class="sdesc" style="font-size:11.5px;color:var(--muted);word-break:break-all">${esc(c.target)}</div>
          ${c.note ? `<div class="sdesc">${esc(c.note)}</div>` : ''}
          <div class="sact">
            <button class="btn primary" data-customlaunch="${esc(c.id)}" type="button">打开</button>
            <button class="btn" data-customedit="${esc(c.id)}" type="button">编辑</button>
            <button class="btn" data-customdel="${escAttr(c.id)}" data-customname="${escAttr(c.name)}" type="button">删除</button>
          </div>
        </div>`).join('')}</div></div>`;
  };

  $$('#page [data-lcat]').forEach(b => b.addEventListener('click', () => {
    S.localCat.apps = b.dataset.lcat;
    $$('#page [data-lcat]').forEach(x => {
      const on = x === b;
      x.classList.toggle('on', on);
      x.setAttribute('aria-selected', String(on));
    });
    paint();
  }));
  $('#appQ').addEventListener('input', paint);

  // 打开应用 —— 等价双击桌面图标
  const launch = async id => {
    const r = await api('/api/apps/launch', { method: 'POST', body: JSON.stringify({ id }) });
    const j = await r.json().catch(() => ({}));
    toast(r.ok ? `已打开 ${j.launched || ''}` : ('打开失败：' + (j.detail || r.status)));
  };
  $('#appGrid').addEventListener('click', e => {
    const lb = e.target.closest('[data-applaunch]');
    if (lb) { launch(lb.dataset.applaunch); return; }
    const cb = e.target.closest('[data-appchat]');
    if (cb) {
      $('#input').value = `打开并使用应用「${cb.dataset.appchat}」`;
      setView('chat'); $('#input').focus();
    }
  });
  $('#appCustomBox').addEventListener('click', async e => {
    const lb = e.target.closest('[data-customlaunch]');
    if (lb) { launch(lb.dataset.customlaunch); return; }
    const eb = e.target.closest('[data-customedit]');
    if (eb) { editCustomApp(eb.dataset.customedit); return; }
    const db = e.target.closest('[data-customdel]');
    if (db) {
      if (!confirm(`删除「${db.dataset.customname}」？`)) return;
      const r = await api(`/api/apps/custom/${encodeURIComponent(db.dataset.customdel)}`, { method: 'DELETE' });
      toast(r.ok ? '已删除' : '删除失败');
      renderApps();
    }
  });
  $('#appAddCustom').addEventListener('click', () => editCustomApp(null));

  paint();
  paintCustom();
}

/* 添加/编辑自定义应用 */
function editCustomApp(id) {
  const load = id
    ? api(`/api/apps/custom`).then(r => r.json())
        .then(d => (d.apps || []).find(x => x.id === id) || {}).catch(() => ({}))
    : Promise.resolve({});
  load.then(d => {
    openModal({
      title: id ? '编辑应用' : '添加应用', wide: false,
      body: `<div class="fld"><label for="caName">应用名</label>
          <input class="inp" id="caName" value="${esc(d.name || '')}" placeholder="如 微信"/></div>
        <div class="fld"><label for="caTarget">程序路径（exe / lnk / 文件夹）</label>
          <div class="row" style="gap:7px">
            <input class="inp" id="caTarget" value="${esc(d.target || '')}" placeholder="C:\\Program Files\\..." style="flex:1"/>
            <button class="btn" id="caPick" type="button">浏览…</button>
          </div>
          <div class="hint">可以从开始菜单的快捷方式指向的 exe 里找；填错了保存时会提示。</div></div>
        <div class="fld"><label for="caNote">备注（可留空）</label>
          <input class="inp" id="caNote" value="${esc(d.note || '')}" placeholder="这个应用是干什么的"/></div>
        <div class="hint" id="caErr" style="color:var(--danger);display:none"></div>`,
      foot: `<button class="btn" data-close type="button">取消</button><button class="btn primary" id="caSave" type="button">保存</button>`,
    });
    $('#caPick').addEventListener('click', async () => {
      if (!HAS_TAURI) { toast('桌面端才能选文件，网页版请手动填路径'); return; }
      try {
        const picked = await window.__TAURI__.dialog.open({ directory: false, multiple: false, title: '选择应用' });
        if (picked) $('#caTarget').value = picked;
      } catch (e) { toast('选文件失败：' + e.message); }
    });
    $('#caSave').addEventListener('click', async () => {
      const payload = {
        name: $('#caName').value.trim(),
        target: $('#caTarget').value.trim(),
        note: $('#caNote').value.trim(),
      };
      if (id) payload.id = id;
      const r = id
        ? await api(`/api/apps/custom/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(payload) })
        : await api('/api/apps/custom', { method: 'POST', body: JSON.stringify(payload) });
      if (!r.ok) {
        const e = await r.json().catch(() => ({}));
        const err = $('#caErr');
        err.textContent = e.detail || ('保存失败（' + r.status + '）');
        err.style.display = '';
        return;
      }
      closeModal();
      toast(id ? '已保存' : '已添加');
      renderApps();
    });
  });
}

/* ───────── 图片库（对话图片 / 常用图片 / 最近图片） ───────── */
/* 分类只有三种，依据是**图的来源**而不是文件名关键词。
   先前那套「照片/票据扫描/图表插画」是按文件名里的英文词猜的，
   在 Windows 上根本匹配不到东西 —— 假的分类比没有分类更糟。

   来源由后端判定（oryx/imagegallery.py）：图标和小杂物在收集阶段就被挡掉，
   前端不用再猜。 */
const IMG_CATS = [
  { key: 'chat', label: '对话图片' },
  { key: 'common', label: '常用图片' },
  { key: 'recent', label: '最近图片' },
];
function imgMonth(ts) {
  const d = new Date((ts || 0) * 1000);
  return `${d.getFullYear()}年${d.getMonth() + 1}月`;
}
function thumbUrl(p, w) {
  return `/api/image/thumb?path=${encodeURIComponent(p)}&w=${w}&t=${encodeURIComponent(ORYX_TOKEN)}`;
}

async function renderImages() {
  const ws = await (await api('/api/workspace')).json().catch(() => ({ allowed_roots: [] }));
  const roots = ws.allowed_roots || [];
  const d = await (await api('/api/images?limit=400')).json().catch(() => ({ images: [], counts: {} }));
  const all = d.images || [];
  const counts = {};
  all.forEach(x => { const k = x.source || 'recent'; counts[k] = (counts[k] || 0) + 1; });
  const cats = [['', `全部 ${all.length}`]]
    .concat(IMG_CATS.filter(c => counts[c.key]).map(c => [c.key, `${c.label} ${counts[c.key]}`]));

  if (!roots.length) {
    await pageShell('图片库', '', `
      <div class="banner">
        <div class="bico" aria-hidden="true">🖼</div>
        <div class="bmain">
          <h2>图库</h2>
          <div class="bdesc">授权读取本地文件后，可按时间浏览并直接交给 Oryx 识别</div>
        </div>
        <div class="bact"><button class="btn primary" id="imgGo" type="button">＋ 立即授权</button></div>
      </div>
      <div class="bigempty">
        <div class="beico" aria-hidden="true">🔒</div>
        <div class="betitle">授权读取本地文件 · 体验完整功能<br/>AI 功能按模型在您的设备上运行，不会自动上传您的文件。</div>
        <button class="btn ghost" id="imgCfg" type="button">设置授权范围</button>
      </div>`);
    $('#imgGo').addEventListener('click', () => setView('settings'));
    $('#imgCfg').addEventListener('click', () => setView('settings'));
    return;
  }

  await pageShell('图片库', '', `
    <div class="lhead">
      <div class="subtabs" role="tablist" aria-label="图片分类">
        ${cats.map(c => `<button role="tab" aria-selected="${c[0] === (S.localCat.img || '')}" class="${c[0] === (S.localCat.img || '') ? 'on' : ''}" data-lcat="${esc(c[0])}" type="button">${esc(c[1])}</button>`).join('')}
      </div>
      <div class="spacer"></div>
      <div class="sizebox" title="缩略图大小">
        <span class="sbl">尺寸</span>
        <input id="imgSize" type="range" min="96" max="240" step="24" value="120" aria-label="缩略图尺寸"/>
        <span class="sbv" id="imgSizeV">120</span>
      </div>
      <div class="searchfield" style="max-width:220px">
        <svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>
        <input id="imgQ" type="search" placeholder="搜索图片…" aria-label="搜索图片" style="border:0;background:transparent;outline:none;flex:1;min-width:0;font-size:13px"/>
      </div>
    </div>
    <div id="imgBody"></div>`);

  const paint = () => {
    // 同应用库：视图可能已经切走，这时 #imgBody 不在了，直接返回
    const body = $('#imgBody');
    if (!body) return;
    const cat = S.localCat.img || '';
    const q = ($('#imgQ')?.value || '').trim().toLowerCase();
    const size = +($('#imgSize')?.value || 120);
    const list = all.filter(x => {
      if (cat && (x.source || 'recent') !== cat) return false;
      return !q || x.name.toLowerCase().includes(q) || (x.dir || '').toLowerCase().includes(q);
    });
    if (!list.length) {
      body.innerHTML = `<div class="bigempty">
        <div class="beico" aria-hidden="true">🖼</div>
        <div class="betitle">${q ? '没有匹配的图片' : '这个分类下还没有图片'}</div>
      </div>`;
      return;
    }
    const months = new Map();
    list.forEach(x => {
      const k = imgMonth(x.mtime);
      if (!months.has(k)) months.set(k, []);
      months.get(k).push(x);
    });
    body.innerHTML = [...months.entries()].map(([label, items]) => `
      <div class="monthgrp">
        <h3>${esc(label)}</h3>
        <div class="filmstrip" style="grid-template-columns:repeat(auto-fill, minmax(${size}px, 1fr))">${items.map(x => `
          <button class="thumb" data-img="${esc(x.path)}" title="${esc(x.name)}" type="button">
            <img src="${esc(thumbUrl(x.path, size * 2))}" alt="${esc(x.name)}" loading="lazy"
                 onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'fall',textContent:'${esc(x.ext.toUpperCase())}'}))"/>
            <span class="cap">${esc(x.name)}</span>
          </button>`).join('')}</div>
      </div>`).join('');
  };
  $('#imgSize').addEventListener('input', e => {
    $('#imgSizeV').textContent = e.target.value;
    paint();
  });
  $$('#page [data-lcat]').forEach(b => b.addEventListener('click', () => {
    S.localCat.img = b.dataset.lcat;
    $$('#page [data-lcat]').forEach(x => {
      const on = x === b;
      x.classList.toggle('on', on);
      x.setAttribute('aria-selected', String(on));
    });
    paint();
  }));
  $('#imgQ').addEventListener('input', paint);
  $('#imgBody').addEventListener('click', e => {
    const b = e.target.closest('[data-img]');
    if (!b) return;
    $('#input').value = `识别并处理这张图片：${b.dataset.img}`;
    setView('chat'); $('#input').focus();
  });
  paint();
}

/* ───────── 此电脑：磁盘 + 目录浏览器（Marvis 式三栏） ───────── */
const FS_ICON_DIR = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>';
const FS_ICON_FILE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/></svg>';
const FS_ICON_DISK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="3" y="5" width="18" height="6" rx="1.5"/><rect x="3" y="13" width="18" height="6" rx="1.5"/><circle cx="7" cy="8" r=".9" fill="currentColor" stroke="none"/><circle cx="7" cy="16" r=".9" fill="currentColor" stroke="none"/></svg>';

function fsSize(bytes) {
  const b = Number(bytes) || 0;
  if (!b) return '';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0, n = b;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n < 10 && i > 0 ? n.toFixed(1) : Math.round(n)} ${units[i]}`;
}
function fsAgo(ts) {
  if (!ts) return '';
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return '刚刚';
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 86400 * 30) return `${Math.floor(diff / 86400)} 天前`;
  return new Date(ts * 1000).toLocaleDateString();
}
/* 面包屑：把路径拆成可点的层级 */
function fsCrumbs(path) {
  const norm = String(path || '').replace(/\\/g, '/');
  const parts = norm.split('/').filter(Boolean);
  const out = [];
  // Windows 盘符单独一段
  if (parts.length && /^[a-zA-Z]:$/.test(parts[0])) {
    out.push({ label: parts[0], path: parts[0] + '/' });
    parts.shift();
  }
  let acc = out.length ? out[0].path : '/';
  parts.forEach(p => {
    acc = acc.replace(/\/$/, '') + '/' + p;
    out.push({ label: p, path: acc });
  });
  return out;
}

S.fs = { path: '', entry: null, drives: [], rows: [] };

async function renderPC() {
  const d = await (await api('/api/drives')).json().catch(() => ({ drives: [] }));
  const list = d.drives || [];
  S.fs.drives = list;
  // 默认落在第一个盘的根目录 —— 空着不好看，也让人以为这里只有磁盘列表
  if (!S.fs.path && list.length) S.fs.path = list[0].root;

  await pageShell('此电脑', '浏览本机磁盘与文件夹 —— 选中目录可设为工作区', `
    <div class="fswrap">
      <div class="fspane drives">
        <div class="fsh">磁盘</div>
        <div class="fsdrives">${list.map(dv => `
          <button class="fsdrive" data-fsroot="${escAttr(dv.root)}" type="button" title="${escAttr(dv.label || dv.name)}">
            <span class="dic" aria-hidden="true">${FS_ICON_DISK}</span>
            <span class="dn">${esc(dv.label || dv.name)}</span>
            <span class="dfree">${dv.free_gb} GB 空</span>
          </button>`).join('') || '<div class="empty" style="padding:8px">没有检测到磁盘</div>'}</div>
      </div>
      <div class="fspane files">
        <div class="fscrumb" id="fsCrumb"></div>
        <div class="card" style="padding:0;overflow:hidden">
          <div class="fslist" id="fsList"><div class="empty" style="padding:26px">正在读取…</div></div>
        </div>
        <div class="row" style="margin-top:12px;gap:8px">
          <button class="btn" id="fsUp" type="button">↑ 上一级</button>
          <button class="btn" id="fsRefresh" type="button">刷新</button>
          <span class="spacer"></span>
          <button class="btn primary" id="fsSetWs" type="button">设为工作区</button>
        </div>
        <div class="hint" style="font-size:11.5px;color:var(--muted);margin-top:10px">
          浏览目录结构不受限；但 Oryx 只能读取「授权范围」内的文件内容。设为工作区后，该目录会加入授权范围。</div>
      </div>
      <div class="fspane preview">
        <div class="fsh">详情</div>
        <div class="card" id="fsPreview"><div class="empty" style="padding:18px">选中一个条目看详情</div></div>
      </div>
    </div>`);

  $$('#page [data-fsroot]').forEach(b => b.addEventListener('click', () => {
    S.fs.path = b.dataset.fsroot;
    S.fs.entry = null;
    loadFsDir();
  }));
  $('#fsUp').addEventListener('click', () => {
    const c = fsCrumbs(S.fs.path);
    if (c.length > 1) { S.fs.path = c[c.length - 2].path; loadFsDir(); }
    else if (c.length === 1) { S.fs.path = c[0].path; loadFsDir(); }
  });
  $('#fsRefresh').addEventListener('click', () => loadFsDir());
  $('#fsSetWs').addEventListener('click', async () => {
    const path = S.fs.entry && S.fs.entry.is_dir ? S.fs.entry.path : S.fs.path;
    const r = await api('/api/workspace', { method: 'POST', body: JSON.stringify({ path }) });
    toast(r.ok ? `工作区已切换到 ${path}` : '切换失败');
    if (r.ok) refreshState();
  });
  $('#fsList').addEventListener('click', e => {
    const b = e.target.closest('[data-fsrow]');
    if (!b) return;
    const item = (S.fs.rows || [])[+b.dataset.fsrow];
    if (!item) return;
    S.fs.entry = item;
    paintFsPreview();
    if (item.is_dir) { S.fs.path = item.path; S.fs.entry = null; loadFsDir(); }
  });

  loadFsDir();
}

async function loadFsDir() {
  const list = $('#fsList');
  if (!list) return;
  const path = S.fs.path;
  if (!path) { list.innerHTML = '<div class="empty" style="padding:26px">左侧选一个磁盘</div>'; return; }

  // 面包屑
  const cr = fsCrumbs(path);
  const crumb = $('#fsCrumb');
  if (crumb) {
    crumb.innerHTML = cr.map((c, i) => {
      const sep = i ? '<span class="sep">›</span>' : '';
      return `${sep}<button data-fscrumb="${escAttr(c.path)}" type="button" title="${escAttr(c.path)}">${esc(c.label)}</button>`;
    }).join('');
    crumb.querySelectorAll('[data-fscrumb]').forEach(b => b.addEventListener('click', () => {
      S.fs.path = b.dataset.fscrumb; S.fs.entry = null; loadFsDir();
    }));
  }
  $$('#page [data-fsroot]').forEach(b => {
    const root = String(b.dataset.fsroot).replace(/\/+$/, '').toLowerCase();
    const cur = String(path).replace(/\/+$/, '').toLowerCase();
    b.classList.toggle('on', root === cur);
  });

  list.innerHTML = '<div class="empty" style="padding:26px">正在读取…</div>';
  let d;
  try {
    const url = `/api/fs/list?path=${encodeURIComponent(path)}&limit=500`;
    const r = await api(url);
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      list.innerHTML = `<div class="empty" style="padding:26px">${esc(e.detail || '读取失败')}</div>`;
      return;
    }
    d = await r.json();
  } catch (err) {
    list.innerHTML = `<div class="empty" style="padding:26px">读取失败：${esc(err.message)}</div>`;
    return;
  }

  const entries = d.entries || [];
  if (!entries.length) {
    list.innerHTML = '<div class="empty" style="padding:26px">这个目录是空的</div>';
    return;
  }
  // 条目数据放 JS 侧，DOM 里只放行号。
  // 不能把 JSON 塞进 data 属性：esc() 走 textContent 序列化，**不转义引号**
  // （innerHTML 只转义 & < >），于是 data-fspath="{...}" 会在第二个引号处
  // 截断成 "{"，点一下就 JSON.parse 炸掉。
  S.fs.rows = entries;
  list.innerHTML = entries.map((it, i) => `
    <button class="fsrow ${it.is_dir ? 'dir' : ''}" data-fsrow="${i}" type="button">
      <span class="fic" aria-hidden="true">${it.is_dir ? FS_ICON_DIR : FS_ICON_FILE}</span>
      <span class="fn" title="${escAttr(it.name)}">${esc(it.name)}</span>
      <span class="fsz">${it.is_dir ? '' : esc(fsSize(it.size))}</span>
      <span class="fsm">${esc(fsAgo(it.mtime))}</span>
    </button>`).join('')
    + (d.truncated ? '<div class="empty" style="padding:12px">条目太多，只显示了前一批</div>' : '');
}

function paintFsPreview() {
  const box = $('#fsPreview');
  if (!box) return;
  const it = S.fs.entry;
  if (!it) { box.innerHTML = '<div class="empty" style="padding:18px">选中一个条目看详情</div>'; return; }
  box.innerHTML = `
    <div class="fspv-icon" aria-hidden="true">${it.is_dir ? FS_ICON_DIR : FS_ICON_FILE}</div>
    <div class="fspv-name">${esc(it.name)}</div>
    <div class="fspv-row"><span>类型</span><span>${it.is_dir ? '文件夹' : (it.ext ? it.ext.toUpperCase() + ' 文件' : '文件')}</span></div>
    ${it.is_dir ? '' : `<div class="fspv-row"><span>大小</span><span>${esc(fsSize(it.size))}</span></div>`}
    <div class="fspv-row"><span>修改时间</span><span>${esc(fsAgo(it.mtime))}</span></div>
    <div class="fspv-row"><span>位置</span><span>${esc(it.path)}</span></div>
    <div class="row" style="margin-top:12px;gap:7px">
      <button class="btn" id="fsPvOpen" type="button">${it.is_dir ? '打开文件夹' : '交给 Oryx'}</button>
    </div>`;
  const open = $('#fsPvOpen');
  if (open) open.addEventListener('click', () => {
    if (it.is_dir) { S.fs.path = it.path; S.fs.entry = null; loadFsDir(); return; }
    $('#input').value = `处理这个文件：${it.path}`;
    setView('chat'); $('#input').focus();
  });
}

async function renderDashboard() {
  const [u, ss] = await Promise.all([
    api('/api/usage').then(r => r.json()),
    api('/api/sessions?limit=12').then(r => r.json()).catch(() => ({ sessions: [] })),
  ]);
  const t = u.today || {}, w = u.total || {};
  const sessions = ss.sessions || [];
  // 窗口如实标注：后端 today 是**过去 24 小时**，total 是**过去 30 天**，
  // 都不是自然日/自然月。以前这里写「本月」，月初时数字会和账单对不上 ——
  // 数字对不上，用户就不信这个面板。
  const winHours = u.window_hours || 720;
  await pageShell('用量面板', 'Token 消耗与调用次数', `
    <div class="sect"><h2>概览</h2><div class="grid">
      <div class="tile"><h3>近 24 小时调用</h3><p style="font-size:26px;font-weight:700;color:var(--text)">${t.calls || 0}</p></div>
      <div class="tile"><h3>近 24 小时 Token</h3><p style="font-size:26px;font-weight:700;color:var(--text)">${(((t.input_tokens || 0) + (t.output_tokens || 0)) / 1000).toFixed(1)}k</p></div>
      <div class="tile"><h3>近 ${Math.round(winHours / 24)} 天 Token</h3><p style="font-size:26px;font-weight:700;color:var(--text)">${(((w.input_tokens || 0) + (w.output_tokens || 0)) / 1000).toFixed(1)}k</p></div>
      <div class="tile"><h3>近 ${Math.round(winHours / 24)} 天调用</h3><p style="font-size:26px;font-weight:700;color:var(--text)">${w.calls || 0}</p></div>
    </div></div>
    <div class="sect"><h2>按模型</h2><div class="card"><table class="mx">
      <thead><tr><th>服务</th><th>模型</th><th>调用</th><th>输入</th><th>输出</th></tr></thead><tbody>
      ${(u.by_model || []).map(m => `<tr><td>${esc(m.provider)}</td><td>${esc(m.model)}</td><td>${m.calls}</td>
        <td>${((m.inp || 0) / 1000).toFixed(1)}k</td><td>${((m.outp || 0) / 1000).toFixed(1)}k</td></tr>`).join('') || '<tr><td colspan="5">还没有用量记录</td></tr>'}
    </tbody></table></div></div>
    <div class="sect"><h2>最近会话 · ${sessions.length}</h2>
      ${sessions.length ? `<div class="card" style="padding:8px 16px"><table class="mx">
        <thead><tr><th>标题</th><th>步骤</th><th></th></tr></thead><tbody>
        ${sessions.map(x => `<tr><td>${esc(x.title || '（空对话）')}</td>
          <td class="num">${x.step_count || 0}</td>
          <td style="text-align:right"><button class="btn" data-dashopen="${esc(x.id)}" type="button">打开</button></td></tr>`).join('')}
      </tbody></table></div>` : '<div class="empty">还没有会话记录</div>'}
    </div>`);
  $$('#page [data-dashopen]').forEach(b => b.addEventListener('click', () => openSession(b.dataset.dashopen)));
}

/* ══════ 模型管理 ══════
   这一块原来是「渲染时取一次快照 → 闭包里 paint 重画」，
   于是删掉的 provider 还显示着、切换了也不变 —— 快照是死的。
   现在改成：数据放在 S 上，每次改动后重新拉 + 重画。 */

/** 重新拉模型数据。改完任何一项都要调它，不然界面停在旧状态。 */
async function loadModelData() {
  const [pv, loc] = await Promise.all([
    api('/api/providers').then(r => r.json()).catch(() => ({ items: [] })),
    api('/api/providers/local').then(r => r.json()).catch(() => ({ models: [] })),
  ]);
  S.modelProviders = pv.items || [];
  S.modelActive = pv.active || '';
  S.localModels = loc.models || [];
  return S.modelProviders;
}

/** 本机已装但还没登记成 provider 的模型。 */
function unregisteredLocalModels() {
  const used = new Set(S.modelProviders.map(p => p.model));
  return S.localModels.filter(m => !used.has(m.name));
}

/** 本机已装的全部模型（含已登记的）。
    添加本地模型时下拉要列全 —— 只列「没登记过的」的话，
    用户想给同一个模型再配一条（比如换个端口）就没得选，只能手填。 */
function allLocalModels() {
  return S.localModels;
}

/** 一行 provider 的表格行。 */
function providerRow(it, isLocal) {
  const on = it.name === S.modelActive;
  return `<tr data-pvrow="${escAttr(it.name)}">
    <td>${esc(it.name)}${on ? ' <span class="badge done">使用中</span>' : ''}${isLocal ? ' <span class="badge">本地</span>' : ''}</td>
    <td>${esc(it.type)}</td>
    <td>${esc(it.model || '')}</td>
    <td>${esc(it.base_url || '')}</td>
    <td>${Math.round((it.capabilities?.context_window || 0) / 1000)}k</td>
    <td>${it.capabilities?.supports_tools ? '✓' : '—'}</td>
    <td>${it.capabilities?.supports_vision ? '✓' : '—'}</td>
    <td><div class="rowacts">
      ${on ? '' : `<button class="btn tiny" data-pvuse="${escAttr(it.name)}" type="button">启用</button>`}
      <button class="btn tiny" data-pvedit="${escAttr(it.name)}" type="button">编辑</button>
      <button class="btn tiny danger" data-pvdel="${escAttr(it.name)}" type="button">删除</button>
    </div></td></tr>`;
}

/** 模型管理区块的 HTML。 */
function modelSectionHtml() {
  const cloud = S.modelProviders.filter(p => !p.is_local);
  const local = S.modelProviders.filter(p => p.is_local);
  const pending = unregisteredLocalModels();
  const cols = `<thead><tr><th>名称</th><th>类型</th><th>模型</th><th>地址</th><th>上下文</th><th>工具</th><th>视觉</th><th></th></tr></thead>`;
  return `
    <div class="card modelcard">
      <h3>云端模型 <span class="badge">${cloud.length}</span></h3>
      <p class="lead">联网调用的大模型服务。填地址和密钥即可接入。</p>
      <table class="mx">${cols}<tbody>
        ${cloud.map(it => providerRow(it, false)).join('') || '<tr><td colspan="8" style="color:var(--muted)">还没有云端模型</td></tr>'}
      </tbody></table>
    </div>
    <div class="card modelcard">
      <h3>本地模型 <span class="badge">${local.length}</span></h3>
      <p class="lead">跑在本机的大模型。不联网，数据不出机器。</p>
      <table class="mx">${cols}<tbody>
        ${local.map(it => providerRow(it, true)).join('') || '<tr><td colspan="8" style="color:var(--muted)">还没有本地模型</td></tr>'}
      </tbody></table>
      ${pending.length ? `<div style="margin-top:14px">
        <div style="font-size:12.5px;color:var(--ink-2);margin-bottom:8px">本机已装、还没登记的服务：</div>
        <div class="picklist">
        ${pending.map(m => `<div class="pick" style="cursor:default">
          <span class="pk">◇</span>
          <span style="flex:1"><span class="pt">${esc(m.name)}</span>
            <div class="pd">本地 · ${esc(m.parameter_size || m.family || '')} ${esc(m.quantization || '')} · ${(m.size / 1024 / 1024 / 1024).toFixed(1)} GB</div></span>
          <button class="btn tiny" data-lmuse="${escAttr(m.name)}" type="button">登记并启用</button>
        </div>`).join('')}
        </div></div>` : ''}
    </div>
    <button class="addmodel-btn" id="pvAdd" type="button"><span class="plus">＋</span>添加模型</button>`;
}

/** 「添加模型」弹窗：先选云端还是本地，再填对应的字段。 */
function openAddModel() {
  openModal({
    title: '添加模型',
    body: `<div class="hint" id="pvErr" style="color:var(--danger);display:none;margin-bottom:12px"></div>
      <div class="fld"><label>模型来源</label>
        <div class="seg" id="pvKind" style="width:fit-content">
          <button data-kind="cloud" class="on" type="button">云端模型</button>
          <button data-kind="local" type="button">本地模型</button>
        </div>
        <div class="hint">云端模型联网调用，需要地址与密钥。本地模型跑在本机，只填地址即可。</div>
      </div>
      <div class="fld" id="pvLocalWrap" style="display:none">
        <label for="pvLocalModel">本机已装的模型</label>
        <select class="inp" id="pvLocalModel">
          ${allLocalModels().map(m => `<option value="${escAttr(m.name)}">${esc(m.name)} · ${esc(m.parameter_size || m.family || '')} · ${(m.size / 1024 / 1024 / 1024).toFixed(1)} GB</option>`).join('')
            || '<option value="">本机没探测到已装模型</option>'}
        </select>
        <div class="hint">没装的话先用 Ollama 拉一个。</div>
      </div>
      <div class="fld"><label for="pvName">名称</label>
        <input class="inp" id="pvName" placeholder="如 deepseek" aria-label="模型服务名称"/></div>
      <div class="fld"><label for="pvType">协议</label>
        <select class="inp" id="pvType"><option value="openai">openai 兼容</option><option value="anthropic">anthropic</option></select></div>
      <div class="fld"><label for="pvBase">接口地址</label>
        <input class="inp" id="pvBase" placeholder="https://api.example.com/v1" aria-label="接口基础地址"/></div>
      <div class="fld"><label for="pvModel">模型名</label>
        <input class="inp" id="pvModel" placeholder="如 deepseek-chat" aria-label="模型名称"/></div>
      <div class="fld" id="pvKeyWrap"><label for="pvKey">api_key</label>
        <input class="inp" id="pvKey" placeholder="密钥会存进系统凭据库" type="password" aria-label="API 密钥"/></div>`,
    foot: `<button class="btn" data-close type="button">取消</button><button class="btn primary" id="pvSave" type="button">添加</button>`,
  });
  let kind = 'cloud';
  const localWrap = $('#pvLocalWrap');
  const keyWrap = $('#pvKeyWrap');
  const baseInput = $('#pvBase');
  const modelInput = $('#pvModel');
  const nameInput = $('#pvName');
  $$('#modal [data-kind]').forEach(b => b.addEventListener('click', () => {
    kind = b.dataset.kind;
    $$('#modal [data-kind]').forEach(x => x.classList.toggle('on', x === b));
    localWrap.style.display = kind === 'local' ? '' : 'none';
    // 本地模型不需要密钥，藏掉免得用户填一堆没用的
    keyWrap.style.display = kind === 'local' ? 'none' : '';
    if (kind === 'local') {
      baseInput.placeholder = 'http://127.0.0.1:11434/v1';
      if (!baseInput.value) baseInput.value = 'http://127.0.0.1:11434/v1';
      const opt = $('#pvLocalModel');
      if (opt && opt.value) { modelInput.value = opt.value; if (!nameInput.value) nameInput.value = opt.value.replace(/[:/]/g, '-'); }
    } else {
      baseInput.placeholder = 'https://api.example.com/v1';
      if (baseInput.value === 'http://127.0.0.1:11434/v1') baseInput.value = '';
    }
  }));
  const localSel = $('#pvLocalModel');
  if (localSel) localSel.addEventListener('change', () => {
    modelInput.value = localSel.value;
    if (!nameInput.value) nameInput.value = localSel.value.replace(/[:/]/g, '-');
  });
  $('#pvSave').addEventListener('click', async () => {
    const showErr = msg => {
      const err = $('#pvErr');
      if (!err) return;
      err.textContent = msg;
      err.style.display = '';
    };
    const name = nameInput.value.trim();
    const base = baseInput.value.trim();
    const model = modelInput.value.trim();
    if (!name || !base || !model) { showErr('名称、地址、模型都要填'); return; }
    const btn = $('#pvSave');
    btn.disabled = true;
    try {
      if (kind === 'local') {
        // 本地模型：先探测有没有这台服务，再登记
        const resp = await api('/api/providers/local', {
          method: 'POST',
          body: JSON.stringify({ model, base_url: base }),
        });
        const r = await resp.json().catch(() => ({}));
        if (!resp.ok || !r.name) { showErr('登记失败：' + (r.detail || resp.status || '未知原因')); btn.disabled = false; return; }
        await api(`/api/providers/${encodeURIComponent(r.name)}/activate`, { method: 'POST' });
      } else {
        const resp = await api('/api/providers', {
          method: 'POST',
          body: JSON.stringify({ name, type: $('#pvType').value, base_url: base, model, api_key: $('#pvKey').value }),
        });
        if (!resp.ok) {
          const r = await resp.json().catch(() => ({}));
          showErr('添加失败：' + (r.detail || resp.status));
          btn.disabled = false;
          return;
        }
      }
      closeModal();
      await loadModelData();
      renderSettingsModel();
      refreshState();
      toast('已添加');
    } catch (err) {
      showErr('添加失败：' + err.message);
      btn.disabled = false;
    }
  });
}

/** 只重画设置页的「模型管理」那一块。
    增删改之后调它 —— 数据在 S 上，重画即最新。 */
function renderSettingsModel() {
  const main = $('#setMain');
  if (!main || S.view !== 'settings') return;
  // 只在模型管理页签上重画，别把别的页签的内容冲掉
  const cur = $('#page [data-sec].on');
  if (!cur || cur.dataset.sec !== 'model') return;
  main.innerHTML = `<h2>模型管理</h2><p class="lead">接入多个模型服务，随时切换</p>${modelSectionHtml()}`;
}

/** 环境体检：拉 /api/doctor 并渲染。
    自检是只读的 —— 不访问网络、不扫描用户文件。 */
function loadDoctor() {
  const card = $('#docCard');
  if (!card) return;
  api('/api/doctor')
    .then(r => r.json())
    .then(d => {
      const card2 = $('#docCard');
      if (!card2) return;   // 异步回来时视图可能已经切走了
      const checks = d.checks || [];
      const deps = d.dependencies || {};
      const depRows = Object.entries(deps);
      card2.innerHTML = `
        <div class="row" style="margin-bottom:12px">
          <span class="badge ${d.active_provider ? 'done' : 'L2'}">${d.active_provider ? '模型就绪' : '缺模型服务'}</span>
          <span style="font-size:12px;color:var(--muted)">${esc(d.active_provider || '未配置')} · Python ${esc(d.python || '?')}</span>
        </div>
        ${checks.map(c => `<div class="row" style="margin-top:9px;align-items:flex-start">
          <span class="badge ${c.ok ? 'done' : 'L2'}">${c.ok ? '正常' : '注意'}</span>
          <span style="font-size:12.5px;color:var(--ink-2);line-height:1.6"><b style="color:var(--ink)">${esc(c.name)}</b> —— ${esc(c.detail || '')}</span>
        </div>`).join('')}
        ${depRows.length ? `<div style="margin-top:16px;padding-top:14px;border-top:1px solid var(--line)">
          <div style="font-size:12px;color:var(--muted);margin-bottom:8px">可选依赖</div>
          ${depRows.map(([k, v]) => `<div class="row" style="margin-top:5px">
            <span class="badge ${v.installed ? 'done' : 'L1'}">${v.installed ? '已装' : '未装'}</span>
            <span style="font-size:12.5px;color:var(--ink-2)">${esc(v.purpose || k)} <span style="color:var(--muted)">(${esc(k)})</span></span>
          </div>`).join('')}
        </div>` : ''}
        ${(d.hints || []).length ? `<div style="margin-top:16px;padding-top:14px;border-top:1px solid var(--line)">
          <div style="font-size:12px;color:var(--muted);margin-bottom:8px">下一步可以做什么</div>
          ${d.hints.map(h => `<div style="font-size:12.5px;color:var(--ink-2);line-height:1.7;margin-top:5px">· ${esc(h)}</div>`).join('')}
        </div>` : ''}`;
    })
    .catch(() => {
      const card2 = $('#docCard');
      if (card2) card2.innerHTML = '<div class="empty">自检失败：后端没响应</div>';
    });
}

/** 编辑一条已保存的模型配置（云端 / 本地都行）。 */
function openEditModel(name) {
  const it = S.modelProviders.find(p => p.name === name);
  if (!it) return;
  openModal({
    title: '编辑模型 · ' + name,
    body: `<div class="hint" id="pvErr" style="color:var(--danger);display:none;margin-bottom:12px"></div>
      <div class="fld"><label for="pvType">协议</label>
        <select class="inp" id="pvType"><option value="openai">openai 兼容</option><option value="anthropic">anthropic</option></select></div>
      <div class="fld"><label for="pvBase">接口地址</label>
        <input class="inp" id="pvBase" value="${escAttr(it.base_url || '')}" aria-label="接口基础地址"/></div>
      <div class="fld"><label for="pvModel">模型名</label>
        <input class="inp" id="pvModel" value="${escAttr(it.model || '')}" aria-label="模型名称"/></div>
      <div class="fld"><label for="pvKey">api_key</label>
        <input class="inp" id="pvKey" placeholder="${it.has_key ? '已保存（留空表示不改）' : '密钥会存进系统凭据库'}" type="password" aria-label="API 密钥"/></div>
      <div class="hint">名称是这条配置的标识，改不了。要换名字就删了重新添加。</div>`,
    foot: `<button class="btn" data-close type="button">取消</button><button class="btn primary" id="pvSave" type="button">保存</button>`,
  });
  $('#pvType').value = it.type || 'openai';
  $('#pvSave').addEventListener('click', async () => {
    const showErr = msg => {
      const err = $('#pvErr');
      if (!err) return;
      err.textContent = msg;
      err.style.display = '';
    };
    const base = $('#pvBase').value.trim();
    const model = $('#pvModel').value.trim();
    if (!base || !model) { showErr('地址、模型都要填'); return; }
    const btn = $('#pvSave');
    btn.disabled = true;
    try {
      const resp = await api(`/api/providers/${encodeURIComponent(name)}`, {
        method: 'PUT',
        body: JSON.stringify({
          type: $('#pvType').value,
          base_url: base,
          model,
          api_key: $('#pvKey').value,
        }),
      });
      if (!resp.ok) {
        const r = await resp.json().catch(() => ({}));
        showErr('保存失败：' + (r.detail || resp.status));
        btn.disabled = false;
        return;
      }
      closeModal();
      await loadModelData();
      renderSettingsModel();
      refreshState();
      toast('已保存');
    } catch (err) {
      showErr('保存失败：' + err.message);
      btn.disabled = false;
    }
  });
}

async function renderSettings() {
  const [s, ws, ap, caps, feat, remote, mcp, sysw] = await Promise.all([
    api('/api/state').then(r => r.json()),
    api('/api/workspace').then(r => r.json()), api('/api/appearance').then(r => r.json()),
    api('/api/capabilities').then(r => r.json()), api('/api/features').then(r => r.json()).catch(() => ({ flags: [] })),
    api('/api/remote/status').then(r => r.json()).catch(() => ({ running: false })),
    api('/api/mcp').then(r => r.json()).catch(() => ({ servers: [], errors: [], warnings: [] })),
    api('/api/sys/switches').then(r => r.json()).catch(() => ({})),
  ]);
  S.sysSwitches = sysw || {};
  // MCP 清单单独存 —— 启停后要重画这一节
  S.mcp = mcp || { servers: [], errors: [], warnings: [] };
  // 模型数据单独拉 —— 它会被反复改动，不能用一次性的快照
  await loadModelData();
  const sections = [
    ['general', '通用设置', '⚙'],
    ['model', '模型管理', '◈'],
    ['feature', '功能开关', '⚑'],
    ['mcp', '扩展工具', '⧉'],
    ['system', '系统设置', '⊞'],
    ['workspace', '工作区', '▤'],
    ['appearance', '外观', '◐'],
    ['capability', '能力', '◫'],
    ['security', '安全', '⛨'],
    ['doctor', '环境体检', '✚'],
  ];
  $('#page').innerHTML = `
    <div class="phead"><h1>设置</h1><div class="spacer"></div><button class="btn" id="backBtn" type="button">返回对话</button></div>
    <div class="setwrap">
      <div class="setnav">${sections.map(([k, n, i]) => `<div class="it" data-sec="${k}"><span style="width:16px;text-align:center">${i}</span>${n}</div>`).join('')}</div>
      <div class="setmain" id="setMain"></div>
    </div>`;
  $('#backBtn').addEventListener('click', () => setView('chat'));
  const main = $('#setMain');
  const paint = (key) => {
    $$('#page [data-sec]').forEach(el => el.classList.toggle('on', el.dataset.sec === key));
    if (key === 'general') main.innerHTML = `
      <h2>通用设置</h2><p class="lead">默认行为与工作方式</p>
      <div class="card"><div class="row" style="margin-bottom:12px">
        <span style="width:110px">交互模式</span>
        <div class="seg" id="stInter">${[['ask','问答'],['plan','规划'],['craft','执行']].map(([k,n]) => `<button data-v="${k}" class="${s.interaction===k?'on':''}" type="button">${n}</button>`).join('')}</div>
      </div><div class="row" style="margin-bottom:12px"><span style="width:110px"><label for="stStyle">回答风格</label></span>
        <select class="inp" id="stStyle">${s.styles.map(x => `<option value="${x.name}" ${x.name===s.style?'selected':''}>${esc(x.label)}</option>`).join('')}</select>
      </div><div class="row"><span style="width:110px"><label for="stMode">计算模式</label></span>
        <select class="inp" id="stMode">
          <option value="efficiency" ${s.mode==='efficiency'?'selected':''}>效率模式（云端理解 + 本地索引）</option>
          <option value="local" ${s.mode==='local'?'selected':''}>本地模式（全本地，可断网）</option>
        </select></div>
        <div style="font-size:12px;color:var(--muted);margin-top:12px">问答与规划模式只给只读工具；执行模式开放全部工具，但删除、改设置、执行命令仍会弹确认。</div>
      </div>`;
    else if (key === 'model') main.innerHTML = `
      <h2>模型管理</h2><p class="lead">接入多个模型服务，随时切换</p>
      ${modelSectionHtml()}`;
    else if (key === 'feature') {
    const flags = feat.flags || [];
    const usable = flags.filter(f => f.implemented !== false);
    const planned = flags.filter(f => f.implemented === false);
    const on = usable.filter(f => f.enabled).length;
    main.innerHTML = `
      <h2>功能开关</h2><p class="lead">${on} / ${usable.length} 项已启用 · 关掉的能力模型就调不到</p>
      <div class="card" style="padding:16px">
        ${usable.map(f => `
          <button class="switchrow ${f.enabled ? 'on' : ''}" data-flag="${esc(f.key)}" data-val="${f.enabled ? '1' : '0'}" type="button">
            <span class="sw"></span>
            <span><span class="st">${esc(f.key.replace(/^(Enable|Disable)/, ''))}</span>
              <div class="sd">${esc(f.description || '')}</div></span>
            <span class="chip" style="margin-left:auto">${f.enabled ? '启用' : '关闭'}</span>
          </button>`).join('') || '<div class="empty">没有可调的特性开关</div>'}
      </div>
      ${planned.length ? `
      <div class="card" style="padding:16px;margin-top:12px;opacity:.72">
        <div style="font-size:12px;font-weight:600;margin-bottom:8px">规划中（本版本没有对应能力）</div>
        ${planned.map(f => `
          <div class="switchrow" style="cursor:default">
            <span class="sw" style="opacity:.35"></span>
            <span><span class="st">${esc(f.key.replace(/^(Enable|Disable)/, ''))}</span>
              <div class="sd">${esc(f.description || '')}</div></span>
            <span class="chip" style="margin-left:auto">未实现</span>
          </div>`).join('')}
      </div>` : ''}
      <div style="font-size:11.5px;color:var(--muted);margin-top:12px;line-height:1.7">
        配置文件：<span class="kbd">%APPDATA%/Oryx/features.yaml</span>（内容用 JSON 写，YAML 也能读）。<br>
        改动立即生效，重启也保留。标注「规划中」的能力本版本没有实现，因此不提供开关。</div>`;
  }
  else if (key === 'mcp') {
    const servers = S.mcp.servers || [];
    main.innerHTML = `
      <h2>扩展工具（MCP）</h2><p class="lead">挂载第三方工具服务，让 Oryx 多一项本事</p>
      <div class="card" style="padding:0">
        ${servers.length ? `<table class="mx">
          <thead><tr><th>名称</th><th>传输</th><th>地址</th><th>工具</th><th>状态</th><th></th></tr></thead>
          <tbody>${servers.map(x => `<tr data-mcprow="${escAttr(x.name)}">
            <td>${esc(x.name)}</td>
            <td>${esc(x.type)}</td>
            <td style="font-family:var(--font-mono);font-size:11.5px;max-width:280px;word-break:break-all">${esc(x.target || '')}</td>
            <td class="num">${x.live_tools || 0}${x.tool_count ? ` <span style="color:var(--muted)">/ ${x.tool_count}</span>` : ''}</td>
            <td>${x.running ? '<span class="badge done">运行中</span>' : (x.enabled ? '<span class="badge">已启用</span>' : '<span class="badge">未启用</span>')}</td>
            <td style="text-align:right"><button class="btn tiny" data-mcptoggle="${escAttr(x.name)}"
              data-on="${x.enabled ? '1' : '0'}" type="button">${x.enabled ? '停用' : '启用'}</button></td>
          </tr>`).join('')}</tbody></table>`
        : '<div class="empty">还没有配置 MCP 服务器<br><span style="font-size:11.5px;opacity:.7">在 .mcp.json 里登记后，这里会出现</span></div>'}
      </div>
      ${(S.mcp.errors || []).length ? `<div class="card" style="padding:14px;margin-top:12px;border-color:var(--warn)">
        <div style="font-size:12px;font-weight:600;margin-bottom:6px">启动时的错误</div>
        ${S.mcp.errors.map(e => `<div style="font-size:11.5px;color:var(--muted);font-family:var(--font-mono)">${esc(e)}</div>`).join('')}
      </div>` : ''}
      ${(S.mcp.warnings || []).length ? `<div class="card" style="padding:14px;margin-top:12px">
        <div style="font-size:12px;font-weight:600;margin-bottom:6px">配置提醒</div>
        ${S.mcp.warnings.map(e => `<div style="font-size:11.5px;color:var(--muted)">${esc(e)}</div>`).join('')}
      </div>` : ''}
      <div style="font-size:11.5px;color:var(--muted);margin-top:12px;line-height:1.7">
        配置文件：<span class="kbd">~/.oryx/.mcp.json</span>（用户级）、<span class="kbd">工作区/.mcp.json</span>（项目级）。<br>
        外部 MCP server 是可执行程序，<b>默认不启动</b> —— 必须在这里显式启用。启用后立即生效，不用重启。</div>`;
  }
  else if (key === 'system') {
    const groups = Object.entries(S.sysSwitches || {});
    main.innerHTML = `
      <h2>系统设置</h2><p class="lead">Oryx 能读、能改哪些系统设置 —— 只读项可以直接查，改动项在对话里走确认</p>
      ${groups.length ? groups.map(([gid, g]) => `
        <div class="sect"><h2>${esc(g.label || gid)}</h2>
          <div class="grid ucgrid">${(g.items || []).map(it => `
            <div class="tile">
              <h3>${esc(it.label || it.name)}</h3>
              <p>${it.action === 'read' ? '只读查询，点了立刻出结果' : '会改动系统设置，在对话里下指令并确认'}</p>
              <div class="foot">
                <span class="badge ${it.action === 'write' ? 'L2' : 'L0'}">${it.action === 'write' ? '需确认' : '只读'}</span>
                <span class="spacer"></span>
                ${it.action === 'read'
                  ? `<button class="btn tiny" data-sysq="${escAttr(it.name)}" type="button">查看</button>`
                  : `<button class="btn tiny" data-sysuse="${escAttr(it.label || it.name)}" type="button">在对话中使用</button>`}
              </div>
            </div>`).join('')}</div>
        </div>`).join('') : '<div class="empty">这台机器上没有可用的系统设置能力</div>'}
      <div class="card" id="sysOut" style="display:none;padding:14px;margin-top:12px">
        <div style="font-size:12px;font-weight:600;margin-bottom:8px" id="sysOutTitle">查询结果</div>
        <pre style="font-size:11.5px;line-height:1.7;white-space:pre-wrap;word-break:break-all;margin:0;color:var(--ink-2)"></pre>
      </div>
      <div style="font-size:11.5px;color:var(--muted);margin-top:12px;line-height:1.7">
        系统设置通过 Windows PowerShell 读取/修改。<b>写操作一律走 Agent 的 L2 审批</b> ——
        这里不提供「一点就改」的按钮，避免绕过执行计划与审计记录。</div>`;
  }
  else if (key === 'workspace') main.innerHTML = `
      <h2>工作区</h2><p class="lead">决定默认在哪些目录里工作</p>
      <div class="card"><div class="row">
        <input class="inp" id="wsPath" value="${esc(ws.workspace)}" aria-label="工作区目录路径" style="flex:1"/>
        <button class="btn primary" id="wsSave" type="button">切换</button></div>
        <div style="font-size:12px;color:var(--muted);margin-top:10px">允许访问：${ws.allowed_roots.map(r => `<span class="kbd">${esc(r)}</span>`).join(' ')}</div>
        <div style="font-size:12px;color:var(--muted);margin-top:8px">禁区（永不触碰）：${ws.forbidden_paths.map(r => `<span class="kbd">${esc(r)}</span>`).join(' ')}</div>
      </div>`;
    else if (key === 'appearance') main.innerHTML = `
      <h2>外观</h2><p class="lead">主题与自定义背景</p>
      <div class="card">
        <div class="fld"><label>主题</label>
          <div class="row"><div class="seg" id="stTheme">
            <button data-th="light" class="${ap.theme !== 'dark' ? 'on' : ''}" type="button">浅色</button>
            <button data-th="dark" class="${ap.theme === 'dark' ? 'on' : ''}" type="button">深色</button>
          </div></div>
        </div>
        <div class="fld"><label>背景图</label>
          <div class="row">
            <div id="bgPrev" style="width:150px;height:88px;border-radius:var(--r);border:1px solid var(--line);display:grid;place-items:center;font-size:11px;color:var(--muted);${ap.background_exists ? `background:url("/api/appearance/background.png?t=${encodeURIComponent(ORYX_TOKEN)}") center/cover no-repeat` : 'background:var(--bg-2)'}">${ap.background_exists ? '' : '未设置'}</div>
            <button class="btn" id="bgPick" type="button">选择图片</button>
            ${ap.background_exists ? '<button class="btn" id="bgClear" type="button">清除</button>' : ''}
            <input type="file" id="bgFile" accept="image/*" aria-label="选择背景图片文件" style="display:none"/>
          </div>
          <div class="hint">背景会铺满窗口，卡片保持半透明以保证可读性。</div>
        </div>
        ${ap.background_exists ? `<div class="fld"><label>平铺方式</label>
          <div class="bgmodes" id="bgModes">
            ${[['cover','填充','铺满并裁切'],['contain','完整','整张放下'],['tile','平铺','重复铺满'],['center','居中','原尺寸居中']]
              .map(([k, n, d]) => `<button class="bgmode ${(ap.background_fit || 'cover') === k ? 'on' : ''}" data-fit="${k}" type="button">${n}<small>${d}</small></button>`).join('')}
          </div>
        </div>` : ''}
      </div>`;
    else if (key === 'capability') {
    const allTools = caps.groups.flatMap(g => g.tools);
    main.innerHTML = `
      <h2>能力</h2><p class="lead">${caps.total} 个工具 · ${caps.agents.length} 个子 Agent · ${caps.groups.length} 个分组</p>
      <div class="row" style="margin-bottom:16px">
        <input class="inp" id="capQ" placeholder="搜工具，如截图 / 网页 / 表格" aria-label="搜索工具" style="flex:1"/>
        <select class="inp" id="capGroup"><option value="">全部分组</option>
          ${caps.groups.map(g => `<option value="${esc(g.group)}">${esc(cnGroup(g.group))}（${g.count}）</option>`).join('')}</select>
        <button class="btn primary" id="capGo" type="button">搜索</button>
      </div>
      <div class="grid ucgrid" id="capGrid"></div>`;
    const grid = $('#capGrid');
    // 工具详情：点标题或描述弹出来，能看完整说明、参数、风险
    const toolInfo = new Map();
    const paintT = ts => {
      // 记住这次渲染出来的工具，详情弹窗要用
      toolInfo.clear();
      ts.forEach(t => toolInfo.set(t.name, t));
      grid.innerHTML = ts.map(t => ucard({
        icon: cnTool(t.name).slice(0, 1),
        name: cnTool(t.name),
        id: t.name,
        badges: badge(t.risk),
        desc: t.description || '（无说明）',
        detail: 'tool:' + t.name,
        primary: { label: '在对话中使用', act: 'capuse', data: t.name },
      })).join('') || '<div class="empty">没有匹配的工具</div>';
      // 详情弹窗：名字 → 打开
      ts.forEach(t => {
        UC_DETAIL['tool:' + t.name] = () => {
          const info = toolInfo.get(t.name) || t;
          ucDetail(cnTool(t.name), [
            { label: '工具 id', value: info.name },
            { label: '风险等级', html: badge(info.risk) + ' ' + esc(RISK[info.risk] || '') },
            { label: '说明', value: info.description || '（无）' },
            { label: '分组', value: cnGroup(String(info.name).split('_')[0]) },
          ], `<button class="btn" data-close type="button">关闭</button>
              <button class="btn primary" id="capUseNow" type="button">在对话中使用</button>`);
          const b = $('#capUseNow');
          if (b) b.addEventListener('click', () => {
            closeModal();
            $('#input').value = '用 ' + cnTool(info.name) + ' 工具';
            setView('chat'); $('#input').focus();
          });
        };
      });
    };
    paintT(allTools);
    bindUCards(grid, (act, data) => {
      if (act !== 'capuse') return;
      $('#input').value = '用 ' + cnTool(data) + ' 工具';
      setView('chat'); $('#input').focus();
    });
    $('#capGo').addEventListener('click', async () => {
      const g = $('#capGroup').value;
      if (g) { const one = caps.groups.find(x => x.group === g); paintT(one ? one.tools : []); return; }
      const q = $('#capQ').value.trim();
      if (!q) { paintT(allTools); return; }
      const d = await (await api('/api/tools/search', { method: 'POST', body: JSON.stringify({ query: q, limit: 40 }) })).json();
      paintT(d.tools || []);
    });
  }
    else if (key === 'security') main.innerHTML = `
      <h2>安全</h2><p class="lead">风险分级、危险动作拦截与跨端远控</p>
      <div class="card" style="margin-bottom:16px">
        <div class="row"><span class="badge L0">只读</span><span>直接执行</span>
          <span class="badge L1">写入</span><span>可逆操作，执行并留痕</span></div>
        <div class="row" style="margin-top:9px"><span class="badge L2">危险</span><span>删除、覆盖、执行命令、改系统设置 —— 先出执行计划等你确认</span></div>
        <div class="row" style="margin-top:9px"><span class="badge L3">禁止</span><span>支付与凭据类操作，永不代执行</span></div>
        <div style="font-size:12px;color:var(--muted);margin-top:14px;line-height:1.7">
          规则表：<span class="kbd">core/oryx/rules/dangerous.yaml</span>（RCE / NET / DEL / SYS / PAY 五类）<br/>
          禁区（永不触碰）：${(ws.forbidden_paths || []).map(r => `<span class="kbd">${esc(r)}</span>`).join(' ')}</div>
      </div>
      <div class="card">
        <h3 style="margin:0 0 4px;font-size:14px;font-weight:650">跨端远控</h3>
        <p style="margin:0 0 14px;font-size:12.5px;color:var(--ink-2);line-height:1.7">
          让手机或另一台电脑通过中继连进来发指令。开启后请确认中继地址可信。</p>
        <div class="row" style="margin-bottom:12px">
          <span class="badge ${remote.running ? 'done' : 'L1'}" id="rmState">${remote.running ? '运行中' : '未启动'}</span>
          <span style="font-size:12px;color:var(--muted)" id="rmDetail">${esc(remote.pairing_code ? '配对码 ' + remote.pairing_code : (remote.relay || remote.detail || ''))}</span>
        </div>
        <div class="row">
          <input class="inp" id="rmUrl" placeholder="中继地址 relay://…" aria-label="中继地址" style="flex:1;min-width:200px"/>
          <select class="inp" id="rmPolicy" aria-label="远控权限等级"><option value="1">只读</option><option value="2" selected>可控</option><option value="3">完整</option></select>
          <button class="btn primary" id="rmStart" type="button">启动远控</button>
          <button class="btn" id="rmStop" type="button">停止</button>
        </div>
        <div class="hint" style="font-size:11.5px;color:var(--muted);margin-top:10px">权限档位决定远端能调用哪些工具，与本机「默认权限」相互独立。</div>
      </div>`;
    else if (key === 'doctor') {
    // 环境体检：一次性拉，结果直接渲染进卡片
    main.innerHTML = `<h2>环境体检</h2><p class="lead">这台机器上现在能做什么、缺什么</p>
      <div class="card" id="docCard"><div class="empty">正在检查…</div></div>`;
    loadDoctor();
  }
  };
  paint('general');
  $$('#page [data-sec]').forEach(el => el.addEventListener('click', () => paint(el.dataset.sec)));

  main.addEventListener('click', async (e) => {
    // 「添加模型」：一颗按钮，点了才弹窗
    if (e.target.closest('#pvAdd')) { openAddModel(); return; }
    // 系统设置：只读项直接查，结果就地展开（切走再回来不丢）
    const sysq = e.target.closest('[data-sysq]');
    if (sysq) {
      const btn = sysq, out = $('#sysOut');
      btn.disabled = true; btn.textContent = '读取中…';
      if (out) { out.style.display = 'block'; $('#sysOutTitle').textContent = '读取中…'; }
      try {
        const r = await api('/api/sys/query', {
          method: 'POST', body: JSON.stringify({ name: btn.dataset.sysq }),
        });
        const d = await r.json().catch(() => ({}));
        if (out) {
          $('#sysOutTitle').textContent = r.ok ? '查询结果' : '读取失败';
          out.querySelector('pre').textContent = JSON.stringify(d, null, 2);
        }
        if (!r.ok) toast('读取失败：' + (d.detail || r.status));
      } catch (err) {
        if (out) {
          $('#sysOutTitle').textContent = '读取失败';
          out.querySelector('pre').textContent = String(err && err.message ? err.message : err);
        }
        toast('读取失败');
      }
      btn.disabled = false; btn.textContent = '查看';
      return;
    }
    // 写操作不在设置页直接执行 —— 把意图带进对话，由 Agent 出执行计划
    const sysuse = e.target.closest('[data-sysuse]');
    if (sysuse) {
      $('#input').value = '帮我' + sysuse.dataset.sysuse;
      setView('chat'); $('#input').focus();
      toast('已填进输入框 —— 发送后会先出执行计划等你确认');
      return;
    }
    // MCP 服务器启停 —— 启用后立即重载，不用重启内核
    const mct = e.target.closest('[data-mcptoggle]');
    if (mct) {
      const name = mct.dataset.mcptoggle;
      const next = mct.dataset.on !== '1';
      mct.disabled = true;
      mct.textContent = '处理中…';
      try {
        const r = await api('/api/mcp/toggle', {
          method: 'POST',
          body: JSON.stringify({ name, enabled: next }),
        });
        if (!r.ok) {
          const x = await r.json().catch(() => ({}));
          toast('切换失败：' + (x.detail || r.status));
        } else {
          toast(next ? '已启用 ' + name : '已停用 ' + name);
        }
      } catch (err) {
        toast('切换失败：' + (err && err.message ? err.message : err));
      }
      // 重新拉清单并重画这一节 —— 启停后工具数与运行状态都会变
      S.mcp = await api('/api/mcp').then(x => x.json()).catch(() => S.mcp);
      paint('mcp');
      return;
    }
    // 编辑已保存的模型配置（云端 / 本地都走这一条）
    const pve = e.target.closest('[data-pvedit]');
    if (pve) { openEditModel(pve.dataset.pvedit); return; }
    // 启用某个已登记的模型
    const use = e.target.closest('[data-pvuse]');
    if (use) {
      await api(`/api/providers/${encodeURIComponent(use.dataset.pvuse)}/activate`, { method: 'POST' });
      await loadModelData();
      renderSettingsModel();
      refreshState();
      toast('已切换到 ' + use.dataset.pvuse);
      return;
    }
    // 本机已装、还没登记的本地模型：登记成 provider 后激活
    const lmuse = e.target.closest('[data-lmuse]');
    if (lmuse) {
      const model = lmuse.dataset.lmuse;
      lmuse.disabled = true;
      try {
        // api() 返回的是 Response，要自己解 JSON
        const resp = await api('/api/providers/local', { method: 'POST', body: JSON.stringify({ model }) });
        const r = await resp.json().catch(() => ({}));
        if (!resp.ok || !r.name) {
          toast('登记失败：' + (r.detail || resp.status || '未知原因'));
          lmuse.disabled = false;
          return;
        }
        await api(`/api/providers/${encodeURIComponent(r.name)}/activate`, { method: 'POST' });
        await loadModelData();
        renderSettingsModel();
        refreshState();
        toast('已切换到 ' + model);
      } catch (err) {
        toast('切换失败：' + err.message);
        lmuse.disabled = false;
      }
      return;
    }
    const pvd = e.target.closest('[data-pvdel]');
    if (pvd) {
      if (!confirm(`删除模型服务「${pvd.dataset.pvdel}」？`)) return;
      const r = await api(`/api/providers/${encodeURIComponent(pvd.dataset.pvdel)}`, { method: 'DELETE' });
      if (!r.ok) { const x = await r.json().catch(() => ({})); toast('失败：' + (x.detail || r.status)); return; }
      // 删完必须重新拉数据 —— 否则被删的那条还留在列表里
      await loadModelData();
      renderSettingsModel();
      refreshState();
      toast('已删除');
      return;
    }
    const flag = e.target.closest('[data-flag]');
    if (flag) {
      const key = flag.dataset.flag;
      const next = flag.dataset.val !== '1';
      flag.classList.toggle('on', next);
      flag.dataset.val = next ? '1' : '0';
      flag.querySelector('.chip').textContent = next ? '启用' : '关闭';
      const r = await api('/api/features/toggle', { method: 'POST', body: JSON.stringify({ key, value: next }) });
      if (!r.ok) {
        const x = await r.json().catch(() => ({}));
        flag.classList.toggle('on', !next); flag.dataset.val = next ? '0' : '1';
        flag.querySelector('.chip').textContent = next ? '关闭' : '启用';
        toast('无法调整：' + (x.detail || r.status));
        return;
      }
      toast(`${key.replace(/^(Enable|Disable)/, '')} 已${next ? '启用' : '关闭'}`);
      return;
    }
    if (e.target.id === 'rmStart') {
      const url = $('#rmUrl').value.trim();
      if (!url) { toast('先填中继地址'); return; }
      if (!confirm('开启后本机屏幕会推送到该中继地址，任何拿到配对码的人都能看到并操作这台电脑。确认继续？')) return;
      const r = await api('/api/remote/start', { method: 'POST', body: JSON.stringify({ relay_url: url, policy: +$('#rmPolicy').value }) });
      if (!r.ok) { const x = await r.json().catch(() => ({})); toast('启动失败：' + (x.detail || r.status)); return; }
      toast('远控已启动'); paint('security');
    }
    if (e.target.id === 'rmStop') {
      const r = await api('/api/remote/stop', { method: 'POST' });
      const x = await r.json().catch(() => ({}));
      toast(x.ok ? '远控已停止' : ('停止失败：' + (x.detail || ''))); paint('security');
    }
    const th = e.target.closest('#stTheme button');
    if (th) {
      $$('#stTheme button').forEach(x => x.classList.toggle('on', x === th));
      applyTheme(th.dataset.th);
      await api('/api/appearance/theme', { method: 'POST', body: JSON.stringify({ theme: th.dataset.th }) });
      return;
    }
    if (e.target.id === 'wsSave') {
      const r = await api('/api/workspace', { method: 'POST', body: JSON.stringify({ path: $('#wsPath').value.trim() }) });
      if (r.ok) { toast('工作区已切换'); paint('workspace'); } else { const x = await r.json().catch(() => ({})); toast('失败：' + (x.detail || r.status)); }
    }
    if (e.target.id === 'bgPick') $('#bgFile').click();
    if (e.target.id === 'bgClear') { await api('/api/appearance/background', { method: 'POST', body: JSON.stringify({ data_url: '' }) }); applyBg(); paint('appearance'); toast('背景已清除'); }
    const fit = e.target.closest('[data-fit]');
    if (fit) {
      $$('#bgModes [data-fit]').forEach(x => x.classList.toggle('on', x === fit));
      paintBg(fit.dataset.fit);
      await api('/api/appearance/background-fit', { method: 'POST', body: JSON.stringify({ fit: fit.dataset.fit }) });
      toast('平铺方式：' + fit.innerText.split('\n')[0]);
    }
  });
  main.addEventListener('change', async (e) => {
    if (e.target.id === 'stStyle') { await api('/api/style', { method: 'POST', body: JSON.stringify({ style: e.target.value }) }); refreshState(); }
    if (e.target.id === 'stMode') {
      await api('/api/mode', { method: 'POST', body: JSON.stringify({ mode: e.target.value }) });
      // 切模式换了 provider —— 顶栏芯片和模型列表都要跟着变
      await refreshState();
      await loadModelData();
      paint('general');
    }
    if (e.target.id === 'bgFile') {
      const f = e.target.files[0]; if (!f) return;
      const rd = new FileReader();
      rd.onload = async () => {
        const r = await api('/api/appearance/background', { method: 'POST', body: JSON.stringify({ data_url: rd.result }) });
        if (!r.ok) { const x = await r.json().catch(() => ({})); toast('失败：' + (x.detail || r.status)); return; }
        await applyBg(); paint('appearance'); toast('背景已更新');
      };
      rd.readAsDataURL(f); e.target.value = '';
    }
  });
  main.addEventListener('click', async (e) => {
    if (e.target.closest('#stInter button')) {
      const b = e.target.closest('#stInter button');
      await api('/api/interaction', { method: 'POST', body: JSON.stringify({ interaction: b.dataset.v }) });
      refreshState(); paint('general');
    }
  });
}

/* ══════ 主题 / 背景 ══════ */
function applyTheme(t) { document.documentElement.setAttribute('data-theme', t); }
const BG_FIT = {
  cover:   { size: 'cover',   pos: 'center', repeat: 'no-repeat' },
  contain: { size: 'contain', pos: 'center', repeat: 'no-repeat' },
  tile:    { size: 'auto',    pos: '0 0',    repeat: 'repeat' },
  center:  { size: 'auto',    pos: 'center', repeat: 'no-repeat' },
};
function paintBg(fit) {
  const f = BG_FIT[fit] || BG_FIT.cover;
  const root = document.documentElement;
  root.style.setProperty('--bg-size', f.size);
  root.style.setProperty('--bg-pos', f.pos);
  root.style.setProperty('--bg-repeat', f.repeat);
}
async function applyBg() {
  try {
    const i = await (await api('/api/appearance')).json();
    paintBg(i.background_fit);
    if (i.background_exists) {
      document.documentElement.style.setProperty('--bg-image', `url("/api/appearance/background.png?t=${encodeURIComponent(ORYX_TOKEN)}")`);
      document.body.classList.add('has-bg');
    } else {
      document.documentElement.style.setProperty('--bg-image', 'none');
      document.body.classList.remove('has-bg');
    }
  } catch (_) {}
}

/* ══════ 启动 ══════ */
(async () => {
  // 恢复上次拖过的栏宽与折叠状态
  for (const v of ['--rail-w', '--side-w']) {
    const saved = localStorage.getItem(v);
    if (saved) document.documentElement.style.setProperty(v, saved);
  }
  for (const [cls, tab] of [['rail-collapsed', '#railToggle'], ['side-collapsed', '#sideToggle']]) {
    if (localStorage.getItem(cls) === '1') {
      document.body.classList.add(cls);
      const t = $(tab); if (t) t.style.display = 'grid';
    }
  }
  // 本地资料现在是浮层 —— 启动时一律收起。
  // 之前是把「展开」状态存进 localStorage 再恢复，
  // 浮层不能开机就杵在屏幕上（挡住主区），所以不再恢复这个状态。
  setLocalOpen(false);
  try { const i = await (await api('/api/appearance')).json(); applyTheme(i.theme || 'light'); } catch (_) { applyTheme('light'); }
  applyBg();
  autoCollapse();
  fitTopbar();
  window.addEventListener('resize', fitTopbar);
  connect();
  await refreshState(); await loadSessions(); await loadTeams();
  await loadWorkflowRuns(true); await loadWorkflowTemplates(true);
  // 用量面板按需拉取，不做后台轮询
  setInterval(refreshState, 6000);
})();
