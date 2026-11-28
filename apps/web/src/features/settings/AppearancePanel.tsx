import { useEffect, useRef, useState, type ChangeEvent } from 'react'

import { Button } from '@/components/ui/button'
import { ApiError } from '@/lib/api'
import {
  setAppearanceTheme,
  setBackground,
  setBackgroundFit,
  fetchAppearance,
} from './api'
import {
  BG_FITS,
  THEMES,
  appearanceNotice,
  backgroundUrl,
  validateBackgroundFile,
  type AppearanceState,
} from './settingsOps'

const FALLBACK: AppearanceState = {
  theme: 'light',
  background: '',
  background_exists: false,
  background_fit: 'cover',
}

function messageOf(cause: unknown): string {
  return cause instanceof ApiError ? cause.message : '保存失败，请稍后重试'
}

function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result ?? ''))
    reader.onerror = () => reject(new Error('读取图片失败'))
    reader.readAsDataURL(file)
  })
}

/**
 * 外观设置。改动成功后**立刻**把新状态交给 App 应用到 DOM ——
 * 只存盘不应用的话，主题按钮看起来能点，界面却纹丝不动。
 */
export default function AppearancePanel({
  onAppearanceChange,
}: {
  onAppearanceChange: (next: AppearanceState) => void
}) {
  const [info, setInfo] = useState<AppearanceState | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [previewKey, setPreviewKey] = useState(0)
  const fileRef = useRef<HTMLInputElement>(null)
  const current = info ?? FALLBACK

  useEffect(() => {
    let alive = true
    fetchAppearance()
      .then((data) => alive && setInfo(data))
      .catch(() => alive && setInfo(FALLBACK))
      .finally(() => alive && setLoading(false))
    return () => { alive = false }
  }, [])

  const commit = (next: AppearanceState, kind: 'theme' | 'fit' | 'background' | 'cleared') => {
    setInfo(next)
    setPreviewKey(Date.now())
    onAppearanceChange(next)
    setNotice(appearanceNotice(kind))
  }

  const applyTheme = async (theme: string) => {
    if (busy || current.theme === theme) return
    setBusy('theme')
    setError(null)
    setNotice(null)
    try {
      await setAppearanceTheme(theme)
      commit({ ...current, theme }, 'theme')
    } catch (cause) {
      setError(messageOf(cause))
    } finally {
      setBusy(null)
    }
  }

  const applyFit = async (fit: string) => {
    if (busy || current.background_fit === fit) return
    setBusy('fit')
    setError(null)
    setNotice(null)
    try {
      await setBackgroundFit(fit)
      commit({ ...current, background_fit: fit }, 'fit')
    } catch (cause) {
      setError(messageOf(cause))
    } finally {
      setBusy(null)
    }
  }

  const upload = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0] ?? null
    event.target.value = '' // 允许连续选同一张图
    const invalid = validateBackgroundFile(file)
    if (invalid) { setError(invalid); return }
    if (!file) return
    setBusy('upload')
    setError(null)
    setNotice(null)
    try {
      const dataUrl = await readAsDataUrl(file)
      await setBackground(dataUrl)
      commit({ ...current, background_exists: true }, 'background')
    } catch (cause) {
      setError(messageOf(cause))
    } finally {
      setBusy(null)
    }
  }

  const clear = async () => {
    if (busy || !current.background_exists) return
    if (!window.confirm('确定清除背景图吗？原文件不会被删除，只是不再显示。')) return
    setBusy('clear')
    setError(null)
    setNotice(null)
    try {
      await setBackground('')
      commit({ ...current, background_exists: false }, 'cleared')
    } catch (cause) {
      setError(messageOf(cause))
    } finally {
      setBusy(null)
    }
  }

  if (loading) return <p className="sys-hint">正在读取外观设置…</p>

  return (
    <div className="ap-panel">
      {error && <p className="sys-error" role="alert">{error}</p>}
      {notice && <p className="sys-notice" role="status">{notice}</p>}

      <div className="ap-block">
        <h3 className="sys-subtitle">主题</h3>
        <div className="ap-segmented">
          {THEMES.map((item) => (
            <button
              key={item.value}
              type="button"
              className={'ap-seg' + (current.theme === item.value ? ' ap-seg--on' : '')}
              disabled={busy !== null}
              onClick={() => void applyTheme(item.value)}
            >
              {item.label}
            </button>
          ))}
        </div>
        <p className="sys-hint">切换后整页立刻生效，并保存到服务端；下次打开仍是这个主题。</p>
      </div>

      <div className="ap-block">
        <h3 className="sys-subtitle">背景填充</h3>
        <div className="ap-segmented">
          {BG_FITS.map((item) => (
            <button
              key={item.value}
              type="button"
              className={'ap-seg' + (current.background_fit === item.value ? ' ap-seg--on' : '')}
              disabled={busy !== null}
              onClick={() => void applyFit(item.value)}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      <div className="ap-block">
        <h3 className="sys-subtitle">背景图</h3>
        <div className="ap-preview-row">
          <div
            className="ap-preview"
            style={{
              backgroundImage: current.background_exists
                ? `url("${backgroundUrl(previewKey)}")`
                : 'none',
              backgroundSize: current.background_fit === 'tile' ? 'auto' : current.background_fit,
              backgroundPosition: current.background_fit === 'tile' ? '0 0' : 'center',
              backgroundRepeat: current.background_fit === 'tile' ? 'repeat' : 'no-repeat',
            }}
          >
            {!current.background_exists && <span className="sys-hint">未设置</span>}
          </div>
          <div className="ap-upload">
            <input
              ref={fileRef}
              type="file"
              accept="image/png,image/jpeg,image/webp,image/gif,image/bmp"
              className="ap-file"
              onChange={(e) => void upload(e)}
            />
            <p className="sys-hint">png / jpeg / webp / gif / bmp，最大 12MB。图片只存在本机。</p>
            <div className="ap-upload-actions">
              <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => fileRef.current?.click()}>
                {busy === 'upload' ? '上传中…' : '选择图片'}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={busy !== null || !current.background_exists}
                onClick={() => void clear()}
              >
                {busy === 'clear' ? '清除中…' : '清除背景'}
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
