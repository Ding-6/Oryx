import { useCallback, useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { ApiError } from '@/lib/api'
import { fetchFeatures, toggleFeature } from './api'
import {
  canToggleFeature,
  confirmToggleText,
  featureGroups,
  featureSummary,
  featureToggleNotice,
  featuresSelfEnabled,
  toggleBody,
  type FeatureCard,
  type FeaturesResponse,
} from './settingsOps'

function messageOf(cause: unknown): string {
  return cause instanceof ApiError ? cause.message : '切换失败，请稍后重试'
}

/**
 * 特性开关。`implemented=false` 的占位开关与「自锁已关闭」状态都禁用，
 * 并给出人话原因 —— 不让用户以为点一下就真的有了那个能力。
 */
export default function FeaturePanel() {
  const [data, setData] = useState<FeaturesResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      const next = await fetchFeatures()
      setData(next)
      setError(null)
    } catch (cause) {
      setError(messageOf(cause))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void reload() }, [reload])

  const flags = data?.flags ?? []
  const selfEnabled = featuresSelfEnabled(flags)
  const groups = featureGroups(flags)
  const summary = featureSummary(flags)

  const toggle = async (card: FeatureCard) => {
    const ability = canToggleFeature(card, selfEnabled)
    if (!ability.ok || busy) return
    const next = !card.value
    const confirmText = confirmToggleText(card, next)
    if (confirmText && !window.confirm(confirmText)) return
    setBusy(card.key)
    setError(null)
    setNotice(null)
    try {
      const body = toggleBody(card.key, next)
      await toggleFeature(body.key, body.value)
      setNotice(featureToggleNotice(card.key, next))
      await reload()
    } catch (cause) {
      setError(messageOf(cause))
    } finally {
      setBusy(null)
    }
  }

  if (loading) return <p className="sys-hint">正在读取特性开关…</p>

  return (
    <div className="ff-panel">
      {error && <p className="sys-error" role="alert">{error}</p>}
      {notice && <p className="sys-notice" role="status">{notice}</p>}

      <div className="ff-summary">
        <span className="sys-badge sys-badge--plain">方案 {data?.profile || 'personal'}</span>
        <span className="sys-badge sys-badge--ok">已开启 {summary.on}</span>
        <span className="sys-badge sys-badge--plain">已关闭 {summary.off}</span>
        {summary.placeholders > 0 && (
          <span className="sys-badge sys-badge--warn">占位 {summary.placeholders}</span>
        )}
      </div>

      {!selfEnabled && (
        <div className="ff-locked" role="alert">
          <strong>运行时调整已被关闭。</strong>
          <span>
            `EnableFeatureFlags` 关掉后，本页所有开关都不能再改，连它自己也不能从界面打开；
            需要手动编辑本机 `features.yaml` 才能恢复。
          </span>
        </div>
      )}

      {groups.map((group) => (
        <section key={group.title} className="ff-group">
          <h3 className="sys-subtitle">{group.title}</h3>
          <p className="sys-hint">{group.hint}</p>
          {group.items.length === 0 && <p className="sys-empty">这一组没有开关</p>}
          <ul className="ff-list">
            {group.items.map((card) => {
              const ability = canToggleFeature(card, selfEnabled)
              const effectiveDiffers = card.value !== card.enabled
              return (
                <li key={card.key} className="ff-item">
                  <div className="ff-item-main">
                    <span className="ff-key">{card.key}</span>
                    <span className="ff-desc">{card.description}</span>
                    {effectiveDiffers && (
                      <span className="ff-effective">
                        文件值：{card.value ? '开' : '关'} · 实际生效：{card.enabled ? '开' : '关'}
                      </span>
                    )}
                    {!ability.ok && <span className="ff-reason">{ability.reason}</span>}
                  </div>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={card.value}
                    aria-label={`${card.key} 开关`}
                    disabled={!ability.ok || busy !== null}
                    className={'ff-switch' + (card.value ? ' ff-switch--on' : '')}
                    onClick={() => void toggle(card)}
                  >
                    <span className="ff-knob" />
                    <span className="ff-switch-text">{busy === card.key ? '…' : card.value ? '开' : '关'}</span>
                  </button>
                </li>
              )
            })}
          </ul>
        </section>
      ))}

      <div className="ff-foot">
        <Button size="sm" variant="outline" onClick={() => void reload()} disabled={loading}>
          重新读取
        </Button>
        <span className="sys-hint">开关写在本机配置文件里，改动会立即保存。</span>
      </div>
    </div>
  )
}
