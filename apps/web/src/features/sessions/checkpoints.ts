/**
 * 检查点回退的纯函数层 —— 不碰网络，便于就地断言。
 *
 * 两个容易做错的点，都在这里钉住：
 *
 * 1. **检查点认领消息的规则**：检查点打在用户提问**之前**，
 *    `msg_count` = 打点那一刻库里已有的消息数 = 那条提问在
 *    messages 数组里的下标。只数用户消息会错位，回退会退到别的位置。
 *
 * 2. **回退范围不是「一键撤销」**：只退代码会改文件、只退对话会删消息，
 *    破坏性完全不同，必须让用户自己选。
 */

import type { Checkpoint, RewindContent } from '@/lib/types'

/** 与后端 `domains/checkpoint.py` 的 SCOPES 一一对应，顺序也别改。 */
export type RewindScope = 'Code' | 'Conversation' | 'CodeAndConversation'

export interface ScopeOption {
  scope: RewindScope
  label: string
  hint: string
  /** 默认落在「两者都退」上不安全，界面上不预选任何一项。 */
  danger: boolean
}

export const REWIND_SCOPES: ScopeOption[] = [
  {
    scope: 'Code',
    label: '只还原文件',
    hint: '把这一轮之后改过的文件恢复到当时的样子，对话保留',
    danger: false,
  },
  {
    scope: 'Conversation',
    label: '只删除对话',
    hint: '删掉这一轮之后的问答与执行记录，文件保持现状',
    danger: false,
  },
  {
    scope: 'CodeAndConversation',
    label: '文件和对话都回退',
    hint: '文件还原 + 删掉这一轮之后的问答与执行记录',
    danger: true,
  },
]

/**
 * 找出认领某条消息的检查点。
 *
 * `msgIndex` 是**全部消息**里的下标（用户与助手都占位）。
 * 同一个下标有多条检查点时（重试会产生）取 seq 最大的一条 ——
 * 那才是最近一次打下的边界。
 */
export function checkpointForMessage(
  checkpoints: Checkpoint[] | null | undefined,
  msgIndex: number,
): Checkpoint | null {
  if (!Number.isInteger(msgIndex) || msgIndex < 0) return null
  let hit: Checkpoint | null = null
  for (const cp of checkpoints ?? []) {
    if (cp.msg_count !== msgIndex) continue
    if (!hit || cp.seq > hit.seq) hit = cp
  }
  return hit
}

/** 运行中不许回退 —— 这一轮可能正在写文件。 */
export function canRewind(status: string): boolean {
  return String(status) !== 'running'
}

/** 回退会影响多少东西（用在确认弹层里，让人看清代价）。 */
export function rewindImpact(
  checkpoint: Checkpoint | null,
  totalMessages: number,
): { files: number; messages: number } {
  if (!checkpoint) return { files: 0, messages: 0 }
  const keep = Math.max(0, Number(checkpoint.msg_count) || 0)
  return {
    files: Math.max(0, Number(checkpoint.files) || 0),
    messages: Math.max(0, totalMessages - keep),
  }
}

/** 把回退结果翻成人话。空数组表示这次什么都没动。 */
export function summarizeRewind(content: RewindContent | null | undefined): string[] {
  if (!content) return []
  const parts: string[] = []
  const restored = content.restored?.length ?? 0
  const removed = content.removed?.length ?? 0
  const skipped = content.skipped?.length ?? 0
  const errors = content.errors?.length ?? 0
  if (restored) parts.push(`还原 ${restored} 个文件`)
  if (removed) parts.push(`删除 ${removed} 个新建文件`)
  if (content.messages_removed) parts.push(`删掉 ${content.messages_removed} 条消息`)
  if (content.steps_removed) parts.push(`清掉 ${content.steps_removed} 条执行记录`)
  if (skipped) parts.push(`${skipped} 个文件跳过（超出授权范围或内容过大）`)
  if (errors) parts.push(`${errors} 个文件失败`)
  return parts
}

/** 范围的中文名 —— 结果提示与历史记录共用一套说法。 */
export function scopeLabel(scope: string): string {
  return REWIND_SCOPES.find((item) => item.scope === scope)?.label ?? scope
}

/** 检查点时间的短格式（本地时区，精确到分钟）。 */
export function formatCheckpointTime(ts: number | null | undefined): string {
  if (!ts) return '—'
  const date = new Date(ts * 1000)
  if (Number.isNaN(date.getTime())) return '—'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getMonth() + 1}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}
