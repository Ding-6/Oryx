/* uiext.js —— 前端扩展加载器。
 *
 * 为什么有这个
 * ------------
 * 界面是零构建的 ES Module，入口只有 boot.js 里的静态 import。
 * 以前加一个界面模块 = 改 boot.js 加一行 —— 核心文件每加一个功能就被碰一次，
 * 改多了互相干扰。
 *
 * 现在：扩展放在用户目录（%APPDATA%/Oryx/ui-extensions/<id>/），
 * 由后端列出清单，这里动态 import 并挂载。**不用碰任何核心文件。**
 *
 * 扩展长什么样
 * ------------
 *   // %APPDATA%/Oryx/ui-extensions/my-ext/index.js
 *   export default {
 *     id: 'my-ext',
 *     mount: 'rail',            // rail | railfoot | topbar | side
 *     label: '我的面板',
 *     icon: '<svg ...>...</svg>',
 *     setup(ctx) {
 *       // ctx.$      选择器（只在扩展自己的 shadow root 内）
 *       // ctx.api    fetch 封装（自动带令牌）
 *       // ctx.on     订阅核心事件（view:changed / state:changed / …）
 *       // ctx.emit   发事件给核心与其他扩展
 *       // ctx.addStyle(css)  注入扩展自己的样式
 *       // ctx.mount  扩展的根节点（在 shadow root 里）
 *     },
 *   };
 *
 * 样式隔离
 * --------
 * 扩展的 DOM 与样式都放进**自己的 Shadow DOM**：
 *   · 扩展的 CSS 不会漏到核心界面（选择器再通用也出不去）
 *   · 核心的 CSS 不会漏进扩展（扩展想用核心的类名是不行的）
 *   · **CSS 自定义属性照常继承** —— `var(--ink)`、`var(--bg-2)` 在
 *     shadow root 里直接可用，暗色主题也跟着自动切。
 *     （自定义属性是继承的，不依赖选择器匹配，所以不需要额外桥接。）
 *
 * 事件总线
 * --------
 * 用 core.js 的 bus —— 扩展与核心 import 的是**同一个 core.js URL**，
 * 浏览器模块缓存保证是同一个实例，所以事件互相收得到。
 *
 * 安全边界
 * --------
 * 扩展拿不到令牌 —— 令牌走 **HttpOnly cookie**，JS 里根本没有它。
 * 后端服务扩展资源时也不注入 __ORYX_TOKEN__。
 *
 * 它只能通过 ctx.api 调后端 —— 而 ctx.api 走的是同源 cookie 鉴权，
 * 扩展**可以用**后端 API（这是设计如此），但**拿不到凭据本身**，
 * 所以没法把令牌外传给第三方。
 *
 * 扩展只从**用户目录**加载。仓库里自带的（clone 来的）不自动注入界面 ——
 * clone 一个仓库不该等于让它往你的界面里插代码。
 *
 * 能力（caps）
 * -----------
 * 扩展可以在清单里声明 `caps`，声明了哪些能力就只给哪些能力：
 * 调用需要别的能力的接口时，ctx.api 会**明确拒绝**并说清缺哪个。
 *
 * caps 的语义是「**特权清单**」不是「许可清单」——
 * 落进能力表的路径需要对应能力，其余路径（读状态、读清单）默认放行。
 * 反过来写（没声明就全禁）会把没写 caps 的老扩展全弄坏。
 *
 * **caps 不是安全边界，是契约。** 扩展跑在你的页面里，
 * 与核心同源 —— 它要是铁了心绕开 ctx.api 直接 fetch，
 * 前端拦不住（cookie 是浏览器自动带的）。caps 真正防的是
 * 「作者不小心越界」和「从别的扩展复制粘贴代码」，
 * 而真正的边界是：**扩展只从用户目录加载**，且**拿不到凭据本身**。
 */

import { $, $$, bus } from './core.js';

/* ══════ 能力（caps）══════ */

/*
 * 能力 → API 路径的映射表**不在前端**。
 *
 * 它由后端定义（`oryx/registry.py` 的 `UI_CAP_RULES`），
 * 随 `/api/ui/modules` 的清单一起下发 —— 这里是唯一来源。
 *
 * 为什么不给前端留一份：
 * 以前前后端各写了一份能力表，改了后端前端不知道，
 * 两边慢慢漂移，界面上「这个能力到底管哪些接口」没人说得准。
 * 现在后端改了，前端下次拉清单就跟上。
 *
 * 词表与后端扩展共用一套（`registry.CAPABILITY_TIERS`）——
 * 同一套词，界面扩展与后端扩展讲的是同一种话。
 *
 * caps 的语义是「**特权清单**」不是「许可清单」——
 * 落进能力表的路径 + 方法组合需要对应能力，
 * 其余路径（读状态、读清单）默认放行。
 *
 * **caps 不是安全边界，是契约。** 扩展跑在你的页面里，
 * 与核心同源 —— 它要是铁了心绕开 ctx.api 直接 fetch，
 * 前端拦不住（cookie 是浏览器自动带的）。caps 真正防的是
 * 「作者不小心越界」和「从别的扩展复制粘贴代码」，
 * 而真正的边界是：**扩展只从用户目录加载**，且**拿不到凭据本身**。
 */

