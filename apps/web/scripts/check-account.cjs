/**
 * 账号页纯函数断言 —— `npm run check:account`（模块 12）。
 *
 * 重点：表单校验顺序、请求体形状、成功提示不夸大、秒级时间戳别再踩坑。
 */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const esbuild = require('esbuild')

const src = fs.readFileSync(
  path.join(__dirname, '..', 'src', 'features', 'settings', 'accountOps.ts'),
  'utf8',
)
const js = esbuild.transformSync(src, { loader: 'ts', format: 'cjs' }).code
const mod = { exports: {} }
new Function('module', 'exports', js)(mod, mod.exports)
const {
  PASSWORD_MIN, PASSWORD_MAX,
  validatePasswordChange, passwordChangeBody, canSubmitPasswordChange,
  changeNotice, passwordHint, accountCreatedAt, LOGOUT_CONFIRM, logoutBusyLabel,
} = mod.exports

let checks = 0
const ok = (cond, msg) => { assert.ok(cond, msg); checks += 1 }
const eq = (actual, expected, msg) => { assert.equal(actual, expected, msg); checks += 1 }
const deep = (actual, expected, msg) => { assert.deepEqual(actual, expected, msg); checks += 1 }

const OLD = 'long-password-1'
const NEW = 'another-long-password-2'
const draft = (over = {}) => ({ oldPassword: OLD, newPassword: NEW, confirm: NEW, ...over })

// ── 常量与后端一致 ──────────────────────────────────────────────────
eq(PASSWORD_MIN, 10, '最短 10 位与后端 Credentials 一致')
eq(PASSWORD_MAX, 256, '最长 256 位与后端一致')

// ── 表单校验 ────────────────────────────────────────────────────────
eq(validatePasswordChange(draft()), null, '合法草稿放行')
eq(validatePasswordChange(draft({ oldPassword: '' })), '请输入原密码', '原密码为空')
eq(validatePasswordChange(draft({ newPassword: '' })), '请输入新密码', '新密码为空')
eq(validatePasswordChange(draft({ newPassword: 'short', confirm: 'short' })),
  '新密码至少需要 10 位', '太短')
eq(validatePasswordChange(draft({ newPassword: 'x'.repeat(257), confirm: 'x'.repeat(257) })),
  '新密码不能超过 256 位', '超长')
eq(validatePasswordChange(draft({ newPassword: OLD, confirm: OLD })),
  '新密码不能与原密码相同', '新旧相同')
eq(validatePasswordChange(draft({ confirm: 'different-long-password' })),
  '两次输入的新密码不一致', '两次不一致')
// 顺序：短密码 + 两次不一致，应该先报「太短」而不是「不一致」
eq(validatePasswordChange(draft({ newPassword: 'short', confirm: 'mismatch' })),
  '新密码至少需要 10 位', '校验顺序：长度优先于一致性')
eq(validatePasswordChange(draft({ oldPassword: '', newPassword: '', confirm: '' })),
  '请输入原密码', '全空时先报原密码')
eq(validatePasswordChange(draft({ newPassword: 'x'.repeat(10), confirm: 'x'.repeat(10) })),
  null, '正好 10 位合法（边界）')
eq(validatePasswordChange(draft({ newPassword: 'x'.repeat(256), confirm: 'x'.repeat(256) })),
  null, '正好 256 位合法（边界）')

// ── 请求体形状 ──────────────────────────────────────────────────────
deep(passwordChangeBody(draft()), { old_password: OLD, new_password: NEW }, '只带两个字段')
ok(!('confirm' in passwordChangeBody(draft())), '确认框不外发')
ok(!('username' in passwordChangeBody(draft())), '账号名不外发')
deep(Object.keys(passwordChangeBody(draft())).sort(), ['new_password', 'old_password'], '键名与后端模型一致')

// ── 防重复提交 ──────────────────────────────────────────────────────
eq(canSubmitPasswordChange(draft(), false), true, '闲时且合法 → 可提交')
eq(canSubmitPasswordChange(draft(), true), false, '提交中 → 禁用')
eq(canSubmitPasswordChange(draft({ confirm: 'nope' }), false), false, '有校验错误 → 禁用')
eq(canSubmitPasswordChange(draft({ oldPassword: '' }), false), false, '空原密码 → 禁用')

// ── 成功提示 ────────────────────────────────────────────────────────
ok(changeNotice(0).includes('当前设备保持登录'), '0 条时不说「全部下线」')
ok(changeNotice(1).includes('其它 1 个'), '1 条如实说')
ok(changeNotice(3).includes('其它 3 个'), '多条如实说')
ok(changeNotice(-2).includes('当前设备保持登录'), '负数按 0 处理')
ok(changeNotice(Number.NaN).includes('当前设备保持登录'), 'NaN 按 0 处理')
ok(changeNotice(2.7).includes('其它 2 个'), '小数向下取整')
ok(!changeNotice(0).includes('undefined'), '提示里不能出现 undefined')

// ── 长度提示 ────────────────────────────────────────────────────────
eq(passwordHint(''), '至少 10 位，最长 256 位', '空值提示规则')
eq(passwordHint('short'), '还差 5 位', '差几位说几位')
eq(passwordHint('x'.repeat(260)), '超出 4 位', '超几位说几位')
eq(passwordHint('x'.repeat(10)), '长度符合要求', '合法长度')

// ── 秒级时间戳 ──────────────────────────────────────────────────────
const someSec = 1732618800
eq(accountCreatedAt(someSec), accountCreatedAt(someSec * 1000), '秒级与毫秒级必须得到同一天')
ok(!accountCreatedAt(someSec).startsWith('1970'), '秒级直接当毫秒会退回 1970（模块 5 回归）')
ok(/^\d{4}-\d{2}-\d{2}$/.test(accountCreatedAt(someSec)), '输出 YYYY-MM-DD')
eq(accountCreatedAt(0), '未记录', '0 视为未记录')
eq(accountCreatedAt(-1), '未记录', '负数视为未记录')
eq(accountCreatedAt(undefined), '未记录', '缺失视为未记录')
eq(accountCreatedAt(Number.NaN), '未记录', 'NaN 视为未记录')

// ── 退出登录 ────────────────────────────────────────────────────────
ok(LOGOUT_CONFIRM.includes('重新输入账号密码'), '二次确认要说清后果')
eq(logoutBusyLabel(true), '正在退出…', '提交中文案')
eq(logoutBusyLabel(false), '退出登录', '闲时文案')

console.log(`[OK] check:account 通过 ${checks} 项断言`)
