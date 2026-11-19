/* session.js —— 分区 6。
   由 core/scripts/split_web_modules.py 从 index.html 拆出。
   改动前先看 docs/FRONTEND-MODULES.md 的依赖说明。
 */

/* ══════ 会话 ══════ */
/** 会话时间分组：今天 / 昨天 / 本周 / 更早。 */
export function sessGroup(ts) {
  const d = new Date((ts || 0) * 1000);
  const now = new Date();
  const day = 86400;
  const t0 = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000;
  const v = d.getTime() / 1000;
  if (v >= t0) return '今天';
  if (v >= t0 - day) return '昨天';
  if (v >= t0 - day * 7) return '本周';
  if (v >= t0 - day * 30) return '本月';
  return '更早';
}

/** 会话状态标记。跑着的转圈，失败/完成给个色点 ——
    对标 Marvis 的 statusIndicator：一眼看出哪条还在跑、哪条卡住了。 */
export function sessStatus(s) {
  const st = (s.status || '').toLowerCase();
  if (st === 'running' || st === 'in_progress') {
    return '<span class="sstat run" title="运行中"><i></i></span>';
  }
  if (st === 'failed' || st === 'error') {
    return '<span class="sstat bad" title="失败">✕</span>';
  }
  if (st === 'done' || st === 'completed') {
    return '<span class="sstat ok" title="已完成"><i></i></span>';
  }
  if (s.todo_open) return '<span class="sstat wait" title="有待确认事项">•</span>';
  return '';
}
