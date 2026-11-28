/**
 * 会话详情 —— 主区。
 *
 * 用的是 v2 的 `GET /api/sessions/{id}`：消息、步骤、待办、用量一次拿全。
 * 三个子资源接口（steps / todos / usage）也都在，但详情里已经带了，
 * 所以这里不重复拉 —— 那是给「只要其中一样」的场景用的。
 */

import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'

import { approveStep, createTodo, fetchCheckpoints, fetchPendingApprovals, fetchSession, setTodoStatus } from './api'
import StepList from './StepList'
import RewindDialog from './RewindDialog'
import { canRewind, checkpointForMessage, formatCheckpointTime } from './checkpoints'
import { sessionStatusLabel, todoSummary, validateTodoTitle } from './sessionOps'
import { RISK_CN, stepExtraFromEvent, type StepExtra } from './stepFormat'
import { useStreamText } from './useStreamText'
import type {
  Checkpoint,
  CheckpointList,
  Message,
  PendingApproval,
  SessionDetail,
  StreamDraft,
} from '@/lib/types'
import { Button } from '@/components/ui/button'
import { ApiError, api, subscribe, type StreamStatus } from '@/lib/api'
import { renderMarkdown } from '@/lib/markdown'
import { labelFor, type PreferenceCatalog } from '@/features/settings/preferences'

const TODO_CN: Record<string, string> = {
  pending: '待办',
  in_progress: '进行中',
  done: '已完成',
}

/** 空数组用同一个引用，避免每次渲染都让对账 memo 失效。 */
const EMPTY_MESSAGES: Message[] = []

