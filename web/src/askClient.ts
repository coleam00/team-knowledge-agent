import type { AnswerResponse } from './answer'

export const FAILURE = 'Could not get an answer. Please try again.'

function isAnswer(value: unknown): value is AnswerResponse {
  if (typeof value !== 'object' || value === null) return false
  const body = value as { known?: unknown; answer?: unknown; citations?: unknown }
  return typeof body.known === 'boolean' && typeof body.answer === 'string' &&
    Array.isArray(body.citations) && (body.known ? body.citations.length > 0 : body.citations.length === 0)
}

export async function postAsk(question: string): Promise<AnswerResponse> {
  let response: Response
  try {
    response = await fetch('/api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }),
    })
  } catch {
    throw new Error(FAILURE)
  }
  const body: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = (body as { detail?: unknown } | null)?.detail
    throw new Error(typeof detail === 'string' && detail ? detail : FAILURE)
  }
  if (!isAnswer(body)) throw new Error(FAILURE)
  return body
}

