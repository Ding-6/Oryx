/**
 * 知识库纯函数断言 —— `npm run check:knowledge`
 *
 * 钉住：检索范围必须显式进请求体（空范围不带这个键）、失效范围要认出来、
 * 轮询退避 / 隐藏暂停 / 超时收口、SSE 事件只认当前集合、清空确认文案。
 */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const esbuild = require('esbuild')

const src = fs.readFileSync(
  path.join(__dirname, '..', 'src', 'features', 'knowledge', 'kbOps.ts'),
  'utf8',
)
const js = esbuild.transformSync(src, { loader: 'ts', format: 'cjs' }).code
const mod = { exports: {} }
new Function('module', 'exports', js)(mod, mod.exports)
const {
  ALL_SCOPES,
  scopeOptions,
  normalizeScope,
  scopeLabel,
  buildSearchBody,
  scopeIsStale,
  POLL_START_MS,
  POLL_MAX_MS,
  POLL_LIMIT_MS,
  nextPollDelay,
  shouldPoll,
  pollHint,
  processingSummary,
  isDocumentEvent,
  eventMatchesCollection,
  clearCollectionConfirm,
  clearIndexConfirm,
  clearIndexError,
} = mod.exports

let checks = 0
const ok = (cond, msg) => { assert.ok(cond, msg); checks += 1 }
const eq = (actual, expected, msg) => { assert.equal(actual, expected, msg); checks += 1 }
const deep = (actual, expected, msg) => { assert.deepEqual(actual, expected, msg); checks += 1 }

const collections = [
  { id: 'c1', name: '甲', visibility: 'private', document_count: 2, total_bytes: 10 },
  { id: 'c2', name: '乙', visibility: 'shared', document_count: 0, total_bytes: 0 },
]

// ── 检索范围 ───────────────────────────────────────────────────────
eq(ALL_SCOPES, '', '空串代表全部可见集合')
const options = scopeOptions(collections)
eq(options.length, 3, '全部 + 两个集合')
eq(options[0].value, '', '第一项是所有集合')
eq(options[0].label, '全部可见集合', '第一项文案')
eq(options[1].label, '甲', '个人集合不加后缀')
eq(options[2].label, '乙（共享）', '共享集合标出来')
eq(options[2].visibility, 'shared', '保留可见范围')
deep(scopeOptions([]), [{ value: '', label: '全部可见集合', visibility: 'private' }], '没有集合也保留全部项')

eq(normalizeScope(undefined), '', 'undefined → 全部')
eq(normalizeScope(null), '', 'null → 全部')
eq(normalizeScope('  c1  '), 'c1', '范围两端空白要去掉')
eq(normalizeScope('   '), '', '全空白 → 全部')

eq(scopeLabel(collections, ''), '全部可见集合', '空范围文案')
eq(scopeLabel(collections, 'c1'), '甲', '回显集合名')
eq(scopeLabel(collections, 'c2'), '乙', '共享集合也回显名字')
eq(scopeLabel(collections, '不存在'), '已失效的集合', '集合被删后要明确提示')

deep(buildSearchBody('  关键词  ', ''), { query: '关键词' }, '空范围不带 collection_id 键')
deep(buildSearchBody('关键词', null), { query: '关键词' }, 'null 范围等同空')
deep(buildSearchBody('关键词', '   '), { query: '关键词' }, '空白范围等同空')
deep(buildSearchBody('关键词', ' c1 '), { query: '关键词', collection_id: 'c1' }, '限定集合带 id')
ok(!('collection_id' in buildSearchBody('q', '')), '空范围绝不能带空串 id')

eq(scopeIsStale(collections, ''), false, '空范围不算失效')
eq(scopeIsStale(collections, 'c1'), false, '存在的集合不算失效')
eq(scopeIsStale(collections, '不存在'), true, '查不到的集合算失效')
eq(scopeIsStale([], 'c1'), true, '集合列表还没加载完也算失效')

