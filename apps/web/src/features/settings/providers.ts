/**
 * 模型服务配置的纯函数层 —— 不碰网络，便于就地断言。
 *
 * 这里钉住三条容易做错的语义：
 *
 * 1. **新增不是「覆盖」**：同名必须提示去编辑，不能把别人的密钥和模型
 *    悄悄换掉。后端也返回 409，前端提前拦一道，错误来得更早。
 *
 * 2. **编辑时密钥留空 = 不动**：后端不回显明文密钥，前端也不回填；
 *    提交空串就表示保持原样，绝不能当成「清空密钥」。
 *
 * 3. **激活 ≠ 马上生效**：计算模式决定实际走本地还是云端。
 *    在效率模式下激活本地服务，只是把它记成「本地默认」，
 *    要切到本地模式才会真的用上 —— 界面必须如实说出来。
 */

export interface ProviderCapabilities {
  context_window?: number
  max_output?: number
  supports_tools?: boolean
  supports_vision?: boolean
  cost_hint?: string
}

/** `GET /api/providers` 里的一条。api_key 是打码后的值，永远不是明文。 */
export interface ProviderItem {
  name: string
  type: string
  base_url: string
  model: string
  api_key: string
  has_key: boolean
  key_storage: 'system' | 'plaintext' | 'none' | string
  capabilities: ProviderCapabilities
  is_local: boolean
}

/** `GET /api/providers` 的返回体。 */
export interface ProvidersResponse {
  active: string
  /** 本地槽位：切到本地模式时用的那一条。 */
  local: string
  /** 云端槽位：切回效率模式时用的那一条。 */
  cloud: string
  items: ProviderItem[]
}

/** `GET /api/providers/local` 里的一条。 */
export interface LocalModel {
  name: string
  size: number
  family: string
  parameter_size: string
  quantization: string
  /** 已经有哪个 provider 在跑它（空串 = 还没有）。 */
  provider: string
}

export interface ProviderForm {
  name: string
  type: string
  base_url: string
  model: string
  api_key: string
}

/** 后端 `manage.PROVIDER_TYPES` 的镜像；值是提交给接口的 type。 */
export const PROVIDER_TYPES = [
  { value: 'openai', label: 'OpenAI 兼容' },
  { value: 'anthropic', label: 'Anthropic' },
]

export function emptyForm(): ProviderForm {
  return { name: '', type: 'openai', base_url: '', model: '', api_key: '' }
}

/** 打开编辑：**不回填密钥** —— 后端只给打码值，回填了也提交不回去。 */
export function formFrom(item: ProviderItem): ProviderForm {
  return {
    name: item.name,
    type: item.type || 'openai',
    base_url: item.base_url || '',
    model: item.model || '',
    api_key: '',
  }
}

/**
 * 表单校验。返回中文错误；null = 可以提交。
 *
 * `existingNames` 只在新增时用：同名已有服务时不拦，
 * 用户点了「新增」却把别人的配置覆盖掉，是最难查的一类事故。
 */
export function validateForm(
  form: ProviderForm,
  existingNames: string[],
  editingName?: string,
): string | null {
  const name = form.name.trim()
  if (!name) return '给这个模型服务起个名字'
  if (/[\\/]/.test(name)) return '名字里不能包含斜杠'
  if (name.length > 64) return '名字太长了（最多 64 字）'
  if (!editingName && existingNames.includes(name)) {
    return `已经有同名模型服务「${name}」，请直接编辑它`
  }
  if (editingName && name !== editingName) return '编辑时不能改名字'
  const base = form.base_url.trim()
  if (!base) return '接口地址不能为空'
  if (!/^https?:\/\//i.test(base)) return '接口地址要以 http:// 或 https:// 开头'
  if (!form.model.trim()) return '模型名不能为空'
  return null
}

/** 新增请求体。 */
export function createBody(form: ProviderForm): Record<string, string> {
  return {
    name: form.name.trim(),
    type: form.type,
    base_url: form.base_url.trim(),
    model: form.model.trim(),
    api_key: form.api_key,
  }
}

/** 编辑请求体 —— **只带真正改了的字段**；密钥留空则整个字段都不传。 */
export function editBody(
  original: ProviderItem,
  form: ProviderForm,
): Record<string, string> {
  const body: Record<string, string> = {}
  if (form.type !== (original.type || 'openai')) body.type = form.type
  if (form.base_url.trim() !== (original.base_url || '')) body.base_url = form.base_url.trim()
  if (form.model.trim() !== (original.model || '')) body.model = form.model.trim()
  if (form.api_key) body.api_key = form.api_key
  return body
}

/** 编辑表单有没有实际改动（避免发一个空 body 换回 400）。 */
export function hasChanges(original: ProviderItem, form: ProviderForm): boolean {
  return Object.keys(editBody(original, form)).length > 0
}

/** 密钥状态的展示文案。 */
export function keyStatus(item: ProviderItem): { label: string; ok: boolean } {
  if (!item.has_key) return { label: '未设置密钥', ok: false }
  return item.key_storage === 'system'
    ? { label: '密钥已存入系统凭据库', ok: true }
    : { label: '密钥已保存（明文）', ok: true }
}

/** 能力矩阵的短标签 —— 上下文 / 工具 / 视觉 / 计费。 */
export function capabilityBadges(caps: ProviderCapabilities | undefined): string[] {
  if (!caps) return []
  const out: string[] = []
  const ctx = Number(caps.context_window || 0)
  if (ctx > 0) out.push(`上下文 ${ctx >= 1000 ? Math.round(ctx / 1000) + 'K' : ctx}`)
  if (caps.supports_tools) out.push('工具调用')
  if (caps.supports_vision) out.push('视觉')
  if (caps.cost_hint) out.push(String(caps.cost_hint))
  return out
}

/** 云端 / 本地分组。顺序按后端返回的原样，不重新排序。 */
export function groupProviders(items: ProviderItem[]): {
  cloud: ProviderItem[]
  local: ProviderItem[]
} {
  return {
    cloud: items.filter((item) => !item.is_local),
    local: items.filter((item) => item.is_local),
  }
}

/**
 * 这一条在界面上该挂什么徽标。
 *
 * 「当前生效」与「某类默认」是两件事：计算模式决定实际用谁，
 * 另一类里的默认只是记着「切回去时用它」。
 */
export function providerBadges(
  item: ProviderItem,
  response: ProvidersResponse | null,
): string[] {
  if (!response) return []
  const badges: string[] = []
  if (item.name === response.active) {
    badges.push('当前生效')
  } else if (item.is_local && item.name === response.local) {
    badges.push('本地默认')
  } else if (!item.is_local && item.name === response.cloud) {
    badges.push('云端默认')
  }
  return badges
}

/** 激活后要说清楚「到底生效了没有」。 */
export function activationNotice(item: ProviderItem, mode: string | undefined): string {
  const current = String(mode || '')
  if (item.is_local && current !== 'local') {
    return `已记为本地默认服务；当前是效率模式，切到本地模式后才会实际使用它`
  }
  if (!item.is_local && current === 'local') {
    return `已记为云端默认服务；当前是本地模式，切到效率模式后才会实际使用它`
  }
  return `已切到「${item.name}」`
}

/** 本机模型体积。0 / 缺失都显示 —，不显示 0 MB。 */
export function formatBytes(n: number): string {
  if (!n || n <= 0) return '—'
  const gb = n / 1024 ** 3
  if (gb >= 1) return `${gb.toFixed(1)} GB`
  return `${Math.max(1, Math.round(n / 1024 ** 2))} MB`
}
