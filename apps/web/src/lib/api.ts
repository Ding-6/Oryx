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
 * 这里把三件事收在一处：
 *   1. **状态码必检** —— 非 2xx 一律抛 `ApiError`，带状态码与后端给的文案
 *   2. **错误文案统一提取** —— 后端 v2 的错误体是
 *      `{code, data, message, detail}`；旧接口只有 `{detail}`。
 *      这里按 `message → detail → 状态码兜底` 的顺序取，前端只管 catch
 *   3. **令牌与来源标记** —— 自动带上 `X-Oryx-Request: 1`（写操作必需）
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

/** 后端统一错误体（v2）。旧接口可能只有 detail —— 见 pickMessage。 */
export interface ApiErrorBody {
  code?: number
  data?: unknown
  message?: string
  detail?: string | Array<{ loc?: (string | number)[]; msg?: string }>
}

/** 业务错误：状态码 + 可直接展示给用户的一句话。 */
export class ApiError extends Error {
  readonly status: number
  readonly body: ApiErrorBody | undefined

  constructor(status: number, message: string, body?: ApiErrorBody) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.body = body
  }
}

/** 从后端错误体里挑一句能给用户看的话。 */
export function pickMessage(body: ApiErrorBody | undefined, status: number): string {
  if (!body) return `请求失败（${status}）`
  if (typeof body.message === 'string' && body.message) return body.message
  if (typeof body.detail === 'string' && body.detail) return body.detail
  // 422 的 detail 是数组（FastAPI 的原始校验错误）
  if (Array.isArray(body.detail)) {
    const first = body.detail[0]
    if (first?.msg) {
      const loc = (first.loc ?? []).filter((x) => x !== 'body').join('.')
      return loc ? `${loc}：${first.msg}` : first.msg
    }
  }
  return `请求失败（${status}）`
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH'
  body?: unknown
  signal?: AbortSignal
}

const BASE = ''

/** 写操作必须带这个头 —— 后端用它挡跨源请求（server.py:REQUEST_HEADER）。 */
const REQUEST_HEADER = 'X-Oryx-Request'

async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const method = opts.method ?? 'GET'
  const headers: Record<string, string> = {}
  if (method !== 'GET') headers[REQUEST_HEADER] = '1'

  let payload: string | undefined
  if (opts.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(opts.body)
  }

  let res: Response
  try {
    res = await fetch(BASE + path, {
      method,
      headers,
      body: payload,
      credentials: 'same-origin',
      signal: opts.signal,
    })
  } catch (err) {
    // 网络层失败（内核没起来、断网）—— 这不是 HTTP 错误，单独处理
    if (err instanceof DOMException && err.name === 'AbortError') throw err
    throw new ApiError(0, '连不上内核服务 —— 它可能还没启动', undefined)
  }

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
    throw new ApiError(res.status, pickMessage(body, res.status), body)
  }

  return parsed as T
}

export const api = {
  get: <T>(path: string, opts?: { signal?: AbortSignal }) => request<T>(path, { ...opts, method: 'GET' }),
  post: <T>(path: string, body?: unknown, opts?: { signal?: AbortSignal }) =>
    request<T>(path, { ...opts, method: 'POST', body }),
  put: <T>(path: string, body?: unknown, opts?: { signal?: AbortSignal }) =>
    request<T>(path, { ...opts, method: 'PUT', body }),
  del: <T>(path: string, opts?: { signal?: AbortSignal }) => request<T>(path, { ...opts, method: 'DELETE' }),
}

/** SSE 订阅 —— EventSource 带不了自定义头，靠同源自动带 HttpOnly cookie。 */
export function subscribe(path: string, onEvent: (ev: Record<string, unknown>) => void): () => void {
  const es = new EventSource(path)
  es.onmessage = (e) => {
    try {
      onEvent(JSON.parse(e.data) as Record<string, unknown>)
    } catch {
      /* 收到非 JSON 的帧（心跳等）—— 忽略 */
    }
  }
  return () => es.close()
}
