import { useEffect, useMemo, useState, type FormEvent } from 'react'

import { Button } from '@/components/ui/button'
import {
  createAgent,
  createExpert,
  deleteAgent,
  deleteExpert,
  fetchAgentCatalog,
  listAgents,
  listExperts,
  updateAgent,
  updateExpert,
  type AgentDefinition,
  type ExpertDefinition,
} from './api'
import {
  catalogHint,
  kindOptions,
  modelOptions,
  permissionOptions,
  readonlyReason,
  skillOptions,
  toggleName,
  toolOptions,
  unknownSelections,
  validateAgentDraft,
  withUnknownValues,
  type AgentCatalog,
  type CandidateOption,
} from './agentCatalog'

type Kind = 'agent' | 'expert'
type Asset = AgentDefinition | ExpertDefinition
type Draft = Record<string, string>

const listText = (value: unknown) => (Array.isArray(value) ? value.join(', ') : '')
const parseList = (value: string) => value.split(/[,，\n]/).map((part) => part.trim()).filter(Boolean)

function toDraft(kind: Kind, item?: Asset): Draft {
  if (kind === 'agent') {
    const agent = item as AgentDefinition | undefined
    return {
      id: agent?.id ?? '', name: agent?.name ?? '', description: agent?.description ?? '',
      tools: listText(agent?.tools), max_turns: String(agent?.max_turns ?? 6),
      model: agent?.model ?? 'inherit', permission_mode: agent?.permission_mode ?? 'inherit',
      skills: listText(agent?.skills), disallowed_tools: listText(agent?.disallowed_tools),
      memory: agent?.memory ?? '', initial_prompt: agent?.initial_prompt ?? '',
      icon: agent?.icon ?? '', subtitle: agent?.subtitle ?? '', kind: agent?.kind ?? 'agent',
      body: agent?.body ?? '',
    }
  }
  const expert = item as ExpertDefinition | undefined
  return {
    id: expert?.id ?? '', name: expert?.name ?? '', description: expert?.description ?? '',
    scenarios: listText(expert?.scenarios), tags: listText(expert?.tags),
    personality: expert?.personality ?? '', speaking_style: expert?.speaking_style ?? '',
    use_cases: expert?.use_cases ?? '', self_intro: expert?.self_intro ?? '', body: expert?.body ?? '',
  }
}

