/* wiring.js —— 分区 30。
   由 core/scripts/split_web_modules.py 从 index.html 拆出。
   改动前先看 docs/FRONTEND-MODULES.md 的依赖说明。
 */

import { addFiles, applyScene, switchSideView, openSession, openWorkflowPicker, autoGrow, setView } from './models.js';
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

/* 「更多设置」已下架。
 *
 * 原来它是个弹层，装了「优化表达 / 调用技能 / 默认权限」三项：
 *   · 「调用技能」跟工具栏上的按钮**是同一个功能**（都调 openSkills），
 *     只是标签写得不一样 —— 纯重复项；
 *   · 「优化表达」现在平铺在工具栏里，跟「上传文件」并列；
 *   · 「默认权限」挪到顶栏的设置下拉，跟交互模式并排。
 *
 * 多一层菜单只为放两个按钮，藏得比露得深 —— 而且点进去才发现
 * 「连接知识库」和「调用技能」是同一件事。
 */

// 「优化表达」平铺进工具栏
$('#optBtn')?.addEventListener('click', () => openPromptOpt());

// 「工作流」：在对话里直接选模板跑
$('#wfBtn')?.addEventListener('click', () => openWorkflowPicker());

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
