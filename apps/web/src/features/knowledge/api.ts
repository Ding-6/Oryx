import { api } from '@/lib/api'

export type KnowledgeCollection = {
  id: string
  name: string
  description: string
  visibility: 'private' | 'shared'
  owner_id: string | null
  created_at: number
  updated_at: number
  document_count: number
  total_bytes: number
  can_edit: boolean
}

export type KnowledgeDocument = {
  id: string
  collection_id: string
  original_name: string
  extension: string
  size_bytes: number
  sha256: string
  version: number
  status: 'queued' | 'processing' | 'ready' | 'failed'
  note: string
  created_at: number
  updated_at: number
  parsed_at: number | null
  can_edit: boolean
}

export type KnowledgeHit = {
  document_id: string
  collection_id: string
  collection_name: string
  document_name: string
  version: number
  chunk_number: number
  start_offset: number
  end_offset: number
  snippet: string
}

export async function listCollections() {
  return (await api.get<{ collections: KnowledgeCollection[] }>('/api/knowledge/collections')).collections
}

export async function listDocuments(collectionId: string) {
  return (await api.get<{ documents: KnowledgeDocument[] }>(
    `/api/knowledge/collections/${encodeURIComponent(collectionId)}/documents`,
  )).documents
}

export function createCollection(payload: Pick<KnowledgeCollection, 'name' | 'description' | 'visibility'>) {
  return api.post<KnowledgeCollection>('/api/knowledge/collections', payload)
}

export function updateCollection(id: string, payload: Partial<Pick<KnowledgeCollection, 'name' | 'description' | 'visibility'>>) {
  return api.put<KnowledgeCollection>(`/api/knowledge/collections/${encodeURIComponent(id)}`, payload)
}

export function deleteCollection(id: string) {
  return api.del(`/api/knowledge/collections/${encodeURIComponent(id)}`)
}

export function uploadDocuments(collectionId: string, files: File[]) {
  const form = new FormData()
  files.forEach((file) => form.append('files', file, file.name))
  return api.postForm<{ accepted: KnowledgeDocument[]; rejected: { name: string; reason: string }[] }>(
    `/api/knowledge/collections/${encodeURIComponent(collectionId)}/documents`, form,
  )
}

export function rebuildDocument(id: string) {
  return api.post<KnowledgeDocument>(`/api/knowledge/documents/${encodeURIComponent(id)}/rebuild`)
}

export function deleteDocument(id: string) {
  return api.del(`/api/knowledge/documents/${encodeURIComponent(id)}`)
}

export type KnowledgeSearchResult = {
  query: string
  hits: KnowledgeHit[]
  count: number
  /** 本次限定的集合；null = 全部可见集合。 */
  collection_id: string | null
}

/**
 * 检索知识库。``collectionId`` 为空 = 全部可见集合。
 * 后端会先校验集合可见性，别人的私有集合返回 404。
 */
export function searchKnowledge(query: string, collectionId?: string | null) {
  const body: { query: string; collection_id?: string } = { query }
  const scope = String(collectionId ?? '').trim()
  if (scope) body.collection_id = scope
  return api.post<KnowledgeSearchResult>('/api/knowledge/search', body)
}

/** 清空集合内全部文档与索引，保留集合本身（创建者）。 */
export function clearCollection(id: string) {
  return api.post<{ collection_id: string; deleted: number }>(
    `/api/knowledge/collections/${encodeURIComponent(id)}/clear`,
  )
}

/**
 * 清空本机目录索引。这是**裸对象**接口（`/api/index/*` 未套 v2 壳），
 * 请求层会自动兼容；扫描中返回 409。
 */
export function clearLocalIndex() {
  return api.post<{ status: string }>('/api/index/clear')
}

export function getParsedText(id: string, offset = 0, limit = 8000) {
  return api.get<{
    document: KnowledgeDocument & { collection_name: string }
    text: string
    offset: number
    total_chars: number
    has_more: boolean
  }>(`/api/knowledge/documents/${encodeURIComponent(id)}/text?offset=${offset}&limit=${limit}`)
}

export const sourceUrl = (id: string) => `/api/knowledge/documents/${encodeURIComponent(id)}/source`

