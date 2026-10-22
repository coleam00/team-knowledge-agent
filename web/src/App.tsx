import { useState, useSyncExternalStore } from 'react'
import { Ask } from './components/Ask'
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

function suggestionsFor(sources: readonly SourceSummary[]) {
  const ready = sources.filter(source => source.status === 'ready')
  if (ready.length === 0) return []
  const name = ready[0]!.name
  return [
    `How do I get started with ${name}?`,
    `How is ${name} organised?`,
    `What has to pass before a change in ${name} can merge?`,
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

function ContextStrip({ sources }: { sources: readonly SourceSummary[] | null }) {
  if (sources === null) return null
  const t = totals(sources)
  if (sources.length === 0) {
    return <p className={styles.context}><a href="#/sources">Add a repository</a> to start asking questions.</p>
  }
  return (
    <p className={styles.context}>
      <span className={styles.contextDot} aria-hidden="true" />
      Answering from {plural(t.ready, 'repository', 'repositories')}
      <span className={styles.contextSep} aria-hidden="true">·</span>{plural(t.files, 'file')}
      <span className={styles.contextSep} aria-hidden="true">·</span>{plural(t.chunks, 'chunk')} indexed
      {t.busy > 0 && <><span className={styles.contextSep} aria-hidden="true">·</span>{t.busy} indexing now</>}
    </p>
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

  return (
    <div className={styles.shell}>
      <a className={styles.skipLink} href="#main" onClick={event => {
        event.preventDefault()
        document.getElementById('main')?.focus()
      }}>Skip to content</a>
      <aside className={styles.sidebar}>
        <div className={styles.brand}>
          <span className={styles.logo} aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
              <path d="M4 4h6l2 2 2-2h6v15h-6l-2 2-2-2H4zM12 6v15" />
            </svg>
          </span>
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
      <main id="main" tabIndex={-1} className={styles.main}>
        <div className={styles.answerColumn}>
          <header className={styles.pageHeader}>
            <p className={styles.eyebrow}>TEAM KNOWLEDGE</p>
            <h1>{route === 'ask' ? 'Ask' : 'Sources'}</h1>
            <p className={styles.description}>
              {route === 'ask'
                ? 'Find answers in your team’s code and docs, with sources you can check.'
                : 'The repositories your team relies on. Each one is cloned, chunked and embedded at a pinned commit.'}
            </p>
          </header>
          {route === 'ask' ? <>
            <ContextStrip sources={sources} />
            <Ask key={preset?.key} initialQuestion={preset?.question} suggestions={suggestionsFor(sources ?? [])} />
          </> : sources === null && !error ? <LoadingState label="Loading sources…" />
            : sources === null ? <ErrorState message={error!} onRetry={() => { void refresh() }} />
              : <>
                {sources.length > 0 && <dl className={styles.stats}>
                  <div><dt>Repositories</dt><dd>{sources.length}</dd></div>
                  <div><dt>Ready</dt><dd>{t.ready}</dd></div>
                  <div><dt>Files indexed</dt><dd>{t.files.toLocaleString('en-US')}</dd></div>
                  <div><dt>Chunks</dt><dd>{t.chunks.toLocaleString('en-US')}</dd></div>
                </dl>}
                <Sources sources={sources} onAdd={add} onAsk={askAbout} />
              </>}
        </div>
      </main>
    </div>
  )
}
