import { useId, type ReactNode } from 'react'
import styles from './States.module.css'

export function LoadingState({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className={styles.card} role="status" aria-live="polite">
      <p className={styles.muted}>{label}</p>
      <div className={styles.skeleton} aria-hidden="true">
        <span /><span /><span />
      </div>
    </div>
  )
}

export function ErrorState({ message, onRetry, retryLabel = 'Try again' }: {
  message: string
  onRetry: () => void
  retryLabel?: string
}) {
  return (
    <div className={`${styles.card} ${styles.error}`}>
      <div role="alert">
        <h2>Something went wrong</h2>
        <p>{message}</p>
      </div>
      <button type="button" onClick={onRetry}>{retryLabel}</button>
    </div>
  )
}

export function EmptySources({ addForm }: { addForm: ReactNode }) {
  const titleId = useId()
  return (
    <section className={`${styles.card} ${styles.empty}`} aria-labelledby={titleId}>
      <h2 id={titleId}>No sources yet</h2>
      <p className={styles.muted}>Add a repository to make its code and docs available to your team.</p>
      <div className={styles.addForm}>{addForm}</div>
    </section>
  )
}

export function UnknownAnswer() {
  return (
    <div className={styles.card} role="status">
      <p>I couldn’t find this in the indexed sources.</p>
      <p className={styles.muted}>Try adding or re-indexing a source that covers your question.</p>
    </div>
  )
}
