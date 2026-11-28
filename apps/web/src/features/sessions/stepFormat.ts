/**
 * 执行步骤的展示格式化 —— 纯函数，不依赖 React，便于单独跑断言。
 *
 * 后端落库的 `args` / `result` 都是 `json.dumps()` 后的**字符串**
 * （store.py 的 create_step / finish_step），`error` 才是纯文本。
 * 所以这里统一走 prettyValue() 还原：对象漂亮打印，字符串原样显示，
 * 坏掉的 JSON 不当异常抛，原样给人看。
 */

import type { Step } from '@/lib/types'

/** SSE 事件里带、但历史库或旧内核没落库的增强字段。落库优先，SSE 只补空。 */
export interface StepExtra {
  error_class?: string | null
  artifact_path?: string | null
  mutated?: boolean | number | null
  risk_label?: string | null
  elapsed_ms?: number | null
  /** step.denied 的原因；step.autoclass 的判定与理由。 */
  reason?: string | null
  verdict?: string | null
}

/**
 * 从一条 SSE `step.*` 事件里抽出增强字段。
 *
 * 只保留事件真正带了的键：直接展开一个「其它键全是 undefined」的对象，
 * 会把同一个 step 之前存下的字段（例如上一条事件带来的 error_class）
 * 悄悄覆盖掉。step.created 只带 risk_label，step.done 才带 mutated，
 * 两者按 step_id 累积，缺一不可。
 */
export function stepExtraFromEvent(ev: Record<string, unknown>): StepExtra {
  const extra: StepExtra = {}
  if (typeof ev.error_class === 'string') extra.error_class = ev.error_class
  if (typeof ev.artifact_path === 'string') extra.artifact_path = ev.artifact_path
  if (typeof ev.mutated === 'boolean') extra.mutated = ev.mutated
  if (typeof ev.risk_label === 'string') extra.risk_label = ev.risk_label
  if (typeof ev.elapsed_ms === 'number') extra.elapsed_ms = ev.elapsed_ms
  if (typeof ev.reason === 'string') extra.reason = ev.reason
  if (typeof ev.verdict === 'string') extra.verdict = ev.verdict
  return extra
}

export interface StepView {
  id: string
  seq: number
  tool: string
  state: string
  stateLabel: string
  risk: number | null
  riskLabel: string | null
  argsText: string
  argsSummary: string
  resultText: string
  errorText: string
  errorClass: string | null
  errorHint: string | null
  artifactPath: string | null
  mutated: boolean | null
  duration: string | null
  reason: string | null
  verdict: string | null
  /** 有没有值得展开的内容。 */
  hasDetails: boolean
}

/** 状态取值见 loop.py 的 finish_step 调用：running / done / failed / denied。 */
export const STEP_STATE_CN: Record<string, string> = {
  running: '执行中',
  done: '完成',
  failed: '失败',
  denied: '已拒绝',
  pending: '等待中',
  cancelled: '已取消',
  skipped: '已跳过',
}

/** 与后端 policy.Risk.label 对齐，不另造一套说法。 */
export const RISK_CN: Record<number, string> = {
  0: 'L0 只读',
  1: 'L1 可逆写入',
  2: 'L2 破坏性操作',
  3: 'L3 禁止操作',
}

/** 失败分类的人话说明（沿用老前端 models.js 的 ERR_CLASS_HINT）。 */
export const ERROR_CLASS_HINT: Record<string, string> = {
  E1: '环境不可达 —— 检查网络/服务是否在运行，别重复试',
  E2: '权限不足 —— 需要更高权限，或换用有权限的账号',
  E3: '工具不支持 —— 换个工具或换个做法',
  E4: '结果与预期不符 —— 确认一下需求是不是理解错了',
  E5: '依赖缺失 —— 缺文件/缺包/缺配置，先补齐',
  E0: '失败原因不明 —— 看报错原文，同一操作最多再试一次',
}

export const AUTOCLASS_CN: Record<string, string> = {
  allow: '自动放行',
  ask: '仍需确认',
  deny: '自动拒绝',
}

