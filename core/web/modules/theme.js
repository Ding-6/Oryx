/* theme.js —— 分区 35。
   由 core/scripts/split_web_modules.py 从 index.html 拆出。
   改动前先看 docs/FRONTEND-MODULES.md 的依赖说明。
 */

import { $, api, bus } from './core.js';

/* ══════ 主题 / 背景 ══════ */
export function applyTheme(t) {
  document.documentElement.setAttribute('data-theme', t);
  // 主题变了 —— 告诉前端扩展。
  // 扩展在 Shadow DOM 里，但 CSS 变量是**继承**的，所以暗色主题照样自动生效；
  // 这个事件是给那些需要按主题改行为的扩展用的（比如换图标）。
  bus.emit('theme:changed', { theme: t });
}
const BG_FIT = {
  cover:   { size: 'cover',   pos: 'center', repeat: 'no-repeat' },
  contain: { size: 'contain', pos: 'center', repeat: 'no-repeat' },
  tile:    { size: 'auto',    pos: '0 0',    repeat: 'repeat' },
  center:  { size: 'auto',    pos: 'center', repeat: 'no-repeat' },
};
export function paintBg(fit) {
  const f = BG_FIT[fit] || BG_FIT.cover;
  const root = document.documentElement;
  root.style.setProperty('--bg-size', f.size);
  root.style.setProperty('--bg-pos', f.pos);
  root.style.setProperty('--bg-repeat', f.repeat);
}
export async function applyBg() {
  try {
    const i = await (await api('/api/appearance')).json();
    paintBg(i.background_fit);
    if (i.background_exists) {
      // 令牌走 HttpOnly cookie，同源请求浏览器自动带上。
      document.documentElement.style.setProperty('--bg-image', 'url("/api/appearance/background.png")');
      document.body.classList.add('has-bg');
    } else {
      document.documentElement.style.setProperty('--bg-image', 'none');
      document.body.classList.remove('has-bg');
    }
  } catch (_) {}
}