export default function SessionDetail({
  taskId,
  onBack,
  onRunEvent,
  catalog,
  refreshKey = 0,
}: {
  taskId: string | null
  onBack?: () => void
  /** 运行开始 / 结束时通知外层刷新会话列表。 */
  onRunEvent?: () => void
  /** 偏好目录 —— 把快照里的 interaction/style/mode 显示成中文名。 */
  catalog?: PreferenceCatalog | null
  /** 外层改名后让详情标题跟着更新。 */
  refreshKey?: number
}) {
  const [detail, setDetail] = useState<SessionDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [sendError, setSendError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [messageDraft, setMessageDraft] = useState('')
  const [sending, setSending] = useState(false)
  const [refreshToken, setRefreshToken] = useState(0)
  const [runAction, setRunAction] = useState<'cancel' | 'retry' | null>(null)
  const [runActionError, setRunActionError] = useState<string | null>(null)
  const [retryPending, setRetryPending] = useState(false)
  const [pendingApprovals, setPendingApprovals] = useState<PendingApproval[]>([])
  // SSE 事件里带、但老库 / 旧内核没落库的步骤增强字段。按 step_id 存，
  // 刷新详情后落库字段优先，这些只用来补空。
  const [stepExtras, setStepExtras] = useState<Record<string, StepExtra>>({})
  // 检查点：只在切会话、运行结束、回退之后拉 —— 跟着每次步骤刷新拉会打太多请求。
  const [checkpoints, setCheckpoints] = useState<CheckpointList | null>(null)
  const [checkpointBump, setCheckpointBump] = useState(0)
  const [rewindTarget, setRewindTarget] = useState<Checkpoint | null>(null)
  const [streamStatus, setStreamStatus] = useState<StreamStatus>('connecting')
  const [approvalBusy, setApprovalBusy] = useState<string | null>(null)
  const [approvalError, setApprovalError] = useState<string | null>(null)
  // 待办：手动添加 + 勾选失败要看得见（以前失败只静默回滚，用户以为存上了）。
  const [todoDraft, setTodoDraft] = useState('')
  const [todoError, setTodoError] = useState<string | null>(null)
  const [todoBusy, setTodoBusy] = useState(false)
  // 外层每次渲染都会传新函数；存 ref 才不会让 SSE 连接跟着反复重建。
  const onRunEventRef = useRef(onRunEvent)
  useEffect(() => { onRunEventRef.current = onRunEvent }, [onRunEvent])

  // 流式正文 / 思考过程：后端逐 chunk 发 SSE，这里攒成会长的气泡。
  // detail 里的正式消息用来对账 —— 落库后草稿自动撤掉，不会重复显示。
  const { visibleDrafts, thinking, onEvent: onStreamEvent, reset: resetStream } =
    useStreamText(detail?.messages ?? EMPTY_MESSAGES)
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!taskId) {
      setDetail(null)
      return
    }
    let alive = true
    setLoading(true)
    setError(null)
    let timer: number | undefined
    let retryWaits = 0
    const load = async () => {
      try {
        const d = await fetchSession(taskId)
        if (!alive) return
        setDetail(d)
        setError(null)
        const status = String(d.task.status ?? '')
        if (status === 'running') {
          setRetryPending(false)
          timer = window.setTimeout(() => void load(), 1800)
        } else if (retryPending && retryWaits < 20) {
          // Retry returns after queuing the worker; briefly poll if this read
          // races the worker's running-state update.
          retryWaits += 1
          timer = window.setTimeout(() => void load(), 400)
        } else if (retryPending) {
          setRetryPending(false)
        }
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : '加载失败')
      } finally {
        if (alive) setLoading(false)
      }
    }
    void load()
    return () => {
      alive = false
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [taskId, refreshToken, retryPending, refreshKey])

  // SSE 事件可能在页面打开前就发了（刷新页面、刚进会话时正在等确认）。
  // 从内核快照补一份回来，卡片才不会因为错过一条事件就永远不出现。
  useEffect(() => {
    if (!taskId) {
      setPendingApprovals([])
      return
    }
    let alive = true
    void fetchPendingApprovals(taskId)
      .then((items) => { if (alive) setPendingApprovals(items) })
      .catch(() => { if (alive) setPendingApprovals([]) })
    return () => { alive = false }
  }, [taskId])

  // 检查点拉取：切会话、运行结束、回退之后各拉一次。
  // 内核没就绪（503）或旧会话没有检查点时一律当「没有」，不报错 ——
  // 回退入口消失比整页变红合理。
  useEffect(() => {
    if (!taskId) {
      setCheckpoints(null)
      return
    }
    let alive = true
    fetchCheckpoints(taskId)
      .then((data) => { if (alive) setCheckpoints(data) })
      .catch(() => { if (alive) setCheckpoints(null) })
    return () => { alive = false }
  }, [taskId, checkpointBump])

  // 实时推送：流式正文 / 思考 / 审批卡 / 运行结束，全走这一条 SSE。
  useEffect(() => {
    if (!taskId) return
    // 换会话（或重新订阅）时丢掉上一个会话的半截正文与步骤增强字段。
    resetStream()
    setStepExtras({})
    let refreshTimer: number | undefined
    const scheduleRefresh = (delay: number) => {
      if (refreshTimer !== undefined) window.clearTimeout(refreshTimer)
      refreshTimer = window.setTimeout(() => setRefreshToken((v) => v + 1), delay)
    }
    const stop = subscribe('/api/events', (ev) => {
      const type = String(ev.type ?? '')
      if (type === 'ready') return
      if (type === 'resync') {
        // 缓冲滚过了断点，续不了传 —— 可能丢了 START/END，草稿不可信，整页重拿。
        resetStream()
        setStepExtras({})
        void fetchPendingApprovals(taskId).then(setPendingApprovals).catch(() => undefined)
        setRefreshToken((value) => value + 1)
        return
      }
      // 后端已经按账号过滤，这里再按会话过滤一次，避免别的会话弹卡/串正文。
      if (ev.task_id && String(ev.task_id) !== taskId) return

      // 后端把文件和对话退回去了 —— 半截草稿与步骤增强字段都属于旧状态，
      // 整份重拿；检查点列表也可能变了。
      if (type === 'checkpoint.rewound') {
        resetStream()
        setStepExtras({})
        setCheckpointBump((value) => value + 1)
        setRefreshToken((value) => value + 1)
        onRunEventRef.current?.()
        return
      }

      // 步骤事件统一处理：立刻刷新详情，并记下事件里「尚未落库」的增强字段
      // （失败分类 / 只读自证 / 产物路径 / 耗时 / 拒绝原因）。
      // state、args、result、error 一律以 GET 详情为准 —— 补发的旧事件
      // 不能把已经完成的步骤改回「执行中」。
      if (type.startsWith('step.')) {
        const stepId = String(ev.step_id ?? '')
        const extra = stepExtraFromEvent(ev)
        if (stepId && Object.keys(extra).length > 0) {
          // 按 step_id 累积：created 带来的 risk_label 不会被 done 事件抹掉。
          setStepExtras((prev) => ({ ...prev, [stepId]: { ...prev[stepId], ...extra } }))
        }
        scheduleRefresh(120)
      }

      // 文本与思考事件交给攒包器（它自己忽略其它类型）。
      onStreamEvent(ev)
      if (type === 'TEXT_MESSAGE_END') {
        // END 早于 store.add_message，稍等一下再拉详情，
        // 正式消息才会把草稿认领掉，用户看到的是同一段文字平滑接上。
        scheduleRefresh(300)
        return
      }

      if (type === 'step.awaiting_approval') {
        const stepId = String(ev.step_id ?? '')
        if (!stepId) return
        const plan = ev.plan && typeof ev.plan === 'object' && !Array.isArray(ev.plan)
          ? ev.plan as PendingApproval['plan']
          : {}
        const item: PendingApproval = {
          step_id: stepId,
          task_id: String(ev.task_id ?? taskId),
          tool: String(ev.tool ?? ''),
          plan,
          risk: typeof ev.risk === 'number' ? ev.risk : null,
          started_at: typeof ev.ts === 'number' ? ev.ts : 0,
        }
        setPendingApprovals((list) =>
          list.some((x) => x.step_id === stepId) ? list : [...list, item]
        )
        setApprovalError(null)
        return
      }
      if (type === 'step.approval_resolved' || type === 'step.denied') {
        const stepId = String(ev.step_id ?? '')
        setPendingApprovals((list) =>
          stepId ? list.filter((x) => x.step_id !== stepId) : []
        )
        return
      }
      if (type.startsWith('RUN_')) {
        setRefreshToken((value) => value + 1)
        if (type === 'RUN_FINISHED' || type === 'RUN_CANCELLED' || type === 'RUN_ERROR') {
          setPendingApprovals([])
          // 这一轮开始时内核新打了一个检查点，跑完才能回退到它。
          setCheckpointBump((value) => value + 1)
          onRunEventRef.current?.()
        }
      }
    }, setStreamStatus)
    return () => {
      if (refreshTimer !== undefined) window.clearTimeout(refreshTimer)
      stop()
    }
  }, [taskId, onStreamEvent, resetStream])

  // 流式期间贴着底部走；用户主动往上翻（离底部 > 260px）就不打扰。
  useEffect(() => {
    if (visibleDrafts.length === 0 || visibleDrafts.every((d) => d.done)) return
    const raf = window.requestAnimationFrame(() => {
      const box = scrollableParent(endRef.current)
      if (box && box.scrollHeight - box.scrollTop - box.clientHeight < 260) {
        box.scrollTop = box.scrollHeight
      }
    })
    return () => window.cancelAnimationFrame(raf)
  }, [visibleDrafts])

  if (!taskId) {
    return (
      <div className="flex h-full items-center justify-center text-[13px] text-muted">
        从左边选一个会话
      </div>
    )
  }

  if (error) {
    return (
      <div className="rounded-[var(--r-lg)] border border-danger/40 bg-danger-soft p-4 text-[13px] text-danger">
        {error}
      </div>
    )
  }

  if (loading || !detail) {
    return <div className="text-[13px] text-muted">加载中…</div>
  }

  const openTodos = detail.todos.filter((t) => t.status !== 'done')
  const doneTodos = detail.todos.filter((t) => t.status === 'done')
  const todoStats = todoSummary(detail.todos)
  const task = detail.task as Record<string, unknown>
  const agentId = typeof task.agent_id === 'string' ? task.agent_id : ''
  const expertId = typeof task.expert_id === 'string' ? task.expert_id : ''
  const snapshot = task.config_snapshot && typeof task.config_snapshot === 'object'
    ? task.config_snapshot as Record<string, unknown>
    : {}
  const agentSnapshot = snapshot.agent && typeof snapshot.agent === 'object'
    ? snapshot.agent as Record<string, unknown>
    : {}
  const expertSnapshot = snapshot.expert && typeof snapshot.expert === 'object'
    ? snapshot.expert as Record<string, unknown>
    : {}
  // 新建运行时后端会把 interaction / style / mode 一起固化进 config_snapshot.runtime。
  // 老会话没有这份记录 —— 不能拿当前全局默认冒充它当时用的设置。
  const runtime = snapshot.runtime && typeof snapshot.runtime === 'object'
    ? snapshot.runtime as Record<string, unknown>
    : {}
  const runtimeLabel = (key: 'interaction' | 'style' | 'mode') => {
    const value = runtime[key]
    if (typeof value !== 'string' || !value) return '未记录'
    const items = key === 'interaction' ? catalog?.interactions
      : key === 'style' ? catalog?.styles : catalog?.compute_modes
    return labelFor(items, value, value)
  }
  const hasRuntime = Boolean(runtime.interaction || runtime.style || runtime.mode)
  const status = String(task.status ?? '')
  const canRetry = detail.messages.some((message) => message.role === 'user')
  const snapshotList = (value: unknown) => Array.isArray(value) ? value.map(String).join('、') : ''

  const toggleTodo = async (id: string, status: string) => {
    const next = status === 'done' ? 'pending' : 'done'
    // 先本地改 —— 点了就该立刻有反馈
    setDetail((d) =>
      d
        ? {
            ...d,
            todos: d.todos.map((t) => (t.id === id ? { ...t, status: next } : t)),
          }
        : d,
    )
    try {
      await setTodoStatus(taskId, id, next)
      setTodoError(null)
    } catch (cause) {
      // 存不住就改回去 —— 界面不能显示一个其实没存上的状态，
      // 而且必须把失败说出来，不能只静默回滚让人以为勾上了。
      setDetail((d) =>
        d
          ? {
              ...d,
              todos: d.todos.map((t) =>
                t.id === id ? { ...t, status: status as typeof t.status } : t,
              ),
            }
          : d,
      )
      setTodoError(cause instanceof ApiError ? cause.message : '待办没存上，已恢复原状态')
    }
  }

  const addTodo = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const title = todoDraft.trim()
    const check = validateTodoTitle(title)
    if (!check.ok) {
      setTodoError(check.message)
      return
    }
    if (todoBusy) return
    setTodoBusy(true)
    setTodoError(null)
    try {
      const todo = await createTodo(taskId, title)
      setDetail((d) => d ? { ...d, todos: [...d.todos, todo] } : d)
      setTodoDraft('')
    } catch (cause) {
      setTodoError(cause instanceof ApiError ? cause.message : '添加待办失败，请稍后重试')
    } finally {
      setTodoBusy(false)
    }
  }

  const sendMessage = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const goal = messageDraft.trim()
    if (!goal || sending || String(task.status ?? '') === 'running') return
    setSending(true)
    setSendError(null)
    try {
      await api.post('/api/chat', { goal, session_id: taskId })
      setMessageDraft('')
      setDetail((current) => current ? {
        ...current,
        task: { ...current.task, status: 'running' },
      } : current)
      setRefreshToken((value) => value + 1)
    } catch (cause) {
      setSendError(cause instanceof ApiError ? cause.message : cause instanceof Error ? cause.message : '发送失败，请稍后重试')
    } finally {
      setSending(false)
    }
  }

  const cancelRun = async () => {
    setRunAction('cancel')
    setRunActionError(null)
    try {
      await api.post('/api/chat/cancel', { task_id: taskId })
      setRefreshToken((value) => value + 1)
    } catch (cause) {
      setRunActionError(cause instanceof ApiError ? cause.message : cause instanceof Error ? cause.message : '停止请求失败')
    } finally {
      setRunAction(null)
    }
  }

  const retryRun = async () => {
    setRunAction('retry')
    setRunActionError(null)
    try {
      await api.post('/api/chat/retry', { task_id: taskId })
      setRetryPending(true)
      setDetail((current) => current ? {
        ...current,
        task: { ...current.task, status: 'running' },
      } : current)
      setRefreshToken((value) => value + 1)
    } catch (cause) {
      setRetryPending(false)
      setRefreshToken((value) => value + 1)
      setRunActionError(cause instanceof ApiError ? cause.message : cause instanceof Error ? cause.message : '重新运行失败')
    } finally {
      setRunAction(null)
    }
  }

  const decideApproval = async (item: PendingApproval, approved: boolean) => {
    setApprovalBusy(item.step_id)
    setApprovalError(null)
    try {
      await approveStep(item.step_id, approved)
      setPendingApprovals((list) => list.filter((x) => x.step_id !== item.step_id))
      setRefreshToken((value) => value + 1)
    } catch (cause) {
      // 已处理完 / 步骤不存在 —— 卡已经没意义了，撤掉即可，别让用户反复点。
      if (cause instanceof ApiError && (cause.status === 404 || cause.status === 409)) {
        setPendingApprovals((list) => list.filter((x) => x.step_id !== item.step_id))
      } else {
        setApprovalError(cause instanceof Error ? cause.message : '审批请求失败')
      }
    } finally {
      setApprovalBusy(null)
    }
  }

  const streamLabel = {
    connecting: '正在连接推送…',
    open: '实时推送已连接',
    reconnecting: '推送重连中…',
    offline: '推送已断开',
  }[streamStatus]

  const handleRewound = () => {
    // 对话可能被裁掉、文件也变了 —— 草稿与步骤增强字段都属于旧状态。
    resetStream()
    setStepExtras({})
    setRefreshToken((value) => value + 1)
    setCheckpointBump((value) => value + 1)
    onRunEventRef.current?.()
  }

  return (
    <div className="mx-auto flex h-full w-full max-w-3xl flex-col gap-4 overflow-y-auto p-6">
      <header>
        <h1 className="text-[17px] font-semibold text-ink">
          {detail.title || '（未命名会话）'}
        </h1>
        <p className="mt-1 text-[12px] text-muted">
          {detail.usage.calls} 次模型调用 · {detail.messages.length} 条消息 ·{' '}
          {detail.steps.length} 个步骤
        </p>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {agentId && <span className="rounded-full bg-accent-soft px-2 py-0.5 text-[11px] text-accent">子 Agent：{String(agentSnapshot.name || agentId)}</span>}
          {expertId && <span className="rounded-full bg-panel-2 px-2 py-0.5 text-[11px] text-ink-2">专家：{String(expertSnapshot.name || expertId)}</span>}
          <span className={'rounded-full px-2 py-0.5 text-[11px] ' + (status === 'failed' ? 'bg-danger-soft text-danger' : status === 'running' ? 'bg-warn-soft text-warn' : 'bg-panel-2 text-ink-2')}>
            {status === 'running' && visibleDrafts.some((d) => !d.done)
              ? '运行中 · 正在输出'
              : ({ running: '运行中 · 实时推送', done: '已完成', failed: '运行失败', cancelled: '已取消', idle: '未开始' } as Record<string, string>)[status] ?? sessionStatusLabel(status)}
          </span>
          <span
            title="运行事件走 SSE 实时推送；断线会自动带序号续传"
            className={'rounded-full px-2 py-0.5 text-[11px] ' + (
              streamStatus === 'open' ? 'bg-accent-soft text-accent'
                : streamStatus === 'offline' ? 'bg-danger-soft text-danger'
                  : 'bg-panel-2 text-muted'
            )}
          >
            {streamLabel}
          </span>
        </div>
        {(status === 'running' || canRetry) && <div className="mt-3 flex flex-wrap items-center gap-2">
          {status === 'running' ? (
            <Button size="sm" variant="danger" disabled={runAction !== null} onClick={() => void cancelRun()}>
              {runAction === 'cancel' ? '正在停止…' : '停止运行'}
            </Button>
          ) : (
            <Button size="sm" variant="outline" disabled={runAction !== null || !canRetry} onClick={() => void retryRun()}>
              {runAction === 'retry' ? '正在重新运行…' : status === 'failed' ? '重试最后一问' : '重新生成最后一问'}
            </Button>
          )}
          {runActionError && <span role="alert" className="text-[12px] text-danger">{runActionError}</span>}
        </div>}
        <details className="mt-3 rounded-md border border-line bg-bg px-3 py-2">
          <summary className="cursor-pointer text-[12px] font-medium text-ink-2">查看启动时的配置快照</summary>
          <div className="mt-2 grid gap-2 text-[11.5px] text-muted sm:grid-cols-2">
            <div className="snap-runtime">
              <p className="font-medium text-ink-2">运行参数（本次会话固定）</p>
              {hasRuntime ? (
                <div className="snap-runtime-row">
                  <span>交互模式：<strong>{runtimeLabel('interaction')}</strong></span>
                  <span>回答风格：<strong>{runtimeLabel('style')}</strong></span>
                  <span>计算模式：<strong>{runtimeLabel('mode')}</strong></span>
                </div>
              ) : (
                <p className="mt-1">这个会话创建于运行参数固化之前，没有记录当时的模式设置。</p>
              )}
            </div>
            {agentId && <div className="space-y-1 rounded bg-card p-2">
              <p className="font-medium text-ink-2">子 Agent · {String(agentSnapshot.name || agentId)}</p>
              <p>模型：{String(agentSnapshot.model || '跟随平台默认')}</p>
              <p>权限基线：{String(agentSnapshot.permission_mode || 'inherit')}</p>
              <p>最多轮数：{String(agentSnapshot.max_turns ?? '—')}</p>
              <p>允许工具：{snapshotList(agentSnapshot.tools) || '无'}</p>
              <p>绑定技能：{snapshotList(agentSnapshot.skills) || '无'}</p>
            </div>}
            {expertId && <div className="space-y-1 rounded bg-card p-2">
              <p className="font-medium text-ink-2">专家人设 · {String(expertSnapshot.name || expertId)}</p>
              <p>{String(expertSnapshot.description || '未填写简介')}</p>
              <p>场景：{snapshotList(expertSnapshot.scenarios) || '未限定'}</p>
              <p>表达风格：{String(expertSnapshot.speaking_style || '默认')}</p>
            </div>}
          </div>
          <p className="mt-2 text-[11px] text-muted">会话续聊与重试继续使用这份快照；共享定义后续编辑不会影响当前会话。</p>
        </details>
      </header>

      {/* 需要人工确认的操作 —— SSE 到达时立刻弹出，页面刷新后用接口补回 */}
      {pendingApprovals.length > 0 && (
        <section aria-label="待确认操作" className="space-y-2">
          {approvalError && (
            <p role="alert" className="rounded-md border border-danger/40 bg-danger-soft px-3 py-2 text-[12px] text-danger">
              {approvalError}
            </p>
          )}
          {pendingApprovals.map((item) => (
            <ApprovalCard
              key={item.step_id}
              item={item}
              busy={approvalBusy === item.step_id}
              onDecide={(approved) => void decideApproval(item, approved)}
            />
          ))}
        </section>
      )}

      {/* 思考过程：与正文分开的通道，折叠起来不打扰 */}
      {thinking.text.trim() && (
        <details className="md-think">
          <summary>{thinking.open ? '正在思考…' : '查看思考过程'}</summary>
          <div className="md-think-body">{thinking.text}</div>
        </details>
      )}

      {/* 消息：用户原样显示，助手按 Markdown 渲染；末尾接正在流式的草稿 */}
      <section className="space-y-3">
        {detail.messages.length === 0 && visibleDrafts.length === 0 && (
          <p className="text-[13px] text-muted">这个会话还没有消息。</p>
        )}
        {detail.messages.map((m, index) => {
          // 检查点打在用户提问之前：msg_count === 这条消息在数组里的下标。
          const cp = m.role === 'user'
            ? checkpointForMessage(checkpoints?.checkpoints, index)
            : null
          const canRewindNow = canRewind(status)
          return (
            <div key={m.id} className={'msg-row' + (m.role === 'user' ? ' msg-row--user' : '')}>
              <article
                className={
                  'rounded-[var(--r-lg)] px-3.5 py-2.5 text-[13px] leading-relaxed ' +
                  (m.role === 'user'
                    ? 'ml-auto max-w-[85%] whitespace-pre-wrap bg-user-bg text-user-fg'
                    : 'mr-auto max-w-[92%] border border-line bg-card text-ink')
                }
              >
                {m.role === 'user' ? m.content : <MarkdownBody content={m.content} />}
              </article>
              {cp && (
                <div className="rewindbar">
                  <button
                    type="button"
                    className="rewind-btn"
                    disabled={!canRewindNow}
                    title={canRewindNow ? '回退到这一轮之前' : '运行中不能回退'}
                    onClick={() => setRewindTarget(cp)}
                  >
                    ↺ 回退到这里
                  </button>
                </div>
              )}
            </div>
          )
        })}
        {visibleDrafts.map((d) => (
          <StreamBubble key={d.key} draft={d} />
        ))}
        <div ref={endRef} />
      </section>

      <form onSubmit={sendMessage} className="space-y-2 rounded-[var(--r-lg)] border border-line bg-card p-3">
        <label htmlFor="session-reply" className="text-[12px] font-medium text-ink-2">继续对话</label>
        <textarea id="session-reply" rows={3} maxLength={20000} value={messageDraft}
          onChange={(event) => setMessageDraft(event.target.value)} disabled={sending || String(task.status ?? '') === 'running'}
          placeholder={String(task.status ?? '') === 'running' ? 'Agent 正在运行…' : '补充信息或继续追问；此会话会沿用启动时的配置。'}
          className="w-full resize-y rounded-md border border-line-strong bg-bg px-3 py-2 text-[13px] leading-relaxed text-ink outline-none placeholder:text-muted/70 focus:border-accent focus:ring-2 focus:ring-accent/20 disabled:bg-panel" />
        {sendError && <p role="alert" className="text-[12px] text-danger">{sendError}</p>}
        <div className="flex items-center justify-between gap-3">
          <span className="text-[11px] text-muted">{String(task.status ?? '') === 'running' ? '等待本轮结束后再发送。' : '子 Agent 与专家配置已固定。'}</span>
          <Button type="submit" size="sm" disabled={sending || String(task.status ?? '') === 'running' || !messageDraft.trim()}>
            {sending ? '正在发送…' : '发送'}
          </Button>
        </div>
      </form>

      {/* 待办：空列表也给入口 —— 以前只有 Agent 能建，用户想列个清单都做不到 */}
      <section>
        <h2 className="mb-2 text-[13px] font-semibold text-ink-2">
          待办
          {todoStats.total > 0 && (
            <span className="ml-2 font-normal text-muted">
              {todoStats.done}/{todoStats.total} 已完成
              {todoStats.open > 0 && ` · 还剩 ${todoStats.open} 项`}
            </span>
          )}
        </h2>
        {detail.todos.length > 0 ? (
          <ul className="divide-y divide-line overflow-hidden rounded-[var(--r-lg)] border border-line bg-card">
            {[...openTodos, ...doneTodos].map((t) => (
              <li key={t.id} className="flex items-center gap-2.5 px-3 py-2">
                <input
                  type="checkbox"
                  checked={t.status === 'done'}
                  onChange={() => void toggleTodo(t.id, t.status)}
                  className="h-3.5 w-3.5 accent-[var(--accent)]"
                />
                <span
                  className={
                    'flex-1 text-[13px] ' +
                    (t.status === 'done' ? 'text-muted line-through' : 'text-ink')
                  }
                >
                  {t.title}
                </span>
                <span className="shrink-0 text-[11.5px] text-muted">
                  {TODO_CN[t.status] ?? t.status}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="todo-empty">还没有待办，在下面加一条吧。</p>
        )}
        <form onSubmit={addTodo} className="todo-add">
          <input
            value={todoDraft}
            maxLength={201}
            onChange={(e) => {
              setTodoDraft(e.target.value)
              if (todoError) setTodoError(null)
            }}
            placeholder="加一条待办…"
            className="todo-input"
          />
          <Button type="submit" size="sm" disabled={todoBusy || !todoDraft.trim()}>
            {todoBusy ? '添加中…' : '添加'}
          </Button>
        </form>
        {todoError && <p role="alert" className="todo-error">{todoError}</p>}
      </section>

      {/* 步骤：参数 / 结果 / 报错 / 失败分类 / 只读自证，见 StepList */}
      <StepList steps={detail.steps} extras={stepExtras} />

      {/* 检查点：除每条提问下的按钮外，再给一份完整清单 ——
          重试产生的点可能不落在某条提问上，靠这里也能回退。 */}
      {checkpoints && checkpoints.checkpoints.length > 0 && (
        <details className="cp-panel">
          <summary className="cp-panel-head">
            检查点 · {checkpoints.checkpoints.length} 个可回退位置
          </summary>
          <ul className="cp-list">
            {checkpoints.checkpoints.map((cp) => (
              <li key={cp.id} className={'cp-item' + (cp.current ? ' cp-item--current' : '')}>
                <span className="cp-seq">#{cp.seq + 1}</span>
                <span className="cp-label" title={cp.label || ''}>{cp.label || '（无说明）'}</span>
                <span className="cp-meta">
                  {formatCheckpointTime(cp.created_at)} · {cp.files} 个文件 · 第 {cp.msg_count + 1} 条消息前
                </span>
                <button
                  type="button"
                  className="rewind-btn"
                  disabled={!canRewind(status)}
                  onClick={() => setRewindTarget(cp)}
                >
                  回退
                </button>
              </li>
            ))}
          </ul>
          <p className="cp-hint">检查点在每次提问前自动记录；回退只影响这个会话，不进回收站。</p>
        </details>
      )}

      {/* 用量 */}
      <section>
        <h2 className="mb-2 text-[13px] font-semibold text-ink-2">用量</h2>
        <div className="grid grid-cols-3 gap-3">
          <Metric label="模型调用" value={String(detail.usage.calls)} />
          <Metric label="输入 Token" value={String(detail.usage.input_tokens)} />
          <Metric label="输出 Token" value={String(detail.usage.output_tokens)} />
        </div>
      </section>

      <div className="pb-6">
        <Button variant="outline" size="sm" onClick={onBack}>
          回到总览
        </Button>
      </div>

      <RewindDialog
        checkpoint={rewindTarget}
        totalMessages={detail.messages.length}
        running={status === 'running'}
        onClose={() => setRewindTarget(null)}
        onRewound={handleRewound}
      />
    </div>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-[var(--r-lg)] border border-line bg-card px-3 py-2">
      <div className="text-[11.5px] text-muted">{label}</div>
      <div className="mt-0.5 font-mono text-[15px] text-ink">{value}</div>
    </div>
  )
}

/** 已落库的助手消息：Markdown 只解析一次，长会话不会每次渲染都重新 parse。 */
function MarkdownBody({ content }: { content: string }) {
  const html = useMemo(() => renderMarkdown(content), [content])
  return <div className="md-body" dangerouslySetInnerHTML={{ __html: html }} />
}

/**
 * 流式气泡：输出中用预格式纯文本（半个代码块、半个表格按 Markdown 渲染会抖），
 * 收尾后整体渲染一次 Markdown —— 与老前端 agui.js 的成熟做法一致。
 */
function StreamBubble({ draft }: { draft: StreamDraft }) {
  return (
    <article className="mr-auto max-w-[92%] rounded-[var(--r-lg)] border border-line bg-card px-3.5 py-2.5 text-[13px] leading-relaxed text-ink">
      {draft.done ? (
        <MarkdownBody content={draft.content} />
      ) : (
        <div className="md-body">
          {draft.content
            ? <span className="whitespace-pre-wrap">{draft.content}</span>
            : <span className="text-muted">正在生成回复…</span>}
          <span className="md-caret" aria-hidden="true" />
          <span className="sr-only">正在生成回复</span>
        </div>
      )}
    </article>
  )
}

/** 从锚点往上找最近的可滚动祖先（会话详情根 div 本身就是滚动容器）。 */
function scrollableParent(el: HTMLElement | null): HTMLElement | null {
  let node = el?.parentElement ?? null
  while (node) {
    const overflow = window.getComputedStyle(node).overflowY
    if (overflow === 'auto' || overflow === 'scroll') return node
    node = node.parentElement
  }
  return null
}

function ApprovalCard({
  item,
  busy,
  onDecide,
}: {
  item: PendingApproval
  busy: boolean
  onDecide: (approved: boolean) => void
}) {
  const plan = item.plan || {}
  const steps = Array.isArray(plan.steps) ? plan.steps : []
  const actions = Array.isArray(plan.actions) ? plan.actions : []
  const risk = typeof item.risk === 'number'
    ? RISK_CN[item.risk] ?? `L${item.risk}`
    : '需要确认'
  const reversible = plan.reversible === undefined ? '按工具默认' : plan.reversible ? '可撤销' : '不可撤销'

  return (
    <article className="rounded-[var(--r-lg)] border border-warn/50 bg-warn-soft p-3.5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-[13px] font-semibold text-ink">
            需要你确认：{plan.summary || item.tool || '一个高风险操作'}
          </p>
          <p className="mt-0.5 text-[11.5px] text-muted">
            工具 {item.tool || plan.tool || '—'} · 风险 {risk} · {reversible}
          </p>
        </div>
        <span className="rounded-full border border-warn/40 bg-card px-2 py-0.5 text-[11px] text-warn">
          等待确认
        </span>
      </div>
      {(steps.length > 0 || actions.length > 0) && (
        <ul className="mt-2.5 space-y-1 border-t border-warn/25 pt-2 text-[12px] text-ink-2">
          {steps.length > 0
            ? steps.map((step, index) => (
                <li key={index}>
                  <span className="font-medium">{step.title || '操作'}</span>
                  {step.detail ? `：${step.detail}` : ''}
                  {step.impact ? <span className="text-muted">（{step.impact}）</span> : null}
                </li>
              ))
            : actions.map((action, index) => (
                <li key={index}>
                  <span className="font-medium">{action.kind || '操作'}</span>
                  {action.target ? `：${action.target}` : ''}
                  {action.detail ? <span className="text-muted"> — {action.detail}</span> : null}
                </li>
              ))}
        </ul>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button size="sm" disabled={busy} onClick={() => onDecide(true)}>
          {busy ? '提交中…' : '批准执行'}
        </Button>
        <Button size="sm" variant="outline" disabled={busy} onClick={() => onDecide(false)}>
          拒绝
        </Button>
        <span className="text-[11px] text-muted">
          拒绝后 Agent 会改用不需要确认的方式；也可以直接点「停止运行」。
        </span>
      </div>
    </article>
  )
}
