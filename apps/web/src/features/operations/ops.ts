/**
 * 团队与自动任务的纯函数层 —— 不含 React、不含网络，方便零依赖断言。
 *
 * 这个模块钉住四条容易回退的规则：
 *
 * 1. **整体保存**：请求体只带 `name / goal / members`，绝不带 `member / role`。
 *    后端靠「有没有 members」区分「整体保存」和「改单个成员分工」，
 *    多带一个旧字段就会走回老路径，半保存的问题又回来了。
 * 2. **成员下限 1 位、上限 8 位**，前端先拦一道，别等 400 才告诉用户。
 * 3. **派发轮询**：running 才继续，超次数要停并给人话提示，不能无限轮询。
 * 4. **启停防重复**：同一个任务在途时不能再点，「停用/启用」文案按目标态算。
 */
import type { AutomationDefinition, DispatchJob, TeamDefinition, TeamMember } from './api'

export interface TeamDraftLike {
  name: string
  goal: string
  members: TeamMember[]
}

export interface FieldError {
  field: 'name' | 'goal' | 'members' | 'cron'
  message: string
}

export const TEAM_MEMBER_MIN = 1
export const TEAM_MEMBER_MAX = 8
export const TEAM_NAME_MAX = 80
export const TEAM_GOAL_MAX = 4000
export const MEMBER_ROLE_MAX = 80
export const MEMBER_ID_MAX = 100

/** 整体保存的提示；失败时后端文案会覆盖它。 */
export const SAVE_TEAM_HINT = '团队定义会一次性整体保存，不会逐条改到一半'

/** 派发轮询：1.2 秒一次，最多 250 次（5 分钟）。 */
export const DISPATCH_POLL_MS = 1200
export const DISPATCH_MAX_POLLS = 250
export const DISPATCH_TIMEOUT_HINT = '派发还在进行中，可稍后刷新团队信箱查看结论'

/** 从详情生成可编辑草稿（深拷贝，编辑时不能改到详情本身）。 */
export function draftFromTeam(detail: TeamDefinition): TeamDraftLike {
  return {
    name: detail.name,
    goal: detail.goal || '',
    members: detail.members.map((m) => ({ agent_id: m.agent_id, role: m.role || '' })),
  }
}

/** 整体保存的请求体。刻意不展开多余字段。 */
export function teamSaveBody(draft: TeamDraftLike) {
  return {
    name: draft.name.trim(),
    goal: draft.goal.trim(),
    members: draft.members.map((m) => ({
      agent_id: m.agent_id.trim(),
      role: (m.role || '').trim(),
    })),
  }
}

/** 新建团队的请求体（与整体保存同形，但走 POST）。 */
export function teamCreateBody(draft: TeamDraftLike) {
  return teamSaveBody(draft)
}

export function validateTeamDraft(draft: TeamDraftLike): FieldError | null {
  const name = (draft.name || '').trim()
  if (!name) return { field: 'name', message: '请填写团队名称' }
  if (name.length > TEAM_NAME_MAX) {
    return { field: 'name', message: `团队名称不能超过 ${TEAM_NAME_MAX} 个字符` }
  }
  if (name.includes('/') || name.includes('\\')) {
    return { field: 'name', message: '团队名称不能包含斜杠' }
  }
  if ((draft.goal || '').length > TEAM_GOAL_MAX) {
    return { field: 'goal', message: `团队目标不能超过 ${TEAM_GOAL_MAX} 个字符` }
  }
  const members = draft.members || []
  if (members.length < TEAM_MEMBER_MIN) {
    return { field: 'members', message: '至少要选择 1 位子 Agent' }
  }
  if (members.length > TEAM_MEMBER_MAX) {
    return { field: 'members', message: `成员最多 ${TEAM_MEMBER_MAX} 位` }
  }
  const seen = new Set<string>()
  for (const m of members) {
    const id = (m.agent_id || '').trim()
    if (!id || id.length > MEMBER_ID_MAX) {
      return { field: 'members', message: '成员 id 不能为空或过长' }
    }
    if (seen.has(id)) {
      return { field: 'members', message: `成员「${id}」重复了` }
    }
    seen.add(id)
    if ((m.role || '').length > MEMBER_ROLE_MAX) {
      return { field: 'members', message: `成员职责不能超过 ${MEMBER_ROLE_MAX} 个字符` }
    }
  }
  return null
}

