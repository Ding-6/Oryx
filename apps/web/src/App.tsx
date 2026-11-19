import { useEffect, useState } from 'react'

import { api, ApiError } from '@/lib/api'
import type { AppState } from '@/lib/types'
import { Button } from '@/components/ui/button'
import OverviewPanel from '@/features/overview/OverviewPanel'
import SessionDetail from '@/features/sessions/SessionDetail'
import SessionList from '@/features/sessions/SessionList'

/**
 * 应用外壳。
 *
 * 这一版把**样板域的第一条完整链路**接进来了：
 *
 *     后端 v2 契约（session 域 + 大屏三接口）
 *       → 统一响应 {code, data, message}
 *       → 前端客户端自动剥壳（api.get 直接给领域对象）
 *       → 类型化调用 + 页面渲染
 *
 * 左栏是会话列表（v2 的 `GET /api/sessions`，data 是数组），
 * 主区在「总览」与「会话详情」之间切换。
 * 顶栏仍然是老前端那套信息（模式 / 交互 / 模型服务）——
 * 迁移是**逐块替换**，不是整页推倒，用户不会失去位置感。
 */
type View = 'overview' | 'session'

export default function App() {
  const [state, setState] = useState<AppState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [view, setView] = useState<View>('overview')
  const [sessionId, setSessionId] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    api
      .get<AppState>('/api/state')
      .then((s) => alive && setState(s))
      .catch((e) => alive && setError(e instanceof ApiError ? e.message : '加载失败'))
    return () => {
      alive = false
    }
  }, [])

  const openSession = (id: string) => {
    setSessionId(id)
    setView('session')
  }

  return (
    <div className="flex h-full flex-col bg-bg text-ink">
      <header className="flex h-12 shrink-0 items-center gap-3 border-b border-line bg-card px-4">
        <div className="flex items-center gap-2">
          <span className="grid h-6 w-6 place-items-center rounded-md bg-gradient-to-br from-brand-1 to-brand-2 text-[11px] font-semibold text-white">
            O
          </span>
          <span className="text-[15px] font-semibold tracking-tight">Oryx</span>
        </div>
        <div className="ml-2 flex items-center gap-1 text-[12.5px] text-muted">
          {state ? (
            <>
              <span>{state.mode}</span>
              <span className="text-line-strong">·</span>
              <span>{state.interaction}</span>
              <span className="text-line-strong">·</span>
              <span>{state.provider}</span>
              {state.busy && <span className="ml-1 text-accent">运行中</span>}
              {state.kernel_ready === false && (
                <span className="ml-1 text-warn" title="Agent 内核还没就绪">
                  内核启动中
                </span>
              )}
            </>
          ) : (
            <span>连接内核…</span>
          )}
        </div>

        <nav className="ml-6 flex items-center gap-1">
          <Button
            size="sm"
            variant={view === 'overview' ? 'default' : 'outline'}
            onClick={() => setView('overview')}
          >
            总览
          </Button>
        </nav>

        <div className="ml-auto flex items-center gap-2">
          <Button size="sm" variant="outline">
            设置
          </Button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <aside className="flex w-64 shrink-0 flex-col border-r border-line bg-card">
          <SessionList activeId={sessionId} onSelect={openSession} />
        </aside>

        <main className="flex min-w-0 flex-1 flex-col">
          <div className="min-h-0 flex-1 overflow-y-auto p-6">
            {error ? (
              <div className="rounded-[var(--r-lg)] border border-danger/40 bg-danger-soft p-4 text-[13px] text-danger">
                {error}
              </div>
            ) : view === 'overview' ? (
              <OverviewPanel onOpenSession={openSession} />
            ) : (
              <SessionDetail taskId={sessionId} />
            )}
          </div>
        </main>
      </div>
    </div>
  )
}
