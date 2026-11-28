/**
 * 偏好纯函数断言 —— `npm run check:preferences`
 *
 * 目录来自后端，前端只做派生。这里把最容易悄悄出错的几条钉住：
 *   · 空串 = 跟随全局，绝不能把 `interaction: ''` 传给 /api/chat
 *     （后端 `req.interaction or config...` 会回退，但传空串会污染快照）
 *   · 目录没加载时不能硬拦用户启动
 *   · 后端给了未知值时降级显示原始值，不能显示空白
 *   · 计算模式不能被单次运行覆盖
 */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const esbuild = require('esbuild')

const src = fs.readFileSync(path.join(__dirname, '..', 'src', 'features', 'settings', 'preferences.ts'), 'utf8')
const js = esbuild.transformSync(src, { loader: 'ts', format: 'cjs' }).code
const mod = { exports: {} }
new Function('module', 'exports', js)(mod, mod.exports)
const {
  FOLLOW_GLOBAL, FALLBACK, labelFor, summary, interactionFlags,
  isElevated, runOverrides, validateOverrides, catalogReady,
} = mod.exports

const catalog = {
  interactions: [
    { name: 'ask', label: '问答', hint: '只读', read_only: true, skip_approval: false, unattended: false, coordination_only: false },
    { name: 'craft', label: '执行', hint: '全部工具', read_only: false, skip_approval: false, unattended: false, coordination_only: false },
    { name: 'bypass', label: '开发者', hint: '跳过确认', read_only: false, skip_approval: true, unattended: false, coordination_only: false },
    { name: 'dontAsk', label: '无人值守', hint: '一律拒绝', read_only: false, skip_approval: false, unattended: true, coordination_only: false },
    { name: 'delegate', label: '委派', hint: '只调度', read_only: true, skip_approval: false, unattended: false, coordination_only: true },
  ],
  styles: [
    { name: 'professional', label: '专业', hint: '先结论后依据' },
    { name: 'efficient', label: '高效', hint: '极简直给' },
  ],
  compute_modes: [
    { name: 'efficiency', label: '效率模式', hint: '云端' },
    { name: 'local', label: '本地模式', hint: '本机' },
  ],
}

let checks = 0
const ok = (cond, msg) => { assert.ok(cond, msg); checks += 1 }

// ── labelFor ────────────────────────────────────────────────
ok(labelFor(catalog.interactions, 'craft') === '执行', '命中目录要返回中文名')
ok(labelFor(catalog.interactions, 'dontAsk') === '无人值守', '大小写混合的值也要命中')
ok(labelFor(catalog.interactions, 'unknown', 'craft') === 'unknown', '未知值降级显示原始值')
ok(labelFor(catalog.interactions, '', '执行') === '执行', '空值用 fallback')
ok(labelFor(undefined, 'craft', 'craft') === 'craft', '目录没加载也不返回空')
ok(labelFor([], 'craft') === 'craft', '空目录不崩')

// ── summary ─────────────────────────────────────────────────
const s0 = summary({}, null)
ok(s0.interaction === FALLBACK.interaction && s0.style === FALLBACK.style && s0.mode === FALLBACK.mode,
  '没有 state 时用后端默认值，且不是空白')
const s1 = summary({ interaction: 'ask', style: 'efficient', mode: 'local' }, catalog)
ok(s1.interaction === '问答' && s1.style === '高效' && s1.mode === '本地模式', '三态都要翻成中文')
ok(summary({ interaction: '', style: '', mode: '' }, catalog).interaction
  === labelFor(catalog.interactions, FALLBACK.interaction),
  '空串按未设置处理（回落到默认值的中文名）')
ok(summary({ interaction: 'weird' }, catalog).interaction === 'weird', '未知值原样显示而不是误报默认')

// ── 风险徽标 ─────────────────────────────────────────────────
const byName = (n) => catalog.interactions.find((x) => x.name === n)
ok(interactionFlags(byName('ask')).join(',') === '只读', '只读模式标只读')
ok(interactionFlags(byName('delegate')).includes('只调度'), '委派模式标只调度')
ok(interactionFlags(byName('bypass')).includes('跳过确认'), '开发者模式标跳过确认')
ok(interactionFlags(byName('craft')).length === 0, '执行模式不加徽标')
ok(isElevated(byName('bypass')) && isElevated(byName('dontAsk')), '跳过确认/无人值守要强提示')
ok(!isElevated(byName('craft')) && !isElevated(byName('ask')), '普通模式不强提示')

// ── runOverrides：单次运行请求体 ───────────────────────────────
ok(Object.keys(runOverrides('', '')).length === 0, '两个都跟随全局时一个字段都不传')
ok(JSON.stringify(runOverrides('ask', '')) === '{"interaction":"ask"}', '只传显式选中的字段')
ok(JSON.stringify(runOverrides('', 'efficient')) === '{"style":"efficient"}', '风格单独覆盖')
ok(JSON.stringify(runOverrides('  ask  ', '  efficient  ')) === '{"interaction":"ask","style":"efficient"}', '值要 trim')
const all = runOverrides('ask', 'efficient')
ok(!('mode' in all), '计算模式绝不能进单次运行请求体')
ok(!('agent_id' in all) && !('goal' in all), '覆盖函数只管两个字段')

// ── validateOverrides ────────────────────────────────────────
ok(validateOverrides('', '', null) === null, '目录没加载时不硬拦启动')
ok(validateOverrides('ask', 'efficient', catalog) === null, '合法组合放行')
ok(validateOverrides('ask', 'efficient', null) === null, '目录缺失时合法值也放行')
ok(String(validateOverrides('nope', '', catalog)).includes('未知的交互模式'), '未知交互模式要拦下')
ok(String(validateOverrides('', 'nope', catalog)).includes('未知的回答风格'), '未知风格要拦下')
ok(validateOverrides('  ask  ', '', catalog) === null, '校验要对 trim 后的值做')
ok(validateOverrides('bypass', '', catalog) === null, '高权限模式本身是合法选项')

// ── catalogReady ─────────────────────────────────────────────
ok(catalogReady(catalog) === true, '完整目录可用')
ok(!catalogReady(null), 'null 不可用')
ok(!catalogReady({ interactions: [], styles: [], compute_modes: [] }), '空目录不可用')
ok(!catalogReady({ ...catalog, compute_modes: [] }), '少一份目录就不可用')

console.log(`preference assertions: ALL PASS (${checks} checks)`)
