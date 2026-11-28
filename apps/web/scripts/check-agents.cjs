/**
 * Agent 候选目录纯函数断言 —— `npm run check:agents`
 *
 * 最重要的一条：**已保存但不在目录里的值不能丢**。内核没起来或工具被卸掉时，
 * 旧定义里的名字查无此人，界面要留着标红，而不是一打开就悄悄清空。
 * 另外钉住「内核降级时跳过那一项校验」「模型服务永远校验」「只读原因三档」。
 */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const esbuild = require('esbuild')

const src = fs.readFileSync(
  path.join(__dirname, '..', 'src', 'features', 'agents', 'agentCatalog.ts'),
  'utf8',
)
const js = esbuild.transformSync(src, { loader: 'ts', format: 'cjs' }).code
const mod = { exports: {} }
new Function('module', 'exports', js)(mod, mod.exports)
const {
  EMPTY_CATALOG,
  catalogNames,
  groupTools,
  toggleName,
  unknownSelections,
  withUnknownValues,
  modelOptions,
  permissionOptions,
  kindOptions,
  toolOptions,
  skillOptions,
  catalogHint,
  readonlyReason,
  validateAgentDraft,
} = mod.exports

let checks = 0
const ok = (cond, msg) => { assert.ok(cond, msg); checks += 1 }
const eq = (actual, expected, msg) => { assert.equal(actual, expected, msg); checks += 1 }
const deep = (actual, expected, msg) => { assert.deepEqual(actual, expected, msg); checks += 1 }

const catalog = {
  tools: [
    { name: 'fs_read', group: 'fs', description: '读文件', risk_label: '低' },
    { name: 'fs_write', group: 'fs', description: '写文件', risk_label: '中' },
    { name: 'web_search', group: 'web', description: '联网搜索', risk_label: '低' },
  ],
  skills: [
    { name: 'writing', description: '写作' },
    { name: 'review', description: '审校' },
  ],
  models: [{ name: 'main', model: 'gpt-x', type: 'openai' }],
  permission_modes: ['inherit', 'ask', 'craft'],
  kinds: ['agent', 'butler'],
  available: { tools: true, skills: true, models: true },
}

const draft = (over = {}) => ({
  id: 'qa-helper', name: '质量助手',
  tools: ['fs_read'], disallowed_tools: ['fs_write'], skills: ['writing'],
  model: 'inherit', permission_mode: 'ask', kind: 'agent', max_turns: 6,
  ...over,
})

// ── 候选名字与分组 ──────────────────────────────────────────────────
deep(catalogNames(catalog, 'tools'), ['fs_read', 'fs_write', 'web_search'], '工具候选名')
deep(catalogNames(catalog, 'skills'), ['writing', 'review'], '技能候选名')
deep(catalogNames(catalog, 'models'), ['main'], '模型服务候选名')
deep(catalogNames(null, 'tools'), [], '目录没加载好不能抛')
deep(catalogNames(EMPTY_CATALOG, 'skills'), [], '空目录返回空数组')

const groups = groupTools(catalog)
eq(groups.length, 2, '工具分两组')
eq(groups[0].group, 'fs', '组名按字典序')
deep(groups[0].tools.map((t) => t.name), ['fs_read', 'fs_write'], '组内按名字排')
deep(groups[1].tools.map((t) => t.name), ['web_search'], 'web 组')
deep(groupTools(null), [], '空目录分组不抛')
eq(groupTools({ ...catalog, tools: [{ name: 'x', group: '', description: '', risk_label: '' }] })[0].group,
  '其他', '没有组名归到「其他」')

// ── 多选与未知值 ────────────────────────────────────────────────────
deep(toggleName(['a', 'b'], 'c'), ['a', 'b', 'c'], '追加新项')
deep(toggleName(['a', 'b'], 'a'), ['b'], '再点一次取消')
deep(toggleName([], 'a'), ['a'], '空列表可加')
const original = ['a']
toggleName(original, 'b')
deep(original, ['a'], 'toggle 不改原数组（不可变）')

deep(unknownSelections(['a', 'zz', 'b', 'zz', 'yy'], ['a', 'b']), ['zz', 'yy'],
  '未知值去重且保序')
deep(unknownSelections(['a'], []), ['a'], 'known 为空时全是未知')
deep(unknownSelections([], ['a']), [], '没有已选项就没有未知')

const options = [{ value: 'a', label: 'A' }, { value: 'b', label: 'B' }]
const merged = withUnknownValues(options, ['a', 'zz'])
eq(merged.length, 3, '补上未知值')
eq(merged[2].value, 'zz', '未知值原名保留')
eq(merged[2].unknown, true, '未知值要打标')
ok(merged[2].label.includes('找不到'), '未知值要有可见提示')
deep(withUnknownValues(options, ['a']), options, '没有未知值时原样返回')

