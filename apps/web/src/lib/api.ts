/**
 * API 客户端 —— 前端**唯一**的联网入口。
 *
 * 为什么要有它
 * ------------
 * 体检发现旧前端的问题：
 *   · 195 个 `api()` 调用里**只有 91 个**检查了 HTTP 状态码
 *     → 104 个接口失败时前端**当成功处理**，用户看到的是「点了没反应」
 *   · 16 处空 `catch` → 错误被彻底吞掉
 *   · 错误信息从 `x.detail` / `x.error` / `x.message` 到处捞，形状不统一
 *
 * 这里把几件事收在一处：
 *   1. **状态码必检** —— 非 2xx 一律抛 `ApiError`，带状态码与后端给的文案
 *   2. **错误文案统一提取** —— 后端 v2 的错误体是
 *      `{code, data, message, detail}`；旧接口只有 `{detail}` 或 `{error}`。
 *      按 `message → detail → error → 状态码兜底` 的顺序取（模块 10）
 *   3. **令牌与来源标记** —— 自动带上 `X-Oryx-Request: 1`（写操作必需）
 *   4. **请求超时** —— 默认 GET 15s / 写 30s / 上传 120s，超时给中文提示，
 *      不再让一个卡住的请求永远转圈（模块 10）
 *   5. **全局 401** —— 会话过期的 401 交给 App 统一回登录页；
 *      `/api/auth/me` 未登录时的 401 是正常探测，不误触发（模块 10）
 *   6. **请求 id** —— 带上后端 `X-Request-Id`，排障时报这个 id 就能定位日志
 *
 * 于是调用方只需要：
 *
 *     const sessions = await api.get<Session[]>('/api/sessions')
 *     try { await api.post('/api/chat', { goal }) }
 *     catch (e) { toast(e instanceof ApiError ? e.message : '出错了') }
 *
 * 顺带：类型直接从 `docs/INTERFACE-SPEC-V2.md` 的契约来，
 * 那个文档有测试守着与代码一致（core/tests/test_interface_spec_v2.py）。
 */

import {
  type ApiErrorBody,
  unwrap,
  pickMessage,
  timeoutFor,
  timeoutMessage,
  isAuthError,
} from './apiContracts'

export type { ApiErrorBody } from './apiContracts'
export { unwrap, pickMessage } from './apiContracts'

export type ApiErrorKind = 'http' | 'timeout' | 'network'

/** 业务错误：状态码 + 可直接展示给用户的一句话。 */
export class ApiError extends Error {
  readonly status: number
  readonly body: ApiErrorBody | undefined
  readonly kind: ApiErrorKind
  readonly requestId: string | undefined

  constructor(
    status: number,
    message: string,
    body?: ApiErrorBody,
    kind: ApiErrorKind = 'http',
    requestId?: string,
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.body = body
    this.kind = kind
    this.requestId = requestId
  }
}

/** 全局 401 回调。同一时刻只挂一个；App 卸载时注销，避免重复跳登录。 */
type UnauthorizedHandler = (status: number, path: string, requestId?: string) => void
let unauthorizedHandler: UnauthorizedHandler | null = null

export function setUnauthorizedHandler(fn: UnauthorizedHandler | null): void {
  unauthorizedHandler = fn
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH'
  body?: unknown
  signal?: AbortSignal
  /** 覆盖默认超时；SSE 不走这里。 */
  timeoutMs?: number
}

export type CallOptions = { signal?: AbortSignal; timeoutMs?: number }

const BASE = ''

/** 写操作必须带这个头 —— 后端用它挡跨源请求（server.py:REQUEST_HEADER）。 */
const REQUEST_HEADER = 'X-Oryx-Request'

