/* promptopt.js —— 分区 29。
   由 core/scripts/split_web_modules.py 从 index.html 拆出。
   改动前先看 docs/FRONTEND-MODULES.md 的依赖说明。
 */

import { closeModal, openModal } from './modal.js';
import { paintAttbar } from './models.js';
import { $, $$, S, api, cnSkill, esc, toast } from './core.js';

/* ══════ 提示词优化（弹层可编辑 + 可自选接入 skill） ══════ */
S.optSkills = null;          // null = 沿用对话已绑的 S.skills；数组 = 本弹层单独选

/** 打开「优化这句表达」弹层。
 *  导出成函数而不是绑死在按钮上 —— 按钮收进了「更多设置」菜单，
 *  入口位置变了，但这个能力本身不能消失。 */
export async function openPromptOpt() {
  const text = $('#input').value.trim();
  if (!text) { toast('先写一句要做什么，我再帮你优化'); $('#input').focus(); return; }

  const sk = await (await api('/api/skills')).json().catch(() => ({ skills: [] }));
  const allSkills = sk.skills || [];
  // 没单独选过时，默认沿用对话侧已绑的技能
  const picked = S.optSkills === null ? S.skills.slice() : S.optSkills.slice();
  let optimized = '', notes = [];
  try {
    const r = await api('/api/prompt/optimize', {
      method: 'POST',
      body: JSON.stringify({ text, skills: picked.join(','), expert: S.expert, style: S.style }),
    });
    const d = await r.json();
    optimized = d.optimized || ''; notes = d.notes || [];
  } catch (_) { optimized = ''; }
  if (!optimized) { toast('优化失败，可手动补充细节'); return; }

  const skillRows = allSkills.map(s => `
    <button class="pick ${picked.includes(s.name) ? 'on' : ''}" data-osk="${esc(s.name)}" type="button">
      <span class="pk">⚡</span>
      <span><span class="pt">${esc(cnSkill(s.name))}</span><div class="pd">${esc(s.description || '（无说明）')}</div></span>
    </button>`).join('');

  openModal({
    title: '优化后的提示词',
    wide: true,
    body: `
      <div class="fld"><label>原文</label><div class="opt-orig">${esc(text)}</div></div>
      <div class="fld">
        <label for="optText">优化版（可直接改）</label>
        <textarea class="inp" id="optText">${esc(optimized)}</textarea>
        <div class="hint">${notes.length ? '优化点：' + esc(notes.join('；')) : '补齐了目标、约束与验收标准。'}</div>
      </div>
      <div class="fld">
        <label>接入的 Skill<button class="lnk" id="optSkillReset" type="button">恢复默认</button></label>
        ${allSkills.length ? `<div class="picklist">${skillRows}</div>
          <div class="hint">勾选的技能会作为参考写进优化结果；优化与对话用的是同一个模型服务。</div>`
        : '<div class="empty">还没有技能。去技能广场新建一个。</div>'}
      </div>
      ${picked.length ? `<div class="fld"><label>本次将绑定</label>
        <div class="chips" id="optPicked">${picked.map(s => `<span class="chip">⚡ ${esc(cnSkill(s))}</span>`).join('')}</div></div>` : ''}`,
    foot: `<button class="btn" data-close type="button">取消</button><button class="btn primary" id="optApply" type="button">用这个版本</button>`,
  });

  // 勾选变化 → 重算优化结果，让用户看到 skill 接入的实际影响
  const repaint = () => {
    const box = $('#optPicked');
    if (box) box.innerHTML = picked.map(s => `<span class="chip">⚡ ${esc(cnSkill(s))}</span>`).join('');
  };
  const reopt = async () => {
    try {
      const r = await api('/api/prompt/optimize', {
        method: 'POST',
        body: JSON.stringify({ text, skills: picked.join(','), expert: S.expert, style: S.style }),
      });
      const d = await r.json();
      if (d.optimized) {
        $('#optText').value = d.optimized;
        const h = $('#optText').parentElement.querySelector('.hint');
        if (h) h.textContent = d.notes && d.notes.length ? '优化点：' + d.notes.join('；') : '补齐了目标、约束与验收标准。';
      }
    } catch (_) {}
  };
  $$('#modal [data-osk]').forEach(b => b.addEventListener('click', async () => {
    const n = b.dataset.osk;
    const i = picked.indexOf(n);
    if (i >= 0) picked.splice(i, 1); else picked.push(n);
    b.classList.toggle('on', picked.includes(n));
    repaint();
    await reopt();
  }));
  const rst = $('#optSkillReset');
  if (rst) rst.addEventListener('click', async () => {
    S.optSkills = S.skills.slice();
    picked.length = 0; picked.push(...S.skills);
    $$('#modal [data-osk]').forEach(x => x.classList.toggle('on', picked.includes(x.dataset.osk)));
    repaint();
    await reopt();
    toast('已恢复为对话侧绑定的技能');
  });

  $('#optApply').addEventListener('click', () => {
    // 勾选结果同步回对话，下一条消息就带着这些技能
    S.optSkills = picked.slice();
    S.skills = picked.slice();
    paintAttbar();
    $('#input').value = $('#optText').value;
    closeModal();
    toast(picked.length ? `已填入输入框 · 接入 ${picked.length} 个技能` : '已填入输入框');
  });
}
