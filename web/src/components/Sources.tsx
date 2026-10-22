import { useId, useRef, useState, type FormEvent } from 'react'
import { ExternalIcon, FileIcon, LayersIcon, PlusIcon, RepoIcon, SparkIcon } from './Icons'
import { PageHeader } from './PageHeader'
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

const count = (value: number) => value.toLocaleString('en-US')

export function AddRepositoryForm({ onAdd, onCancel }: { onAdd: Action; onCancel?: () => void }) {
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
      <div className={styles.formRow}>
        <input id={`${id}-url`} name="url" type="url" required autoComplete="url"
          placeholder="https://github.com/team/handbook" value={url} readOnly={pending}
          aria-invalid={Boolean(error)} aria-describedby={`${id}-hint${error ? ` ${id}-error` : ''}`}
          onChange={event => { setUrl(event.target.value); setError(null) }} />
        <button type="submit" className={styles.primary} disabled={pending}>{pending ? 'Adding repository…' : 'Add repository'}</button>
        {onCancel && <button type="button" onClick={onCancel} disabled={pending}>Cancel</button>}
      </div>
      {error && <p className={styles.error} id={`${id}-error`} role="alert">{error}</p>}
      {pending && <p className={styles.hint} role="status">Adding this repository. Your URL is kept here.</p>}
    </form>
  )
}

const STEPS = [
  { title: 'Clone', text: 'Pulled at a pinned commit, with git hooks disabled.' },
  { title: 'Select', text: 'Binary, vendored and oversized files are skipped.' },
  { title: 'Chunk', text: 'Split by headings or line windows, keeping exact line ranges.' },
  { title: 'Embed', text: 'Each chunk is embedded and stored for hybrid search.' },
]

const DATE = new Intl.DateTimeFormat('en-GB', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC' })

export function SourceCard({ source, onAsk, totalChunks = 0 }: {
  source: SourceSummary
  onAsk?: (source: SourceSummary) => void
  /** Chunks across the whole library, for the share bar. */
  totalChunks?: number
}) {
  const share = source.status === 'ready' && totalChunks > 0 ? Math.round((source.chunk_count / totalChunks) * 100) : null
  const titleId = useId()
  return (
    <article className={styles.card} aria-labelledby={titleId} data-status={source.status}>
      <div className={styles.heading}>
        <span className={styles.avatar} aria-hidden="true">{source.name.charAt(0).toUpperCase()}</span>
        <h2 id={titleId} aria-label={`${source.owner}/${source.name}`}>
          <span className={styles.owner}>{source.owner}/</span><span className={styles.name}>{source.name}</span>
        </h2>
        <span className={`${styles.status} ${styles[source.status]}`}>
          <span className={styles.dot} aria-hidden="true" />{source.status}
        </span>
      </div>
      <dl className={styles.metadata}>
        <div><dt>Files</dt><dd>{count(source.file_count)}</dd></div>
        <div><dt>Chunks</dt><dd>{count(source.chunk_count)}</dd></div>
        <div><dt>Indexed commit</dt><dd>{source.indexed_commit
          ? <code title={source.indexed_commit}>{source.indexed_commit.slice(0, 7)}</code>
          : 'Not indexed yet'}</dd></div>
        <div className={styles.wide}><dt>Last indexed</dt><dd>{source.indexed_at
          ? <time dateTime={source.indexed_at}>{DATE.format(new Date(source.indexed_at))} UTC</time>
          : 'Not indexed yet'}</dd></div>
      </dl>
      {share !== null && <div className={styles.share}>
        <span>Share of library</span>
        <span className={styles.bar} aria-hidden="true"><span style={{ width: `${Math.max(share, 3)}%` }} /></span>
        <span className={styles.sharePercent}>{share}%</span>
      </div>}
      {source.status === 'failed' && <p className={styles.error}>Indexing failed: {source.error || 'No error details available.'}</p>}
      <div className={styles.actions}>
        {onAsk && <button type="button" className={styles.primary} disabled={source.status !== 'ready'}
          onClick={() => { onAsk(source) }}><SparkIcon size={16} />Ask about this repo</button>}
        {source.url && /^https:\/\/github\.com\//.test(source.url) && <a className={styles.link} href={source.url} target="_blank"
          rel="noopener noreferrer">Open on GitHub<ExternalIcon size={14} /><span className={styles.srOnly}> (opens in a new tab)</span></a>}
      </div>
    </article>
  )
}

export function Sources({ sources, onAdd, onAsk }: {
  sources: readonly SourceSummary[]
  onAdd: Action
  onAsk?: (source: SourceSummary) => void
}) {
  const [adding, setAdding] = useState(false)
  const add: Action = async url => {
    await onAdd(url)
    setAdding(false)
  }
  const ready = sources.filter(source => source.status === 'ready')
  const totalChunks = ready.reduce((sum, source) => sum + source.chunk_count, 0)
  const stats = [
    { label: 'Repositories', value: count(sources.length), icon: <RepoIcon size={18} /> },
    { label: 'Ready', value: count(ready.length), icon: <SparkIcon size={18} /> },
    { label: 'Files indexed', value: count(ready.reduce((sum, source) => sum + source.file_count, 0)), icon: <FileIcon size={18} /> },
    { label: 'Chunks', value: count(ready.reduce((sum, source) => sum + source.chunk_count, 0)), icon: <LayersIcon size={18} /> },
  ]

  return (
    <div className={styles.page}>
      <PageHeader title="Sources"
        description="The repositories your team relies on. Each one is cloned, chunked and embedded at a pinned commit."
        actions={sources.length > 0 && !adding && <button type="button" className={styles.primary} onClick={() => { setAdding(true) }}>
          <PlusIcon size={16} />Add repository
        </button>} />
      {sources.length === 0
        ? <EmptySources addForm={<AddRepositoryForm onAdd={onAdd} />} />
        : <>
          {adding && <section className={styles.addPanel} aria-label="Add a repository">
            <AddRepositoryForm onAdd={add} onCancel={() => { setAdding(false) }} />
          </section>}
          <dl className={styles.stats}>
            {stats.map(stat => <div key={stat.label}>
              <span className={styles.statIcon}>{stat.icon}</span>
              <dt>{stat.label}</dt>
              <dd>{stat.value}</dd>
            </div>)}
          </dl>
          <ul className={styles.grid} aria-label="Repositories">
            {sources.map(source => <li key={source.id}><SourceCard source={source} onAsk={onAsk} totalChunks={totalChunks} /></li>)}
            {!adding && <li><button type="button" className={styles.addTile} onClick={() => { setAdding(true) }}>
              <span className={styles.addIcon}><PlusIcon size={20} /></span>
              <span className={styles.addTitle}>Add a repository</span>
              <span className={styles.addHint}>Index another public GitHub repository</span>
            </button></li>}
          </ul>
          <section className={styles.steps} aria-labelledby="indexing-steps">
            <h2 id="indexing-steps">How a repository becomes searchable</h2>
            <ol>
              {STEPS.map((step, index) => <li key={step.title}>
                <span className={styles.stepNumber}>{index + 1}</span>
                <span className={styles.stepTitle}>{step.title}</span>
                <span className={styles.stepText}>{step.text}</span>
              </li>)}
            </ol>
          </section>
        </>}
    </div>
  )
}
