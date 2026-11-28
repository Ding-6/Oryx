/**
 * Markdown 渲染器的回归断言（零新依赖）。
 *
 * 为什么要有它：renderMarkdown 的输出直接进 dangerouslySetInnerHTML，
 * 是本项目少数几处「写错就是 XSS」的地方。类型检查管不到转义是否漏了，
 * 所以这里把常见的块级语法、链接协议白名单、HTML 转义逐条钉住。
 *
 * 用 esbuild 把 TS 就地转成 CJS 再执行 —— esbuild 是 vite 的既有依赖，
 * 不需要为此装测试框架，也不需要编译产物落地。
 *
 * 跑法：npm run check:markdown
 */

const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const esbuild = require('esbuild')

const source = fs.readFileSync(
  path.join(__dirname, '..', 'src', 'lib', 'markdown.ts'),
  'utf8',
)
const js = esbuild.transformSync(source, { loader: 'ts', format: 'cjs' }).code
const mod = { exports: {} }
new Function('module', 'exports', js)(mod, mod.exports)
const { renderMarkdown, escapeHtml } = mod.exports

let checks = 0
const check = (fn, label) => {
  fn()
  checks += 1
  void label
}

// ── 基本块级与行内语法 ──────────────────────────────────────────────
const basic = renderMarkdown('# 标题\n\n**粗体** 和 `code` 与 *斜体*')
check(() => assert.match(basic, /<h2 class="md-h">标题<\/h2>/), '标题')
check(() => assert.match(basic, /<strong>粗体<\/strong>/), '粗体')
check(() => assert.match(basic, /<code class="md-code">code<\/code>/), '行内代码')
check(() => assert.match(basic, /<em>斜体<\/em>/), '斜体')

// ── XSS：所有 HTML 必须先转义 ────────────────────────────────────────
const xss = renderMarkdown('<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>')
check(() => assert.ok(!xss.includes('<script') && !xss.includes('<img')), '标签被转义')
check(() => assert.ok(xss.includes('&lt;script&gt;')), '转义后的文本还在')
check(() => assert.equal(escapeHtml('a&b<c>"d\'e'), 'a&amp;b&lt;c&gt;&quot;d&#39;e'), '转义表')

// ── 链接协议白名单 ──────────────────────────────────────────────────
const bad = renderMarkdown('[点我](javascript:alert(1))')
check(() => assert.ok(!bad.includes('<a ') && bad.includes('点我')), 'javascript: 降级为文本')
check(() => assert.ok(!renderMarkdown('[x](//evil.com)').includes('<a ')), '协议相对 URL 拒绝')
check(
  () => assert.ok(
    renderMarkdown('[官网](https://example.com/a?b=1&c=2)')
      .includes('href="https://example.com/a?b=1&amp;c=2"'),
  ),
  'https 链接可用且 & 已转义',
)

// ── 围栏代码块 / 列表 / 引用 / 表格 / 分隔线 ────────────────────────
const code = renderMarkdown('```python\nprint("<x>")\n```')
check(
  () => assert.ok(
    code.includes('<pre class="md-pre" data-lang="python">') &&
    code.includes('&lt;x&gt;') && !code.includes('<x>'),
  ),
  '代码块带语言标签且内容转义',
)
check(
  () => assert.ok(renderMarkdown('```js\nlet a = 1').includes('<pre class="md-pre" data-lang="js">')),
  '未闭合代码块也要收尾',
)

const blocks = renderMarkdown('- 一\n- 二\n\n1. 甲\n2. 乙\n\n> 引用\n\n---')
check(() => assert.ok(blocks.includes('<ul class="md-list">') && blocks.includes('<li>一</li>')), '无序列表')
check(() => assert.ok(blocks.includes('<ol class="md-list">') && blocks.includes('<li>甲</li>')), '有序列表')
// `>` 会被 HTML 转义成 `&gt;` —— 这里守的就是「引用块不能因此消失」这个回归。
check(() => assert.ok(blocks.includes('<blockquote class="md-quote">') && blocks.includes('引用')), '引用块')
check(() => assert.ok(blocks.includes('<hr class="md-hr" />')), '分隔线')

const table = renderMarkdown('| 列A | 列B |\n| --- | --- |\n| 1 | 2 |')
check(
  () => assert.ok(
    table.includes('<table class="md-table">') &&
    table.includes('<th>列A</th>') && table.includes('<td>1</td>'),
  ),
  '表格',
)

// ── 空输入 ──────────────────────────────────────────────────────────
check(() => assert.equal(renderMarkdown(''), ''), '空字符串')
check(() => assert.equal(renderMarkdown(null), ''), 'null')

console.log(`markdown assertions: ALL PASS (${checks} checks)`)
