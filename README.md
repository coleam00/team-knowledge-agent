# Team Knowledge Agent

Ask questions about your team's code and docs and get answers with citations. Built from scratch by an AI Software Factory, with Port keeping it to standard.

## Run it

The app provides the application page, database migrations, health, build
identity, a persistent source registry, and [hybrid search](docs/search.md) with
commit-pinned citations over stored chunks. Accepted sources are indexed in the
background and become ready or failed with an error. Answers are not built yet.
[MCP transports](docs/mcp.md) support HTTP and stdio with no production tools yet.
The model library provides deterministic fake embeddings and a quoting answerer.
Search embeds each query with the active model. The [acquisition library](docs/file-selection.md)
clones and selects commit-pinned text for the background worker.

The pure [chunking library](docs/chunking.md) splits Markdown at headings and
other text into overlapping line windows, preserving original text and exact
inclusive line ranges. The background worker uses it before embedding and storage.

The model interfaces also provide configurable OpenRouter adapters. Real mode
(the default) requires `OPENROUTER_API_KEY` at startup; use `TKA_MODELS=fake` for
offline development and checks. See [model configuration](docs/model-configuration.md)
for model overrides, timeout/retry limits, and error behavior.

Install Python 3.12, [uv](https://docs.astral.sh/uv/), Node.js 22 LTS (22.13 or
newer), and Git 2.47 or newer. Run these commands from the checkout root:

```text
uv sync --frozen
npm ci --prefix web
uv run python scripts/build_web.py
```

Supply an existing, dedicated Postgres 17 database with pgvector available. Its
role must be able to create the vector extension in `public` and create tables in
its default schema. The app never provisions or starts Postgres. Set the database
URL through your environment; do not store credentials in the repository.

PowerShell:

```powershell
$env:DATABASE_URL = 'postgresql://USER:PASSWORD@HOST:5432/DATABASE'
$env:TKA_MODELS = 'fake'
uv run python -m app serve --port 8000
```

POSIX shell:

```sh
export DATABASE_URL='postgresql://USER:PASSWORD@HOST:5432/DATABASE'
export TKA_MODELS=fake
uv run python -m app serve --port 8000
```

Open <http://127.0.0.1:8000>. FastAPI serves the built frontend from `app/static/`
on the same port. Re-run the build after changing the frontend. Without a build,
`/` returns 503 with build instructions.

`GET /api/health` returns 200 with `{"status":"ok","db":"ok"}` after startup
migrations commit and the database answers a fresh probe. While initialization
is pending, or if the data directory, database, or migrations fail, it returns
503 with `{"status":"unavailable","db":"unavailable"}`. Fix a startup failure
and restart; initialization does not retry. A later database outage recovers on
the next successful health probe. The page and build identity stay available.

Startup enables `vector` before applying forward SQL migrations, under a
transaction-scoped advisory lock. `schema_migrations` records each version once;
restarting preserves versions and timestamps. Failed migrations roll back, and
an older app refuses readiness when the database contains unknown versions.

`GET /api/build-id` returns plain text: the exact `FACTORY_RUNTIME_CANDIDATE`
value when set (even if empty), otherwise this checkout's Git commit, otherwise
`unknown`. `TKA_BUILD_ID` is not used. Identity is fixed at application creation.

## Environment variables

Set configuration in the process environment before starting the app. Keep
credentials out of source files and command output.

