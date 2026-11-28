/**
 * 总览面板纯函数（模块 8）。
 *
 * 两个真缺口：
 *   · 「执行」按钮原来只在 `action` 型建议上出现，还完全没绑事件 ——
 *     而绝大多数建议是 `prompt` 型（要发起新一轮对话），界面根本没得点。
 *   · 总览只在挂载时拉一次，跑完不刷新。这里把「哪些 SSE 事件该触发刷新」
 *     与节流规则抽出来，组件只管订阅。
 */
import type { OverviewRuns, OverviewTeams, Suggestion } from '@/lib/types'

export const RUN_STATUS_CN: Record<string, string> = {
  running: '进行中',
  done: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

export type SuggestionAction =
  | { kind: 'preview'; url: string; label: string }
  | { kind: 'prompt'; prompt: string; label: string }
  | { kind: 'none'; label: string }

export const PREVIEW_BASE = '/api/files/preview'

export function previewUrl(path: string): string | null {
  const cleaned = String(path ?? '').trim()
  if (!cleaned) return null
  return `${PREVIEW_BASE}?path=${encodeURIComponent(cleaned)}`
}

/**
 * 把一条建议变成可执行动作。
 *
 * `action=preview` 且带路径 → 直接开预览页；
 * 其余一律走 `prompt` → 带到「新建运行」页预填，由用户确认后再提交
 * （不自动发起一轮，避免误触产生昂贵的运行）。
 */
export function suggestionAction(s: Suggestion | null | undefined): SuggestionAction {
  const label = String(s?.label ?? '')
  if (s?.action === 'preview') {
    const url = previewUrl(String(s?.path ?? ''))
    return url ? { kind: 'preview', url, label } : { kind: 'none', label }
  }
  const prompt = String(s?.prompt ?? '').trim()
  return prompt ? { kind: 'prompt', prompt, label } : { kind: 'none', label }
}

export function actionButtonLabel(action: SuggestionAction): string {
  if (action.kind === 'preview') return '预览'
  if (action.kind === 'prompt') return '用这条新建运行'
  return ''
}

export function hasExecutableAction(action: SuggestionAction): boolean {
  return action.kind === 'preview' || action.kind === 'prompt'
}

// ── 自动刷新 ───────────────────────────────────────────────────────

export const REFRESH_THROTTLE_MS = 400

/** 哪些事件值得重拉三个总览接口。控制帧与其它域的噪音一律忽略。 */
export function shouldRefresh(type: unknown): boolean {
  const value = String(type ?? '')
  if (value === 'resync') return true // 续不上断点，整份重拿
  if (value === 'ready' || value === '') return false
  if (value.startsWith('RUN_')) return true
  if (value.startsWith('step.')) return true
  if (value.startsWith('team.')) return true
  if (value === 'knowledge.document') return true
  return false
}

/** 距上次刷新不足一节流窗口时，还要等多久。 */
export function throttleDelay(elapsedMs: number): number {
  return Math.max(0, REFRESH_THROTTLE_MS - Math.max(0, Number.isFinite(elapsedMs) ? elapsedMs : 0))
}

export function timeAgo(ts: number, now = Date.now()): string {
  const secs = Math.max(0, Math.round(now / 1000 - ts))
  if (secs < 60) return `${secs} 秒前`
  if (secs < 3600) return `${Math.round(secs / 60)} 分钟前`
  if (secs < 86400) return `${Math.round(secs / 3600)} 小时前`
  return `${Math.round(secs / 86400)} 天前`
}

export function statCards(runs: OverviewRuns | null, teams: OverviewTeams | null) {
  return [
    { label: '最近运行', value: runs?.runs.length ?? 0, hint: `共 ${runs?.total ?? 0} 轮` },
    { label: '进行中', value: runs?.active ?? 0, hint: '此刻在跑' },
    { label: '今天动过', value: runs?.today ?? 0, hint: '今天改过的会话' },
    {
      label: '编队',
      value: teams?.team_count ?? 0,
      hint: `${teams?.member_count ?? 0} 个成员`,
    },
  ]
}
