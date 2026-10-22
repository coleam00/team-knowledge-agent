// Verification entry only: production components with typed API-shaped fixtures.
import { createRoot } from 'react-dom/client'
import { Answer, type AnswerResponse } from '../src/components/Answer'
import { citedAnswer, deployCitation, literalText } from './citations-fixtures'
import styles from '../src/App.module.css'
import '../src/styles/tokens.css'

const state = window.location.hash.slice(1)
const response: AnswerResponse = state === 'unknown' ? { known: false, answer: '', citations: [] }
  : state === 'literal' ? {
    known: true,
    answer: `${literalText}\nLiteral source text [1]; no citation for [99].`,
    citations: [{ ...deployCitation, snippet: literalText }],
  } : citedAnswer

createRoot(document.getElementById('root')!).render(
  <main className={styles.main}>
    <div className={styles.answerColumn}>
      <header className={styles.pageHeader}>
        <h1>When are deploys frozen?</h1>
        <p>Answers with sources you can check.</p>
      </header>
      <Answer response={response} />
    </div>
  </main>,
)
