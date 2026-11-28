/**
 * 设置弹层剩余三项的纯函数：外观 / 特性开关 / 长期记忆。
 *
 * 三处容易做成「假功能」的地方，这里先钉死：
 *
 *  1. **外观必须真的作用到页面**。`tokens.css` 里已有 `[data-theme="dark"]`
 *     和 `#bgLayer` 规则（新前端通过 `@import` 吃到了），但此前没有任何 JS
 *     调 `/api/appearance`。所以除了表单，还要把服务端存的主题 / 背景
 *     换算成一份「DOM 应用计划」，在 App 挂载与每次改动后执行。
 *  2. **特性开关有自锁**：`EnableFeatureFlags` 关掉后，后端 `/api/features/toggle`
 *     一律 403，连它自己都不能再从界面打开。界面要在关闭前二次确认，
 *     关掉后把整组开关禁用并说明「只能改 features.yaml」。
 *  3. `implemented=false` 的开关背后没有能力，切了也不会改变行为 ——
 *     禁用并如实标注，不让它看起来像能用。
 *
 * 这三个域返回的都是**裸对象**（老前端还在用），前端 `api.get` 会原样透传，
 * 不要按 v2 的 `data` 去取。
 */

// ── 外观 ────────────────────────────────────────────────────────────

export const THEMES = [
  { value: 'light', label: '浅色' },
  { value: 'dark', label: '深色' },
] as const

export const BG_FITS = [
  { value: 'cover', label: '铺满' },
  { value: 'contain', label: '完整显示' },
  { value: 'tile', label: '平铺' },
  { value: 'center', label: '居中' },
] as const

/** 与后端 `appearance.py` 的 FITS 一一对应，别各改各的。 */
export const BG_FIT_CSS: Record<string, { size: string; pos: string; repeat: string }> = {
  cover: { size: 'cover', pos: 'center', repeat: 'no-repeat' },
  contain: { size: 'contain', pos: 'center', repeat: 'no-repeat' },
  tile: { size: 'auto', pos: '0 0', repeat: 'repeat' },
  center: { size: 'auto', pos: 'center', repeat: 'no-repeat' },
}

export const BACKGROUND_MAX_BYTES = 12 * 1024 * 1024
export const BACKGROUND_EXTS = ['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp'] as const

export interface AppearanceState {
  theme: string
  background: string
  background_exists: boolean
  background_fit: string
}

export interface AppearancePlan {
  theme: 'light' | 'dark'
  hasBg: boolean
  /** CSS `background-image` 的值；没有背景时是 'none'。 */
  bgImage: string
  size: string
  pos: string
  repeat: string
}

export function isTheme(value: string): boolean {
  return THEMES.some((t) => t.value === value)
}

export function isFit(value: string): boolean {
  return BG_FITS.some((f) => f.value === value)
}

/** 背景图地址：换图后带一个 `?v=` 破缓存；`t` 是令牌参数，不能占用。 */
export function backgroundUrl(cacheKey: number | string): string {
  return `/api/appearance/background.png?v=${encodeURIComponent(String(cacheKey))}`
}

/**
 * 把服务端状态换算成「要往 DOM 上写什么」。纯函数，不碰 document。
 * `cacheKey` 用来给背景图 URL 破缓存（换图后浏览器不会还显示旧的那张）。
 */
export function appearancePlan(
  state: AppearanceState | null,
  cacheKey: number | string = 0,
): AppearancePlan {
  const theme: 'light' | 'dark' = state?.theme === 'dark' ? 'dark' : 'light'
  const hasBg = Boolean(state?.background_exists)
  const fitValue = String(state?.background_fit ?? '')
  const fit = isFit(fitValue) ? fitValue : 'cover'
  const css = BG_FIT_CSS[fit] ?? BG_FIT_CSS.cover
  return {
    theme,
    hasBg,
    bgImage: hasBg ? `url("${backgroundUrl(cacheKey)}")` : 'none',
    size: css.size,
    pos: css.pos,
    repeat: css.repeat,
  }
}

export function validateBackgroundFile(
  file: { name?: string; size?: number } | null | undefined,
): string | null {
  if (!file) return '请先选择一张图片'
  const ext = String(file.name ?? '').split('.').pop()?.toLowerCase() ?? ''
  if (!BACKGROUND_EXTS.includes(ext as (typeof BACKGROUND_EXTS)[number])) {
    return '只支持 png / jpeg / webp / gif / bmp 格式'
  }
  const size = Number(file.size ?? 0)
  if (!Number.isFinite(size) || size <= 0) return '图片是空的'
  if (size > BACKGROUND_MAX_BYTES) {
    return `图片太大（${(size / 1024 / 1024).toFixed(1)}MB），上限 12MB`
  }
  return null
}

