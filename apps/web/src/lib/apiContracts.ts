/**
 * 请求层的纯函数 —— 全局接口层里「能被断言」的那部分。
 *
 * 为什么单独一个文件：`api.ts` 依赖 fetch / EventSource / AbortController，
 * 没法在 Node 里就地转译跑断言。把形状判断、文案提取、超时档位、
 * 401 判定、裸接口清单抽出来，`scripts/check-api.cjs` 就能一键复跑 ——
 * 改请求层不用靠手点浏览器，也不会把「统一」做成假功能。
 */

/** 后端错误体。v2 是 {code,data,message,detail}；老接口可能只有 detail / error。 */
export interface ApiErrorBody {
  code?: number
  data?: unknown
  message?: string
  detail?: string | Array<{ loc?: (string | number)[]; msg?: string }> | { message?: string }
  error?: string
}

/** 成功响应的超时档位（毫秒）。上传给足时间，普通写操作别让用户干等。 */
export const GET_TIMEOUT_MS = 15_000
export const WRITE_TIMEOUT_MS = 30_000
export const UPLOAD_TIMEOUT_MS = 120_000

/** 按方法挑超时；`FormData` 一律走上传档。 */
export function timeoutFor(method: string, isForm = false): number {
  if (isForm) return UPLOAD_TIMEOUT_MS
  const m = String(method || 'GET').toUpperCase()
  return m === 'GET' || m === 'HEAD' || m === 'OPTIONS'
    ? GET_TIMEOUT_MS
    : WRITE_TIMEOUT_MS
}

export function timeoutMessage(ms: number): string {
  return `请求超时（${Math.round(ms / 1000)} 秒），内核可能正忙，稍后再试`
}

/**
 * v2 统一响应 → 领域数据。
 *
 * **怎么认出「这是个 v2 响应」**：只看有没有 `data` 键是不够的 ——
 * 老接口里也有返回体正好带 `data` 字段的，那样会被误剥一层。
 * 所以要求三个特征同时成立：`code` 是数字、`message` 是字符串、`data` 键存在。
 *
 * 还没改造的旧接口返回裸对象，原样透传 —— 迁移期两种都能吃。
 * 见 BARE_ENDPOINTS：这些接口的响应体**不能**被改成 v2 壳。
 */
export function unwrap<T>(body: unknown): T {
  if (body && typeof body === 'object' && !Array.isArray(body)) {
    const maybe = body as { code?: unknown; message?: unknown; data?: unknown }
    if (
      typeof maybe.code === 'number' &&
      typeof maybe.message === 'string' &&
      'data' in maybe
    ) {
      return maybe.data as T
    }
  }
  return body as T
}

/** 从后端错误体里挑一句能给用户看的话。 */
export function pickMessage(body: ApiErrorBody | undefined, status: number): string {
  if (!body) return `请求失败（${status}）`
  if (typeof body.message === 'string' && body.message) return body.message
  if (typeof body.detail === 'string' && body.detail) return body.detail
  // 少数老接口用 {error: "..."}，以前会退化成「请求失败（500）」
  if (typeof body.error === 'string' && body.error) return body.error
  // HTTPException 的 detail 偶尔是对象（{message: "..."}）
  if (body.detail && typeof body.detail === 'object' && !Array.isArray(body.detail)) {
    const nested = (body.detail as { message?: unknown }).message
    if (typeof nested === 'string' && nested) return nested
  }
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

export function isAuthError(status: number): boolean {
  return status === 401
}

/**
 * 401 要不要触发「全局回登录页」。
 *
 * `/api/auth/me` 在未登录时返回 401 是**正常探测**，不是会话过期；
 * 登录 / 注册接口自己的错误也要留给 AuthScreen 显示。
 * 只有在已经登录、且不是 auth 域的 401 才说明 cookie 失效了。
 */
export function shouldHandleUnauthorized(status: number, path: string, hasUser: boolean): boolean {
  if (!isAuthError(status) || !hasUser) return false
  return !String(path || '').startsWith('/api/auth/')
}

/** 顶部横幅的文案与「要不要给重试」。网络/超时/服务端错误才值得重试。 */
export function errorNotice(status: number, message: string): { text: string; retry: boolean } {
  if (status === 401) return { text: '登录状态已失效，请重新登录', retry: false }
  if (status === 0 || status >= 500) return { text: message, retry: true }
  return { text: message, retry: false }
}

/**
 * 老前端（`core/web/modules/*.js`）仍在直接读裸对象的接口。
 *
 * 这些响应**不能**逐域改成 `{code,data,message}` —— 老前端
 * `await (await api('/api/state')).json()` 之后读 `s.mode` 会全部变空。
 * `core/tests/test_error_contract.py` 也钉住了「成功保持裸 dict」。
 *
 * 所以「统一响应」的边界落在前端 `unwrap()`：它同时吃得下 v2 壳与裸对象。
 * 这份清单是改动护栏 —— 谁要把其中一条改成 `ok()`，先确认老前端已退役。
 */
export const BARE_ENDPOINTS: readonly string[] = [
  '/api/state',
  '/api/prewarm',
  '/api/usage',
  '/api/tasks',
  '/api/capabilities',
  '/api/doctor',
  '/api/audit',
  '/api/sys/query',
  '/api/sys/switches',
  '/api/fs/list',
  '/api/files/list',
  '/api/prompt/optimize',
  '/api/chat/cancel',
  '/api/approve',
  '/api/clipboard',
  '/api/paste',
  '/api/index/clear',
  '/api/checkpoints',
  '/api/appearance',
  '/api/memory',
  '/api/features',
]

/** 路径是否落在裸对象接口里（支持子路径，忽略查询串）。 */
export function isBareEndpoint(path: string): boolean {
  const clean = String(path || '').split('?')[0]
  return BARE_ENDPOINTS.some((p) => clean === p || clean.startsWith(`${p}/`))
}
