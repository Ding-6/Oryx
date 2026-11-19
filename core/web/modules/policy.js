/* policy.js —— 分区 27。
   由 core/scripts/split_web_modules.py 从 index.html 拆出。
   改动前先看 docs/FRONTEND-MODULES.md 的依赖说明。
 */

import { closeModal, openModal } from './modal.js';
import { POLICY_DESC, POLICY_LABEL, paintAttbar } from './models.js';
import { $, $$, S, toast } from './core.js';

/* ══════ 默认权限 ══════ */
export function openPerm() {
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
