import { useId, useRef, useState, type FormEvent } from 'react'
import { EmptySources } from './States'
import styles from './Sources.module.css'

export type SourceStatus = 'queued' | 'indexing' | 'ready' | 'failed'

export interface SourceSummary {
  id: string
  owner: string
  name: string
  status: SourceStatus
  indexed_commit: string | null
  indexed_at: string | null
  file_count: number
  chunk_count: number
  error: string | null
  url?: string
}

type Action = (value: string) => void | Promise<void>

function message(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback
}

export function AddRepositoryForm({ onAdd }: { onAdd: Action }) {
  const id = useId()
  const [url, setUrl] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const submitting = useRef(false)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (submitting.current) return
    const entered = url.trim()
    if (!/^https:\/\/github\.com\/[a-z\d](?:[a-z\d-]*[a-z\d])?\/[a-z\d_.-]+\/?$/i.test(entered)) {
      setError('Enter a public GitHub repository URL, such as https://github.com/team/handbook.')
      return
    }
    setError(null)
    submitting.current = true
    setPending(true)
    try {
      await onAdd(entered)
    } catch (error) {
      setError(message(error, 'Could not add this repository. Please try again.'))
    } finally {
      submitting.current = false
      setPending(false)
    }
  }

  return (
    <form className={styles.form} onSubmit={submit} noValidate aria-labelledby={`${id}-title`} aria-busy={pending}>
      <h2 id={`${id}-title`}>Add repository</h2>
      <label htmlFor={`${id}-url`}>Repository URL</label>
      <p className={styles.hint} id={`${id}-hint`}>Use the URL of a public GitHub repository.</p>
      <input id={`${id}-url`} name="url" type="url" required autoComplete="url"
        placeholder="https://github.com/team/handbook" value={url} readOnly={pending}
        aria-invalid={Boolean(error)} aria-describedby={`${id}-hint${error ? ` ${id}-error` : ''}`}
        onChange={event => { setUrl(event.target.value); setError(null) }} />
      {error && <p className={styles.error} id={`${id}-error`} role="alert">{error}</p>}
      <div><button type="submit" disabled={pending}>{pending ? 'Adding repository…' : 'Add repository'}</button></div>
      {pending && <p className={styles.hint} role="status">Adding this repository. Your URL is kept here.</p>}
    </form>
  )
}

export function SourceCard({ source, onReindex, onAsk }: {
  source: SourceSummary
  onReindex?: Action
  onAsk?: (source: SourceSummary) => void
}) {
  const titleId = useId()
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const submitting = useRef(false)
  const busy = source.status === 'queued' || source.status === 'indexing'

  async function reindex() {
    if (!onReindex || busy || submitting.current) return
    submitting.current = true
    setPending(true)
    setError(null)
    try {
      await onReindex(source.id)
    } catch (error) {
      setError(message(error, 'Could not re-index this repository. Please try again.'))
    } finally {
      submitting.current = false
      setPending(false)
    }
  }

  return (
    <article className={styles.card} aria-labelledby={titleId}>
      <div className={styles.heading}>
        <h2 id={titleId}>{source.owner}/{source.name}</h2>
        <span className={`${styles.status} ${styles[source.status]}`}>
          <span className={styles.dot} aria-hidden="true" />{source.status}
        </span>
      </div>
      <dl className={styles.metadata}>
        <div><dt>Indexed commit</dt><dd>{source.indexed_commit
          ? <code title={source.indexed_commit}>{source.indexed_commit.slice(0, 7)}</code>
          : 'Not indexed yet'}</dd></div>
        <div><dt>Last indexed</dt><dd>{source.indexed_at
          ? <time dateTime={source.indexed_at}>{new Intl.DateTimeFormat('en-GB', {
            dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC',
          }).format(new Date(source.indexed_at))} UTC</time>
          : 'Not indexed yet'}</dd></div>
        <div><dt>Files</dt><dd>{source.file_count}</dd></div>
        <div><dt>Chunks</dt><dd>{source.chunk_count}</dd></div>
      </dl>
      {source.status === 'failed' && <p className={styles.error}>Indexing failed: {source.error || 'No error details available. Try re-indexing.'}</p>}
      {error && <p className={styles.error} role="alert">{error}</p>}
      <div className={styles.actions}>
        {onAsk && <button type="button" className={styles.primary} disabled={source.status !== 'ready'}
          onClick={() => { onAsk(source) }}>Ask about this repo</button>}
        {onReindex && <button type="button" disabled={busy || pending} onClick={reindex}>{pending ? 'Queuing re-index…' : 'Re-index'}</button>}
        {source.url && /^https:\/\/github\.com\//.test(source.url) && <a className={styles.link} href={source.url} target="_blank"
          rel="noopener noreferrer">Open on GitHub</a>}
      </div>
      {pending && <p className={styles.hint} role="status">Requesting re-index.</p>}
    </article>
  )
}

export function Sources({ sources, onAdd, onReindex, onAsk }: {
  sources: readonly SourceSummary[]
  onAdd: Action
  onReindex?: Action
  onAsk?: (source: SourceSummary) => void
}) {
  const form = <AddRepositoryForm onAdd={onAdd} />
  if (sources.length === 0) return <EmptySources addForm={form} />
  return (
    <div className={styles.sources}>
      <ul className={styles.list} aria-label="Repositories">
        {sources.map(source => <li key={source.id}><SourceCard source={source} onReindex={onReindex} onAsk={onAsk} /></li>)}
      </ul>
      <section className={styles.card}>{form}</section>
    </div>
  )
}
