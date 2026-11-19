/* modal.js —— 分区 22。
   由 core/scripts/split_web_modules.py 从 index.html 拆出。
   改动前先看 docs/FRONTEND-MODULES.md 的依赖说明。
 */

import { $, esc } from './core.js';

/* ══════ 弹层基础设施 ══════ */
let _modalOpener = null;   // 打开弹层前聚焦的元素，关闭时还回去
/* 弹层代号。每开一次 +1。
   用来判断「现在这个弹层还是我开的那一个吗」——
   openModal 复用同一个 #modal 元素，只比元素身份没用（永远是同一个）。
   确认弹窗里 onOk 经常会再开一个弹窗（比如删完团队重开另一个团队的面板），
   那时就不能再 closeModal，否则把刚开的那个也关了。 */
export let _modalGen = 0;
const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]):not([type="hidden"]),textarea:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])';
export function openModal({ title, body, foot, wide }) {
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
export function closeModal() {
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
