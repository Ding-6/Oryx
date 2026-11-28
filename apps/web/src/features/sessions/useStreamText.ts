/**
 * 流式正文 / 思考过程的客户端攒包器。
 *
 * 后端链路本来就是通的：providers 逐 chunk 解析 → loop 发
 * `TEXT_MESSAGE_START/CONTENT/END` 和 `REASONING_*` → SSE 推给前端。
 * 缺的一直是「前端把这些事件攒成一条会长的消息」。
 *
 * 三个容易踩的坑，这里都处理了：
 *
 *  1. **message_id 不能当唯一键**：后端在 `_run_loop` 循环外生成一次
 *     message_id，一轮里多次模型调用（工具调用循环）复用同一个值，
 *     但每轮会各发一组 START/CONTENT/END，也各落库一条消息。
 *     所以每次 START 一律新建草稿，key 用前端自增序号。
 *
 *  2. **事件先于落库**：`TEXT_MESSAGE_END` 在 `store.add_message` 之前发，
 *     所以 END 时不能立刻把草稿换成「正式消息」。做法是让草稿留着，
 *     等详情接口把消息拿回来后按**内容相等**认领、自动隐藏草稿 ——
 *     用户看到的是同一段文字平滑接上，不会闪一下或出现两条。
 *
 *  3. **高频 delta 不能一条一次 setState**：模型一秒能吐几十个 chunk。
 *     这里把内容攒在 ref 里，40ms 合并成一次渲染。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import type { Message, StreamDraft, StreamThinking } from '@/lib/types'

const FLUSH_MS = 40
/** done 但始终没被落库消息认领的草稿最多挂 15 秒，防止 SSE 丢事件后永远转圈。 */
const STALE_MS = 15_000

export interface StreamTextState {
  /** 还没被正式消息认领的草稿（按产生顺序）。 */
  visibleDrafts: StreamDraft[]
  thinking: StreamThinking
  /** 喂一条 SSE 事件；只处理文本/思考族，其他类型原样忽略。 */
  onEvent: (ev: Record<string, unknown>) => void
  /** 切会话、收到 resync 时清空。 */
  reset: () => void
}

export function useStreamText(messages: Message[]): StreamTextState {
  const draftsRef = useRef<Map<string, StreamDraft>>(new Map())
  const activeRef = useRef<string | null>(null)
  const seqRef = useRef(0)
  const thinkingRef = useRef<StreamThinking>({ text: '', open: false, done: false })
  const flushTimer = useRef<number | undefined>(undefined)
  // 草稿存在 ref 里，靠这个版本号通知 React 重算可见列表。
  const [version, setVersion] = useState(0)

  const bump = useCallback(() => {
    if (flushTimer.current !== undefined) return
    flushTimer.current = window.setTimeout(() => {
      flushTimer.current = undefined
      setVersion((v) => v + 1)
    }, FLUSH_MS)
  }, [])

  const flushNow = useCallback(() => {
    if (flushTimer.current !== undefined) {
      window.clearTimeout(flushTimer.current)
      flushTimer.current = undefined
    }
    setVersion((v) => v + 1)
  }, [])

  const reset = useCallback(() => {
    draftsRef.current.clear()
    activeRef.current = null
    thinkingRef.current = { text: '', open: false, done: false }
    flushNow()
  }, [flushNow])

  const onEvent = useCallback(
    (ev: Record<string, unknown>) => {
      switch (String(ev.type ?? '')) {
        case 'TEXT_MESSAGE_START': {
          // 上一条没收到 END（断线 / 异常）—— 先收尾，别让它一直带光标。
          if (activeRef.current) {
            const prev = draftsRef.current.get(activeRef.current)
            if (prev && !prev.done) prev.done = true
          }
          seqRef.current += 1
          const key = `d${seqRef.current}`
          draftsRef.current.set(key, {
            key,
            messageId: String(ev.message_id ?? ''),
            content: '',
            done: false,
            startedAt: Date.now(),
          })
          activeRef.current = key
          bump()
          break
        }
        case 'TEXT_MESSAGE_CONTENT': {
          const delta = typeof ev.delta === 'string' ? ev.delta : ''
          if (!delta) break
          let key = activeRef.current
          if (!key || !draftsRef.current.has(key)) {
            // 半路接上流（断线续传丢了 START、或流式中途才打开页面）：
            // 按 message_id 找一条未收尾的，找不到就补一条，正文不能丢。
            const mid = String(ev.message_id ?? '')
            const found = [...draftsRef.current.values()]
              .reverse()
              .find((d) => !d.done && d.messageId === mid)
            if (found) {
              key = found.key
            } else {
              seqRef.current += 1
              key = `d${seqRef.current}`
              draftsRef.current.set(key, {
                key, messageId: mid, content: '', done: false, startedAt: Date.now(),
              })
            }
            activeRef.current = key
          }
          const draft = draftsRef.current.get(key)
          if (draft) draft.content += delta
          bump()
          break
        }
        case 'TEXT_MESSAGE_END': {
          const key = activeRef.current
          if (key) {
            const draft = draftsRef.current.get(key)
            if (draft) draft.done = true
          }
          activeRef.current = null
          flushNow()
          break
        }
        case 'REASONING_START':
          thinkingRef.current = { text: '', open: true, done: false }
          bump()
          break
        case 'REASONING_CONTENT': {
          const delta = typeof ev.delta === 'string' ? ev.delta : ''
          if (!delta) break
          const t = thinkingRef.current
          if (!t.open && !t.done) t.open = true
          t.text += delta
          bump()
          break
        }
        case 'REASONING_END':
          thinkingRef.current = { ...thinkingRef.current, open: false, done: true }
          flushNow()
          break
        default:
          break
      }
    },
    [bump, flushNow],
  )

  const sweep = useCallback(() => {
    const now = Date.now()
    let changed = false
    for (const [key, draft] of draftsRef.current) {
      if (draft.done && now - draft.startedAt > STALE_MS) {
        draftsRef.current.delete(key)
        if (activeRef.current === key) activeRef.current = null
        changed = true
      }
    }
    if (changed) flushNow()
  }, [flushNow])

  useEffect(() => {
    const timer = window.setInterval(sweep, 3000)
    return () => {
      window.clearInterval(timer)
      if (flushTimer.current !== undefined) window.clearTimeout(flushTimer.current)
    }
  }, [sweep])

  // 对账：detail 里已经落库的 assistant 消息，把内容相同的 done 草稿「认领」。
  // 从后往前各认一次，避免两条内容相同的回复互相抢。
  const visibleDrafts = useMemo(() => {
    const drafts = [...draftsRef.current.values()]
    const claimed = new Set<string>()
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      if (messages[i].role !== 'assistant') continue
      for (let j = drafts.length - 1; j >= 0; j -= 1) {
        const d = drafts[j]
        if (d.done && !claimed.has(d.key) && d.content === messages[i].content) {
          claimed.add(d.key)
          break
        }
      }
    }
    // 空且已收尾的草稿（模型这轮只调了工具、没写正文）不该渲染成空气泡。
    return drafts.filter((d) => !claimed.has(d.key) && (d.content.length > 0 || !d.done))
    // eslint-disable-next-line react-hooks/exhaustive-deps -- version 是草稿变化的信号
  }, [messages, version])

  const thinking = useMemo(() => ({ ...thinkingRef.current }), [version])

  return { visibleDrafts, thinking, onEvent, reset }
}
