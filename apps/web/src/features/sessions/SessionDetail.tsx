/**
 * 会话详情 —— 主区。
 *
 * 用的是 v2 的 `GET /api/sessions/{id}`：消息、步骤、待办、用量一次拿全。
 * 三个子资源接口（steps / todos / usage）也都在，但详情里已经带了，
 * 所以这里不重复拉 —— 那是给「只要其中一样」的场景用的。
 */

import { useEffect, useState } from 'react'

import { fetchSession, setTodoStatus } from './api'
import type { SessionDetail } from '@/lib/types'
import { Button } from '@/components/ui/button'

const TODO_CN: Record<string, string> = {
  pending: '待办',
  in_progress: '进行中',
  done: '已完成',
}

export default function SessionDetail({ taskId }: { taskId: string | null }) {
  const [detail, setDetail] = useState<SessionDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!taskId) {
      setDetail(null)
      return
    }
    let alive = true
    setLoading(true)
    fetchSession(taskId)
      .then((d) => alive && setDetail(d))
      .catch((e) => alive && setError(e instanceof Error ? e.message : '加载失败'))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [taskId])

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
    } catch {
      // 存不住就改回去 —— 界面不能显示一个其实没存上的状态
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
    }
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
      </header>

      {/* 消息 */}
      <section className="space-y-3">
        {detail.messages.length === 0 && (
          <p className="text-[13px] text-muted">这个会话还没有消息。</p>
        )}
        {detail.messages.map((m) => (
          <article
            key={m.id}
            className={
              'rounded-[var(--r-lg)] px-3.5 py-2.5 text-[13px] leading-relaxed ' +
              (m.role === 'user'
                ? 'ml-auto max-w-[85%] bg-user-bg text-user-fg'
                : 'mr-auto max-w-[92%] border border-line bg-card text-ink')
            }
          >
            {m.content}
          </article>
        ))}
      </section>

      {/* 待办 */}
      {detail.todos.length > 0 && (
        <section>
          <h2 className="mb-2 text-[13px] font-semibold text-ink-2">
            待办
            {openTodos.length > 0 && (
              <span className="ml-2 font-normal text-muted">还剩 {openTodos.length} 项</span>
            )}
          </h2>
          <ul className="divide-y divide-line overflow-hidden rounded-[var(--r-lg)] border border-line bg-card">
            {[...openTodos, ...doneTodos].map((t) => (
              <li key={t.id} className="flex items-center gap-2.5 px-3 py-2">
                <input
                  type="checkbox"
                  checked={t.status === 'done'}
                  onChange={() => toggleTodo(t.id, t.status)}
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
        </section>
      )}

      {/* 步骤 */}
      {detail.steps.length > 0 && (
        <section>
          <h2 className="mb-2 text-[13px] font-semibold text-ink-2">执行步骤</h2>
          <ul className="divide-y divide-line overflow-hidden rounded-[var(--r-lg)] border border-line bg-card">
            {detail.steps.map((s) => (
              <li key={s.id} className="flex items-center gap-2.5 px-3 py-1.5">
                <span
                  className={
                    'h-1.5 w-1.5 shrink-0 rounded-full ' +
                    (s.state === 'failed'
                      ? 'bg-danger'
                      : s.state === 'running'
                        ? 'bg-accent'
                        : 'bg-line-strong')
                  }
                />
                <span className="font-mono text-[12px] text-ink">{s.tool ?? '—'}</span>
                {s.agent && <span className="text-[11.5px] text-muted">{s.agent}</span>}
                <span className="ml-auto shrink-0 text-[11.5px] text-muted">
                  {s.state ?? '—'}
                </span>
              </li>
            ))}
          </ul>
        </section>
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
        <Button variant="outline" size="sm">
          回到总览
        </Button>
      </div>
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
