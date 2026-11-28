/**
 * 把服务端存的外观**真正作用到页面**。
 *
 * 这是模块 9 最容易漏的一步：`tokens.css` 里早就有 `[data-theme="dark"]`
 * 和 `#bgLayer` 规则（新前端通过 `@import` 吃到了），但此前没有任何 JS
 * 调 `/api/appearance` —— 设置页就算存了主题，界面也不会变，属于假功能。
 *
 * 这里不归 React 管：`#bgLayer` 由 DOM 直接创建并插到 body 最前面，
 * 它在 `#root` 之下（z-index 见 index.css），React 重渲染不会动它。
 * 换成纯 CSS 变量后，明暗主题、背景图与填充方式都在一处落地。
 */

import { fetchAppearance } from './api'
import { appearancePlan, type AppearanceState } from './settingsOps'

const LAYER_ID = 'bgLayer'

function ensureLayer(): HTMLElement | null {
  let el = document.getElementById(LAYER_ID)
  if (!el) {
    el = document.createElement('div')
    el.id = LAYER_ID
    el.setAttribute('aria-hidden', 'true')
    document.body.prepend(el)
  }
  return el
}

export function applyAppearanceState(
  state: AppearanceState | null,
  cacheKey: number | string = Date.now(),
): void {
  const plan = appearancePlan(state, cacheKey)
  const root = document.documentElement
  root.setAttribute('data-theme', plan.theme)
  root.style.setProperty('--bg-image', plan.bgImage)
  root.style.setProperty('--bg-size', plan.size)
  root.style.setProperty('--bg-pos', plan.pos)
  root.style.setProperty('--bg-repeat', plan.repeat)

  const el = ensureLayer()
  if (el) el.style.display = plan.hasBg ? 'block' : 'none'
  document.body.classList.toggle('has-bg', plan.hasBg)
}

/**
 * 挂载时拉一次并应用。失败（内核还没起来 / 没登录）时静默退回默认浅色，
 * 不把「外观读不到」顶成整页错误 —— 外观是锦上添花，不该拖垮主界面。
 */
export async function loadAndApplyAppearance(
  cacheKey: number | string = Date.now(),
): Promise<AppearanceState | null> {
  try {
    const state = await fetchAppearance()
    applyAppearanceState(state, cacheKey)
    return state
  } catch {
    applyAppearanceState(null, cacheKey)
    return null
  }
}
