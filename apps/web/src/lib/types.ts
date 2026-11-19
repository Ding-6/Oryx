/**
 * v2 契约的类型定义 —— **从后端代码反推**，不是手写的猜测。
 *
 * 对应的后端实现：
 *   · session 域   core/oryx/domains/session.py
 *   · overview 域  core/oryx/domains/overview.py
 *
 * 契约有测试守着与代码一致（core/tests/test_session_contract.py、
 * core/tests/test_interface_spec_v2.py），所以这里的字段名不会悄悄对不上。
 *
 * **`data` 的形状规则**（spec §1.1）
 * ---------------------------------
 *   · 领域对象本身是一个列表 → data 是**数组**
 *   · 否则 → data 是**对象**
 */

/** v2 统一响应体。 */
export interface Envelope<T> {
  code: number
  data: T
  message: string
}

/** 会话列表项（GET /api/sessions 的 data 元素）。 */
export interface Session {
  id: string
  goal: string
  title: string
  status: string
  mode: string
  provider: string
  created_at: number
  updated_at: number
  msg_count: number
  step_count: number
  todo_open: number
  /** 用户改过名字（false 时标题是 goal 的摘要）。 */
  renamed: boolean
}

/** 会话里的一条消息。 */
export interface Message {
  id: string
  task_id: string
  role: 'user' | 'assistant' | string
  content: string
  ts: number
}

/** 一条执行步骤。 */
export interface Step {
  id: string
  task_id: string
  seq: number
  agent: string | null
  tool: string | null
  args: string | null
  state: string | null
  risk: number | null
  result: string | null
  error: string | null
  started_at: number | null
  ended_at: number | null
}

/** 一条待办。 */
export interface Todo {
  id: string
  seq: number
  title: string
  status: 'pending' | 'in_progress' | 'done' | string
}

/** 用量统计。 */
export interface Usage {
  calls: number
  input_tokens: number
  output_tokens: number
}

/** 会话详情（GET /api/sessions/{id} 的 data）。 */
export interface SessionDetail {
  task: Record<string, unknown>
  title: string
  messages: Message[]
  steps: Step[]
  todos: Todo[]
  usage: Usage
}

/** 用量摘要（GET /api/sessions/{id}/usage 的 data）。 */
export interface SessionUsage {
  task_id: string
  usage: Usage
  steps: number
  todos: number
  messages: number
}

/** 编队里的一个成员。 */
export interface TeamMember {
  agent_id: string
  role: string
  /** 成员 id 在子 Agent 目录里查到的名字（查不到就是 id 本身）。 */
  name: string
  /** 这个成员是不是一个已知的子 Agent。 */
  known: boolean
}

/** 一个编队。 */
export interface Team {
  id: string
  name: string
  goal: string
  members: TeamMember[]
  member_count: number
  message_count: number
  unread: number
  created_at: number
}

/** 可用的子 Agent。 */
export interface AgentSpec {
  id: string
  name: string
  description: string
  kind: string
  icon: string
  subtitle: string
}

/** GET /api/overview/teams 的 data。 */
export interface OverviewTeams {
  teams: Team[]
  team_count: number
  member_count: number
  agents: AgentSpec[]
  agent_count: number
  /** 降级了的部分（内核没起来时是 ["teams","agents"]）。 */
  degraded: string[]
}

/** 一次运行。 */
export interface Run {
  id: string
  title: string
  goal: string
  status: string
  mode: string
  provider: string
  created_at: number
  updated_at: number
  msg_count: number
  step_count: number
  todo_open: number
  renamed: boolean
}

/** GET /api/overview/runs 的 data。 */
export interface OverviewRuns {
  runs: Run[]
  /** 窗口大小（limit）。 */
  window: number
  /** 会话总数（全量，不是列表长度）。 */
  total: number
  /** 进行中的会话数。 */
  active: number
  /** 今天动过的会话数。 */
  today: number
}

/** 一条主动建议。 */
export interface Suggestion {
  label: string
  /** 需要发起新一轮对话的建议带这个。 */
  prompt?: string
  /** 前端直接执行的动作型建议带这个（如预览文件）。 */
  action?: string
  path: string
  kind: string
}

/** GET /api/overview/suggestions 的 data。 */
export interface OverviewSuggestions {
  suggestions: Suggestion[]
  count: number
  source: {
    task_id?: string
    title?: string
    status?: string
    steps?: number
    artifacts?: string[]
    artifact_count?: number
  }
}

/** 首屏状态（GET /api/state）。 */
export interface AppState {
  mode?: string
  interaction?: string
  style?: string
  expert?: string
  provider?: string
  model?: string
  provider_is_local?: boolean
  busy?: boolean
  skills?: string[]
  experts?: Array<{ id: string; name: string }>
  /** 内核起没起来。false 时那些走 RPC 的接口会 503。 */
  kernel_ready?: boolean
}
