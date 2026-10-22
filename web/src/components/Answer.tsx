import { useId, useRef, useState, type ReactNode, type Ref } from 'react'
import { UnknownAnswer } from './States'
import styles from './Answer.module.css'
import type { Citation } from '../citation'
import type { AnswerResponse } from '../answer'

export type { Citation } from '../citation'
export type { AnswerResponse } from '../answer'

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


type Block = { kind: 'p'; lines: string[] } | { kind: 'ol' | 'ul'; items: string[] }

/** Splits model text into paragraphs and lists. Text only: nothing is ever parsed as HTML. */
function blocks(text: string): Block[] {
  const out: Block[] = []
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trimEnd()
    const ordered = /^\s*\d+[.)]\s+(.*)$/.exec(line)
    const bullet = /^\s*[-*•]\s+(.*)$/.exec(line)
    const last = out[out.length - 1]
    if (ordered || bullet) {
      const kind = ordered ? 'ol' : 'ul'
      const item = (ordered ?? bullet)![1]!
      if (last && last.kind === kind) last.items.push(item)
      else out.push({ kind, items: [item] })
    } else if (line.trim() === '') {
      out.push({ kind: 'p', lines: [] })
    } else if (last && last.kind === 'p') {
      last.lines.push(line)
    } else if (last && (last.kind === 'ol' || last.kind === 'ul') && /^\s{2,}\S/.test(raw)) {
      last.items[last.items.length - 1] += ` ${line.trim()}`
    } else {
      out.push({ kind: 'p', lines: [line] })
    }
  }
  return out.filter(block => block.kind !== 'p' || block.lines.length > 0)
}

function inline(text: string, chip: (marker: string, key: string) => ReactNode | null, prefix: string): ReactNode[] {
  return text.split(/(`[^`]+`|\*\*[^*]+\*\*|\[\d+\])/).map((part, index) => {
    const key = `${prefix}-${index}`
    if (/^\[\d+\]$/.test(part)) return chip(part, key) ?? part
    if (part.length > 2 && part.startsWith('`') && part.endsWith('`')) return <code key={key} className={styles.inlineCode}>{part.slice(1, -1)}</code>
    if (part.length > 4 && part.startsWith('**') && part.endsWith('**')) return <strong key={key}>{part.slice(2, -2)}</strong>
    return part
  })
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
      <div className={styles.verdict}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M5 12l4 4L19 6" /></svg>
        <span>Grounded answer</span>
        <span className={styles.verdictMeta}>{response.citations.length} source{response.citations.length === 1 ? '' : 's'} cited
          · {new Set(response.citations.map(citation => citation.path)).size} file{new Set(response.citations.map(citation => citation.path)).size === 1 ? '' : 's'}</span>
      </div>
      <div className={styles.prose}>{(() => {
        const chip = (marker: string, key: string) => {
          const citation = citationsByMarker.get(marker)
          return citation
            ? <button key={key} type="button" className={styles.chip} aria-label={`Show citation ${citation.n}`}
              aria-controls={`${id}-citation-${citation.n}`} onClick={() => { activate(citation) }}>{marker}</button>
            : null
        }
        return blocks(response.answer).map((block, index) => block.kind === 'p'
          ? <p key={index}>{inline(block.lines.join(' '), chip, `p${index}`)}</p>
          : block.kind === 'ol'
            ? <ol key={index}>{block.items.map((item, i) => <li key={i}>{inline(item, chip, `o${index}-${i}`)}</li>)}</ol>
            : <ul key={index}>{block.items.map((item, i) => <li key={i}>{inline(item, chip, `u${index}-${i}`)}</li>)}</ul>)
      })()}</div>
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
