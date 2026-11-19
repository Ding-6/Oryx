/* search.js —— 分区 4。
   由 core/scripts/split_web_modules.py 从 index.html 拆出。
   改动前先看 docs/FRONTEND-MODULES.md 的依赖说明。
 */

import { openSession, setView, viewSkill } from './models.js';
import { $, api, esc } from './core.js';

/* ══════ 全局搜索（会话 / 技能 / 工具） ══════ */
let searchTimer = null;
function closeSearch() {
  const box = $('#searchBox');
  if (box) { box.remove(); $('#search').value = ''; }
  $('#search').setAttribute('aria-expanded', 'false');
  searchIdx = -1;
}
let searchIdx = -1;   // 当前高亮项下标，-1 = 无高亮
function moveSearchIdx(step) {
  const rows = [...document.querySelectorAll('#searchBox [data-go]')];
  if (!rows.length) return;
  searchIdx = (searchIdx + step + rows.length) % rows.length;
  rows.forEach((r, i) => {
    r.classList.toggle('on', i === searchIdx);
    r.setAttribute('aria-selected', String(i === searchIdx));
  });
  rows[searchIdx].scrollIntoView({ block: 'nearest' });
}
function renderSearchResults(q, sessions, skills, tools) {
  let box = $('#searchBox');
  if (!box) {
    box = document.createElement('div');
    box.id = 'searchBox';
    box.className = 'searchbox';
    box.setAttribute('role', 'listbox');
    box.setAttribute('aria-label', '搜索结果');
    $('.railbox').appendChild(box);
  }
  searchIdx = -1;
  $('#search').setAttribute('aria-expanded', 'true');
  const rows = [];
  if (q) {
    const ms = sessions.filter(s => (s.title || '').toLowerCase().includes(q));
    rows.push(...ms.slice(0, 5).map(s =>
      `<div class="srow" role="option" aria-selected="false" tabindex="-1" data-go="session" data-id="${esc(s.id)}"><span class="sic" aria-hidden="true">💬</span><span class="stx">${esc(s.title || '（空对话）')}</span><span class="smeta">对话</span></div>`));
    const sk = skills.filter(x => (x.name + x.description).toLowerCase().includes(q));
    rows.push(...sk.slice(0, 5).map(x =>
      `<div class="srow" role="option" aria-selected="false" tabindex="-1" data-go="skill"><span class="sic" aria-hidden="true">⚡</span><span class="stx">${esc(x.name)}</span><span class="smeta">技能</span></div>`));
    const tl = tools.filter(x => (x.name + (x.description || '')).toLowerCase().includes(q));
    rows.push(...tl.slice(0, 6).map(x =>
      `<div class="srow" role="option" aria-selected="false" tabindex="-1" data-go="tool" data-tool="${esc(x.name)}"><span class="sic" aria-hidden="true">◈</span><span class="stx">${esc(x.name)}</span><span class="smeta">工具</span></div>`));
  }
  box.innerHTML = rows.length
    ? rows.join('')
    : '<div class="sempty" role="status">没有匹配的结果</div>';
  box.querySelectorAll('[data-go]').forEach(r => r.addEventListener('click', async () => {
    const kind = r.dataset.go;
    closeSearch();
    if (kind === 'session') { openSession(r.dataset.id); return; }
    if (kind === 'skill') { viewSkill(r.querySelector('.stx').innerText); return; }
    if (kind === 'tool') { $('#input').value = '用 ' + r.dataset.tool + ' 工具'; setView('chat'); $('#input').focus(); }
  }));
}
$('#search').addEventListener('input', e => {
  const q = e.target.value.trim().toLowerCase();
  clearTimeout(searchTimer);
  if (!q) { closeSearch(); return; }
  searchTimer = setTimeout(async () => {
    const [ss, sk, cp] = await Promise.all([
      api('/api/sessions').then(r => r.json()).catch(() => ({ sessions: [] })),
      api('/api/skills').then(r => r.json()).catch(() => ({ skills: [] })),
      api('/api/capabilities').then(r => r.json()).catch(() => ({ groups: [] })),
    ]);
    renderSearchResults(q, ss.sessions || [], sk.skills || [], (cp.groups || []).flatMap(g => g.tools || []));
  }, 220);
});
$('#search').addEventListener('keydown', e => {
  const box = $('#searchBox');
  const open = box && box.querySelector('[data-go]');
  if (e.key === 'ArrowDown') { e.preventDefault(); if (open) moveSearchIdx(searchIdx < 0 ? 1 : 1); return; }
  if (e.key === 'ArrowUp')   { e.preventDefault(); if (open) moveSearchIdx(-1); return; }
  if (e.key === 'Enter') {
    const rows = [...document.querySelectorAll('#searchBox [data-go]')];
    const pick = rows[searchIdx] || rows[0];
    if (pick) { e.preventDefault(); pick.click(); }
    return;
  }
  if (e.key === 'Escape') closeSearch();
});
document.addEventListener('click', e => {
  if (!e.target.closest('#searchBox') && !e.target.closest('.search')) closeSearch();
});