/** 把落库的字符串还原成人类可读文本。任何异常都退回原值，绝不抛。 */
export function prettyValue(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value !== 'string') {
    try {
      return JSON.stringify(value, null, 2) ?? String(value)
    } catch {
      return String(value)
    }
  }
  if (value === '') return ''
  try {
    const parsed: unknown = JSON.parse(value)
    if (typeof parsed === 'string') return parsed
    if (parsed === null) return ''
    if (typeof parsed === 'object') return JSON.stringify(parsed, null, 2) ?? value
    return String(parsed)
  } catch {
    // 本来就是纯文本（例如 error、老版本直接存的 result）
    return value
  }
}

/** 折叠头上的一行参数摘要：最多 3 个键，长值截短。 */
export function argsSummary(value: unknown): string {
  const text = prettyValue(value)
  if (!text) return '无参数'
  try {
    const parsed: unknown = typeof value === 'string' ? JSON.parse(value) : value
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      const parts = Object.entries(parsed as Record<string, unknown>)
        .slice(0, 3)
        .map(([key, val]) => {
          const raw = typeof val === 'string' ? val : JSON.stringify(val) ?? String(val)
          return `${key}=${truncate(raw, 42)}`
        })
      const extra = Object.keys(parsed as Record<string, unknown>).length - 3
      return truncate(parts.join(' · ') + (extra > 0 ? ` · 还有 ${extra} 项` : ''), 110)
    }
  } catch {
    /* 走下面的纯文本兜底 */
  }
  return truncate(text.replace(/\s+/g, ' '), 110)
}

export function truncate(text: string, max: number): string {
  return text.length > max ? text.slice(0, max) + '…' : text
}

export function formatMs(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return ''
  if (ms < 1000) return `${Math.round(ms)} ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)} s`
  return `${(ms / 60000).toFixed(1)} min`
}

export function formatDuration(
  startedAt: number | null,
  endedAt: number | null,
  fallbackMs?: number | null,
): string | null {
  if (
    typeof startedAt === 'number' && typeof endedAt === 'number' &&
    Number.isFinite(startedAt) && Number.isFinite(endedAt) && endedAt >= startedAt
  ) {
    return formatMs((endedAt - startedAt) * 1000)
  }
  if (typeof fallbackMs === 'number') return formatMs(fallbackMs)
  return null
}

/**
 * 数据库行 + SSE 增强字段 → 视图模型。
 *
 * 落库字段**优先**：state / args / result / error 一律以 GET 详情为准，
 * SSE 事件只负责补「老库没有、或事件里才有」的字段，避免补发的旧事件
 * 把已经完成的步骤改回「执行中」。
 */
export function buildStepView(step: Step, extra?: StepExtra): StepView {
  const state = step.state ?? 'pending'
  const argsText = prettyValue(step.args)
  const resultText = prettyValue(step.result)
  const errorText = step.error ?? ''
  const errorClass = step.error_class || extra?.error_class || null
  const artifactPath = step.artifact_path || extra?.artifact_path || null
  const mutated = step.mutated != null
    ? Boolean(step.mutated)
    : extra?.mutated != null ? Boolean(extra.mutated) : null
  const duration = formatDuration(step.started_at, step.ended_at, extra?.elapsed_ms)
  const reason = extra?.reason ?? null
  const verdict = extra?.verdict ?? null

  return {
    id: step.id,
    seq: step.seq,
    tool: step.tool || '（未记录工具）',
    state,
    stateLabel: STEP_STATE_CN[state] ?? state,
    risk: step.risk,
    riskLabel: extra?.risk_label || (step.risk != null ? RISK_CN[step.risk] ?? `L${step.risk}` : null),
    argsText,
    argsSummary: argsSummary(step.args),
    resultText,
    errorText,
    errorClass,
    errorHint: errorClass ? ERROR_CLASS_HINT[errorClass] ?? null : null,
    artifactPath,
    mutated,
    duration,
    reason,
    verdict,
    hasDetails: Boolean(
      argsText || resultText || errorText || artifactPath ||
      errorClass || mutated !== null || reason || verdict,
    ),
  }
}