/** 草稿与详情是否一致（用于禁用「没改动还点保存」）。 */
export function teamDraftEquals(a: TeamDraftLike, b: TeamDraftLike): boolean {
  const left = teamSaveBody(a)
  const right = teamSaveBody(b)
  if (left.name !== right.name || left.goal !== right.goal) return false
  if (left.members.length !== right.members.length) return false
  return left.members.every((m, i) =>
    m.agent_id === right.members[i].agent_id && m.role === right.members[i].role,
  )
}

// ── 派发作业 ────────────────────────────────────────────────────────

export type DispatchStatus = 'running' | 'done' | 'failed'

export function isDispatchRunning(job: DispatchJob | null | undefined): boolean {
  return job?.status === 'running'
}

export function dispatchStatusText(job: DispatchJob | null | undefined): string {
  if (!job) return ''
  if (job.status === 'running') return '派发中…'
  if (job.status === 'done') return '已完成派发'
  return '派发失败'
}

/** 作业结束后给用户的通知；结论太长就截断，详情在信箱里看。 */
export function dispatchResultNotice(job: DispatchJob, agentName: string): string {
  if (job.status === 'failed') {
    return `${agentName} 派发失败：${job.error || '未知原因'}`
  }
  const text = (job.content || '').trim()
  if (!text) return `${agentName} 已派发完成，但没有产出文本`
  return `${agentName} 已完成派发：${text.slice(0, 120)}${text.length > 120 ? '…' : ''}`
}

/** 是否还要继续轮询：只有 running 且没超上限。 */
export function shouldContinuePolling(
  job: DispatchJob | null | undefined,
  polls: number,
): boolean {
  return isDispatchRunning(job) && polls < DISPATCH_MAX_POLLS
}

/** 轮询耗尽时的兜底提示（job 仍是 running）。 */
export function dispatchTimedOut(job: DispatchJob | null | undefined, polls: number): boolean {
  return isDispatchRunning(job) && polls >= DISPATCH_MAX_POLLS
}

export function dispatchDuration(job: DispatchJob): number {
  const end = job.finished_at ?? Date.now() / 1000
  return Math.max(0, Math.round(end - job.started_at))
}

// ── 自动任务 ────────────────────────────────────────────────────────

export function busyKey(action: string, id: string): string {
  return `${action}:${id}`
}

/** 目标态：把「停用/启用」按钮的意图算清楚，避免按旧值猜。 */
export function toggleIntent(item: AutomationDefinition): boolean {
  return !item.enabled
}

export function canToggle(item: AutomationDefinition, busy: ReadonlySet<string>): boolean {
  return !busy.has(busyKey('toggle', item.id))
}

export function toggleNotice(targetEnabled: boolean): string {
  return targetEnabled ? '自动任务已启用' : '自动任务已停用'
}

export function validateAutomationDraft(draft: {
  name: string
  cron: string
  goal: string
}): FieldError | null {
  if (!(draft.name || '').trim()) return { field: 'name', message: '请填写任务名称' }
  if (!(draft.cron || '').trim()) return { field: 'cron', message: '请填写 Cron 表达式' }
  if (!(draft.goal || '').trim()) return { field: 'goal', message: '请填写执行目标' }
  return null
}

export function runStatusLabel(status: string): string {
  if (status === 'done') return '成功'
  if (status === 'failed') return '失败'
  return '运行中'
}
