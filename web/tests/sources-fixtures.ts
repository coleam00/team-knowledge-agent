import type { SourceSummary } from '../src/components/Sources'

export const sources: SourceSummary[] = (['queued', 'indexing', 'ready', 'failed'] as const).map(status => ({
  id: `source-${status}`,
  owner: 'team',
  name: status === 'ready' ? 'handbook' : `handbook-${status}`,
  status,
  indexed_commit: status === 'ready' ? '0123456789abcdef' : null,
  indexed_at: status === 'ready' ? '2026-10-20T14:00:00Z' : null,
  file_count: status === 'ready' ? 5 : 0,
  chunk_count: status === 'ready' ? 9 : 0,
  error: status === 'failed' ? 'The repository could not be reached. Check its URL and try re-indexing.' : null,
}))
