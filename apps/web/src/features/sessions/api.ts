/**
 * 会话域的前端接入 —— v2 契约的第一条完整链路。
 *
 * 这条链路是**样板**（backend 的 session 域同样按它改造）：
 *
 *     契约层（后端 pydantic）→ 统一响应 → 前端类型化调用
 *
 * 关键点：`api.get` 返回的是**领域对象**而不是整个响应体。
 * 后端 `{code, data, message}` 的壳在这里剥掉 —— 前端拿到的就是
 * `Session[]` / `SessionDetail`，不用到处写 `.data`。
 */

import { api } from '@/lib/api'
import type {
  CheckpointList,
  CreateSessionResult,
  PendingApproval,
  RenameResult,
  RewindResult,
  Session,
  SessionDetail,
  SessionUsage,
  Todo,
} from '@/lib/types'
import type { RewindScope } from './checkpoints'
import { buildSessionQuery } from './sessionOps'

/** 列表查询：分页 + 服务端搜索（q 过滤全部会话，不是当前这一页）。 */
export interface SessionQuery {
  limit?: number
  offset?: number
  q?: string
}

/** 会话列表。data 是**数组**（不是 {sessions:[...]}）。 */
export function fetchSessions(params: SessionQuery = {}): Promise<Session[]> {
  return api.get<Session[]>(`/api/sessions?${buildSessionQuery(params)}`)
}

/** 新建一条空会话。不传名字时后端兜底成「新会话」。 */
export function createSession(title = ''): Promise<CreateSessionResult> {
  const name = title.trim()
  return api.post<CreateSessionResult>('/api/sessions', name ? { title: name } : {})
}

/** 会话详情。 */
export function fetchSession(taskId: string): Promise<SessionDetail> {
  return api.get<SessionDetail>(`/api/sessions/${encodeURIComponent(taskId)}`)
}

/** 改待办状态。 */
export function setTodoStatus(
  taskId: string,
  todoId: string,
  status: Todo['status'],
): Promise<{ updated: string; todo: Todo }> {
  return api.put(`/api/sessions/${encodeURIComponent(taskId)}/todos/${encodeURIComponent(todoId)}`, {
    status,
  })
}

/** 改名。空串 = 撤掉自定义名字，退回 goal 摘要。 */
export function renameSession(taskId: string, title: string): Promise<RenameResult> {
  return api.put<RenameResult>(`/api/sessions/${encodeURIComponent(taskId)}`, { title })
}

/** 删会话。后端会连检查点、消息、步骤、待办、用量一起清。 */
export function deleteSession(taskId: string): Promise<{ deleted: string }> {
  return api.del<{ deleted: string }>(`/api/sessions/${encodeURIComponent(taskId)}`)
}

/** 用户手动加一条待办（以前只有 Agent 能建）。 */
export function createTodo(taskId: string, title: string): Promise<Todo> {
  return api.post<Todo>(
    `/api/sessions/${encodeURIComponent(taskId)}/todos`,
    { title: title.trim() },
  )
}

/**
 * 当前待审批的操作 —— 页面刷新 / SSE 断线后的兜底。
 * 内核还没就绪时后端返回空数组，不当成错误。
 */
export function fetchPendingApprovals(taskId: string): Promise<PendingApproval[]> {
  return api.get<PendingApproval[]>(`/api/sessions/${encodeURIComponent(taskId)}/approvals`)
}

/** 批准 / 拒绝一个 L2 操作。 */
export function approveStep(stepId: string, approved: boolean): Promise<{ step_id: string; approved: boolean }> {
  return api.post(`/api/approve`, { step_id: stepId, approved })
}

/** 用量摘要。 */
export function fetchSessionUsage(taskId: string): Promise<SessionUsage> {
  return api.get<SessionUsage>(`/api/sessions/${encodeURIComponent(taskId)}/usage`)
}

/**
 * 这个会话有哪些检查点（可回退的位置）。
 *
 * 内核没就绪时后端会 503 —— 调用方当成「暂时没有」处理即可，
 * 不该让整个会话页报错。
 */
export function fetchCheckpoints(taskId: string): Promise<CheckpointList> {
  return api.get<CheckpointList>(`/api/checkpoints/${encodeURIComponent(taskId)}`)
}

/**
 * 回退到某个检查点。
 *
 * 后端在**这个会话正在运行**时返回 400（带着人话），
 * 调用方直接展示 message 即可，不需要自己拼提示。
 */
export function rewindCheckpoint(
  checkpointId: string,
  scope: RewindScope,
): Promise<RewindResult> {
  return api.post<RewindResult>(
    `/api/checkpoints/${encodeURIComponent(checkpointId)}/rewind`,
    { scope },
  )
}