| Variable | Current behavior |
|---|---|
| `DATABASE_URL` | Connection URL for the existing Postgres database. Required for readiness; unset or empty leaves health unavailable. Startup runs migrations, but never starts a database server. |
| `TKA_DATA_DIR` | Data directory. A nonempty value takes precedence over `FACTORY_RUNTIME_STATE`, then `./.tka-data`. Relative paths use the working directory; `~` is expanded. Startup creates the directory; the [acquisition library](docs/file-selection.md) stores source-specific clones beneath `repos/`. |
| `FACTORY_RUNTIME_STATE` | Data-directory fallback when `TKA_DATA_DIR` is unset or empty. An unset or empty fallback uses `./.tka-data`. |
| `FACTORY_RUNTIME_CANDIDATE` | Exact build identity when set, including an empty value; otherwise the Git commit, then `unknown`, as described above. There is no `TKA_BUILD_ID` override. |
| `TKA_MODELS` | Model mode, default `real`, using OpenRouter adapters. Set `fake` for deterministic offline embeddings and a quoting answerer; no key is needed. Search embeds queries with this mode. Startup and model factories reject unknown modes. Tests and runtime checks use `fake`. |
| `OPENROUTER_API_KEY` | Environment-only credential required at real-mode startup and for model calls. A missing, blank, or invalid key causes `ModelConfigurationError` naming the variable without revealing its value. Fake mode does not read it; never supply it to CI or runtime checks. |
| `TKA_CHAT_MODEL` | OpenRouter chat model, default `anthropic/claude-sonnet-5.5`. Overrides are sent exactly as provided; blank IDs are rejected at real-mode startup. |
| `TKA_EMBEDDING_MODEL` | OpenRouter embedding model, default `openai/text-embedding-3-small`. Overrides are sent exactly as provided; blank IDs are rejected at real-mode startup. |
| `TKA_MIN_SIMILARITY` | Inclusive search cosine threshold, default 0.25 in fake mode and 0.35 in real mode. Overrides must be finite in [-1, 1]. Full-text matches remain eligible independently. |
| `TKA_ALLOW_LOCAL_SOURCES` | Exactly `1` enables absolute local Git repository roots for offline tests and runtime checks. Off by default. The repository must have a public GitHub HTTPS `origin` for owner/name metadata. |

See [model configuration](docs/model-configuration.md) for validation, timeout/retry
limits, and error behavior.

## Sources API

`POST /api/sources` with `{"url":"https://github.com/acme/handbook"}` returns
202 and the persisted source: UUID `id`, normalized `url`, `owner`, `name`,
`status: "queued"`, null `indexed_commit`, `indexed_at` and `error`, zero
`file_count` and `chunk_count`, and `created_at`. `GET /api/sources` returns an
array of sources in creation order; `GET /api/sources/{id}` returns one source
or 404 for an unknown UUID. Registration also creates one durable queued job;
the worker advances the source through indexing to ready, or failed with an error.
Ready sources expose the complete indexed commit, file count and chunk count.

`GET /api/sources/{id}/file?path=docs/deploy.md&start=14&end=18` returns
exact inclusive lines at the persisted `indexed_commit`, with the commit, path,
actual line bounds and total line count. Omit the range for the whole file.
Reads use eligible cached Git blobs and never fetch upstream or read working
files. Missing indexed content returns 404; unsafe paths or invalid ranges return
422. Indexed content becomes available after the background job reaches ready.
See the [source file contract](docs/source-files.md) for ranges, eligibility,
storage safety, concurrency and the shared service used by future adapters.

GitHub URLs are case insensitive; an optional `.git` suffix and trailing slash
normalize to the same URL. Duplicates return 409 with `detail` and the existing
`id`, including simultaneous submissions. Invalid inputs return 422 with a reason.
Only repository HTTPS URLs on github.com are accepted: no credentials, query,
fragment, SSH, or blob/tree/file paths. Registration makes no GitHub request and
does not establish repository existence or public visibility; background acquisition
uses anonymous access. No credentials are stored or used.

With local sources enabled, pass an absolute Git working-tree root or bare
repository path in `url`. The stored URL is the resolved local path, while
owner/name come from its local `origin` configuration. Relative paths, non-Git
directories and network paths are rejected. Registration only reads Git metadata;
it never runs repository code, hooks, filters, or credential helpers.

## Chunk storage and lexical retrieval

`app.index_store.replace_file(connection, source_id=..., path=..., blob_sha=...,
language=..., chunks=...)` stores a sequence of validated `ChunkInput` records.
Each record carries its exact content, 1-based inclusive line range, embedding,
and embedding model ID. The function atomically updates file metadata and replaces
that file's chunks, retaining the file ID. An empty sequence clears its chunks.
Concurrent replacements of the same source/path serialize; failures restore the
previous metadata and chunks. A caller's existing transaction can roll back the
whole operation. Other files are unaffected.

