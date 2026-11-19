/* skillcall.js —— 分区 28。
   由 core/scripts/split_web_modules.py 从 index.html 拆出。
   改动前先看 docs/FRONTEND-MODULES.md 的依赖说明。
 */

import { openModal } from './modal.js';
import { paintAttbar } from './models.js';
import { $, $$, S, api, esc } from './core.js';

/* ══════ 调用技能 ══════ */
export async function openSkills() {
  const d = await (await api('/api/skills')).json();
  const list = d.skills || [];
  openModal({
    title: '调用技能',
    body: list.length ? `<div class="picklist">
      ${list.map(s => `
        <button class="pick ${S.skills.includes(s.name) ? 'on' : ''}" data-skill="${esc(s.name)}" type="button">
          <span class="pk">⚡</span>
          <span><span class="pt">${esc(s.name)}</span><div class="pd">${esc(s.description || '（无说明）')}</div></span>
        </button>`).join('')}
    </div>` : '<div class="empty">还没有技能。去技能广场新建一个。</div>',
    foot: `<button class="btn" data-close type="button">关闭</button>`,
  });
  $$('#modal [data-skill]').forEach(b => b.addEventListener('click', () => {
    const n = b.dataset.skill;
    const i = S.skills.indexOf(n);
    if (i >= 0) S.skills.splice(i, 1); else S.skills.push(n);
    b.classList.toggle('on', S.skills.includes(n));
    paintAttbar();
  }));
}
