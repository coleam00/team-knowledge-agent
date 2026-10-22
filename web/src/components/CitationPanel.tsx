import { useEffect, useRef, type KeyboardEvent } from 'react'
import { CitationCard, type Citation } from './Answer'
import { CloseIcon } from './Icons'
import styles from './CitationPanel.module.css'

export function CitationPanel({ citations, question, idPrefix, selectedN, focusToken, onClose }: {
  citations: readonly Citation[]
  question: string
  idPrefix: string
  selectedN: number | null
  /** Bumped on every chip click so re-selecting the same citation still scrolls and focuses it. */
  focusToken: number
  onClose: () => void
}) {
  const cards = useRef(new Map<number, HTMLElement>())

  useEffect(() => {
    if (selectedN === null) return
    const card = cards.current.get(selectedN)
    if (!card) return
    if (focusToken > 0) card.focus({ preventScroll: true })
    card.scrollIntoView({ block: 'nearest', behavior: 'auto' })
  }, [idPrefix, selectedN, focusToken])

  function onKeyDown(event: KeyboardEvent) {
    if (event.code === 'Escape') onClose()
  }

  const files = new Set(citations.map(citation => citation.path)).size

  return (
    <>
      <div className={styles.backdrop} onClick={onClose} aria-hidden="true" />
      <aside className={styles.panel} aria-label="Sources" onKeyDown={onKeyDown}>
        <header className={styles.header}>
          <div className={styles.titles}>
            <h2>Sources</h2>
            <p className={styles.meta}>{citations.length} citation{citations.length === 1 ? '' : 's'} · {files} file{files === 1 ? '' : 's'}</p>
          </div>
          <button type="button" className={styles.close} onClick={onClose} aria-label="Close sources"><CloseIcon /></button>
        </header>
        <p className={styles.question} title={question}><span>For</span> {question}</p>
        <ol className={styles.cards}>
          {citations.map(citation => <li key={citation.n}>
            <CitationCard citation={citation} id={`${idPrefix}-citation-${citation.n}`} highlighted={selectedN === citation.n}
              ref={element => {
                if (element) cards.current.set(citation.n, element)
                else cards.current.delete(citation.n)
              }} />
          </li>)}
        </ol>
      </aside>
    </>
  )
}
