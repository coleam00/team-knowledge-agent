import { useEffect, useId, useLayoutEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { Answer } from './Answer'
import { CitationPanel } from './CitationPanel'
import { BranchIcon, ChatPlusIcon, CompassIcon, GraphIcon, LogoMark, SendIcon, ShieldIcon } from './Icons'
import { ErrorState } from './States'
import { FAILURE, postAsk } from '../askClient'
import styles from './Ask.module.css'
import type { AnswerResponse } from '../answer'

export type AskFunction = (question: string) => Promise<AnswerResponse>

export interface Suggestion {
  label: string
  question: string
}

export interface LibrarySummary {
  repositories: number
  files: number
  chunks: number
  busy: number
}

type Result =
  | { status: 'pending' }
  | { status: 'done'; response: AnswerResponse }
  | { status: 'failed'; message: string }

interface Exchange {
  id: number
  question: string
  result: Result
}

interface PanelState {
  id: number
  selectedN: number | null
  token: number
}

const SUGGESTION_ICONS = [CompassIcon, GraphIcon, ShieldIcon, BranchIcon]

function plural(count: number, word: string, many = `${word}s`) {
  return `${count.toLocaleString('en-US')} ${count === 1 ? word : many}`
}

/** The citation panel is a column on wide screens and a drawer elsewhere; only the column opens by itself. */
function panelIsColumn() {
  return typeof window.matchMedia === 'function' && window.matchMedia('(min-width: 1200px)').matches
}

function ContextLine({ library }: { library: LibrarySummary }) {
  return (
    <p className={styles.context}>
      <span className={styles.contextDot} aria-hidden="true" />
      Answering from {plural(library.repositories, 'repository', 'repositories')}
      <span className={styles.sep} aria-hidden="true">·</span>{plural(library.files, 'file')}
      <span className={styles.sep} aria-hidden="true">·</span>{plural(library.chunks, 'chunk')} indexed
      {library.busy > 0 && <><span className={styles.sep} aria-hidden="true">·</span>{library.busy} indexing now</>}
    </p>
  )
}

function Hero({ library, suggestions, onPick }: {
  library: LibrarySummary | null
  suggestions: readonly Suggestion[]
  onPick: (question: string) => void
}) {
  return (
    <div className={styles.hero}>
      <span className={styles.heroLogo} aria-hidden="true"><LogoMark size={30} /></span>
      <h2>Ask your team’s knowledge</h2>
      <p className={styles.heroLead}>Find answers in your team’s code and docs, with sources you can check.</p>
      {library !== null && (library.repositories === 0
        ? <p className={styles.context}><a href="#/sources">Add a repository</a> to start asking questions.</p>
        : <ContextLine library={library} />)}
      {suggestions.length > 0 && <div className={styles.suggestions}>
        <p className={styles.label}>Try asking</p>
        <ul>
          {suggestions.map((suggestion, index) => {
            const Icon = SUGGESTION_ICONS[index % SUGGESTION_ICONS.length]!
            return <li key={suggestion.question}>
              <button type="button" className={styles.suggestion} onClick={() => { onPick(suggestion.question) }}>
                <span className={styles.suggestionIcon}><Icon size={18} /></span>
                <span className={styles.suggestionText}>
                  <span className={styles.suggestionLabel}>{suggestion.label}</span>
                  <span className={styles.suggestionQuestion}>{suggestion.question}</span>
                </span>
              </button>
            </li>
          })}
        </ul>
      </div>}
    </div>
  )
}

function Thinking({ library }: { library: LibrarySummary | null }) {
  const label = library && library.repositories > 0
    ? `Searching ${plural(library.repositories, 'repository', 'repositories')}…`
    : 'Searching the indexed sources…'
  return (
    <div className={styles.thinking} role="status" aria-live="polite">
      <p className={styles.thinkingLabel}>
        <span className={styles.dots} aria-hidden="true"><i /><i /><i /></span>{label}
      </p>
      <div className={styles.skeleton} aria-hidden="true"><span /><span /><span /></div>
    </div>
  )
}

export function Ask({ ask = postAsk, suggestions = [], library = null, preset }: {
  ask?: AskFunction
  suggestions?: readonly Suggestion[]
  library?: LibrarySummary | null
  /** A question handed over from elsewhere in the app; a new key asks it again. */
  preset?: { question: string; key: number }
}) {
  const uid = useId()
  const [text, setText] = useState('')
  const [exchanges, setExchanges] = useState<Exchange[]>([])
  const [panel, setPanel] = useState<PanelState | null>(null)
  const [pending, setPending] = useState(false)
  const busy = useRef(false)
  const generation = useRef(0)
  const nextId = useRef(0)
  const scroller = useRef<HTMLDivElement>(null)
  const rows = useRef(new Map<number, HTMLElement>())
  const box = useRef<HTMLTextAreaElement>(null)
  const scrollTarget = useRef<number | null>(null)

  async function submit(question: string, retryId?: number) {
    const trimmed = question.trim()
    if (!trimmed || busy.current) return
    busy.current = true
    setPending(true)
    const mine = generation.current
    const id = retryId ?? ++nextId.current
    scrollTarget.current = id
    const update = (result: Result) => {
      setExchanges(list => list.map(item => item.id === id ? { ...item, result } : item))
    }
    if (retryId === undefined) {
      setExchanges(list => [...list, { id, question: trimmed, result: { status: 'pending' } }])
      setText('')
    } else {
      update({ status: 'pending' })
    }
    try {
      const response = await ask(trimmed)
      if (mine !== generation.current) return
      scrollTarget.current = id
      update({ status: 'done', response })
      if (response.known && panelIsColumn()) setPanel({ id, selectedN: null, token: 0 })
    } catch (error) {
      if (mine !== generation.current) return
      scrollTarget.current = id
      update({ status: 'failed', message: error instanceof Error && error.message ? error.message : FAILURE })
    } finally {
      if (mine === generation.current) {
        busy.current = false
        setPending(false)
      }
    }
  }

  function newChat() {
    generation.current += 1
    busy.current = false
    setPending(false)
    setExchanges([])
    setPanel(null)
    setText('')
    box.current?.focus()
  }

  const handled = useRef<number | null>(null)
  useEffect(() => {
    if (preset && handled.current !== preset.key) {
      handled.current = preset.key
      void submit(preset.question)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preset?.key])

  // Bring the newest question to the top of the thread; answers then grow beneath it.
  useLayoutEffect(() => {
    const id = scrollTarget.current
    if (id === null) return
    scrollTarget.current = null
    const row = rows.current.get(id)
    if (row && scroller.current) {
      const top = row.offsetTop - 16
      scroller.current.scrollTo?.({ top, behavior: 'smooth' })
    }
  }, [exchanges])

  useLayoutEffect(() => {
    const element = box.current
    if (!element) return
    element.style.height = 'auto'
    element.style.height = `${Math.min(element.scrollHeight, 220)}px`
  }, [text])

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void submit(text)
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.code !== 'Enter' && event.code !== 'NumpadEnter') return
    if (event.shiftKey || event.nativeEvent.isComposing) return
    event.preventDefault()
    void submit(text)
  }

  const active = panel ? exchanges.find(item => item.id === panel.id) : undefined
  const activeResponse = active?.result.status === 'done' && active.result.response.known ? active.result.response : null
  const showPanel = panel !== null && active !== undefined && activeResponse !== null
  const empty = exchanges.length === 0

  return (
    <div className={styles.ask} data-panel={showPanel}>
      <section className={styles.conversation} aria-label="Conversation">
        <header className={styles.topbar}>
          <div className={styles.titleGroup}>
            <h1>Ask</h1>
            {!empty && library !== null && library.repositories > 0 && <span className={styles.topContext}>
              <span className={styles.contextDot} aria-hidden="true" />
              {plural(library.repositories, 'repository', 'repositories')} · {plural(library.chunks, 'chunk')}
            </span>}
          </div>
          <button type="button" className={styles.newChat} onClick={newChat} disabled={empty}>
            <ChatPlusIcon size={16} />New chat
          </button>
        </header>
        <div className={styles.scroller} ref={scroller}>
          {empty
            ? <Hero library={library} suggestions={suggestions} onPick={question => { void submit(question) }} />
            : <div className={styles.thread} role="log" aria-label="Messages">
              {exchanges.map(exchange => <div key={exchange.id} className={styles.exchange}
                ref={element => {
                  if (element) rows.current.set(exchange.id, element)
                  else rows.current.delete(exchange.id)
                }}>
                <div className={styles.user}>
                  <p className={styles.bubble}><span className={styles.srOnly}>You asked: </span>{exchange.question}</p>
                </div>
                <div className={styles.assistant}>
                  <span className={styles.avatar} aria-hidden="true"><LogoMark size={18} /></span>
                  <div className={styles.message}>
                    {exchange.result.status === 'pending' && <Thinking library={library} />}
                    {exchange.result.status === 'done' && <Answer response={exchange.result.response}
                      idPrefix={`${uid}-${exchange.id}`}
                      selectedN={panel?.id === exchange.id ? panel.selectedN : null}
                      onOpenSources={() => {
                        setPanel(previous => ({ id: exchange.id, selectedN: previous?.id === exchange.id ? previous.selectedN : null, token: previous?.token ?? 0 }))
                      }}
                      onCite={citation => {
                        setPanel(previous => ({ id: exchange.id, selectedN: citation.n, token: (previous?.token ?? 0) + 1 }))
                      }} />}
                    {exchange.result.status === 'failed' && <ErrorState message={exchange.result.message} retryLabel="Retry"
                      onRetry={() => { void submit(exchange.question, exchange.id) }} />}
                  </div>
                </div>
              </div>)}
            </div>}
        </div>
        <div className={styles.composerWrap}>
          <form className={styles.composer} onSubmit={onSubmit} aria-busy={pending}>
            <div className={styles.composerBox}>
              <textarea ref={box} name="question" rows={1} value={text} aria-label="Your question"
                placeholder={empty ? 'Ask anything about your team’s code and docs…' : 'Ask a follow-up…'}
                aria-describedby={`${uid}-hint`}
                onChange={event => { setText(event.target.value) }} onKeyDown={onKeyDown} />
              <button type="submit" className={styles.send} aria-label="Send" disabled={pending || text.trim() === ''}>
                <SendIcon size={18} />
              </button>
            </div>
            <p className={styles.hint} id={`${uid}-hint`}>
              <span>Enter to send · Shift+Enter for a new line</span>
              <span className={styles.hintAside}>Answers are grounded in your indexed repositories</span>
            </p>
          </form>
        </div>
      </section>
      {showPanel && <CitationPanel citations={activeResponse.citations} question={active.question}
        idPrefix={`${uid}-${active.id}`} selectedN={panel.selectedN} focusToken={panel.token}
        onClose={() => { setPanel(null) }} />}
    </div>
  )
}
