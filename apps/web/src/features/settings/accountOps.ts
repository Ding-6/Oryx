/**
 * 账号页纯函数 —— `npm run check:account` 就地断言（模块 12）。
 *
 * 为什么单独一层：改密码表单最容易做成「看起来能用」的假功能 ——
 * 前端不拦、后端 422 才报；或者把 confirm 一起发到后端；
 * 或者成功提示说「已让所有设备下线」但当前设备其实还登着。
 * 这些都能在 Node 里断言，不必开浏览器。
 */
export const PASSWORD_MIN = 10
export const PASSWORD_MAX = 256

export interface PasswordDraft {
  oldPassword: string
  newPassword: string
  confirm: string
}

/**
 * 返回中文错误；`null` 表示可以提交。
 * 顺序刻意固定：先空值、再长度、再「与原密码相同」、最后两次一致，
 * 避免「两次不一致」把「新密码太短」盖掉，用户来回改还是提交不了。
 */
export function validatePasswordChange(draft: PasswordDraft): string | null {
  const { oldPassword, newPassword, confirm } = draft
  if (!oldPassword) return '请输入原密码'
  if (!newPassword) return '请输入新密码'
  if (newPassword.length < PASSWORD_MIN) return `新密码至少需要 ${PASSWORD_MIN} 位`
  if (newPassword.length > PASSWORD_MAX) return `新密码不能超过 ${PASSWORD_MAX} 位`
  if (newPassword === oldPassword) return '新密码不能与原密码相同'
  if (newPassword !== confirm) return '两次输入的新密码不一致'
  return null
}

/** 请求体只带后端要的两个字段：确认框与账号名绝不外发。 */
export function passwordChangeBody(draft: PasswordDraft): {
  old_password: string
  new_password: string
} {
  return { old_password: draft.oldPassword, new_password: draft.newPassword }
}

/** 提交按钮的禁用条件：有校验错误，或上一次请求还没回来。 */
export function canSubmitPasswordChange(draft: PasswordDraft, busy: boolean): boolean {
  return !busy && validatePasswordChange(draft) === null
}

/** 成功提示按真实注销数如实说，不把「当前会话保留」说成「全部下线」。 */
export function changeNotice(revoked: number): string {
  const n = Number.isFinite(revoked) && revoked > 0 ? Math.floor(revoked) : 0
  if (n === 0) return '密码已修改。当前设备保持登录状态。'
  return `密码已修改。其它 ${n} 个登录会话已失效，需要在那些设备上重新登录。`
}

/** 只做长度提示 —— 不搞一个没有依据的「强度百分比」。 */
export function passwordHint(value: string): string {
  if (!value) return `至少 ${PASSWORD_MIN} 位，最长 ${PASSWORD_MAX} 位`
  if (value.length < PASSWORD_MIN) return `还差 ${PASSWORD_MIN - value.length} 位`
  if (value.length > PASSWORD_MAX) return `超出 ${value.length - PASSWORD_MAX} 位`
  return '长度符合要求'
}

/**
 * 后端 `created_at` 是 `time.time()` 的**秒**（模块 5 踩过：直接
 * `new Date(ts)` 会把所有会话归到 1970）。已经是毫秒的就别再乘。
 */
export function accountCreatedAt(ts?: number): string {
  if (!ts || !Number.isFinite(ts) || ts <= 0) return '未记录'
  const ms = ts < 1e12 ? ts * 1000 : ts
  const d = new Date(ms)
  if (Number.isNaN(d.getTime())) return '未记录'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

export const LOGOUT_CONFIRM = '退出后需要重新输入账号密码。确定要退出吗？'

export function logoutBusyLabel(busy: boolean): string {
  return busy ? '正在退出…' : '退出登录'
}
