/**
 * 全局接口层纯函数断言 —— `npm run check:api`
 *
 * 重点是别做成假功能：
 *   · 请求超时要有档位且能区分「用户取消」和「真超时」；
 *   · 全局 401 不能把「未登录时探测 /api/auth/me」误判成会话过期；
 *   · error 横幅不能顶掉整个主区；
 *   · 裸对象接口清单是护栏 —— unwrap 对「恰好带 data 键」的裸对象不能误剥。
 */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const esbuild = require('esbuild')

const WEB = path.join(__dirname, '..')
const CORE = path.join(__dirname, '..', '..', '..', 'core')

const src = fs.readFileSync(path.join(WEB, 'src', 'lib', 'apiContracts.ts'), 'utf8')
const js = esbuild.transformSync(src, { loader: 'ts', format: 'cjs' }).code
const mod = { exports: {} }
new Function('module', 'exports', js)(mod, mod.exports)
const {
  GET_TIMEOUT_MS, WRITE_TIMEOUT_MS, UPLOAD_TIMEOUT_MS,
  timeoutFor, timeoutMessage, unwrap, pickMessage, isAuthError,
  shouldHandleUnauthorized, errorNotice, BARE_ENDPOINTS, isBareEndpoint,
} = mod.exports

let checks = 0
const ok = (cond, msg) => { assert.ok(cond, msg); checks += 1 }
const eq = (actual, expected, msg) => { assert.equal(actual, expected, msg); checks += 1 }
const deep = (actual, expected, msg) => { assert.deepEqual(actual, expected, msg); checks += 1 }

// ── 超时档位 ───────────────────────────────────────────────────────
eq(GET_TIMEOUT_MS, 15000, 'GET 默认 15 秒')
eq(WRITE_TIMEOUT_MS, 30000, '写操作默认 30 秒')
eq(UPLOAD_TIMEOUT_MS, 120000, '上传默认 120 秒')
eq(timeoutFor('GET'), 15000, 'GET 走只读档')
eq(timeoutFor('get'), 15000, '方法名大小写不敏感')
eq(timeoutFor('HEAD'), 15000, 'HEAD 走只读档')
eq(timeoutFor('OPTIONS'), 15000, 'OPTIONS 走只读档')
eq(timeoutFor('POST'), 30000, 'POST 走写档')
eq(timeoutFor('PUT'), 30000, 'PUT 走写档')
eq(timeoutFor('DELETE'), 30000, 'DELETE 走写档')
eq(timeoutFor('GET', true), 120000, 'FormData 优先于方法档')
eq(timeoutFor('POST', true), 120000, '上传 POST 也是 120 秒')
eq(timeoutMessage(15000), '请求超时（15 秒），内核可能正忙，稍后再试', '超时文案带秒数')
ok(timeoutMessage(30000).includes('30 秒'), '写操作超时文案带秒数')

// ── unwrap：v2 剥壳，裸对象原样 ────────────────────────────────────
deep(unwrap({ code: 0, data: [1, 2], message: '' }), [1, 2], 'v2 的 data 是数组')
deep(unwrap({ code: 0, data: { mode: 'craft' }, message: 'ok' }), { mode: 'craft' }, 'v2 的 data 是对象')
eq(unwrap({ code: 0, data: null, message: '' }), null, 'v2 的 data 可以是 null')
deep(unwrap({ mode: 'craft', busy: false }), { mode: 'craft', busy: false }, '裸对象原样透传')
deep(unwrap([{ id: 1 }]), [{ id: 1 }], '裸数组原样透传')
eq(unwrap(null), null, 'null 原样')
eq(unwrap('plain'), 'plain', '字符串原样')
// 关键回归：只看 data 键会把老接口的业务字段误剥一层
deep(unwrap({ data: { x: 1 }, mode: 'craft' }), { data: { x: 1 }, mode: 'craft' }, '缺 code/message 不剥')
deep(unwrap({ code: '0', data: { x: 1 }, message: '' }), { code: '0', data: { x: 1 }, message: '' }, 'code 不是数字不剥')
deep(unwrap({ code: 0, data: { x: 1 } }), { code: 0, data: { x: 1 } }, '缺 message 不剥')
deep(unwrap({ code: 0, data: { x: 1 }, message: '' }), { x: 1 }, '三特征齐才剥')

// ── 错误文案提取 ───────────────────────────────────────────────────
eq(pickMessage(undefined, 500), '请求失败（500）', '无错误体按状态码兜底')
eq(pickMessage({}, 404), '请求失败（404）', '空错误体按状态码兜底')
eq(pickMessage({ message: '人话', detail: '原始' }, 400), '人话', 'message 优先')
eq(pickMessage({ detail: '没有这个会话' }, 404), '没有这个会话', 'detail 字符串')
eq(pickMessage({ error: '老接口报错' }, 500), '老接口报错', '兼容老接口 error 字段')
eq(pickMessage({ detail: { message: '对象里的说明' } }, 400), '对象里的说明', 'detail 是对象时取 message')
eq(pickMessage({ detail: [{ loc: ['body', 'goal'], msg: '不能为空' }] }, 422), 'goal：不能为空', '422 去掉 body 前缀')
eq(pickMessage({ detail: [{ loc: [], msg: '参数不合法' }] }, 422), '参数不合法', '422 无 loc 时只给 msg')
eq(pickMessage({ detail: [] }, 422), '请求失败（422）', '空数组回退状态码')

