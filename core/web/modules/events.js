/* events.js —— 分区 13。
   由 core/scripts/split_web_modules.py 从 index.html 拆出。
   改动前先看 docs/FRONTEND-MODULES.md 的依赖说明。
 */

import { $, esc } from './core.js';

/* ══════ 事件 ══════ */
export function showThinking(label) {
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
export function setThinking(label) {
  const el = $('#thinkingRow');
  if (el && label) el.querySelector('.tlabel').textContent = label;
}
export function hideThinking() {
  const el = $('#thinkingRow');
  if (el) el.remove();
}
