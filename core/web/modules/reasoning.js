/* reasoning.js —— 分区 14。
   由 core/scripts/split_web_modules.py 从 index.html 拆出。
   改动前先看 docs/FRONTEND-MODULES.md 的依赖说明。
 */

import { $, mut } from './core.js';

/* ══════ 思考过程面板 ══════
   对齐 Marvis 的 ai-chat-dialogue-reasoning：
     - 标题写「正在思考中…」/「已思考完成」
     - 思考时自动展开，结束后折叠（用户点一下能再展开）
     - 面板插在助手消息上方，跟正文分开

   思考内容是模型的思维链。多数服务端模型不返回这一段，
   那就没有面板 —— 界面不能因为缺了它就空着。 */

function reasonPanel() {
  const el = document.createElement('div');
  el.className = 'reason live open';
  el.innerHTML = `<div class="reason-head" role="button" tabindex="0" aria-expanded="true">
      <span class="ric" aria-hidden="true"><svg class="reason-caret" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18l6-6-6-6"/></svg></span>
      <span class="reason-title">正在思考中…</span>
    </div>
    <div class="reason-body"></div>`;
  const toggle = () => {
    const open = el.classList.toggle('open');
    el.querySelector('.reason-head').setAttribute('aria-expanded', String(open));
    // 记下用户动过：思考结束时不该把用户的选择覆盖掉
    el.dataset.touched = '1';
    // 收起时打一个标记 —— 思考中「默认展开」的规则靠它让路，
    // 否则用户点了收起，思考内容还会被 CSS 顶回来。
    if (open) delete el.dataset.collapsed;
    else el.dataset.collapsed = '1';
  };
  const head = el.querySelector('.reason-head');
  head.addEventListener('click', toggle);
  head.addEventListener('keydown', e => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
  });
  // 挂在消息流末尾。助手正文是之后才创建的（addMsg 会追加到末尾），
  // 所以面板自然落在正文的上方 —— 顺序是「思考 → 回答」。
  //
  // 每轮思考都是**独立一块**面板：续接对话时第二轮不能覆盖第一轮的思考。
  const host = $('#messages');
  host.appendChild(el);
  mut.S_REASON = el;
  return el;
}

export function reasonStart() {
  const el = reasonPanel();
  el.classList.add('live', 'open');
  el.querySelector('.reason-title').textContent = '正在思考中…';
  el.querySelector('.reason-body').textContent = '';
  $('#scroll').scrollTop = 1e9;
}

export function reasonDelta(delta) {
  if (!delta || !mut.S_REASON || !document.body.contains(mut.S_REASON)) return;
  const body = mut.S_REASON.querySelector('.reason-body');
  body.textContent += delta;
  body.scrollTop = body.scrollHeight;
  $('#scroll').scrollTop = 1e9;
}

export function reasonEnd() {
  if (!mut.S_REASON || !document.body.contains(mut.S_REASON)) { mut.S_REASON = null; return; }
  mut.S_REASON.classList.remove('live');
  // 清掉「思考中强制展开」的让路标记 —— 现在由 open 类决定展开与否
  delete mut.S_REASON.dataset.collapsed;
  // 用户手动展开过就保持展开 —— 跟 Marvis 一致：
  // 它的 useState(status !== 'completed') 只决定初始值，
  // 后续用户点开的状态不会被"思考完成"覆盖掉。
  if (mut.S_REASON.dataset.touched !== '1') {
    mut.S_REASON.classList.remove('open');
    mut.S_REASON.querySelector('.reason-head').setAttribute('aria-expanded', 'false');
  }
  mut.S_REASON.querySelector('.reason-title').textContent = '已思考完成';
}
