# Grounded answers

`POST /api/ask` accepts `{"question":"When are deploys frozen?"}` and returns
`{known, answer, citations}`. Optional `source_ids` is an array of source UUIDs:
omitting it (or sending null) searches all sources; an empty array selects none.
Unknown UUIDs match no sources. Invalid UUIDs or blank questions return 422.
Database or model failures return a sanitized 503, not an unknown answer.

The shared `app.ask.ask(settings, question, source_ids=None)` service uses the
same retrieval and citation path as search. Source selection applies before
each branch's top-50 limit; RRF selects up to ten chunks. A chunk qualifies by
full-text match OR cosine similarity at least `TKA_MIN_SIMILARITY` (0.25 fake,
0.35 real by default). Only matching model IDs and dimensions are compared as
vectors; lexical evidence remains independently eligible.

With no relevant chunks, the service returns `known: false`, an explicit
"I don't know. I couldn't find this in the indexed sources.", and no citations,
without creating or calling an answerer. Otherwise the answerer receives only
the question and numbered retrieved text. OpenRouter keeps its grounding rules
in a system message and source text in a JSON data object, never interpolating
source instructions into the system prompt.

The server validates numeric `[n]` markers against retrieved chunks, removes
unrecognized markers and model-authored reference URLs, and returns only cited evidence using the original
numbers. If none remain, it returns the same unknown response. Citation metadata
and commit-pinned GitHub URLs come from retrieval, never from the model. This
checks citation membership; real-mode semantic answer quality still needs human
evaluation. Fake mode deterministically quotes the supplied evidence.

The v2 fake embedder uses 2048 SHA-256 word-count buckets and a fixed English
stop-word list, normalized to unit length; its ID is
`fake-bow-sha256-2048-v2`. It is deterministic and offline. This supersedes the
PRD's original 256-dimension assumption under the owner decision on issue #13.
Old `fake-bow-sha256-256-v1` vectors are excluded from vector matching until re-indexed; lexical matches remain eligible, and no data migration is performed.

Tests cover the handbook freeze answer and exact lines, the parental-leave gap
with zero answerer calls, source selection before limits, similarity boundaries,
forged citations, and hostile source text. Feature smoke checks exercise actual
background ingestion, HTTP answers and persistence across restart. Chat UI,
re-index and MCP remain separate increments.
