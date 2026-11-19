/* approval.js —— 分区 20。
   由 core/scripts/split_web_modules.py 从 index.html 拆出。
   改动前先看 docs/FRONTEND-MODULES.md 的依赖说明。
 */

import { $, api, esc } from './core.js';

/* ══════ 确认卡 ══════ */
export function showPlan(ev) {
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
