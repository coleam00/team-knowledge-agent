// Derived from app.retrieval.Citation; run uv run python -m scripts.citation_type.
export interface Citation {
  n: number
  source_id: string
  repo: string
  path: string
  start_line: number
  end_line: number
  commit: string
  url: string
  snippet: string
}
