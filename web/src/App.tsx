import { useSyncExternalStore } from 'react'
import { Ask } from './components/Ask'
import { EmptySources } from './components/States'
import styles from './App.module.css'

function subscribe(listener: () => void) {
  window.addEventListener('hashchange', listener)
  return () => window.removeEventListener('hashchange', listener)
}

function currentRoute() {
  return window.location.hash === '#/sources' ? 'sources' : 'ask'
}

export default function App() {
  const route = useSyncExternalStore(subscribe, currentRoute, () => 'ask')

  return (
    <div className={styles.shell}>
      <a className={styles.skipLink} href="#main" onClick={event => {
        event.preventDefault()
        document.getElementById('main')?.focus()
      }}>Skip to content</a>
      <aside className={styles.sidebar}>
        <div className={styles.brand}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
            <path d="M4 4h6l2 2 2-2h6v15h-6l-2 2-2-2H4zM12 6v15" />
          </svg>
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
          </a>
        </nav>
        <p className={styles.sidebarNote}>Your team's knowledge.<br />Answers you can verify.</p>
      </aside>
      <main id="main" tabIndex={-1} className={styles.main}>
        <div className={styles.answerColumn}>
          <header className={styles.pageHeader}>
            <p className={styles.eyebrow}>TEAM KNOWLEDGE</p>
            <h1>{route === 'ask' ? 'Ask' : 'Sources'}</h1>
            <p className={styles.description}>
              {route === 'ask'
                ? 'Find answers in your team’s code and docs, with sources you can check.'
                : 'A shared library of the repositories your team relies on.'}
            </p>
          </header>
          {route === 'ask' ? (
            <Ask />
          ) : (
            <EmptySources addForm={
              <p className={styles.note}>Adding repositories will be available in a later release.</p>
            } />
          )}
        </div>
      </main>
    </div>
  )
}
