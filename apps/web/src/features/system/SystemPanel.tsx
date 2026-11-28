import { useCallback, useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { ApiError, subscribe } from '@/lib/api'
import { shouldRefresh, throttleDelay } from '@/features/overview/overviewOps'
import type { Session } from '@/lib/types'
import {
  fetchAudit,
  fetchDoctor,
  fetchExportDetail,
  fetchExportSessions,
  fetchUsage,
} from './api'
import {
  EXPORT_KINDS,
  USAGE_WINDOWS,
  buildExport,
  clampUsageHours,
  dependencyRows,
  doctorCheckDetail,
  doctorCheckTone,
  doctorStatus,
  formatTokens,
  modelRows,
  usageCards,
  type DoctorReport,
  type ExportFile,
  type ExportKind,
  type UsageSummary,
} from './systemOps'

function messageOf(cause: unknown): string {
  if (cause instanceof ApiError) return cause.message
  if (cause instanceof Error) return cause.message
  return '加载失败，请稍后重试'
}

/** 下载由前端本地生成，不新增后端导出接口。 */
function download(file: ExportFile): void {
  const blob = new Blob([file.content], { type: file.mime })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = file.filename
  anchor.style.display = 'none'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

/**
 * 「系统」页：用量 / 环境自检 / 审计导出。
 *
 * 用量窗口是**滚动小时**（后端 `today` 固定 24 小时、`total` 是 window_hours
 * 小时），卡片与表格一律按后端回显的 `window_hours` 标注，不冒充自然月。
 */
export default function SystemPanel() {
  const [hours, setHours] = useState(720)
  const hoursRef = useRef(hours)
  const [usage, setUsage] = useState<UsageSummary | null>(null)
  const [usageLoading, setUsageLoading] = useState(true)
  const [usageError, setUsageError] = useState<string | null>(null)

  const [doctor, setDoctor] = useState<DoctorReport | null>(null)
  const [doctorLoading, setDoctorLoading] = useState(true)
  const [doctorError, setDoctorError] = useState<string | null>(null)

  const [sessions, setSessions] = useState<Session[]>([])
  const [sessionsError, setSessionsError] = useState<string | null>(null)
  const [taskId, setTaskId] = useState('')
  const [exportBusy, setExportBusy] = useState<ExportKind | null>(null)
  const [exportError, setExportError] = useState<string | null>(null)
  const [exportNotice, setExportNotice] = useState<string | null>(null)

  const reloadUsage = useCallback(async () => {
    setUsageLoading(true)
    try {
      const data = await fetchUsage(hoursRef.current)
      setUsage(data)
      setUsageError(null)
    } catch (cause) {
      setUsageError(messageOf(cause))
    } finally {
      setUsageLoading(false)
    }
  }, [])

  useEffect(() => {
    hoursRef.current = hours
    void reloadUsage()
  }, [hours, reloadUsage])

  const reloadDoctor = useCallback(async () => {
    setDoctorLoading(true)
    try {
      const data = await fetchDoctor()
      setDoctor(data)
      setDoctorError(null)
    } catch (cause) {
      setDoctorError(messageOf(cause))
    } finally {
      setDoctorLoading(false)
    }
  }, [])

  useEffect(() => {
    void reloadDoctor()
  }, [reloadDoctor])

  useEffect(() => {
    let alive = true
    fetchExportSessions(50)
      .then((list) => {
        if (!alive) return
        setSessions(list)
        setTaskId((current) => current || list[0]?.id || '')
      })
      .catch((cause) => alive && setSessionsError(messageOf(cause)))
    return () => { alive = false }
  }, [])

  // 运行结束 / 步骤变化会改变用量，订阅 SSE 节流刷新（与总览同一套判定）。
  useEffect(() => {
    let timer: number | undefined
    let last = 0
    const off = subscribe('/api/events', (ev) => {
      if (!shouldRefresh(ev.type)) return
      const wait = throttleDelay(Date.now() - last)
      if (timer !== undefined) window.clearTimeout(timer)
      timer = window.setTimeout(() => {
        last = Date.now()
        void reloadUsage()
      }, wait)
    })
    return () => {
      off()
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [reloadUsage])

  const runExport = async (kind: ExportKind) => {
    if (!taskId || exportBusy) return
    setExportBusy(kind)
    setExportError(null)
    setExportNotice(null)
    try {
      let file: ExportFile | null = null
      if (kind === 'audit') {
        const payload = await fetchAudit(taskId)
        file = buildExport('audit', { taskId, audit: payload.audit })
      } else {
        const detail = await fetchExportDetail(taskId)
        file = buildExport(kind, {
          taskId,
          messages: detail.messages,
          steps: detail.steps,
        })
      }
      if (!file) throw new Error('没有生成导出文件')
      download(file)
      setExportNotice(`已导出 ${file.filename}`)
    } catch (cause) {
      setExportError(messageOf(cause))
    } finally {
      setExportBusy(null)
    }
  }

  const cards = usageCards(usage)
  const rows = modelRows(usage)
  const status = doctorStatus(doctor)
  const deps = dependencyRows(doctor)

  return (
    <div className="sys-panel">
      <header className="sys-head">
        <div>
          <h1 className="text-[17px] font-semibold text-ink">系统</h1>
          <p className="mt-0.5 text-[12px] text-muted">
            用量、环境自检与会话导出。请求日志目前只写在服务端日志文件里，界面暂不提供。
          </p>
        </div>
      </header>

      {/* ── 用量汇总 ─────────────────────────────────────────────── */}
      <section className="sys-section">
        <div className="sys-section-head">
          <div>
            <h2 className="text-[14px] font-semibold text-ink">用量汇总</h2>
            <p className="sys-hint">按账号统计；窗口是滚动小时，不是自然日 / 自然月。</p>
          </div>
          <div className="sys-actions">
            <select
              aria-label="统计窗口"
              className="sys-select"
              value={hours}
              onChange={(e) => setHours(clampUsageHours(e.target.value))}
            >
              {USAGE_WINDOWS.map((w) => (
                <option key={w.hours} value={w.hours}>{w.label}</option>
              ))}
            </select>
            <Button size="sm" variant="outline" onClick={() => void reloadUsage()} disabled={usageLoading}>
              {usageLoading ? '读取中…' : '刷新'}
            </Button>
          </div>
        </div>

        {usageError && <p className="sys-error" role="alert">{usageError}</p>}
        <div className="sys-cards">
          {cards.map((card) => (
            <div key={card.key} className="sys-card">
              <span className="sys-card-label">{card.label}</span>
              <strong className="sys-card-value">{card.value}</strong>
              <span className="sys-hint">{card.hint}</span>
            </div>
          ))}
        </div>

        <div className="sys-table-wrap">
          <table className="sys-table">
            <thead>
              <tr><th>服务</th><th>模型</th><th>调用</th><th>输入</th><th>输出</th></tr>
            </thead>
            <tbody>
              {rows.length === 0 ? (
                <tr><td colSpan={5} className="sys-empty">这个窗口里还没有用量记录</td></tr>
              ) : rows.map((row, index) => (
                <tr key={`${row.provider}-${row.model}-${index}`}>
                  <td>{row.provider || '—'}</td>
                  <td>{row.model || '—'}</td>
                  <td>{row.calls}</td>
                  <td>{formatTokens(row.inp)}</td>
                  <td>{formatTokens(row.outp)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* ── 环境自检 ─────────────────────────────────────────────── */}
      <section className="sys-section">
        <div className="sys-section-head">
          <div>
            <h2 className="text-[14px] font-semibold text-ink">环境自检</h2>
            <p className="sys-hint">只读：不访问网络，也不扫描你的文件。</p>
          </div>
          <Button size="sm" variant="outline" onClick={() => void reloadDoctor()} disabled={doctorLoading}>
            {doctorLoading ? '检查中…' : '重新检查'}
          </Button>
        </div>

        {doctorError && <p className="sys-error" role="alert">{doctorError}</p>}
        {doctor && (
          <>
            <div className="sys-doctor-meta">
              <span className={'sys-badge ' + (status.providerReady ? 'sys-badge--ok' : 'sys-badge--warn')}>
                {status.providerReady ? '模型就绪' : '缺模型服务'}
              </span>
              <span className="sys-badge sys-badge--plain">{status.text}</span>
              <span className="sys-hint">
                {doctor.active_provider || '未配置'} · Python {doctor.python || '?'} · {doctor.platform || '?'}
                {doctor.version ? ` · v${doctor.version}` : ''}
              </span>
            </div>

            <div className="sys-checks">
              {(doctor.checks ?? []).length === 0 ? (
                <p className="sys-empty">没有自检项</p>
              ) : (doctor.checks ?? []).map((check, index) => (
                <div key={`${check.name}-${index}`} className="sys-check">
                  <span className={'sys-badge ' + (doctorCheckTone(check) === 'ok' ? 'sys-badge--ok' : 'sys-badge--warn')}>
                    {doctorCheckTone(check) === 'ok' ? '正常' : '注意'}
                  </span>
                  <span className="sys-check-name">{check.name}</span>
                  <span className="sys-check-detail">{doctorCheckDetail(check)}</span>
                </div>
              ))}
            </div>

            {deps.length > 0 && (
              <div className="sys-subblock">
                <h3 className="sys-subtitle">可选依赖</h3>
                <div className="sys-checks">
                  {deps.map((dep) => (
                    <div key={dep.name} className="sys-check">
                      <span className={'sys-badge ' + (dep.installed ? 'sys-badge--ok' : 'sys-badge--warn')}>
                        {dep.installed ? '已装' : '未装'}
                      </span>
                      <span className="sys-check-name">{dep.purpose}</span>
                      <span className="sys-check-detail">{dep.name}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {(doctor.hints ?? []).length > 0 && (
              <div className="sys-subblock">
                <h3 className="sys-subtitle">下一步可以做什么</h3>
                <ul className="sys-hints">
                  {(doctor.hints ?? []).map((hint, index) => <li key={index}>{hint}</li>)}
                </ul>
              </div>
            )}
            {doctor.network_note && <p className="sys-hint">{doctor.network_note}</p>}
          </>
        )}
      </section>

      {/* ── 审计与导出 ───────────────────────────────────────────── */}
      <section className="sys-section">
        <div className="sys-section-head">
          <div>
            <h2 className="text-[14px] font-semibold text-ink">审计与导出</h2>
            <p className="sys-hint">
              选一个会话，把审计记录 / 对话 / 执行计划下载到本地。文件由浏览器生成，不上传任何数据。
            </p>
          </div>
        </div>

        {sessionsError && <p className="sys-error" role="alert">会话列表加载失败：{sessionsError}</p>}
        <div className="sys-export">
          <label className="sys-field" htmlFor="sys-export-task">
            <span>会话</span>
            <select
              id="sys-export-task"
              className="sys-select sys-select--wide"
              value={taskId}
              onChange={(e) => { setTaskId(e.target.value); setExportNotice(null); setExportError(null) }}
            >
              {sessions.length === 0 && <option value="">（还没有会话）</option>}
              {sessions.map((s) => (
                <option key={s.id} value={s.id}>{s.title || '（空对话）'} · {s.id.slice(0, 8)}</option>
              ))}
            </select>
          </label>
          <div className="sys-export-actions">
            {EXPORT_KINDS.map((item) => (
              <Button
                key={item.kind}
                size="sm"
                variant="outline"
                title={item.hint}
                disabled={!taskId || exportBusy !== null}
                onClick={() => void runExport(item.kind)}
              >
                {exportBusy === item.kind ? '生成中…' : item.label}
              </Button>
            ))}
          </div>
        </div>
        {exportError && <p className="sys-error" role="alert">导出失败：{exportError}</p>}
        {exportNotice && <p className="sys-notice" role="status">{exportNotice}</p>}
        <p className="sys-hint">
          审计是每次工具调用与审批的流水；对话与执行计划来自会话详情。
          「请求日志」目前只有服务端日志（方法 / 路径 / 状态码 / 耗时 / 请求 id），
          没有持久化查询接口，因此这一页不提供。
        </p>
      </section>
    </div>
  )
}
