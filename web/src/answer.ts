// Derived from app.ask.AskResult; run uv run python -m scripts.citation_type.
import type { Citation } from './citation'

export interface KnownAnswer {
  known: true
  answer: string
  citations: readonly [Citation, ...Citation[]]
}

export interface UnknownAnswer {
  known: false
  answer: string
  citations: readonly []
}

export type AnswerResponse = KnownAnswer | UnknownAnswer
