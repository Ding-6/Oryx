/* 示例前端扩展。
 *
 * 这是**仓库自带**的样例 —— 默认 enabled=false，且后端只从**用户目录**
 * 加载扩展，所以它默认不会出现在界面上。
 *
 * 想试的话：
 *   1. 把这个目录复制到 %APPDATA%/Oryx/ui-extensions/
 *   2. 把 oryx.json 里的 enabled 改成 true
 *
 * 注意扩展跑在**自己的 Shadow DOM** 里：
 *   · ctx.$ 只在扩展内部查（核心的 DOM 查不到）
 *   · 扩展的样式不会漏到核心
 *   · 但 CSS 变量照常继承 —— var(--ink) 这些直接能用
 */

export default {
  id: 'sample-panel',
  mount: 'rail',
  label: '示例面板',

  setup(ctx) {
    // 扩展自己的样式 —— 进 shadow root，不影响核心界面。
    // 注意**没有**定义 .iconbtn / .btn —— 那两个走核心的（清单里的 baseCss）。
    ctx.addStyle(`
      .panel {
        position: fixed; inset: auto 16px 16px auto; z-index: 60;
        padding: 14px 16px; border-radius: 12px;
        background: var(--bg-2); color: var(--ink);
        border: 1px solid var(--line); box-shadow: var(--shadow-lg);
        max-width: 320px; font-size: 13px; line-height: 1.6;
      }
      .panel .ttl { font-weight: 600; margin-bottom: 6px; }
      .panel .muted { color: var(--muted); font-size: 12px; }
      .uiext-btn { margin-top: 8px; }
    `);

    const btn = document.createElement('button');
    btn.className = 'iconbtn';
    btn.title = '示例面板';
    btn.setAttribute('aria-label', '示例面板');
    btn.dataset.uiext = 'sample-panel';
    btn.innerHTML = '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M4 5h16v14H4z" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>';

    const panel = document.createElement('div');
    panel.className = 'panel';
    panel.hidden = true;
    panel.dataset.uiextPanel = 'sample-panel';
    panel.innerHTML = '<div class="ttl">示例面板</div><div class="muted">加载中…</div>';

    btn.addEventListener('click', () => { panel.hidden = !panel.hidden; });
    document.addEventListener('keydown', (ev) => {
      if (ev.key === 'Escape') panel.hidden = true;
    });

    ctx.mount.append(btn);
    ctx.mount.append(panel);

    // 订阅核心事件 —— 视图切换时更新面板
    ctx.on('view:changed', ({ view }) => {
      const d = panel.querySelector('.muted');
      if (d) d.textContent = `当前视图：${view}`;
    });

    // 反向：扩展可以请求核心做事。
    // 点「回到对话」→ 发 ui:view → 核心切视图 → 又触发 view:changed。
    const back = document.createElement('button');
    back.className = 'btn uiext-btn';
    back.dataset.uiextAction = 'back';
    back.textContent = '回到对话';
    back.addEventListener('click', () => ctx.emit('ui:view', { view: 'chat' }));
    ctx.mount.append(back);

    // 拉一次后端状态（ctx.api 自动带令牌）
    ctx.api('/api/state')
      .then((r) => r.json())
      .then((s) => {
        const d = panel.querySelector('.muted');
        if (d) d.textContent = `模式：${s.mode || '?'} · provider：${s.provider || '未配置'}`;
      })
      .catch(() => {
        const d = panel.querySelector('.muted');
        if (d) d.textContent = '后端不可达';
      });
  },
};
