/**
 * 步骤详情纯函数断言 —— 可一键复跑：npm run check:steps
 *
 * stepFormat.ts 负责把后端「JSON 字符串」形态的 args / result 还原成
 * 可读文本，并合并 SSE 增强字段。这里钉住最容易出错的几处：
 *   · 字符串被 json.dumps 过一次（`"文件已写入"` → 文件已写入）
 *   · 坏 JSON / 纯文本不能抛异常
 *   · 落库字段优先，SSE 只补空（mutated=0 不能被改成 true）
 *   · 失败分类、耗时、风险文案与后端一致
 */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const esbuild = require('esbuild')

const src = fs.readFileSync(
  path.join(__dirname, '..', 'src', 'features', 'sessions', 'stepFormat.ts'),
  'utf8',
)
const js = esbuild.transformSync(src, { loader: 'ts', format: 'cjs' }).code
const mod = { exports: {} }
new Function('module', 'exports', js)(mod, mod.exports)
const {
  prettyValue, argsSummary, truncate, formatMs, formatDuration,
  buildStepView, stepExtraFromEvent, RISK_CN, ERROR_CLASS_HINT, STEP_STATE_CN,
} = mod.exports

let checks = 0
const check = (fn) => { fn(); checks += 1 }

// ── prettyValue ────────────────────────────────────────────────
check(() => assert.equal(prettyValue(null), ''))
check(() => assert.equal(prettyValue(undefined), ''))
check(() => assert.equal(prettyValue(''), ''))
check(() => assert.equal(prettyValue('"已写入 a.txt"'), '已写入 a.txt'))
check(() => assert.equal(
  prettyValue('{"path":"a.txt","content":"hi"}'),
  '{\n  "path": "a.txt",\n  "content": "hi"\n}',
))
check(() => assert.equal(prettyValue('这是一段没有转义的纯文本'), '这是一段没有转义的纯文本'))
check(() => assert.equal(prettyValue('{坏掉的 JSON'), '{坏掉的 JSON'))
check(() => assert.equal(prettyValue({ a: 1 }), '{\n  "a": 1\n}'))
check(() => assert.equal(prettyValue([1, 2]), '[\n  1,\n  2\n]'))
check(() => assert.equal(prettyValue('123'), '123'))
check(() => assert.equal(prettyValue('null'), ''))

// ── argsSummary ────────────────────────────────────────────────
check(() => assert.equal(argsSummary(null), '无参数'))
check(() => assert.equal(argsSummary(''), '无参数'))
check(() => assert.match(argsSummary('{"path":"a.txt","content":"hi"}'), /path=a\.txt/))
check(() => assert.match(argsSummary('{"a":1,"b":2,"c":3,"d":4}'), /还有 1 项/))
check(() => assert.equal(argsSummary('纯文本参数'), '纯文本参数'))
check(() => assert.ok(truncate('abcdef', 3) === 'abc…'))
check(() => assert.equal(truncate('ab', 3), 'ab'))

// ── 耗时 ───────────────────────────────────────────────────────
check(() => assert.equal(formatMs(320), '320 ms'))
check(() => assert.equal(formatMs(1500), '1.5 s'))
check(() => assert.equal(formatMs(90000), '1.5 min'))
check(() => assert.equal(formatMs(-1), ''))
check(() => assert.equal(formatDuration(1000, 1001.5), '1.5 s'))
check(() => assert.equal(formatDuration(1000, null, 420), '420 ms'))
check(() => assert.equal(formatDuration(null, null), null))
check(() => assert.equal(formatDuration(2000, 1000), null, '结束早于开始不能算出负耗时'))

