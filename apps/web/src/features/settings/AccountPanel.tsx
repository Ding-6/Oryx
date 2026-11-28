import { useState, type ChangeEvent, type FormEvent } from 'react'

import { Button } from '@/components/ui/button'
import { ApiError } from '@/lib/api'
import type { AccountUser } from '@/features/account/AuthScreen'
import { changePassword } from './api'
import {
  LOGOUT_CONFIRM,
  accountCreatedAt,
  canSubmitPasswordChange,
  changeNotice,
  logoutBusyLabel,
  passwordChangeBody,
  passwordHint,
  validatePasswordChange,
  type PasswordDraft,
} from './accountOps'

const EMPTY: PasswordDraft = { oldPassword: '', newPassword: '', confirm: '' }

function messageOf(cause: unknown): string {
  if (cause instanceof ApiError) return cause.message
  return cause instanceof Error ? cause.message : '修改失败，请稍后重试'
}

/**
 * 账号页 —— 看账号信息、改密码、退出登录。
 *
 * 不做角色体系：界面如实写「本机没有角色体系」，不摆一个点了没用的下拉。
 * 改密码成功后当前会话仍有效，所以这里不主动跳登录页，只把
 * 「其它 N 个会话已失效」如实告诉用户。
 */
export default function AccountPanel({
  user,
  onLogout,
}: {
  user: AccountUser | null
  /** 由 App 统一清态并回登录页 —— 面板不自己改登录状态。 */
  onLogout: () => Promise<void> | void
}) {
  const [draft, setDraft] = useState<PasswordDraft>(EMPTY)
  const [busy, setBusy] = useState(false)
  const [loggingOut, setLoggingOut] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const validation = validatePasswordChange(draft)
  const canSubmit = canSubmitPasswordChange(draft, busy)

  const update = (key: keyof PasswordDraft) => (event: ChangeEvent<HTMLInputElement>) => {
    const value = event.target.value
    setDraft((prev) => ({ ...prev, [key]: value }))
  }

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!canSubmit) {
      setError(validation)
      return
    }
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const body = passwordChangeBody(draft)
      const result = await changePassword(body.old_password, body.new_password)
      setDraft(EMPTY)
      setNotice(changeNotice(result.revoked_sessions))
    } catch (cause) {
      setError(messageOf(cause))
    } finally {
      setBusy(false)
    }
  }

  const logout = async () => {
    if (loggingOut || !window.confirm(LOGOUT_CONFIRM)) return
    setLoggingOut(true)
    setError(null)
    try {
      await onLogout()
    } catch (cause) {
      setError(messageOf(cause))
    } finally {
      setLoggingOut(false)
    }
  }

  return (
    <div className="ac-panel">
      {error && <p className="sys-error" role="alert">{error}</p>}
      {notice && <p className="sys-notice" role="status">{notice}</p>}

      <section className="ac-card">
        <h3 className="sys-subtitle">当前账号</h3>
        <div className="ac-row">
          <span className="ac-row-label">账号名</span>
          <strong className="ac-row-value">{user?.username ?? '—'}</strong>
        </div>
        <div className="ac-row">
          <span className="ac-row-label">创建时间</span>
          <span className="ac-row-value">{accountCreatedAt(user?.created_at)}</span>
        </div>
        <div className="ac-row">
          <span className="ac-row-label">账号 ID</span>
          <code className="ac-mono">{user?.id ?? '—'}</code>
        </div>
        <p className="sys-hint ac-note">
          对话、长期记忆与会话按账号隔离。本机没有角色体系，只有「创建者可改、他人只读」两档。
        </p>
      </section>

      <form className="ac-form" onSubmit={(e) => void submit(e)}>
        <h3 className="sys-subtitle">修改密码</h3>
        <p className="sys-hint ac-note">
          改完后当前设备保持登录；其它设备的登录会话会立即失效，需要重新登录。
        </p>

        <label className="sys-field" htmlFor="ac-old">
          <span>原密码</span>
          <input
            id="ac-old"
            type="password"
            autoComplete="current-password"
            className="ac-input"
            value={draft.oldPassword}
            onChange={update('oldPassword')}
            disabled={busy}
          />
        </label>
        <label className="sys-field" htmlFor="ac-new">
          <span>新密码</span>
          <input
            id="ac-new"
            type="password"
            autoComplete="new-password"
            className="ac-input"
            value={draft.newPassword}
            onChange={update('newPassword')}
            disabled={busy}
          />
        </label>
        <p className={'ac-hint' + (draft.newPassword && validation ? ' ac-hint--warn' : '')}>
          {draft.newPassword && validation ? validation : passwordHint(draft.newPassword)}
        </p>
        <label className="sys-field" htmlFor="ac-confirm">
          <span>确认新密码</span>
          <input
            id="ac-confirm"
            type="password"
            autoComplete="new-password"
            className="ac-input"
            value={draft.confirm}
            onChange={update('confirm')}
            disabled={busy}
          />
        </label>

        <div className="mm-form-actions">
          <Button size="sm" type="submit" disabled={!canSubmit}>
            {busy ? '正在修改…' : '修改密码'}
          </Button>
          <span className="sys-hint">至少 10 位，最长 256 位；新密码不能与原密码相同。</span>
        </div>
      </form>

      <section className="ac-card ac-danger">
        <div>
          <h3 className="sys-subtitle">退出登录</h3>
          <p className="sys-hint">退出后需要重新输入账号密码，服务端会撤销当前会话。</p>
        </div>
        <Button size="sm" variant="outline" disabled={loggingOut} onClick={() => void logout()}>
          {logoutBusyLabel(loggingOut)}
        </Button>
      </section>
    </div>
  )
}
