/**
 * 知识库面板纯函数（模块 8）。
 *
 * 两处真缺口钉在这里：
 *   · 检索范围必须显式写进请求体，空范围 = 全部可见集合（后端按 null 处理）；
 *   · 解析进度不能永远 1.5s 死轮询 —— 退避、页面隐藏时暂停、超时收口。
 * 组件只负责把它们接起来，规则集中在这里，便于零依赖回归。
 */
import type { KnowledgeCollection, KnowledgeDocument } from './api'

/** 空串 = 检索全部可见集合。 */
export const ALL_SCOPES = ''

export type ScopeOption = {
  value: string
  label: string
  visibility: 'private' | 'shared'
}

export function scopeOptions(collections: KnowledgeCollection[]): ScopeOption[] {
  return [
    { value: ALL_SCOPES, label: '全部可见集合', visibility: 'private' },
    ...collections.map((item) => ({
      value: item.id,
      label: item.visibility === 'shared' ? `${item.name}（共享）` : item.name,
      visibility: item.visibility,
    })),
  ]
}

export function normalizeScope(scope: string | null | undefined): string {
  return String(scope ?? '').trim()
}

export function scopeLabel(collections: KnowledgeCollection[], scope: string | null | undefined): string {
  const value = normalizeScope(scope)
  if (!value) return '全部可见集合'
  return collections.find((item) => item.id === value)?.name ?? '已失效的集合'
}

/** 只带改动范围 —— 空范围不带 `collection_id` 这个键，避免后端收到空串。 */
export function buildSearchBody(query: string, scope: string | null | undefined): { query: string; collection_id?: string } {
  const cleaned = String(query ?? '').trim()
  const value = normalizeScope(scope)
  return value ? { query: cleaned, collection_id: value } : { query: cleaned }
}

export function scopeIsStale(collections: KnowledgeCollection[], scope: string | null | undefined): boolean {
  const value = normalizeScope(scope)
  return Boolean(value) && !collections.some((item) => item.id === value)
}

// ── 解析进度轮询 ────────────────────────────────────────────────────

export const POLL_START_MS = 1500
export const POLL_MAX_MS = 8000
/** 3 分钟后收口：卡住的解析不该让页面永远发请求。 */
export const POLL_LIMIT_MS = 180_000
export const POLL_GROWTH = 1.6

export function nextPollDelay(delay: number): number {
  if (!Number.isFinite(delay) || delay <= 0) return POLL_START_MS
  return Math.min(POLL_MAX_MS, Math.round(delay * POLL_GROWTH))
}

export function shouldPoll(hasProcessing: boolean, visible: boolean, elapsedMs: number): boolean {
  return hasProcessing && visible && elapsedMs < POLL_LIMIT_MS
}

export function pollHint(elapsedMs: number): string | null {
  return elapsedMs >= POLL_LIMIT_MS
    ? '解析时间较长，已停止自动刷新；可点「刷新」查看最新状态'
    : null
}

export function processingSummary(docs: KnowledgeDocument[]) {
  const count = (status: KnowledgeDocument['status']) =>
    docs.filter((doc) => doc.status === status).length
  const processing = docs.filter(
    (doc) => doc.status === 'queued' || doc.status === 'processing',
  ).length
  return {
    total: docs.length,
    ready: count('ready'),
    failed: count('failed'),
    processing,
    label: processing > 0 ? `解析中 ${processing} 篇` : `${count('ready')} 篇可检索`,
  }
}

// ── SSE：解析完成事件 ───────────────────────────────────────────────

export function isDocumentEvent(ev: Record<string, unknown> | null | undefined): boolean {
  return String(ev?.type ?? '') === 'knowledge.document'
}

/** 只认当前集合的事件，别的集合解析完不该打断用户正在看的列表。 */
export function eventMatchesCollection(
  ev: Record<string, unknown> | null | undefined,
  selectedId: string | null,
): boolean {
  return Boolean(selectedId) && String(ev?.collection_id ?? '') === String(selectedId)
}

// ── 清空确认文案 ───────────────────────────────────────────────────

export function clearCollectionConfirm(name: string, count: number): string {
  return count > 0
    ? `清空「${name}」里的 ${count} 篇文档及其索引？集合本身会保留，此操作不可撤销。`
    : `「${name}」里还没有文档，确定要清空吗？`
}

export function clearIndexConfirm(files: unknown, chunks: unknown): string {
  const f = typeof files === 'number' ? files : 0
  const c = typeof chunks === 'number' ? chunks : 0
  return `清空本机目录索引（已索引 ${f} 个文件、${c} 个片段）？只删索引，不删磁盘上的原文件；下次扫描会重建。`
}

export function clearIndexError(status: number): string {
  if (status === 409) return '正在扫描目录，等扫描结束后再清空索引'
  if (status === 503) return '内核还没就绪，暂时无法清空索引'
  return '清空索引失败，请稍后重试'
}
