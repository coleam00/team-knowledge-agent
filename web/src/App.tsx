import { useState, useSyncExternalStore } from 'react'
import { Ask, type LibrarySummary, type Suggestion } from './components/Ask'
import { LogoMark } from './components/Icons'
import { PageHeader } from './components/PageHeader'
import { Sources, type SourceSummary } from './components/Sources'
import { ErrorState, LoadingState } from './components/States'
import { isBusy, useSources } from './sourcesClient'
import styles from './App.module.css'

function subscribe(listener: () => void) {
  window.addEventListener('hashchange', listener)
  return () => window.removeEventListener('hashchange', listener)
}

function currentRoute() {
  return window.location.hash === '#/sources' ? 'sources' : 'ask'
}

function totals(sources: readonly SourceSummary[]) {
  const ready = sources.filter(source => source.status === 'ready')
  return {
    ready: ready.length,
    busy: sources.filter(isBusy).length,
    files: ready.reduce((sum, source) => sum + source.file_count, 0),
    chunks: ready.reduce((sum, source) => sum + source.chunk_count, 0),
  }
}

function plural(count: number, word: string, many = `${word}s`) {
  return `${count.toLocaleString('en-US')} ${count === 1 ? word : many}`
}

function suggestionsFor(sources: readonly SourceSummary[]): Suggestion[] {
  const ready = sources.filter(source => source.status === 'ready')
  if (ready.length === 0) return []
  const first = ready[0]!.name
  const second = ready[1]?.name ?? first
  return [
    { label: 'Get started', question: `How do I get started with ${first}?` },
    { label: 'Structure', question: `How is ${second} organised?` },
    { label: 'Quality gates', question: `What has to pass before a change in ${first} can merge?` },
    { label: 'Architecture', question: `What are the main moving parts of ${first}?` },
  ]
}

function Library({ sources }: { sources: readonly SourceSummary[] | null }) {
  return (
    <section className={styles.library} aria-label="Indexed library">
      <p className={styles.libraryTitle}>Library</p>
      {sources === null ? <p className={styles.libraryEmpty}>Loading…</p>
        : sources.length === 0 ? <p className={styles.libraryEmpty}>No repositories yet</p>
          : <ul>
            {sources.map(source => <li key={source.id}>
              <span className={`${styles.libraryDot} ${styles[source.status]}`} aria-hidden="true" />
              <span className={styles.libraryName} title={`${source.owner}/${source.name}`}>{source.name}</span>
              <span className={styles.libraryMeta}>{source.status === 'ready' ? plural(source.chunk_count, 'chunk') : source.status}</span>
            </li>)}
          </ul>}
    </section>
  )
}

export default function App() {
  const route = useSyncExternalStore(subscribe, currentRoute, () => 'ask')
  const { sources, error, refresh, add } = useSources()
  const [preset, setPreset] = useState<{ question: string; key: number } | null>(null)

  function askAbout(source: SourceSummary) {
    setPreset({ question: `What is ${source.name} and how do I use it?`, key: Date.now() })
    window.location.hash = '#/ask'
  }

  const t = totals(sources ?? [])
  const library: LibrarySummary | null = sources === null ? null
    : { repositories: t.ready, files: t.files, chunks: t.chunks, busy: t.busy }

  return (
    <div className={styles.shell}>
      <a className={styles.skipLink} href="#main" onClick={event => {
        event.preventDefault()
        document.getElementById('main')?.focus()
      }}>Skip to content</a>
      <aside className={styles.sidebar}>
        <div className={styles.brand}>
          <span className={styles.logo} aria-hidden="true"><LogoMark /></span>
          <span>Team Knowledge<br />Agent</span>
        </div>
        <nav aria-label="Main navigation" className={styles.navigation}>
          <a href="#/ask" aria-current={route === 'ask' ? 'page' : undefined}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
              <path d="M5 4h14v12H9l-4 4zM8 8h8M8 12h5" />
            </svg>
            Ask
          </a>
          <a href="#/sources" aria-current={route === 'sources' ? 'page' : undefined}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
              <path d="M3 6h7l2 2h9v12H3zM3 6V4h7l2 2h7v2" />
            </svg>
            Sources
            {sources !== null && sources.length > 0 && <span className={styles.count}>{sources.length}</span>}
          </a>
        </nav>
        <Library sources={sources} />
        <p className={styles.sidebarNote}>Every answer cites the exact lines it came from, pinned to the indexed commit.</p>
      </aside>
      <main id="main" tabIndex={-1} className={route === 'ask' ? styles.chat : styles.main}>
        <div hidden={route !== 'ask'}>
          <Ask preset={preset ?? undefined} suggestions={suggestionsFor(sources ?? [])} library={library} />
        </div>
        {route === 'sources' && <div className={styles.pageColumn}>
          {sources === null && !error ? <>
            <PageHeader title="Sources" description="The repositories your team relies on." />
            <LoadingState label="Loading sources…" />
          </> : sources === null ? <>
            <PageHeader title="Sources" description="The repositories your team relies on." />
            <ErrorState message={error!} onRetry={() => { void refresh() }} />
          </> : <Sources sources={sources} onAdd={add} onAsk={askAbout} />}
        </div>}
      </main>
    </div>
  )
}
