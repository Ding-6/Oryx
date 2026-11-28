/**
 * 设置里的「模型服务」页 —— 列表 / 新增 / 编辑 / 激活 / 删除 / 本机模型。
 *
 * 三条交互底线（对应 providers.ts 里的纯函数）：
 *  · 新增同名不覆盖，提示去编辑；
 *  · 编辑时密钥留空 = 不动原来的；
 *  · 「激活」只是记成那一类的默认，实际用谁由计算模式决定 —— 一定要说清。
 */

import { useEffect, useState, type FormEvent } from 'react'

import { Button } from '@/components/ui/button'
import { ApiError } from '@/lib/api'
import type { AppState } from '@/lib/types'
import {
  activateProvider,
  createProvider,
  deleteProvider,
  fetchLocalModels,
  fetchProviders,
  registerLocalModel,
  updateProvider,
} from './api'
import {
  PROVIDER_TYPES,
  activationNotice,
  capabilityBadges,
  createBody,
  editBody,
  emptyForm,
  formatBytes,
  formFrom,
  groupProviders,
  hasChanges,
  keyStatus,
  providerBadges,
  validateForm,
  type LocalModel,
  type ProviderForm,
  type ProviderItem,
  type ProvidersResponse,
} from './providers'

export default function ProviderPanel({
  state,
  onStateChange,
}: {
  state: AppState | null
  /** 激活之后刷新 `/api/state`，顶栏的模型服务要跟着变。 */
  onStateChange: () => Promise<void> | void
}) {
  const [data, setData] = useState<ProvidersResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)

  const [localModels, setLocalModels] = useState<LocalModel[] | null>(null)
  const [localBase, setLocalBase] = useState('')
  const [localLoading, setLocalLoading] = useState(false)
  const [localBusy, setLocalBusy] = useState<string | null>(null)
  const [localError, setLocalError] = useState<string | null>(null)

  const reload = async () => {
    setLoading(true)
    setError(null)
    try {
      setData(await fetchProviders())
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : cause instanceof Error ? cause.message : '读取模型服务失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void reload()
  }, [])

  const refreshStateQuietly = async () => {
    // 顶栏慢一拍不算失败，别把它显示成「操作失败」。
    try {
      await onStateChange()
    } catch {
      /* 下一轮 /api/state 会补上 */
    }
  }

  const names = (data?.items ?? []).map((item) => item.name)

  const onSaved = async (message: string) => {
    setAdding(false)
    setEditing(null)
    setNotice(message)
    await reload()
    await refreshStateQuietly()
  }

  const activate = async (item: ProviderItem) => {
    if (busy) return
    setBusy(item.name)
    setError(null)
    setNotice(null)
    try {
      await activateProvider(item.name)
      setNotice(activationNotice(item, state?.mode))
      await reload()
      await refreshStateQuietly()
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : cause instanceof Error ? cause.message : '激活失败')
    } finally {
      setBusy(null)
    }
  }

  const remove = async (item: ProviderItem) => {
    if (busy) return
    setBusy(item.name)
    setError(null)
    setNotice(null)
    try {
      await deleteProvider(item.name)
      setConfirmDelete(null)
      setNotice(`已删除「${item.name}」`)
      await reload()
      await refreshStateQuietly()
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : cause instanceof Error ? cause.message : '删除失败')
    } finally {
      setBusy(null)
    }
  }

  const scanLocal = async () => {
    setLocalLoading(true)
    setLocalError(null)
    try {
      const out = await fetchLocalModels()
      setLocalModels(out.models)
      setLocalBase(out.base_url)
    } catch (cause) {
      setLocalModels([])
      setLocalError(cause instanceof ApiError ? cause.message : cause instanceof Error ? cause.message : '探测本机模型失败')
    } finally {
      setLocalLoading(false)
    }
  }

  const useLocalModel = async (model: LocalModel) => {
    if (localBusy) return
    setLocalBusy(model.name)
    setLocalError(null)
    setNotice(null)
    try {
      let providerName = model.provider
      if (!providerName) {
        const registered = await registerLocalModel(model.name, localBase)
        providerName = registered.name
      }
      await activateProvider(providerName)
      setNotice(
        String(state?.mode || '') === 'local'
          ? `已切到本机模型「${model.name}」`
          : `已登记「${model.name}」为本地默认服务；当前是效率模式，切到本地模式后才会实际使用它`,
      )
      await reload()
      await refreshStateQuietly()
      // 重新扫描，让「已登记为 …」那一列跟着更新
      await scanLocal()
    } catch (cause) {
      setLocalError(cause instanceof ApiError ? cause.message : cause instanceof Error ? cause.message : '切换本机模型失败')
    } finally {
      setLocalBusy(null)
    }
  }

  const groups = groupProviders(data?.items ?? [])
  const modeLabel = state?.mode === 'local' ? '本地模式' : '效率模式'

  return (
    <div className="pv">
      <div className="pv-bar">
        <div className="min-w-0">
          <p className="pv-title">模型服务</p>
          <p className="pv-sub">
            当前生效：<strong>{state?.provider || data?.active || '未配置'}</strong>
            {state?.model ? ` · ${state.model}` : ''} · {modeLabel}
          </p>
        </div>
        <div className="pv-bar-actions">
          <Button size="sm" variant="outline" disabled={loading} onClick={() => void reload()}>
            {loading ? '读取中…' : '刷新'}
          </Button>
          <Button
            size="sm"
            disabled={adding}
            onClick={() => { setAdding(true); setEditing(null); setNotice(null) }}
          >
            新增
          </Button>
        </div>
      </div>

      {notice && <p className="pref-notice" role="status">{notice}</p>}
      {error && (
        <div className="pref-error" role="alert">
          <span>{error}</span>
          <Button size="sm" variant="outline" onClick={() => void reload()}>重试</Button>
        </div>
      )}

      {adding && (
        <ProviderEditor
          initial={emptyForm()}
          existingNames={names}
          submitLabel="添加"
          onCancel={() => setAdding(false)}
          onSaved={() => onSaved('已新增模型服务')}
        />
      )}

      {loading && !data ? (
        <p className="pv-muted">正在读取模型服务…</p>
      ) : !data || data.items.length === 0 ? (
        <div className="pv-empty">
          <p>还没有配置任何模型服务。没有它，新建运行会直接失败。</p>
          <p className="pv-muted">你可以新增一条云端服务（OpenAI 兼容 / Anthropic），或先在下方扫描本机 Ollama 模型。</p>
        </div>
      ) : (
        <>
          <ProviderGroup
            title="云端服务"
            items={groups.cloud}
            data={data}
            busy={busy}
            editing={editing}
            confirmDelete={confirmDelete}
            names={names}
            onEdit={setEditing}
            onCancelEdit={() => setEditing(null)}
            onActivate={activate}
            onAskDelete={setConfirmDelete}
            onDelete={remove}
            onSaved={onSaved}
          />
          <ProviderGroup
            title="本地服务"
            items={groups.local}
            data={data}
            busy={busy}
            editing={editing}
            confirmDelete={confirmDelete}
            names={names}
            onEdit={setEditing}
            onCancelEdit={() => setEditing(null)}
            onActivate={activate}
            onAskDelete={setConfirmDelete}
            onDelete={remove}
            onSaved={onSaved}
          />
          {groups.cloud.length === 0 && groups.local.length === 0 && (
            <p className="pv-muted">列表里暂时没有可用服务。</p>
          )}
        </>
      )}

      <div className="pv-local">
        <div className="pv-local-head">
          <div className="min-w-0">
            <p className="pv-title">本机模型</p>
            <p className="pv-sub">从 Ollama 读取已安装的模型；选中后自动登记并切换。</p>
          </div>
          <Button size="sm" variant="outline" disabled={localLoading} onClick={() => void scanLocal()}>
            {localLoading ? '正在探测…' : '扫描本机模型'}
          </Button>
        </div>
        {localError && <p className="pref-error" role="alert">{localError}</p>}
        {localModels === null ? (
          <p className="pv-muted">点「扫描本机模型」看看这台机器上装了什么。</p>
        ) : localModels.length === 0 ? (
          <p className="pv-muted">没有探测到本机模型服务。装好并启动 Ollama 后再扫描。</p>
        ) : (
          <ul className="pv-local-list">
            {localModels.map((model) => (
              <li key={model.name} className="pv-local-item">
                <div className="min-w-0">
                  <strong className="pv-local-name">{model.name}</strong>
                  <span className="pv-muted">
                    {' '}· {model.parameter_size || '—'} · {model.quantization || '—'} · {formatBytes(model.size)}
                  </span>
                </div>
                <span className="pv-muted">
                  {model.provider ? `已登记为 ${model.provider}` : '未登记'}
                </span>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={localBusy !== null}
                  onClick={() => void useLocalModel(model)}
                >
                  {localBusy === model.name ? '切换中…' : model.provider ? '使用' : '登记并使用'}
                </Button>
              </li>
            ))}
          </ul>
        )}
        {localBase && <p className="pv-hint">探测地址：{localBase}</p>}
      </div>
    </div>
  )
}

