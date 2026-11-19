/**
 * 会话列表 —— 左栏。
 *
 * 数据来自 v2 的 `GET /api/sessions`（data 是**数组**）。
 * 前端不需要写 `.sessions` —— 后端不再多包那一层。
 */

import { useEffect, useMemo, useState } from 'react'

import { fetchSessions } from './api'
import type { Session } from '@/lib/types'

const STATUS_CN: Record<string, string> = {
  running: '进行中',
  done: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

function groupLabel(ts: number): string {
  const now = new Date()
  const day = new Date(ts)
  const sameDay = (a: Date, b: Date) =>
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  if (sameDay(day, now)) return '今天'
  const yesterday = new Date(now.getTime() - 86400_000)
  if (sameDay(day, yesterday)) return '昨天'
  if (now.getTime() - ts < 7 * 86400_000) return '本周'
  if (now.getTime() - ts < 30 * 86400_000) return '本月'
  return '更早'
}

export default function SessionList({
  activeId,
  onSelect,
}: {
  activeId: string | null
  onSelect: (id: string) => void
}) {
  const [sessions, setSessions] = useState<Session[]>([])
  const [q, setQ] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    fetchSessions(100)
      .then((s) => alive && setSessions(s))
      .catch((e) => alive && setError(e instanceof Error ? e.message : '加载失败'))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [])

  // 过滤 + 按时间分组
  const groups = useMemo(() => {
    const needle = q.trim().toLowerCase()
    const matched = needle
      ? sessions.filter(
          (s) =>
            s.title.toLowerCase().includes(needle) ||
            s.goal.toLowerCase().includes(needle),
        )
      : sessions
    const map = new Map<string, Session[]>()
    for (const s of matched) {
      const key = groupLabel(s.updated_at)
      if (!map.has(key)) map.set(key, [])
      map.get(key)!.push(s)
    }
    return [...map.entries()]
  }, [sessions, q])

  if (error) {
    return <div className="px-3 py-2 text-[12px] text-danger">{error}</div>
  }

  return (
    <div className="flex h-full flex-col">
      <div className="p-3">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="搜索会话…"
          className="w-full rounded-[var(--r)] border border-line bg-bg-2 px-2.5 py-1.5 text-[12.5px] text-ink outline-none placeholder:text-muted focus:border-accent-line"
        />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
        {loading ? (
          <p className="px-2 py-2 text-[12px] text-muted">加载中…</p>
        ) : sessions.length === 0 ? (
          <p className="px-2 py-6 text-center text-[12px] text-muted">还没有会话</p>
        ) : groups.length === 0 ? (
          <p className="px-2 py-6 text-center text-[12px] text-muted">没有匹配的会话</p>
        ) : (
          groups.map(([label, items]) => (
            <div key={label} className="mb-2">
              <div className="px-2 py-1 text-[11px] font-medium text-muted">{label}</div>
              <ul>
                {items.map((s) => (
                  <li key={s.id}>
                    <button
                      type="button"
                      onClick={() => onSelect(s.id)}
                      className={
                        'flex w-full items-center gap-2 rounded-[var(--r)] px-2 py-1.5 text-left transition-colors ' +
                        (s.id === activeId ? 'bg-accent-soft' : 'hover:bg-panel')
                      }
                    >
                      <span
                        className={
                          'h-1.5 w-1.5 shrink-0 rounded-full ' +
                          (s.status === 'running'
                            ? 'bg-accent'
                            : s.status === 'failed'
                              ? 'bg-danger'
                              : 'bg-line-strong')
                        }
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[12.5px] text-ink">{s.title}</span>
                        <span className="block truncate text-[11px] text-muted">
                          {STATUS_CN[s.status] ?? s.status}
                          {s.msg_count > 0 && ` · ${s.msg_count} 条消息`}
                          {s.todo_open > 0 && ` · ${s.todo_open} 项待办`}
                        </span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
