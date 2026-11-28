/**
 * 知识库面板。
 *
 * 模块 8 接了四件事：
 *   · 检索可限定单个集合（范围显式进请求体，后端先校验可见性）；
 *   · 解析进度改成 SSE 立即刷新 + 退避轮询兜底（页面隐藏暂停、3 分钟收口）；
 *   · 集合可「清空文档」但保留集合本身；
 *   · 本地目录索引可清空（后端 /api/index/clear 早就存在，只是没入口）。
 */
import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'

import { Button } from '@/components/ui/button'
import { api, ApiError, subscribe } from '@/lib/api'
import {
  clearCollection,
  clearLocalIndex,
  createCollection,
  deleteCollection,
  deleteDocument,
  getParsedText,
  listCollections,
  listDocuments,
  rebuildDocument,
  searchKnowledge,
  sourceUrl,
  updateCollection,
  uploadDocuments,
  type KnowledgeCollection,
  type KnowledgeDocument,
  type KnowledgeHit,
} from './api'
import {
  ALL_SCOPES,
  clearCollectionConfirm,
  clearIndexConfirm,
  clearIndexError,
  eventMatchesCollection,
  isDocumentEvent,
  nextPollDelay,
  POLL_START_MS,
  pollHint,
  processingSummary,
  scopeIsStale,
  scopeLabel,
  scopeOptions,
  shouldPoll,
} from './kbOps'

type Section = 'collections' | 'local-index'
type CollectionDraft = { name: string; description: string; visibility: 'private' | 'shared' }
type TextPreview = { documentId: string; name: string; text: string; offset: number; total: number; more: boolean }

const EMPTY_DRAFT: CollectionDraft = { name: '', description: '', visibility: 'private' }
const ACCEPTED = '.txt,.md,.markdown,.pdf,.docx,.xlsx,.pptx,.png,.jpg,.jpeg,.bmp,.webp,.tif,.tiff'

function formatBytes(size: number) {
  if (size < 1024) return `${size} B`
  if (size < 1024 ** 2) return `${(size / 1024).toFixed(1)} KB`
  if (size < 1024 ** 3) return `${(size / 1024 ** 2).toFixed(1)} MB`
  return `${(size / 1024 ** 3).toFixed(2)} GB`
}

function formatDate(value: number) {
  return new Date(value * 1000).toLocaleString()
}

function statusLabel(status: KnowledgeDocument['status']) {
  if (status === 'ready') return '已就绪'
  if (status === 'processing') return '解析中'
  if (status === 'queued') return '等待解析'
  return '解析失败'
}

