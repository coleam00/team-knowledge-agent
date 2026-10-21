# Hybrid search

`GET /api/search?q=deploy&source_id=<UUID>&limit=2` returns a flat array of
citations. `q` is required and must contain non-whitespace text. `source_id` is
optional; an unknown UUID returns `[]`. `limit` defaults to 10 and accepts positive
decimal integers (including leading zeroes). Missing/blank queries, invalid UUIDs,
and nonpositive or noninteger limits return 422. No matches returns `[]`.

Each citation contains exactly these fields:

`app.retrieval.Citation` owns this wire contract. The browser's
`web/src/citation.ts` is derived from its serialization schema. After changing
the schema, run `uv run python -m scripts.citation_type`; ordinary static checks
reject a stale generated type before checking the browser's consumers.

```json
{
  "n": 1,
  "source_id": "00000000-0000-0000-0000-000000000001",
  "repo": "acme/handbook",
  "path": "docs/deploy.md",
  "start_line": 14,
  "end_line": 18,
  "commit": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "url": "https://github.com/acme/handbook/blob/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/docs/deploy.md#L14-L18",
  "snippet": "The exact stored chunk text, including whitespace and line endings."
}
```

Numbers start at 1 after limiting. Lines are 1-based and inclusive. URLs encode
spaces, Unicode and reserved characters while preserving path separators. The
snippet is the complete stored content, with no highlighting or truncation.

`app.retrieval.search(settings, query, source_id=None, limit=10)` is the shared
service for HTTP and future MCP/answer consumers. It strips outer query whitespace
and embeds once with the active model before opening a database connection.

Both branches filter by source and require source metadata with a full 40- or
64-character hexadecimal indexed commit. Unindexed sources, symbolic refs and
orphan chunks are omitted. Source status is not a filter: a published snapshot
remains usable during a queued retry.

The lexical branch uses English `websearch_to_tsquery` and `ts_rank_cd`. The vector
branch uses exact pgvector cosine similarity, comparing only active-model vectors
with matching dimensions and nonzero norms. Lexical matches remain eligible
regardless of vector compatibility or similarity. Zero query vectors contribute
no vector candidates. Empty or nonfinite model output is an availability failure.

Each branch returns its top 50 ordered by descending score, then ascending chunk
UUID. Ranks start at 1. Candidates are deduplicated by chunk UUID and fused using
`sum(1 / (60 + rank))`. Final ties use ascending UUID. At most 100 distinct chunks
can be returned, even with a larger limit. No ANN index is used.

`TKA_MIN_SIMILARITY` overrides the inclusive cosine threshold: 0.25 in fake mode,
0.35 in real mode by default. Overrides must be finite numbers in [-1, 1]; empty
or invalid values fail startup without reflecting the value. Direct `Settings`
construction supports `min_similarity` with the same numeric validation.

Ranking, text and citation metadata come from one SQL statement snapshot. Writers
must publish changed chunks, deleted files and the source's indexed commit in one
transaction. Retrieval cannot repair inconsistent data already committed by a
writer. It does not read working files or contact GitHub to construct citations.

Database and expected model failures return a generic 503, without query text,
credentials or provider error details. A provider failure does not silently fall
back to lexical results. Exact scans use the database's existing five-second
statement timeout.

Background indexing, answers and MCP remain separate increments. Persisted-row
tests and smoke setup exercise retrieval without claiming automatic ingestion.
