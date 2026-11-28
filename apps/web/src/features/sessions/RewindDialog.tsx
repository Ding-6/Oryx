/**
 * 检查点回退弹层 —— 先选范围，再执行，最后把结果如实报出来。
 *
 * 为什么不点一下就退：三种范围的破坏性完全不同。
 * 「只删对话」丢的是消息，「只还原文件」改的是磁盘，
 * 默认替用户选一个都可能造成不可逆的意外。
 */

import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { ApiError } from '@/lib/api'
import type { Checkpoint, RewindContent } from '@/lib/types'
import {
  REWIND_SCOPES,
  formatCheckpointTime,
  rewindImpact,
  scopeLabel,
  summarizeRewind,
  type RewindScope,
} from './checkpoints'
import { rewindCheckpoint } from './api'

export default function RewindDialog({
  checkpoint,
  totalMessages,
  running,
  onClose,
  onRewound,
}: {
  checkpoint: Checkpoint | null
  totalMessages: number
  running: boolean
  onClose: () => void
  /** 回退成功：让会话页重新拉详情、检查点与左栏计数。 */
  onRewound: (content: RewindContent) => void
}) {
  const [scope, setScope] = useState<RewindScope | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<RewindContent | null>(null)
  const dialogRef = useRef<HTMLDivElement>(null)
  const onCloseRef = useRef(onClose)
  useEffect(() => { onCloseRef.current = onClose }, [onClose])

  // 换检查点 / 重新打开时把上一次的选择与结果清干净。
  useEffect(() => {
    if (!checkpoint) return
    setScope(null)
    setError(null)
    setResult(null)
    setBusy(false)
    dialogRef.current?.focus()
  }, [checkpoint?.id])

  useEffect(() => {
    if (!checkpoint) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !busy) onCloseRef.current()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [checkpoint, busy])

  if (!checkpoint) return null

  const impact = rewindImpact(checkpoint, totalMessages)
  const summary = summarizeRewind(result)

  const run = async () => {
    if (!scope || busy) return
    setBusy(true)
    setError(null)
    try {
      const out = await rewindCheckpoint(checkpoint.id, scope)
      setResult(out.content)
      onRewound(out.content)
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : cause instanceof Error ? cause.message : '回退失败，请稍后重试')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      className="pref-overlay"
      onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose() }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="rewind-title"
        tabIndex={-1}
        className="pref-dialog"
      >
        <header className="pref-head">
          <div>
            <h2 id="rewind-title" className="text-[15px] font-semibold text-ink">回退到这一轮之前</h2>
            <p className="mt-0.5 text-[11.5px] text-muted">
              {checkpoint.label || '这次提问'} · {formatCheckpointTime(checkpoint.created_at)}
            </p>
          </div>
          <Button size="sm" variant="ghost" onClick={onClose} disabled={busy} aria-label="关闭回退弹层">关闭</Button>
        </header>

        <div className="pref-body">
          {running && (
            <p className="pref-error" role="alert">
              这个会话正在运行，先停止或等它结束再回退。
            </p>
          )}
          {error && <p className="pref-error" role="alert">{error}</p>}

          {result ? (
            <div className="rw-result" role="status">
              <p className="rw-result-title">已回退（{scopeLabel(result.scope)}）</p>
              {summary.length > 0 ? (
                <ul className="rw-result-list">
                  {summary.map((line) => <li key={line}>{line}</li>)}
                </ul>
              ) : (
                <p className="rw-result-empty">这次没有需要改动的内容。</p>
              )}
              {result.errors?.length > 0 && (
                <details className="rw-result-errors">
                  <summary>{result.errors.length} 个文件失败，查看明细</summary>
                  <ul>{result.errors.map((line) => <li key={line}>{line}</li>)}</ul>
                </details>
              )}
              {result.skipped?.length > 0 && (
                <details className="rw-result-errors">
                  <summary>{result.skipped.length} 个文件被跳过，查看明细</summary>
                  <ul>{result.skipped.map((line) => <li key={line}>{line}</li>)}</ul>
                </details>
              )}
            </div>
          ) : (
            <>
              <p className="rw-warning">
                回退不会进回收站，也不依赖 Git —— 做完就不可撤销。
              </p>
              <div className="rw-scopes" role="radiogroup" aria-label="回退范围">
                {REWIND_SCOPES.map((item) => (
                  <button
                    key={item.scope}
                    type="button"
                    role="radio"
                    aria-checked={scope === item.scope}
                    className={
                      'rw-scope' +
                      (scope === item.scope ? ' rw-scope--on' : '') +
                      (item.danger ? ' rw-scope--danger' : '')
                    }
                    disabled={busy || running}
                    onClick={() => setScope(item.scope)}
                  >
                    <span className="rw-scope-title">{item.label}</span>
                    <span className="rw-scope-hint">{item.hint}</span>
                  </button>
                ))}
              </div>
              <p className="rw-impact">
                {impact.files > 0
                  ? `这一轮之后改过 ${impact.files} 个文件`
                  : '这一轮没有改动文件'}
                {impact.messages > 0 ? ` · 将删掉 ${impact.messages} 条消息` : ''}
              </p>
              <p className="rw-note">
                「只还原文件」和「只删除对话」都只做一半；不确定时选「文件和对话都回退」。
              </p>
            </>
          )}
        </div>

        <footer className="pref-foot">
          <span className="text-[11.5px] text-muted">
            {result ? '界面已刷新到回退后的状态' : '选一个范围后才会执行'}
          </span>
          {result ? (
            <Button size="sm" onClick={onClose}>完成</Button>
          ) : (
            <Button
              size="sm"
              variant={scope === 'CodeAndConversation' ? 'danger' : 'default'}
              disabled={!scope || busy || running}
              onClick={() => void run()}
            >
              {busy ? '正在回退…' : '确认回退'}
            </Button>
          )}
        </footer>
      </div>
    </div>
  )
}
