/** 偏好域的前端接入。目录走 v2 统一响应，三个写入接口还是旧式裸对象 —— api 客户端两种都吃。 */

import { api } from '@/lib/api'
import type { PreferenceCatalog } from './preferences'
import type { LocalModel, ProvidersResponse } from './providers'
import type { AppearanceState, FeaturesResponse, MemoryItem } from './settingsOps'

export type { PreferenceCatalog } from './preferences'

/** 三份目录的唯一数据源。 */
export function fetchPreferenceCatalog(): Promise<PreferenceCatalog> {
  return api.get<PreferenceCatalog>('/api/preferences/catalog')
}

/** 切全局交互模式。返回 cycle 是 Shift+Tab 轮换顺序，界面暂不用。 */
export function setInteraction(
  interaction: string,
): Promise<{ interaction: string; cycle: string[] }> {
  return api.post('/api/interaction', { interaction })
}

/** 切全局回答风格。 */
export function setStyle(style: string): Promise<{ style: string }> {
  return api.post('/api/style', { style })
}

/**
 * 切全局计算模式（效率 / 本地）。
 * 后端会同步把 active_provider 指到同类模型上，返回值带上切完后的模型服务。
 */
export function setComputeMode(
  mode: string,
): Promise<{ mode: string; provider: string | null; model: string | null }> {
  return api.post('/api/mode', { mode })
}

// ── 模型服务（provider）────────────────────────────────────────────
// 这些接口返回的是裸对象（不是 v2 统一响应），api 客户端会原样透传。

/** 全部模型服务 + 当前生效的那一条 + 两个槽位。 */
export function fetchProviders(): Promise<ProvidersResponse> {
  return api.get<ProvidersResponse>('/api/providers')
}

/** 新增。同名会 409 —— 调用前先用 validateForm 挡一道。 */
export function createProvider(body: Record<string, string>): Promise<{ name: string; type: string }> {
  return api.post('/api/providers', body)
}

/** 编辑。api_key 留空表示不动已存的那份。 */
export function updateProvider(
  name: string,
  body: Record<string, string>,
): Promise<{ name: string; type: string }> {
  return api.put(`/api/providers/${encodeURIComponent(name)}`, body)
}

/** 激活。会同时写 active_provider 与该类槽位（本地 / 云端）。 */
export function activateProvider(name: string): Promise<{
  active_provider: string
  local_provider: string | null
  cloud_provider: string | null
}> {
  return api.post(`/api/providers/${encodeURIComponent(name)}/activate`)
}

/** 删除。正在用的那一条后端会拒绝（400），直接展示 message 即可。 */
export function deleteProvider(name: string): Promise<{ deleted: string }> {
  return api.del(`/api/providers/${encodeURIComponent(name)}`)
}

/** 本机（Ollama）已装的模型。服务没起时返回空数组，不是错误。 */
export function fetchLocalModels(): Promise<{ models: LocalModel[]; base_url: string }> {
  return api.get('/api/providers/local')
}

/** 把一个本机模型登记成 provider（已存在则复用），随后调用方再激活它。 */
export function registerLocalModel(
  model: string,
  base_url = '',
): Promise<{ name: string; model: string; type: string; created: boolean }> {
  return api.post('/api/providers/local', { model, base_url })
}

// ── 外观 ───────────────────────────────────────────────────────────
// 裸对象；主题 / 背景要由调用方应用到 DOM，接口本身只负责存盘。

export function fetchAppearance(): Promise<AppearanceState> {
  return api.get<AppearanceState>('/api/appearance')
}

export function setAppearanceTheme(theme: string): Promise<{ theme: string }> {
  return api.post('/api/appearance/theme', { theme })
}

export function setBackgroundFit(fit: string): Promise<{ background_fit: string }> {
  return api.post('/api/appearance/background-fit', { fit })
}

/** 传空串表示清除背景图；传 data URL 表示上传（上限 12MB）。 */
export function setBackground(dataUrl: string): Promise<{
  path?: string
  bytes?: number
  background?: string
  cleared?: boolean
}> {
  return api.post('/api/appearance/background', { data_url: dataUrl })
}

// ── 特性开关 ───────────────────────────────────────────────────────

export function fetchFeatures(): Promise<FeaturesResponse> {
  return api.get<FeaturesResponse>('/api/features')
}

/** `EnableFeatureFlags` 关闭时后端返回 403（自锁），界面要如实提示。 */
export function toggleFeature(key: string, value: boolean): Promise<{ key: string; value: boolean }> {
  return api.post('/api/features/toggle', { key, value })
}

// ── 长期记忆 ───────────────────────────────────────────────────────

export function fetchMemories(limit = 50): Promise<{ memories: MemoryItem[] }> {
  return api.get<{ memories: MemoryItem[] }>(`/api/memory?limit=${encodeURIComponent(String(limit))}`)
}

export function addMemory(text: string, tags: string): Promise<{
  id: string
  text: string
  deduped: boolean
}> {
  return api.post('/api/memory', { text, tags })
}

export function deleteMemory(memoryId: string): Promise<{ deleted: string }> {
  return api.del(`/api/memory/${encodeURIComponent(memoryId)}`)
}

// ── 账号（模块 12）──────────────────────────────────────────────────
// 账号域走 v2 统一响应，api 客户端会剥掉 {code, data, message} 壳。

export interface PasswordChangeResult {
  changed: boolean
  /** 被注销的**其它**登录会话数；当前会话保留。 */
  revoked_sessions: number
}

/**
 * 改密码。原密码错误 400、新密码不合规 422，都由 ApiError.message 带中文。
 * 成功后服务端会按 user_id 注销其它会话，但当前 cookie 仍有效。
 */
export function changePassword(
  oldPassword: string,
  newPassword: string,
): Promise<PasswordChangeResult> {
  return api.post('/api/auth/password', {
    old_password: oldPassword,
    new_password: newPassword,
  })
}
