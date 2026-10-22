import { useEffect, useRef, useState, useId, type ReactNode, type Ref } from 'react'
import { CheckIcon, CopyIcon, ExternalIcon } from './Icons'
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
    if (part.length > 4 && part.startsWith('**') && part.endsWith('**')) return <strong key={key}>{inline(part.slice(2, -2), chip, key)}</strong>
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
        <span className={styles.repo} title={citation.repo}>{citation.repo}</span>
      </div>
      <h3 id={titleId} className={styles.path} title={citation.path} aria-label={citation.path}>
        <span aria-hidden="true">{displayPath(citation.path)}</span>
      </h3>
      <div className={styles.meta}>
        <p className={styles.range}>L{citation.start_line}-L{citation.end_line}</p>
        <span className={styles.commit} title={citation.commit}>@ {citation.commit.slice(0, 7)}</span>
      </div>
      <ol className={styles.snippet} aria-label="Source lines" start={citation.start_line}>
        {lines.map((line, index) => {
          // Wrapped rows hang under their own indentation instead of snapping back to column zero.
          const indent = Math.min(/^ */.exec(line)![0].length, 32)
          return <li key={index}>
            <span className={styles.lineNumber} aria-hidden="true">{citation.start_line + index}</span>
            <code style={indent > 0 ? { paddingLeft: `${indent}ch`, textIndent: `-${indent}ch` } : undefined}>{line}</code>
          </li>
        })}
      </ol>
      <div className={styles.cardFooter}>
        {href ? <a href={href} target="_blank" rel="noopener noreferrer">Open on GitHub<ExternalIcon size={14} /><span className={styles.srOnly}> (opens in a new tab)</span></a>
          : <p className={styles.repo}>GitHub link unavailable.</p>}
      </div>
    </article>
  )
}

function filename(path: string) {
  return path.slice(path.lastIndexOf('/') + 1) || path
}

export function Answer({ response, idPrefix, selectedN = null, onCite, onOpenSources }: {
  response: AnswerResponse
  /** Same prefix the citation panel uses for card ids, so chips can reference them. */
  idPrefix?: string
  selectedN?: number | null
  onCite?: (citation: Citation) => void
  onOpenSources?: () => void
}) {
  const fallbackId = useId()
  const prefix = idPrefix ?? fallbackId
  const [copied, setCopied] = useState(false)
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => () => { window.clearTimeout(timer.current) }, [])
  const citationsByMarker = new Map(response.citations.map(citation => [`[${citation.n}]`, citation]))

  if (!response.known) return <UnknownAnswer />

  const count = response.citations.length

  function copy() {
    void navigator.clipboard?.writeText(response.answer).then(() => {
      setCopied(true)
      window.clearTimeout(timer.current)
      timer.current = window.setTimeout(() => { setCopied(false) }, 1800)
    }, () => undefined)
  }

  const chip = (marker: string, key: string) => {
    const citation = citationsByMarker.get(marker)
    return citation
      ? <button key={key} type="button" className={styles.chip} aria-label={`Show citation ${citation.n}`}
        aria-controls={`${prefix}-citation-${citation.n}`} data-active={selectedN === citation.n}
        onClick={() => { onCite?.(citation) }}>{marker}</button>
      : null
  }

  return (
    <div className={styles.answer}>
      <div className={styles.verdict}>
        <span className={styles.verdictIcon}><CheckIcon size={12} /></span>
        <span className={styles.verdictLabel}>Grounded answer</span>
        <span className={styles.dot} aria-hidden="true">·</span>
        <button type="button" className={styles.sourcesButton} onClick={onOpenSources}>
          {count} source{count === 1 ? '' : 's'}
        </button>
      </div>
      <div className={styles.prose}>{blocks(response.answer).map((block, index) => block.kind === 'p'
        ? <p key={index}>{inline(block.lines.join(' '), chip, `p${index}`)}</p>
        : block.kind === 'ol'
          ? <ol key={index}>{block.items.map((item, i) => <li key={i}>{inline(item, chip, `o${index}-${i}`)}</li>)}</ol>
          : <ul key={index}>{block.items.map((item, i) => <li key={i}>{inline(item, chip, `u${index}-${i}`)}</li>)}</ul>)}</div>
      <div className={styles.footer}>
        <ul className={styles.sourceList} aria-label="Cited sources">
          {response.citations.map(citation => <li key={citation.n}>
            <button type="button" className={styles.sourcePill} data-active={selectedN === citation.n} title={citation.path}
              onClick={() => { onCite?.(citation) }}>
              <span className={styles.pillNumber}>{citation.n}</span>{filename(citation.path)}
            </button>
          </li>)}
        </ul>
        <button type="button" className={styles.copy} onClick={copy} aria-label={copied ? 'Answer copied' : 'Copy answer'}>
          {copied ? <CheckIcon size={15} /> : <CopyIcon size={15} />}<span>{copied ? 'Copied' : 'Copy'}</span>
        </button>
      </div>
    </div>
  )
}
