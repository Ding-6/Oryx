/**
 * 大屏总览面板 —— 多 Agent 一屏总览。
 *
 * 数据来自三个**服务端真相**接口（不是 SSE 攒的内存状态），刷新页面也不会空。
 * 模块 8 补上两件事：
 *   · 订阅 SSE，运行 / 步骤 / 团队 / 知识库解析有动静时节流重拉；
 *   · 建议的「执行」真的能点 —— preview 型开预览页，prompt 型带到
 *     「新建运行」页预填（原来只有 action 型显示按钮，还完全没绑事件）。
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { subscribe } from '@/lib/api'
import type { OverviewRuns, OverviewSuggestions, OverviewTeams, Suggestion } from '@/lib/types'
import { fetchOverviewRuns, fetchOverviewSuggestions, fetchOverviewTeams } from './api'
import {
  RUN_STATUS_CN,
  actionButtonLabel,
  hasExecutableAction,
  shouldRefresh,
  statCards,
  suggestionAction,
  throttleDelay,
  timeAgo,
} from './overviewOps'

export default function OverviewPanel({
  onOpenSession,
  onUsePrompt,
}: {
  onOpenSession?: (id: string) => void
  /** prompt 型建议：把这句话预填到「新建运行」页，由用户确认后再提交。 */
  onUsePrompt?: (prompt: string, taskId?: string) => void
}) {
  const [teams, setTeams] = useState<OverviewTeams | null>(null)
  const [runs, setRuns] = useState<OverviewRuns | null>(null)
  const [sugs, setSugs] = useState<OverviewSuggestions | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [updatedAt, setUpdatedAt] = useState<number | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [nowTick, setNowTick] = useState(Date.now())

  const refresh = useCallback(async () => {
    try {
      const [t, r, s] = await Promise.all([
        fetchOverviewTeams(), fetchOverviewRuns(), fetchOverviewSuggestions(),
      ])
      setTeams(t)
      setRuns(r)
      setSugs(s)
      setError(null)
      setUpdatedAt(Date.now())
    } catch (e) {
      // 刷新失败保留上一次数据，不让整屏变红；初次加载才由下面的空态兜底。
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  const lastRefreshRef = useRef(0)
  const timerRef = useRef<number | undefined>(undefined)

  useEffect(() => {
    lastRefreshRef.current = Date.now()
    void refresh()
    const stop = subscribe('/api/events', (ev) => {
      if (!shouldRefresh(ev.type)) return
      const wait = throttleDelay(Date.now() - lastRefreshRef.current)
      if (timerRef.current !== undefined) window.clearTimeout(timerRef.current)
      timerRef.current = window.setTimeout(() => {
        lastRefreshRef.current = Date.now()
        void refresh()
      }, wait)
    })
    return () => {
      if (timerRef.current !== undefined) window.clearTimeout(timerRef.current)
      stop()
    }
  }, [refresh])

  // 「上次更新 x 分钟前」需要自己走时钟，否则数字永远不动。
  useEffect(() => {
    const timer = window.setInterval(() => setNowTick(Date.now()), 30_000)
    return () => window.clearInterval(timer)
  }, [])

  const runSuggestion = (s: Suggestion) => {
    const action = suggestionAction(s)
    setActionError(null)
    if (action.kind === 'preview') {
      // 不带 noopener 特性才能拿到返回值判断是否被拦截；手动清 opener 等价安全。
      const win = window.open(action.url, '_blank')
      if (win) {
        try { win.opener = null } catch { /* 跨域时无所谓 */ }
      } else {
        setActionError('浏览器拦截了新窗口，请允许弹窗后重试')
      }
      return
    }
    if (action.kind === 'prompt') {
      if (onUsePrompt) onUsePrompt(action.prompt, sugs?.source?.task_id)
      else setActionError('当前界面没有可用的新建运行入口')
    }
  }

  const degraded = teams?.degraded ?? []
  const teamCount = teams?.team_count ?? 0
  const cards = statCards(runs, teams)

  return (
    <div className="space-y-5">
      <div className="ov-toolbar">
        <span className="ov-updated">
          {updatedAt ? `上次更新 ${timeAgo(updatedAt / 1000, nowTick)} · 运行有动静会自动刷新` : '正在拉取总览…'}
        </span>
        <Button size="sm" variant="ghost" disabled={loading} onClick={() => void refresh()}>
          {loading ? '刷新中…' : '刷新'}
        </Button>
      </div>

      {error && (
        <div className="rounded-[var(--r-lg)] border border-danger/40 bg-danger-soft p-3 text-[13px] text-danger">
          {error}
        </div>
      )}
      {actionError && (
        <div role="alert" className="rounded-[var(--r-lg)] border border-warn/40 bg-warn-soft p-3 text-[13px] text-warn">
          {actionError}
        </div>
      )}

      {loading && !runs && !error ? (
        <div className="text-[13px] text-muted">正在拉取总览…</div>
      ) : (
        <>
          {/* 统计条 */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {cards.map((card) => (
              <Stat key={card.label} label={card.label} value={card.value} hint={card.hint} />
            ))}
          </div>

          {/* 内核没就绪时的提示 —— 说清楚是「还没起来」，不是「坏了」 */}
          {degraded.length > 0 && (
            <div className="rounded-[var(--r-lg)] border border-warn/40 bg-warn-soft px-3 py-2 text-[12.5px] text-warn">
              内核还没就绪，编队信息暂时取不到（其余数据正常）。
            </div>
          )}

          {/* 编队 */}
          <section>
            <h2 className="mb-2 text-[13px] font-semibold text-ink-2">
              编队
              {teams && teams.agents.length > 0 && (
                <span className="ml-2 font-normal text-muted">
                  可用子 Agent {teams.agents.length} 个
                </span>
              )}
            </h2>
            {teamCount === 0 ? (
              <Empty text="还没有编队 —— 跑一次多 Agent 任务就会出现在这里。" />
            ) : (
              <div className="grid gap-2 sm:grid-cols-2">
                {teams!.teams.map((t) => (
                  <div key={t.id || t.name} className="rounded-[var(--r-lg)] border border-line bg-card p-3">
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="text-[13px] font-medium text-ink">{t.name}</span>
                      {t.unread > 0 && (
                        <span className="rounded-full bg-accent-soft px-1.5 text-[11px] text-accent">
                          {t.unread} 条未读
                        </span>
                      )}
                    </div>
                    {t.goal && <p className="mt-1 text-[12px] text-muted">{t.goal}</p>}
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {t.members.map((m) => (
                        <span
                          key={m.agent_id}
                          title={m.role}
                          className={
                            'rounded-full px-2 py-0.5 text-[11.5px] ' +
                            (m.known ? 'bg-accent-soft text-accent' : 'bg-panel-2 text-muted')
                          }
                        >
                          {m.name}
                          <span className="ml-1 opacity-70">{m.role}</span>
                        </span>
                      ))}
                      {t.members.length === 0 && <span className="text-[11.5px] text-muted">暂无成员</span>}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* 最近运行 */}
          <section>
            <h2 className="mb-2 text-[13px] font-semibold text-ink-2">最近运行</h2>
            {!runs || runs.runs.length === 0 ? (
              <Empty text="还没有运行记录。" />
            ) : (
              <ul className="divide-y divide-line overflow-hidden rounded-[var(--r-lg)] border border-line bg-card">
                {runs.runs.map((r) => (
                  <li key={r.id}>
                    <button
                      type="button"
                      onClick={() => onOpenSession?.(r.id)}
                      className="flex w-full items-center gap-3 px-3 py-2 text-left transition-colors hover:bg-panel"
                    >
                      <span
                        className={
                          'h-1.5 w-1.5 shrink-0 rounded-full ' +
                          (r.status === 'running'
                            ? 'bg-accent'
                            : r.status === 'failed'
                              ? 'bg-danger'
                              : 'bg-line-strong')
                        }
                      />
                      <span className="min-w-0 flex-1 truncate text-[13px] text-ink">{r.title}</span>
                      <span className="shrink-0 text-[11.5px] text-muted">
                        {RUN_STATUS_CN[r.status] ?? r.status}
                      </span>
                      <span className="shrink-0 text-[11.5px] text-muted">{timeAgo(r.updated_at, nowTick)}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* 主动建议 */}
          <section>
            <h2 className="mb-2 text-[13px] font-semibold text-ink-2">下一步</h2>
            {!sugs || sugs.suggestions.length === 0 ? (
              <Empty
                text={
                  sugs?.source?.task_id
                    ? '这一轮没有可给的建议。'
                    : '跑完一轮之后，这里会给出下一步建议。'
                }
              />
            ) : (
              <div className="space-y-2">
                {sugs.suggestions.map((s) => {
                  const action = suggestionAction(s)
                  return (
                    <div
                      key={s.label}
                      className="flex items-start gap-3 rounded-[var(--r-lg)] border border-line bg-card p-3"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="text-[13px] font-medium text-ink">{s.label}</div>
                        {s.prompt && (
                          <p className="mt-1 text-[12px] leading-relaxed text-muted">{s.prompt}</p>
                        )}
                        {s.path && (
                          <p className="mt-1 truncate font-mono text-[11.5px] text-muted">{s.path}</p>
                        )}
                      </div>
                      {hasExecutableAction(action) && (
                        <Button size="sm" variant="outline" onClick={() => runSuggestion(s)}>
                          {actionButtonLabel(action)}
                        </Button>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </section>
        </>
      )}
    </div>
  )
}

function Stat({ label, value, hint }: { label: string; value: number; hint?: string }) {
  return (
    <div className="rounded-[var(--r-lg)] border border-line bg-card px-3 py-2.5">
      <div className="text-[11.5px] text-muted">{label}</div>
      <div className="mt-0.5 text-[22px] font-semibold leading-tight text-ink">{value}</div>
      {hint && <div className="mt-0.5 text-[11px] text-muted">{hint}</div>}
    </div>
  )
}

function Empty({ text }: { text: string }) {
  return (
    <div className="rounded-[var(--r-lg)] border border-dashed border-line px-3 py-6 text-center text-[12.5px] text-muted">
      {text}
    </div>
  )
}
