import { useCallback, useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { api, ApiError, setUnauthorizedHandler } from '@/lib/api'
import { errorNotice, shouldHandleUnauthorized } from '@/lib/apiContracts'
import type { AppState } from '@/lib/types'
import { fetchPreferenceCatalog } from '@/features/settings/api'
import PreferencesDialog from '@/features/settings/PreferencesDialog'
import { summary, type PreferenceCatalog } from '@/features/settings/preferences'
import { applyAppearanceState, loadAndApplyAppearance } from '@/features/settings/applyAppearance'
import type { AppearanceState } from '@/features/settings/settingsOps'
import { Button } from '@/components/ui/button'
import OverviewPanel from '@/features/overview/OverviewPanel'
import SessionDetail from '@/features/sessions/SessionDetail'
import SessionList from '@/features/sessions/SessionList'
import AuthScreen, { type AccountUser } from '@/features/account/AuthScreen'
import AgentManagement from '@/features/agents/AgentManagement'
import RunPanel from '@/features/agents/RunPanel'
import KnowledgeBase from '@/features/knowledge/KnowledgeBase'
import OperationsPanel from '@/features/operations/OperationsPanel'
import SystemPanel from '@/features/system/SystemPanel'

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
type View = 'overview' | 'session' | 'agents' | 'run' | 'knowledge' | 'operations' | 'system'

export default function App() {
  const queryClient = useQueryClient()
  const [user, setUser] = useState<AccountUser | null>(null)
  const [authMode, setAuthMode] = useState<'login' | 'register'>('login')
  const [authChecked, setAuthChecked] = useState(false)
  const [authError, setAuthError] = useState<string | null>(null)
  const [state, setState] = useState<AppState | null>(null)
  const [catalog, setCatalog] = useState<PreferenceCatalog | null>(null)
  const [catalogLoading, setCatalogLoading] = useState(false)
  const [catalogError, setCatalogError] = useState<string | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)
  /** 错误的状态码 —— 决定横幅要不要给「重试」（模块 10）。 */
  const [errorStatus, setErrorStatus] = useState<number>(0)
  const [view, setView] = useState<View>('overview')
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [sessionRefreshKey, setSessionRefreshKey] = useState(0)
  /** 左栏改名后让会话详情标题跟着变（详情自己持有一份 state）。 */
  const [detailRefreshKey, setDetailRefreshKey] = useState(0)
  /** 总览的 prompt 型建议 → 预填到「新建运行」。key 保证同一句话再点也能触发。 */
  const [runPreset, setRunPreset] = useState<{ goal: string; key: number } | null>(null)
  /** 外观（主题 / 背景）：挂载时应用一次，设置弹层里改动后同步。 */
  const [appearance, setAppearance] = useState<AppearanceState | null>(null)

  /** 401 回调要读「当前是否登录」，但不能把 user 放进依赖里反复重挂。 */
  const userRef = useRef<AccountUser | null>(null)
  useEffect(() => { userRef.current = user }, [user])

  const showError = useCallback((cause: unknown, fallback = '加载失败') => {
    const e = cause instanceof ApiError ? cause : null
    setError(e ? e.message : fallback)
    setErrorStatus(e?.status ?? 0)
  }, [])

  useEffect(() => {
    let alive = true
    void loadAndApplyAppearance().then((next) => { if (alive && next) setAppearance(next) })
    return () => { alive = false }
  }, [])

  /** 外观面板存盘成功后立刻应用到 DOM，不等下一次接口往返。 */
  const applyAppearanceNow = useCallback((next: AppearanceState) => {
    applyAppearanceState(next, Date.now())
    setAppearance(next)
  }, [])

  /**
   * 全局 401：会话过期时统一回登录页。
   *
   * 以前各面板各自报错，用户看到的是一堆「加载失败」却不知道要重新登录。
   * `/api/auth/me` 未登录时的 401 是正常探测（shouldHandleUnauthorized 会挡掉），
   * 只有「已经登录 + 非 auth 域」的 401 才说明 cookie 失效了。
   */
  useEffect(() => {
    setUnauthorizedHandler((status, path) => {
      if (!shouldHandleUnauthorized(status, path, userRef.current !== null)) return
      queryClient.clear()
      setUser(null)
      setState(null)
      setSessionId(null)
      setView('overview')
      setError('登录状态已失效，请重新登录')
      setErrorStatus(status)
    })
    return () => setUnauthorizedHandler(null)
  }, [queryClient])

  useEffect(() => {
    let alive = true
    api.get<{ account_count: number }>('/api/auth/status')
      .then(async ({ account_count }) => {
        if (!alive) return
        setAuthMode(account_count === 0 ? 'register' : 'login')
        try {
          const current = await api.get<AccountUser>('/api/auth/me')
          if (alive) setUser(current)
        } catch (cause) {
          if (alive && (!(cause instanceof ApiError) || cause.status !== 401)) {
            setAuthError(cause instanceof ApiError ? cause.message : '无法检查登录状态')
          }
        } finally {
          if (alive) setAuthChecked(true)
        }
      })
      .catch((cause) => {
        if (alive) {
          setAuthError(cause instanceof ApiError ? cause.message : '无法连接账号服务')
          setAuthChecked(true)
        }
      })
    return () => {
      alive = false
    }
  }, [])

  /** 切完偏好后重新拉首屏状态 —— 顶栏与新建运行页都跟着变。 */
  const refreshState = useCallback(async () => {
    const s = await api.get<AppState>('/api/state')
    setState(s)
  }, [])

  /** 偏好目录单独拉，失败只影响设置弹层，不顶掉整个主区。 */
  const reloadCatalog = useCallback(() => {
    setCatalogLoading(true)
    setCatalogError(null)
    fetchPreferenceCatalog()
      .then(setCatalog)
      .catch((e) => setCatalogError(e instanceof ApiError ? e.message : '加载失败'))
      .finally(() => setCatalogLoading(false))
  }, [])

  useEffect(() => {
    if (!user) return
    let alive = true
    api
      .get<AppState>('/api/state')
      .then((s) => alive && setState(s))
      .catch((e) => alive && showError(e))
    reloadCatalog()
    return () => {
      alive = false
    }
  }, [user?.id, reloadCatalog, showError])

  const logout = async () => {
    try {
      await api.post('/api/auth/logout')
      queryClient.clear()
      setUser(null)
      setState(null)
      setSessionId(null)
      setView('overview')
    } catch (cause) {
      showError(cause, '退出失败，请检查连接后重试')
    }
  }

  const openSession = (id: string) => {
    setSessionId(id)
    setView('session')
  }

  // 顶栏显示中文标签；目录还没到时退回显示后端原始值，不显示空白。
  const labels = summary(state ?? {}, catalog)
  /** 首屏错误改成顶部横幅 —— 不再顶掉整个主区（模块 10）。 */
  const notice = error ? errorNotice(errorStatus, error) : null

  if (!authChecked) {
    return (
      <div className="grid h-full place-items-center bg-bg text-[13px] text-muted">
        {authError ?? '正在检查账号…'}
      </div>
    )
  }

  if (!user) {
    return (
      <AuthScreen
        initialMode={authMode}
        initialError={authError ?? undefined}
        onAuthenticated={(next) => {
          setAuthError(null)
          setUser(next)
        }}
      />
    )
  }

  return (
    <div className={'app-shell flex h-full flex-col text-ink ' + (appearance?.background_exists ? '' : 'bg-bg')}>
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
              <span title="计算模式">{labels.mode}</span>
              <span className="text-line-strong">·</span>
              <span title="交互模式">{labels.interaction}</span>
              <span className="text-line-strong">·</span>
              <span title="回答风格">{labels.style}</span>
              <span className="text-line-strong">·</span>
              <span title="当前模型服务">{state.provider || '未配置模型'}</span>
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
          <Button
            size="sm"
            variant={view === 'run' ? 'default' : 'outline'}
            onClick={() => setView('run')}
          >
            新建运行
          </Button>
          <Button
            size="sm"
            variant={view === 'agents' ? 'default' : 'outline'}
            onClick={() => setView('agents')}
          >
            Agent 管理
          </Button>
          <Button
            size="sm"
            variant={view === 'knowledge' ? 'default' : 'outline'}
            onClick={() => setView('knowledge')}
          >
            知识库
          </Button>
          <Button
            size="sm"
            variant={view === 'operations' ? 'default' : 'outline'}
            onClick={() => setView('operations')}
          >
            团队与自动任务
          </Button>
          <Button
            size="sm"
            variant={view === 'system' ? 'default' : 'outline'}
            onClick={() => setView('system')}
          >
            系统
          </Button>
        </nav>

        <div className="ml-auto flex items-center gap-2">
          <span className="hidden text-[12px] text-muted sm:inline">{user.username}</span>
          <Button size="sm" variant="outline" onClick={() => setSettingsOpen(true)}>
            设置
          </Button>
          <Button size="sm" variant="outline" onClick={() => void logout()}>
            退出
          </Button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <aside className="flex w-64 shrink-0 flex-col border-r border-line bg-card">
          <SessionList
            activeId={sessionId}
            onSelect={openSession}
            refreshKey={sessionRefreshKey}
            onCreated={(id) => {
              setSessionRefreshKey((key) => key + 1)
              openSession(id)
            }}
            onDeleted={(id) => {
              if (id === sessionId) {
                setSessionId(null)
                setView('overview')
              }
              setSessionRefreshKey((key) => key + 1)
            }}
            onRenamed={(id) => {
              if (id === sessionId) setDetailRefreshKey((key) => key + 1)
              setSessionRefreshKey((key) => key + 1)
            }}
          />
        </aside>

        <main className="flex min-w-0 flex-1 flex-col">
          {notice && (
            <div className="app-error" role="alert">
              <span className="app-error-text">{notice.text}</span>
              {notice.retry && (
                <button
                  type="button"
                  className="app-error-btn"
                  onClick={() => {
                    setError(null)
                    void refreshState().catch((cause) => showError(cause))
                  }}
                >
                  重试
                </button>
              )}
              <button
                type="button"
                className="app-error-close"
                aria-label="关闭提示"
                onClick={() => setError(null)}
              >
                ×
              </button>
            </div>
          )}
          {/* 会话详情自带滚动容器与内边距；外层再滚一次会出现双滚动条、双 padding。 */}
          <div className={
            view === 'session'
              ? 'min-h-0 flex-1 overflow-hidden'
              : 'min-h-0 flex-1 overflow-y-auto p-6'
          }>
            {view === 'overview' ? (
              <OverviewPanel
                onOpenSession={openSession}
                onUsePrompt={(prompt) => {
                  setRunPreset({ goal: prompt, key: Date.now() })
                  setView('run')
                }}
              />
            ) : view === 'run' ? (
              <RunPanel
                state={state}
                catalog={catalog}
                initialGoal={runPreset?.goal ?? null}
                presetKey={runPreset?.key}
                onOpenSettings={() => setSettingsOpen(true)}
                onStarted={(id) => {
                  setRunPreset(null)
                  setSessionRefreshKey((key) => key + 1)
                  openSession(id)
                }}
              />
            ) : view === 'agents' ? (
              <AgentManagement />
            ) : view === 'knowledge' ? (
              <KnowledgeBase />
            ) : view === 'operations' ? (
              <OperationsPanel />
            ) : view === 'system' ? (
              <SystemPanel />
            ) : (
              <SessionDetail
                taskId={sessionId}
                onBack={() => setView('overview')}
                onRunEvent={() => setSessionRefreshKey((key) => key + 1)}
                catalog={catalog}
                refreshKey={detailRefreshKey}
              />
            )}
          </div>
        </main>
      </div>

      <PreferencesDialog
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        state={state}
        catalog={catalog}
        catalogLoading={catalogLoading}
        catalogError={catalogError}
        onReloadCatalog={reloadCatalog}
        onStateChange={refreshState}
        onAppearanceChange={applyAppearanceNow}
        user={user}
        onLogout={logout}
      />
    </div>
  )
}