export default function KnowledgeBase() {
  const [section, setSection] = useState<Section>('collections')
  const [collections, setCollections] = useState<KnowledgeCollection[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([])
  const [loadingCollections, setLoadingCollections] = useState(true)
  const [loadingDocuments, setLoadingDocuments] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [modalOpen, setModalOpen] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [draft, setDraft] = useState<CollectionDraft>(EMPTY_DRAFT)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchScope, setSearchScope] = useState<string>(ALL_SCOPES)
  const [searching, setSearching] = useState(false)
  const [didSearch, setDidSearch] = useState(false)
  const [hits, setHits] = useState<KnowledgeHit[]>([])
  const [preview, setPreview] = useState<TextPreview | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [refreshKey, setRefreshKey] = useState(0)
  const [indexStats, setIndexStats] = useState<Record<string, unknown> | null>(null)
  const [indexError, setIndexError] = useState<string | null>(null)
  const [indexSearchQuery, setIndexSearchQuery] = useState('')
  const [indexHits, setIndexHits] = useState<Record<string, unknown>[]>([])
  const [indexSearching, setIndexSearching] = useState(false)
  const [clearingCollectionId, setClearingCollectionId] = useState<string | null>(null)
  const [clearingIndex, setClearingIndex] = useState(false)
  const [sseBump, setSseBump] = useState(0)
  const [pollHintText, setPollHintText] = useState<string | null>(null)

  const selected = useMemo(() => collections.find((item) => item.id === selectedId) ?? null, [collections, selectedId])
  const summary = useMemo(() => processingSummary(documents), [documents])
  const processing = summary.processing > 0
  const readyCount = summary.ready

  const refreshCollections = useCallback(async (preferredId?: string) => {
    setLoadingCollections(true)
    try {
      const next = await listCollections()
      setCollections(next)
      // 限定的集合被别人删了 / 改成不可见了，退回全部，别留一个查不到的范围。
      setSearchScope((current) => (scopeIsStale(next, current) ? ALL_SCOPES : current))
      setSelectedId((current) => {
        if (preferredId && next.some((item) => item.id === preferredId)) return preferredId
        if (current && next.some((item) => item.id === current)) return current
        return next[0]?.id ?? null
      })
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '知识库集合加载失败')
    } finally {
      setLoadingCollections(false)
    }
  }, [])

  /** silent=true 用于后台轮询，不闪「正在加载文档」。 */
  const refreshDocuments = useCallback(async (silent = false) => {
    if (!selectedId) {
      setDocuments([])
      return
    }
    if (!silent) setLoadingDocuments(true)
    try {
      setDocuments(await listDocuments(selectedId))
    } catch (cause) {
      if (!silent) setError(cause instanceof Error ? cause.message : '文档列表加载失败')
    } finally {
      if (!silent) setLoadingDocuments(false)
    }
  }, [selectedId])

  useEffect(() => { void refreshCollections() }, [refreshCollections])
  useEffect(() => { void refreshDocuments() }, [refreshDocuments, refreshKey])

  // 解析完成 / 失败会推一条 SSE：立刻刷新，不必干等轮询。
  // 事件带 _owner_id，服务端只发给本人；共享集合的其它账号仍靠下面的兜底轮询。
  useEffect(() => {
    const stop = subscribe('/api/events', (ev) => {
      if (isDocumentEvent(ev) && eventMatchesCollection(ev, selectedId)) {
        void refreshDocuments(true)
        setSseBump((value) => value + 1) // 有动静 → 退避重新从 1.5s 开始
      }
    })
    return stop
  }, [selectedId, refreshDocuments])

  // 兜底轮询：退避到 8s、页面隐藏时暂停、累计 3 分钟收口。
  // 依赖里刻意不放 documents —— 每次刷新都是新数组，放进去会把 timer 无限重置。
  useEffect(() => {
    setPollHintText(null)
    if (!selectedId || !processing) return
    let cancelled = false
    let timer: number | undefined
    let delay = POLL_START_MS
    const startedAt = Date.now()

    const tick = async () => {
      if (cancelled) return
      const elapsed = Date.now() - startedAt
      const hint = pollHint(elapsed)
      if (hint) {
        setPollHintText(hint)
        return // 超时收口，交给用户手动刷新
      }
      // 页面隐藏时**不发请求但继续排 timer** —— 切回来后下一拍自动恢复，
      // 不能在这里 return，否则轮询一次就永久停了。
      if (!document.hidden && shouldPoll(processing, true, elapsed)) {
        await refreshDocuments(true)
        delay = nextPollDelay(delay)
      }
      if (cancelled) return
      timer = window.setTimeout(() => { void tick() }, delay)
    }

    timer = window.setTimeout(() => { void tick() }, POLL_START_MS)
    return () => {
      cancelled = true
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [processing, selectedId, refreshDocuments, sseBump])

  const refreshIndexStatus = useCallback(async () => {
    try {
      setIndexStats(await api.get<Record<string, unknown>>('/api/index/status'))
      setIndexError(null)
    } catch (cause) {
      setIndexError(cause instanceof ApiError ? cause.message : '本地索引状态暂不可用')
    }
  }, [])

  useEffect(() => {
    if (section !== 'local-index') return
    void refreshIndexStatus()
  }, [section, refreshIndexStatus])

  const indexScanning = Boolean(indexStats?.scanning)
  useEffect(() => {
    if (!indexScanning || section !== 'local-index') return
    const timer = window.setTimeout(() => { void refreshIndexStatus() }, 1800)
    return () => window.clearTimeout(timer)
  }, [indexScanning, section, refreshIndexStatus, indexStats])

  const openCreate = () => {
    setEditingId(null)
    setDraft(EMPTY_DRAFT)
    setError(null)
    setModalOpen(true)
  }

  const openEdit = (collection: KnowledgeCollection) => {
    setEditingId(collection.id)
    setDraft({ name: collection.name, description: collection.description, visibility: collection.visibility })
    setError(null)
    setModalOpen(true)
  }

  const saveCollection = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setSaving(true)
    setError(null)
    setNotice(null)
    try {
      const saved = editingId
        ? await updateCollection(editingId, draft)
        : await createCollection(draft)
      setModalOpen(false)
      setNotice(editingId ? '集合设置已保存' : '集合已创建')
      await refreshCollections(saved.id)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '集合保存失败')
    } finally {
      setSaving(false)
    }
  }

  const removeCollection = async (collection: KnowledgeCollection) => {
    if (!window.confirm(`删除“${collection.name}”及其中的全部文档？此操作不可撤销。`)) return
    setSaving(true)
    setError(null)
    try {
      await deleteCollection(collection.id)
      setNotice('集合和其中的文档已删除')
      if (selectedId === collection.id) setSelectedId(null)
      await refreshCollections()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '集合删除失败')
    } finally {
      setSaving(false)
    }
  }

  const clearDocs = async (collection: KnowledgeCollection) => {
    if (!window.confirm(clearCollectionConfirm(collection.name, collection.document_count))) return
    setClearingCollectionId(collection.id)
    setError(null)
    setNotice(null)
    try {
      const result = await clearCollection(collection.id)
      setNotice(`已清空「${collection.name}」里的 ${result.deleted} 篇文档，集合本身保留`)
      setPreview(null)
      await refreshDocuments()
      await refreshCollections(collection.id)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '清空集合文档失败')
    } finally {
      setClearingCollectionId(null)
    }
  }

  const upload = async (files: File[]) => {
    if (!selected || !files.length) return
    setSaving(true)
    setError(null)
    setNotice(null)
    try {
      const result = await uploadDocuments(selected.id, files)
      const lines = [
        result.accepted.length ? `已接收 ${result.accepted.length} 个文件，后台开始解析` : '',
        ...result.rejected.map((item) => `${item.name}：${item.reason}`),
      ].filter(Boolean)
      if (result.rejected.length && !result.accepted.length) setError(lines.join('；'))
      else setNotice(lines.join('；'))
      setRefreshKey((key) => key + 1)
      await refreshCollections(selected.id)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '文件上传失败')
    } finally {
      setSaving(false)
    }
  }

  const reparse = async (doc: KnowledgeDocument) => {
    setSaving(true)
    setError(null)
    try {
      await rebuildDocument(doc.id)
      setNotice(`已开始重建“${doc.original_name}”的索引`)
      setRefreshKey((key) => key + 1)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '文档重建失败')
    } finally {
      setSaving(false)
    }
  }

  const removeDocument = async (doc: KnowledgeDocument) => {
    if (!window.confirm(`删除“${doc.original_name}”第 ${doc.version} 版及其索引？`)) return
    setSaving(true)
    setError(null)
    try {
      await deleteDocument(doc.id)
      setNotice('文档版本已删除')
      if (preview?.documentId === doc.id) setPreview(null)
      await refreshDocuments()
      await refreshCollections(selectedId ?? undefined)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '文档删除失败')
    } finally {
      setSaving(false)
    }
  }

  const runSearch = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!searchQuery.trim()) return
    setSearching(true)
    setError(null)
    setDidSearch(true)
    try {
      setHits((await searchKnowledge(searchQuery, searchScope)).hits)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '知识库检索失败')
      setHits([])
      setDidSearch(false)
    } finally {
      setSearching(false)
    }
  }

  const showText = async (documentId: string, name: string, offset = 0) => {
    setPreviewLoading(true)
    setError(null)
    try {
      const result = await getParsedText(documentId, offset)
      setPreview({
        documentId, name, text: result.text, offset: result.offset,
        total: result.total_chars, more: result.has_more,
      })
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '解析文本读取失败')
    } finally {
      setPreviewLoading(false)
    }
  }

  const scanLocalDirectories = async () => {
    setIndexError(null)
    try {
      await api.post('/api/index/scan')
      await refreshIndexStatus()
    } catch (cause) {
      setIndexError(cause instanceof Error ? cause.message : '目录扫描启动失败')
    }
  }

  const clearLocalDirectoryIndex = async () => {
    if (!window.confirm(clearIndexConfirm(indexStats?.files, indexStats?.chunks))) return
    setClearingIndex(true)
    setIndexError(null)
    setNotice(null)
    try {
      await clearLocalIndex()
      setNotice('本机目录索引已清空，磁盘上的原文件没有删除；需要时重新扫描即可')
      setIndexHits([])
      await refreshIndexStatus()
    } catch (cause) {
      setIndexError(cause instanceof ApiError
        ? clearIndexError(cause.status)
        : '清空索引失败，请稍后重试')
    } finally {
      setClearingIndex(false)
    }
  }

  const searchLocalIndex = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!indexSearchQuery.trim()) return
    setIndexSearching(true)
    setIndexError(null)
    try {
      const result = await api.post<{ hits?: Record<string, unknown>[] }>('/api/index/search', { query: indexSearchQuery })
      setIndexHits(result.hits ?? [])
    } catch (cause) {
      setIndexError(cause instanceof Error ? cause.message : '本地索引检索失败')
      setIndexHits([])
    } finally {
      setIndexSearching(false)
    }
  }

  return (
    <div className="mx-auto w-full max-w-7xl space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-[12px] font-medium text-accent">文件解析 · 检索 · 来源追踪</p>
          <h1 className="mt-1 text-xl font-semibold text-ink">知识库</h1>
          <p className="mt-1 text-[13px] text-muted">按集合管理文档；重复上传保留为新版本，检索结果可回到原文件和解析文本。</p>
        </div>
        {section === 'collections' && <Button onClick={openCreate}>新建集合</Button>}
      </header>

      <div className="flex flex-wrap gap-2 border-b border-line pb-2">
        <Button size="sm" variant={section === 'collections' ? 'default' : 'ghost'} onClick={() => setSection('collections')}>
          知识集合与上传
        </Button>
        <Button size="sm" variant={section === 'local-index' ? 'default' : 'ghost'} onClick={() => setSection('local-index')}>
          本地目录索引
        </Button>
      </div>

      {error && <div role="alert" className="rounded-[var(--r-lg)] border border-danger/40 bg-danger-soft p-3 text-[13px] text-danger">{error}</div>}
      {notice && <div role="status" className="rounded-[var(--r-lg)] border border-ok/30 bg-ok-soft p-3 text-[13px] text-ok">{notice}</div>}

      {section === 'collections' ? (
        <>
          <section className="grid gap-3 sm:grid-cols-3">
            <div className="rounded-[var(--r-lg)] border border-line bg-card p-4">
              <p className="text-xs text-muted">可访问集合</p><p className="mt-1 text-2xl font-semibold">{collections.length}</p>
            </div>
            <div className="rounded-[var(--r-lg)] border border-line bg-card p-4">
              <p className="text-xs text-muted">当前集合文档</p><p className="mt-1 text-2xl font-semibold">{selected?.document_count ?? 0}</p>
            </div>
            <div className="rounded-[var(--r-lg)] border border-line bg-card p-4">
              <p className="text-xs text-muted">已解析可检索</p><p className="mt-1 text-2xl font-semibold">{readyCount}</p>
            </div>
          </section>

          <form onSubmit={(event) => void runSearch(event)} className="kb-search">
            <label className="sr-only" htmlFor="kb-search">搜索知识库</label>
            <input id="kb-search" value={searchQuery} onChange={(event) => { setSearchQuery(event.target.value); setDidSearch(false); setHits([]) }}
              placeholder="搜索你有权访问的知识库文档…" className="h-10 min-w-48 flex-1 rounded-[var(--r)] border border-line bg-bg px-3 text-[13px] outline-none focus:border-accent" />
            <label className="sr-only" htmlFor="kb-scope">检索范围</label>
            <select id="kb-scope" value={searchScope} onChange={(event) => { setSearchScope(event.target.value); setDidSearch(false); setHits([]) }}
              className="h-10 rounded-[var(--r)] border border-line bg-bg px-2 text-[13px] outline-none focus:border-accent">
              {scopeOptions(collections).map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
            <Button type="submit" disabled={searching || !searchQuery.trim()}>{searching ? '检索中…' : '检索'}</Button>
          </form>

          <p className="kb-scope-hint">检索范围：{scopeLabel(collections, searchScope)}；留空表示搜索全部可见集合。</p>

          {hits.length > 0 && (
            <section className="space-y-2 rounded-[var(--r-lg)] border border-line bg-card p-4">
              <div className="flex items-center justify-between"><h2 className="font-semibold">检索结果</h2><span className="text-xs text-muted">{hits.length} 条</span></div>
              {hits.map((hit, index) => (
                <article key={`${hit.document_id}:${hit.chunk_number}:${index}`} className="rounded-[var(--r)] border border-line bg-bg p-3">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-muted">
                    <span className="font-medium text-ink">{hit.document_name}</span><span>v{hit.version}</span><span>·</span><span>{hit.collection_name}</span>
                    <span>· 第 {hit.chunk_number} 段，字符 {hit.start_offset}–{hit.end_offset}</span>
                  </div>
                  <p className="mt-2 whitespace-pre-wrap text-[13px] leading-6 text-ink-2">{hit.snippet}</p>
                  <div className="mt-2 flex gap-2">
                    <Button size="sm" variant="outline" onClick={() => void showText(hit.document_id, hit.document_name, Math.max(0, hit.start_offset - 500))}>查看附近原文</Button>
                    <a className="inline-flex h-8 items-center rounded-[var(--r)] border border-line-strong px-3 text-[13px] text-ink-2 hover:bg-panel" href={sourceUrl(hit.document_id)} target="_blank" rel="noreferrer">打开来源文件</a>
                  </div>
                </article>
              ))}
            </section>
          )}
          {didSearch && !searching && !error && hits.length === 0 && <div role="status" className="rounded-[var(--r-lg)] border border-line bg-card p-4 text-[13px] text-muted">没有找到匹配内容。可检查关键词、切换检索范围，或确认文档已解析完成。</div>}

          <div className="grid gap-4 xl:grid-cols-[300px_minmax(0,1fr)]">
            <aside className="space-y-3">
              <div className="flex items-center justify-between"><h2 className="font-semibold">集合</h2><span className="text-xs text-muted">个人与共享</span></div>
              {loadingCollections ? (
                <div className="rounded-[var(--r-lg)] border border-line bg-card p-4 text-[13px] text-muted" role="status">正在加载集合…</div>
              ) : collections.length === 0 ? (
                <div className="rounded-[var(--r-lg)] border border-dashed border-line-strong bg-card p-5 text-center">
                  <p className="text-sm font-medium">还没有知识集合</p><p className="mt-1 text-[12px] text-muted">创建集合后上传文档，解析完成即可检索。</p>
                  <Button size="sm" className="mt-3" onClick={openCreate}>创建第一个集合</Button>
                </div>
              ) : collections.map((collection) => (
                <div key={collection.id} className={`rounded-[var(--r-lg)] border bg-card p-3 ${selectedId === collection.id ? 'border-accent shadow-card' : 'border-line'}`}>
                  <button className="w-full text-left" onClick={() => { setSelectedId(collection.id); setPreview(null) }} aria-pressed={selectedId === collection.id}>
                    <span className="flex items-center justify-between gap-2">
                      <span className="truncate text-[13px] font-semibold">{collection.name}</span>
                      <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] ${collection.visibility === 'shared' ? 'bg-accent-soft text-accent' : 'bg-panel text-muted'}`}>
                        {collection.visibility === 'shared' ? '共享' : '个人'}
                      </span>
                    </span>
                    <span className="mt-1 block line-clamp-2 text-[12px] text-muted">{collection.description || '暂无说明'}</span>
                    <span className="mt-2 block text-[11px] text-muted">{collection.document_count} 篇 · {formatBytes(collection.total_bytes)}</span>
                  </button>
                  {collection.can_edit && <div className="mt-2 flex flex-wrap gap-1 border-t border-line pt-2">
                    <Button size="sm" variant="ghost" onClick={() => openEdit(collection)}>设置</Button>
                    <Button size="sm" variant="ghost" disabled={collection.document_count === 0 || saving || clearingCollectionId === collection.id}
                      onClick={() => void clearDocs(collection)}>
                      {clearingCollectionId === collection.id ? '清空中…' : '清空文档'}
                    </Button>
                    <Button size="sm" variant="ghost" disabled={saving} onClick={() => void removeCollection(collection)}>删除集合</Button>
                  </div>}
                </div>
              ))}
            </aside>

            <section className="min-w-0 space-y-3">
              {selected ? (
                <>
                  <div className="flex flex-wrap items-end justify-between gap-3 rounded-[var(--r-lg)] border border-line bg-card p-4">
                    <div><p className="text-[11px] font-medium text-accent">{selected.visibility === 'shared' ? '共享集合' : '个人集合'}</p>
                      <h2 className="mt-1 text-lg font-semibold">{selected.name}</h2><p className="mt-1 text-[12px] text-muted">{selected.description || '上传支持的文件类型；同名文件会保留为新的版本。'}</p></div>
                    {selected.can_edit && <label className={`inline-flex h-9 cursor-pointer items-center rounded-[var(--r)] bg-accent px-4 text-[13px] font-medium text-on-accent hover:opacity-90 ${saving ? 'pointer-events-none opacity-50' : ''}`}>
                      {saving ? '处理中…' : '上传文档'}
                      <input type="file" multiple accept={ACCEPTED} className="sr-only" disabled={saving}
                        onChange={(event) => { const selectedFiles = Array.from(event.target.files ?? []); event.target.value = ''; void upload(selectedFiles) }} />
                    </label>}
                  </div>
                  {selected.can_edit && <p className="px-1 text-[11px] text-muted">支持 TXT、MD、PDF、DOCX、XLSX、PPTX 和图片 OCR；应用不设单文件大小上限。每次上传都会保留为独立版本。</p>}

                  <div className="overflow-hidden rounded-[var(--r-lg)] border border-line bg-card">
                    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-3">
                      <h3 className="text-[13px] font-semibold">文档版本</h3>
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-[11px] text-muted">{documents.length} 篇 · {readyCount} 篇可检索</span>
                        {processing && <span className="kb-progress" role="status">{summary.label} · 自动刷新中</span>}
                        <Button size="sm" variant="ghost" onClick={() => void refreshDocuments()}>刷新</Button>
                      </div>
                    </div>
                    {pollHintText && <p className="kb-poll-hint" role="status">{pollHintText}</p>}
                    {loadingDocuments ? <div className="p-5 text-[13px] text-muted" role="status">正在加载文档…</div> : documents.length === 0 ? (
                      <div className="p-8 text-center"><p className="text-sm font-medium">集合里还没有文档</p><p className="mt-1 text-[12px] text-muted">上传文件后会在后台解析，解析失败时可查看原因并单篇重建。</p></div>
                    ) : <div className="divide-y divide-line">
                      {documents.map((doc) => (
                        <article key={doc.id} className="grid gap-2 px-4 py-3 md:grid-cols-[minmax(0,1fr)_auto] md:items-center">
                          <div className="min-w-0">
                            <div className="flex flex-wrap items-center gap-2">
                              <span className="max-w-full truncate text-[13px] font-medium" title={doc.original_name}>{doc.original_name}</span>
                              <span className="rounded bg-panel px-1.5 py-0.5 text-[10px] text-muted">v{doc.version}</span>
                              <span className={`rounded-full px-2 py-0.5 text-[10px] ${doc.status === 'ready' ? 'bg-ok-soft text-ok' : doc.status === 'failed' ? 'bg-danger-soft text-danger' : 'bg-warn-soft text-warn'}`} role="status">{statusLabel(doc.status)}</span>
                            </div>
                            <p className="mt-1 text-[11px] text-muted">{formatBytes(doc.size_bytes)} · 上传于 {formatDate(doc.created_at)}</p>
                            {doc.note && <p className="mt-1 break-words text-[12px] text-danger" role="alert">{doc.note}</p>}
                          </div>
                          <div className="flex flex-wrap items-center gap-1">
                            {doc.status === 'ready' && <Button size="sm" variant="ghost" disabled={previewLoading} onClick={() => void showText(doc.id, doc.original_name)}>解析文本</Button>}
                            <a className="inline-flex h-8 items-center rounded-[var(--r)] px-3 text-[12px] text-ink-2 hover:bg-panel" href={sourceUrl(doc.id)} target="_blank" rel="noreferrer">来源文件</a>
                            {doc.can_edit && <>
                              <Button size="sm" variant="ghost" disabled={saving || doc.status === 'processing' || doc.status === 'queued'} onClick={() => void reparse(doc)}>{doc.status === 'failed' ? '重新构建' : '重建索引'}</Button>
                              <Button size="sm" variant="ghost" disabled={saving || doc.status === 'processing' || doc.status === 'queued'} onClick={() => void removeDocument(doc)}>删除</Button>
                            </>}
                          </div>
                        </article>
                      ))}
                    </div>}
                  </div>

                  {preview && <section className="overflow-hidden rounded-[var(--r-lg)] border border-line bg-card">
                    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-3">
                      <div><h3 className="text-[13px] font-semibold">{preview.name}</h3><p className="text-[11px] text-muted">解析文本 · 字符 {preview.offset}–{Math.min(preview.offset + preview.text.length, preview.total)} / {preview.total}</p></div>
                      <div className="flex gap-1"><a className="inline-flex h-8 items-center rounded-[var(--r)] border border-line-strong px-3 text-[12px] hover:bg-panel" href={sourceUrl(preview.documentId)} target="_blank" rel="noreferrer">打开原文件</a><Button size="sm" variant="ghost" onClick={() => setPreview(null)}>关闭</Button></div>
                    </div>
                    <pre className="max-h-[420px] overflow-auto whitespace-pre-wrap break-words p-4 font-mono text-[12px] leading-6 text-ink-2">{preview.text}</pre>
                    {preview.more && <div className="border-t border-line p-3 text-center"><Button size="sm" variant="outline" disabled={previewLoading} onClick={() => void showText(preview.documentId, preview.name, preview.offset + preview.text.length)}>继续查看</Button></div>}
                  </section>}
                </>
              ) : <div className="grid min-h-72 place-items-center rounded-[var(--r-lg)] border border-dashed border-line-strong bg-card p-8 text-center text-[13px] text-muted">
                {loadingCollections ? '正在加载知识集合…' : '先创建个人集合，或等待其他账号分享集合。'}
              </div>}
            </section>
          </div>
        </>
      ) : <LocalIndexPanel
        stats={indexStats} scanning={indexScanning} error={indexError} hits={indexHits}
        query={indexSearchQuery} setQuery={setIndexSearchQuery} searching={indexSearching}
        clearing={clearingIndex} canClear={!indexScanning}
        onScan={() => void scanLocalDirectories()} onClear={() => void clearLocalDirectoryIndex()}
        onSearch={(event) => void searchLocalIndex(event)}
      />}

      {modalOpen && <div className="fixed inset-0 z-50 grid place-items-center bg-black/35 p-4" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setModalOpen(false) }}>
        <form role="dialog" aria-modal="true" onKeyDown={(event) => { if (event.key === 'Escape') setModalOpen(false) }} onSubmit={(event) => void saveCollection(event)} className="w-full max-w-lg space-y-4 rounded-[var(--r-lg)] border border-line bg-card p-5 shadow-lg" aria-labelledby="collection-dialog-title">
          <div><h2 id="collection-dialog-title" className="text-lg font-semibold">{editingId ? '集合设置' : '新建知识集合'}</h2><p className="mt-1 text-[12px] text-muted">个人集合仅自己可访问；共享集合对所有账号可检索，只有创建者可修改。</p></div>
          <label className="block space-y-1 text-[12px] font-medium">集合名称
            <input autoFocus required maxLength={120} value={draft.name} onChange={(event) => setDraft((value) => ({ ...value, name: event.target.value }))} className="h-10 w-full rounded-[var(--r)] border border-line bg-bg px-3 text-[13px] font-normal outline-none focus:border-accent" />
          </label>
          <label className="block space-y-1 text-[12px] font-medium">说明
            <textarea maxLength={500} rows={3} value={draft.description} onChange={(event) => setDraft((value) => ({ ...value, description: event.target.value }))} className="w-full rounded-[var(--r)] border border-line bg-bg px-3 py-2 text-[13px] font-normal outline-none focus:border-accent" />
          </label>
          <label className="block space-y-1 text-[12px] font-medium">可见范围
            <select value={draft.visibility} onChange={(event) => setDraft((value) => ({ ...value, visibility: event.target.value as CollectionDraft['visibility'] }))} className="h-10 w-full rounded-[var(--r)] border border-line bg-bg px-3 text-[13px] font-normal">
              <option value="private">个人可见</option><option value="shared">对所有账号共享</option>
            </select>
          </label>
          <div className="flex justify-end gap-2 border-t border-line pt-3"><Button type="button" variant="outline" onClick={() => setModalOpen(false)}>取消</Button><Button type="submit" disabled={saving || !draft.name.trim()}>{saving ? '保存中…' : '保存'}</Button></div>
        </form>
      </div>}
    </div>
  )
}

function LocalIndexPanel(props: {
  stats: Record<string, unknown> | null
  scanning: boolean
  error: string | null
  hits: Record<string, unknown>[]
  query: string
  setQuery: (value: string) => void
  searching: boolean
  clearing: boolean
  canClear: boolean
  onScan: () => void
  onClear: () => void
  onSearch: (event: FormEvent<HTMLFormElement>) => void
}) {
  const last = props.stats?.last_scan as { partial?: boolean; limit_hits?: string[]; indexed?: number; failed?: number } | null
  return <section className="mx-auto max-w-5xl space-y-4">
    <div className="rounded-[var(--r-lg)] border border-warn/40 bg-warn-soft p-4 text-[12px] text-ink-2">
      目录索引继续使用本机已授权目录和现有索引。它对本机账号共用；如需按个人或共享范围控制文档，请使用上方的知识集合。
    </div>
    {props.error && <div role="alert" className="rounded-[var(--r-lg)] border border-danger/40 bg-danger-soft p-3 text-[13px] text-danger">{props.error}</div>}
    <div className="grid gap-3 sm:grid-cols-3">
      <div className="rounded-[var(--r-lg)] border border-line bg-card p-4"><p className="text-xs text-muted">已索引文件</p><p className="mt-1 text-2xl font-semibold">{String(props.stats?.files ?? '—')}</p></div>
      <div className="rounded-[var(--r-lg)] border border-line bg-card p-4"><p className="text-xs text-muted">索引片段</p><p className="mt-1 text-2xl font-semibold">{String(props.stats?.chunks ?? '—')}</p></div>
      <div className="rounded-[var(--r-lg)] border border-line bg-card p-4"><p className="text-xs text-muted">扫描状态</p><p className="mt-1 text-lg font-semibold">{props.scanning ? '正在扫描…' : '空闲'}</p></div>
    </div>
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-[var(--r-lg)] border border-line bg-card p-4">
      <div><h2 className="font-semibold">扫描已授权目录</h2><p className="mt-1 text-[12px] text-muted">扫描可能耗时较长；进度和截断原因会在扫描完成后显示。</p></div>
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" disabled={!props.canClear || props.clearing} onClick={props.onClear}>
          {props.clearing ? '清空中…' : '清空索引'}
        </Button>
        <Button disabled={props.scanning} onClick={props.onScan}>{props.scanning ? '扫描中…' : '开始扫描'}</Button>
      </div>
    </div>
    <p className="text-[12px] text-muted">「清空索引」只删除本机索引数据，磁盘上的原文件不受影响；清空后重新扫描即可恢复。扫描进行中不能清空。</p>
    {last && <p className="text-[12px] text-muted">最近一次：新增 {last.indexed ?? 0} 个文件，失败 {last.failed ?? 0} 个{last.partial ? `；扫描不完整，触及上限：${(last.limit_hits ?? []).join('、')}` : ''}</p>}
    <form onSubmit={props.onSearch} className="flex flex-wrap gap-2 rounded-[var(--r-lg)] border border-line bg-card p-3">
      <label className="sr-only" htmlFor="local-index-search">检索本机目录索引</label>
      <input id="local-index-search" value={props.query} onChange={(event) => props.setQuery(event.target.value)} placeholder="搜索已扫描目录…" className="h-10 min-w-48 flex-1 rounded-[var(--r)] border border-line bg-bg px-3 text-[13px] outline-none focus:border-accent" />
      <Button type="submit" disabled={props.searching || !props.query.trim()}>{props.searching ? '检索中…' : '检索本机索引'}</Button>
    </form>
    {props.hits.length > 0 && <div className="space-y-2">{props.hits.map((hit, index) => <article key={`${String(hit.path)}:${index}`} className="rounded-[var(--r-lg)] border border-line bg-card p-3">
      <p className="break-all text-[12px] font-medium">{String(hit.path ?? '未知来源')}</p><p className="mt-2 whitespace-pre-wrap text-[13px] text-ink-2">{String(hit.snippet ?? '')}</p>
    </article>)}</div>}
  </section>
}