// ── 解析进度轮询 ───────────────────────────────────────────────────
eq(POLL_START_MS, 1500, '起始间隔')
eq(POLL_MAX_MS, 8000, '最大间隔')
eq(POLL_LIMIT_MS, 180000, '3 分钟收口')
eq(nextPollDelay(POLL_START_MS), 2400, '第一次退避 1.5s → 2.4s')
eq(nextPollDelay(2400), 3840, '继续退避')
eq(nextPollDelay(POLL_MAX_MS), POLL_MAX_MS, '到顶不再涨')
eq(nextPollDelay(0), POLL_START_MS, '0 重置到起始')
eq(nextPollDelay(-1), POLL_START_MS, '负数重置')
eq(nextPollDelay(NaN), POLL_START_MS, 'NaN 重置')
eq(nextPollDelay(Infinity), POLL_START_MS, 'Infinity 按无效值重置到起始')

eq(shouldPoll(true, true, 0), true, '有解析中且页面可见 → 继续')
eq(shouldPoll(false, true, 0), false, '没有解析中 → 立即停')
eq(shouldPoll(true, false, 0), false, '页面隐藏 → 暂停')
eq(shouldPoll(true, true, POLL_LIMIT_MS), false, '超过 3 分钟 → 停')
eq(shouldPoll(true, true, POLL_LIMIT_MS - 1), true, '没超时继续')
eq(pollHint(0), null, '未超时不给提示')
ok(String(pollHint(POLL_LIMIT_MS)).includes('刷新'), '超时提示要告诉用户怎么继续')

const doc = (id, status) => ({
  id, collection_id: 'c1', original_name: id, extension: '.txt', size_bytes: 1,
  sha256: 'x', version: 1, status, note: '', created_at: 0, updated_at: 0,
  parsed_at: null, can_edit: true,
})
const summary = processingSummary([
  doc('a', 'ready'), doc('b', 'queued'), doc('c', 'processing'),
  doc('d', 'failed'), doc('e', 'ready'),
])
eq(summary.total, 5, '总数')
eq(summary.ready, 2, '已就绪数')
eq(summary.failed, 1, '失败数')
eq(summary.processing, 2, 'queued + processing 都算解析中')
eq(summary.label, '解析中 2 篇', '解析中时给进度文案')
eq(processingSummary([]).label, '0 篇可检索', '空列表不炸')
eq(processingSummary([doc('a', 'ready')]).label, '1 篇可检索', '没有解析中时给可检索数')

// ── SSE 事件 ───────────────────────────────────────────────────────
eq(isDocumentEvent({ type: 'knowledge.document' }), true, '认得解析事件')
eq(isDocumentEvent({ type: 'RUN_FINISHED' }), false, '别的不算')
eq(isDocumentEvent(null), false, '空事件不炸')
eq(isDocumentEvent({}), false, '没 type 不算')
eq(eventMatchesCollection({ collection_id: 'c1' }, 'c1'), true, '当前集合的事件要认')
eq(eventMatchesCollection({ collection_id: 'c2' }, 'c1'), false, '别的集合不打断')
eq(eventMatchesCollection({}, 'c1'), false, '缺字段不认')
eq(eventMatchesCollection({ collection_id: 'c1' }, null), false, '没选集合不认')

// ── 清空确认 ───────────────────────────────────────────────────────
ok(clearCollectionConfirm('甲', 2).includes('2 篇'), '确认文案带篇数')
ok(clearCollectionConfirm('甲', 2).includes('保留'), '要说清楚集合会保留')
ok(clearCollectionConfirm('甲', 2).includes('不可撤销'), '要说清楚不可撤销')
ok(clearCollectionConfirm('甲', 0).includes('还没有文档'), '空集合给不同文案')
ok(clearIndexConfirm(12, 34).includes('12'), '索引确认带文件数')
ok(clearIndexConfirm(12, 34).includes('34'), '索引确认带片段数')
ok(clearIndexConfirm(null, undefined).includes('0 个文件'), '非数字兜底成 0')
ok(clearIndexConfirm(1, 2).includes('不删磁盘'), '要说清楚原文件安全')
eq(clearIndexError(409), '正在扫描目录，等扫描结束后再清空索引', '409 给具体操作建议')
ok(clearIndexError(503).includes('内核'), '503 说明是内核没就绪')
ok(clearIndexError(500).includes('失败'), '其它错误给兜底文案')

console.log(`check:knowledge OK — ${checks} 项断言全过`)
