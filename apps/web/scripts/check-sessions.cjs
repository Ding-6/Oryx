/**
 * 会话列表纯函数断言 —— `npm run check:sessions`
 *
 * 最重要的一条：**后端 updated_at 是秒**，`new Date(ts)` 会当成毫秒，
 * 结果所有会话都被分到「更早」。这条回归不钉住，左栏分组永远是错的。
 * 另外钉住服务端搜索的参数构造、分页判断、改名/待办校验、
 * 以及「运行中的会话不许删」。
 */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const esbuild = require('esbuild')

const src = fs.readFileSync(
  path.join(__dirname, '..', 'src', 'features', 'sessions', 'sessionOps.ts'),
  'utf8',
)
const js = esbuild.transformSync(src, { loader: 'ts', format: 'cjs' }).code
const mod = { exports: {} }
new Function('module', 'exports', js)(mod, mod.exports)
const {
  PAGE_SIZE,
  SESSION_STATUS_CN,
  sessionStatusLabel,
  groupLabel,
  groupSessions,
  buildSessionQuery,
  hasMorePage,
  validateSessionTitle,
  validateTodoTitle,
  sessionMeta,
  todoSummary,
  canDeleteSession,
  DELETE_RUNNING_HINT,
} = mod.exports

let checks = 0
const ok = (cond, msg) => { assert.ok(cond, msg); checks += 1 }
const eq = (actual, expected, msg) => { assert.equal(actual, expected, msg); checks += 1 }

const now = new Date(2026, 10, 26, 12, 0, 0).getTime()
const sec = (ms) => ms / 1000

// ── 时间分组：秒 → 毫秒 ────────────────────────────────────────────
eq(groupLabel(sec(now - 3600_000), now), '今天', '一小时前是今天')
eq(groupLabel(sec(now - 86400_000), now), '昨天', '前一天是昨天')
eq(groupLabel(sec(now - 3 * 86400_000), now), '本周', '三天前是本周')
eq(groupLabel(sec(now - 10 * 86400_000), now), '本月', '十天前是本月')
eq(groupLabel(sec(now - 60 * 86400_000), now), '更早', '两个月前是更早')
// 关键回归：1700000000 是 2023 年的秒。没乘 1000 就会被当成 1970 年 → 「更早」。
const ts = 1700000000
eq(groupLabel(ts, ts * 1000), '今天', '秒级时间戳必须乘 1000，否则永远落到「更早」')
eq(groupLabel(0, now), '更早', '0 不该被当成今天')
eq(groupLabel(-1, now), '更早', '负数不该被当成今天')
eq(groupLabel(NaN, now), '更早', 'NaN 不该被当成今天')
eq(groupLabel(undefined, now), '更早', '缺失值不该被当成今天')

const mk = (id, updated, status = 'done', msg_count = 0, todo_open = 0) => ({
  id, goal: 'g', title: id, status, mode: 'craft', provider: 'p',
  created_at: sec(updated), updated_at: sec(updated),
  msg_count, step_count: 0, todo_open, renamed: false,
})
const groups = groupSessions([
  mk('a', now - 3600_000),
  mk('b', now - 86400_000),
  mk('c', now - 60 * 86400_000),
], now)
eq(groups.length, 3, '三个时间桶')
eq(groups[0][0], '今天', '先展示今天')
eq(groups[1][0], '昨天', '再展示昨天')
eq(groups[2][0], '更早', '最后展示更早')
eq(groups[0][1][0].id, 'a', '分组内保持传入顺序')
eq(groupSessions([], now).length, 0, '空列表没有分组')

// ── 查询串 ─────────────────────────────────────────────────────────
eq(buildSessionQuery(), 'limit=50&offset=0', '默认值')
const q1 = new URLSearchParams(buildSessionQuery({ limit: 30, offset: 30, q: '周报' }))
eq(q1.get('limit'), '30', 'limit 透传')
eq(q1.get('offset'), '30', 'offset 透传')
eq(q1.get('q'), '周报', 'q 用 URLSearchParams 编码，不手拼')
ok(!buildSessionQuery({ q: '   ' }).includes('q='), '空白搜索词不进查询串')
eq(new URLSearchParams(buildSessionQuery({ limit: 9999 })).get('limit'), '200', 'limit 上限 200')
eq(new URLSearchParams(buildSessionQuery({ limit: 0 })).get('limit'), '1', 'limit 下限 1')
eq(new URLSearchParams(buildSessionQuery({ offset: -10 })).get('offset'), '0', 'offset 不能为负')
eq(new URLSearchParams(buildSessionQuery({ q: '100%_done' })).get('q'), '100%_done',
  '搜索里的 % 和 _ 是字面量，编码后原样送到后端')

