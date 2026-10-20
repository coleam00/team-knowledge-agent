import styles from './App.module.css'

export default function App() {
  return (
    <main className={styles.page}>
      <h1>Team Knowledge Agent</h1>
      <p className={styles.description}>
        A home for answers grounded in your team’s code and docs.
      </p>
      <p>
        This first release establishes the application foundation. Adding
        repositories, searching, and asking questions are coming in later releases.
      </p>
    </main>
  )
}
