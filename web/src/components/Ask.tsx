import { useId, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
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

export function Ask({ ask = postAsk }: { ask?: AskFunction }) {
  const id = useId()
  const [text, setText] = useState('')
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
          placeholder="When are deploys frozen?" aria-describedby={`${id}-hint`}
          onChange={event => { setText(event.target.value) }} onKeyDown={onKeyDown} />
        <div><button type="submit" disabled={pending || text.trim() === ''}>{pending ? 'Asking…' : 'Ask'}</button></div>
      </form>
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