// ── 下拉选项 ────────────────────────────────────────────────────────
eq(modelOptions(catalog)[0].value, 'inherit', '模型首项是跟随默认')
eq(modelOptions(catalog)[1].label, 'main · gpt-x', '模型项带上模型名')
deep(modelOptions(null).map((o) => o.value), ['inherit'], '空目录只有 inherit')
deep(permissionOptions(catalog).map((o) => o.value), ['inherit', 'ask', 'craft'], '权限枚举来自后端')
eq(permissionOptions(catalog)[1].label, '每次询问', '权限中文名')
ok(permissionOptions(null).length > 0, '目录没加载时权限枚举有兜底')
deep(kindOptions(catalog).map((o) => o.value), ['agent', 'butler'], '类型枚举来自后端')
eq(kindOptions(catalog)[1].label, '管家', '类型中文名')
eq(toolOptions(catalog)[0].description, '读文件', '工具带描述')
eq(toolOptions(catalog)[0].group, 'fs', '工具带分组')
eq(skillOptions(catalog)[1].description, '审校', '技能带描述')

// ── 降级提示与只读原因 ──────────────────────────────────────────────
eq(catalogHint(catalog), null, '目录齐全时不提示')
ok(catalogHint(null).includes('没加载'), '目录没加载要有提示')
ok(catalogHint({ ...catalog, available: { tools: false, skills: false, models: true } })
  .includes('工具与技能'), '两项都不可用')
ok(catalogHint({ ...catalog, available: { tools: false, skills: true, models: true } })
  .includes('工具候选'), '只有工具不可用')
ok(catalogHint({ ...catalog, available: { tools: true, skills: false, models: true } })
  .includes('技能候选'), '只有技能不可用')

eq(readonlyReason({ can_edit: true, creator_id: 'u1' }), '你是创建者，可以修改此定义。', '可编辑')
ok(readonlyReason({ can_edit: false, creator_id: null }).includes('内置定义'), '内置定义')
ok(!readonlyReason({ can_edit: false, creator_id: null }).includes('其他创建者'),
  '内置定义不能说成别人创建的')
ok(readonlyReason({ can_edit: false, creator_id: 'u2' }).includes('其他创建者'), '他人创建')
ok(readonlyReason(null).includes('选择'), '未选中给引导')

// ── 保存前校验 ──────────────────────────────────────────────────────
eq(validateAgentDraft(draft(), catalog, false), null, '全合法通过')
eq(validateAgentDraft(draft(), catalog, true), null, '新建全合法通过')

eq(validateAgentDraft(draft({ id: '' }), catalog, true).field, 'id', '新建缺 ID')
eq(validateAgentDraft(draft({ id: 'bad/id' }), catalog, true).field, 'id', 'ID 含斜杠')
eq(validateAgentDraft(draft({ id: '' }), catalog, false), null, '编辑时不看 ID')
eq(validateAgentDraft(draft({ name: '  ' }), catalog, true).field, 'name', '缺名称')
for (const n of [0, 101, 1.5, NaN]) {
  eq(validateAgentDraft(draft({ max_turns: n }), catalog, true).field, 'max_turns',
    `轮数 ${n} 不合法`)
}

const unknownModel = validateAgentDraft(draft({ model: 'ghost' }), catalog, true)
eq(unknownModel.field, 'model', '未知模型服务')
ok(unknownModel.message.includes('设置里添加'), '模型错误指路到设置')
eq(validateAgentDraft(draft({ model: 'main' }), catalog, true), null, '已配置的服务通过')
eq(validateAgentDraft(draft({ model: '' }), catalog, true), null, '空串按 inherit 处理')
eq(validateAgentDraft(draft({ model: 'ghost' }), null, true), null,
  '目录没加载时跳过模型校验，交给后端判')

eq(validateAgentDraft(draft({ permission_mode: 'god' }), catalog, true).field,
  'permission_mode', '未知权限基线')
eq(validateAgentDraft(draft({ kind: 'robot' }), catalog, true).field, 'kind', '未知类型')

const badTools = validateAgentDraft(draft({ tools: ['fs_read', 'ghost'] }), catalog, true)
eq(badTools.field, 'tools', '未知工具')
ok(badTools.message.includes('这些工具不存在'), '工具错误文案与后端一致')
const badDisallowed = validateAgentDraft(
  draft({ disallowed_tools: ['ghost'] }), catalog, true)
eq(badDisallowed.field, 'disallowed_tools', '未知额外禁用工具')
ok(badDisallowed.message.includes('额外禁用工具'), '禁用工具错误文案')
const badSkills = validateAgentDraft(draft({ skills: ['ghost'] }), catalog, true)
eq(badSkills.field, 'skills', '未知技能')
ok(badSkills.message.includes('这些技能不存在'), '技能错误文案与后端一致')

const degraded = { ...catalog, available: { tools: false, skills: false, models: true } }
eq(validateAgentDraft(draft({ tools: ['ghost'], skills: ['ghost'], disallowed_tools: ['ghost'] }),
  degraded, true), null, '内核降级时跳过工具/技能校验，旧值不丢')
eq(validateAgentDraft(draft({ model: 'ghost' }), degraded, true).field, 'model',
  '模型服务来自配置，降级时仍要校验')
eq(validateAgentDraft(draft({ tools: ['ghost'], skills: ['ghost'] }), null, true), null,
  '目录没加载时跳过清单校验')
eq(validateAgentDraft(draft({ name: '' }), null, true).field, 'name',
  '目录没加载时基本校验照做')

console.log(`check-agents: ${checks} 项断言全部通过`)
