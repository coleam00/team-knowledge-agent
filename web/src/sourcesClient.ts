import { useCallback, useEffect, useState } from 'react'
import type { SourceSummary } from './components/Sources'

const FAILURE = 'Could not load sources. Please try again.'

async function detail(response: Response, fallback: string) {
  const body: unknown = await response.json().catch(() => null)
  const value = (body as { detail?: unknown } | null)?.detail
  if (typeof value === 'string' && value) return value
  if (Array.isArray(value) && value.length > 0) {
    const first = value[0] as { msg?: unknown }
    if (typeof first.msg === 'string') return first.msg
  }
  return fallback
}

export async function listSources(): Promise<SourceSummary[]> {
  const response = await fetch('/api/sources')
  if (!response.ok) throw new Error(await detail(response, FAILURE))
  const body: unknown = await response.json()
  if (!Array.isArray(body)) throw new Error(FAILURE)
  return body as SourceSummary[]
}

export async function addSource(url: string): Promise<SourceSummary> {
  const response = await fetch('/api/sources', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url }),
  })
  if (response.status === 409) throw new Error('This repository is already in the library.')
  if (!response.ok) throw new Error(await detail(response, 'Could not add this repository. Please try again.'))
  return await response.json() as SourceSummary
}

export function isBusy(source: SourceSummary) {
  return source.status === 'queued' || source.status === 'indexing'
}

/** Live source list: polls quickly while anything is indexing, slowly otherwise. */
export function useSources() {
  const [sources, setSources] = useState<SourceSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [tick, setTick] = useState(0)
  const refresh = useCallback(() => { setTick(value => value + 1) }, [])

  useEffect(() => {
    let cancelled = false
    let timer: number | undefined
    listSources().then(next => {
      if (cancelled) return
      setSources(next)
      setError(null)
      timer = window.setTimeout(refresh, next.some(isBusy) ? 2500 : 30000)
    }, (caught: unknown) => {
      if (cancelled) return
      setError(caught instanceof Error ? caught.message : FAILURE)
      timer = window.setTimeout(refresh, 30000)
    })
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [tick, refresh])

  const add = useCallback(async (url: string) => {
    await addSource(url)
    refresh()
  }, [refresh])

  return { sources, error, refresh, add }
}