/**
 * 这个 API 调用需要哪些能力。
 *
 * `rules` 是**后端下发的**能力表（`/api/ui/modules` 的 `cap_rules`），
 * 唯一来源在 `registry.UI_CAP_RULES` —— 前端不再自己写一份。
 * （以前两边各有一份，改了后端前端不知道，漂移了没人发现。）
 *
 * 表的结构是「能力 → 规则列表」，每条规则带自己的 methods 与 paths ——
 * 因为「同一个路径，不同方法，不同能力」是真的：
 * `GET /api/memory` 是读、`POST /api/memory` 是写、`DELETE` 是删。
 * 塞进一个 methods+paths 就必然歧义。
 *
 * 前缀匹配要精确：`/api/chat` 不该把 `/api/chatroom` 也算进来。
 * 所以带斜杠的前缀按前缀匹配，不带斜杠的按「整段」匹配。
 */
export function capsNeededFor(path, method, rules) {
  const p = String(path || '');
  const m = String(method || 'GET').toUpperCase();
  const need = [];
  for (const [cap, spec] of Object.entries(rules || {})) {
    for (const rule of (spec.rules || [])) {
      if (!(rule.methods || []).includes(m)) continue;
      for (const pre of rule.paths || []) {
        const hit = pre.endsWith('/')
          ? p.startsWith(pre)
          : (p === pre || p.startsWith(pre + '/'));
        if (hit) {
          // 同一条能力可能被多条规则命中 —— 只记一次
          if (!need.includes(cap)) need.push(cap);
          break;
        }
      }
    }
  }
  return need;
}

/**
 * 按扩展声明的能力给 api 加一层约束。
 *
 * 没声明 caps 的扩展**不限制** —— 那是对现状的保守解释：
 * caps 是后加的字段，老扩展没写，不该因为它就全哑了。
 * （但设置页会标出「未声明」，让人知道它等于全权。）
 */
