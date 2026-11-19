/**
 * 大屏总览面板 —— 多 Agent 一屏总览。
 *
 * 数据来自三个**服务端真相**接口（不是 SSE 攒的内存状态），
 * 所以刷新页面也不会空。
 *
 * 内核没就绪时 `/api/overview/teams` 会降级成空列表（不是 503），
 * 所以这个面板在内核启动期间也能渲染 —— 只是没内容。
 */

import { useEffect, useState } from 'react'

import { fetchOverviewRuns, fetchOverviewSuggestions, fetchOverviewTeams } from './api'
import type { OverviewRuns, OverviewSuggestions, OverviewTeams } from '@/lib/types'
import { Button } from '@/components/ui/button'

const RUN_STATUS_CN: Record<string, string> = {
  running: '进行中',
  done: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

function timeAgo(ts: number): string {
  const secs = Math.max(0, Math.round(Date.now() / 1000 - ts))
  if (secs < 60) return `${secs} 秒前`
  if (secs < 3600) return `${Math.round(secs / 60)} 分钟前`
  if (secs < 86400) return `${Math.round(secs / 3600)} 小时前`
  return `${Math.round(secs / 86400)} 天前`
}

export default function OverviewPanel({ onOpenSession }: { onOpenSession?: (id: string) => void }) {
  const [teams, setTeams] = useState<OverviewTeams | null>(null)
  const [runs, setRuns] = useState<OverviewRuns | null>(null)
  const [sugs, setSugs] = useState<OverviewSuggestions | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    Promise.all([fetchOverviewTeams(), fetchOverviewRuns(), fetchOverviewSuggestions()])
      .then(([t, r, s]) => {
        if (!alive) return
        setTeams(t)
        setRuns(r)
        setSugs(s)
      })
      .catch((e) => alive && setError(e instanceof Error ? e.message : '加载失败'))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [])

  if (error) {
    return (
      <div className="rounded-[var(--r-lg)] border border-danger/40 bg-danger-soft p-4 text-[13px] text-danger">
        {error}
      </div>
    )
  }

  if (loading) {
    return <div className="text-[13px] text-muted">正在拉取总览…</div>
  }

  const degraded = teams?.degraded ?? []
  const teamCount = teams?.team_count ?? 0
  const memberCount = teams?.member_count ?? 0

  return (
    <div className="space-y-5">
      {/* 统计条 */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="最近运行" value={runs?.runs.length ?? 0} hint={`共 ${runs?.total ?? 0} 轮`} />
        <Stat label="进行中" value={runs?.active ?? 0} hint="此刻在跑" />
        <Stat label="今天动过" value={runs?.today ?? 0} hint="今天改过的会话" />
        <Stat label="编队" value={teamCount} hint={`${memberCount} 个成员`} />
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
                        (m.known
                          ? 'bg-accent-soft text-accent'
                          : 'bg-panel-2 text-muted')
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
                  <span className="shrink-0 text-[11.5px] text-muted">{timeAgo(r.updated_at)}</span>
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
            {sugs.suggestions.map((s) => (
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
                {s.action && (
                  <Button size="sm" variant="outline">
                    执行
                  </Button>
                )}
              </div>
            ))}
          </div>
        )}
      </section>
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