// ── 分页 ───────────────────────────────────────────────────────────
eq(PAGE_SIZE, 30, '一页 30 条')
ok(hasMorePage(30), '满页 → 还有下一页')
ok(!hasMorePage(29), '不足一页 → 到底了')
ok(!hasMorePage(0), '空页 → 到底了')
ok(!hasMorePage(30, 0), 'pageSize 为 0 不该显示加载更多')
ok(hasMorePage(5, 5), '自定义 pageSize 也生效')

// ── 校验 ───────────────────────────────────────────────────────────
ok(validateSessionTitle('').ok, '空串合法：撤掉自定义名字')
ok(validateSessionTitle('   ').ok, '全空格按空串处理')
ok(validateSessionTitle('名'.repeat(60)).ok, '刚好 60 字可以')
ok(!validateSessionTitle('名'.repeat(61)).ok, '61 字要拦')
ok(validateSessionTitle('名'.repeat(61)).message.includes('60'), '报错要说清上限')
ok(validateSessionTitle('  名字  ').ok, '前后空格不算进长度')
ok(!validateTodoTitle('').ok, '待办不能没标题')
ok(!validateTodoTitle('   ').ok, '待办全空格不行')
ok(validateTodoTitle('字'.repeat(200)).ok, '待办 200 字可以')
ok(!validateTodoTitle('字'.repeat(201)).ok, '待办 201 字要拦')

// ── 文案与汇总 ─────────────────────────────────────────────────────
eq(SESSION_STATUS_CN.idle, '未开始', '空会话有中文状态')
eq(sessionStatusLabel('idle'), '未开始', 'idle 走中文')
eq(sessionStatusLabel('running'), '进行中', 'running 走中文')
eq(sessionStatusLabel('weird'), 'weird', '未知状态原样显示，不假装认识')
eq(sessionMeta(mk('x', now, 'running', 3, 2)), '进行中 · 3 条消息 · 2 项待办', '列表第二行')
eq(sessionMeta(mk('x', now, 'idle', 0, 0)), '未开始', '空会话不显示计数')
eq(todoSummary([]).percent, 0, '空待办 0%')
eq(todoSummary([
  { id: '1', seq: 1, title: 'a', status: 'done' },
  { id: '2', seq: 2, title: 'b', status: 'pending' },
]).open, 1, '还有一项没做完')
eq(todoSummary([
  { id: '1', seq: 1, title: 'a', status: 'done' },
  { id: '2', seq: 2, title: 'b', status: 'in_progress' },
  { id: '3', seq: 3, title: 'c', status: 'pending' },
]).percent, 33, '1/3 取整成 33%')

ok(!canDeleteSession('running'), '运行中的会话不许删')
ok(canDeleteSession('done'), '已完成的可以删')
ok(canDeleteSession('idle'), '空会话可以删')
ok(DELETE_RUNNING_HINT.includes('停止'), '提示要告诉用户先停止')

// ── 停止必须带上当前会话的 task_id（模块 11） ──────────────────────
//
// 后端的中止信号改成「每个运行一份」之后，停止请求必须能精确指出
// 停哪一轮。不带 task_id 的停止会退化成「停当前那一个」——
// 两个会话并发时就会停错。
//
// 这条钉住：会话页的停止按钮永远带 task_id。
const detailSrc = fs.readFileSync(
  path.join(__dirname, '..', 'src', 'features', 'sessions', 'SessionDetail.tsx'),
  'utf8',
)
ok(
  detailSrc.includes("api.post('/api/chat/cancel', { task_id: taskId })"),
  '停止请求必须带 task_id —— 否则并发时停错会话',
)
ok(
  !/api\/chat\/cancel'?\s*,\s*\{\s*\}/.test(detailSrc),
  '停止请求不能发空 body',
)

console.log(`check:sessions 通过 ${checks} 项`)
