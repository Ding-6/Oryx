/**
 * 检查点回退纯函数断言 —— `npm run check:checkpoints`
 *
 * 钉住最容易悄悄退错位置的一条：
 *   `msg_count` 是**全部消息**里的下标（用户与助手都占位），
 *   不是「第几条用户提问」。只数用户消息会让回退按钮挂到上一轮。
 */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const esbuild = require('esbuild')

const src = fs.readFileSync(
  path.join(__dirname, '..', 'src', 'features', 'sessions', 'checkpoints.ts'),
  'utf8',
)
const js = esbuild.transformSync(src, { loader: 'ts', format: 'cjs' }).code
const mod = { exports: {} }
new Function('module', 'exports', js)(mod, mod.exports)
const {
  REWIND_SCOPES,
  checkpointForMessage,
  canRewind,
  rewindImpact,
  summarizeRewind,
  scopeLabel,
  formatCheckpointTime,
} = mod.exports

let checks = 0
const ok = (cond, msg) => { assert.ok(cond, msg); checks += 1 }

const cps = [
  { id: 'cp1', seq: 0, label: '第一轮', created_at: 1700000000, files: 1, msg_count: 0, current: false },
  { id: 'cp2', seq: 1, label: '第二轮', created_at: 1700000100, files: 2, msg_count: 2, current: true },
  // 重试会产生同一个 msg_count 的新点：要取 seq 最大的
  { id: 'cp2b', seq: 3, label: '第二轮重试', created_at: 1700000200, files: 0, msg_count: 2, current: false },
]

// ── checkpointForMessage：全部消息的下标 ─────────────────────
ok(checkpointForMessage(cps, 0) === cps[0], 'msg_count=0 认领第一条提问')
ok(checkpointForMessage(cps, 2) === cps[2], '同一个 msg_count 取 seq 最大的那条')
ok(checkpointForMessage(cps, 1) === null, '下标 1 是助手回复，不该挂回退按钮')
ok(checkpointForMessage(cps, 9) === null, '没有对应检查点时返回 null')
ok(checkpointForMessage(null, 0) === null, '检查点还没加载时不崩')
ok(checkpointForMessage(undefined, 0) === null, 'undefined 也不崩')
ok(checkpointForMessage(cps, -1) === null, '负下标无效')
ok(checkpointForMessage(cps, 1.5) === null, '非整数下标无效')

// ── canRewind ────────────────────────────────────────────────
ok(canRewind('running') === false, '运行中不能回退')
ok(canRewind('done') === true, '已结束可以回退')
ok(canRewind('failed') === true, '失败也可以回退')
ok(canRewind('') === true, '状态未知时放行，交给后端判定')

// ── rewindImpact ─────────────────────────────────────────────
const impact = rewindImpact(cps[1], 4)
ok(impact.files === 2, '文件的代价取检查点记录的 files')
ok(impact.messages === 2, '消息的代价 = 总数 - msg_count')
ok(rewindImpact(cps[0], 4).messages === 4, '退到最开头会删掉全部消息')
ok(rewindImpact(cps[1], 2).messages === 0, '退到末尾不删消息')
ok(rewindImpact(null, 4).files === 0 && rewindImpact(null, 4).messages === 0, '空检查点不报错')
ok(rewindImpact({ ...cps[1], msg_count: 99 }, 2).messages === 0, '异常 msg_count 不能算成负数')

// ── summarizeRewind ──────────────────────────────────────────
const summary = summarizeRewind({
  scope: 'CodeAndConversation',
  restored: ['a.txt', 'b.txt'],
  removed: ['c.txt'],
  skipped: ['d.txt'],
  errors: ['e.txt: 权限不足'],
  messages_removed: 3,
  steps_removed: 5,
})
ok(summary.some((x) => x.includes('还原 2 个文件')), '要说还原了几个文件')
ok(summary.some((x) => x.includes('删除 1 个新建文件')), '新建文件被删也要说')
ok(summary.some((x) => x.includes('删掉 3 条消息')), '消息条数要说')
ok(summary.some((x) => x.includes('清掉 5 条执行记录')), '执行记录要说')
ok(summary.some((x) => x.includes('1 个文件跳过')), '跳过要说')
ok(summary.some((x) => x.includes('1 个文件失败')), '失败要说')
ok(summarizeRewind(null).length === 0, 'null 结果返回空')
ok(
  summarizeRewind({ scope: 'Code', restored: [], removed: [], skipped: [], errors: [], messages_removed: 0, steps_removed: 0 }).length === 0,
  '什么都没动时不编造文案',
)

// ── 范围与时间 ───────────────────────────────────────────────
ok(REWIND_SCOPES.length === 3, '三个范围')
ok(REWIND_SCOPES.map((s) => s.scope).join(',') === 'Code,Conversation,CodeAndConversation',
  '范围 id 必须与后端 SCOPES 完全一致')
ok(scopeLabel('Code') === '只还原文件', '范围中文名')
ok(scopeLabel('weird') === 'weird', '未知范围原样显示')
ok(formatCheckpointTime(0) === '—', '没有时间显示破折号')
ok(formatCheckpointTime(null) === '—', 'null 时间不崩')
ok(/^\d{2}-\d{2} \d{2}:\d{2}$/.test(formatCheckpointTime(1700000000)), '时间格式为 月-日 时:分')

console.log(`checkpoint assertions: ALL PASS (${checks} checks)`)
