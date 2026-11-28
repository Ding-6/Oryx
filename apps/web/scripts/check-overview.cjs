/**
 * 总览纯函数断言 —— `npm run check:overview`
 *
 * 钉住两件事：**prompt 型建议也必须有可点的动作**（原来只有 action 型显示按钮，
 * 而绝大多数建议是 prompt 型），以及哪些 SSE 事件该触发总览刷新。
 */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const esbuild = require('esbuild')

const src = fs.readFileSync(
  path.join(__dirname, '..', 'src', 'features', 'overview', 'overviewOps.ts'),
  'utf8',
)
const js = esbuild.transformSync(src, { loader: 'ts', format: 'cjs' }).code
const mod = { exports: {} }
new Function('module', 'exports', js)(mod, mod.exports)
const {
  RUN_STATUS_CN,
  PREVIEW_BASE,
  previewUrl,
  suggestionAction,
  actionButtonLabel,
  hasExecutableAction,
  REFRESH_THROTTLE_MS,
  shouldRefresh,
  throttleDelay,
  timeAgo,
  statCards,
} = mod.exports

let checks = 0
const ok = (cond, msg) => { assert.ok(cond, msg); checks += 1 }
const eq = (actual, expected, msg) => { assert.equal(actual, expected, msg); checks += 1 }
const deep = (actual, expected, msg) => { assert.deepEqual(actual, expected, msg); checks += 1 }

// ── 建议动作 ───────────────────────────────────────────────────────
eq(PREVIEW_BASE, '/api/files/preview', '预览接口路径')
eq(previewUrl('D:/out/report.html'), '/api/files/preview?path=D%3A%2Fout%2Freport.html', '路径要编码')
eq(previewUrl('  a b.svg  '), '/api/files/preview?path=a%20b.svg', '两端空白去掉、内部空格编码')
eq(previewUrl(''), null, '空路径不能生成预览链接')
eq(previewUrl(null), null, 'null 路径不能生成预览链接')

const preview = suggestionAction({ label: '在界面里预览', action: 'preview', path: 'D:/out/a.html', kind: 'preview' })
eq(preview.kind, 'preview', 'preview 型识别')
ok(preview.url.includes('a.html'), 'preview 型带可打开的地址')
eq(actionButtonLabel(preview), '预览', '预览按钮文案')
eq(hasExecutableAction(preview), true, '预览可执行')

// 关键回归：绝大多数建议是 prompt 型，原来界面根本没给按钮。
const prompt = suggestionAction({ label: '画成图表', prompt: '把 a.xlsx 画成柱状图', path: 'a.xlsx', kind: 'chart' })
eq(prompt.kind, 'prompt', 'prompt 型要变成可执行动作')
eq(prompt.prompt, '把 a.xlsx 画成柱状图', '原文带过去，不改写')
eq(actionButtonLabel(prompt), '用这条新建运行', 'prompt 型按钮文案')
eq(hasExecutableAction(prompt), true, 'prompt 型必须可点')

const blank = suggestionAction({ label: '没有内容', path: '', kind: 'x' })
eq(blank.kind, 'none', '既没 action 也没 prompt 就不给动作')
eq(actionButtonLabel(blank), '', 'none 不渲染按钮')
eq(hasExecutableAction(blank), false, 'none 不可执行')
eq(suggestionAction(null).kind, 'none', '空建议不炸')
eq(suggestionAction({ label: 'x', action: 'preview' }).kind, 'none', 'preview 但没路径不给动作')
eq(suggestionAction({ label: 'x', prompt: '   ' }).kind, 'none', '全空白 prompt 不给动作')

// ── 自动刷新 ───────────────────────────────────────────────────────
eq(REFRESH_THROTTLE_MS, 400, '节流窗口')
for (const type of ['RUN_STARTED', 'RUN_FINISHED', 'RUN_ERROR', 'RUN_CANCELLED',
  'step.created', 'step.done', 'step.awaiting_approval', 'team.saved',
  'knowledge.document', 'resync']) {
  eq(shouldRefresh(type), true, `${type} 应该触发刷新`)
}
for (const type of ['ready', '', 'TEXT_MESSAGE_CONTENT', 'heartbeat', null, undefined, 123]) {
  eq(shouldRefresh(type), false, `${String(type)} 不该触发刷新`)
}
eq(throttleDelay(0), 400, '刚刷新过要等满一个窗口')
eq(throttleDelay(150), 250, '半个窗口后只等剩余')
eq(throttleDelay(400), 0, '窗口过了立刻刷')
eq(throttleDelay(999), 0, '超过窗口不等')
eq(throttleDelay(-10), 400, '负数按 0 处理')
eq(throttleDelay(NaN), 400, 'NaN 按 0 处理')

const now = new Date(2026, 10, 26, 12, 0, 0).getTime()
const sec = (ms) => ms / 1000
eq(timeAgo(sec(now), now), '0 秒前', '刚刚')
eq(timeAgo(sec(now - 30_000), now), '30 秒前', '半分钟')
eq(timeAgo(sec(now - 120_000), now), '2 分钟前', '两分钟')
eq(timeAgo(sec(now - 7200_000), now), '2 小时前', '两小时')
eq(timeAgo(sec(now - 3 * 86400_000), now), '3 天前', '三天')
eq(timeAgo(sec(now + 60_000), now), '0 秒前', '未来时间夹到 0')
eq(RUN_STATUS_CN.running, '进行中', '状态中文')
eq(RUN_STATUS_CN.done, '已完成', '状态中文')

const cards = statCards({ runs: [{ id: 'a' }, { id: 'b' }], total: 9, active: 1, today: 3, window: 20 },
  { team_count: 2, member_count: 5, teams: [], agents: [], agent_count: 0, degraded: [] })
eq(cards.length, 4, '四张统计卡')
eq(cards[0].value, 2, '最近运行取列表长度')
eq(cards[0].hint, '共 9 轮', '总数取全量聚合，不是列表长度')
eq(cards[1].value, 1, '进行中')
eq(cards[2].value, 3, '今天动过')
eq(cards[3].value, 2, '编队数')
eq(cards[3].hint, '5 个成员', '成员数')
deep(statCards(null, null).map((c) => c.value), [0, 0, 0, 0], '没数据时全 0，不炸')

console.log(`check:overview OK — ${checks} 项断言全过`)
