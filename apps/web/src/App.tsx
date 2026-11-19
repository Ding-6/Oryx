import { useEffect, useState } from 'react'

import { api, ApiError } from '@/lib/api'
import { Button } from '@/components/ui/button'

/**
 * 应用外壳（骨架）。
 *
 * 这一版先把**壳**立起来：顶栏 + 左栏 + 主区 + 右栏，
 * 与现有前端（core/web）的信息架构对齐 ——
 * 迁移时是「逐页替换内容」，不是「整页推倒」，用户不会失去位置感。
 *
 * 状态用 @tanstack/react-query 管理：
 *   · 自动缓存与去重（同一个接口多个组件要用时只打一次）
 *   · 自动重试与失效
 *   · 错误态统一 —— 不用每个组件各写各的 loading/error
 */
interface AppState {
  mode?: string
  interaction?: string
  style?: string
  expert?: string
  provider?: string
  model?: string
  busy?: boolean
  experts?: Array<{ id: string; name: string }>
}

export default function App() {
  const [state, setState] = useState<AppState | null>(null)
  const [error, setError] = useState<string | null>(null)

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
            </>
          ) : (
            <span>连接内核…</span>
          )}
        </div>
        <div className="ml-auto flex items-center gap-2">
          <Button size="sm" variant="outline">
            设置
          </Button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <aside className="flex w-56 shrink-0 flex-col border-r border-line bg-card">
          <div className="flex-1 overflow-y-auto p-3">
            <p className="text-[12px] text-muted">会话列表</p>
          </div>
        </aside>

        <main className="flex min-w-0 flex-1 flex-col">
          <div className="flex-1 overflow-y-auto p-6">
            {error ? (
              <div className="rounded-[var(--r-lg)] border border-danger/40 bg-danger-soft p-4 text-[13px] text-danger">
                {error}
              </div>
            ) : (
              <div className="text-muted">主区</div>
            )}
          </div>
        </main>

        <aside className="w-72 shrink-0 border-l border-line bg-card">
          <div className="p-3">
            <p className="text-[12px] text-muted">右栏</p>
          </div>
        </aside>
      </div>
    </div>
  )
}
