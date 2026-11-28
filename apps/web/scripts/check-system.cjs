/**
 * 「系统」页纯函数断言 —— `npm run check:system`
 *
 * 重点钉住三件容易被界面「看起来对」掩盖的事：
 *   · 用量窗口是**滚动小时**，不是自然日 / 自然月；
 *   · 导出文件由前端本地生成，不新增后端接口，且文件名必须防路径注入；
 *   · 审计 / 自检的坏数据（null / 越界 / 空数组）不能让整页崩掉。
 */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const esbuild = require('esbuild')

const src = fs.readFileSync(
  path.join(__dirname, '..', 'src', 'features', 'system', 'systemOps.ts'),
  'utf8',
)
const js = esbuild.transformSync(src, { loader: 'ts', format: 'cjs' }).code
const mod = { exports: {} }
new Function('module', 'exports', js)(mod, mod.exports)
const {
  USAGE_WINDOWS, USAGE_HOURS_MIN, USAGE_HOURS_MAX, AUDIT_RISK_CN, EXPORT_KINDS,
  clampUsageHours, usageWindowLabel, formatTokens, usageCards, modelRows,
  doctorStatus, doctorCheckTone, doctorCheckDetail, dependencyRows,
  auditRiskLabel, formatAuditTime, safeFilePart,
  buildAuditExport, buildConversationExport, buildPlanExport, buildExport,
} = mod.exports

let checks = 0
const ok = (cond, msg) => { assert.ok(cond, msg); checks += 1 }
const eq = (actual, expected, msg) => { assert.equal(actual, expected, msg); checks += 1 }
const deep = (actual, expected, msg) => { assert.deepEqual(actual, expected, msg); checks += 1 }

// ── 用量窗口 ───────────────────────────────────────────────────────
eq(USAGE_HOURS_MIN, 1, '下限 1 小时')
eq(USAGE_HOURS_MAX, 8760, '上限 8760 小时（365 天）')
ok(Array.isArray(USAGE_WINDOWS) && USAGE_WINDOWS.length === 4, '四个可选窗口')
ok(USAGE_WINDOWS.every((w) => w.hours > 0 && w.hours <= USAGE_HOURS_MAX), '窗口都在 clamp 范围内')

eq(clampUsageHours(-5), 1, '负数 clamp 到 1')
eq(clampUsageHours(0), 1, '0 clamp 到 1')
eq(clampUsageHours(NaN), 1, 'NaN clamp 到 1')
eq(clampUsageHours(undefined), 1, 'undefined clamp 到 1')
eq(clampUsageHours('168'), 168, '数字字符串按数字处理')
eq(clampUsageHours(99999), 8760, '超大值 clamp 到 8760')
eq(clampUsageHours(25.7), 26, '小数四舍五入')

eq(usageWindowLabel(24), '近 1 天', '24 小时说成 1 天')
eq(usageWindowLabel(720), '近 30 天', '720 小时说成 30 天')
eq(usageWindowLabel(25), '近 25 小时', '零头按小时说')
eq(usageWindowLabel(0), '近 1 小时', '0 被 clamp 后按小时说')

eq(formatTokens(0), '0', '0 显示 0')
eq(formatTokens(-3), '0', '负数不当成有效数字')
eq(formatTokens(NaN), '0', 'NaN 不当成有效数字')
eq(formatTokens(999), '999', '小于 1000 不缩写')
eq(formatTokens(1500), '1.5k', '千级缩写')
eq(formatTokens(2_500_000), '2.5M', '百万级缩写')

const emptyCards = usageCards(null)
eq(emptyCards.length, 4, '没有数据时也给四张卡')
ok(emptyCards.every((c) => c.value === '—'), '没有数据时显示占位符而不是 NaN')

const cards = usageCards({
  window_hours: 720,
  total: { calls: 42, input_tokens: 12000, output_tokens: 3000 },
  today: { calls: 7, input_tokens: 1000, output_tokens: 500 },
  by_model: [],
})
eq(cards.length, 4, '四张概览卡')
eq(cards[0].value, '7', '近 24 小时调用取 today.calls')
eq(cards[1].value, '1.5k', '近 24 小时 Token 是输入 + 输出')
eq(cards[2].value, '42', '窗口内调用取 total.calls')
eq(cards[2].label, '近 30 天调用', '窗口标签按回显的 window_hours 生成')
eq(cards[3].value, '15.0k', '窗口内 Token 合计')
ok(cards[0].hint.includes('滚动'), 'hint 明确写「滚动」，不冒充自然日')
ok(cards[3].hint.includes('720'), 'hint 回显真实窗口小时数')

// 后端把 99999 clamp 成 8760 后回显 —— 展示必须以回显值为准
const clamped = usageCards({
  window_hours: 8760,
  total: { calls: 1, input_tokens: 0, output_tokens: 0 },
  today: { calls: 0, input_tokens: 0, output_tokens: 0 },
  by_model: [],
})
eq(clamped[2].label, '近 365 天调用', '以后端回显的窗口为准')

deep(modelRows(null), [], 'null 用量 → 空模型表')
deep(modelRows({ by_model: null }), [], '坏字段 → 空模型表')
const row = { provider: 'p', model: 'm', calls: 3, inp: 10, outp: 20 }
deep(modelRows({ by_model: [row] }), [row], '正常模型表原样返回')

