# Model configuration

`TKA_MODELS=fake` selects deterministic offline models and needs no credentials.
The default mode is `real`: startup and model factories refuse a missing, blank,
or invalid `OPENROUTER_API_KEY`, naming the variable without printing its value.
Supply the key only through the process environment. It is never a settings
field, database value, or part of a public error.
Model validation runs before data-directory resolution, so a storage configuration
failure cannot hide a missing key, invalid mode, or blank model ID. Fake mode keeps
the existing unhealthy-but-serving behavior for storage setup failures.

| Environment variable | Real-mode default |
| --- | --- |
| `TKA_CHAT_MODEL` | `anthropic/claude-sonnet-5.5` |
| `TKA_EMBEDDING_MODEL` | `openai/text-embedding-3-small` |

Overrides are sent exactly as provided. Empty model IDs are rejected in real
mode. These defaults are the PRD's configurable placeholders; mocked checks do
not establish provider availability, cost, or final human approval of a model.

The synchronous `Embedder.embed(text)` posts one input to
`https://openrouter.ai/api/v1/embeddings`, requesting float encoding. It requires
one result with index zero and a nonempty finite numeric vector, preserving
coordinate order and the model's dimensions. It does not impose the fake model's
2048 dimensions on real embeddings. The background worker stores each returned
vector and the embedder's actual `model_id` with its chunk, so stored vectors keep
their model identity.

`Answerer.answer(question, chunks)` posts to `/api/v1/chat/completions`. The system
message requires answers grounded in numbered sources. The user message encodes
the question and ordered source records as JSON data, preserving text and marker
numbers even when the text contains delimiter-like instructions. Blank evidence
returns `I don't know.` without a provider call. Only completed, nonempty assistant
text is accepted. The shared [ask service](ask.md) retrieves relevant evidence and validates
citation markers; a prompt alone does not enforce citation correctness.

Each attempt uses a 30-second socket timeout and a remaining time budget between
connection, request, headers, and body reads. There are at most three attempts,
with delays of 0.5 and 1 second. Timeouts, connection failures, 429, and 5xx retry;
authentication errors (401/403), other HTTP errors, and malformed responses do
not. Redirects are never followed. Response bodies are limited to 4 MiB. These
bounds apply to network I/O; OS DNS resolution and a continuously trickling header
block are subject to the standard library's resolver/header handling, so this is
not a hard wall-clock SLA. Tests inject a 0.01-second timeout and mock transports;
there are no background timeout workers or live model calls.

Failures use `ModelError` subclasses for authentication, timeout, availability,
request rejection, and invalid response. Raw provider bodies and transport
exceptions are not logged or chained into these errors. Responses echoing the
API key are rejected, including JSON-escaped echoes and keys containing quotes or
backslashes. Raw provider request/response bodies are not persisted; validated
embedding vectors are stored in the index. Fake mode never imports the provider
or reads the key.

The HTTPS transport uses the Python standard library, so production does not
depend on the development-only HTTP test client or require a lockfile change.

Run `uv run python scripts/check.py static`, `uv run python scripts/check.py unit`,
and `python harness/ci.py` with the documented dedicated Postgres database.
Shared runtime and independent holdout gates remain separate.
