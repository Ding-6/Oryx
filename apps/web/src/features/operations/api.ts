import { api } from '@/lib/api'

export interface TeamMember {
  agent_id: string
  role: string
}

export interface TeamMessage {
  id: string
  sender: string
  recipient: string
  content: string
  ts: number
  read: boolean
}

export interface TeamDefinition {
  id: string
  name: string
  goal: string
  members: TeamMember[]
  thread: TeamMessage[]
  message_count: number
  unread: number
  creator_id: string | null
  shared: boolean
  can_edit: boolean
}

export interface AutomationDefinition {
  id: string
  name: string
  cron: string
  goal: string
  enabled: boolean
  recurring: boolean
  last_run: number | null
  next_run: number | null
  run_count: number
  retry_count: number
  max_retries: number
  retrying: boolean
  timezone: 'Asia/Shanghai'
  description: string
}

/** 后台派发作业。`status` 由 POST 提交时的 running 变成 done / failed。 */
export interface DispatchJob {
  job_id: string
  team: string
  agent: string
  status: 'running' | 'done' | 'failed'
  content: string
  error: string
  started_at: number
  finished_at: number | null
}

/** 整体保存团队：带 members 时后端一次校验一次落地。 */
export interface TeamSaveResult {
  ok: boolean
  team: TeamDefinition
}

export interface AutomationRun {
  id: string
  automation_id: string
  name: string
  goal: string
  status: 'running' | 'done' | 'failed'
  output: string
  error: string
  task_id: string | null
  started_at: number
  ended_at: number
  duration_ms: number
  attempt: number
  parent_run_id: string | null
  manual_retry: boolean
}

export const listTeams = () => api.get<{ teams: TeamDefinition[] }>('/api/teams')
export const getTeam = (name: string) => api.get<TeamDefinition>(`/api/teams/${encodeURIComponent(name)}`)
export const createTeam = (payload: { name: string; goal: string; members: TeamMember[] }) =>
  api.post<TeamDefinition>('/api/teams', payload)
export const updateTeam = (name: string, payload: { name?: string; goal?: string; member?: string; role?: string }) =>
  api.put<{ ok: boolean; team: TeamDefinition }>(`/api/teams/${encodeURIComponent(name)}`, payload)
export const deleteTeam = (name: string) => api.del(`/api/teams/${encodeURIComponent(name)}`)

/** 整体保存：名字 / 目标 / 成员名单一次提交，不再拆成多次串行请求。 */
export const saveTeamConfig = (
  name: string,
  payload: { name: string; goal: string; members: TeamMember[] },
) => api.put<TeamSaveResult>(`/api/teams/${encodeURIComponent(name)}`, payload)

/** 提交后台派发：立刻回 job_id，不等待子 Agent 跑完。 */
export const startDispatchJob = (name: string, agent: string) =>
  api.post<DispatchJob>(`/api/teams/${encodeURIComponent(name)}/dispatch/jobs`, { agent })

/** 轮询作业状态。别人的作业、别的团队一律 404。 */
export const getDispatchJob = (name: string, jobId: string) =>
  api.get<DispatchJob>(
    `/api/teams/${encodeURIComponent(name)}/dispatch/jobs/${encodeURIComponent(jobId)}`,
  )

export const addTeamMember = (name: string, agent: string, role: string) =>
  api.post(`/api/teams/${encodeURIComponent(name)}/members`, { agent, role })
export const removeTeamMember = (name: string, agent: string) =>
  api.del(`/api/teams/${encodeURIComponent(name)}/members/${encodeURIComponent(agent)}`)
export const sendTeamMessage = (name: string, to: string, content: string) =>
  api.post(`/api/teams/${encodeURIComponent(name)}/message`, { to, content })
export const dispatchTeamMember = (name: string, agent: string) =>
  api.post(`/api/teams/${encodeURIComponent(name)}/dispatch`, { agent })

export const listAutomations = () => api.get<{ automations: AutomationDefinition[] }>('/api/automations')
export const createAutomation = (payload: Pick<AutomationDefinition, 'name' | 'cron' | 'goal'>) =>
  api.post<AutomationDefinition>('/api/automations', payload)
export const updateAutomation = (
  id: string,
  payload: Partial<Pick<AutomationDefinition, 'name' | 'cron' | 'goal'>>,
) => api.put<{ updated: AutomationDefinition }>(`/api/automations/${encodeURIComponent(id)}`, payload)
export const deleteAutomation = (id: string) => api.del(`/api/automations/${encodeURIComponent(id)}`)
export const toggleAutomation = (id: string, enabled: boolean) =>
  api.post(`/api/automations/${encodeURIComponent(id)}/toggle`, { enabled })
export const listAutomationRuns = (id: string) =>
  api.get<{ runs: AutomationRun[] }>(`/api/automations/${encodeURIComponent(id)}/runs`)
export const retryAutomationRun = (runId: string) =>
  api.post(`/api/automation-runs/${encodeURIComponent(runId)}/retry`)
