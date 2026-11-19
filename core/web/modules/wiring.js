/* wiring.js —— 分区 30。
   由 core/scripts/split_web_modules.py 从 index.html 拆出。
   改动前先看 docs/FRONTEND-MODULES.md 的依赖说明。
 */

import { addFiles } from './models.js';
import { handlePaste, importFromClipboard } from './paste.js';
import { openPerm } from './policy.js';
import { openSkills } from './skillcall.js';
import { $ } from './core.js';

/* ══════ 文件 / 权限 / 技能 芯片接线 ══════ */
$('#fileBtn').addEventListener('click', addFiles);
$('#permBtn').addEventListener('click', openPerm);
$('#skillBtn').addEventListener('click', openSkills);

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
