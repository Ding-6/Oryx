/**
 * 会话列表 —— 左栏。
 *
 * 数据来自 v2 的 `GET /api/sessions`（data 是**数组**）。
 * 此前缺的三个入口都在这里补齐：新建、改名、删除；搜索改成服务端 `q`
 * （过滤全部会话，不是当前这一页），列表按 `offset` 翻页。
 * 时间分组的「秒 / 毫秒」坑在 `sessionOps.ts` 里处理。
 */

import { useEffect, useMemo, useState, type FormEvent } from 'react'

import { createSession, deleteSession, fetchSessions, renameSession } from './api'
import {
  DELETE_RUNNING_HINT,
  PAGE_SIZE,
  canDeleteSession,
  groupSessions,
  hasMorePage,
  sessionMeta,
  validateSessionTitle,
} from './sessionOps'
import type { Session } from '@/lib/types'

export default function SessionList({
  activeId,
  onSelect,
  refreshKey = 0,
  onCreated,
  onDeleted,
  onRenamed,
}: {
  activeId: string | null
  onSelect: (id: string) => void
  refreshKey?: number
  /** 新建后外层要打开这个会话。 */
  onCreated?: (id: string) => void
  /** 删掉的若正是当前打开的会话，外层要退回总览。 */
  onDeleted?: (id: string) => void
  /** 改名后外层要让详情标题跟着变。 */
  onRenamed?: (id: string) => void
}) {
  const [sessions, setSessions] = useState<Session[]>([])
  const [q, setQ] = useState('')
  const [query, setQuery] = useState('')
  const [offset, setOffset] = useState(0)
  const [more, setMore] = useState(false)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [reloadToken, setReloadToken] = useState(0)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editTitle, setEditTitle] = useState('')
  const [confirmId, setConfirmId] = useState<string | null>(null)

  // 输入 250ms 后才发请求，避免每敲一个字打一次后端。
  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(q.trim()), 250)
    return () => window.clearTimeout(timer)
  }, [q])

  // 切搜索词 / 外层刷新 / 自己操作完 → 回到第一页重拿。
  useEffect(() => {
    let alive = true
    setLoading(true)
    setError(null)
    fetchSessions({ limit: PAGE_SIZE, offset: 0, q: query })
      .then((list) => {
        if (!alive) return
        setSessions(list)
        setOffset(list.length)
        setMore(hasMorePage(list.length))
      })
      .catch((e) => alive && setError(e instanceof Error ? e.message : '加载失败'))
      .finally(() => alive && setLoading(false))
    return () => { alive = false }
  }, [refreshKey, query, reloadToken])

  const reload = () => setReloadToken((v) => v + 1)

  const loadMore = async () => {
    if (loadingMore) return
    setLoadingMore(true)
    setActionError(null)
    try {
      const list = await fetchSessions({ limit: PAGE_SIZE, offset, q: query })
      // 会话在翻页期间可能被动过，去重后再拼，避免同一个 id 出现两次。
      setSessions((prev) => {
        const seen = new Set(prev.map((s) => s.id))
        return [...prev, ...list.filter((s) => !seen.has(s.id))]
      })
      setOffset((v) => v + list.length)
      setMore(hasMorePage(list.length))
    } catch (e) {
      setActionError(e instanceof Error ? e.message : '加载更多失败')
    } finally {
      setLoadingMore(false)
    }
  }

  const handleCreate = async () => {
    if (busy) return
    setBusy(true)
    setActionError(null)
    try {
      const result = await createSession()
      setEditingId(null)
      setConfirmId(null)
      onCreated?.(result.task_id)
      reload()
    } catch (e) {
      setActionError(e instanceof Error ? e.message : '新建会话失败')
    } finally {
      setBusy(false)
    }
  }

  const startEdit = (s: Session) => {
    setConfirmId(null)
    setEditingId(s.id)
    // 没改过名的会话，输入框给空串 —— 它的 title 是 goal 摘要，
    // 拿摘要当「当前名字」会让用户以为改名就是把摘要再存一遍。
    setEditTitle(s.renamed ? s.title : '')
    setActionError(null)
  }

  const submitEdit = async (event: FormEvent<HTMLFormElement>, id: string) => {
    event.preventDefault()
    const check = validateSessionTitle(editTitle)
    if (!check.ok) {
      setActionError(check.message)
      return
    }
    if (busy) return
    setBusy(true)
    setActionError(null)
    try {
      await renameSession(id, editTitle.trim())
      setEditingId(null)
      onRenamed?.(id)
      // 清空名字后要退回 goal 摘要，交给后端算才准，不本地猜。
      reload()
    } catch (e) {
      setActionError(e instanceof Error ? e.message : '改名失败')
    } finally {
      setBusy(false)
    }
  }

  const doDelete = async (id: string) => {
    if (busy) return
    setBusy(true)
    setActionError(null)
    try {
      await deleteSession(id)
      setConfirmId(null)
      onDeleted?.(id)
      reload()
    } catch (e) {
      setActionError(e instanceof Error ? e.message : '删除失败')
    } finally {
      setBusy(false)
    }
  }

  const groups = useMemo(() => groupSessions(sessions), [sessions])

  return (
    <div className="flex h-full flex-col">
      <div className="sl-head">
        <button
          type="button"
          className="sl-new"
          onClick={() => void handleCreate()}
          disabled={busy}
        >
          {busy ? '正在创建…' : '+ 新会话'}
        </button>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="搜索全部会话…"
          className="sl-search"
        />
      </div>

      {actionError && <p role="alert" className="sl-error">{actionError}</p>}

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
        {error ? (
          <p className="px-2 py-2 text-[12px] text-danger">{error}</p>
        ) : loading ? (
          <p className="px-2 py-2 text-[12px] text-muted">加载中…</p>
        ) : sessions.length === 0 ? (
          <p className="px-2 py-6 text-center text-[12px] text-muted">
            {query ? '没有匹配的会话' : '还没有会话，点上面「+ 新会话」开一个'}
          </p>
        ) : (
          groups.map(([label, items]) => (
            <div key={label} className="mb-2">
              <div className="px-2 py-1 text-[11px] font-medium text-muted">{label}</div>
              <ul>
                {items.map((s) => (
                  <li key={s.id} className={'sl-row' + (s.id === activeId ? ' sl-row--active' : '')}>
                    {editingId === s.id ? (
                      <form className="sl-edit" onSubmit={(e) => void submitEdit(e, s.id)}>
                        <input
                          autoFocus
                          value={editTitle}
                          maxLength={61}
                          onChange={(e) => setEditTitle(e.target.value)}
                          onKeyDown={(e) => { if (e.key === 'Escape') setEditingId(null) }}
                          placeholder="留空恢复默认标题"
                          className="sl-edit-input"
                        />
                        <button type="submit" className="sl-mini" disabled={busy}>保存</button>
                        <button type="button" className="sl-mini" onClick={() => setEditingId(null)}>
                          取消
                        </button>
                      </form>
                    ) : (
                      <div className="sl-line">
                        <button type="button" className="sl-main" onClick={() => onSelect(s.id)}>
                          <span
                            className={
                              'sl-dot ' +
                              (s.status === 'running' ? 'bg-accent'
                                : s.status === 'failed' ? 'bg-danger' : 'bg-line-strong')
                            }
                          />
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-[12.5px] text-ink">{s.title}</span>
                            <span className="block truncate text-[11px] text-muted">{sessionMeta(s)}</span>
                          </span>
                        </button>
                        <div className="sl-actions">
                          <button type="button" className="sl-act" onClick={() => startEdit(s)}>
                            改名
                          </button>
                          <button
                            type="button"
                            className="sl-act sl-act--danger"
                            title={canDeleteSession(s.status) ? '删除会话' : DELETE_RUNNING_HINT}
                            disabled={!canDeleteSession(s.status)}
                            onClick={() => {
                              setEditingId(null)
                              setConfirmId(s.id)
                              setActionError(null)
                            }}
                          >
                            删除
                          </button>
                        </div>
                      </div>
                    )}
                    {confirmId === s.id && (
                      <div className="sl-confirm">
                        <span>删除后消息、步骤、待办和检查点会一起清掉，无法恢复。</span>
                        <div className="sl-confirm-actions">
                          <button
                            type="button"
                            className="sl-mini sl-mini--danger"
                            disabled={busy}
                            onClick={() => void doDelete(s.id)}
                          >
                            确认删除
                          </button>
                          <button type="button" className="sl-mini" onClick={() => setConfirmId(null)}>
                            取消
                          </button>
                        </div>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))
        )}
        {more && !loading && !error && (
          <button
            type="button"
            className="sl-more"
            onClick={() => void loadMore()}
            disabled={loadingMore}
          >
            {loadingMore ? '加载中…' : '加载更早的会话'}
          </button>
        )}
      </div>
    </div>
  )
}
