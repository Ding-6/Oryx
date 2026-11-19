/* wiring.js —— 分区 30。
   由 core/scripts/split_web_modules.py 从 index.html 拆出。
   改动前先看 docs/FRONTEND-MODULES.md 的依赖说明。
 */

import { addFiles, applyScene, switchSideView, openSession, autoGrow, setView } from './models.js';
import { handlePaste, importFromClipboard } from './paste.js';
import { openPerm } from './policy.js';
import { openSkills } from './skillcall.js';
import { $, $$, api } from './core.js';
import { openModal, closeModal } from './modal.js';
import { openPromptOpt } from './promptopt.js';
import { applyTheme } from './theme.js';

/* ══════ 文件 / 权限 / 技能 芯片接线 ══════ */
$('#fileBtn').addEventListener('click', addFiles);
$('#skillBtn').addEventListener('click', openSkills);

/* 「更多设置」= 优化表达 / 调用技能 / 默认权限 三项的入口。
   设计稿的输入框底部只有三个按钮（上传文件 / 连接知识库 / 更多设置），
   但这三项功能一个都不能少 —— 收进一个菜单里，
   既贴合设计稿，又不丢能力。 */
$('#moreBtn').addEventListener('click', () => {
  openModal({
    title: '更多设置',
    body: `<div class="picklist">
      <button class="pick" data-more-item="opt" type="button">
        <span class="pk">✦</span>
        <span><span class="pt">优化这句表达</span><div class="pd">把这句话改得更清楚、更可执行</div></span>
      </button>
      <button class="pick" data-more-item="skill" type="button">
        <span class="pk">⚡</span>
        <span><span class="pt">调用技能</span><div class="pd">为这一轮绑定技能</div></span>
      </button>
      <button class="pick" data-more-item="perm" type="button">
        <span class="pk">🛡</span>
        <span><span class="pt">默认权限</span><div class="pd">设置这一轮的动作权限</div></span>
      </button>
    </div>`,
    foot: `<button class="btn" data-close type="button">关闭</button>`,
  });
  $$('#modal [data-more-item]').forEach(b => b.addEventListener('click', () => {
    const k = b.dataset.moreItem;
    closeModal();
    if (k === 'opt') openPromptOpt();
    else if (k === 'skill') openSkills();
    else if (k === 'perm') openPerm();
  }));
});

// 输入框顶部的场景预设：一键切这一轮用什么能力
$$('#sceneBar .scene').forEach(b => {
  b.addEventListener('click', () => applyScene(b.dataset.scene));
});

// ── 顶栏 ──
// 主题切换：明 / 暗
$('#themeBtn').addEventListener('click', async () => {
  const cur = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
  applyTheme(cur);
  try {
    await api('/api/appearance/theme', { method: 'POST', body: JSON.stringify({ theme: cur }) });
  } catch (_) { /* 存不住也不影响本次切换 */ }
});

// 顶栏「对话设置」下拉 —— 点齿轮展开，再点收起；点外面也收起
{
  const btn = $('#topMoreDrop');
  const pop = $('#topMorePop');
  const setOpen = (open) => {
    if (!pop) return;
    pop.hidden = !open;
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
  };
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    setOpen(pop.hidden);
  });
  pop.addEventListener('click', (e) => e.stopPropagation());
  document.addEventListener('click', () => setOpen(false));
}

// 顶栏的视图快捷切换
$$('#viewTabs .vtab').forEach(b => {
  b.addEventListener('click', () => {
    const v = b.dataset.vt;
    $$('#viewTabs .vtab').forEach(x => {
      const on = x === b;
      x.classList.toggle('on', on);
      x.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    if (v === 'chat') setView('chat');
    else if (v === 'workflows') setView('workflows');
    else if (v === 'market') setView('market');
    else if (v === 'kb') {
      const kb = document.querySelector('.navitem.localhead');
      if (kb) kb.click();
      const item = document.querySelector('#localSub .subitem[data-lv="kb"]');
      if (item) item.click();
    } else if (v === 'plugins') setView('market');
  });
});

// ── 右栏 ──
// 视图切换：助手（快捷功能 + 最近对话）/ 执行（过程面板）
$$('#side .stview-tab').forEach(b => {
  b.addEventListener('click', () => switchSideView(b.dataset.sv));
});

// 快捷功能：每一项都要有真实动作，不能是摆设
$$('#side [data-sq]').forEach(b => {
  b.addEventListener('click', () => {
    const k = b.dataset.sq;
    if (k === 'kb') {
      const kb = document.querySelector('.navitem.localhead');
      if (kb) kb.click();
      const item = document.querySelector('#localSub .subitem[data-lv="kb"]');
      if (item) item.click();
    } else if (k === 'wf') {
      const nav = document.querySelector('.navitem[data-view="workflows"]');
      if (nav) nav.click();
    } else if (k === 'code') {
      applyScene('code');
      const inp = $('#input');
      if (inp) { inp.focus(); autoGrow(); }
    } else if (k === 'files') {
      const nav = document.querySelector('.navitem[data-view="market"]');
      if (nav) nav.click();
    }
  });
});

// 最近对话：点一条就打开那个会话
document.addEventListener('click', e => {
  const sr = e.target.closest('#sideRecent .sr');
  if (sr && sr.dataset.sid) openSession(sr.dataset.sid);
});

/* 粘贴导入：点按钮与按 Ctrl+V 都走后端读系统剪贴板。
   浏览器那套 navigator.clipboard.read() 读不到文件，还会因为权限被拒 ——
   后端直接调 Win32 一次到位。 */
$('#pasteBtn').addEventListener('click', async () => {
  const btn = $('#pasteBtn');
  if (btn) { btn.disabled = true; }
  try {
    await importFromClipboard();
  } finally {
    if (btn) btn.disabled = false;
    $('#input').focus();
  }
});
/* 全局粘贴：在输入框之外粘贴也能接住 */
document.addEventListener('paste', ev => {
  // 已经在一个输入控件里粘贴 → 交给那个控件
  const tgt = ev.target;
  if (tgt && (tgt.tagName === 'INPUT' || tgt.tagName === 'TEXTAREA' || tgt.isContentEditable)) {
    // 但输入框里的图片粘贴还是要接住（截图场景）
    if (tgt.id !== 'input') return;
    handlePaste(ev);
    return;
  }
  handlePaste(ev);
});
