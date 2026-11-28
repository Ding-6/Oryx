/**
 * 团队与自动任务纯函数断言 —— `npm run check:ops`
 *
 * 最重要的一条：**整体保存的请求体只能带 name / goal / members**。
 * 后端靠「有没有 members」区分整体保存与旧的「改单个成员分工」，
 * 要是把旧字段 member / role 一起带上，就会走回多次串行请求的老路。
 * 另外钉住单成员换人（旧的先删后加必失败）、派发轮询上限、启停防重复。
 */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const esbuild = require('esbuild')

const src = fs.readFileSync(
  path.join(__dirname, '..', 'src', 'features', 'operations', 'ops.ts'),
  'utf8',
)
const js = esbuild.transformSync(src, { loader: 'ts', format: 'cjs' }).code
const mod = { exports: {} }
new Function('module', 'exports', js)(mod, mod.exports)
const {
  TEAM_MEMBER_MIN, TEAM_MEMBER_MAX, TEAM_NAME_MAX, TEAM_GOAL_MAX,
  SAVE_TEAM_HINT, DISPATCH_POLL_MS, DISPATCH_MAX_POLLS, DISPATCH_TIMEOUT_HINT,
  draftFromTeam, teamSaveBody, teamCreateBody, validateTeamDraft, teamDraftEquals,
  isDispatchRunning, dispatchStatusText, dispatchResultNotice,
  shouldContinuePolling, dispatchTimedOut, dispatchDuration,
  busyKey, toggleIntent, canToggle, toggleNotice, validateAutomationDraft,
  runStatusLabel,
} = mod.exports

let checks = 0
const ok = (cond, msg) => { assert.ok(cond, msg); checks += 1 }
const eq = (actual, expected, msg) => { assert.equal(actual, expected, msg); checks += 1 }
const deep = (actual, expected, msg) => { assert.deepEqual(actual, expected, msg); checks += 1 }

const member = (agent_id, role = '') => ({ agent_id, role })
const draft = (over = {}) => ({ name: '研究小组', goal: '做调研', members: [member('a', '调研')], ...over })

// ── 整体保存的请求体形状 ────────────────────────────────────────────
const body = teamSaveBody(draft())
deep(Object.keys(body).sort(), ['goal', 'members', 'name'], '整体保存只带三个字段')
ok(!('member' in body), '不能带旧的 member 字段')
ok(!('role' in body), '不能带旧的 role 字段')
deep(body.members, [{ agent_id: 'a', role: '调研' }], '成员按 agent_id/role 传')
eq(body.name, '研究小组', '名字要 trim')
eq(teamSaveBody(draft({ name: '  新名  ' })).name, '新名', '名字前后空格要去掉')
eq(teamSaveBody(draft({ members: [member('a', '  ')] })).members[0].role, '', '空职责归一化成空串')
deep(teamCreateBody(draft()), teamSaveBody(draft()), '新建与整体保存同形')
ok(SAVE_TEAM_HINT.includes('整体'), '保存提示要说明整体语义')

// ── 草稿校验：成员下限 / 上限 / 重复 / 斜杠 ─────────────────────────
eq(TEAM_MEMBER_MIN, 1, '下限 1 位')
eq(TEAM_MEMBER_MAX, 8, '上限 8 位')
eq(validateTeamDraft(draft()), null, '正常草稿通过')
eq(validateTeamDraft(draft({ name: '  ' })).field, 'name', '名字不能空')
eq(validateTeamDraft(draft({ name: 'a'.repeat(TEAM_NAME_MAX + 1) })).field, 'name', '名字超长')
eq(validateTeamDraft(draft({ name: '坏/名字' })).field, 'name', '名字不能带斜杠')
eq(validateTeamDraft(draft({ name: '坏\\名字' })).field, 'name', '反斜杠也不行')
eq(validateTeamDraft(draft({ goal: 'x'.repeat(TEAM_GOAL_MAX + 1) })).field, 'goal', '目标超长')
eq(validateTeamDraft(draft({ members: [] })).message, '至少要选择 1 位子 Agent', '空成员要先拦')
eq(validateTeamDraft(draft({ members: Array.from({ length: 9 }, (_, i) => member(`a${i}`)) })).field,
  'members', '超过 8 位要先拦')
eq(validateTeamDraft(draft({ members: [member('a'), member('a')] })).message,
  '成员「a」重复了', '重复成员要先拦')
eq(validateTeamDraft(draft({ members: [member('')] })).field, 'members', '成员 id 不能空')
eq(validateTeamDraft(draft({ members: [member('a', 'x'.repeat(81))] })).field, 'members', '职责超长')

// 关键回归：单成员换人是合法的（旧的「先删后加」会撞「至少要留一个成员」）
eq(validateTeamDraft(draft({ members: [member('b', '成稿')] })), null,
  '单成员团队换人必须合法，不许先删后加')

