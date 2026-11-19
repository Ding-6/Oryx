/* agui.js —— 分区 18。
   由 core/scripts/split_web_modules.py 从 index.html 拆出。
   改动前先看 docs/FRONTEND-MODULES.md 的依赖说明。
 */

import { tlAgentStarted, tlMemberDone, tlSubFinished, tlSubStarted } from './agentflow.js';
import { showPlan } from './approval.js';
import { DEGRADED, renderDegraded } from './degraded.js';
import { hideThinking, setThinking, showThinking } from './events.js';
import { ERR_CLASS_HINT, FILE_TOOLS, OV_EVENTS, addMsg, agentLine, disarmBusyTimeout, loadCheckpoints, loadSessions, loadTeams, markAutoclass, markElapsed, markProof, ovOn, refreshState, refreshUsage, renderDetail, renderFiles, renderSuggestions, renderTodos, setBadge, setSendState, stepCard, wfOnFinished, wfOnStage, wfOnStarted, wfOnStep } from './models.js';
import { reasonDelta, reasonEnd, reasonStart } from './reasoning.js';
import { $, S, api, md, mut, toast } from './core.js';

/* ══════ AG-UI 事件处理 ══════
   后端已升级为标准事件族（seq 单调 + trace_id），这里做两件事：
   1) 把 AG-UI 事件翻译成界面动作
   2) 维护 mut.lastSeq，重连时带 ?since= 让后端补发断连期间的事件 */
let esRetry = 0;

/* 只影响「当前对话区内容」的事件 —— 这些必须按 task_id 过滤。
   团队、工作流、终端这些是全局的，不归某个任务管，不能过滤
   （过滤了团队时间线就不更新了）。
   判据是事件**会不会往对话区/步骤区写东西**。 */
const SCOPED_EVENTS = new Set([
  'REASONING_START', 'REASONING_CONTENT', 'REASONING_END',
  'TEXT_MESSAGE_START', 'TEXT_MESSAGE_CONTENT', 'TEXT_MESSAGE_END',
  'TOOL_CALL_START', 'TOOL_CALL_ARGS', 'TOOL_CALL_END', 'TOOL_CALL_RESULT',
  'step.created', 'step.awaiting_approval', 'step.running',
  'step.done', 'step.failed', 'step.denied', 'step.autoclass',
  'todos.updated', 'hook.metrics',
  'run.suggestion', 'run.progress',
  'RUN_CANCELLING',
]);

/* 终止类事件：一轮跑完了 / 失败了 / 被停掉了。
   这几个会改发送键状态、清思考面板 —— 别的任务发来的不能改当前界面。 */
const TERMINAL_EVENTS = new Set(['RUN_FINISHED', 'RUN_ERROR', 'RUN_CANCELLED']);

/** 这个事件是不是属于别的任务（不该进当前界面）。 */
function isForeign(ev) {
  const t = ev.type;
  if (SCOPED_EVENTS.has(t) || TERMINAL_EVENTS.has(t)) {
    // 没有 task_id 的事件无从判断 —— 放行，别误伤
    if (!ev.task_id) return false;
    // 界面还没认领任何任务（新会话刚开）：任何任务的事件都不该进来
    if (!S.taskId) return true;
    return ev.task_id !== S.taskId;
  }
  return false;
}

