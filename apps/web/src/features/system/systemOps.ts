/**
 * 「系统」页纯函数 —— 用量 / 环境自检 / 审计导出。
 *
 * 取证时确认的三件事，直接决定了这里的取舍：
 *
 *  1. `/api/usage` 的 `today` 是**过去 24 小时**、`total` 是 `window_hours`
 *     小时，都不是自然日 / 自然月。老前端踩过「标成『本月』结果月初对不上
 *     账单」的坑，所以标签一律按滚动窗口如实写。
 *  2. 后端 `hours` 会 clamp 到 1–8760，前端用同一套边界，不把用户选的值
 *     悄悄改掉；`window_hours` 由后端回显，展示以回显值为准。
 *  3. 「导出审计 / 对话 / 执行计划」老前端是**前端本地生成文件**，
 *     没有、也不需要后端导出接口。这里只产出 `{filename, content, mime}`，
 *     真正触发下载交给 UI —— 纯函数不碰 DOM。
 *
 * 「请求日志」后端只有一行 Python logging（写服务端日志），没有持久化与
 * 查询接口，所以本模块**不做**它，界面里如实说明。
 */

export interface UsageBucket {
  calls: number
  input_tokens: number
  output_tokens: number
}

export interface UsageModelRow {
  provider: string
  model: string
  calls: number
  inp: number
  outp: number
}

export interface UsageSummary {
  window_hours: number
  total: UsageBucket
  today: UsageBucket
  by_model: UsageModelRow[]
}

export interface DoctorCheck {
  name: string
  ok: boolean
  detail: string
}

export interface DoctorDependency {
  installed?: boolean
  purpose?: string
}

export interface DoctorReport {
  version: string
  python: string
  platform: string
  mode?: string
  network_note?: string
  active_provider?: string
  local_provider?: string
  providers?: Record<string, unknown>
  dependencies?: Record<string, DoctorDependency>
  data?: Record<string, unknown>
  checks: DoctorCheck[]
  hints: string[]
}

export interface AuditRow {
  id: string
  ts: number
  task_id: string
  step_id: string
  tool: string
  args: string
  risk: number
  decision: string
  outcome: string
  mutated?: number | null
}

export interface AuditPayload {
  task_id: string
  audit: AuditRow[]
}

export interface ExportFile {
  filename: string
  content: string
  mime: string
}

export type ExportKind = 'audit' | 'conversation' | 'plan'

export interface ExportContext {
  taskId: string
  title?: string
  audit?: AuditRow[]
  messages?: Array<{ role: string; content: string }>
  steps?: Array<{ tool: string | null; args: string | null; state: string | null }>
}

/** 可选的统计窗口。与后端 clamp 上限一致（8760 小时 = 365 天）。 */
export const USAGE_WINDOWS = [
  { hours: 24, label: '近 24 小时' },
  { hours: 168, label: '近 7 天' },
  { hours: 720, label: '近 30 天' },
  { hours: 2160, label: '近 90 天' },
] as const

export const USAGE_HOURS_MIN = 1
export const USAGE_HOURS_MAX = 8760

export const AUDIT_RISK_CN = ['只读', '写入', '危险', '禁止'] as const

export const EXPORT_KINDS: Array<{ kind: ExportKind; label: string; hint: string }> = [
  { kind: 'audit', label: '导出审计', hint: '工具调用与审批记录（JSON）' },
  { kind: 'conversation', label: '导出对话', hint: '按发言顺序的 Markdown' },
  { kind: 'plan', label: '导出执行计划', hint: '每一步的工具与状态（Markdown）' },
]

/** 与后端同一套 clamp：负数 / NaN 归到 1，超大值收到 8760。 */
export function clampUsageHours(hours: unknown): number {
  const n = typeof hours === 'number' ? hours : Number(hours)
  if (!Number.isFinite(n)) return USAGE_HOURS_MIN
  return Math.max(USAGE_HOURS_MIN, Math.min(Math.round(n), USAGE_HOURS_MAX))
}

/** 窗口标签：满 24 小时按天说，零头按小时说，不冒充自然日 / 自然月。 */
export function usageWindowLabel(hours: number): string {
  const h = clampUsageHours(hours)
  if (h >= 24 && h % 24 === 0) return `近 ${h / 24} 天`
  return `近 ${h} 小时`
}

/** Token 数字：大数缩写，但绝不把 0 / 空值显示成 NaN 或 undefined。 */
export function formatTokens(value: unknown): string {
  const n = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(n) || n <= 0) return '0'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(n)
}

export interface UsageCard {
  key: string
  label: string
  value: string
  hint: string
}

/** 四张概览卡。hint 明确写「滚动」，避免和账单的自然月混淆。 */
export function usageCards(summary: UsageSummary | null): UsageCard[] {
  if (!summary) {
    return [
      { key: 'today-calls', label: '近 24 小时调用', value: '—', hint: '过去 24 小时（滚动）' },
      { key: 'today-tokens', label: '近 24 小时 Token', value: '—', hint: '输入 + 输出' },
      { key: 'window-calls', label: '窗口内调用', value: '—', hint: '等待用量数据' },
      { key: 'window-tokens', label: '窗口内 Token', value: '—', hint: '等待用量数据' },
    ]
  }
  const hours = clampUsageHours(summary.window_hours)
  const windowText = usageWindowLabel(hours)
  const todayTokens = (summary.today?.input_tokens ?? 0) + (summary.today?.output_tokens ?? 0)
  const allTokens = (summary.total?.input_tokens ?? 0) + (summary.total?.output_tokens ?? 0)
  return [
    {
      key: 'today-calls', label: '近 24 小时调用',
      value: String(summary.today?.calls ?? 0), hint: '过去 24 小时（滚动，不是自然日）',
    },
    {
      key: 'today-tokens', label: '近 24 小时 Token',
      value: formatTokens(todayTokens), hint: '输入 + 输出',
    },
    {
      key: 'window-calls', label: `${windowText}调用`,
      value: String(summary.total?.calls ?? 0), hint: '滚动窗口，非自然月',
    },
    {
      key: 'window-tokens', label: `${windowText}Token`,
      value: formatTokens(allTokens), hint: `窗口 ${hours} 小时，由后端回显`,
    },
  ]
}