// ── buildStepView ──────────────────────────────────────────────
const base = {
  id: 's1', task_id: 't1', seq: 1, agent: null, tool: 'fs_write',
  args: '{"path":"a.txt","content":"hi"}',
  state: 'done', risk: 1, result: '"已写入 a.txt"', error: null,
  started_at: 1000, ended_at: 1001.5,
}
check(() => {
  const v = buildStepView(base)
  assert.equal(v.stateLabel, '完成')
  assert.equal(v.riskLabel, 'L1 可逆写入')
  assert.equal(v.resultText, '已写入 a.txt')
  assert.match(v.argsText, /"path": "a\.txt"/)
  assert.equal(v.duration, '1.5 s')
  assert.equal(v.hasDetails, true)
})
check(() => {
  // SSE 只补空：落库已有的 error_class / mutated 不能被覆盖
  const v = buildStepView(
    { ...base, state: 'failed', result: null, error: '权限不足',
      error_class: 'E2', artifact_path: 'D:/x', mutated: 0 },
    { error_class: 'E5', artifact_path: 'D:/y', mutated: true, elapsed_ms: 99999 },
  )
  assert.equal(v.errorClass, 'E2')
  assert.equal(v.errorHint, ERROR_CLASS_HINT.E2)
  assert.equal(v.artifactPath, 'D:/x')
  assert.equal(v.mutated, false, 'mutated=0 是有效值，不能被 SSE 覆盖')
  assert.equal(v.duration, '1.5 s', '有落库时间时不采用事件耗时')
  assert.equal(v.errorText, '权限不足')
  assert.equal(v.hasDetails, true)
})
check(() => {
  // 老库没有新列：靠 SSE 补齐
  const v = buildStepView(
    { ...base, state: 'failed', result: null, error: '依赖缺失', started_at: null, ended_at: null },
    { error_class: 'E5', artifact_path: 'D:/out.xlsx', mutated: false, elapsed_ms: 2500 },
  )
  assert.equal(v.errorClass, 'E5')
  assert.equal(v.errorHint, ERROR_CLASS_HINT.E5)
  assert.equal(v.artifactPath, 'D:/out.xlsx')
  assert.equal(v.mutated, false)
  assert.equal(v.duration, '2.5 s')
})
check(() => {
  const v = buildStepView(
    { ...base, state: 'denied', result: null, error: '命中禁止规则', risk: 3 },
    { reason: '安全策略禁止', verdict: 'deny', risk_label: 'L3 禁止操作' },
  )
  assert.equal(v.stateLabel, '已拒绝')
  assert.equal(v.riskLabel, 'L3 禁止操作')
  assert.equal(v.reason, '安全策略禁止')
  assert.equal(v.verdict, 'deny')
})
check(() => {
  const v = buildStepView(
    { ...base, state: 'weird', args: null, result: null, error: null, risk: null,
      started_at: null, ended_at: null, tool: null, mutated: null },
  )
  assert.equal(v.stateLabel, 'weird', '未知状态原样显示，不吞掉')
  assert.equal(v.riskLabel, null)
  assert.equal(v.tool, '（未记录工具）')
  assert.equal(v.argsSummary, '无参数')
  assert.equal(v.hasDetails, false, '没有任何详情时不展开区块')
})
check(() => {
  // 文案与后端 policy.Risk.label / loop.py 状态取值对齐
  assert.equal(RISK_CN[0], 'L0 只读')
  assert.equal(RISK_CN[3], 'L3 禁止操作')
  assert.equal(STEP_STATE_CN.running, '执行中')
  assert.equal(STEP_STATE_CN.denied, '已拒绝')
})

// ── SSE 增强字段抽取 ──────────────────────────────────────────
check(() => {
  const a = stepExtraFromEvent({ type: 'step.created', step_id: 's1', risk_label: 'L1 可逆写入' })
  assert.deepEqual(a, { risk_label: 'L1 可逆写入' })
  assert.ok(!('error_class' in a), '未带的字段不能出现在对象里，否则会覆盖旧值')
})
check(() => assert.deepEqual(
  stepExtraFromEvent({ step_id: 's1', error_class: 'E2', mutated: false, elapsed_ms: 12 }),
  { error_class: 'E2', mutated: false, elapsed_ms: 12 },
))
check(() => assert.deepEqual(stepExtraFromEvent({ type: 'step.running', step_id: 's1' }), {}))
check(() => assert.deepEqual(
  stepExtraFromEvent({ error_class: 123, mutated: 'yes', verdict: null }),
  {},
  '类型不对的字段一律忽略',
))
check(() => {
  // 按 step_id 累积：created 带来的 risk_label 不能被 done 事件抹掉
  const created = stepExtraFromEvent({ step_id: 's1', risk_label: 'L1 可逆写入' })
  const done = stepExtraFromEvent({ step_id: 's1', mutated: false, artifact_path: 'D:/x' })
  const merged = { ...created, ...done }
  assert.equal(merged.risk_label, 'L1 可逆写入')
  assert.equal(merged.mutated, false)
  assert.equal(merged.artifact_path, 'D:/x')
})

console.log(`step format assertions: ALL PASS (${checks} checks)`)
