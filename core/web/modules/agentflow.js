/* agentflow.js —— 分区 15。
   由 core/scripts/split_web_modules.py 从 index.html 拆出。
   改动前先看 docs/FRONTEND-MODULES.md 的依赖说明。
 */

import { $, S, esc } from './core.js';

/* ══════ 多 Agent 对话流 ══════
   管家之间、或管家与 Oryx 之间的往来，都在这条时间线里显示。

   之前这些消息只躺在团队面板的弹层里（用户得手动打开才看得到），
   `team.member.done` 明明带着子 Agent 的完整结论，前端却把它丢了 ——
   界面上只剩一个"执行中"的转圈，跑完了也不知道说了什么。 */
S.tl = [];       // [{agent, name, icon, kind:'start'|'done'|'poke', text, ts}]

/** 取（或建）某个 Agent 在时间线里的段。 */
function tlSection(agent, name, icon) {
  let sec = S.tl.find(s => s.agent === agent);
  if (!sec) {
    sec = { agent, name, icon, items: [] };
    S.tl.push(sec);
  }
  return sec;
}

/** 时间线容器（在消息流里）。 */
function tlBox() {
  let el = $('#tl');
  if (!el) {
    el = document.createElement('div');
    el.id = 'tl';
    el.className = 'tl';
    $('#messages').appendChild(el);
  }
  return el;
}

/** 重画整条时间线。 */
function tlPaint() {
  const box = tlBox();
  box.innerHTML = S.tl.map(sec => `
    <div class="tlsec">
      <div class="tlhead">
        <span class="av">${esc(sec.icon || sec.name.slice(0, 1))}</span>
        <span class="nm">${esc(sec.name)}</span>
        ${sec.busy ? '<span class="dots"><i></i><i></i><i></i></span>' : ''}
      </div>
      ${sec.items.map(it => {
        if (it.kind === 'poke') return `<div class="tlpoke">${esc(it.text)}</div>`;
        return `<div class="tlmsg ${it.who === 'main' ? 'me' : ''} ${it.empty ? 'dim' : ''}">${it.tag ? `<span class="tag">${esc(it.tag)}</span>` : ''}${esc(it.text)}</div>`;
      }).join('')}
    </div>`).join('');
  $('#scroll').scrollTop = 1e9;
}

/** 管家被派活了。 */
export function tlAgentStarted(ev) {
  const name = ev.agent_name || ev.agent || '';
  const sec = tlSection(ev.agent, name, ev.agent_icon || '');
  sec.busy = true;
  sec.items.push({ kind: 'poke', text: `Oryx 派活给 ${name}：${(ev.goal || '').slice(0, 60)}${(ev.goal || '').length > 60 ? '…' : ''}` });
  tlPaint();
}

/** 子 Agent 开跑（team_dispatch 之外的路径，比如 spawn_agent）。 */
export function tlSubStarted(ev) {
  const name = ev.agent_name || ev.agent || '';
  const sec = tlSection(ev.agent, name, ev.agent_icon || '');
  sec.busy = true;
  sec.items.push({ kind: 'poke', text: `${name} 开始执行` });
  tlPaint();
}

/** 子 Agent 跑完了 —— 结论要显示出来。 */
export function tlSubFinished(ev) {
  const name = ev.agent_name || ev.agent || '';
  const sec = tlSection(ev.agent, name, ev.agent_icon || '');
  sec.busy = false;
  const text = (ev.text || '').trim();
  if (text) sec.items.push({ who: 'agent', tag: ev.empty ? '没有产出' : '', text, empty: !!ev.empty });
  tlPaint();
}

/** 团队成员跑完了。team.member.done 带着完整结论 ——
    之前这个 text 被整个丢掉，界面上只剩一个转圈。 */
export function tlMemberDone(ev) {
  const name = ev.agent_name || ev.agent || '';
  const sec = tlSection(ev.agent, name, ev.agent_icon || '');
  sec.busy = false;
  const text = (ev.text || '').trim();
  if (text) {
    const empty = !!ev.empty || text === '（子 Agent 没有产出文本）';
    sec.items.push({ who: 'agent', tag: ev.ok === false ? '失败' : (empty ? '没有产出' : ''), text, empty });
  }
  tlPaint();
}
