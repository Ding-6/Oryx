/* layout.js —— 分区 2。
   由 core/scripts/split_web_modules.py 从 index.html 拆出。
   改动前先看 docs/FRONTEND-MODULES.md 的依赖说明。
 */

import { $, $$ } from './core.js';

/* ══════ 侧栏拖拽 / 折叠 ══════ */
const RAIL_MIN = 168, RAIL_MAX = 400;
const SIDE_MIN = 200, SIDE_MAX = 540;
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
/* 顶栏自适应：**量实际占位**，而不是猜一个像素阈值。
   以前是 `w < 860 ? 'tight' : ''` —— 阈值是拍脑袋定的，
   于是「主区 640px 但顶栏其实放得下」的时候，
   「专业」「效率」两个选择器也被藏了起来，
   用户**没法切回答风格和计算模式**。
   现在量一遍：所有可见控件的宽度 + gap，跟可用宽度比，
   放得下就一个都不藏。 */
export function fitTopbar() {
  const bar = $('.topbar'), center = $('#center');
  if (!bar || !center) return;
  const barCs = getComputedStyle(bar);
  // 可用宽度要**扣掉 topbar 自己的 padding** —— 它占的是主区的宽度
  const avail = center.getBoundingClientRect().width
    - (parseFloat(barCs.paddingLeft) || 0)
    - (parseFloat(barCs.paddingRight) || 0);
  if (!avail) return;
  // 量「真正常驻」的控件 —— 隐藏的（display:none）、占位符不算。
  //
  // **副标题（.sub，如「云端模型」）不参与测量** —— 它是**信息**不是控件，
  // 空间紧的时候该牺牲的是它（tight 模式下会隐藏）。
  // 把它算进需求，就会出现「因为放不下所以藏副标题，
  // 但因为副标题占着所以判定放不下」的循环。
  //
  // 宽 0 的（占位 spacer、窄屏才出现的「更多」按钮）也要排除 ——
  // 它们虽然不占宽，但**会占一个 gap**。
  const items = $$('.topbar > *').filter(el =>
    el.offsetParent !== null
    && el.getBoundingClientRect().width > 0
    && !el.classList.contains('sub'));
  if (!items.length) { bar.dataset.fit = ''; return; }
  const gap = parseFloat(getComputedStyle(bar).columnGap || '8') || 8;
  // **用 scrollWidth 而不是 rect.width** —— rect.width 是「收缩之后」的宽度，
  // 拿它算就成了循环：被压窄 → 看起来放得下 → 不收紧 → 其实放不下。
  // scrollWidth 是内容的自然宽度，才是「真需要多少地方」。
  const need = items.reduce((s, el) => {
    const rect = el.getBoundingClientRect().width;
    // **自然宽度要按 max-width 封顶** —— 对设了 max-width 的元素
    // （比如模型名胶囊）scrollWidth 是「内容有多长」，
    // 不是「它最多能占多宽」。按内容算会把需求算虚高，
    // 于是明明放得下也判定放不下，控件全被藏起来。
    const cs = getComputedStyle(el);
    const maxW = parseFloat(cs.maxWidth) || 0;
    let natural = el.scrollWidth || 0;
    if (maxW) natural = Math.min(natural, maxW);
    return s + Math.max(rect, natural);
  }, 0) + gap * (items.length - 1);
  const ratio = need ? avail / need : 2;
  bar.dataset.fit = ratio < 0.62 ? 'mini' : ratio < 0.82 ? 'ultra' : ratio < 0.99 ? 'tight' : '';
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
export function autoCollapse() {
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
