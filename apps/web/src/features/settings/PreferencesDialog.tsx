import { useEffect, useRef, useState, type ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { ApiError } from '@/lib/api'
import type { AppState } from '@/lib/types'
import ProviderPanel from './ProviderPanel'
import AppearancePanel from './AppearancePanel'
import FeaturePanel from './FeaturePanel'
import MemoryPanel from './MemoryPanel'
import AccountPanel from './AccountPanel'
import type { AccountUser } from '@/features/account/AuthScreen'
import { setComputeMode, setInteraction, setStyle } from './api'
import type { AppearanceState } from './settingsOps'
import {
  FALLBACK,
  catalogReady,
  interactionFlags,
  isElevated,
  labelFor,
  type InteractionOption,
  type PreferenceCatalog,
} from './preferences'

type Field = 'interaction' | 'style' | 'mode'
type Tab = 'prefs' | 'providers' | 'appearance' | 'features' | 'memory' | 'account'

const TAB_META: Record<Tab, { title: string; hint: string; foot: string }> = {
  prefs: {
    title: '对话设置',
    hint: '这里是全局默认；新建运行时可以单独覆盖交互模式与回答风格。',
    foot: '切换后立即保存，下一轮对话生效',
  },
  providers: {
    title: '模型服务',
    hint: '配置模型服务、切换当前使用的那一个；密钥只写入，不回显。',
    foot: '模型服务的改动会立即保存',
  },
  appearance: {
    title: '外观',
    hint: '主题、背景填充与背景图；改动会立刻作用到整个界面。',
    foot: '外观设置保存在本机，刷新页面后仍然生效',
  },
  features: {
    title: '特性开关',
    hint: '占位开关不代表已有这个能力；运行时调整关闭后需改配置文件。',
    foot: '开关写在本机 features 配置文件里，改动立即保存',
  },
  memory: {
    title: '长期记忆',
    hint: '记下来的内容按账号隔离，后续对话可以取用。',
    foot: '记忆保存在本机数据库，删除后不可恢复',
  },
  account: {
    title: '账号',
    hint: '查看账号信息、修改密码或退出登录；改密码会让其它设备的登录立即失效。',
    foot: '密码修改后立即生效；本机没有角色体系',
  },
}

const TABS: Array<{ key: Tab; label: string }> = [
  { key: 'prefs', label: '对话偏好' },
  { key: 'providers', label: '模型服务' },
  { key: 'appearance', label: '外观' },
  { key: 'features', label: '特性开关' },
  { key: 'memory', label: '长期记忆' },
  { key: 'account', label: '账号' },
]

/**
 * 顶栏「设置」弹层 —— 三个全局偏好的切换入口。
 *
 * 三个下拉全部受控于 `/api/state` 的当前值：切失败时不改 state，
 * 选择器会自动弹回原值，不会留下「显示已切、实际没切」的假状态。
 */
export default function PreferencesDialog({
  open,
  onClose,
  state,
  catalog,
  catalogLoading,
  catalogError,
  onReloadCatalog,
  onStateChange,
  onAppearanceChange,
  user,
  onLogout,
}: {
  open: boolean
  onClose: () => void
  state: AppState | null
  catalog: PreferenceCatalog | null
  catalogLoading: boolean
  catalogError: string | null
  onReloadCatalog: () => void
  onStateChange: () => Promise<void> | void
  /** 外观改动后由 App 立刻应用到 DOM —— 只存盘不应用会是假功能。 */
  onAppearanceChange: (next: AppearanceState) => void
  /** 账号页：当前登录用户 + 由 App 统一清态的退出入口。 */
  user: AccountUser | null
  onLogout: () => Promise<void> | void
}) {
  const [busy, setBusy] = useState<Field | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>('prefs')
  const dialogRef = useRef<HTMLDivElement>(null)
  // 外层每次渲染都会传新的 onClose / onStateChange。存 ref，
  // 否则父组件一刷新（切完偏好就会刷新）effect 重跑，把刚显示的
  // 成功提示又清空了。
  const onCloseRef = useRef(onClose)
  useEffect(() => { onCloseRef.current = onClose }, [onClose])

  useEffect(() => {
    if (!open) return
    setError(null)
    setNotice(null)
    dialogRef.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCloseRef.current()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  if (!open) return null

  const apply = async (field: Field, value: string) => {
    if (busy) return
    setBusy(field)
    setError(null)
    setNotice(null)
    try {
      if (field === 'interaction') {
        await setInteraction(value)
        setNotice(`交互模式已切到「${labelFor(catalog?.interactions, value, value)}」`)
      } else if (field === 'style') {
        await setStyle(value)
        setNotice(`回答风格已切到「${labelFor(catalog?.styles, value, value)}」`)
      } else {
        const result = await setComputeMode(value)
        const provider = result.provider ? ` · 模型服务 ${result.provider}` : ''
        setNotice(`已切到「${labelFor(catalog?.compute_modes, value, value)}」${provider}`)
      }
      // 后端已经保存了。刷新首屏状态失败只是「顶栏慢一拍」，
      // 不能把它显示成「切换失败」—— 那会让用户反复点同一个选项。
      try {
        await onStateChange()
      } catch {
        setNotice((prev) => `${prev ?? '已保存'}（状态刷新失败，稍后会自动更新）`)
      }
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : cause instanceof Error ? cause.message : '切换失败，请稍后重试')
    } finally {
      setBusy(null)
    }
  }

  const interaction = state?.interaction || FALLBACK.interaction
  const style = state?.style || FALLBACK.style
  const mode = state?.mode || FALLBACK.mode
  const selectedInteraction = catalog?.interactions.find((x) => x.name === interaction)
  const ready = catalogReady(catalog)
  const meta = TAB_META[tab]

  return (
    <div
      className="pref-overlay"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="pref-title"
        tabIndex={-1}
        className={'pref-dialog' + (tab === 'prefs' ? '' : ' pref-dialog--wide')}
      >
        <header className="pref-head">
          <div>
            <h2 id="pref-title" className="text-[15px] font-semibold text-ink">{meta.title}</h2>
            <p className="mt-0.5 text-[11.5px] text-muted">{meta.hint}</p>
          </div>
          <Button size="sm" variant="ghost" onClick={onClose} aria-label="关闭设置">关闭</Button>
        </header>

        <div className="pref-tabs" role="tablist" aria-label="设置分类">
          {TABS.map((item) => (
            <button
              key={item.key}
              type="button"
              role="tab"
              aria-selected={tab === item.key}
              className={'pref-tab' + (tab === item.key ? ' pref-tab--on' : '')}
              onClick={() => setTab(item.key)}
            >
              {item.label}
            </button>
          ))}
        </div>

        <div className="pref-body">
          {tab === 'prefs' ? (
            <>
          {catalogLoading && <p className="text-[12px] text-muted">正在读取可选模式…</p>}
          {!catalogLoading && catalogError && (
            <div className="pref-error" role="alert">
              <span>偏好目录加载失败：{catalogError}</span>
              <Button size="sm" variant="outline" onClick={onReloadCatalog}>重试</Button>
            </div>
          )}
          {!catalogLoading && !catalogError && !ready && (
            <div className="pref-error" role="alert">
              <span>后端没有返回可选的偏好目录，暂时无法切换。</span>
              <Button size="sm" variant="outline" onClick={onReloadCatalog}>重新加载</Button>
            </div>
          )}

          {error && <p className="pref-error" role="alert">{error}</p>}
          {notice && <p className="pref-notice" role="status">{notice}</p>}

          {ready && (
            <>
              <Field
                id="pref-interaction"
                label="交互模式"
                value={interaction}
                disabled={busy !== null}
                onChange={(v) => void apply('interaction', v)}
                options={(catalog?.interactions ?? []).map((x) => ({ name: x.name, label: x.label }))}
                hint={selectedInteraction?.hint ?? ''}
                flags={selectedInteraction ? <InteractionFlags item={selectedInteraction} /> : null}
              />
              <p className="pref-tip">
                决定「这一轮允许 Agent 做什么」，是代码强制的能力闸门；问答 / 规划只给只读工具。
              </p>

              <Field
                id="pref-style"
                label="回答风格"
                value={style}
                disabled={busy !== null}
                onChange={(v) => void apply('style', v)}
                options={(catalog?.styles ?? []).map((x) => ({ name: x.name, label: x.label }))}
                hint={catalog?.styles.find((x) => x.name === style)?.hint ?? ''}
              />
              <p className="pref-tip">只影响「怎么说」，不改变工具权限与安全策略。</p>

              <Field
                id="pref-mode"
                label="计算模式"
                value={mode}
                disabled={busy !== null}
                onChange={(v) => void apply('mode', v)}
                options={(catalog?.compute_modes ?? []).map((x) => ({ name: x.name, label: x.label }))}
                hint={catalog?.compute_modes.find((x) => x.name === mode)?.hint ?? ''}
              />
              <p className="pref-tip">这是全局模型路由：切换会同时换到同类模型服务，不能按单次运行覆盖。</p>

              <div className="pref-info">
                <p className="font-medium text-ink-2">当前模型服务</p>
                <p className="mt-1 font-mono text-[12px] text-ink">
                  {state?.provider || '（未配置）'}
                  {state?.model ? ` · ${state.model}` : ''}
                </p>
                <p className="mt-1 text-[11.5px] text-muted">
                  {state?.provider_is_local ? '本地模型' : '云端模型'}
                  {state?.kernel_ready === false ? ' · 内核启动中' : ''}
                  {state?.busy ? ' · 有运行在进行中，切换会在下一轮生效' : ''}
                </p>
                <p className="mt-1.5 text-[11.5px] text-muted">
                  需要新增、编辑或切换模型服务？到上面的「模型服务」标签页。
                </p>
              </div>
            </>
          )}
            </>
          ) : tab === 'providers' ? (
            <ProviderPanel state={state} onStateChange={onStateChange} />
          ) : tab === 'appearance' ? (
            <AppearancePanel onAppearanceChange={onAppearanceChange} />
          ) : tab === 'features' ? (
            <FeaturePanel />
          ) : tab === 'memory' ? (
            <MemoryPanel />
          ) : (
            <AccountPanel user={user} onLogout={onLogout} />
          )}
        </div>

        <footer className="pref-foot">
          <span className="text-[11.5px] text-muted">{meta.foot}</span>
          <Button size="sm" onClick={onClose}>完成</Button>
        </footer>
      </div>
    </div>
  )
}

function InteractionFlags({ item }: { item: InteractionOption }) {
  const flags = interactionFlags(item)
  if (flags.length === 0) return null
  return (
    <span className="pref-flags">
      {flags.map((f) => (
        <span key={f} className={'pref-flag' + (isElevated(item) ? ' pref-flag--warn' : '')}>{f}</span>
      ))}
    </span>
  )
}

function Field({
  id, label, value, options, hint, flags, disabled, onChange,
}: {
  id: string
  label: string
  value: string
  options: Array<{ name: string; label: string }>
  hint: string
  flags?: ReactNode
  disabled: boolean
  onChange: (value: string) => void
}) {
  return (
    <label className="pref-field" htmlFor={id}>
      <span className="pref-label">
        {label}
        {flags}
      </span>
      <select
        id={id}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        className="pref-select"
      >
        {options.map((x) => <option key={x.name} value={x.name}>{x.label}</option>)}
      </select>
      {hint && <span className="pref-hint">{hint}</span>}
    </label>
  )
}