`full_text_search(connection, query, source_id=None)` returns at most 50 typed
matches with chunk/file/source IDs, path, lines, content, embedding/model and rank.
It uses PostgreSQL's English `websearch_to_tsquery` and `ts_rank_cd`, sorting by
descending rank then ascending chunk UUID. Quotes, OR and exclusions use PostgreSQL
web-search syntax. The optional source UUID restricts matches before ranking and
limiting. Empty, stopword-only and unrelated queries return no matches.

The forward migration adds generated English `tsv` with a GIN index and an
untyped pgvector column; different models may store different vector dimensions.
Embeddings use pgvector's float32 precision and retain their model identity.
Model IDs are opaque and stored unchanged. SQL and Python reject empty IDs and
IDs containing only ASCII spaces (U+0020); other characters, including tabs and
Unicode whitespace, remain significant, preserving the existing SQL domain.
The low-level lexical API does not compare embeddings or call a model. Public
[hybrid search](docs/search.md) combines it with compatible exact cosine candidates
and returns commit-pinned evidence. See [background ingestion](docs/background-ingest.md) for jobs, limits and deployment.

## Checks

With the locked dependencies installed and `DATABASE_URL` (or the test-only
fallback `TKA_TEST_DATABASE_URL`) pointing to a dedicated test database:

```text
uv run python scripts/check.py static
uv run python scripts/check.py unit
python harness/ci.py
```

Static checks run Ruff lint/format, strict mypy, ESLint, and TypeScript. Unit
checks run pytest and Vitest with fake models and without an OpenRouter key.
Python tests block network sockets except event-loop IPC; explicitly marked
database tests use libpq to reach the supplied Postgres. Those tests create and
remove only their own unique schemas; the vector extension remains in `public`.
Missing database configuration fails the integration tests instead of skipping.

For the live smoke check, build the web app and supply a **fresh empty** database
and `FACTORY_RUNTIME_CANDIDATE` equal to the checkout's `git rev-parse HEAD`:

```text
uv run python -m scripts.smoke
```

Smoke checks real HTTP responses and assets, the extension and migration ledger,
source registration, seeded hybrid citations, commit-pinned file reads across
edits/deletions/renames, restart persistence for both APIs, absent future APIs, and
missing-database health. It creates
and cleans its own temporary data directory and app processes. It leaves the
supplied database migrated. `--output PATH` also saves the observations as JSON.

GitHub Actions workflow `ci` has Linux `backend`, `frontend`, and `smoke` jobs;
database jobs use `pgvector/pgvector:pg17`. Shared runtime and independent holdout
verification remain separate from these ordinary checks.

## Repository standards and Port reporting

Root `CODEOWNERS` mirrors Port owner `factory_team` (Factory Team) through
`@coleam00`. The [pull request template](.github/pull_request_template.md) requests
command results and evidence paths in the PR text.

Port's GitHub integration reads `README.md`, `.gitignore`, `CODEOWNERS`,
`.github/pull_request_template.md`, and `.github/workflows/ci.yml` from the default
branch at those exact paths. Keep the workflow name `ci` and its `backend`,
`frontend`, and `smoke` check names.

The existing [`port-ci-status`](.github/workflows/port-ci-status.yml) workflow runs
when `ci` completes on `main`. Using repository secrets `PORT_CLIENT_ID` and
`PORT_CLIENT_SECRET`, it writes the conclusion to the repository's `ci_main_status`
property in Port, then requests a `github-ocean` resync to refresh file and PR
inputs. A resync request failure emits a warning after the status was reported;
Port also resyncs on its own schedule. These secrets configure the reporter, not
the app.

These files provide repository-side readiness prerequisites. A successful report
or resync request alone does not establish an observed Gold score; that requires
Port's refreshed scorecard results.
