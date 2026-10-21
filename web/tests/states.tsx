// Browser fixture only; not included in the production entry point.
import { createRoot } from 'react-dom/client'
import { EmptySources, ErrorState, LoadingState, UnknownAnswer } from '../src/components/States'
import styles from '../src/App.module.css'
import '../src/styles/tokens.css'

const state = window.location.hash.slice(1)
createRoot(document.getElementById('root')!).render(
  <main className={styles.main}>
    <div className={styles.answerColumn}>
      <header className={styles.pageHeader}>
        <h1>Shared states</h1>
        <p>Component verification preview</p>
        <code>docs/deploy.md · L12–L30 · abc1234</code>
      </header>
      {state === 'loading' && <LoadingState label="Loading sources…" />}
      {state === 'error' && <ErrorState message="The repository could not be reached." onRetry={() => {
        document.querySelector('h1')!.textContent = 'Retry requested'
      }} />}
      {state === 'unknown' && <UnknownAnswer />}
      {state === 'empty' && <EmptySources addForm={
        <form onSubmit={event => {
          event.preventDefault()
          document.querySelector('h1')!.textContent = 'Add requested'
        }}>
          <p><label htmlFor="repo">Repository URL</label></p>
          <p><input id="repo" name="url" type="url" placeholder="https://github.com/team/handbook" required /></p>
          <button type="submit">Add repository</button>
        </form>
      } />}
    </div>
  </main>,
)
