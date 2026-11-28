/**
 * 执行步骤面板 —— 模块 2「步骤详情」。
 *
 * 数据来自 `GET /api/sessions/{id}` 的 steps（参数 / 结果 / 报错 / 风险 /
 * 时间 / 失败分类 / 只读自证 / 产物路径），SSE 的 step.* 事件只负责
 * 触发刷新与补少量未落库的字段，落库数据始终是准绳。
 *
 * 交互约定：
 *   · 默认折叠，失败 / 被拒绝的步骤自动展开 —— 报错不该藏起来
 *   · 长文本默认截到 2000 字，可展开全文；每个区块可单独复制
 *   · 没有步骤时给引导空状态，而不是把整块区域悄悄隐藏
 */

import { useEffect, useState } from 'react'

import type { Step } from '@/lib/types'
import { AUTOCLASS_CN, buildStepView, type StepExtra, type StepView } from './stepFormat'

const PREVIEW_LIMIT = 2000

export default function StepList({
  steps,
  extras,
}: {
  steps: Step[]
  extras: Record<string, StepExtra>
}) {
  const failed = steps.filter((s) => s.state === 'failed' || s.state === 'denied').length
  const running = steps.filter((s) => s.state === 'running').length

  return (
    <section aria-label="执行步骤">
      <h2 className="mb-2 text-[13px] font-semibold text-ink-2">
        执行步骤
        <span className="ml-2 font-normal text-muted">
          {steps.length} 步
          {running > 0 ? ` · ${running} 步进行中` : ''}
          {failed > 0 ? ` · ${failed} 步未成功` : ''}
        </span>
      </h2>
      {steps.length === 0 ? (
        <div className="rounded-[var(--r-lg)] border border-line bg-card px-4 py-6 text-center text-[12.5px] leading-relaxed text-muted">
          这次运行还没有执行步骤。
          <br />
          Agent 调用工具时，这里会实时显示每一步的参数、结果与报错。
        </div>
      ) : (
        <ul className="space-y-1.5">
          {steps.map((s) => (
            <StepCard key={s.id} view={buildStepView(s, extras[s.id])} />
          ))}
        </ul>
      )}
    </section>
  )
}

function StepCard({ view }: { view: StepView }) {
  // 失败 / 拒绝默认展开；运行中变成失败时也要自动展开一次。
  const [open, setOpen] = useState(view.state === 'failed' || view.state === 'denied')
  useEffect(() => {
    if (view.state === 'failed' || view.state === 'denied') setOpen(true)
  }, [view.state])

  return (
    <li className="step-card">
      <button
        type="button"
        className="step-head"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="step-caret" aria-hidden="true">{open ? '▾' : '▸'}</span>
        <span className="step-seq">#{view.seq}</span>
        <span className="step-tool" title={view.tool}>{view.tool}</span>
        <span className={'step-badge step-badge--' + (view.state in STATE_TONE ? view.state : 'other')}>
          {view.stateLabel}
        </span>
        {view.riskLabel && (
          <span className={'step-risk step-risk--' + (view.risk ?? 0)}>{view.riskLabel}</span>
        )}
        {view.errorClass && (
          <span className="step-tag" title={view.errorHint ?? undefined}>{view.errorClass}</span>
        )}
        {view.duration && <span className="step-duration">{view.duration}</span>}
        <span className="step-summary" title={view.argsSummary}>{view.argsSummary}</span>
      </button>

      {open && (
        <div className="step-body">
          {!view.hasDetails ? (
            <p className="step-empty">这一步没有留下参数或输出。</p>
          ) : (
            <>
              {view.argsText && <Field label="调用参数" text={view.argsText} />}
              {view.resultText && <Field label="执行结果" text={view.resultText} />}
              {view.errorText && <Field label="错误信息" text={view.errorText} tone="danger" />}
              {view.errorClass && (
                <p className="step-hint">
                  失败分类 {view.errorClass}
                  {view.errorHint ? ` · ${view.errorHint}` : ''}
                </p>
              )}
              {view.artifactPath && (
                <p className="step-meta">
                  产物路径：<code className="step-inline-code">{view.artifactPath}</code>
                </p>
              )}
              {view.mutated !== null && (
                <span className={view.mutated ? 'step-proof step-proof--mutated' : 'step-proof'}>
                  {view.mutated ? '已改动' : '未改动（只读）'}
                </span>
              )}
              {(view.reason || view.verdict) && (
                <p className="step-meta">
                  {view.verdict ? `自动分类：${AUTOCLASS_CN[view.verdict] ?? view.verdict}` : ''}
                  {view.verdict && view.reason ? ' · ' : ''}
                  {view.reason ?? ''}
                </p>
              )}
            </>
          )}
        </div>
      )}
    </li>
  )
}

const STATE_TONE: Record<string, true> = {
  running: true, done: true, failed: true, denied: true,
}

function Field({ label, text, tone }: { label: string; text: string; tone?: 'danger' }) {
  return (
    <div className="step-field">
      <div className="step-field-head">
        <span className="step-field-label">{label}</span>
        <span className="step-field-size">{text.length} 字</span>
        <CopyButton text={text} />
      </div>
      <CodeBlock text={text} tone={tone} />
    </div>
  )
}

function CodeBlock({ text, tone }: { text: string; tone?: 'danger' }) {
  const [expanded, setExpanded] = useState(false)
  const tooLong = text.length > PREVIEW_LIMIT
  const shown = tooLong && !expanded ? text.slice(0, PREVIEW_LIMIT) : text
  return (
    <>
      <pre className={'step-code' + (tone === 'danger' ? ' step-code--danger' : '')}>
        {shown}
        {tooLong && !expanded ? '\n…（已截断）' : ''}
      </pre>
      {tooLong && (
        <button type="button" className="step-more" onClick={() => setExpanded((v) => !v)}>
          {expanded ? '收起' : `显示全文（共 ${text.length} 字）`}
        </button>
      )}
    </>
  )
}

function CopyButton({ text }: { text: string }) {
  const [state, setState] = useState<'idle' | 'ok' | 'fail'>('idle')
  const copy = async () => {
    let ok = false
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text)
        ok = true
      }
    } catch {
      /* 走下面的降级 */
    }
    if (!ok) {
      // 非安全上下文 / 老浏览器：Clipboard API 不可用，用临时 textarea 兜底。
      try {
        const ta = document.createElement('textarea')
        ta.value = text
        ta.style.position = 'fixed'
        ta.style.opacity = '0'
        document.body.appendChild(ta)
        ta.select()
        ok = document.execCommand('copy')
        document.body.removeChild(ta)
      } catch {
        ok = false
      }
    }
    setState(ok ? 'ok' : 'fail')
    window.setTimeout(() => setState('idle'), 1800)
  }
  return (
    <button type="button" className="step-copy" onClick={() => void copy()} aria-label="复制内容">
      {state === 'ok' ? '已复制' : state === 'fail' ? '复制失败' : '复制'}
    </button>
  )
}
