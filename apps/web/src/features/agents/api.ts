import { api } from '@/lib/api'
import type { AgentCatalog } from './agentCatalog'

export interface AgentDefinition {
  id: string
  name: string
  description: string
  tools: string[]
  max_turns: number
  model: string
  permission_mode: string
  skills: string[]
  disallowed_tools: string[]
  memory: string
  initial_prompt: string
  icon: string
  subtitle: string
  kind: string
  body: string
  creator_id: string | null
  can_edit: boolean
  shared: boolean
}

export interface ExpertDefinition {
  id: string
  name: string
  description: string
  scenarios: string[]
  tags: string[]
  personality: string
  speaking_style: string
  use_cases: string
  self_intro: string
  body: string
  creator_id: string | null
  can_edit: boolean
  shared: boolean
}

/** 候选目录：工具 / 技能 / 模型服务 / 枚举。内核没起来时逐项降级，不会 503。 */
export const fetchAgentCatalog = () => api.get<AgentCatalog>('/api/agents/catalog')

export const listAgents = () => api.get<AgentDefinition[]>('/api/agents')
export const listExperts = () => api.get<ExpertDefinition[]>('/api/experts')
export const createAgent = (payload: Partial<AgentDefinition>) => api.post('/api/agents', payload)
export const updateAgent = (id: string, payload: Partial<AgentDefinition>) =>
  api.put(`/api/agents/${encodeURIComponent(id)}`, payload)
export const deleteAgent = (id: string) => api.del(`/api/agents/${encodeURIComponent(id)}`)
export const createExpert = (payload: Partial<ExpertDefinition>) => api.post('/api/experts', payload)
export const updateExpert = (id: string, payload: Partial<ExpertDefinition>) =>
  api.put(`/api/experts/${encodeURIComponent(id)}`, payload)
export const deleteExpert = (id: string) => api.del(`/api/experts/${encodeURIComponent(id)}`)

