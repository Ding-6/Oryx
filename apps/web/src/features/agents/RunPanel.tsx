import { useEffect, useState, type FormEvent } from 'react'

import { Button } from '@/components/ui/button'
import { ApiError, api } from '@/lib/api'
import type { AppState } from '@/lib/types'
import { listAgents, listExperts, type AgentDefinition, type ExpertDefinition } from './api'
import {
  FOLLOW_GLOBAL,
  catalogReady,
  isElevated,
  runOverrides,
  summary,
  validateOverrides,
  type PreferenceCatalog,
} from '@/features/settings/preferences'

export default function RunPanel({
  onStarted,
  state,
  catalog,
  onOpenSettings,
  initialGoal,
  presetKey,
}: {
  onStarted: (taskId: string) => void
  /** 全局偏好，用来显示「跟随全局设置」当前到底是哪一档。 */
  state: AppState | null
  catalog: PreferenceCatalog | null
  onOpenSettings?: () => void
  /** 从总览建议带过来的预填目标；只预填，不自动提交，由用户确认。 */
  initialGoal?: string | null
  /** 同一句话再点一次也能重新预填（App 用时间戳）。 */
  presetKey?: number
}) {
  const [agents, setAgents] = useState<AgentDefinition[]>([])
  const [experts, setExperts] = useState<ExpertDefinition[]>([])
  const [goal, setGoal] = useState(initialGoal ?? '')
  const [agentId, setAgentId] = useState('')
  const [expertId, setExpertId] = useState('')
  // 空串 = 跟随全局默认；选了具体值才随 /api/chat 传过去。
  const [interaction, setInteraction] = useState(FOLLOW_GLOBAL)
  const [style, setStyle] = useState(FOLLOW_GLOBAL)
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    Promise.all([listAgents(), listExperts()])
      .then(([agentItems, expertItems]) => {
        if (!alive) return
        setAgents(agentItems)
        setExperts(expertItems)
      })
      .catch((cause) => alive && setError(cause instanceof Error ? cause.message : '加载可选定义失败'))
      .finally(() => alive && setLoading(false))
    return () => { alive = false }
  }, [])

  // 总览建议带过来的预填值：只在「新的一次建议」到达时覆盖用户当前输入，
  // 不能每次渲染都 setValue，否则用户刚改两个字就被打回原文。
  useEffect(() => {
    if (presetKey !== undefined && initialGoal != null) setGoal(initialGoal)
  }, [presetKey, initialGoal])

  const labels = summary(state ?? {}, catalog)
  const chosenInteraction = catalog?.interactions.find((x) => x.name === interaction)
  const chosenStyle = catalog?.styles.find((x) => x.name === style)
  const ready = catalogReady(catalog)

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!goal.trim()) {
      setError('先写下这次希望 Agent 完成的事情。')
      return
    }
    const invalid = validateOverrides(interaction, style, catalog)
    if (invalid) {
      setError(invalid)
      return
    }
    setRunning(true)
    setError(null)
    try {
      const result = await api.post<{ task_id?: string }>('/api/chat', {
        goal: goal.trim(),
        agent_id: agentId,
        expert: expertId,
        ...runOverrides(interaction, style),
      })
      if (!result.task_id) throw new Error('运行已提交，但没有收到会话编号；请到左侧会话列表查看。')
      onStarted(result.task_id)
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : cause instanceof Error ? cause.message : '启动失败，请稍后重试')
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="mx-auto w-full max-w-3xl">
      <header className="mb-5">
        <p className="text-[12px] font-medium text-accent">新建运行</p>
        <h1 className="mt-1 text-xl font-semibold text-ink">这次要完成什么？</h1>
        <p className="mt-1 text-[13px] text-muted">可同时选择一个子 Agent 和一个专家人设。启动后，本会话会固定使用这份配置快照。</p>
      </header>

      {error && <div role="alert" className="mb-4 rounded-[var(--r-lg)] border border-danger/40 bg-danger-soft p-3 text-[13px] text-danger">{error}</div>}

      <form onSubmit={submit} className="space-y-4 rounded-[var(--r-lg)] border border-line bg-card p-4 shadow-card sm:p-5">
        <label className="block space-y-1.5">
          <span className="text-[12px] font-medium text-ink-2">任务描述 <span className="text-danger">*</span></span>
          <textarea autoFocus rows={6} maxLength={20000} value={goal} onChange={(event) => setGoal(event.target.value)}
            placeholder="例如：整理这份需求文档，输出待办、接口字段和验收标准。"
            className="w-full resize-y rounded-md border border-line-strong bg-bg px-3 py-2.5 text-[14px] leading-relaxed text-ink outline-none placeholder:text-muted/70 focus:border-accent focus:ring-2 focus:ring-accent/20" />
          <span className="block text-right text-[11px] text-muted">{goal.length} / 20000</span>
        </label>

        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block space-y-1.5">
            <span className="text-[12px] font-medium text-ink-2">子 Agent 定义</span>
            <select value={agentId} onChange={(event) => setAgentId(event.target.value)} disabled={loading || running}
              className="h-10 w-full rounded-md border border-line-strong bg-bg px-2.5 text-[13px] text-ink outline-none focus:border-accent focus:ring-2 focus:ring-accent/20 disabled:bg-panel">
              <option value="">不选择</option>
              {agents.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.description || item.id}</option>)}
            </select>
          </label>
          <label className="block space-y-1.5">
            <span className="text-[12px] font-medium text-ink-2">专家人设</span>
            <select value={expertId} onChange={(event) => setExpertId(event.target.value)} disabled={loading || running}
              className="h-10 w-full rounded-md border border-line-strong bg-bg px-2.5 text-[13px] text-ink outline-none focus:border-accent focus:ring-2 focus:ring-accent/20 disabled:bg-panel">
              <option value="">不选择</option>
              {experts.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.description || item.id}</option>)}
            </select>
          </label>
        </div>

        {loading ? <p className="text-[12px] text-muted">正在读取共享定义…</p> : agents.length === 0 && experts.length === 0 ? (
          <p className="rounded-md bg-panel px-3 py-2 text-[12px] text-muted">暂时没有可选的 Agent 或专家人设；你仍可以直接启动普通对话。</p>
        ) : null}

        {/* 本次运行的对话参数：交互模式与回答风格可以按运行覆盖，计算模式只跟随全局。 */}
        <section className="space-y-2 border-t border-line pt-3">
          <div className="run-prefs">
            <span>当前全局</span>
            <span className="run-prefs-chip">{labels.mode}</span>
            <span className="run-prefs-chip">{labels.interaction}</span>
            <span className="run-prefs-chip">{labels.style}</span>
            {onOpenSettings && (
              <button type="button" className="run-prefs-link ml-auto" onClick={onOpenSettings}>
                去设置
              </button>
            )}
          </div>

          {!ready && (
            <p className="text-[11.5px] text-muted">
              {catalog === null ? '偏好目录还没加载完，暂时只能沿用全局设置；' : '后端没有返回可选偏好，暂时只能沿用全局设置；'}
              启动仍可正常进行。
            </p>
          )}

          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block space-y-1.5">
              <span className="text-[12px] font-medium text-ink-2">交互模式（本次运行）</span>
              <select value={interaction} onChange={(event) => setInteraction(event.target.value)} disabled={running || !ready}
                className="h-10 w-full rounded-md border border-line-strong bg-bg px-2.5 text-[13px] text-ink outline-none focus:border-accent focus:ring-2 focus:ring-accent/20 disabled:bg-panel">
                <option value={FOLLOW_GLOBAL}>跟随全局设置（{labels.interaction}）</option>
                {catalog?.interactions.map((x) => (
                  <option key={x.name} value={x.name}>{x.label}</option>
                ))}
              </select>
            </label>
            <label className="block space-y-1.5">
              <span className="text-[12px] font-medium text-ink-2">回答风格（本次运行）</span>
              <select value={style} onChange={(event) => setStyle(event.target.value)} disabled={running || !ready}
                className="h-10 w-full rounded-md border border-line-strong bg-bg px-2.5 text-[13px] text-ink outline-none focus:border-accent focus:ring-2 focus:ring-accent/20 disabled:bg-panel">
                <option value={FOLLOW_GLOBAL}>跟随全局设置（{labels.style}）</option>
                {catalog?.styles.map((x) => (
                  <option key={x.name} value={x.name}>{x.label}</option>
                ))}
              </select>
            </label>
          </div>

          {chosenInteraction && (
            <p className={'run-override-hint' + (isElevated(chosenInteraction) ? ' run-override-hint--warn' : '')}>
              本次使用「{chosenInteraction.label}」：{chosenInteraction.hint}
              {isElevated(chosenInteraction) ? ' 请确认这正是你要的。' : ''}
            </p>
          )}
          {chosenStyle && (
            <p className="run-override-hint">本次使用「{chosenStyle.label}」：{chosenStyle.hint}</p>
          )}
          {!chosenInteraction && !chosenStyle && (
            <p className="run-override-hint">不选择就沿用全局设置；所选值会固化到这个会话，之后改全局不会影响它。</p>
          )}
          <p className="run-override-hint">
            计算模式（{labels.mode}）是全局模型路由，单次运行不能覆盖 —— 要换模型服务请到顶栏「设置」。
          </p>
        </section>

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line pt-3">
          <p className="max-w-lg text-[11.5px] leading-relaxed text-muted">所选定义会以当前内容保存到会话中。之后编辑共享定义，不会改变这次运行的配置。</p>
          <Button type="submit" size="lg" disabled={running || loading || !goal.trim()}>
            {running ? '正在启动…' : '启动 Agent'}
          </Button>
        </div>
      </form>
    </div>
  )
}
