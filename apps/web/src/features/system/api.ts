/** 「系统」页的接口接入。三个后端能力都是**裸对象**（老前端还在用），api 客户端原样透传。 */

import { api } from '@/lib/api'
import { fetchSession, fetchSessions } from '@/features/sessions/api'
import type { Session, SessionDetail } from '@/lib/types'
import type { AuditPayload, DoctorReport, UsageSummary } from './systemOps'

/** 用量汇总。`hours` 由后端 clamp 到 1–8760 并回显在 `window_hours`。 */
export function fetchUsage(hours: number): Promise<UsageSummary> {
  return api.get<UsageSummary>(`/api/usage?hours=${encodeURIComponent(String(hours))}`)
}

/** 环境自检：只读，不访问网络、不扫描用户文件。 */
export function fetchDoctor(): Promise<DoctorReport> {
  return api.get<DoctorReport>('/api/doctor')
}

/** 某次会话的审计明细。会话不存在 / 不属于当前账号时是 404「没有这个会话」。 */
export function fetchAudit(taskId: string): Promise<AuditPayload> {
  return api.get<AuditPayload>(`/api/audit/${encodeURIComponent(taskId)}`)
}

/** 导出用的会话下拉。复用会话列表，不另开接口。 */
export function fetchExportSessions(limit = 50): Promise<Session[]> {
  return fetchSessions({ limit })
}

/** 导出对话 / 执行计划要用到消息与步骤，复用会话详情。 */
export function fetchExportDetail(taskId: string): Promise<SessionDetail> {
  return fetchSession(taskId)
}