/** 按模型的行：空 / 坏数据都要能渲染成空表，不能抛。 */
export function modelRows(summary: UsageSummary | null): UsageModelRow[] {
  const rows = summary?.by_model
  return Array.isArray(rows) ? rows : []
}

export interface DoctorStatus {
  okCount: number
  badCount: number
  level: 'ok' | 'warn'
  text: string
  providerReady: boolean
}

export function doctorStatus(report: DoctorReport | null): DoctorStatus {
  const raw = report?.checks
  const checks: DoctorCheck[] = Array.isArray(raw) ? raw : []
  const okCount = checks.filter((c) => Boolean(c?.ok)).length
  const badCount = checks.length - okCount
  const providerReady = Boolean(report?.active_provider)
  return {
    okCount,
    badCount,
    level: badCount > 0 ? 'warn' : 'ok',
    text: checks.length === 0
      ? '没有自检项'
      : `${okCount} 项正常${badCount > 0 ? ` · ${badCount} 项需要注意` : ''}`,
    providerReady,
  }
}

/** 自检项渲染成「正常 / 注意」两档。detail 缺失给占位，不显示 undefined。 */
export function doctorCheckTone(check: DoctorCheck): 'ok' | 'warn' {
  return check?.ok ? 'ok' : 'warn'
}

export function doctorCheckDetail(check: DoctorCheck): string {
  return (check?.detail || '').trim() || '没有更多说明'
}

export interface DependencyRow {
  name: string
  installed: boolean
  purpose: string
}

export function dependencyRows(report: DoctorReport | null): DependencyRow[] {
  const deps = report?.dependencies
  if (!deps || typeof deps !== 'object') return []
  return Object.entries(deps).map(([name, value]) => ({
    name,
    installed: Boolean(value?.installed),
    purpose: (value?.purpose || '').trim() || name,
  }))
}

/** audit.risk 是 L0-L3，越界给「未知」而不是数组越界。 */
export function auditRiskLabel(risk: unknown): string {
  const n = Number(risk)
  if (!Number.isInteger(n) || n < 0 || n >= AUDIT_RISK_CN.length) return '未知'
  return AUDIT_RISK_CN[n]
}

/** 后端 ts 是秒，不是毫秒（与会话列表同一个坑）。 */
export function formatAuditTime(ts: unknown): string {
  const n = Number(ts)
  if (!Number.isFinite(n) || n <= 0) return '—'
  try {
    return new Date(n * 1000).toLocaleString('zh-CN', { hour12: false })
  } catch {
    return '—'
  }
}

/** 文件名只留安全字符 —— task_id 会进下载名，不能让用户数据带路径分隔符。 */
export function safeFilePart(value: unknown): string {
  const raw = String(value ?? '').trim()
  const cleaned = raw.replace(/[^A-Za-z0-9_-]/g, '_').slice(0, 48)
  return cleaned || 'session'
}

export function buildAuditExport(taskId: string, audit: AuditRow[] | undefined): ExportFile {
  const rows = Array.isArray(audit) ? audit : []
  return {
    filename: `audit-${safeFilePart(taskId)}.json`,
    content: JSON.stringify(rows, null, 2),
    mime: 'application/json;charset=utf-8',
  }
}

export function buildConversationExport(
  taskId: string,
  messages: ExportContext['messages'] | undefined,
): ExportFile {
  const lines = (Array.isArray(messages) ? messages : []).map((m) => {
    const who = m?.role === 'user' ? '我' : 'Oryx'
    return `## ${who}\n\n${String(m?.content ?? '')}`
  })
  return {
    filename: `conversation-${safeFilePart(taskId)}.md`,
    content: lines.join('\n\n---\n\n') || '# 空对话',
    mime: 'text/markdown;charset=utf-8',
  }
}

export function buildPlanExport(
  taskId: string,
  steps: ExportContext['steps'] | undefined,
): ExportFile {
  const lines = (Array.isArray(steps) ? steps : []).map((s) => {
    const args = String(s?.args ?? '').slice(0, 200)
    return `- [${String(s?.state ?? '')}] ${String(s?.tool ?? '')}${args ? ` ${args}` : ''}`
  })
  return {
    filename: `steps-${safeFilePart(taskId)}.md`,
    content: `# 执行记录 ${safeFilePart(taskId)}\n\n${lines.join('\n') || '- 无'}`,
    mime: 'text/markdown;charset=utf-8',
  }
}

/** 统一分发：三种导出都只依赖已经拿到的数据，不再发请求。 */
export function buildExport(kind: ExportKind, ctx: ExportContext): ExportFile | null {
  if (!ctx?.taskId) return null
  if (kind === 'audit') return buildAuditExport(ctx.taskId, ctx.audit)
  if (kind === 'conversation') return buildConversationExport(ctx.taskId, ctx.messages)
  if (kind === 'plan') return buildPlanExport(ctx.taskId, ctx.steps)
  return null
}
