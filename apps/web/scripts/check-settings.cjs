/**
 * 设置剩余三项纯函数断言 —— `npm run check:settings`
 *
 * 重点是别做成假功能：
 *   · 外观要产出可执行的 DOM 计划（主题 + 背景图 + 填充），而不只是存盘；
 *   · 特性开关的自锁与「占位开关」必须禁用并给人话原因；
 *   · 记忆的秒级时间戳、null 守卫与去重提示。
 */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const esbuild = require('esbuild')

const src = fs.readFileSync(
  path.join(__dirname, '..', 'src', 'features', 'settings', 'settingsOps.ts'),
  'utf8',
)
const js = esbuild.transformSync(src, { loader: 'ts', format: 'cjs' }).code
const mod = { exports: {} }
new Function('module', 'exports', js)(mod, mod.exports)
const {
  THEMES, BG_FITS, BG_FIT_CSS, BACKGROUND_MAX_BYTES,
  isTheme, isFit, backgroundUrl, appearancePlan, validateBackgroundFile, appearanceNotice,
  featuresSelfEnabled, canToggleFeature, confirmToggleText, toggleBody,
  featureToggleNotice, featureGroups, featureSummary,
  MEMORY_TEXT_MAX, MEMORY_TAGS_MAX, validateMemoryText, validateMemoryTags,
  memoryBody, memoryTime, memorySummary, dedupeNotice,
} = mod.exports

let checks = 0
const ok = (cond, msg) => { assert.ok(cond, msg); checks += 1 }
const eq = (actual, expected, msg) => { assert.equal(actual, expected, msg); checks += 1 }
const deep = (actual, expected, msg) => { assert.deepEqual(actual, expected, msg); checks += 1 }

// ── 外观 ───────────────────────────────────────────────────────────
deep(THEMES.map((t) => t.value), ['light', 'dark'], '两档主题')
deep(BG_FITS.map((f) => f.value), ['cover', 'contain', 'tile', 'center'], '四种填充方式与后端一致')
eq(Object.keys(BG_FIT_CSS).length, 4, '每种填充都有 CSS 规则')
eq(BG_FIT_CSS.tile.repeat, 'repeat', '平铺要重复')
eq(BG_FIT_CSS.contain.size, 'contain', '完整显示用 contain')
eq(BACKGROUND_MAX_BYTES, 12 * 1024 * 1024, '12MB 上限与后端一致')

eq(isTheme('dark'), true, 'dark 合法')
eq(isTheme('neon'), false, '未知主题非法')
eq(isFit('cover'), true, 'cover 合法')
eq(isFit('stretch'), false, '未知填充非法')

eq(backgroundUrl(123), '/api/appearance/background.png?v=123', '背景地址带版本参数')
ok(!backgroundUrl(123).includes('?t='), '不能占用令牌参数 t')
eq(backgroundUrl('a b'), '/api/appearance/background.png?v=a%20b', '版本参数要编码')

const base = appearancePlan(null)
eq(base.theme, 'light', '没有外观数据时退回浅色')
eq(base.hasBg, false, '没有外观数据时没有背景')
eq(base.bgImage, 'none', '没有背景图时 CSS 值为 none')
eq(base.size, 'cover', '填充默认 cover')

const dark = appearancePlan({
  theme: 'dark', background: '', background_exists: true, background_fit: 'tile',
}, 42)
eq(dark.theme, 'dark', '深色主题要真的写进 DOM 计划')
eq(dark.hasBg, true, '有背景图')
eq(dark.size, 'auto', 'tile 的尺寸')
eq(dark.pos, '0 0', 'tile 的位置')
eq(dark.repeat, 'repeat', 'tile 重复')
ok(dark.bgImage.includes('background.png?v=42'), '背景图 URL 带破缓存参数')
ok(dark.bgImage.startsWith('url('), '背景图是合法 CSS url()')
ok(!dark.bgImage.includes('?t='), '背景图 URL 不带令牌参数')

const badFit = appearancePlan({ theme: 'light', background: '', background_exists: false, background_fit: 'stretch' })
eq(badFit.size, 'cover', '未知填充退回 cover，不显示 undefined')

eq(validateBackgroundFile(null), '请先选择一张图片', '没选文件')
eq(validateBackgroundFile({ name: 'a.PNG', size: 100 }), null, '大写扩展名也认')
eq(validateBackgroundFile({ name: 'a.svg', size: 100 }), '只支持 png / jpeg / webp / gif / bmp 格式', '拒绝不支持的格式')
eq(validateBackgroundFile({ name: 'a.png', size: 0 }), '图片是空的', '空文件')
ok(validateBackgroundFile({ name: 'a.png', size: BACKGROUND_MAX_BYTES + 1 }).includes('上限 12MB'), '超限提示')
eq(validateBackgroundFile({ name: 'a.png', size: BACKGROUND_MAX_BYTES }), null, '刚好 12MB 放行')

