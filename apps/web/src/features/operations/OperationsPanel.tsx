import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import {
  AlertCircle, Bot, CalendarClock, Check, ChevronRight, Clock3,
  LoaderCircle, MessageSquareText, Pencil, Play, Plus, RefreshCw, RotateCcw,
  Send, ShieldCheck, Trash2, Users, X,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { ApiError } from '@/lib/api'
import { listAgents, type AgentDefinition } from '@/features/agents/api'
import {
  createAutomation, createTeam, deleteAutomation, deleteTeam,
  getDispatchJob, getTeam, listAutomationRuns, listAutomations, listTeams,
  retryAutomationRun, saveTeamConfig, sendTeamMessage, startDispatchJob,
  toggleAutomation, updateAutomation,
  type AutomationDefinition, type AutomationRun, type DispatchJob,
  type TeamDefinition, type TeamMember,
} from './api'
import {
  DISPATCH_MAX_POLLS, DISPATCH_POLL_MS, DISPATCH_TIMEOUT_HINT, SAVE_TEAM_HINT,
  busyKey, canToggle, dispatchResultNotice, dispatchStatusText, draftFromTeam,
  isDispatchRunning, runStatusLabel, shouldContinuePolling, teamCreateBody,
  teamSaveBody, toggleIntent, toggleNotice, validateAutomationDraft,
  validateTeamDraft,
} from './ops'

type Tab = 'teams' | 'automations'
type TeamDraft = { name: string; goal: string; members: TeamMember[] }
type AutomationDraft = { name: string; cron: string; goal: string }

const emptyTeam: TeamDraft = { name: '', goal: '', members: [] }
const emptyAutomation: AutomationDraft = { name: '', cron: '0 9 * * 1-5', goal: '' }
const friendlyError = (error: unknown, fallback: string) =>
  error instanceof ApiError || error instanceof Error ? error.message : fallback

function cn(...parts: Array<string | false | undefined>) {
  return parts.filter(Boolean).join(' ')
}

function formatBeijingTime(value: number | null | undefined) {
  if (!value) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(value * 1000))
}

function SectionTitle({ eyebrow, title, detail, action }: {
  eyebrow: string; title: string; detail: string; action?: ReactNode
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div>
        <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-accent">{eyebrow}</p>
        <h1 className="mt-1 text-[24px] font-semibold tracking-tight text-ink">{title}</h1>
        <p className="mt-1 text-[13px] text-muted">{detail}</p>
      </div>
      {action}
    </div>
  )
}

