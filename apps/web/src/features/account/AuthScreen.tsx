import { useState, type FormEvent } from 'react'

import { ApiError, api } from '@/lib/api'
import { Button } from '@/components/ui/button'

export interface AccountUser {
  id: string
  username: string
  created_at?: number
}

interface AuthScreenProps {
  initialMode: 'login' | 'register'
  initialError?: string
  onAuthenticated: (user: AccountUser) => void
}

export default function AuthScreen({ initialMode, initialError, onAuthenticated }: AuthScreenProps) {
  const [mode, setMode] = useState(initialMode)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(initialError ?? '')

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError('')
    if (mode === 'register' && password !== confirm) {
      setError('两次输入的密码不一致')
      return
    }
    setBusy(true)
    try {
      const endpoint = mode === 'login' ? '/api/auth/login' : '/api/auth/register'
      const user = await api.post<AccountUser>(endpoint, { username, password })
      onAuthenticated(user)
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : '暂时无法连接服务，请稍后重试')
    } finally {
      setBusy(false)
    }
  }

  const chooseMode = (next: 'login' | 'register') => {
    setMode(next)
    setError('')
  }

  return (
    <main className="grid min-h-full place-items-center bg-bg px-5 py-10 text-ink">
      <section className="w-full max-w-[420px] rounded-[var(--r-lg)] border border-line bg-card p-7 shadow-card">
        <div className="mb-7 flex items-center gap-3">
          <span className="grid h-10 w-10 place-items-center rounded-xl bg-gradient-to-br from-brand-1 to-brand-2 text-sm font-semibold text-white">
            O
          </span>
          <div>
            <h1 className="text-lg font-semibold tracking-tight">{mode === 'register' ? '创建 Oryx 账号' : '登录 Oryx'}</h1>
            <p className="mt-0.5 text-[13px] text-muted">对话与长期记忆仅对所属账号可见</p>
          </div>
        </div>

        <div className="mb-5 grid grid-cols-2 rounded-lg bg-bg-2 p-1" role="tablist" aria-label="账号操作">
          <button
            type="button"
            role="tab"
            aria-selected={mode === 'login'}
            onClick={() => chooseMode('login')}
            className={`rounded-md px-3 py-2 text-[13px] transition-colors ${mode === 'login' ? 'bg-card font-medium text-ink shadow-sm' : 'text-muted hover:text-ink'}`}
          >
            登录
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === 'register'}
            onClick={() => chooseMode('register')}
            className={`rounded-md px-3 py-2 text-[13px] transition-colors ${mode === 'register' ? 'bg-card font-medium text-ink shadow-sm' : 'text-muted hover:text-ink'}`}
          >
            注册
          </button>
        </div>

        <form className="space-y-4" onSubmit={submit}>
          <label className="block space-y-1.5 text-[13px] font-medium">
            账号
            <input
              autoComplete="username"
              autoFocus
              required
              minLength={3}
              maxLength={32}
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              placeholder="3–32 位字母、数字、_ 或 -"
              className="h-10 w-full rounded-md border border-line bg-bg px-3 text-sm font-normal outline-none transition focus:border-accent focus:ring-2 focus:ring-accent/20"
            />
          </label>

          <label className="block space-y-1.5 text-[13px] font-medium">
            密码
            <input
              type="password"
              autoComplete={mode === 'register' ? 'new-password' : 'current-password'}
              required
              minLength={mode === 'register' ? 10 : 1}
              maxLength={256}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder={mode === 'register' ? '至少 10 位' : '输入密码'}
              className="h-10 w-full rounded-md border border-line bg-bg px-3 text-sm font-normal outline-none transition focus:border-accent focus:ring-2 focus:ring-accent/20"
            />
          </label>

          {mode === 'register' && (
            <label className="block space-y-1.5 text-[13px] font-medium">
              确认密码
              <input
                type="password"
                autoComplete="new-password"
                required
                maxLength={256}
                value={confirm}
                onChange={(event) => setConfirm(event.target.value)}
                placeholder="再次输入密码"
                className="h-10 w-full rounded-md border border-line bg-bg px-3 text-sm font-normal outline-none transition focus:border-accent focus:ring-2 focus:ring-accent/20"
              />
            </label>
          )}

          {error && (
            <p role="alert" className="rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-[13px] text-danger">
              {error}
            </p>
          )}

          <Button type="submit" className="w-full" disabled={busy}>
            {busy ? '处理中…' : mode === 'register' ? '创建账号' : '登录'}
          </Button>
        </form>

        <p className="mt-5 text-center text-[12px] leading-5 text-muted">
          Agent、专家人设等平台能力共享；对话与长期记忆按账号隔离。
        </p>
      </section>
    </main>
  )
}
