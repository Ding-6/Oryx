/* boot.js —— 分区 36。
   由 core/scripts/split_web_modules.py 从 index.html 拆出。
   改动前先看 docs/FRONTEND-MODULES.md 的依赖说明。
 */

import { connect } from './agui.js';
import { autoCollapse, fitTopbar } from './layout.js';
import { loadSessions, loadTeams, loadWorkflowRuns, loadWorkflowTemplates, openSession, refreshState, setLocalOpen, setView } from './models.js';
import { applyBg, applyTheme } from './theme.js';
import { $, api, applyClamp, bus, toast } from './core.js';
import { loadUiExtensions } from './uiext.js';
import './promptopt.js';
import './search.js';
import './terminal.js';
import './topbar-ui.js';
import './wiring.js';

/* ══════ 扩展 → 核心 的接线 ══════
 *
 * 前端扩展可以发这几个事件请求核心做事：
 *   ui:view     {view}    切换视图
 *   ui:session  {id}      切到某个会话
 *   ui:toast    {text}    弹一条提示
 *
 * 放在 boot.js（启动入口）里做接线 —— 这里是「谁负责响应」的天然位置，
 * 不用让 core.js 去 import 业务模块（core.js 要保持依赖根）。
 */
bus.on('ui:view', ({ view }) => { if (view) setView(view); });
bus.on('ui:session', ({ id }) => { if (id) openSession(id); });
bus.on('ui:toast', ({ text }) => { if (text) toast(text); });

/* ══════ 截断标记 ══════
 *
 * 卡片描述被截断时补一个省略号。
 *
 * 用 MutationObserver 而不是让每个页面各调一次 ——
 * 新加的卡片、重新渲染的列表都会自动跟上，
 * 以后谁写了个 `.clamp2` 也不用记得补调用。
 *
 * 观察整个 body 但只扫 `.clamp2`（几十个元素），
 * 代价可以忽略；用 rAF 推迟一帧 —— DOM 刚变完就量高度，
 * 字体还没排完，量出来的行数会不准。
 */
const clampObserver = new MutationObserver(() => {
  requestAnimationFrame(() => applyClamp(document));
});
clampObserver.observe(document.body, { childList: true, subtree: true });

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
  // 前端扩展：用户目录下的界面模块，不改核心文件就能加。
  // 放在最后 —— 核心先起来，扩展挂了也不影响主界面。
  try {
    const rep = await loadUiExtensions(api);
    if (rep.failed.length) console.warn('[uiext] 有扩展没挂上', rep.failed);
    if (rep.loaded.length) console.info('[uiext] 已挂载扩展', rep.loaded.map(x => x.id));
  } catch (e) {
    console.warn('[uiext] 扩展加载出错', e);
  }
  // 用量面板按需拉取，不做后台轮询
  setInterval(refreshState, 6000);
})();