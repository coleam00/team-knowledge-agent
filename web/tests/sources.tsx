// Browser fixture only: real components with local callbacks, never shipped in the app.
import { useState } from 'react'
import { createRoot } from 'react-dom/client'
import { Sources } from '../src/components/Sources'
import { sources } from './sources-fixtures'
import styles from '../src/App.module.css'
import '../src/styles/tokens.css'

export function Preview() {
  const state = window.location.hash.slice(1)
  const [calls, setCalls] = useState<string[]>([])
  return (
    <main className={styles.main}>
      <div className={styles.answerColumn}>
        <header className={styles.pageHeader}>
          <h1>Sources</h1>
          <p>A shared library of the repositories your team relies on.</p>
        </header>
        <Sources sources={['empty', 'pending', 'error'].includes(state) ? [] : sources.filter(source => state === 'all' || source.status === state)}
          onAdd={async url => {
            setCalls(previous => [...previous, `add: ${url}`])
            if (state === 'pending') await new Promise<void>(() => {})
            if (state === 'error') throw new Error('This repository is already a source.')
          }}
          onAsk={source => { setCalls(previous => [...previous, `ask: ${source.id}`]) }} />
        <p aria-label="Callback log">{calls.join('; ')}</p>
      </div>
    </main>
  )
}

createRoot(document.getElementById('root')!).render(<Preview />)
