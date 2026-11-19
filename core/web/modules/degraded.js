/* degraded.js —— 分区 17。
   由 core/scripts/split_web_modules.py 从 index.html 拆出。
   改动前先看 docs/FRONTEND-MODULES.md 的依赖说明。
 */

import { $, esc } from './core.js';

/* ══════ 能力降级可见化 ══════
   向量检索退回关键词、OCR 引擎缺失，以前都是静默发生的：
   用户以为自己在用语义检索，结果差了也不知道原因。
   这里把 capability.degraded 事件变成一条可关闭的常驻提示。 */
export const DEGRADED = new Map();
export function renderDegraded() {
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
