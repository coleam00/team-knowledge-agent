import { useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { Answer } from './Answer'
import { ErrorState, LoadingState } from './States'
import { FAILURE, postAsk } from '../askClient'
import styles from './Ask.module.css'
import type { AnswerResponse } from '../answer'

export type AskFunction = (question: string) => Promise<AnswerResponse>

type Result =
  | { status: 'idle' }
  | { status: 'pending'; question: string }
  | { status: 'done'; question: string; response: AnswerResponse }
  | { status: 'failed'; question: string; message: string }

export function Ask({ ask = postAsk, suggestions = [], initialQuestion }: {
  ask?: AskFunction
  suggestions?: readonly string[]
  initialQuestion?: string
}) {
  const id = useId()
  const [text, setText] = useState(initialQuestion ?? '')
  const [result, setResult] = useState<Result>({ status: 'idle' })
  const busy = useRef(false)

  async function submit(question: string) {
    const trimmed = question.trim()
    if (!trimmed || busy.current) return
    busy.current = true
    setResult({ status: 'pending', question: trimmed })
    try {
      const response = await ask(trimmed)
      setResult({ status: 'done', question: trimmed, response })
    } catch (error) {
      setResult({
        status: 'failed',
        question: trimmed,
        message: error instanceof Error && error.message ? error.message : FAILURE,
      })
    } finally {
      busy.current = false
    }
  }

  const asked = useRef(false)
  useEffect(() => {
    if (initialQuestion && !asked.current) {
      asked.current = true
      void submit(initialQuestion)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialQuestion])

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

  const pending = result.status === 'pending'
  return (
    <div className={styles.ask}>
      <form className={styles.form} onSubmit={onSubmit} aria-busy={pending}>
        <label htmlFor={`${id}-question`}>Your question</label>
        <p className={styles.hint} id={`${id}-hint`}>Press Enter to ask, or Shift+Enter for a new line.</p>
        <textarea id={`${id}-question`} name="question" rows={3} value={text} readOnly={pending}
          placeholder="Ask anything about your team’s code and docs…" aria-describedby={`${id}-hint`}
          onChange={event => { setText(event.target.value) }} onKeyDown={onKeyDown} />
        <div className={styles.formFooter}>
          <button type="submit" className={styles.submit} disabled={pending || text.trim() === ''}>{pending ? 'Asking…' : 'Ask'}</button>
        </div>
      </form>
      {result.status === 'idle' && suggestions.length > 0 && <div className={styles.suggestions}>
        <p className={styles.label}>Try asking</p>
        <ul>
          {suggestions.map(suggestion => <li key={suggestion}>
            <button type="button" className={styles.suggestion} onClick={() => { setText(suggestion); void submit(suggestion) }}>
              {suggestion}
            </button>
          </li>)}
        </ul>
      </div>}
      {result.status !== 'idle' && <section className={styles.result} aria-label="Answer">
        <p className={styles.question}><span className={styles.label}>You asked</span>{result.question}</p>
        {result.status === 'pending' && <LoadingState label="Searching the indexed sources…" />}
        {result.status === 'done' && <Answer response={result.response} />}
        {result.status === 'failed' && <ErrorState message={result.message} retryLabel="Retry"
          onRetry={() => { void submit(result.question) }} />}
      </section>}
    </div>
  )
}