function Notice({ children, tone = 'error' }: { children: ReactNode; tone?: 'error' | 'success' }) {
  return (
    <div role={tone === 'error' ? 'alert' : 'status'} className={cn(
      'flex items-start gap-2 rounded-[var(--r)] border px-3 py-2.5 text-[13px]',
      tone === 'error'
        ? 'border-danger/30 bg-danger-soft text-danger'
        : 'border-ok/30 bg-ok-soft text-ok',
    )}>
      {tone === 'error' ? <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" /> : <Check className="mt-0.5 h-4 w-4 shrink-0" />}
      <span>{children}</span>
    </div>
  )
}

function EmptyState({ icon: Icon, title, detail, action }: {
  icon: LucideIcon; title: string; detail: string; action?: ReactNode
}) {
  return (
    <div className="grid min-h-60 place-items-center rounded-[var(--r-lg)] border border-dashed border-line-strong bg-card px-6 py-10 text-center">
      <div className="flex max-w-sm flex-col items-center">
        <span className="grid h-11 w-11 place-items-center rounded-xl bg-accent-soft text-accent"><Icon className="h-5 w-5" /></span>
        <h2 className="mt-3 text-[15px] font-semibold text-ink">{title}</h2>
        <p className="mt-1 text-[13px] text-muted">{detail}</p>
        {action && <div className="mt-4">{action}</div>}
      </div>
    </div>
  )
}

export default function OperationsPanel() {
  const [tab, setTab] = useState<Tab>('teams')
  const [teams, setTeams] = useState<TeamDefinition[]>([])
  const [automations, setAutomations] = useState<AutomationDefinition[]>([])
  const [agents, setAgents] = useState<AgentDefinition[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const reload = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [teamResult, automationResult, agentResult] = await Promise.all([
        listTeams(), listAutomations(), listAgents(),
      ])
      setTeams(teamResult.teams)
      setAutomations(automationResult.automations)
      setAgents(agentResult)
    } catch (cause) {
      setError(friendlyError(cause, '加载团队和自动任务失败'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void reload() }, [reload])

  return (
    <div className="mx-auto flex w-full max-w-[1440px] flex-col gap-6">
      <SectionTitle
        eyebrow="OPERATIONS"
        title="团队与自动任务"
        detail="管理可复用的 Agent 团队，并安排按北京时间运行的自动任务。"
        action={<Button variant="outline" size="sm" onClick={() => void reload()} disabled={loading}>
          {loading ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          刷新
        </Button>}
      />

      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-3">
        <div className="flex gap-2" role="tablist" aria-label="运营模块">
          <Button role="tab" aria-selected={tab === 'teams'} variant={tab === 'teams' ? 'default' : 'ghost'} size="sm" onClick={() => { setTab('teams'); setError(null); setNotice(null) }}>
            <Users className="h-4 w-4" /> 团队 <span className="rounded-full bg-black/10 px-1.5 py-0.5 text-[10px]">{teams.length}</span>
          </Button>
          <Button role="tab" aria-selected={tab === 'automations'} variant={tab === 'automations' ? 'default' : 'ghost'} size="sm" onClick={() => { setTab('automations'); setError(null); setNotice(null) }}>
            <CalendarClock className="h-4 w-4" /> 自动任务 <span className="rounded-full bg-black/10 px-1.5 py-0.5 text-[10px]">{automations.length}</span>
          </Button>
        </div>
        <p className="flex items-center gap-1.5 text-[12px] text-muted">
          <ShieldCheck className="h-4 w-4 text-ok" /> 团队定义共享，对话记录按账号隔离
        </p>
      </div>

      {error && <Notice>{error}</Notice>}
      {notice && <Notice tone="success">{notice}</Notice>}
      {loading && teams.length === 0 && automations.length === 0 ? (
        <div className="grid min-h-64 place-items-center text-[13px] text-muted">
          <span className="flex items-center gap-2"><LoaderCircle className="h-4 w-4 animate-spin" />正在加载…</span>
        </div>
      ) : tab === 'teams' ? (
        <TeamsView teams={teams} agents={agents} onReload={reload} setError={setError} setNotice={setNotice} />
      ) : (
        <AutomationsView items={automations} onReload={reload} setError={setError} setNotice={setNotice} />
      )}
    </div>
  )
}

function TeamsView({ teams, agents, onReload, setError, setNotice }: {
  teams: TeamDefinition[]; agents: AgentDefinition[]; onReload: () => Promise<void>
  setError: (value: string | null) => void; setNotice: (value: string | null) => void
}) {
  const [selectedId, setSelectedId] = useState<string | null>(teams[0]?.id ?? null)
  const [detail, setDetail] = useState<TeamDefinition | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [draft, setDraft] = useState<TeamDraft>(emptyTeam)
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [messageTo, setMessageTo] = useState('')
  const [message, setMessage] = useState('')
  const [sending, setSending] = useState(false)
  const [dispatchJobs, setDispatchJobs] = useState<Record<string, DispatchJob>>({})

  // 轮询的定时器与卸载标记：切 tab / 关闭页面后不能再往 state 里写。
  const timers = useRef<number[]>([])
  const alive = useRef(true)
  useEffect(() => () => {
    alive.current = false
    timers.current.forEach((t) => window.clearTimeout(t))
  }, [])

  const selectTeam = useCallback(async (team: TeamDefinition) => {
    setSelectedId(team.id)
    setCreating(false)
    setEditing(false)
    setError(null)
    setNotice(null)
    setDetailLoading(true)
    try {
      const latest = await getTeam(team.name)
      setDetail(latest)
      setDraft(draftFromTeam(latest))
      setMessageTo(latest.members[0]?.agent_id ?? '')
    } catch (cause) {
      setError(friendlyError(cause, '无法加载团队详情'))
    } finally {
      setDetailLoading(false)
    }
  }, [setError, setNotice])

  useEffect(() => {
    if (!teams.length) {
      setSelectedId(null)
      setDetail(null)
      setCreating(false)
      setEditing(false)
      return
    }
    const selected = teams.find((team) => team.id === selectedId) ?? teams[0]
    if (selected.id !== selectedId) setSelectedId(selected.id)
    if (!detail || detail.id !== selected.id) void selectTeam(selected)
  }, [teams, selectedId, detail, selectTeam])

  const startCreate = () => {
    setCreating(true)
    setEditing(false)
    setDetail(null)
    setSelectedId(null)
    setDraft(emptyTeam)
    setError(null)
    setNotice(null)
  }

  const toggleAgent = (agentId: string) => {
    setDraft((current) => {
      const found = current.members.find((item) => item.agent_id === agentId)
      return {
        ...current,
        members: found
          ? current.members.filter((item) => item.agent_id !== agentId)
          : [...current.members, { agent_id: agentId, role: '成员' }],
      }
    })
  }

  const changeRole = (agentId: string, role: string) => setDraft((current) => ({
    ...current, members: current.members.map((item) => item.agent_id === agentId ? { ...item, role } : item),
  }))

  /** 整体保存：一次请求，失败整单不动（草稿原样留着让用户重试）。 */
  const saveTeam = async (event: FormEvent) => {
    event.preventDefault()
    if (saving) return
    const problem = validateTeamDraft(draft)
    if (problem) {
      setError(problem.message)
      return
    }
    setSaving(true)
    setError(null)
    setNotice(null)
    try {
      let savedName = draft.name.trim()
      let savedId: string | null = null
      if (creating) {
        const created = await createTeam(teamCreateBody(draft))
        savedId = created.id
      } else if (detail) {
        const result = await saveTeamConfig(detail.name, teamSaveBody(draft))
        savedName = result.team.name
        savedId = result.team.id
      }
      setCreating(false)
      setEditing(false)
      setNotice('团队已保存')
      await onReload()
      if (savedId) setSelectedId(savedId)
      const refreshed = await getTeam(savedName)
      setDetail(refreshed)
      setDraft(draftFromTeam(refreshed))
    } catch (cause) {
      setError(friendlyError(cause, '保存团队失败'))
    } finally {
      setSaving(false)
    }
  }

  const remove = async () => {
    if (!detail || saving) return
    if (!window.confirm(`删除共享团队“${detail.name}”？其他账号也将无法继续使用该团队定义。`)) return
    setSaving(true)
    setError(null)
    try {
      await deleteTeam(detail.name)
      setDetail(null)
      setSelectedId(null)
      setNotice('团队已删除')
      await onReload()
    } catch (cause) {
      setError(friendlyError(cause, '删除团队失败'))
    } finally {
      setSaving(false)
    }
  }

  const send = async (event: FormEvent) => {
    event.preventDefault()
    if (!detail || !messageTo || !message.trim() || sending) return
    setSending(true)
    setError(null)
    try {
      await sendTeamMessage(detail.name, messageTo, message.trim())
      setMessage('')
      setNotice('任务消息已投递到你的团队信箱')
      setDetail(await getTeam(detail.name))
    } catch (cause) {
      setError(friendlyError(cause, '投递消息失败'))
    } finally {
      setSending(false)
    }
  }

  /** 轮询后台派发作业，直到 done / failed 或超过上限。 */
  const pollDispatch = async (
    teamName: string, agentId: string, agentName: string, jobId: string, polls: number,
  ): Promise<void> => {
    if (!alive.current) return
    if (polls >= DISPATCH_MAX_POLLS) {
      setNotice(DISPATCH_TIMEOUT_HINT)
      setDispatchJobs((current) => {
        const next = { ...current }
        delete next[agentId]
        return next
      })
      return
    }
    await new Promise<void>((resolve) => {
      const timer = window.setTimeout(() => resolve(), DISPATCH_POLL_MS)
      timers.current.push(timer)
    })
    if (!alive.current) return
    try {
      const job = await getDispatchJob(teamName, jobId)
      setDispatchJobs((current) => ({ ...current, [agentId]: job }))
      if (shouldContinuePolling(job, polls + 1)) {
        void pollDispatch(teamName, agentId, agentName, jobId, polls + 1)
        return
      }
      const text = dispatchResultNotice(job, agentName)
      if (job.status === 'failed') setError(text)
      else setNotice(text)
      // 结论已经回投到信箱，刷新详情让 thread 跟上（失败不掩盖派发结果）。
      try {
        setDetail(await getTeam(teamName))
      } catch {
        /* 团队可能刚被改名或删掉，信箱结论仍持久化着 */
      }
      setDispatchJobs((current) => {
        const next = { ...current }
        delete next[agentId]
        return next
      })
    } catch (cause) {
      setError(friendlyError(cause, '查询派发状态失败'))
      setDispatchJobs((current) => {
        const next = { ...current }
        delete next[agentId]
        return next
      })
    }
  }

  const dispatch = async (agentId: string) => {
    if (!detail || isDispatchRunning(dispatchJobs[agentId])) return
    const teamName = detail.name
    const agentName = agents.find((a) => a.id === agentId)?.name ?? agentId
    setError(null)
    setNotice(null)
    try {
      const job = await startDispatchJob(teamName, agentId)
      setDispatchJobs((current) => ({ ...current, [agentId]: job }))
      void pollDispatch(teamName, agentId, agentName, job.job_id, 0)
    } catch (cause) {
      setError(friendlyError(cause, '派发子 Agent 失败'))
    }
  }

  const beginEdit = () => {
    if (!detail) return
    setDraft(draftFromTeam(detail))
    setEditing(true)
    setCreating(false)
    setError(null)
  }

  const agentById = useMemo(() => new Map(agents.map((agent) => [agent.id, agent])), [agents])
  const editingNow = creating || editing

  return (
    <div className="grid min-w-0 gap-4 xl:grid-cols-[300px_minmax(0,1fr)]">
      <aside className="flex flex-col gap-3">
        <Button className="w-full justify-center" onClick={startCreate}><Plus className="h-4 w-4" />新建团队</Button>
        {teams.length === 0 && !creating ? (
          <EmptyState icon={Users} title="还没有团队" detail="创建一个团队，组合适合协作的子 Agent 与职责。" action={<Button size="sm" onClick={startCreate}>创建第一个团队</Button>} />
        ) : (
          <div className="flex flex-col gap-2">
            {teams.map((team) => (
              <button key={team.id} onClick={() => void selectTeam(team)} aria-current={team.id === selectedId}
                className={cn('rounded-[var(--r-lg)] border bg-card p-3 text-left transition-colors hover:border-accent-line', team.id === selectedId && 'border-accent-line bg-accent-soft/40')}>
                <span className="flex items-center justify-between gap-2">
                  <span className="truncate text-[13px] font-semibold text-ink">{team.name}</span>
                  {team.can_edit ? <Pencil className="h-3.5 w-3.5 text-muted" /> : <ShieldCheck className="h-3.5 w-3.5 text-muted" />}
                </span>
                <span className="mt-1.5 flex items-center gap-2 text-[11px] text-muted">
                  <span>{team.members.length} 位成员</span><span>·</span><span>{team.shared ? '共享只读' : '由你创建'}</span>
                </span>
                <span className="mt-2 block line-clamp-2 text-[12px] text-ink-2">{team.goal || '尚未填写团队目标'}</span>
              </button>
            ))}
          </div>
        )}
      </aside>

      <section className="min-w-0 rounded-[var(--r-lg)] border border-line bg-card p-4 sm:p-5">
        {detailLoading ? (
          <div role="status" className="grid min-h-60 place-items-center text-[13px] text-muted"><span className="flex items-center gap-2"><LoaderCircle className="h-4 w-4 animate-spin" />正在加载团队详情…</span></div>
        ) : editingNow ? (
          <form onSubmit={(event) => void saveTeam(event)} className="flex flex-col gap-4">
            <div className="flex items-start justify-between gap-3">
              <div><p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-accent">TEAM DEFINITION</p><h2 className="mt-1 text-[18px] font-semibold text-ink">{creating ? '创建团队' : '编辑团队'}</h2></div>
              <Button type="button" variant="ghost" size="icon" aria-label="取消编辑" onClick={() => { setCreating(false); setEditing(false); if (detail) void selectTeam(detail) }}><X className="h-4 w-4" /></Button>
            </div>
            <label className="grid gap-1.5 text-[12px] font-medium text-ink-2">团队名称
              <input required maxLength={80} value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="例如：产品研究小组" className="h-10 rounded-[var(--r)] border border-line-strong bg-bg px-3 text-[13px] text-ink outline-none focus:border-accent" />
            </label>
            <label className="grid gap-1.5 text-[12px] font-medium text-ink-2">团队目标
              <textarea maxLength={4000} rows={3} value={draft.goal} onChange={(e) => setDraft({ ...draft, goal: e.target.value })} placeholder="说明这个团队负责什么，以及完成标准。" className="resize-y rounded-[var(--r)] border border-line-strong bg-bg px-3 py-2 text-[13px] text-ink outline-none focus:border-accent" />
            </label>
            <div>
              <div className="mb-2 flex items-center justify-between"><span className="text-[12px] font-medium text-ink-2">子 Agent 与职责</span><span className="text-[11px] text-muted">至少 1 位，最多 8 位</span></div>
              {agents.length === 0 ? <p className="rounded-md bg-warn-soft p-3 text-[12px] text-warn">尚无可选子 Agent，请先到 Agent 管理中创建。</p> : (
                <div className="flex max-h-72 flex-col gap-2 overflow-y-auto rounded-[var(--r)] border border-line p-2">
                  {Array.from(new Set([...agents.map((agent) => agent.id), ...draft.members.map((member) => member.agent_id)])).map((agentId) => {
                    const member = draft.members.find((value) => value.agent_id === agentId)
                    const agent = agentById.get(agentId)
                    return (
                      <div key={agentId} className="flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-panel">
                        <input aria-label={`选择${agent?.name ?? agentId}`} type="checkbox" checked={Boolean(member)} onChange={() => toggleAgent(agentId)} className="h-4 w-4 accent-[var(--accent)]" />
                        <span className={cn('min-w-0 flex-1 truncate text-[12px]', agent ? 'text-ink' : 'text-danger')}>{agent?.name ?? `${agentId}（当前不可用）`}</span>
                        {member && <input aria-label={`${agent?.name ?? agentId}的职责`} maxLength={80} value={member.role} onChange={(e) => changeRole(agentId, e.target.value)} placeholder="职责" className="h-8 w-32 rounded border border-line bg-bg px-2 text-[12px] outline-none focus:border-accent" />}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
            <p className="op-save-hint">{SAVE_TEAM_HINT}</p>
            <div className="flex justify-end gap-2 border-t border-line pt-3">
              <Button type="button" variant="outline" onClick={() => { setCreating(false); setEditing(false); if (detail) void selectTeam(detail) }}>取消</Button>
              <Button type="submit" disabled={saving || draft.members.length === 0}>{saving ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}保存团队</Button>
            </div>
          </form>
        ) : detail ? (
          <div className="flex flex-col gap-5">
            <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line pb-4">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2"><h2 className="text-[19px] font-semibold text-ink">{detail.name}</h2><span className="rounded-full bg-panel px-2 py-0.5 text-[10px] text-muted">{detail.can_edit ? '你创建的共享团队' : '共享团队 · 只读定义'}</span></div>
                <p className="mt-1 max-w-2xl whitespace-pre-wrap text-[13px] text-ink-2">{detail.goal || '尚未填写团队目标'}</p>
              </div>
              {detail.can_edit && <div className="flex gap-2"><Button variant="outline" size="sm" onClick={beginEdit}><Pencil className="h-3.5 w-3.5" />编辑</Button><Button variant="danger" size="sm" disabled={saving} onClick={() => void remove()}><Trash2 className="h-3.5 w-3.5" />删除</Button></div>}
            </div>
            <div>
              <div className="mb-2 flex items-center gap-2 text-[12px] font-semibold text-ink"><Users className="h-4 w-4 text-accent" />团队成员 <span className="font-normal text-muted">{detail.members.length} 位</span></div>
              <div className="grid gap-2 sm:grid-cols-2">
                {detail.members.map((member) => {
                  const agent = agentById.get(member.agent_id)
                  const hasPendingTask = detail.thread.some((item) => item.recipient === member.agent_id && !item.read)
                  const job = dispatchJobs[member.agent_id]
                  const running = isDispatchRunning(job)
                  return <div key={member.agent_id} className="flex min-w-0 items-center gap-3 rounded-[var(--r)] border border-line bg-bg px-3 py-2.5">
                    <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-accent-soft text-accent"><Bot className="h-4 w-4" /></span>
                    <span className="min-w-0 flex-1"><span className="block truncate text-[12px] font-medium text-ink">{agent?.name ?? member.agent_id}</span><span className="block truncate text-[11px] text-muted">{member.role || '成员'}{agent?.subtitle ? ` · ${agent.subtitle}` : ''}</span>
                      {job && <span className={cn('op-dispatch-state', `op-dispatch-state--${job.status}`)}>{dispatchStatusText(job)}</span>}
                    </span>
                    <Button variant="ghost" size="sm" disabled={running || !hasPendingTask} onClick={() => void dispatch(member.agent_id)} title={running ? '这个成员正在派发中' : hasPendingTask ? '派发待执行任务' : '此成员当前没有待派发任务'}>
                      {running ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
                      <span className="sr-only">派发</span>
                    </Button>
                  </div>
                })}
              </div>
            </div>
            <div className="rounded-[var(--r-lg)] border border-line bg-bg p-3 sm:p-4">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2"><div className="flex items-center gap-2 text-[12px] font-semibold text-ink"><MessageSquareText className="h-4 w-4 text-accent" />我的团队信箱</div><span className="text-[11px] text-muted">消息与运行结果仅对当前账号可见</span></div>
              {detail.thread.length ? <div className="mb-3 flex max-h-64 flex-col gap-2 overflow-y-auto">
                {detail.thread.map((item) => <div key={item.id} className="rounded-md border border-line bg-card px-3 py-2">
                  <div className="flex items-center justify-between gap-2 text-[10px] text-muted"><span>{item.sender} → {item.recipient}</span><time>{formatBeijingTime(item.ts)}</time></div>
                  <p className="mt-1 whitespace-pre-wrap break-words text-[12px] text-ink-2">{item.content}</p>
                </div>)}
              </div> : <p className="mb-3 rounded-md border border-dashed border-line px-3 py-4 text-center text-[12px] text-muted">还没有投递记录。先写一条任务消息，再派发给对应成员。</p>}
              <form onSubmit={(event) => void send(event)} className="grid gap-2 sm:grid-cols-[180px_minmax(0,1fr)_auto]">
                <select aria-label="任务接收成员" value={messageTo} onChange={(e) => setMessageTo(e.target.value)} className="h-9 rounded-[var(--r)] border border-line-strong bg-card px-2 text-[12px] text-ink outline-none focus:border-accent">
                  {detail.members.map((member) => <option key={member.agent_id} value={member.agent_id}>{agentById.get(member.agent_id)?.name ?? member.agent_id}</option>)}
                </select>
                <input value={message} onChange={(e) => setMessage(e.target.value)} maxLength={4000} placeholder="描述要交给成员执行的任务…" className="h-9 min-w-0 rounded-[var(--r)] border border-line-strong bg-card px-3 text-[12px] text-ink outline-none focus:border-accent" />
                <Button size="sm" type="submit" disabled={sending || !message.trim()}>{sending ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}投递</Button>
              </form>
              {detail.thread.some((item) => item.recipient === messageTo && !item.read) && (
                <div className="mt-2 flex justify-end">
                  <Button variant="ghost" size="sm" disabled={isDispatchRunning(dispatchJobs[messageTo])}
                    onClick={() => void dispatch(messageTo)}>
                    {isDispatchRunning(dispatchJobs[messageTo]) ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
                    派发给 {agentById.get(messageTo)?.name ?? messageTo}
                  </Button>
                </div>
              )}
            </div>
          </div>
        ) : (
          <EmptyState icon={Users} title="选择或创建一个团队" detail="可查看共享团队定义。只有创建者可以修改，团队信箱按账号分别保存。" action={<Button variant="outline" size="sm" onClick={startCreate}><Plus className="h-4 w-4" />新建团队</Button>} />
        )}
      </section>
    </div>
  )
}

function AutomationsView({ items, onReload, setError, setNotice }: {
  items: AutomationDefinition[]; onReload: () => Promise<void>
  setError: (value: string | null) => void; setNotice: (value: string | null) => void
}) {
  const [selectedId, setSelectedId] = useState<string | null>(items[0]?.id ?? null)
  const [runs, setRuns] = useState<AutomationRun[]>([])
  const [runsLoading, setRunsLoading] = useState(false)
  const [draft, setDraft] = useState<AutomationDraft>(emptyAutomation)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [saving, setSaving] = useState(false)
  // 一个 key 一个在途动作：启停 / 删除 / 重试各自独立，互不误伤。
  const [busy, setBusy] = useState<Record<string, boolean>>({})
  const busySet = useMemo(
    () => new Set(Object.entries(busy).filter(([, on]) => on).map(([key]) => key)),
    [busy],
  )
  const mark = (key: string, on: boolean) => setBusy((current) => ({ ...current, [key]: on }))

  const selected = useMemo(() => items.find((item) => item.id === selectedId) ?? null, [items, selectedId])

  const loadRuns = useCallback(async (itemId: string) => {
    setRunsLoading(true)
    setRuns([])
    try {
      const result = await listAutomationRuns(itemId)
      setRuns(result.runs)
    } catch (cause) {
      setError(friendlyError(cause, '加载运行历史失败'))
    } finally {
      setRunsLoading(false)
    }
  }, [setError])

  useEffect(() => {
    if (!items.length) {
      setSelectedId(null)
      setRuns([])
      return
    }
    const current = items.find((item) => item.id === selectedId) ?? items[0]
    if (current.id !== selectedId) setSelectedId(current.id)
    if (current.id !== selectedId || runs.length === 0) void loadRuns(current.id)
  }, [items, selectedId, loadRuns])

  const choose = (item: AutomationDefinition) => {
    setSelectedId(item.id)
    setEditingId(null)
    setCreating(false)
    setError(null)
    void loadRuns(item.id)
  }

  const startCreate = () => {
    setCreating(true)
    setEditingId(null)
    setSelectedId(null)
    setDraft(emptyAutomation)
    setError(null)
    setNotice(null)
  }

  const startEdit = (item: AutomationDefinition) => {
    setDraft({ name: item.name, cron: item.cron, goal: item.goal })
    setEditingId(item.id)
    setCreating(false)
    setSelectedId(item.id)
    setError(null)
  }

  const save = async (event: FormEvent) => {
    event.preventDefault()
    if (saving) return
    const problem = validateAutomationDraft(draft)
    if (problem) {
      setError(problem.message)
      return
    }
    setSaving(true)
    setError(null)
    try {
      let savedId: string | null = null
      if (creating) {
        const created = await createAutomation({ name: draft.name.trim(), cron: draft.cron.trim(), goal: draft.goal.trim() })
        savedId = created.id
      } else if (editingId) {
        const updated = await updateAutomation(editingId, { name: draft.name.trim(), cron: draft.cron.trim(), goal: draft.goal.trim() })
        savedId = updated.updated.id
      }
      setCreating(false)
      setEditingId(null)
      setNotice('自动任务已保存')
      await onReload()
      if (savedId) {
        setSelectedId(savedId)
        void loadRuns(savedId)
      }
    } catch (cause) {
      setError(friendlyError(cause, '保存自动任务失败'))
    } finally {
      setSaving(false)
    }
  }

  /** 启停幂等 + 防重复：在途时直接忽略，文案按目标态算。 */
  const toggle = async (item: AutomationDefinition) => {
    const key = busyKey('toggle', item.id)
    if (!canToggle(item, busySet)) return
    setError(null)
    setNotice(null)
    mark(key, true)
    const target = toggleIntent(item)
    try {
      await toggleAutomation(item.id, target)
      setNotice(toggleNotice(target))
      await onReload()
    } catch (cause) {
      setError(friendlyError(cause, '切换自动任务状态失败'))
    } finally {
      mark(key, false)
    }
  }

  const remove = async (item: AutomationDefinition) => {
    const key = busyKey('delete', item.id)
    if (busySet.has(key)) return
    if (!window.confirm(`删除自动任务“${item.name}”？已有运行历史仍会保留。`)) return
    setError(null)
    setNotice(null)
    mark(key, true)
    try {
      await deleteAutomation(item.id)
      setSelectedId(null)
      setNotice('自动任务已删除')
      await onReload()
    } catch (cause) {
      setError(friendlyError(cause, '删除自动任务失败'))
    } finally {
      mark(key, false)
    }
  }

  const retry = async (run: AutomationRun) => {
    const key = busyKey('retry', run.id)
    if (busySet.has(key)) return
    setError(null)
    setNotice(null)
    mark(key, true)
    try {
      await retryAutomationRun(run.id)
      setNotice('失败记录已加入手动重试队列；完成后刷新历史查看结果')
    } catch (cause) {
      setError(friendlyError(cause, '手动重试失败'))
    } finally {
      mark(key, false)
    }
  }

  const editing = creating || editingId !== null

  return (
    <div className="grid min-w-0 gap-4 xl:grid-cols-[350px_minmax(0,1fr)]">
      <aside className="flex flex-col gap-3">
        <Button className="w-full justify-center" onClick={startCreate}><Plus className="h-4 w-4" />新建自动任务</Button>
        {items.length === 0 && !creating ? <EmptyState icon={CalendarClock} title="还没有自动任务" detail="设置 Cron 时间与执行目标，系统将按北京时间定期运行。" action={<Button size="sm" onClick={startCreate}>创建第一个任务</Button>} /> : (
          <div className="flex flex-col gap-2">
            {items.map((item) => {
              const toggleKey = busyKey('toggle', item.id)
              const deleteKey = busyKey('delete', item.id)
              return <div key={item.id} className={cn('rounded-[var(--r-lg)] border bg-card p-3 transition-colors', item.id === selectedId && 'border-accent-line bg-accent-soft/40')}>
                <button className="w-full text-left" onClick={() => choose(item)}>
                  <span className="flex items-start justify-between gap-2"><span className="min-w-0 truncate text-[13px] font-semibold text-ink">{item.name}</span><span className={cn('shrink-0 rounded-full px-2 py-0.5 text-[10px]', item.enabled ? 'bg-ok-soft text-ok' : 'bg-panel text-muted')}>{item.enabled ? '运行中' : '已停用'}</span></span>
                  <span className="mt-1 block font-mono text-[11px] text-muted">{item.cron} · 北京时间</span>
                  <span className="mt-1.5 block line-clamp-2 text-[12px] text-ink-2">{item.goal}</span>
                </button>
                <div className="mt-3 flex items-center justify-between border-t border-line pt-2">
                  <span className="text-[10px] text-muted">{item.retrying ? `失败重试 ${item.retry_count}/${item.max_retries}` : `${item.run_count} 次运行`}</span>
                  <div className="flex gap-1">
                    <Button variant="ghost" size="sm" disabled={!canToggle(item, busySet)} onClick={() => void toggle(item)}>
                      {busySet.has(toggleKey) && <LoaderCircle className="h-3.5 w-3.5 animate-spin" />}
                      {item.enabled ? '停用' : '启用'}
                    </Button>
                    <Button variant="ghost" size="icon" aria-label={`编辑${item.name}`} onClick={() => startEdit(item)}><Pencil className="h-3.5 w-3.5" /></Button>
                    <Button variant="ghost" size="icon" aria-label={`删除${item.name}`} disabled={busySet.has(deleteKey)} onClick={() => void remove(item)}><Trash2 className="h-3.5 w-3.5 text-danger" /></Button>
                  </div>
                </div>
              </div>
            })}
          </div>
        )}
      </aside>

      <section className="min-w-0 rounded-[var(--r-lg)] border border-line bg-card p-4 sm:p-5">
        {editing ? <form onSubmit={(event) => void save(event)} className="flex flex-col gap-4">
          <div className="flex items-start justify-between gap-3"><div><p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-accent">SCHEDULE</p><h2 className="mt-1 text-[18px] font-semibold text-ink">{creating ? '创建自动任务' : '编辑自动任务'}</h2></div><Button type="button" variant="ghost" size="icon" aria-label="取消编辑" onClick={() => { setCreating(false); setEditingId(null) }}><X className="h-4 w-4" /></Button></div>
          <label className="grid gap-1.5 text-[12px] font-medium text-ink-2">任务名称
            <input required maxLength={80} value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="例如：工作日早间简报" className="h-10 rounded-[var(--r)] border border-line-strong bg-bg px-3 text-[13px] text-ink outline-none focus:border-accent" />
          </label>
          <label className="grid gap-1.5 text-[12px] font-medium text-ink-2">Cron 表达式 <span className="font-normal text-muted">按北京时间（Asia/Shanghai）解释</span>
            <input required maxLength={100} value={draft.cron} onChange={(e) => setDraft({ ...draft, cron: e.target.value })} placeholder="分 时 日 月 周，例如 0 9 * * 1-5" className="h-10 rounded-[var(--r)] border border-line-strong bg-bg px-3 font-mono text-[13px] text-ink outline-none focus:border-accent" />
          </label>
          <div className="flex flex-wrap gap-2 text-[11px] text-muted"><span className="rounded-full bg-panel px-2 py-1">0 9 * * 1-5 工作日 09:00</span><span className="rounded-full bg-panel px-2 py-1">0 */2 * * * 每两小时</span><span className="rounded-full bg-panel px-2 py-1">0 10 * * 1 每周一 10:00</span></div>
          <label className="grid gap-1.5 text-[12px] font-medium text-ink-2">执行目标
            <textarea required maxLength={8000} rows={5} value={draft.goal} onChange={(e) => setDraft({ ...draft, goal: e.target.value })} placeholder="描述每次触发时 Agent 要完成的工作。" className="resize-y rounded-[var(--r)] border border-line-strong bg-bg px-3 py-2 text-[13px] text-ink outline-none focus:border-accent" />
          </label>
          <div className="flex items-start gap-2 rounded-md bg-accent-soft px-3 py-2.5 text-[12px] text-ink-2"><RotateCcw className="mt-0.5 h-4 w-4 shrink-0 text-accent" /><span>失败后自动重试最多 3 次，间隔逐步延长（1、2、4 分钟）。你也可以从历史记录手动重试。</span></div>
          <div className="flex justify-end gap-2 border-t border-line pt-3"><Button type="button" variant="outline" onClick={() => { setCreating(false); setEditingId(null) }}>取消</Button><Button type="submit" disabled={saving}>{saving ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}保存任务</Button></div>
        </form> : selected ? <div className="flex flex-col gap-5">
          <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line pb-4">
            <div><p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-accent">AUTOMATION</p><h2 className="mt-1 text-[19px] font-semibold text-ink">{selected.name}</h2><div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-muted"><span className="rounded bg-panel px-2 py-1 font-mono">{selected.cron}</span><span>北京时间</span><span>·</span><span>{selected.enabled ? '已启用' : '已停用'}</span></div></div>
            <div className="flex gap-2"><Button variant="outline" size="sm" onClick={() => startEdit(selected)}><Pencil className="h-3.5 w-3.5" />编辑</Button><Button variant={selected.enabled ? 'outline' : 'default'} size="sm" disabled={!canToggle(selected, busySet)} onClick={() => void toggle(selected)}>{selected.enabled ? '停用' : '启用'}</Button></div>
          </div>
          <p className="whitespace-pre-wrap text-[13px] text-ink-2">{selected.goal}</p>
          <div className="grid gap-2 sm:grid-cols-3">
            <div className="rounded-md border border-line bg-bg p-3"><span className="text-[11px] text-muted">下次运行</span><span className="mt-1 block text-[13px] font-medium text-ink">{formatBeijingTime(selected.next_run)}</span></div>
            <div className="rounded-md border border-line bg-bg p-3"><span className="text-[11px] text-muted">上次运行</span><span className="mt-1 block text-[13px] font-medium text-ink">{formatBeijingTime(selected.last_run)}</span></div>
            <div className="rounded-md border border-line bg-bg p-3"><span className="text-[11px] text-muted">失败重试</span><span className="mt-1 block text-[13px] font-medium text-ink">最多 {selected.max_retries} 次 · {selected.retrying ? `已排队 ${selected.retry_count}/${selected.max_retries}` : '逐步延长间隔'}</span></div>
          </div>
          <div>
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2"><div><h3 className="text-[14px] font-semibold text-ink">运行历史</h3><p className="text-[11px] text-muted">每次自动重试与手动重试都会单独记录。</p></div><Button variant="outline" size="sm" disabled={runsLoading} onClick={() => void loadRuns(selected.id)}>{runsLoading ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}刷新历史</Button></div>
            {runsLoading && runs.length === 0 ? <div className="grid min-h-28 place-items-center text-[12px] text-muted"><LoaderCircle className="h-4 w-4 animate-spin" /></div> : runs.length === 0 ? <div className="rounded-md border border-dashed border-line px-4 py-8 text-center text-[12px] text-muted">暂无运行记录；任务触发后会显示状态、产出和失败原因。</div> : (
              <div className="flex flex-col gap-2">
                {runs.map((run) => <RunHistoryCard key={run.id} run={run} retrying={busySet.has(busyKey('retry', run.id))} onRetry={() => void retry(run)} />)}
              </div>
            )}
          </div>
        </div> : <EmptyState icon={CalendarClock} title="选择或创建一个自动任务" detail="自动任务在服务重启后保留，使用北京时间运行并记录每次结果。" action={<Button variant="outline" size="sm" onClick={startCreate}><Plus className="h-4 w-4" />创建自动任务</Button>} />}
      </section>
    </div>
  )
}

function RunHistoryCard({ run, retrying, onRetry }: {
  run: AutomationRun; retrying: boolean; onRetry: () => void
}) {
  const [expanded, setExpanded] = useState(run.status === 'failed')
  const statusLabel = runStatusLabel(run.status)
  return (
    <article className="rounded-[var(--r)] border border-line bg-bg px-3 py-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span className={cn('rounded-full px-2 py-0.5 text-[10px] font-medium', run.status === 'done' ? 'bg-ok-soft text-ok' : run.status === 'failed' ? 'bg-danger-soft text-danger' : 'bg-warn-soft text-warn')}>{statusLabel}</span>
        <time className="flex items-center gap-1 text-[11px] text-muted"><Clock3 className="h-3 w-3" />{formatBeijingTime(run.started_at)}</time>
        <span className="text-[11px] text-muted">第 {run.attempt || 1} 次尝试</span>
        {run.manual_retry && <span className="rounded bg-accent-soft px-2 py-0.5 text-[10px] text-accent">手动重试</span>}
        {run.duration_ms > 0 && <span className="text-[11px] text-muted">{Math.max(1, Math.round(run.duration_ms / 1000))} 秒</span>}
        <button className="ml-auto inline-flex items-center gap-1 text-[11px] text-accent hover:underline" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded}>
          {expanded ? '收起详情' : '查看详情'} <ChevronRight className={cn('h-3.5 w-3.5 transition-transform', expanded && 'rotate-90')} />
        </button>
      </div>
      {expanded && <div className="mt-3 border-t border-line pt-3">
        {run.error && <p className="whitespace-pre-wrap break-words rounded-md bg-danger-soft px-3 py-2 text-[12px] text-danger">{run.error}</p>}
        {run.output && <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-md bg-code p-3 font-mono text-[11px] text-ink-2">{run.output}</pre>}
        {!run.error && !run.output && <p className="text-[12px] text-muted">暂无文本产出。</p>}
        {run.status === 'failed' && <div className="mt-3 flex justify-end"><Button size="sm" variant="outline" disabled={retrying} onClick={onRetry}>{retrying ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <RotateCcw className="h-4 w-4" />}手动重试</Button></div>}
      </div>}
    </article>
  )
}