function handle(ev) {
  // 控制帧不带业务语义，且 seq 等于当前水位，必须放行后再做单调校验
  if (ev.type === 'ready' || ev.type === 'resync') {
    $('#live').classList.add('on');
    if (ev.type === 'resync') {
      // 缓冲已滚过 lastSeq，补不齐了：整页刷新比错序渲染安全
      toast('连接中断，正在重新同步');
      refreshState().then(loadSessions);
    }
    return;
  }
  // 乱序保护：seq 倒退说明补发与实时流撞车了，直接丢弃避免重复渲染
  if (typeof ev.seq === 'number') {
    if (ev.seq <= mut.lastSeq) return;
    mut.lastSeq = ev.seq;
  }
  // 别的任务的内容不往当前对话区写。
  // 用户点了「新建对话」，但后台那个任务还在跑 ——
  // 它的回答流进来，用户会看到一条跟自己无关的回答凭空冒出来。
  if (isForeign(ev)) return;
  // 旁路聚合到「总览」面板：不改下面各 case 的原有渲染逻辑
  if (OV_EVENTS.has(ev.type)) ovOn(ev);
  switch (ev.type) {
    case 'RUN_STARTED':
      // 新任务开跑，界面认领它
      S.taskId = ev.task_id;
      S.current = ev.task_id; S.assistant = null;
      $('#pageTitle').textContent = (ev.goal || '').slice(0, 30);
      showThinking('正在思考');
      // 这一轮刚打了一个检查点 —— 拉到本地，
      // 「回退到这里」按钮才知道自己对应哪个点。
      // 事件里直接带了 checkpoint id，不用再查一遍。
      //
      // msg_count 要跟库里一致：检查点打在**这条提问之前**，
      // 所以它的 msg_count = 这条提问在消息序列里的序号 = _msgSeq - 1
      // （addMsg 已经把序号占掉了）。
      if (ev.checkpoint) {
        S.checkpoints = (S.checkpoints || []).filter(c => c.id !== ev.checkpoint);
        S.checkpoints.push({
          id: ev.checkpoint,
          seq: S.checkpoints.length,
          label: (ev.goal || '').slice(0, 60),
          files: 0,
          msg_count: mut._msgSeq - 1,
          current: true,
        });
      }
      break;
    // 思考过程：独立于正文的通道（对齐 Marvis）
    case 'REASONING_START':
      hideThinking();
      reasonStart();
      break;
    case 'REASONING_CONTENT':
      reasonDelta(ev.delta || '');
      break;
    case 'REASONING_END':
      reasonEnd();
      break;
    case 'TEXT_MESSAGE_START':
      S.assistant = null; S.streaming = true;
      hideThinking();
      break;
    case 'TEXT_MESSAGE_CONTENT': {
      if (!S.assistant) {
        S.assistant = addMsg('bot', '');
        // 状态行要进 .body 这一列（气泡上方），不能塞进 flex 行里 ——
        // 之前插到 .msg 里，跑到头像和正文中间去了
        S.assistant.parentElement.prepend(agentLine('Oryx', '', ''));
      }
      S.assistant.textContent += ev.delta || '';
      $('#scroll').scrollTop = 1e9;
      break;
    }
    case 'TEXT_MESSAGE_END':
      // 流式期间用 textContent 追加（快），完成后整体渲染一次 markdown
      if (S.assistant) S.assistant.innerHTML = md(S.assistant.textContent);
      S.assistant = null; S.streaming = false;
      break;
    case 'TOOL_CALL_START':
      hideThinking();
      break;
    case 'step.created': setThinking('正在执行：' + (ev.tool || '')); stepCard(ev); break;
    case 'step.autoclass': { markAutoclass(ev); break; }
    case 'step.awaiting_approval': { hideThinking(); const c = stepCard(ev); setBadge(c, 'run', '等待确认'); showPlan(ev); break; }
    case 'step.running': { const c = stepCard(ev); setBadge(c, 'run', '执行中'); break; }
    case 'step.done': {
      hideThinking();
      const c = stepCard(ev); setBadge(c, 'done', '完成');
      markProof(c, ev);
      if (ev.result != null) c.querySelector('.r').textContent = typeof ev.result === 'string' ? ev.result.slice(0, 600) : JSON.stringify(ev.result).slice(0, 600);
      // 产物路径主判定来自后端 artifact_path（按「真的写了字节」的结果元数据），
      // 新增落盘工具不用改这里。FILE_TOOLS 只给老工具做兜底 —— 之前
      // step.done 根本不带 args，ev.args?.path 永远取不到，产物面板实时收集是断的。
      if (ev.artifact_path || FILE_TOOLS.has(ev.tool)) {
        const path = ev.artifact_path || ev.args?.path || ev.args?.image_path
          || ev.args?.output || ev.args?.file || ev.args?.src || ev.args?.dest;
        if (path) {
          const key = String(path);
          if (!S.artifacts.some(x => x.path === key)) {
            S.artifacts.unshift({ tool: ev.tool, path: key });
            S.artifacts = S.artifacts.slice(0, 50);
            renderFiles();
          }
        }
      }
      break;
    }
    case 'step.failed': {
      hideThinking();
      const c = stepCard(ev);
      setBadge(c, 'failed', '失败');
      // 失败分类（E1-E5）要露出来 —— 笼统的「失败」用户看不出该做什么
      const cls = ev.error_class;
      if (cls) {
        const tag = document.createElement('span');
        tag.className = 'ecls';
        tag.textContent = cls;
        tag.title = ERR_CLASS_HINT[cls] || '';
        c.querySelector('.t')?.appendChild(tag);
      }
      c.querySelector('.r').textContent = ev.error || '';
      break;
    }
    case 'step.denied': { hideThinking(); const c = stepCard(ev); setBadge(c, 'denied', '已取消'); c.querySelector('.r').textContent = ev.reason || ''; break; }
    case 'hook.metrics': markElapsed(ev); break;
    case 'todos.updated': renderTodos(ev.todos); break;
    // 主动性：模型跑的过程中就给可点的下一步，而不是跑完才留一句「还需要什么」
    case 'run.suggestion': renderSuggestions(ev.suggestions, ev.phase); break;
    case 'run.progress': break;   // 总览已在 ovOn 里更新，这里不重复渲染
    case 'team.created': case 'team.updated': case 'team.message':
    case 'team.deleted':
      loadTeams();
      if (ev.type === 'team.deleted') toast('团队已解散');
      break;
    case 'team.member.done':
      loadTeams();
      // 成员跑完了 —— 结论要落到时间线上，不能只在弹层里躺着
      tlMemberDone(ev);
      break;
    case 'team.member.started': tlAgentStarted(ev); break;
    case 'subagent.started': tlSubStarted(ev); break;
    case 'subagent.finished': tlSubFinished(ev); break;
    case 'workflow.started': wfOnStarted(ev); break;
    case 'workflow.stage.started': case 'workflow.stage.finished': wfOnStage(ev); break;
    case 'workflow.step.started': case 'workflow.step.finished': wfOnStep(ev); break;
    case 'workflow.finished': wfOnFinished(ev); break;
    case 'workflow.cancelling': toast('正在中止工作流'); break;
    case 'term.event': {
      const o = $('#termOut');
      // 有输出了就把空状态引导收起来
      const empty = $('#termEmpty');
      if (empty) empty.remove();
      if (ev.t === 'term.started') { S.term = ev.id; o.textContent = `$ ${ev.command}\n`; }
      else if (ev.t === 'term.output') { o.textContent += ev.line + '\n'; o.scrollTop = o.scrollHeight; }
      else if (ev.t === 'term.finished') { o.textContent += `\n[退出 ${ev.exit_code}]\n`; o.scrollTop = o.scrollHeight; }
      break;
    }
    case 'RUN_FINISHED':
      hideThinking();
      S.busy = false; S.busySince = 0; disarmBusyTimeout();
      setSendState('idle'); S.assistant = null; S.streaming = false;
      // 检查点要重拉 —— 这一轮改了几个文件只有跑完才知道
      loadCheckpoints(S.current);
      loadSessions(); refreshUsage(); renderDetail();
      break;
    case 'RUN_ERROR':
      hideThinking();
      // 内核兜底发的 RUN_ERROR 不带 task_id —— 那说明是内核在准备阶段就炸了
      //（比如模型没配好），跟界面盯着的是不是同一个任务无从得知。
      // 只有界面正盯着某个任务时才提示，否则用户点了「新建对话」之后，
      // 旧任务的报错会凭空插进新会话里。
      if (ev.task_id || S.taskId) addMsg('bot', '任务失败：' + (ev.error || '未知错误'));
      S.busy = false; S.busySince = 0; disarmBusyTimeout();
      setSendState('idle'); S.assistant = null; S.streaming = false;
      break;
    case 'RUN_CANCELLED':
      hideThinking();
      // 被用户停掉的：说明一下，别让人以为出了错
      addMsg('bot', '（已停止）');
      S.busy = false; S.busySince = 0; disarmBusyTimeout();
      setSendState('idle'); S.assistant = null; S.streaming = false;
      loadSessions(); renderDetail();
      break;
    case 'RUN_CANCELLING':
      toast('正在停止…');
      break;
    // 断点续跑 / 上下文裁剪 / 内核自愈：以前这些状态只在后端流转，
    // 用户完全看不到，任务「自己从头开始」还以为界面坏了。
    case 'run.resumed':
      toast(`内核已恢复：跳过 ${ev.completed || 0} 个已完成步骤，从断点继续`);
      break;
    case 'context.trimmed':
      toast(`长会话已压缩：保留 ${ev.after}/${ev.before} 条历史（预算 ${ev.budget} tokens）`);
      break;
    case 'agent.kernel.health':
      toast(ev.detail || '内核状态变化');
      break;
    case 'model.routed':
      // 运行时换模型必须可见：静默切模型会让用户以为答复风格变了是幻觉
      if (ev.switched) toast(`本轮改用 ${ev.name}：${String(ev.reason || '').slice(0, 70)}`);
      break;
    case 'capability.degraded': {
      const key = (ev.capability || '能力') + '|' + (ev.reason || '');
      if (!DEGRADED.has(key)) {
        DEGRADED.set(key, { key, capability: ev.capability || '能力',
          reason: ev.reason || '', action: ev.action || '' });
        renderDegraded();
        toast(`${ev.capability || '能力'}已降级：${String(ev.reason || '').slice(0, 50)}`);
      }
      break;
    }
  }
}

export function connect() {
  if (mut.es) { try { mut.es.close(); } catch (_) {} }
  // 首连不带 since；重连带上最后一个 seq，后端据此补发断连期间的事件
  // 不带令牌 —— EventSource 是同源请求，浏览器自动带 HttpOnly cookie。
  const q = new URLSearchParams();
  if (mut.lastSeq > 0) q.set('since', String(mut.lastSeq));
  mut.es = new EventSource('/api/events?' + q.toString());
  mut.es.onopen = () => { esRetry = 0; $('#live').classList.add('on'); };
  mut.es.onerror = () => {
    $('#live').classList.remove('on');
    // EventSource 自带重连，但它不会带 since；这里自己接管，退避重连才能补发
    try { mut.es.close(); } catch (_) {}
    mut.es = null;
    esRetry = Math.min(esRetry + 1, 6);
    setTimeout(connect, 500 * esRetry);
  };
  mut.es.onmessage = e => { try { handle(JSON.parse(e.data)); } catch (_) {} };
}