function ProviderGroup({
  title,
  items,
  data,
  busy,
  editing,
  confirmDelete,
  names,
  onEdit,
  onCancelEdit,
  onActivate,
  onAskDelete,
  onDelete,
  onSaved,
}: {
  title: string
  items: ProviderItem[]
  data: ProvidersResponse
  busy: string | null
  editing: string | null
  confirmDelete: string | null
  names: string[]
  onEdit: (name: string | null) => void
  onCancelEdit: () => void
  onActivate: (item: ProviderItem) => void
  onAskDelete: (name: string | null) => void
  onDelete: (item: ProviderItem) => void
  onSaved: (message: string) => Promise<void>
}) {
  if (items.length === 0) return null
  return (
    <section className="pv-group">
      <h3 className="pv-group-title">{title}<span className="pv-count">{items.length}</span></h3>
      <div className="pv-list">
        {items.map((item) => {
          if (editing === item.name) {
            return (
              <ProviderEditor
                key={item.name}
                initial={formFrom(item)}
                original={item}
                existingNames={names}
                submitLabel="保存"
                onCancel={onCancelEdit}
                onSaved={() => onSaved(`已保存「${item.name}」`)}
              />
            )
          }
          const key = keyStatus(item)
          const badges = providerBadges(item, data)
          const isActive = item.name === data.active
          return (
            <article key={item.name} className="pv-item">
              <div className="pv-item-main">
                <div className="pv-item-title">
                  <strong>{item.name}</strong>
                  <span className="pv-type">{item.type}</span>
                  {item.is_local && <span className="pv-tag">本地</span>}
                  {badges.map((badge) => (
                    <span key={badge} className={'pv-badge' + (badge === '当前生效' ? ' pv-badge--on' : '')}>
                      {badge}
                    </span>
                  ))}
                </div>
                <div className="pv-line"><span className="pv-k">模型</span><code>{item.model || '—'}</code></div>
                <div className="pv-line"><span className="pv-k">地址</span><code title={item.base_url}>{item.base_url || '—'}</code></div>
                <div className="pv-line">
                  <span className="pv-k">密钥</span>
                  <span className={key.ok ? 'pv-ok' : 'pv-muted'}>{key.label}</span>
                </div>
                <div className="pv-caps">
                  {capabilityBadges(item.capabilities).map((cap) => (
                    <span key={cap} className="pv-cap">{cap}</span>
                  ))}
                </div>
              </div>
              <div className="pv-item-actions">
                {!isActive && (
                  <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => onActivate(item)}>
                    {busy === item.name ? '切换中…' : '激活'}
                  </Button>
                )}
                <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => onEdit(item.name)}>
                  编辑
                </Button>
                {confirmDelete === item.name ? (
                  <>
                    <Button size="sm" variant="danger" disabled={busy !== null} onClick={() => onDelete(item)}>
                      {busy === item.name ? '删除中…' : '确认删除'}
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => onAskDelete(null)}>取消</Button>
                  </>
                ) : (
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy !== null || isActive}
                    title={isActive ? '正在使用，先切到别的服务再删' : undefined}
                    onClick={() => onAskDelete(item.name)}
                  >
                    删除
                  </Button>
                )}
              </div>
            </article>
          )
        })}
      </div>
    </section>
  )
}

