/* policy.js —— 分区 27。
   由 core/scripts/split_web_modules.py 从 index.html 拆出。
   改动前先看 docs/FRONTEND-MODULES.md 的依赖说明。
 */

import { closeModal, openModal } from './modal.js';
import { POLICY_DESC, POLICY_LABEL, paintAttbar, paintModeHint } from './models.js';
import { $, $$, DEFAULT_POLICY_KEY, S, getDefaultPolicy, setDefaultPolicy, toast } from './core.js';

/* ══════ 默认权限 ══════ */

/** 记住的默认权限档。
 *
 * 「默认权限」跟单轮的 S.policy 不是一回事：
 * 单轮覆盖发完消息就清掉了，默认权限得**一直管着后面的每一轮**。
 * 所以单独记一份（localStorage —— 这是个纯偏好，不需要上后端）。
 */
/** 把「默认权限」落到当前这一轮上。 */
export function applyDefaultPolicy() {
  const v = getDefaultPolicy();
  S.policy = v || null;
  paintAttbar();
  paintModeHint();
  return v;
}

export function openPerm() {
  // 四档权限的图标：◔ 只读 → ◑ 受控 → ◕ 完整 → ◉ 完全访问
  const ICON = { readonly: '◔', guarded: '◑', full: '◕', unrestricted: '◉' };
  openModal({
    title: '本次任务的权限',
    body: `<div class="picklist">
      ${Object.entries(POLICY_LABEL).map(([k, n]) => `
        <button class="pick ${S.policy === k ? 'on' : ''}" data-pol="${k}" type="button">
          <span class="pk">${ICON[k] || '◕'}</span>
          <span><span class="pt">${n}权限</span><div class="pd">${POLICY_DESC[k]}</div></span>
        </button>`).join('')}
    </div>
    <div class="hint" style="font-size:11.5px;color:var(--muted);margin-top:12px;line-height:1.65">
      只影响这一条消息。不选则沿用顶栏的「${({ ask: '问答', plan: '规划', craft: '执行' })[S.interaction] || S.interaction}」模式。<br>
      <b>完全访问</b>不会弹任何审批，直接执行；支付、凭据这类操作仍受系统硬规则拦截。
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
