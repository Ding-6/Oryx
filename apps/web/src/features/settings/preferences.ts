/**
 * 偏好（交互模式 / 回答风格 / 计算模式）的纯函数层。
 *
 * 目录全部来自后端 `GET /api/preferences/catalog`，前端不硬编码选项 ——
 * 后端加一种模式，界面自动多一项。这里只做「展示用」的派生：
 * 中文标签、风险徽标、单次运行的覆盖请求体，都不碰网络。
 */

/** 「跟随全局设置」在下拉里用空串表示：不向 /api/chat 传该字段，后端自然回退全局默认。 */
export const FOLLOW_GLOBAL = ''

export interface InteractionOption {
  name: string
  label: string
  hint: string
  read_only: boolean
  skip_approval: boolean
  unattended: boolean
  coordination_only: boolean
}

export interface StyleOption {
  name: string
  label: string
  hint: string
}

export interface ComputeModeOption {
  name: string
  label: string
  hint: string
}

export interface PreferenceCatalog {
  interactions: InteractionOption[]
  styles: StyleOption[]
  compute_modes: ComputeModeOption[]
}

/** 顶栏 / 摘要条要用的三态。字段都是**后端原始值**，展示时才查目录。 */
export interface PreferenceValues {
  interaction?: string
  style?: string
  mode?: string
}

export const FALLBACK = {
  interaction: 'craft',
  style: 'professional',
  mode: 'efficiency',
} as const

/** 目录里查中文名；目录还没加载 / 后端给了未知值时退回显示原始值，不显示空白。 */
export function labelFor(
  items: Array<{ name: string; label?: string }> | undefined,
  name: string | undefined,
  fallback = '',
): string {
  const key = (name || '').trim()
  if (!key) return fallback
  const hit = items?.find((item) => item.name === key)
  return hit?.label || key
}

/** 当前三态的中文摘要 —— 顶栏与新建运行页共用同一套说法。 */
export function summary(
  values: PreferenceValues,
  catalog: PreferenceCatalog | null,
): { interaction: string; style: string; mode: string } {
  return {
    interaction: labelFor(catalog?.interactions, values.interaction || FALLBACK.interaction, FALLBACK.interaction),
    style: labelFor(catalog?.styles, values.style || FALLBACK.style, FALLBACK.style),
    mode: labelFor(catalog?.compute_modes, values.mode || FALLBACK.mode, FALLBACK.mode),
  }
}

/** 交互模式的风险徽标 —— 让用户在选择前就知道它会不会跳过确认。 */
export function interactionFlags(item: InteractionOption): string[] {
  const flags: string[] = []
  if (item.read_only) flags.push('只读')
  if (item.coordination_only) flags.push('只调度')
  if (item.skip_approval) flags.push('跳过确认')
  if (item.unattended) flags.push('无人值守')
  return flags
}

/** 这一类交互模式要不要在进入前给一句更强的警告。 */
export function isElevated(item: InteractionOption): boolean {
  return item.skip_approval || item.unattended
}

/**
 * 单次运行的覆盖请求体 —— 只把「显式选了具体值」的字段带上。
 *
 * 故意**不含 mode**：计算模式是全局模型路由（切 provider 要走
 * POST /api/mode），单次传它只会被记进会话快照、并不会换模型服务，
 * 做成可选反而像假功能。详见 docs/INTERFACE-SPEC-V2.md §2.2。
 */
export function runOverrides(interaction: string, style: string): { interaction?: string; style?: string } {
  const out: { interaction?: string; style?: string } = {}
  const i = interaction.trim()
  const s = style.trim()
  if (i && i !== FOLLOW_GLOBAL) out.interaction = i
  if (s && s !== FOLLOW_GLOBAL) out.style = s
  return out
}

/** 提交前校验目录：前端手滑或缓存里带了后端不认的值，提前挡住。 */
export function validateOverrides(
  interaction: string,
  style: string,
  catalog: PreferenceCatalog | null,
): string | null {
  if (!catalog) return null // 目录没加载时不拦，交给后端校验
  const i = interaction.trim()
  const s = style.trim()
  if (i && !catalog.interactions.some((x) => x.name === i)) return `未知的交互模式：${i}`
  if (s && !catalog.styles.some((x) => x.name === s)) return `未知的回答风格：${s}`
  return null
}

/** 目录是不是可用（三个数组都得有内容），用于空状态判断。 */
export function catalogReady(catalog: PreferenceCatalog | null): boolean {
  return Boolean(
    catalog &&
    catalog.interactions?.length &&
    catalog.styles?.length &&
    catalog.compute_modes?.length,
  )
}
