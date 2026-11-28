/**
 * 子 Agent 配置页的纯函数层 —— 不碰网络，便于就地断言。
 *
 * 钉住四条容易做错的语义：
 *
 * 1. **候选以目录为准**：工具 / 技能 / 模型服务不能再手敲，枚举也从后端
 *    `GET /api/agents/catalog` 下发，前端不硬编码 `permission_modes` / `kinds`。
 *
 * 2. **已保存但不在目录里的值不能丢**：内核没起来、工具被卸掉、技能改名，
 *    都会让旧定义里的名字「查无此人」。这时候要留着并标红，而不是一打开
 *    就悄悄清空 —— 否则用户一保存就把配置改没了。
 *
 * 3. **内核没起来时跳过那一项本地校验**：跟后端对齐（`available.tools=false`
 *    时不判工具名），否则用户连合法编辑都提交不了。模型服务来自配置，
 *    这项永远校验。
 *
 * 4. **只读原因要如实说**：内置定义没有创建者记录，不能一律写
 *    「由其他创建者维护」。
 */

export interface ToolCandidate {
  name: string
  group: string
  description: string
  risk_label: string
}

export interface SkillCandidate {
  name: string
  description: string
}

export interface ModelCandidate {
  name: string
  model: string
  type: string
}

export interface AgentCatalog {
  tools: ToolCandidate[]
  skills: SkillCandidate[]
  models: ModelCandidate[]
  permission_modes: string[]
  kinds: string[]
  available: { tools: boolean; skills: boolean; models: boolean }
}

export interface CandidateOption {
  value: string
  label: string
  description?: string
  group?: string
  /** 已保存但当前目录里找不到 —— 界面要标出来，但不能自动清掉。 */
  unknown?: boolean
}

export interface AgentDraftValues {
  id: string
  name: string
  tools: string[]
  disallowed_tools: string[]
  skills: string[]
  model: string
  permission_mode: string
  kind: string
  max_turns: number
}

export interface ValidationIssue {
  field: string
  message: string
}

export const EMPTY_CATALOG: AgentCatalog = {
  tools: [], skills: [], models: [], permission_modes: [], kinds: [],
  available: { tools: false, skills: false, models: false },
}

const PERMISSION_CN: Record<string, string> = {
  inherit: '跟随平台默认', ask: '每次询问', plan: '先出计划', craft: '自主执行',
  bypass: '跳过确认', dontAsk: '不再询问', auto: '自动判定', delegate: '委派模式',
}
const KIND_CN: Record<string, string> = { agent: '子 Agent', butler: '管家' }

/** 取某一类候选的名字。目录没加载好时返回空数组，不抛。 */
export function catalogNames(
  catalog: AgentCatalog | null,
  field: 'tools' | 'skills' | 'models',
): string[] {
  const items = catalog?.[field] ?? []
  return items.map((item) => item.name).filter(Boolean)
}

/** 工具按组聚合，组内按名字排 —— 候选面板按组渲染。 */
export function groupTools(catalog: AgentCatalog | null): Array<{ group: string; tools: ToolCandidate[] }> {
  const byGroup = new Map<string, ToolCandidate[]>()
  for (const tool of catalog?.tools ?? []) {
    const key = tool.group || '其他'
    const bucket = byGroup.get(key) ?? []
    bucket.push(tool)
    byGroup.set(key, bucket)
  }
  return [...byGroup.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([group, tools]) => ({ group, tools: tools.sort((a, b) => a.name.localeCompare(b.name)) }))
}

/** 多选切换：有则移除，无则追加；保持原有顺序。 */
export function toggleName(list: string[], name: string): string[] {
  if (list.includes(name)) return list.filter((item) => item !== name)
  return [...list, name]
}

/** 已选但目录里找不到的名字（去重、保序）。 */
export function unknownSelections(selected: string[], known: string[]): string[] {
  const set = new Set(known)
  const seen = new Set<string>()
  return selected.filter((name) => {
    if (set.has(name) || seen.has(name)) return false
    seen.add(name)
    return true
  })
}

/** 把「已保存但查不到」的值补进下拉选项，标成 unknown。 */
export function withUnknownValues(options: CandidateOption[], selected: string[]): CandidateOption[] {
  const existing = new Set(options.map((option) => option.value))
  const extra = unknownSelections(selected, [...existing]).map((value) => ({
    value,
    label: `${value}（当前值，目录里找不到）`,
    unknown: true,
  }))
  return [...options, ...extra]
}

export function modelOptions(catalog: AgentCatalog | null): CandidateOption[] {
  const base: CandidateOption[] = [
    { value: 'inherit', label: '跟随平台默认', description: '与主 Agent 用同一个模型服务' },
    ...(catalog?.models ?? []).map((item) => ({
      value: item.name,
      label: item.model ? `${item.name} · ${item.model}` : item.name,
      description: `类型 ${item.type}`,
    })),
  ]
  return base
}