// ── 全局 401 ───────────────────────────────────────────────────────
eq(isAuthError(401), true, '401 是鉴权错误')
eq(isAuthError(403), false, '403 不是鉴权错误')
eq(isAuthError(0), false, '网络错误不是鉴权错误')
eq(shouldHandleUnauthorized(401, '/api/sessions', true), true, '已登录 + 业务接口 401 → 回登录页')
eq(shouldHandleUnauthorized(401, '/api/auth/me', false), false, '未登录探测 401 不触发')
eq(shouldHandleUnauthorized(401, '/api/auth/login', true), false, 'auth 域自己处理错误')
eq(shouldHandleUnauthorized(403, '/api/sessions', true), false, '403 不触发')
eq(shouldHandleUnauthorized(401, '/api/sessions', false), false, '未登录不触发')

// ── 顶部横幅 ───────────────────────────────────────────────────────
deep(errorNotice(401, '未登录'), { text: '登录状态已失效，请重新登录', retry: false }, '401 固定文案且不重试')
deep(errorNotice(0, '连不上内核服务 —— 它可能还没启动'), { text: '连不上内核服务 —— 它可能还没启动', retry: true }, '网络错误可重试')
deep(errorNotice(503, '内核未就绪'), { text: '内核未就绪', retry: true }, '503 可重试')
deep(errorNotice(500, '服务器内部错误'), { text: '服务器内部错误', retry: true }, '500 可重试')
deep(errorNotice(400, '参数不合法'), { text: '参数不合法', retry: false }, '400 不给重试')
deep(errorNotice(404, '没有这个会话'), { text: '没有这个会话', retry: false }, '404 不给重试')

// ── 裸对象接口清单 ─────────────────────────────────────────────────
ok(BARE_ENDPOINTS.length >= 15, '清单覆盖主要的裸对象接口')
ok(BARE_ENDPOINTS.every((p) => p.startsWith('/api/')), '清单里都是 /api/ 路径')
eq(new Set(BARE_ENDPOINTS).size, BARE_ENDPOINTS.length, '清单没有重复项')
for (const p of ['/api/state', '/api/paste', '/api/approve', '/api/clipboard', '/api/chat/cancel', '/api/index/clear']) {
  ok(BARE_ENDPOINTS.includes(p), `清单包含 ${p}`)
}
eq(isBareEndpoint('/api/state'), true, '精确匹配')
eq(isBareEndpoint('/api/state?since=1'), true, '忽略查询串')
eq(isBareEndpoint('/api/audit/abc'), true, '子路径匹配')
eq(isBareEndpoint('/api/checkpoints/cp1/rewind'), true, '多层子路径匹配')
eq(isBareEndpoint('/api/sessions'), false, 'v2 域不在清单里')
eq(isBareEndpoint(''), false, '空路径不匹配')
eq(isBareEndpoint('/api/stateful'), false, '前缀相同但不是同一路径')

// ── 静态护栏：改动不能悄悄回退 ─────────────────────────────────────
const apiSrc = fs.readFileSync(path.join(WEB, 'src', 'lib', 'api.ts'), 'utf8')
ok(apiSrc.includes('setUnauthorizedHandler'), 'api.ts 挂了全局 401 注册入口')
ok(apiSrc.includes('timeoutFor'), 'api.ts 用了统一超时档位')
ok(apiSrc.includes('AbortController'), 'api.ts 用 controller 管超时')
ok(apiSrc.includes('X-Request-Id'), 'api.ts 透传后端请求 id')
ok(!apiSrc.includes('signal: opts.signal'), '不能只透传外部 signal 而不管超时')
ok(apiSrc.includes("credentials: 'same-origin'"), '同源 cookie 仍在')

const appSrc = fs.readFileSync(path.join(WEB, 'src', 'App.tsx'), 'utf8')
ok(appSrc.includes('setUnauthorizedHandler'), 'App 注册了全局 401 处理')
ok(appSrc.includes('shouldHandleUnauthorized'), 'App 用纯函数挡掉误判')
ok(appSrc.includes('app-error'), 'App 有顶部错误横幅')
ok(!appSrc.includes('{error ? ('), 'error 不再以三元顶掉整个主区')
ok(appSrc.includes('errorNotice('), '横幅用纯函数决定重试')

const commonSrc = fs.readFileSync(path.join(CORE, 'oryx', 'domains', '_common.py'), 'utf8')
ok(commonSrc.includes('def map_ipc_error'), '后端有唯一的 IPC 错误映射')
ok(commonSrc.includes('return map_ipc_error(exc)'), 'rpc_failure 委托同一套规则')
const chatSrc = fs.readFileSync(path.join(CORE, 'oryx', 'domains', 'chat.py'), 'utf8')
ok(chatSrc.includes('class PasteRequest'), '/api/paste 换成 Pydantic 模型')
ok(chatSrc.includes('async def _paste(req: PasteRequest)'), 'paste 路由用模型而非 req: dict')
const sysSrc = fs.readFileSync(path.join(CORE, 'oryx', 'domains', 'system.py'), 'utf8')
ok(sysSrc.includes('return json(await app_state'), '/api/state 仍返回裸对象')
ok(sysSrc.includes('def optimize_payload'), 'prompt/optimize 出参恒为对象')

console.log(`[check:api] ${checks} 项断言全部通过`)