function ProviderEditor({
  initial,
  original,
  existingNames,
  submitLabel,
  onCancel,
  onSaved,
}: {
  initial: ProviderForm
  /** 有值 = 编辑；没有 = 新增。 */
  original?: ProviderItem
  existingNames: string[]
  submitLabel: string
  onCancel: () => void
  onSaved: () => Promise<void>
}) {
  const [form, setForm] = useState<ProviderForm>(initial)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const set = (key: keyof ProviderForm, value: string) => {
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (busy) return
    const invalid = validateForm(form, existingNames, original?.name)
    if (invalid) {
      setError(invalid)
      return
    }
    if (original && !hasChanges(original, form)) {
      setError('没有改动')
      return
    }
    setBusy(true)
    setError(null)
    try {
      if (original) {
        await updateProvider(original.name, editBody(original, form))
      } else {
        await createProvider(createBody(form))
      }
      await onSaved()
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : cause instanceof Error ? cause.message : '保存失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="pv-form" onSubmit={submit}>
      <div className="pv-form-grid">
        <label className="pv-field">
          <span>名称 <span className="text-danger">*</span></span>
          <input
            value={form.name}
            onChange={(e) => set('name', e.target.value)}
            disabled={busy || Boolean(original)}
            placeholder="例如 deepseek / 本机-qwen"
            autoFocus={!original}
          />
        </label>
        <label className="pv-field">
          <span>类型</span>
          <select value={form.type} onChange={(e) => set('type', e.target.value)} disabled={busy}>
            {PROVIDER_TYPES.map((type) => <option key={type.value} value={type.value}>{type.label}</option>)}
          </select>
        </label>
        <label className="pv-field pv-field--wide">
          <span>接口地址 <span className="text-danger">*</span></span>
          <input
            value={form.base_url}
            onChange={(e) => set('base_url', e.target.value)}
            disabled={busy}
            placeholder="https://api.deepseek.com/v1"
          />
        </label>
        <label className="pv-field">
          <span>模型名 <span className="text-danger">*</span></span>
          <input
            value={form.model}
            onChange={(e) => set('model', e.target.value)}
            disabled={busy}
            placeholder="deepseek-chat"
          />
        </label>
        <label className="pv-field">
          <span>API 密钥</span>
          <input
            type="password"
            value={form.api_key}
            onChange={(e) => set('api_key', e.target.value)}
            disabled={busy}
            autoComplete="new-password"
            placeholder={original?.has_key ? '已保存，留空表示不改' : '密钥会存进系统凭据库'}
          />
        </label>
      </div>
      {original && (
        <p className="pv-hint">
          名称是配置键，编辑时不能改。密钥不回显；留空就保持原来那份。
        </p>
      )}
      {error && <p className="pref-error" role="alert">{error}</p>}
      <div className="pv-form-foot">
        <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={onCancel}>取消</Button>
        <Button type="submit" size="sm" disabled={busy}>{busy ? '保存中…' : submitLabel}</Button>
      </div>
    </form>
  )
}