export function appearanceNotice(kind: 'theme' | 'fit' | 'background' | 'cleared' | 'failed'): string {
  return ({
    theme: '主题已保存，页面已切换',
    fit: '背景填充方式已保存',
    background: '背景图已更新',
    cleared: '背景图已清除',
    failed: '保存失败，请重试',
  } as const)[kind]
}

// ── 特性开关 ────────────────────────────────────────────────────────

export interface FeatureCard {
  key: string
  description: string
  enabled: boolean
  value: boolean
  implemented: boolean
}

export interface FeaturesResponse {
  profile: string
  flags: FeatureCard[]
  raw: Record<string, boolean>
}

/** `EnableFeatureFlags` 是否还允许运行时调整（关掉就自锁）。 */
export function featuresSelfEnabled(flags: FeatureCard[] | undefined): boolean {
  if (!Array.isArray(flags)) return false
  const self = flags.find((f) => f?.key === 'EnableFeatureFlags')
  return self ? Boolean(self.value) : false
}

export interface ToggleAbility {
  ok: boolean
  reason: string
}

export function canToggleFeature(card: FeatureCard | null | undefined, selfEnabled: boolean): ToggleAbility {
  if (!card?.key) return { ok: false, reason: '开关数据缺失' }
  if (!selfEnabled) {
    return { ok: false, reason: 'EnableFeatureFlags 已关闭，运行时禁止调整；需要改 features.yaml' }
  }
  if (!card.implemented) return { ok: false, reason: '本项目还没有这个能力，开关只是占位' }
  return { ok: true, reason: '' }
}

/** 关掉自锁开关前要二次确认；其余改动不需要。 */
export function confirmToggleText(card: FeatureCard, next: boolean): string | null {
  if (card.key === 'EnableFeatureFlags' && !next) {
    return '关闭后，运行时开关调整会被禁用，连它自己都不能再从界面打开，只能手动改 features.yaml。确定关闭吗？'
  }
  return null
}

export function toggleBody(key: string, value: boolean): { key: string; value: boolean } {
  return { key: key.trim(), value }
}

export function featureToggleNotice(key: string, next: boolean): string {
  return `已${next ? '开启' : '关闭'}「${key}」`
}

export interface FeatureGroup {
  title: string
  hint: string
  items: FeatureCard[]
}

/** 分成「已实现」与「占位」两组 —— 占位组界面要标成不可切换。 */
export function featureGroups(flags: FeatureCard[] | undefined): FeatureGroup[] {
  const items = Array.isArray(flags) ? flags : []
  return [
    {
      title: '可用能力',
      hint: '这些开关背后有真实实现，切换会改变系统行为',
      items: items.filter((f) => Boolean(f?.implemented)),
    },
    {
      title: '尚未实现',
      hint: '本项目还没有这个能力，开关只是占位，切换也不会有效果',
      items: items.filter((f) => !f?.implemented),
    },
  ]
}

export function featureSummary(flags: FeatureCard[] | undefined): { on: number; off: number; placeholders: number } {
  const items = Array.isArray(flags) ? flags : []
  return {
    on: items.filter((f) => f?.implemented && f.value).length,
    off: items.filter((f) => f?.implemented && !f.value).length,
    placeholders: items.filter((f) => !f?.implemented).length,
  }
}

// ── 长期记忆 ────────────────────────────────────────────────────────

export interface MemoryItem {
  id: string
  text: string
  tags: string
  updated_at: number
}

export const MEMORY_TEXT_MAX = 2000
export const MEMORY_TAGS_MAX = 200

export function validateMemoryText(text: string): string | null {
  const value = String(text ?? '').trim()
  if (!value) return '请输入要记住的内容'
  if (value.length > MEMORY_TEXT_MAX) return `内容最多 ${MEMORY_TEXT_MAX} 字`
  return null
}

export function validateMemoryTags(tags: string): string | null {
  const value = String(tags ?? '').trim()
  if (value.length > MEMORY_TAGS_MAX) return `标签最多 ${MEMORY_TAGS_MAX} 字`
  return null
}

export function memoryBody(text: string, tags: string): { text: string; tags: string } {
  return { text: String(text ?? '').trim(), tags: String(tags ?? '').trim() }
}

/** 秒级时间戳；0 / 坏值显示占位，不显示 1970 年。 */
export function memoryTime(ts: unknown): string {
  const n = Number(ts)
  if (!Number.isFinite(n) || n <= 0) return '—'
  try {
    return new Date(n * 1000).toLocaleString('zh-CN', { hour12: false })
  } catch {
    return '—'
  }
}

export function memorySummary(items: MemoryItem[] | undefined): { count: number; text: string } {
  const count = Array.isArray(items) ? items.length : 0
  return { count, text: count === 0 ? '还没有记住任何内容' : `共 ${count} 条记忆` }
}

export function dedupeNotice(deduped: boolean): string {
  return deduped ? '这条内容已经记住了，只更新了时间' : '已记住'
}
