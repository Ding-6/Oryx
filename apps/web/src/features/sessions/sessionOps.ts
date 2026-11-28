/**
 * 会话列表的纯函数层 —— 不碰网络，便于就地断言。
 *
 * 三个容易做错的地方，都在这里钉住：
 *
 * 1. **后端 `updated_at` 是秒**（`time.time()`），`new Date(ts)` 会当成毫秒，
 *    结果所有会话都被归到「更早」。检查点那边用的是 `ts * 1000`，这里要一致。
 * 2. **搜索在服务端做**：`q` 过滤全部会话，不是「先拿一页再在前端筛」——
 *    否则三个月前的会话永远搜不到。改名后按新名字也要能搜到。
 * 3. **删运行中的会话会撞内核**：界面先拦一下，别让用户点完才发现出事。
 */
import type { Session, Todo } from '@/lib/types'

/** 一页多少条。是否还有下一页由「本页条数 == PAGE_SIZE」判断。 */
export const PAGE_SIZE = 30

export const SESSION_STATUS_CN: Record<string, string> = {
  running: '进行中',
  done: '已完成',
  failed: '失败',
  cancelled: '已取消',
  /** 新建、还没发过第一句话的空会话。内核续聊时才置成 running。 */
  idle: '未开始',
}

export interface Validation {
  ok: boolean
  message: string
}

export function sessionStatusLabel(status: string): string {
  return SESSION_STATUS_CN[status] ?? status
}

/**
 * 时间分组。`ts` 是**秒级**时间戳；`now` 是毫秒级，便于测试时注入固定时间。
 */
export function groupLabel(ts: number, now: number = Date.now()): string {
  if (!Number.isFinite(ts) || ts <= 0) return '更早'
  const dayMs = ts * 1000
  const day = new Date(dayMs)
  if (Number.isNaN(day.getTime())) return '更早'
  const current = new Date(now)
  const sameDay = (a: Date, b: Date) =>
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  if (sameDay(day, current)) return '今天'
  if (sameDay(day, new Date(now - 86400_000))) return '昨天'
  if (now - dayMs < 7 * 86400_000) return '本周'
  if (now - dayMs < 30 * 86400_000) return '本月'
  return '更早'
}

/** 按时间分组，保持传入顺序（后端已按 updated_at DESC 排好）。 */
export function groupSessions(
  sessions: Session[],
  now: number = Date.now(),
): Array<[string, Session[]]> {
  const map = new Map<string, Session[]>()
  for (const s of sessions) {
    const key = groupLabel(s.updated_at, now)
    if (!map.has(key)) map.set(key, [])
    map.get(key)!.push(s)
  }
  return [...map.entries()]
}

/** 构造 GET /api/sessions 的查询串。q 为空时不带这个参数。 */
export function buildSessionQuery(
  params: { limit?: number; offset?: number; q?: string } = {},
): string {
  const limit = Math.max(1, Math.min(Math.trunc(params.limit ?? 50) || 1, 200))
  const offset = Math.max(0, Math.trunc(params.offset ?? 0) || 0)
  const q = (params.q ?? '').trim()
  const qs = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  if (q) qs.set('q', q)
  return qs.toString()
}

/** 满页就还有下一页（不额外要 total，保持 data 是数组的 v2 形状）。 */
export function hasMorePage(pageLength: number, pageSize: number = PAGE_SIZE): boolean {
  return pageLength >= pageSize && pageSize > 0
}

/** 空串合法（= 撤掉自定义名字），只有长度要拦。 */
export function validateSessionTitle(title: string): Validation {
  if ((title ?? '').trim().length > 60) {
    return { ok: false, message: '会话名太长了（最多 60 字）' }
  }
  return { ok: true, message: '' }
}

export function validateTodoTitle(title: string): Validation {
  const name = (title ?? '').trim()
  if (!name) return { ok: false, message: '待办得有个标题' }
  if (name.length > 200) return { ok: false, message: '标题太长了（最多 200 字）' }
  return { ok: true, message: '' }
}

/** 列表第二行：状态 · 消息数 · 未完成待办数。 */
export function sessionMeta(s: Session): string {
  const bits = [sessionStatusLabel(s.status)]
  if (s.msg_count > 0) bits.push(`${s.msg_count} 条消息`)
  if (s.todo_open > 0) bits.push(`${s.todo_open} 项待办`)
  return bits.join(' · ')
}

export function todoSummary(todos: Todo[]): {
  total: number
  done: number
  open: number
  percent: number
} {
  const total = todos.length
  const done = todos.filter((t) => t.status === 'done').length
  return {
    total,
    done,
    open: total - done,
    percent: total ? Math.round((done * 100) / total) : 0,
  }
}

/** 运行中的会话不许删 —— 内核还在写文件、还在等审批。 */
export function canDeleteSession(status: string): boolean {
  return status !== 'running'
}

export const DELETE_RUNNING_HINT = '这个会话正在运行，先停止再删除'