// ── 草稿 diff ───────────────────────────────────────────────────────
ok(teamDraftEquals(draft(), draft()), '相同草稿相等')
ok(!teamDraftEquals(draft(), draft({ goal: '改了' })), '目标改了要算改动')
ok(!teamDraftEquals(draft(), draft({ name: '改名' })), '改名要算改动')
ok(!teamDraftEquals(draft(), draft({ members: [member('b')] })), '换成员要算改动')
ok(!teamDraftEquals(draft(), draft({ members: [member('a', '换了职责')] })), '改职责要算改动')
ok(teamDraftEquals(
  draft({ name: '  研究小组  ', members: [member(' a ', ' 调研 ')] }),
  draft(),
), 'trim 之后相同的草稿不该算改动')

const detail = {
  id: 't1', name: '研究小组', goal: '做调研',
  members: [member('a', '调研')], thread: [], message_count: 0, unread: 0,
  creator_id: 'u1', shared: false, can_edit: true,
}
const fromDetail = draftFromTeam(detail)
deep(fromDetail.members, [member('a', '调研')], '从详情生成草稿')
ok(fromDetail.members !== detail.members, '草稿要是拷贝，不能指向详情原数组')
fromDetail.members[0].role = '改坏了'
eq(detail.members[0].role, '调研', '改草稿不能污染详情')

// ── 派发作业 ───────────────────────────────────────────────────────
const running = { job_id: 'j1', team: 't', agent: 'a', status: 'running', content: '', error: '', started_at: 100, finished_at: null }
const done = { ...running, status: 'done', content: '结论在这里', finished_at: 103 }
const failed = { ...running, status: 'failed', error: '模型超时', finished_at: 104 }

ok(isDispatchRunning(running), 'running 算在途')
ok(!isDispatchRunning(done) && !isDispatchRunning(failed) && !isDispatchRunning(null), '完成/失败/空都不算在途')
eq(dispatchStatusText(running), '派发中…', '在途文案')
eq(dispatchStatusText(done), '已完成派发', '完成文案')
eq(dispatchStatusText(failed), '派发失败', '失败文案')
eq(dispatchStatusText(null), '', '没作业时没文案')

ok(dispatchResultNotice(done, '调研员').includes('结论在这里'), '成功通知带结论')
ok(dispatchResultNotice(failed, '调研员').includes('模型超时'), '失败通知带原因')
ok(dispatchResultNotice({ ...done, content: '' }, '调研员').includes('没有产出文本'), '空产出要说清楚')
ok(dispatchResultNotice({ ...done, content: 'x'.repeat(300) }, '调研员').endsWith('…'), '长结论要截断')

ok(shouldContinuePolling(running, 0), 'running 要继续轮询')
ok(!shouldContinuePolling(done, 0), 'done 立刻停')
ok(!shouldContinuePolling(failed, 0), 'failed 立刻停')
ok(!shouldContinuePolling(running, DISPATCH_MAX_POLLS), '超上限必须停')
ok(dispatchTimedOut(running, DISPATCH_MAX_POLLS), '超上限要标超时')
ok(!dispatchTimedOut(done, DISPATCH_MAX_POLLS), '已完成不算超时')
ok(DISPATCH_POLL_MS >= 500 && DISPATCH_POLL_MS <= 3000, '轮询间隔别太密也别太慢')
ok(DISPATCH_TIMEOUT_HINT.includes('信箱'), '超时提示要指向持久化的信箱')
eq(dispatchDuration(done), 3, '完成耗时按 finished_at 算')
ok(dispatchDuration(running) >= 0, '在途耗时不能是负数')

// ── 启停防重复 ─────────────────────────────────────────────────────
const item = (enabled, id = 'auto1') => ({ id, name: '早报', enabled })
eq(toggleIntent(item(true)), false, '运行中 → 目标是停用')
eq(toggleIntent(item(false)), true, '已停用 → 目标是启用')
eq(toggleNotice(true), '自动任务已启用', '启用文案按目标态')
eq(toggleNotice(false), '自动任务已停用', '停用文案按目标态')
const busy = new Set([busyKey('toggle', 'auto1'), busyKey('delete', 'auto2')])
ok(!canToggle(item(true, 'auto1'), busy), '同一任务在途不能再点')
ok(canToggle(item(false, 'auto9'), busy), '别的任务不受影响')
ok(canToggle(item(true, 'auto2'), busy), '删除在途不挡启停（key 要区分动作）')

eq(validateAutomationDraft({ name: '早报', cron: '0 9 * * 1-5', goal: '生成简报' }), null, '正常任务通过')
eq(validateAutomationDraft({ name: '', cron: '0 9 * * 1-5', goal: 'x' }).field, 'name', '名字必填')
eq(validateAutomationDraft({ name: 'x', cron: '', goal: 'x' }).field, 'cron', 'Cron 必填')
eq(validateAutomationDraft({ name: 'x', cron: '0 9 * * *', goal: '' }).field, 'goal', '目标必填')
eq(runStatusLabel('done'), '成功', '运行历史状态中文')
eq(runStatusLabel('failed'), '失败', '失败状态中文')
eq(runStatusLabel('running'), '运行中', '在途状态中文')
eq(runStatusLabel('别的'), '运行中', '未知状态兜底成运行中')

console.log(`check:ops 通过 ${checks} 项断言`)
