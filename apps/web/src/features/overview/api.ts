/**
 * 大屏总览的前端接入。
 *
 * 三个接口是**服务端真相** —— 以前大屏纯渲染 SSE 攒出来的内存状态，
 * 刷新一下页面就空了。现在有了接口，页面打开或刷新时先拉一份底，
 * 之后由 SSE 增量覆盖。
 *
 * 两边不冲突：SSE 说「现在」，接口说「本来」。
 */

import { api } from '@/lib/api'
import type { OverviewRuns, OverviewSuggestions, OverviewTeams } from '@/lib/types'

/** 编队总览：团队、成员、可用的子 Agent。 */
export function fetchOverviewTeams(): Promise<OverviewTeams> {
  return api.get<OverviewTeams>('/api/overview/teams')
}

/** 运行总览：最近运行 + 整体计数。 */
export function fetchOverviewRuns(limit = 20): Promise<OverviewRuns> {
  return api.get<OverviewRuns>(`/api/overview/runs?limit=${limit}`)
}

/** 主动建议：上一轮产出之后下一步做什么。 */
export function fetchOverviewSuggestions(taskId?: string): Promise<OverviewSuggestions> {
  const q = taskId ? `?task_id=${encodeURIComponent(taskId)}` : ''
  return api.get<OverviewSuggestions>(`/api/overview/suggestions${q}`)
}
