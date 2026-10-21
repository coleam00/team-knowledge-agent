import { useId, useRef, useState, type Ref } from 'react'
import { UnknownAnswer } from './States'
import styles from './Answer.module.css'
import type { Citation } from '../citation'

export type { Citation } from '../citation'

export interface AnswerResponse {
  known: boolean
  answer: string
  citations: readonly Citation[]
}

function displayPath(path: string) {
  if (path.length <= 48) return path
  const filename = path.slice(path.lastIndexOf('/') + 1)
  return filename === path
    ? `${path.slice(0, 20)}…${path.slice(-24)}`
    : `${path.slice(0, 18)}…/${filename}`
}

function githubUrl(url: string) {
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'https:' && parsed.hostname === 'github.com' && !parsed.username && !parsed.password
      ? url : undefined
  } catch {
    return undefined
  }
}

export function CitationCard({ citation, id, highlighted = false, ref }: {
  citation: Citation
  id?: string
  highlighted?: boolean
  ref?: Ref<HTMLElement>
}) {
  const titleId = useId()
  // A trailing newline terminates the last source row; it is not an extra row.
  const lines = citation.snippet === '' ? [] : citation.snippet.replace(/\r?\n$/, '').split(/\r?\n/)
    .slice(0, Math.min(6, Math.max(0, citation.end_line - citation.start_line + 1)))
  const href = githubUrl(citation.url)

  return (
    <article ref={ref} id={id} tabIndex={-1} aria-labelledby={titleId}
      className={styles.card} data-highlighted={highlighted}>
      <div className={styles.heading}>
        <span className={styles.number}>[{citation.n}]</span>
        <span className={styles.repo}>{citation.repo}</span>
      </div>
      <h3 id={titleId} className={styles.path} title={citation.path} aria-label={citation.path}>
        <span aria-hidden="true">{displayPath(citation.path)}</span>
      </h3>
      <p className={styles.range}>L{citation.start_line}-L{citation.end_line}</p>
      <ol className={styles.snippet} aria-label="Source lines" start={citation.start_line}>
        {lines.map((line, index) => <li key={index}>
          <span className={styles.lineNumber} aria-hidden="true">{citation.start_line + index}</span>
          <code>{line}</code>
        </li>)}
      </ol>
      {href ? <a href={href} target="_blank" rel="noopener noreferrer">Open on GitHub<span className={styles.srOnly}> (opens in a new tab)</span></a>
        : <p className={styles.repo}>GitHub link unavailable.</p>}
    </article>
  )
}

export function Answer({ response }: { response: AnswerResponse }) {
  const id = useId()
  const cards = useRef(new Map<number, HTMLElement>())
  const [selected, setSelected] = useState<Citation | null>(null)
  const citationsByMarker = new Map(response.citations.map(citation => [`[${citation.n}]`, citation]))

  function activate(citation: Citation) {
    setSelected(citation)
    const card = cards.current.get(citation.n)
    card?.focus({ preventScroll: true })
    card?.scrollIntoView({ block: 'nearest', behavior: 'instant' })
  }

  if (!response.known) return <UnknownAnswer />

  return (
    <div className={styles.answer}>
      <p className={styles.prose}>{response.answer.split(/(\[\d+\])/).map((part, index) => {
        const citation = citationsByMarker.get(part)
        return citation
          ? <button key={index} type="button" className={styles.chip} aria-label={`Show citation ${citation.n}`}
            aria-controls={`${id}-citation-${citation.n}`} onClick={() => { activate(citation) }}>{part}</button>
          : part
      })}</p>
      {response.citations.length > 0 && <section aria-labelledby={`${id}-sources`}>
        <h2 id={`${id}-sources`} className={styles.sourcesTitle}>Sources</h2>
        <ol className={styles.cards}>
          {response.citations.map(citation => <li key={citation.n}>
            <CitationCard citation={citation} id={`${id}-citation-${citation.n}`} highlighted={selected === citation}
              ref={element => {
                if (element) cards.current.set(citation.n, element)
                else cards.current.delete(citation.n)
              }} />
          </li>)}
        </ol>
      </section>}
    </div>
  )
}
