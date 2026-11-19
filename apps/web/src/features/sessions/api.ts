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
import type { Session, SessionDetail, SessionUsage, Todo } from '@/lib/types'

/** 会话列表。data 是**数组**（不是 {sessions:[...]}）。 */
export function fetchSessions(limit = 50): Promise<Session[]> {
  return api.get<Session[]>(`/api/sessions?limit=${limit}`)
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

/** 改名。空串 = 撤掉自定义名字。 */
export function renameSession(
  taskId: string,
  title: string,
): Promise<{ renamed: string; title: string }> {
  return api.put(`/api/sessions/${encodeURIComponent(taskId)}`, { title })
}

/** 删会话。 */
export function deleteSession(taskId: string): Promise<{ deleted: string }> {
  return api.del(`/api/sessions/${encodeURIComponent(taskId)}`)
}

/** 用量摘要。 */
export function fetchSessionUsage(taskId: string): Promise<SessionUsage> {
  return api.get<SessionUsage>(`/api/sessions/${encodeURIComponent(taskId)}/usage`)
}
