import { useCallback, useEffect, useState, type FormEvent } from 'react'

import { Button } from '@/components/ui/button'
import { ApiError } from '@/lib/api'
import { addMemory, deleteMemory, fetchMemories } from './api'
import {
  dedupeNotice,
  memoryBody,
  memorySummary,
  memoryTime,
  validateMemoryTags,
  validateMemoryText,
  type MemoryItem,
} from './settingsOps'

function messageOf(cause: unknown): string {
  return cause instanceof ApiError ? cause.message : '操作失败，请稍后重试'
}

/** 长期记忆：记、查、忘。空内容 / 坏数据交给契约层挡，前端先拦一道。 */
export default function MemoryPanel() {
  const [items, setItems] = useState<MemoryItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [text, setText] = useState('')
  const [tags, setTags] = useState('')
  const [busy, setBusy] = useState<string | null>(null)

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      const data = await fetchMemories(50)
      setItems(Array.isArray(data.memories) ? data.memories : [])
      setError(null)
    } catch (cause) {
      setError(messageOf(cause))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void reload() }, [reload])

  const add = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const invalid = validateMemoryText(text) ?? validateMemoryTags(tags)
    if (invalid) { setError(invalid); return }
    setBusy('add')
    setError(null)
    setNotice(null)
    try {
      const body = memoryBody(text, tags)
      const result = await addMemory(body.text, body.tags)
      setNotice(dedupeNotice(Boolean(result.deduped)))
      setText('')
      setTags('')
      await reload()
    } catch (cause) {
      setError(messageOf(cause))
    } finally {
      setBusy(null)
    }
  }

  const remove = async (item: MemoryItem) => {
    if (busy) return
    if (!window.confirm('确定忘掉这条记忆吗？')) return
    setBusy(item.id)
    setError(null)
    setNotice(null)
    try {
      await deleteMemory(item.id)
      setNotice('已忘掉这条记忆')
      await reload()
    } catch (cause) {
      setError(messageOf(cause))
    } finally {
      setBusy(null)
    }
  }

  const summary = memorySummary(items)

  return (
    <div className="mm-panel">
      {error && <p className="sys-error" role="alert">{error}</p>}
      {notice && <p className="sys-notice" role="status">{notice}</p>}

      <form className="mm-form" onSubmit={(e) => void add(e)}>
        <label className="sys-field" htmlFor="mm-text">
          <span>要记住的内容</span>
          <textarea
            id="mm-text"
            className="mm-textarea"
            rows={3}
            value={text}
            placeholder="例如：这个项目的构建命令是 npm run build"
            onChange={(e) => setText(e.target.value)}
          />
        </label>
        <label className="sys-field" htmlFor="mm-tags">
          <span>标签（可选）</span>
          <input
            id="mm-tags"
            className="mm-input"
            value={tags}
            placeholder="构建 / 偏好 / 项目"
            onChange={(e) => setTags(e.target.value)}
          />
        </label>
        <div className="mm-form-actions">
          <Button size="sm" type="submit" disabled={busy !== null}>
            {busy === 'add' ? '保存中…' : '记住'}
          </Button>
          <span className="sys-hint">完全相同的内容不会重复存，只更新时间。</span>
        </div>
      </form>

      <div className="mm-list-head">
        <h3 className="sys-subtitle">已记住</h3>
        <span className="sys-hint">{summary.text}</span>
        <Button size="sm" variant="outline" onClick={() => void reload()} disabled={loading}>
          {loading ? '读取中…' : '刷新'}
        </Button>
      </div>

      {items.length === 0 ? (
        <p className="sys-empty">{loading ? '正在读取…' : '还没有记住任何内容'}</p>
      ) : (
        <ul className="mm-list">
          {items.map((item) => (
            <li key={item.id} className="mm-item">
              <div className="mm-item-main">
                <span className="mm-text">{item.text}</span>
                <span className="mm-meta">
                  {item.tags ? `${item.tags} · ` : ''}{memoryTime(item.updated_at)}
                </span>
              </div>
              <Button
                size="sm"
                variant="ghost"
                disabled={busy !== null}
                onClick={() => void remove(item)}
              >
                {busy === item.id ? '…' : '忘掉'}
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
