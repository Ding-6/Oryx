/**
 * Markdown 渲染 —— 零依赖、**先转义再变换**。
 *
 * 为什么不用 marked / react-markdown
 * ----------------------------------
 * 老前端 `core/web/modules/core.js` 有一条硬规矩：
 *   「markdown-lite：先转义再变换，永远不引入未转义 HTML」。
 * Agent 的回复是模型生成的**不可信文本**，里面可能带 `<script>`、
 * `<img onerror=…>`、`[点我](javascript:…)`。用 dangerouslySetInnerHTML
 * 注入时，只要变换过程有一处漏转义就是 XSS。
 *
 * 这里沿用同一条规矩，并把覆盖面补齐：标题、粗斜体、删除线、行内代码、
 * 围栏代码块、有序/无序列表、引用、表格、分隔线、链接、段落换行。
 * 全部在**已转义文本**上做正则替换，生成的标签是固定字符串，
 * 用户内容只能作为文本或经过白名单的 href 出现。
 *
 * 取舍：不做语法高亮、不做嵌套列表、不支持原始 HTML（这是安全要求，
 * 不是缺陷）。Markdown 的边界情况很多，这里宁可少渲染也不要渲染错。
 */

/** HTML 转义。所有用户内容进变换链之前必须先过这一层。 */
export function escapeHtml(input: unknown): string {
  const table: Record<string, string> = {
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }
  return String(input ?? '').replace(/[&<>"']/g, (c) => table[c])
}

/** 链接协议白名单 —— 只放行 http(s) / mailto / 站内绝对路径 / 锚点。 */
function safeLink(href: string): boolean {
  if (/^\/\//.test(href)) return false // 协议相对 URL，不收
  return /^(?:https?:\/\/|mailto:|\/(?!\/)|#)/i.test(href)
}

/**
 * 行内变换。入参**已经是转义后的文本**。
 * 行内代码先抽成占位符，避免代码里的 `*` 被当成粗体处理。
 */
function inline(source: string): string {
  const codes: string[] = []
  let text = source.replace(/`([^`\n]+)`/g, (_match, code: string) => {
    codes.push(`<code class="md-code">${code}</code>`)
    return `\u0002${codes.length - 1}\u0002`
  })

  text = text.replace(
    /\[([^\]\n]+)\]\(([^)\s]+)\)/g,
    (_whole: string, label: string, href: string) => {
      if (!safeLink(href)) return label
      return `<a class="md-link" href="${href}" target="_blank" rel="noopener noreferrer">${label}</a>`
    },
  )
  text = text.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
  text = text.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>')
  text = text.replace(/~~([^~\n]+)~~/g, '<del>$1</del>')
  text = text.replace(/\u0002(\d+)\u0002/g, (_m, n: string) => codes[Number(n)] ?? '')
  return text
}

/** 表格单元格切分：`| a | b |` → ['a','b']。 */
function splitRow(row: string): string[] {
  return row.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim())
}

/** 表格分隔行：`| --- | --- |` 或 `---|---`。 */
function isTableDivider(row: string): boolean {
  return /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(row) && row.includes('-')
}

/** 判断这一行是不是某个块级结构的开头（段落合并时遇到它就收手）。 */
function isBlockStart(lines: string[], i: number): boolean {
  const line = lines[i]
  if (/^\u0001\d+\u0001$/.test(line.trim())) return true
  if (/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line)) return true
  if (/^\s*#{1,6}\s+/.test(line)) return true
  // `>` 在转义后会变成 `&gt;` —— 引用符是唯一被 HTML 转义改写的块级标记，
  // 两种写法都要认，否则引用块会当成普通段落。
  if (/^\s*(?:>|&gt;)\s?/.test(line)) return true
  if (/^\s*[-*+]\s+/.test(line)) return true
  if (/^\s*\d+[.)]\s+/.test(line)) return true
  return line.includes('|') && i + 1 < lines.length && isTableDivider(lines[i + 1])
}

/**
 * 把 Markdown 文本渲染成安全的 HTML 片段。
 * 结果只能配 `dangerouslySetInnerHTML` 用；外层容器请加 `md-body` class。
 */
export function renderMarkdown(src: unknown): string {
  const raw = String(src ?? '')
  if (!raw) return ''

  // 1) 围栏代码块先整体抽走，用占位符占位（占位符不会被 HTML 转义影响）。
  //    语言名只允许 [\w+-]，之后直接进 data-lang 属性也是安全的。
  const blocks: Array<{ lang: string; code: string }> = []
  let text = raw.replace(
    /```([\w+-]*)\s*\n?([\s\S]*?)(?:```|$)/g,
    (_match, lang: string, code: string) => {
      blocks.push({ lang, code: String(code).replace(/\n$/, '') })
      return `\u0001${blocks.length - 1}\u0001`
    },
  )

  // 2) 转义。此后所有正则都作用在安全文本上。
  text = escapeHtml(text)

  const lines = text.split('\n')
  const out: string[] = []
  let i = 0

  while (i < lines.length) {
    const line = lines[i]

    // 代码块占位
    const placeholder = /^\u0001(\d+)\u0001$/.exec(line.trim())
    if (placeholder) {
      const block = blocks[Number(placeholder[1])]
      if (block) {
        const langAttr = block.lang ? ` data-lang="${block.lang}"` : ''
        out.push(
          `<pre class="md-pre"${langAttr}><code>${escapeHtml(block.code)}</code></pre>`,
        )
      }
      i += 1
      continue
    }

    if (!line.trim()) { i += 1; continue }

    if (/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      out.push('<hr class="md-hr" />')
      i += 1
      continue
    }

    const heading = /^\s*(#{1,6})\s+(.+?)\s*#*\s*$/.exec(line)
    if (heading) {
      // 页面自己的 h1 是会话标题，正文最大给到 h2，避免层级打架。
      const level = Math.min(heading[1].length + 1, 6)
      out.push(`<h${level} class="md-h">${inline(heading[2])}</h${level}>`)
      i += 1
      continue
    }

    if (/^\s*(?:>|&gt;)\s?/.test(line)) {
      const quoted: string[] = []
      while (i < lines.length && /^\s*(?:>|&gt;)\s?/.test(lines[i])) {
        quoted.push(lines[i].replace(/^\s*(?:>|&gt;)\s?/, ''))
        i += 1
      }
      out.push(`<blockquote class="md-quote">${inline(quoted.join('\n'))}</blockquote>`)
      continue
    }

    // 表格：当前行含 |，下一行是 `---|---` 这样的分隔行
    if (line.includes('|') && i + 1 < lines.length && isTableDivider(lines[i + 1])) {
      const header = splitRow(line)
      i += 2
      const rows: string[][] = []
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) {
        rows.push(splitRow(lines[i]))
        i += 1
      }
      const head = header.map((c) => `<th>${inline(c)}</th>`).join('')
      const body = rows
        .map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join('')}</tr>`)
        .join('')
      out.push(
        `<div class="md-table-wrap"><table class="md-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`,
      )
      continue
    }

    if (/^\s*[-*+]\s+/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*+]\s+/, ''))
        i += 1
      }
      out.push(
        `<ul class="md-list">${items.map((x) => `<li>${inline(x)}</li>`).join('')}</ul>`,
      )
      continue
    }

    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+[.)]\s+/, ''))
        i += 1
      }
      out.push(
        `<ol class="md-list">${items.map((x) => `<li>${inline(x)}</li>`).join('')}</ol>`,
      )
      continue
    }

    // 普通段落：连续非空行合并，行间用 <br /> 保留软换行。
    const paragraph: string[] = []
    while (i < lines.length && lines[i].trim() && !isBlockStart(lines, i)) {
      paragraph.push(lines[i])
      i += 1
    }
    if (paragraph.length === 0) {
      // 兜底：保证循环一定向前推进，不会卡死。
      paragraph.push(line)
      i += 1
    }
    out.push(`<p class="md-p">${paragraph.map(inline).join('<br />')}</p>`)
  }

  return out.join('')
}