async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const method = opts.method ?? 'GET'
  const headers: Record<string, string> = {}
  if (method !== 'GET') headers[REQUEST_HEADER] = '1'

  let payload: BodyInit | undefined
  const isForm = opts.body instanceof FormData
  if (opts.body !== undefined) {
    // 这里要再判一次 instanceof：提成布尔变量后 TS 在赋值处丢了类型收窄，
    // 会把 opts.body 当成 unknown（{} | null）拒绝赋给 BodyInit。
    if (opts.body instanceof FormData) {
      // Let the browser attach the multipart boundary for uploads.
      payload = opts.body
    } else {
      headers['Content-Type'] = 'application/json'
      payload = JSON.stringify(opts.body)
    }
  }

  // ── 超时：自己管一个 controller，把外部 signal 桥接进来 ──────────
  // 不直接用 AbortSignal.timeout()：要区分「用户主动取消」和「真的超时」，
  // 前者原样抛给调用方（切页面 / 卸载不该弹错误），后者给中文提示。
  const timeoutMs = opts.timeoutMs ?? timeoutFor(method, isForm)
  const controller = new AbortController()
  let timedOut = false
  const timer = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeoutMs)
  const external = opts.signal
  const onExternalAbort = () => controller.abort()
  if (external) {
    if (external.aborted) controller.abort()
    else external.addEventListener('abort', onExternalAbort, { once: true })
  }

  let res: Response
  try {
    res = await fetch(BASE + path, {
      method,
      headers,
      body: payload,
      credentials: 'same-origin',
      signal: controller.signal,
    })
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      if (!timedOut && external?.aborted) throw err
      throw new ApiError(0, timeoutMessage(timeoutMs), undefined, 'timeout')
    }
    // 网络层失败（内核没起来、断网）—— 这不是 HTTP 错误，单独处理
    throw new ApiError(0, '连不上内核服务 —— 它可能还没启动', undefined, 'network')
  } finally {
    clearTimeout(timer)
    if (external) external.removeEventListener('abort', onExternalAbort)
  }

  const requestId = res.headers.get('X-Request-Id') || undefined
  if (res.status === 204) return undefined as T

  // 先按文本读 —— 有些接口返回的不是 JSON（图片、CSS、文件预览）
  const text = await res.text()
  let parsed: unknown
  if (text) {
    try {
      parsed = JSON.parse(text)
    } catch {
      parsed = undefined
    }
  }

  if (!res.ok) {
    const body = (parsed ?? undefined) as ApiErrorBody | undefined
    if (isAuthError(res.status) && unauthorizedHandler) {
      // 回调自己抛错不能掀翻原本的请求错误
      try {
        unauthorizedHandler(res.status, path, requestId)
      } catch {
        /* 全局处理失败不影响本次错误上抛 */
      }
    }
    throw new ApiError(res.status, pickMessage(body, res.status), body, 'http', requestId)
  }

  // v2 统一响应 → 领域数据（老接口的裸对象原样透传）。
  return unwrap<T>(parsed)
}

export const api = {
  get: <T>(path: string, opts?: CallOptions) => request<T>(path, { ...opts, method: 'GET' }),
  post: <T>(path: string, body?: unknown, opts?: CallOptions) =>
    request<T>(path, { ...opts, method: 'POST', body }),
  postForm: <T>(path: string, body: FormData, opts?: CallOptions) =>
    request<T>(path, { ...opts, method: 'POST', body }),
  put: <T>(path: string, body?: unknown, opts?: CallOptions) =>
    request<T>(path, { ...opts, method: 'PUT', body }),
  del: <T>(path: string, opts?: CallOptions) =>
    request<T>(path, { ...opts, method: 'DELETE' }),
}

/** SSE 连接状态。UI 要能区分「连着」「重连中」「彻底断了」。 */
export type StreamStatus = 'connecting' | 'open' | 'reconnecting' | 'offline'

/**
 * SSE 订阅 —— EventSource 带不了自定义头，靠同源自动带 HttpOnly cookie。
 *
 * 后端的事件流带单调递增的 `seq`，并支持 `?since=N` 补发断线期间的事件。
 * 所以这里不能让浏览器原生重连：原生重连不带 since，审批事件会在
 * 断线那一瞬间永久丢失（用户就看到任务一直卡在「运行中」）。
 *
 * 收到 `resync` 说明缓冲已经滚过断点、没法续传，回调会通知页面整页刷新；
 * 这里把 since 清零，避免带着一个失效的水位继续接。
 *
 * 注意：SSE 是长连接，**不走上面的超时 controller** —— 断了由下面的
 * 指数退避重连处理，15 秒超时反而会把正常流掐掉。
 */
export function subscribe(
  path: string,
  onEvent: (ev: Record<string, unknown>) => void,
  onStatus?: (status: StreamStatus) => void,
): () => void {
  let source: EventSource | null = null
  let lastSeq = -1
  let retries = 0
  let timer: number | undefined
  let closed = false

  const open = () => {
    if (closed) return
    onStatus?.(retries > 0 ? 'reconnecting' : 'connecting')
    const url = lastSeq >= 0
      ? `${path}${path.includes('?') ? '&' : '?'}since=${lastSeq}`
      : path
    const es = new EventSource(url)
    source = es
    es.onopen = () => {
      retries = 0
      onStatus?.('open')
    }
    es.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data) as Record<string, unknown>
        // ready/resync 是控制帧，seq 等于水位但不占号，不能当事件序号记。
        if (ev.type === 'resync') {
          lastSeq = -1
        } else if (ev.type !== 'ready' && typeof ev.seq === 'number') {
          lastSeq = Math.max(lastSeq, ev.seq)
        }
        onEvent(ev)
      } catch {
        /* 非 JSON 的帧（心跳等）—— 忽略 */
      }
    }
    es.onerror = () => {
      es.close()
      if (source === es) source = null
      if (closed) return
      retries += 1
      if (retries > 8) {
        onStatus?.('offline')
        return
      }
      const delay = Math.min(1000 * 2 ** Math.min(retries - 1, 4), 15000)
      timer = window.setTimeout(open, delay)
    }
  }

  open()
  return () => {
    closed = true
    if (timer !== undefined) window.clearTimeout(timer)
    source?.close()
    source = null
  }
}
