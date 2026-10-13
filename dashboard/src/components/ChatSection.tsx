import type { FormEvent } from 'react'
import { useState } from 'react'
import { useChat } from '../hooks/useChat'
import type { ChatMeta } from '../lib/types'
import { buttonSolid, card, cardGlow, spinnerOnGradient } from '../lib/ui'
import { Reveal } from './Reveal'

/** Client mirror of the server's LAB_CHAT_MAX_CHARS default. */
const MAX_CHARS = 400

/** "served by {model_name|group} · {latency_ms}ms[, n retry/retries][, n fallback(s)]". */
function attributionLine(meta: ChatMeta): string {
  let line = `served by ${meta.model_name ?? meta.served_by ?? 'unknown'}`
  if (meta.latency_ms !== null) line += ` · ${meta.latency_ms}ms`
  const retries = meta.attempted_retries ?? 0
  if (retries > 0) line += `, ${retries} ${retries === 1 ? 'retry' : 'retries'}`
  const fallbacks = meta.attempted_fallbacks ?? 0
  if (fallbacks > 0) line += `, ${fallbacks} ${fallbacks === 1 ? 'fallback' : 'fallbacks'}`
  return line
}

/** "Try the router": real completions through the live gateway, with attribution. */
export function ChatSection() {
  const { turns, streaming, cooldownReason, cooldownRemainingS, send } = useChat()
  const [input, setInput] = useState('')

  const limited = cooldownReason !== null
  const canSend = input.trim() !== '' && !streaming && !limited

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!canSend) return
    if (send(input)) setInput('')
  }

  let buttonLabel = 'Send'
  if (streaming) buttonLabel = 'Streaming…'
  else if (cooldownReason === 'rate_limited') {
    buttonLabel = `Rate limited - ${cooldownRemainingS ?? 0}s`
  } else if (cooldownReason === 'busy') {
    buttonLabel = `Busy - ${cooldownRemainingS ?? 0}s`
  }

  return (
    <section id="chat" className="scroll-mt-20">
      <div className="mx-auto max-w-5xl px-5 pt-10 sm:px-8 sm:pt-12">
        <Reveal>
          <div className={`${card} ${cardGlow} p-6 sm:p-8`}>
            <h2 className="font-display text-2xl font-bold tracking-tight sm:text-3xl">
              Try the router
            </h2>
            <p className="mt-2 max-w-2xl text-sm leading-relaxed text-muted">
              Send a message through the real gateway behind this server: free-tier pools handle
              it live, and every reply shows which deployment served it.
            </p>

            <div aria-live="polite" className="mt-6 space-y-5" data-testid="chat-transcript">
              {turns.map((turn, index) => {
                const awaitingFirstFrame =
                  streaming &&
                  index === turns.length - 1 &&
                  !turn.answer &&
                  !turn.reasoning &&
                  !turn.notice
                const attribution = turn.meta ? attributionLine(turn.meta) : null
                return (
                  <div key={turn.id} className="space-y-2">
                    <p className="ml-auto w-fit max-w-[85%] rounded-2xl border border-line bg-surface/60 px-3.5 py-2.5 text-sm whitespace-pre-wrap text-ink">
                      {turn.question}
                    </p>
                    <div className="space-y-2">
                      {turn.reasoning ? (
                        <div
                          data-testid="chat-thinking"
                          className="rounded-xl border border-line/60 bg-bg-soft/60 px-3 py-2"
                        >
                          <p className="font-mono text-[10px] tracking-[0.25em] text-dim uppercase">
                            thinking
                          </p>
                          <p className="mt-1 text-xs leading-relaxed whitespace-pre-wrap text-dim">
                            {turn.reasoning}
                          </p>
                          {turn.reasoningCapped ? (
                            <p className="mt-1.5 text-xs text-warn">
                              hit the token cap while the model was reasoning
                            </p>
                          ) : null}
                        </div>
                      ) : null}
                      {turn.answer ? (
                        <p
                          data-testid="chat-answer"
                          className="text-sm leading-relaxed whitespace-pre-wrap text-ink"
                        >
                          {turn.answer}
                        </p>
                      ) : null}
                      {turn.notice ? (
                        <p
                          data-testid="chat-notice"
                          className="text-sm leading-relaxed text-warn"
                        >
                          {turn.notice}
                        </p>
                      ) : null}
                      {attribution !== null ? (
                        <p
                          data-testid="chat-attribution"
                          title={attribution}
                          className="font-mono text-[11px] break-words text-dim"
                        >
                          {attribution}
                        </p>
                      ) : null}
                      {awaitingFirstFrame ? (
                        <p className="text-xs text-dim">reaching the free pools…</p>
                      ) : null}
                    </div>
                  </div>
                )
              })}
            </div>

            <form
              onSubmit={onSubmit}
              className="mt-6 flex flex-col gap-3 sm:flex-row sm:items-center"
            >
              <input
                type="text"
                value={input}
                onChange={(event) => setInput(event.target.value)}
                maxLength={MAX_CHARS}
                placeholder="Ask the router anything…"
                aria-label="Chat message"
                className="w-full flex-1 rounded-full border border-line bg-bg-soft px-4 py-2.5 text-sm text-ink placeholder:text-dim focus:border-accent/60 focus:outline-none"
              />
              <button
                type="submit"
                disabled={!canSend}
                aria-busy={streaming}
                className={`${buttonSolid} px-6 py-2.5 text-sm whitespace-nowrap`}
              >
                {streaming ? <span className={`h-4 w-4 ${spinnerOnGradient}`} /> : null}
                {buttonLabel}
              </button>
            </form>
            <div className="mt-2 flex items-center justify-between gap-3 text-xs text-dim">
              <p>{streaming ? 'streaming from the free pools…' : 'Enter sends.'}</p>
              <p className={input.length >= MAX_CHARS ? 'text-warn' : undefined}>
                {input.length}/{MAX_CHARS}
              </p>
            </div>
            <p className="mt-4 text-xs leading-relaxed text-dim">
              Real completions from free-tier pools. 5 messages / 10 min per visitor; replies are
              capped.
            </p>
          </div>
        </Reveal>
      </div>
    </section>
  )
}