export default function AgentManagement() {
  const [kind, setKind] = useState<Kind>('agent')
  const [agents, setAgents] = useState<AgentDefinition[]>([])
  const [experts, setExperts] = useState<ExpertDefinition[]>([])
  const [catalog, setCatalog] = useState<AgentCatalog | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [draft, setDraft] = useState<Draft>(() => toDraft('agent'))
  const [creating, setCreating] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  // 候选目录只拉一次，失败也不拖垮整个页面 —— 已保存的值照常显示，
  // 保存时交给后端校验（内核没起来时后端也会跳过拿不到的那一项）。
  useEffect(() => {
    let alive = true
    fetchAgentCatalog()
      .then((next) => { if (alive) setCatalog(next) })
      .catch(() => { if (alive) setCatalog(null) })
    return () => { alive = false }
  }, [])

  const items = kind === 'agent' ? agents : experts
  const selected = useMemo(() => items.find((item) => item.id === selectedId) ?? null, [items, selectedId])
  const editable = creating || selected?.can_edit === true

  const reload = async (preserveId?: string | null) => {
    setLoading(true)
    setError(null)
    try {
      const [nextAgents, nextExperts] = await Promise.all([listAgents(), listExperts()])
      setAgents(nextAgents)
      setExperts(nextExperts)
      const currentItems = kind === 'agent' ? nextAgents : nextExperts
      const nextId = preserveId && currentItems.some((item) => item.id === preserveId)
        ? preserveId
        : currentItems[0]?.id ?? null
      setSelectedId(nextId)
      setCreating(false)
      setDraft(toDraft(kind, currentItems.find((item) => item.id === nextId)))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '加载共享定义失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void reload() }, [kind])

  const choose = (item: Asset) => {
    setCreating(false)
    setSelectedId(item.id)
    setDraft(toDraft(kind, item))
    setError(null)
    setNotice(null)
  }

  const startCreate = () => {
    setCreating(true)
    setSelectedId(null)
    setDraft(toDraft(kind))
    setError(null)
    setNotice(null)
  }

  const set = (key: string, value: string) => setDraft((current) => ({ ...current, [key]: value }))

  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!draft.name.trim() || (creating && !draft.id.trim())) {
      setError(creating ? '请填写 ID 和名称' : '请填写名称')
      return
    }
    if (kind === 'agent') {
      // 本地先拦一道，错误来得比一次网络往返更早；规则与后端同一套。
      const issue = validateAgentDraft({
        id: draft.id,
        name: draft.name,
        tools: parseList(draft.tools),
        disallowed_tools: parseList(draft.disallowed_tools),
        skills: parseList(draft.skills),
        model: draft.model,
        permission_mode: draft.permission_mode,
        kind: draft.kind,
        max_turns: Number(draft.max_turns || 6),
      }, catalog, creating)
      if (issue) {
        setError(issue.message)
        return
      }
    }
    setSaving(true)
    setError(null)
    setNotice(null)
    try {
      if (kind === 'agent') {
        const payload = {
          ...draft,
          tools: parseList(draft.tools),
          skills: parseList(draft.skills),
          disallowed_tools: parseList(draft.disallowed_tools),
          max_turns: Number(draft.max_turns || 6),
        }
        if (creating) await createAgent(payload)
        else if (selectedId) await updateAgent(selectedId, payload)
      } else {
        const payload = {
          ...draft,
          scenarios: parseList(draft.scenarios),
          tags: parseList(draft.tags),
        }
        if (creating) await createExpert(payload)
        else if (selectedId) await updateExpert(selectedId, payload)
      }
      setNotice(creating ? '已创建并共享' : '已保存')
      await reload(draft.id.trim())
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '保存失败，请稍后重试')
    } finally {
      setSaving(false)
    }
  }

  const remove = async () => {
    if (!selectedId || !window.confirm(`确定删除「${draft.name}」吗？`)) return
    setSaving(true)
    setError(null)
    try {
      if (kind === 'agent') await deleteAgent(selectedId)
      else await deleteExpert(selectedId)
      setNotice('已删除')
      await reload(null)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '删除失败')
    } finally {
      setSaving(false)
    }
  }

  const hint = kind === 'agent' ? catalogHint(catalog) : null

  return (
    <div className="mx-auto w-full max-w-6xl space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-[12px] font-medium text-accent">平台能力 · 共享定义</p>
          <h1 className="mt-1 text-xl font-semibold text-ink">Agent 与专家人设</h1>
          <p className="mt-1 text-[13px] text-muted">所有账号可查看；定义创建者可编辑和删除。</p>
        </div>
        <Button onClick={startCreate}>新建{kind === 'agent' ? '子 Agent' : '专家人设'}</Button>
      </header>

      <div className="flex gap-2 border-b border-line pb-2">
        <Button size="sm" variant={kind === 'agent' ? 'default' : 'ghost'} onClick={() => setKind('agent')}>
          子 Agent <span className="ml-1 opacity-75">{agents.length}</span>
        </Button>
        <Button size="sm" variant={kind === 'expert' ? 'default' : 'ghost'} onClick={() => setKind('expert')}>
          专家人设 <span className="ml-1 opacity-75">{experts.length}</span>
        </Button>
      </div>

      {error && <div role="alert" className="rounded-[var(--r-lg)] border border-danger/40 bg-danger-soft p-3 text-[13px] text-danger">{error}</div>}
      {notice && <div role="status" className="rounded-[var(--r-lg)] border border-ok/30 bg-ok-soft p-3 text-[13px] text-ok">{notice}</div>}

      <div className="grid min-h-[520px] gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
        <aside className="rounded-[var(--r-lg)] border border-line bg-card p-2">
          <div className="px-2 py-2 text-[11.5px] font-medium text-muted">共享目录</div>
          {loading ? <p className="px-2 py-4 text-[13px] text-muted">正在加载…</p> : items.length === 0 && !creating ? (
            <div className="px-2 py-5 text-[13px] text-muted">还没有定义，创建后会立即对所有账号可见。</div>
          ) : items.map((item) => (
            <button key={item.id} type="button" onClick={() => choose(item)}
              className={'mb-1 w-full rounded-md px-2.5 py-2 text-left transition-colors ' + (selectedId === item.id ? 'bg-accent-soft text-accent' : 'text-ink-2 hover:bg-panel')}>
              <span className="block truncate text-[13px] font-medium">{item.name}</span>
              <span className="mt-0.5 block truncate text-[11.5px] text-muted">{item.description || item.id}</span>
              <span className="mt-1 inline-flex rounded-full bg-panel-2 px-2 py-0.5 text-[10.5px] text-muted">
                {item.can_edit ? '我创建的 · 可编辑'
                  : item.creator_id ? '共享只读' : '内置 · 只读'}
              </span>
            </button>
          ))}
        </aside>

        <section className="rounded-[var(--r-lg)] border border-line bg-card p-4 sm:p-5">
          {!creating && !selected ? (
            <div className="grid h-full min-h-[360px] place-items-center text-center text-[13px] text-muted">
              <div><div className="text-2xl">✦</div><p className="mt-2">选择一个定义查看或配置，或新建共享能力。</p></div>
            </div>
          ) : (
            <form onSubmit={save} className="space-y-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h2 className="text-[16px] font-semibold text-ink">{creating ? '创建共享定义' : draft.name || '定义详情'}</h2>
                  <p className="mt-1 text-[12px] text-muted">
                    {creating ? '创建后所有账号都能在运行时选择。' : readonlyReason(selected)}
                  </p>
                </div>
                {selected && editable && <Button type="button" variant="danger" size="sm" disabled={saving} onClick={() => void remove()}>删除</Button>}
              </div>

              {hint && <div className="ag-hint" role="status">{hint}</div>}

              <div className="grid gap-3 sm:grid-cols-2">
                {creating && <Field label="唯一 ID" value={draft.id} onChange={(v) => set('id', v)} disabled={!editable} hint="使用英文、数字、短横线或下划线；创建后不可更改。" />}
                <Field label="显示名称" value={draft.name} onChange={(v) => set('name', v)} disabled={!editable} required />
                <Field label="简介" value={draft.description} onChange={(v) => set('description', v)} disabled={!editable} className="sm:col-span-2" />
                {kind === 'agent' ? <>
                  <MultiSelectField label="允许使用的工具" value={draft.tools} options={toolOptions(catalog)}
                    disabled={!editable} onChange={(v) => set('tools', v)}
                    hint="从候选里勾选；空白表示无工具权限。" className="sm:col-span-2" />
                  <MultiSelectField label="技能" value={draft.skills} options={skillOptions(catalog)}
                    disabled={!editable} onChange={(v) => set('skills', v)}
                    hint="从已安装的技能里勾选。" className="sm:col-span-2" />
                  <MultiSelectField label="额外禁用工具" value={draft.disallowed_tools} options={toolOptions(catalog)}
                    disabled={!editable} onChange={(v) => set('disallowed_tools', v)}
                    hint="在白名单之上再收紧一层。" className="sm:col-span-2" />
                  <Field label="最多推理轮数" type="number" value={draft.max_turns} onChange={(v) => set('max_turns', v)} disabled={!editable} />
                  <SelectField label="模型服务" value={draft.model} onChange={(v) => set('model', v)} disabled={!editable}
                    options={withUnknownValues(modelOptions(catalog), draft.model ? [draft.model] : [])}
                    hint="取值是模型服务名，不是模型 ID。" />
                  <SelectField label="权限基线" value={draft.permission_mode} onChange={(v) => set('permission_mode', v)} disabled={!editable}
                    options={withUnknownValues(permissionOptions(catalog), [draft.permission_mode])} />
                  <SelectField label="定义类型" value={draft.kind} onChange={(v) => set('kind', v)} disabled={!editable}
                    options={withUnknownValues(kindOptions(catalog), [draft.kind])} />
                  <Field label="专属记忆标签" value={draft.memory} onChange={(v) => set('memory', v)} disabled={!editable} />
                  <Field label="界面副标题" value={draft.subtitle} onChange={(v) => set('subtitle', v)} disabled={!editable} />
                  <Field label="图标" value={draft.icon} onChange={(v) => set('icon', v)} disabled={!editable} />
                  <TextField label="启动提示" value={draft.initial_prompt} onChange={(v) => set('initial_prompt', v)} disabled={!editable} className="sm:col-span-2" />
                </> : <>
                  <Field label="适用场景" value={draft.scenarios} onChange={(v) => set('scenarios', v)} disabled={!editable} hint="多个场景用逗号分隔。" />
                  <Field label="标签" value={draft.tags} onChange={(v) => set('tags', v)} disabled={!editable} />
                  <Field label="性格特征" value={draft.personality} onChange={(v) => set('personality', v)} disabled={!editable} />
                  <Field label="表达风格" value={draft.speaking_style} onChange={(v) => set('speaking_style', v)} disabled={!editable} />
                  <Field label="使用案例" value={draft.use_cases} onChange={(v) => set('use_cases', v)} disabled={!editable} className="sm:col-span-2" />
                  <Field label="自我介绍" value={draft.self_intro} onChange={(v) => set('self_intro', v)} disabled={!editable} className="sm:col-span-2" />
                </>}
                <TextField label="职责正文 / 工作规范" value={draft.body} onChange={(v) => set('body', v)} disabled={!editable} className="sm:col-span-2" rows={8} />
              </div>

              {editable && <div className="flex justify-end border-t border-line pt-3">
                <Button type="submit" disabled={saving}>{saving ? '正在保存…' : creating ? '创建并共享' : '保存修改'}</Button>
              </div>}
            </form>
          )}
        </section>
      </div>
    </div>
  )
}

