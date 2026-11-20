/* approval.js —— 分区 20。
   由 core/scripts/split_web_modules.py 从 index.html 拆出。
   改动前先看 docs/FRONTEND-MODULES.md 的依赖说明。
 */

import { $, api, esc } from './core.js';
import { closeModal, openModal } from './modal.js';

/* ══════ 确认弹窗 ══════
 *
 * 以前是一张**卡片插在聊天流里**（`.plan`），用户要往回翻消息才能找到。
 * 而且它永远留在那儿 —— 确认完也不消失，历史里堆着一堆「已确认 / 已取消」。
 *
 * 现在改成**弹窗**：
 *   · 弹出时自动聚焦，视线不用离开当前对话；
 *   · 允许或取消 → 弹窗关闭，不在聊天里留痕。
 *
 * **两个必须处理的细节**
 *
 * 1. **Esc 不能直接把弹窗关掉**
 *    modal.js 的全局 Esc 处理是「关掉弹窗」——
 *    对审批来说这就等于「用户跑了，内核还在那儿等着」，任务永远不结束。
 *    所以这里在**捕获阶段**拦下 Esc，当成「取消」处理。
 *
 * 2. **多个审批要排队**
 *    内核一次只等一个 step 的确认，但事件可能连着来几个。
 *    openModal 复用同一个 #modal 元素，同时开两个会互相覆盖 ——
 *    所以排成队列，一个一个弹。
 */

/** 待处理的审批（按到达顺序）。 */
const _queue = [];
/** 当前正在弹的那一个。 */
let _current = null;

export function showPlan(ev) {
  const stepId = ev.step_id;              // 每张卡自己持有 step_id，不再用全局变量
  const p = ev.plan || {};
  _queue.push({ stepId, plan: p });
  if (!_current) _next();
}

function _next() {
  const item = _queue.shift();
  if (!item) { _current = null; return; }
  const { stepId, plan: p } = item;
  _current = item;

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

  openModal({
    title: '需要你确认',
    wide: true,
    body: `<div class="s" style="margin-bottom:10px">${esc(p.summary || '')}</div>
      ${revNote}
      ${steps}`,
    foot: `<button class="btn" data-no type="button">取消</button><button class="btn primary" data-yes type="button">确认执行</button>`,
  });

  // Esc = 取消。
  //
  // 在**捕获阶段**拦：modal.js 的 Esc 处理挂在 document 的冒泡阶段，
  // stopPropagation 之后它就收不到了 ——
  // 否则用户随手一按 Esc，弹窗没了，任务却永远卡在等确认。
  const onKey = e => {
    if (e.key !== 'Escape') return;
    e.preventDefault();
    e.stopPropagation();
    document.removeEventListener('keydown', onKey, true);
    resolve(false);
  };
  document.addEventListener('keydown', onKey, true);

  const finish = ok => {
    document.removeEventListener('keydown', onKey, true);
    closeModal();
    _current = null;
    // 后端通知内核：这一步放行还是否决
    api('/api/approve', { method: 'POST', body: JSON.stringify({ step_id: stepId, approved: ok }) })
      .catch(() => { /* 通知失败内核自己会超时 */ })
      .finally(() => _next());
  };
  const resolve = ok => { if (_current === item) finish(ok); };

  $('#modal [data-no]')?.addEventListener('click', () => resolve(false));
  $('#modal [data-yes]')?.addEventListener('click', () => resolve(true));
}