// ── 自检 ───────────────────────────────────────────────────────────
const noDoctor = doctorStatus(null)
eq(noDoctor.okCount, 0, '没有自检报告时 okCount 为 0')
eq(noDoctor.text, '没有自检项', '没有自检项给人话')
eq(noDoctor.providerReady, false, '没有报告时模型不算就绪')

const mixed = doctorStatus({
  version: '1', python: '3.11', platform: 'Windows', active_provider: 'p',
  checks: [
    { name: '配置', ok: true, detail: '正常' },
    { name: '依赖', ok: false, detail: '缺包' },
  ],
  hints: ['装包'],
})
eq(mixed.okCount, 1, '正常项计数')
eq(mixed.badCount, 1, '注意项计数')
eq(mixed.level, 'warn', '有注意项就是 warn')
eq(mixed.providerReady, true, '有 active_provider 即就绪')
eq(doctorCheckTone({ ok: true }), 'ok', 'ok 项')
eq(doctorCheckTone({ ok: false }), 'warn', '失败项')
eq(doctorCheckDetail({ detail: '' }), '没有更多说明', 'detail 空给占位')
eq(doctorCheckDetail({ detail: '  缺包  ' }), '缺包', 'detail 去空白')

const deps = dependencyRows({
  dependencies: { yaml: { installed: true, purpose: '读 YAML 配置' }, ocr: { installed: false } },
})
eq(deps.length, 2, '依赖行数量')
eq(deps[0].installed, true, '已装依赖')
eq(deps[0].purpose, '读 YAML 配置', '用途取 purpose')
eq(deps[1].purpose, 'ocr', '没有 purpose 时退回包名')
deep(dependencyRows(null), [], 'null 报告 → 空依赖表')

// ── 审计 ───────────────────────────────────────────────────────────
eq(AUDIT_RISK_CN.length, 4, '四档风险')
eq(auditRiskLabel(0), '只读', 'L0')
eq(auditRiskLabel(3), '禁止', 'L3')
eq(auditRiskLabel(9), '未知', '越界给未知')
eq(auditRiskLabel('x'), '未知', '坏值给未知')
eq(formatAuditTime(0), '—', '0 时间戳显示占位')
eq(formatAuditTime('bad'), '—', '坏时间戳显示占位')
ok(!formatAuditTime(1700000000).includes('1970') && formatAuditTime(1700000000) !== '—',
  '秒级时间戳要乘 1000，不能落到 1970')

eq(safeFilePart('abc-123'), 'abc-123', '安全字符保留')
eq(safeFilePart('a/b'), 'a_b', '斜杠被替换')
ok(!safeFilePart('a/b\\c').includes('/') && !safeFilePart('a/b\\c').includes('\\'), '正反斜杠都不能留在文件名里')
eq(safeFilePart(''), 'session', '空值兜底')
eq(safeFilePart('x'.repeat(100)).length, 48, '文件名片段截断到 48 字')

const auditRows = [{ id: '1', ts: 1, task_id: 't', step_id: 's', tool: 'write_file', args: '{}', risk: 1, decision: 'approved', outcome: 'ok' }]
const auditFile = buildAuditExport('a/b', auditRows)
eq(auditFile.filename, 'audit-a_b.json', '审计文件名防路径注入')
eq(auditFile.mime, 'application/json;charset=utf-8', '审计是 JSON')
deep(JSON.parse(auditFile.content), auditRows, '审计内容可解析且与原数据一致')
eq(buildAuditExport('t', undefined).content, '[]', '没有审计记录导出空数组')

const conv = buildConversationExport('t/1', [
  { role: 'user', content: '你好' },
  { role: 'assistant', content: '收到' },
])
eq(conv.filename, 'conversation-t_1.md', '对话文件名')
ok(conv.content.includes('## 我\n\n你好'), '用户消息标成「我」')
ok(conv.content.includes('## Oryx\n\n收到'), '助手消息标成「Oryx」')
ok(conv.content.includes('\n\n---\n\n'), '两条消息之间有分隔线')
eq(buildConversationExport('t', []).content, '# 空对话', '空对话也给内容')

const plan = buildPlanExport('t', [
  { tool: 'write_file', args: 'x'.repeat(300), state: 'done' },
  { tool: null, args: null, state: null },
])
eq(plan.filename, 'steps-t.md', '执行计划文件名')
ok(plan.content.includes('x'.repeat(200)), '参数要带进计划')
ok(!plan.content.includes('x'.repeat(201)), '参数截断到 200 字')
ok(plan.content.includes('[done] write_file'), '状态与工具名保留')
eq(buildPlanExport('t', []).content.includes('- 无'), true, '没有步骤给占位')

eq(EXPORT_KINDS.length, 3, '三种导出')
ok(EXPORT_KINDS.every((k) => k.label && k.hint), '每种导出都有文案与说明')
eq(buildExport('audit', { taskId: 't', audit: auditRows }).filename, 'audit-t.json', '分发到审计')
eq(buildExport('conversation', { taskId: 't', messages: [] }).filename, 'conversation-t.md', '分发到对话')
eq(buildExport('plan', { taskId: 't', steps: [] }).filename, 'steps-t.md', '分发到执行计划')
eq(buildExport('audit', { taskId: '' }), null, '没有 taskId 不生成文件')
eq(buildExport('unknown', { taskId: 't' }), null, '未知类型不生成文件')

console.log(`[check:system] ${checks} 项断言全部通过`)