export function scopedApi(api, caps, rules) {
  const declared = new Set((caps || []).map(c => String(c)));
  if (!declared.size) return api;
  return async (path, o = {}) => {
    const missing = capsNeededFor(path, o.method || 'GET', rules)
      .filter(c => !declared.has(c));
    if (missing.length) {
      return new Response(JSON.stringify({
        detail: `该扩展没有「${missing.join('、')}」能力，拒绝调用 ${path}`,
      }), {
        status: 403,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    return api(path, o);
  };
}

/**
 * 能力表变了没有 —— 变了的话，已挂载的扩展要按新表重新收窄。
 *
 * 只存个版本串（表的 JSON），不存表本身 ——
 * 表每次都随清单一起拿到，这里只是用来判断「变没变」。
 */
let capRulesVersion = '';


/* 挂载点 → 锚点选择器。
   锚点不在时扩展仍然加载，只是没有落脚点（记进报告，不静默丢）。

   注意 **rail 指向 .railgroup 而不是 .railnav** ——
   .railnav 里是 `<div class="nav" role="tablist">`，tablist 只能放 tab，
   塞普通按钮是语义错误（读屏器会当成「多了一个未选中的标签页」）。
   .railgroup 是 tablist 旁边的普通容器，放按钮才对。 */
const MOUNT_ANCHORS = {
  rail: '.railgroup',
  railfoot: '.railfoot',
  topbar: '.topbar',
  side: '#side',
};

/* ══════ 隔离容器 ══════ */

/**
 * 给扩展造一个隔离的挂载点。
 *
 * 返回 { host, root }：
 *   host —— 插到核心 DOM 里的宿主元素（一个空 div）
 *   root —— 扩展的 shadow root，扩展的 DOM 与样式都在里面
 */
function makeIsolatedHost(mount) {
  const sel = MOUNT_ANCHORS[mount] || MOUNT_ANCHORS.rail;
  let anchor = document.querySelector(sel);
  if (!anchor) anchor = document.querySelector('#rail') || document.body;
  if (!anchor) return null;

  const host = document.createElement('div');
  host.className = 'uiext-host';
  host.dataset.uiextHost = '1';
  // 宿主本身不吃布局（display:contents 让它不产生盒子）
  host.style.display = 'contents';
  anchor.appendChild(host);

  // attachShadow 失败（极老浏览器）→ 退回普通 div，功能不受影响，只是不隔离
  let root = host;
  try {
    root = host.attachShadow({ mode: 'open' });
  } catch (_) {
    root = host;
  }
  return { host, root };
}

/* ══════ 挂载 ══════ */

function mountExtension(ext, mod, api, baseCss) {
  const def = mod.default || mod;
  if (!def || typeof def.setup !== 'function') {
    return { id: ext.id, ok: false, reason: '扩展没有导出 setup()' };
  }
  const iso = makeIsolatedHost(ext.mount);
  if (!iso) return { id: ext.id, ok: false, reason: '找不到挂载点' };

  const { host, root } = iso;
  const styles = [];
  const cleanups = [];

  // 样式分两段：核心子集在前，扩展自己的在后 ——
  // 同一段里后写的覆盖先写的，所以扩展能覆盖核心的样式。
  const styleEl = document.createElement('style');
  styleEl.dataset.uiext = '1';
  if (baseCss) styleEl.textContent = baseCss;
  root.appendChild(styleEl);

  const ctx = {
    $: (s) => root.querySelector(s),
    $$: (s) => Array.from(root.querySelectorAll(s)),
    // api 是 core.js 的 fetch 封装（同源请求，浏览器自动带 HttpOnly cookie）。
    // 扩展**拿不到令牌本身** —— 令牌在 HttpOnly cookie 里，JS 读不到，
    // core.js 里也没有。所以它只能通过这里调后端，绕不过去，
    // 更没法把凭据外传给第三方。
    api,
    // 事件总线直接用 core.js 的那个 —— 同一个实例，核心发的事件收得到。
    on: bus.on,
    once: bus.once,
    emit: bus.emit,
    // 挂载点：扩展的根节点（在 shadow root 里）
    mount: root,
    host,
    id: ext.id,
    meta: ext,
    /** 注入扩展自己的样式（进 shadow root，不影响核心）。 */
    addStyle(css) {
      if (!css) return;
      styles.push(css);
      styleEl.textContent = (baseCss ? baseCss + '\n' : '') + styles.join('\n');
    },
    /** 注册清理函数 —— 扩展被热重载替换时会被调用。
     *
     * 为什么需要：扩展常在 document 上挂监听器
     * （document.addEventListener('keydown', ...)）。
     * 不清理的话，重新 setup 会**再挂一遍**，
     * 于是按一次键触发两次 —— 热重载几次就放大几次。
     */
    onCleanup(fn) {
      if (typeof fn === 'function') cleanups.push(fn);
    },
  };

  try {
    def.setup(ctx);
  } catch (e) {
    console.warn('[uiext] 扩展 setup() 出错', ext.id, e);
    return { id: ext.id, ok: false, reason: String(e && e.message || e) };
  }
  mounted.set(ext.id, { ext, host, cleanups, url: ext.url });
  return { id: ext.id, ok: true, mount: ext.mount, host };
}

/** 卸载一个扩展：跑它的清理函数 + 摘掉宿主节点。 */
function unmountExtension(id) {
  const rec = mounted.get(id);
  if (!rec) return false;
  for (const fn of rec.cleanups) {
    try { fn(); } catch (e) { console.warn('[uiext] 清理出错', id, e); }
  }
  rec.host?.remove();
  mounted.delete(id);
  return true;
}

/* ══════ 已挂载的扩展（用于热重载时卸载）══════════════ */

const mounted = new Map();   // id → { ext, host, cleanups }

/* ══════ 核心样式子集 ══════ */

/* 拉过的类名组合 → CSS 文本。
   多个扩展要同一批类时只拉一次。 */
const baseCssCache = new Map();

/**
 * 取核心样式的子集。
 *
 * 扩展在 Shadow DOM 里，核心的类名（.btn / .inp / .iconbtn）用不了。
 * 与其让扩展抄一遍样式（抄了不会跟着核心一起变），
 * 不如让它声明「我要用哪些类」，后端从 main.css 里挑原文出来。
 */
async function fetchBaseCss(api, classes) {
  const wanted = (classes || []).filter(Boolean);
  if (!wanted.length) return '';
  const key = wanted.slice().sort().join(',');
  if (baseCssCache.has(key)) return baseCssCache.get(key);
  try {
    const r = await api(`/api/ui/base-css?classes=${encodeURIComponent(key)}`);
    const css = r.ok ? (await r.text()) : '';
    baseCssCache.set(key, css);
    return css;
  } catch (_) {
    return '';
  }
}

/* ══════ 加载 ══════ */

/**
 * 加载并挂载全部前端扩展。
 *
 * @param {Function} api  core.js 的 api（带令牌）
 * @returns {Promise<{loaded: Array, failed: Array, skipped: Array}>}
 */
export async function loadUiExtensions(api) {
  const report = { loaded: [], failed: [], skipped: [] };
  let list;
  try {
    const r = await api('/api/ui/modules');
    if (!r.ok) {
      report.skipped.push({ reason: `清单接口返回 ${r.status}` });
      return report;
    }
    list = await r.json();
  } catch (e) {
    report.skipped.push({ reason: `清单接口不可用：${e && e.message || e}` });
    return report;
  }

  for (const s of (list.skipped || [])) {
    report.skipped.push(s);
  }

  // 能力表随清单一起下发（后端是唯一来源）
  const capRules = list.cap_rules || {};
  capRulesVersion = JSON.stringify(capRules);

  // 逐个加载 —— 一个扩展坏了不影响别的。
  for (const ext of (list.extensions || [])) {
    if (!ext.url) {
      report.failed.push({ id: ext.id, reason: '清单里没有 url' });
      continue;
    }
    const res = await loadOne(api, ext, capRules);
    if (res.ok) report.loaded.push(res);
    else report.failed.push(res);
  }

  // 扩展都挂好了 —— 通知彼此（有的扩展要等别人挂完才能做事）
  bus.emit('uiext:ready', { ids: report.loaded.map(x => x.id) });
  startAutoReload(api);
  return report;
}

/* ══════ 自动热重载 ══════ */

/**
 * 轮询清单，检测到扩展变了就重新加载它。
 *
 * 怎么做到「改了代码刷新一下就生效」的：
 *   1. 清单里的 URL 带**内容指纹**（`index.js?v=abc123`）
 *   2. 文件改了 → 指纹变 → URL 变
 *   3. `import()` 新 URL → 浏览器当成新资源，重新执行
 *
 * 浏览器会缓存 ES Module —— 同一个 URL 第二次 import 拿到的还是旧代码。
 * 指纹就是绕开这个缓存的钥匙。
 *
 * 卸载时会跑扩展注册的清理函数（`ctx.onCleanup`）——
 * 不清理的话，扩展在 document 上挂的监听器会重复注册，
 * 按一次键触发两次。
 */
let autoReloadTimer = null;

/** 加载单个扩展。 */
async function loadOne(api, ext, capRules) {
  let mod;
  try {
    mod = await import(ext.url);
  } catch (e) {
    return { id: ext.id, ok: false, reason: `加载失败：${e && e.message || e}` };
  }
  const baseCss = await fetchBaseCss(api, ext.base_css);
  // 按扩展**自己声明**的能力收窄 api ——
  // 没声明就不收窄（保守解释，见 scopedApi 的注释）。
  return mountExtension(ext, mod, scopedApi(api, ext.caps, capRules), baseCss);
}

function startAutoReload(api) {
  if (autoReloadTimer) return;
  const tick = async () => {
    try {
      const r = await api('/api/ui/modules');
      if (!r.ok) return;
      const list = await r.json();
      const next = new Map((list.extensions || []).map(e => [e.id, e]));
      const capRules = list.cap_rules || {};

      // 能力表变了 → 已挂载的扩展得按新表重新收窄。
      // ctx.api 是**挂载时**包出来的，能力表换了不重载就还用旧规则。
      const version = JSON.stringify(capRules);
      if (version !== capRulesVersion) {
        capRulesVersion = version;
        for (const id of [...mounted.keys()]) {
          const ext = next.get(id);
          if (!ext) continue;
          unmountExtension(id);
          const res = await loadOne(api, ext, capRules);
          if (!res.ok) console.warn('[uiext] 按新能力表重挂失败', id, res.reason);
        }
        return;   // 这一轮已经重挂完了
      }

      // 变了或新出现的 → 卸载旧的（如果有）再重新加载
      for (const [id, ext] of next) {
        const cur = mounted.get(id);
        if (cur && cur.url === ext.url) continue;   // 没变
        if (cur) {
          console.info('[uiext] 检测到扩展变更，重载：', id);
          unmountExtension(id);
        }
        const res = await loadOne(api, ext, capRules);
        if (!res.ok) console.warn('[uiext] 重载失败', id, res.reason);
      }
      // 消失的扩展 → 卸载
      for (const id of [...mounted.keys()]) {
        if (!next.has(id)) {
          console.info('[uiext] 扩展已移除，卸载：', id);
          unmountExtension(id);
        }
      }
    } catch (_) {
      // 轮询失败不重要 —— 下次再试
    }
  };
  autoReloadTimer = setInterval(tick, 5000);
}
