/* cards.js —— 分区 23。
   由 core/scripts/split_web_modules.py 从 index.html 拆出。
   改动前先看 docs/FRONTEND-MODULES.md 的依赖说明。
 */

import { _modalGen, closeModal, openModal } from './modal.js';
import { $, esc, escAttr, toast } from './core.js';

/* ══════ 统一卡片 ══════
   技能、人设、工具集、自动任务、能力都用这一个渲染器。
   好处是「所有框一样大、按钮在同一个位置、描述多了会折叠」这件事
   只在一个地方定义，不会各页面各写各的又走样。 */

/**
 * 渲染一张统一卡片。
 * @param {object} o
 *   icon     图标字符（可省）
 *   name     标题
 *   id       副标识（目录名 / 工具 id / cron 表达式）
 *   count    右上角计数徽章文字（可省）
 *   badges   徽章 html（可省）
 *   desc     描述文字（长了自己折叠）
 *   chips    标签数组（可省）
 *   primary  主操作按钮 {label, act, data}
 *   actions  次要操作按钮数组 [{label, act, data}]
 *   detail   详情标识：点了标题/描述会弹详情
 */
export function ucard(o) {
  const acts = (o.actions || []).map(a =>
    `<button class="btn" data-ucact="${escAttr(a.act)}" data-ucdata="${escAttr(a.data || '')}" type="button">${esc(a.label)}</button>`
  ).join('');
  const primary = o.primary
    ? `<button class="btn primary" data-ucact="${escAttr(o.primary.act)}" data-ucdata="${escAttr(o.primary.data || '')}" type="button">${esc(o.primary.label)}</button>`
    : '';
  // 五个槽位**永远渲染** —— 缺内容也留空位，
  // 这样所有卡片的高度、描述位置、按钮位置完全一致。
  return `<div class="uc"${o.detail ? ` data-ucdetail="${escAttr(o.detail)}"` : ''}>
    <div class="uh">
      ${o.icon ? `<span class="uicon">${esc(o.icon)}</span>` : '<span class="uicon"></span>'}
      <span class="uname"${o.detail ? ` data-ucdetail="${escAttr(o.detail)}" title="点击查看详情"` : ''}>${esc(o.name)}</span>
      ${o.count ? `<span class="ucnt">${esc(o.count)}</span>` : ''}
    </div>
    <div class="umeta">
      ${o.id ? `<span class="uid">${esc(o.id)}</span>` : ''}
      ${o.badges ? `<span class="ubadges">${o.badges}</span>` : ''}
    </div>
    <div class="udesc clamp2"${o.detail ? ` data-ucdetail="${escAttr(o.detail)}" title="点击查看完整说明"` : ''}>${esc(o.desc || '（无说明）')}</div>
    <div class="uchips">${(o.chips || []).slice(0, 8).map(c => `<span class="setchip">${esc(c)}</span>`).join('')}</div>
    <div class="ufoot">${acts}<div class="spacer"></div>${primary}</div>
  </div>`;
}

/** 给一整块容器挂上统一卡片的点击行为（详情 + 按钮）。 */
export function bindUCards(root, onAction) {
  if (!root) return;
  root.querySelectorAll('[data-ucact]').forEach(b => b.addEventListener('click', e => {
    e.stopPropagation();
    onAction(b.dataset.ucact, b.dataset.ucdata);
  }));
  root.querySelectorAll('[data-ucdetail]').forEach(el => el.addEventListener('click', e => {
    e.stopPropagation();
    const key = el.dataset.ucdetail;
    const fn = UC_DETAIL[key];
    if (fn) fn();
  }));
}
/* 详情弹窗的注册表：key → 打开详情的函数。
   各页面渲染卡片时把自己要看的详情挂上来。 */
export const UC_DETAIL = {};

/** 打开详情弹窗。rows 是 [{label, value}]，value 可以是 html。 */
export function ucDetail(title, rows, foot) {
  openModal({
    title, wide: true,
    body: `<div class="ucdetail">${rows.map(r => `
      <div class="urow"><div class="ul">${esc(r.label)}</div>
        <div class="uv">${r.html ? r.html : esc(r.value == null ? '' : r.value)}</div></div>`).join('')}</div>`,
    foot: foot || `<button class="btn" data-close type="button">关闭</button>`,
  });
}

/**
 * 危险操作确认弹窗。
 * 之前都是 confirm() —— 丑、不能自定义文案、也不能说清后果。
 */
export function ucConfirm({ title, message, okLabel, danger = true, onOk }) {
  openModal({
    title: title || '确认操作',
    body: `<div class="confirm">
      <span class="cico">${danger ? '⚠' : '?'}</span>
      <div class="ctxt">${message}</div>
    </div>`,
    foot: `<button class="btn" data-close type="button">取消</button>
           <button class="btn ${danger ? 'primary' : ''}" id="ucOk" type="button">${esc(okLabel || '确认')}</button>`,
  });
  $('#ucOk').addEventListener('click', async () => {
    const btn = $('#ucOk');
    // 记下这个确认弹窗的代号 —— onOk 里可能又开了新弹层
    // （比如删完团队重开另一个团队的面板），那时就不能再 closeModal，
    // 否则把刚开的那个也关了。
    const myGen = _modalGen;
    if (btn) { btn.disabled = true; btn.textContent = '处理中…'; }
    try {
      await onOk();
      if (_modalGen === myGen) closeModal();
    } catch (e) {
      if (btn) { btn.disabled = false; btn.textContent = okLabel || '确认'; }
      toast('失败：' + (e && e.message ? e.message : e));
    }
  });
}