export function permissionOptions(catalog: AgentCatalog | null): CandidateOption[] {
  const values = catalog?.permission_modes?.length
    ? catalog.permission_modes
    : Object.keys(PERMISSION_CN)
  return values.map((value) => ({ value, label: PERMISSION_CN[value] ?? value }))
}

export function kindOptions(catalog: AgentCatalog | null): CandidateOption[] {
  const values = catalog?.kinds?.length ? catalog.kinds : Object.keys(KIND_CN)
  return values.map((value) => ({ value, label: KIND_CN[value] ?? value }))
}

export function toolOptions(catalog: AgentCatalog | null): CandidateOption[] {
  return (catalog?.tools ?? []).map((tool) => ({
    value: tool.name,
    label: tool.name,
    description: tool.description,
    group: tool.group,
  }))
}

export function skillOptions(catalog: AgentCatalog | null): CandidateOption[] {
  return (catalog?.skills ?? []).map((skill) => ({
    value: skill.name,
    label: skill.name,
    description: skill.description,
  }))
}

/** 目录不可用时给一句人话，别让用户以为候选本来就是空的。 */
export function catalogHint(catalog: AgentCatalog | null): string | null {
  if (!catalog) return '候选目录还没加载出来，已保存的配置仍会原样保留。'
  const { tools, skills } = catalog.available ?? { tools: false, skills: false }
  if (!tools && !skills) {
    return '内核还没就绪，工具与技能候选暂不可用；已保存的配置会原样保留。'
  }
  if (!tools) return '内核还没就绪，工具候选暂不可用；已保存的配置会原样保留。'
  if (!skills) return '内核还没就绪，技能候选暂不可用；已保存的配置会原样保留。'
  return null
}

/** 只读原因三档：可编辑 / 内置无创建者 / 别人创建的。 */
export function readonlyReason(item: {
  can_edit?: boolean
  creator_id?: string | null
} | null): string {
  if (!item) return '选择一个定义查看或配置，或新建共享能力。'
  if (item.can_edit) return '你是创建者，可以修改此定义。'
  if (!item.creator_id) {
    return '这是内置定义，没有创建者记录，只能查看；如需修改请新建一个自己的定义。'
  }
  return '这条定义由其他创建者维护，当前为只读。'
}

const ID_RE = /^[A-Za-z0-9_\-\u4e00-\u9fff ]{1,64}$/

/**
 * 保存前本地校验 —— 与后端 `validate_agent_assets` 同一套规则，
 * 让错误来得比一次网络往返更早。返回第一个问题，没有则返回 null。
 */
export function validateAgentDraft(
  values: AgentDraftValues,
  catalog: AgentCatalog | null,
  creating: boolean,
): ValidationIssue | null {
  const id = values.id.trim()
  if (creating && !id) return { field: 'id', message: '请填写唯一 ID' }
  if (creating && !ID_RE.test(id)) {
    return { field: 'id', message: 'ID 只能含中英文、数字、-、_、空格，且不超过 64 字' }
  }
  if (!values.name.trim()) return { field: 'name', message: '请填写显示名称' }
  if (!Number.isInteger(values.max_turns) || values.max_turns < 1 || values.max_turns > 100) {
    return { field: 'max_turns', message: '最多推理轮数要是 1 到 100 之间的整数' }
  }

  const model = values.model.trim()
  if (catalog?.available?.models === true && model && model !== 'inherit'
    && !catalogNames(catalog, 'models').includes(model)) {
    return { field: 'model', message: `模型服务「${model}」不存在，请先到设置里添加` }
  }
  const modes = permissionOptions(catalog).map((option) => option.value)
  if (modes.length && !modes.includes(values.permission_mode)) {
    return { field: 'permission_mode', message: '权限基线不受支持，请重新选一个' }
  }
  const kinds = kindOptions(catalog).map((option) => option.value)
  if (kinds.length && !kinds.includes(values.kind)) {
    return { field: 'kind', message: '定义类型不受支持，请重新选一个' }
  }

  // 内核拿不到清单时只跳过那一项 —— 与后端行为一致
  const toolsKnown = catalog?.available?.tools === true
  if (toolsKnown) {
    const knownTools = catalogNames(catalog, 'tools')
    const badTools = unknownSelections(values.tools, knownTools)
    if (badTools.length) {
      return { field: 'tools', message: `这些工具不存在：${badTools.join('、')}` }
    }
    const badDisallowed = unknownSelections(values.disallowed_tools, knownTools)
    if (badDisallowed.length) {
      return { field: 'disallowed_tools', message: `额外禁用工具里有不存在的：${badDisallowed.join('、')}` }
    }
  }
  if (catalog?.available?.skills === true) {
    const badSkills = unknownSelections(values.skills, catalogNames(catalog, 'skills'))
    if (badSkills.length) {
      return { field: 'skills', message: `这些技能不存在：${badSkills.join('、')}` }
    }
  }
  return null
}