function Field({
  label, value, onChange, disabled, required, type = 'text', hint, className = '',
}: {
  label: string; value: string; onChange: (value: string) => void; disabled?: boolean
  required?: boolean; type?: string; hint?: string; className?: string
}) {
  return <label className={'block space-y-1.5 ' + className}>
    <span className="text-[12px] font-medium text-ink-2">{label}{required && <span className="ml-1 text-danger">*</span>}</span>
    <input type={type} value={value} onChange={(event) => onChange(event.target.value)} disabled={disabled} required={required}
      className="h-9 w-full rounded-md border border-line-strong bg-bg px-2.5 text-[13px] text-ink outline-none focus:border-accent focus:ring-2 focus:ring-accent/20 disabled:bg-panel disabled:text-muted" />
    {hint && <span className="block text-[11px] leading-relaxed text-muted">{hint}</span>}
  </label>
}

function SelectField({ label, value, onChange, disabled, options, hint }: {
  label: string; value: string; onChange: (value: string) => void; disabled?: boolean
  options: CandidateOption[]; hint?: string
}) {
  return <label className="block space-y-1.5">
    <span className="text-[12px] font-medium text-ink-2">{label}</span>
    <select value={value} onChange={(event) => onChange(event.target.value)} disabled={disabled}
      className="h-9 w-full rounded-md border border-line-strong bg-bg px-2.5 text-[13px] text-ink outline-none focus:border-accent focus:ring-2 focus:ring-accent/20 disabled:bg-panel disabled:text-muted">
      {options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
    </select>
    {hint && <span className="block text-[11px] leading-relaxed text-muted">{hint}</span>}
  </label>
}

/**
 * 候选多选器。已选值以 chip 展示，候选面板可搜索、按组折叠；
 * **已保存但目录里找不到的值会标红保留**，绝不自动清除。
 */
function MultiSelectField({ label, value, options, disabled, hint, onChange, className = '' }: {
  label: string
  value: string
  options: CandidateOption[]
  disabled?: boolean
  hint?: string
  onChange: (value: string) => void
  className?: string
}) {
  const [query, setQuery] = useState('')
  const selected = parseList(value)
  const known = new Set(options.map((option) => option.value))
  const missing = unknownSelections(selected, [...known])
  const keyword = query.trim().toLowerCase()
  const filtered = options.filter((option) =>
    option.value.toLowerCase().includes(keyword) ||
    (option.description ?? '').toLowerCase().includes(keyword))
  const buckets = new Map<string, CandidateOption[]>()
  for (const option of filtered) {
    const key = option.group || ''
    buckets.set(key, [...(buckets.get(key) ?? []), option])
  }
  const showGroups = buckets.size > 1 || (buckets.size === 1 && [...buckets.keys()][0] !== '')
  const setList = (next: string[]) => onChange(listText(next))

  return <div className={'ag-multi ' + className}>
    <div className="ag-multi-head">
      <span className="text-[12px] font-medium text-ink-2">{label}</span>
      {hint && <span className="text-[11px] text-muted">{hint}</span>}
    </div>
    <div className="ag-chips">
      {selected.length === 0 && <span className="ag-empty">未选择</span>}
      {selected.map((name) => (
        <button key={name} type="button" disabled={disabled}
          className={'ag-chip ' + (known.has(name) ? '' : 'ag-chip--unknown')}
          title={known.has(name) ? '点击移除' : '当前值，目录里找不到 —— 已原样保留'}
          onClick={() => setList(toggleName(selected, name))}>
          {name}{!known.has(name) && <span className="ag-chip-warn">⚠</span>}
          {!disabled && <span className="ag-chip-x" aria-hidden="true">×</span>}
        </button>
      ))}
    </div>
    {!disabled && <details className="ag-picker">
      <summary>从候选里选择 / 取消</summary>
      <div className="ag-picker-body">
        <input type="text" value={query} onChange={(event) => setQuery(event.target.value)}
          placeholder="搜索候选…" className="ag-search" />
        {missing.length > 0 && <p className="ag-missing">
          已保存但目录里找不到：{missing.join('、')}（保留原值，不会自动清除）
        </p>}
        {filtered.length === 0 && <p className="ag-none">没有匹配的候选</p>}
        {[...buckets.entries()].map(([group, groupOptions]) => (
          <div key={group}>
            {showGroups && <div className="ag-group">{group || '其他'}</div>}
            {groupOptions.map((option) => (
              <label key={option.value} className="ag-option">
                <input type="checkbox" checked={selected.includes(option.value)}
                  onChange={() => setList(toggleName(selected, option.value))}
                  className="h-3.5 w-3.5 accent-[var(--accent)]" />
                <span className="ag-option-name">{option.label}</span>
                {option.description && <span className="ag-option-desc">{option.description}</span>}
              </label>
            ))}
          </div>
        ))}
      </div>
    </details>}
  </div>
}

function TextField({ label, value, onChange, disabled, className = '', rows = 4 }: {
  label: string; value: string; onChange: (value: string) => void; disabled?: boolean
  className?: string; rows?: number
}) {
  return <label className={'block space-y-1.5 ' + className}>
    <span className="text-[12px] font-medium text-ink-2">{label}</span>
    <textarea rows={rows} value={value} onChange={(event) => onChange(event.target.value)} disabled={disabled}
      className="w-full resize-y rounded-md border border-line-strong bg-bg px-2.5 py-2 text-[13px] leading-relaxed text-ink outline-none focus:border-accent focus:ring-2 focus:ring-accent/20 disabled:bg-panel disabled:text-muted" />
  </label>
}
