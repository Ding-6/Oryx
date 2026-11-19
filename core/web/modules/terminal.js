/* terminal.js —— 分区 31。
   由 core/scripts/split_web_modules.py 从 index.html 拆出。
   改动前先看 docs/FRONTEND-MODULES.md 的依赖说明。
 */

import { autoGrow } from './models.js';
import { $, S, api, toast } from './core.js';

/* ══════ 终端 ══════ */

/**
 * 跑一条终端命令。
 *
 * 三个问题在这里修掉：
 * 1. **失败信息不能混进 stdout** —— 以前 `[失败] …` 直接追加到输出流里，
 *    和真实命令输出视觉上完全一样，用户以为命令跑出了那段内容。
 *    现在失败单独用一个错误样式渲染。
 * 2. **输入框不要先清空** —— 网络失败时命令文本已经丢了，用户无法重试。
 *    现在失败时把命令留在框里。
 * 3. **按钮要禁掉** —— 连点两次会发两条命令。
 */
async function runTerm() {
  const input = $('#termInput');
  const c = input.value.trim();
  if (!c) return;
  const btn = $('#termRun');
  btn.disabled = true;
  try {
    const r = await api('/api/terminal/run', { method: 'POST', body: JSON.stringify({ command: c }) });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      // 失败：命令留在输入框里，用户可以直接改或重试
      appendTermError(e.detail || ('请求失败（' + r.status + '）'));
      toast('命令没发出去');
      return;
    }
    input.value = '';
    autoGrow();
  } catch (err) {
    appendTermError((err && err.message) || '无法连接内核');
    toast('无法连接内核');
  } finally {
    btn.disabled = false;
  }
}

/** 终端里的错误行 —— 与 stdout 视觉上区分开。 */
function appendTermError(msg) {
  const out = $('#termOut');
  if (!out) return;
  const line = document.createElement('div');
  line.style.color = 'var(--danger)';
  line.textContent = '[失败] ' + msg;
  out.appendChild(line);
  out.scrollTop = out.scrollHeight;
}
$('#termRun').addEventListener('click', runTerm);
$('#termInput').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); runTerm(); } });
$('#termKill').addEventListener('click', async () => {
  // 没有运行中的会话时要**说清楚**，不能静默返回 ——
  // 静默返回的话用户点了没反应，以为按钮坏了。
  if (!S.term) { toast('现在没有正在运行的命令'); return; }
  const btn = $('#termKill');
  btn.disabled = true;
  try {
    const r = await api('/api/terminal/interrupt', { method: 'POST', body: JSON.stringify({ id: S.term }) });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      toast('中断失败：' + (e.detail || r.status));
      return;
    }
    toast('已中断');
  } catch (err) {
    toast('中断失败：' + ((err && err.message) || err));
  } finally {
    btn.disabled = false;
  }
});