ok(appearanceNotice('theme').includes('页面已切换'), '主题提示要说明页面真的切了')
ok(appearanceNotice('cleared').includes('清除'), '清除背景提示')

// ── 特性开关 ───────────────────────────────────────────────────────
const card = (key, value, implemented = true) => ({
  key, value, implemented, enabled: value, description: `${key} 说明`,
})

eq(featuresSelfEnabled(undefined), false, '没有开关清单时视为自锁关闭')
eq(featuresSelfEnabled([card('EnableFeatureFlags', false)]), false, '自锁开关关掉后不允许调整')
eq(featuresSelfEnabled([card('EnableFeatureFlags', true)]), true, '自锁开关开启时允许调整')
eq(featuresSelfEnabled([card('EnableOffice', true)]), false, '清单里没有自锁开关时视为关闭')

const locked = canToggleFeature(card('EnableOffice', true), false)
eq(locked.ok, false, '自锁时不能切换')
ok(locked.reason.includes('features.yaml'), '自锁原因指路到配置文件')

const placeholder = canToggleFeature(card('EnableA2A', true, false), true)
eq(placeholder.ok, false, '占位开关不能切换')
ok(placeholder.reason.includes('还没有这个能力'), '占位原因如实说明')

const normal = canToggleFeature(card('EnableOffice', true), true)
eq(normal.ok, true, '正常开关可切换')
eq(normal.reason, '', '正常开关没有禁用原因')
eq(canToggleFeature(null, true).ok, false, '空数据不能切换')

ok(confirmToggleText(card('EnableFeatureFlags', true), false).includes('features.yaml'), '关闭自锁开关前要二次确认')
eq(confirmToggleText(card('EnableFeatureFlags', true), true), null, '开启自锁开关不需要确认')
eq(confirmToggleText(card('EnableOffice', true), false), null, '普通开关不需要二次确认')

deep(toggleBody('  EnableOffice  ', false), { key: 'EnableOffice', value: false }, '请求体去空白')
ok(featureToggleNotice('EnableOffice', false).includes('关闭'), '关闭提示')
ok(featureToggleNotice('EnableOffice', true).includes('开启'), '开启提示')

const groups = featureGroups([card('A', true), card('B', false, false), card('C', false)])
eq(groups.length, 2, '两组')
eq(groups[0].items.length, 2, '已实现组两项')
eq(groups[1].items.length, 1, '占位组一项')
ok(groups[1].hint.includes('没有这个能力'), '占位组说明如实')
deep(featureGroups(undefined).map((g) => g.items.length), [0, 0], '空清单不炸')

const summary = featureSummary([card('A', true), card('B', false), card('C', true, false)])
deep(summary, { on: 1, off: 1, placeholders: 1 }, '开关汇总')
deep(featureSummary(undefined), { on: 0, off: 0, placeholders: 0 }, '空清单汇总')

// ── 长期记忆 ───────────────────────────────────────────────────────
eq(MEMORY_TEXT_MAX, 2000, '内容上限')
eq(MEMORY_TAGS_MAX, 200, '标签上限')
eq(validateMemoryText(''), '请输入要记住的内容', '空内容')
eq(validateMemoryText('   '), '请输入要记住的内容', '纯空白内容')
eq(validateMemoryText('a'.repeat(MEMORY_TEXT_MAX)), null, '刚好到上限放行')
ok(validateMemoryText('a'.repeat(MEMORY_TEXT_MAX + 1)).includes('2000'), '超限提示')
eq(validateMemoryTags('a'.repeat(MEMORY_TAGS_MAX)), null, '标签上限内放行')
ok(validateMemoryTags('a'.repeat(MEMORY_TAGS_MAX + 1)).includes('200'), '标签超限提示')
deep(memoryBody('  记住这个  ', '  标签  '), { text: '记住这个', tags: '标签' }, '请求体去空白')

eq(memoryTime(0), '—', '0 时间戳占位')
eq(memoryTime('bad'), '—', '坏时间戳占位')
ok(!memoryTime(1700000000).includes('1970'), '秒级时间戳乘 1000')
deep(memorySummary(undefined), { count: 0, text: '还没有记住任何内容' }, '空记忆提示')
deep(memorySummary([{ id: '1' }]), { count: 1, text: '共 1 条记忆' }, '记忆计数')
ok(dedupeNotice(true).includes('已经记住'), '去重提示')
ok(dedupeNotice(false).includes('已记住'), '新增提示')

console.log(`[check:settings] ${checks} 项断言全部通过`)
