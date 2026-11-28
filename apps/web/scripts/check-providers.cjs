/**
 * 模型服务纯函数断言 —— `npm run check:providers`
 *
 * 钉住三条会直接影响用户数据安全的语义：
 *   · 新增同名不能悄悄覆盖（要提示去编辑）
 *   · 编辑时密钥留空 = 不动原密钥（绝不把空串发出去）
 *   · 激活 ≠ 立刻生效：计算模式决定实际走本地还是云端，提示要如实
 */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const esbuild = require('esbuild')

const src = fs.readFileSync(
  path.join(__dirname, '..', 'src', 'features', 'settings', 'providers.ts'),
  'utf8',
)
const js = esbuild.transformSync(src, { loader: 'ts', format: 'cjs' }).code
const mod = { exports: {} }
new Function('module', 'exports', js)(mod, mod.exports)
const {
  emptyForm,
  formFrom,
  validateForm,
  createBody,
  editBody,
  hasChanges,
  keyStatus,
  capabilityBadges,
  groupProviders,
  providerBadges,
  activationNotice,
  formatBytes,
} = mod.exports

let checks = 0
const ok = (cond, msg) => { assert.ok(cond, msg); checks += 1 }

const baseForm = {
  name: 'deepseek',
  type: 'openai',
  base_url: 'https://api.deepseek.com/v1',
  model: 'deepseek-chat',
  api_key: 'sk-new',
}

const cloud = {
  name: 'deepseek', type: 'openai', base_url: 'https://api.deepseek.com/v1',
  model: 'deepseek-chat', api_key: 'sk-ab…1234', has_key: true, key_storage: 'plaintext',
  capabilities: { context_window: 128000, supports_tools: true, supports_vision: false, cost_hint: '按 token' },
  is_local: false,
}
const local = {
  name: 'qwen3-8b', type: 'openai', base_url: 'http://127.0.0.1:11434/v1',
  model: 'qwen3:8b', api_key: '', has_key: false, key_storage: 'none',
  capabilities: { context_window: 32768, supports_tools: true, supports_vision: false },
  is_local: true,
}

// ── 表单 ────────────────────────────────────────────────────
ok(emptyForm().name === '' && emptyForm().type === 'openai', '空表单默认 OpenAI 兼容')
const from = formFrom(cloud)
ok(from.api_key === '', '编辑时绝不回填密钥')
ok(from.name === 'deepseek' && from.model === 'deepseek-chat', '其余字段要回填')

// ── validateForm ────────────────────────────────────────────
ok(validateForm(baseForm, [], undefined) === null, '合法新增放行')
ok(String(validateForm({ ...baseForm, name: '' }, [])).includes('起个名字'), '名字不能为空')
ok(String(validateForm({ ...baseForm, name: 'a/b' }, [])).includes('斜杠'), '名字不能带斜杠')
ok(String(validateForm(baseForm, ['deepseek'])).includes('同名'), '新增同名要拦下')
ok(validateForm({ ...baseForm, name: 'deepseek' }, ['deepseek'], 'deepseek') === null,
  '编辑自己时同名不算冲突')
ok(String(validateForm({ ...baseForm, name: 'other' }, [], 'deepseek')).includes('不能改名字'),
  '编辑时不能改名')
ok(String(validateForm({ ...baseForm, base_url: '' }, [])).includes('接口地址不能为空'), '地址必填')
ok(String(validateForm({ ...baseForm, base_url: 'ftp://x' }, [])).includes('http://'), '地址协议要拦')
ok(String(validateForm({ ...baseForm, model: '   ' }, [])).includes('模型名不能为空'), '模型名必填')
ok(validateForm({ ...baseForm, name: '  deepseek  ' }, ['deepseek']) !== null,
  '校验前先 trim，空格不能绕过同名检查')

// ── createBody ──────────────────────────────────────────────
const created = createBody({ ...baseForm, name: '  deepseek  ', base_url: ' https://a/v1 ', model: ' m ' })
ok(created.name === 'deepseek' && created.base_url === 'https://a/v1' && created.model === 'm', '新增请求体要 trim')
ok(created.api_key === 'sk-new', '新增时密钥照传')

// ── editBody：只带改了的字段 ────────────────────────────────
ok(Object.keys(editBody(cloud, { ...from, api_key: '' })).length === 0, '什么都没改时 body 为空')
const changedModel = editBody(cloud, { ...from, model: 'deepseek-reasoner' })
ok(changedModel.model === 'deepseek-reasoner', '改模型要带上')
ok(!('api_key' in changedModel), '密钥留空时整个字段都不传')
const changedKey = editBody(cloud, { ...from, api_key: 'sk-new' })
ok(changedKey.api_key === 'sk-new', '填了新密钥才传')
ok(!('base_url' in changedKey), '没改的地址不重复传')
ok(hasChanges(cloud, { ...from, api_key: '' }) === false, '无改动要识别出来')
ok(hasChanges(cloud, { ...from, base_url: 'https://x/v1' }) === true, '有改动要识别出来')

// ── 密钥状态 ────────────────────────────────────────────────
ok(keyStatus(cloud).ok === true && keyStatus(cloud).label.includes('明文'), '明文密钥要如实说明')
ok(keyStatus({ ...cloud, key_storage: 'system' }).label.includes('系统凭据库'), '凭据库要说明')
ok(keyStatus(local).ok === false && keyStatus(local).label.includes('未设置'), '没有密钥要标出来')

// ── 能力 / 分组 / 徽标 ──────────────────────────────────────
const caps = capabilityBadges(cloud.capabilities)
ok(caps.includes('上下文 128K'), '上下文按 K 展示')
ok(caps.includes('工具调用'), '工具调用要标')
ok(!caps.includes('视觉'), '不支持视觉就不标')
ok(capabilityBadges(undefined).length === 0, '没有能力声明不崩')

const groups = groupProviders([cloud, local])
ok(groups.cloud.length === 1 && groups.cloud[0].name === 'deepseek', '云端归云端')
ok(groups.local.length === 1 && groups.local[0].name === 'qwen3-8b', '本地归本地')

const resp = { active: 'deepseek', local: 'qwen3-8b', cloud: 'deepseek', items: [cloud, local] }
ok(providerBadges(cloud, resp).includes('当前生效'), '生效的那条要标')
ok(providerBadges(local, resp).includes('本地默认'), '本地槽位要标')
ok(providerBadges(local, { ...resp, active: 'qwen3-8b' }).includes('当前生效'), '本地生效时标当前生效')
ok(providerBadges({ ...cloud, name: 'other' }, resp).length === 0, '既非生效也非默认时不乱标')

// ── 激活提示 ────────────────────────────────────────────────
ok(activationNotice(local, 'efficiency').includes('切到本地模式'), '效率模式激活本地服务要说清还没生效')
ok(activationNotice(cloud, 'local').includes('切到效率模式'), '本地模式激活云端服务要说清')
ok(activationNotice(cloud, 'efficiency').includes('已切到'), '匹配时直接说已切到')
ok(activationNotice(local, 'local').includes('已切到'), '本地模式激活本地服务直接生效')

// ── 体积 ────────────────────────────────────────────────────
ok(formatBytes(0) === '—', '0 显示破折号')
ok(formatBytes(1024 ** 3 * 4.2) === '4.2 GB', 'GB 保留一位')
ok(formatBytes(512 * 1024 ** 2) === '512 MB', 'MB 取整')

console.log(`provider assertions: ALL PASS (${checks} checks)`)
