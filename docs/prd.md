# Team Knowledge Agent - PRD (first release)

**Repository:** `coleam00/team-knowledge-agent` (public, default branch `main`)
**Owner (Port):** team `factory_team` ("Factory Team")
**Status:** approved for build, 2026-10-20
**Built by:** the AI Software Factory, one increment at a time, against `MISSION.md`

---

## 1. Problem

A team's knowledge lives in its repositories: READMEs, `docs/`, ADRs, runbooks and the code itself. Finding the answer to "how do we rotate the API keys?" or "when are deploys frozen?" means knowing which repo to open, grepping, and reading. Generic chat assistants answer confidently from their own training data, cannot point at the line that proves the answer, and cannot tell you when the repo simply does not say.

People need answers they can **verify in one click**: an answer built only from the team's own files, with a citation to the exact file and line range at a known commit, and an honest "I don't know" when the files do not contain the answer.

## 2. Users

- **The engineer with a question.** Wants an answer about the team's code or docs in seconds, with a link to the lines that back it up, so they can trust it or read further.
- **The engineer in Claude Code.** Wants their coding agent to look things up in the team's indexed repos through MCP and get the same citations the web app shows.
- **The person who curates sources.** Adds a public GitHub repo by URL, sees what is indexed and when, and re-indexes after upstream changes.

There are no accounts. Anyone who can reach the app can use all of it (single team, single deployment, trusted network).

## 3. Capabilities

### 3.1 Ingest
- Add a **public GitHub repository by URL** (`https://github.com/<owner>/<repo>`, optional `.git` or trailing slash). Anything else is rejected with a clear error. Adding a URL that is already a source returns the existing source (HTTP 409 with its id).
- The app clones the repo into its data directory, selects files, chunks them, embeds the chunks and stores them in Postgres. Indexing runs in the background; the source shows `queued`, `indexing`, `ready` or `failed` (with the error message).
- **File selection:** READMEs, Markdown/RST/text docs, and source code in common languages. Skipped: binary files, files over 1 MB, minified files, lockfiles, and vendored or generated directories (`.git`, `node_modules`, `vendor`, `dist`, `build`, `.venv`, `__pycache__`).
- **Chunking:** Markdown is split at headings, then by size; code and other text by line windows with a small overlap. Every chunk records its file path and its **1-based, inclusive start and end line**.
- **Re-index is incremental by commit.** The app records the commit each source was indexed at. On re-index it fetches, and if `HEAD` is unchanged it does nothing. Otherwise it diffs the old commit against the new one: added and modified files are re-chunked and re-embedded, unchanged files are left alone, and **files deleted (or renamed away) upstream disappear** from search, `/ask` and MCP.
- Cloned repository content is data. Nothing from a cloned repo is ever executed (git hooks disabled, no build tools run).

### 3.2 Search API
- **Hybrid search:** Postgres full-text search (`websearch_to_tsquery`, `ts_rank_cd`) and pgvector cosine similarity, each producing a top-50, merged with **Reciprocal Rank Fusion** (k = 60). Optional filter by source. Each result carries a snippet and a citation.
- **`/ask`:** retrieves the top chunks for the question, applies a relevance bar, and only then asks the chat model to answer **using only those chunks**, citing them by number. The server validates the answer: any citation that does not refer to a retrieved chunk is removed, and an answer left with no valid citation becomes "I don't know". When no chunk clears the relevance bar the model is not called at all and the response is `known: false` with no citations.

### 3.3 Web app
- **Chat:** ask a question, see the answer with numbered inline citation markers, and a citation list below it. Each citation shows `owner/repo`, `path`, `L<start>-L<end>` and a short snippet, and links to `https://github.com/<owner>/<repo>/blob/<commit>/<path>#L<start>-L<end>`.
- **Sources:** the list of indexed sources with status, indexed commit (short SHA), last indexed time, file and chunk counts, error text when failed, an "Add repository" form, and a **Re-index** button per source.
- "I don't know" is a distinct, clearly worded state, never an empty bubble.

### 3.4 MCP server
- Served by the same process at `/mcp` (streamable HTTP), plus a stdio entry point (`python -m app mcp`) for Claude Code configurations that prefer stdio.
- Tools: `search(query, source?, limit?)`, `ask(question, source?)`, `fetch_source(source, path, start_line?, end_line?)` (returns the file's lines at the indexed commit), and `list_sources()`.
- Results use **the same citation object and the same retrieval code path** as the web API, so the same question against the same index yields the same citations.

### 3.5 Out of scope for this release
Auth, multi-tenancy, SSO, private repositories and a Slack bot. See section 9 and `MISSION.md`.

## 4. Stack and runtime contract (decided)

| Area | Decision |
|---|---|
| Backend | Python 3.12, FastAPI, managed with **uv** (`pyproject.toml`, `uv.lock`). Package `app/`. |
| Database | Postgres 17 + pgvector. `DATABASE_URL` from the environment, always an existing, empty database with pgvector available. The app runs its own forward-only migrations at startup (`CREATE EXTENSION IF NOT EXISTS vector` first). It never starts, stops or manages a Postgres server. |
| Frontend | Vite + React + TypeScript in `web/`. Built into `app/static/` (gitignored) by `scripts/build_web.py`, and served by the backend on the same port. |
| Models | Through **OpenRouter**: `OPENROUTER_API_KEY` (never committed), `TKA_CHAT_MODEL`, `TKA_EMBEDDING_MODEL`. |
| Fake mode | `TKA_MODELS=fake`: deterministic fake embeddings and a fake answerer that quotes retrieved chunks. Tests and factory runtime checks always use it and never call a network model. |
| Start | `python -m app serve --port <port>` (factory argv: `["{python}", "-m", "app", "serve", "--port", "{port}"]`; locally `uv run python -m app serve --port 8000`). |
| Health | `GET /api/health` returns 200 `{"status": "ok", "db": "ok"}` once migrations ran and the DB answers; 503 otherwise. |
| Build identity | `GET /api/build-id` returns 200 `text/plain`: exactly `FACTORY_RUNTIME_CANDIDATE` when set, otherwise `TKA_BUILD_ID` when set, otherwise the running Git commit (`git rev-parse HEAD`), otherwise `unknown`. |
| Persistent state | `DATABASE_URL` (all index data) and `TKA_DATA_DIR` (clones). `TKA_DATA_DIR` defaults to `FACTORY_RUNTIME_STATE` when set, else `./.tka-data` (gitignored). |
| Local sources | `TKA_ALLOW_LOCAL_SOURCES=1` additionally accepts an absolute path to a local Git repository. It exists for tests and factory runtime checks (no network). Citation links are built from the repo's `origin` remote, exactly as for cloned repos. Off by default. |
| Fixture contents | `README.md` (what the handbook is); `docs/deploy.md` (states that deploys are frozen on Fridays after 14:00 UTC); `docs/legacy-deploy.md` (an old deploy process with a distinctive sentence found nowhere else); `docs/onboarding.md`; `src/keys.py` (a `rotate_api_keys` function with a docstring explaining key rotation); `assets/logo.png` (binary, must be skipped); `node_modules/left-pad/index.js` (vendored, must be skipped). It does **not** mention parental leave. `harness/END-TO-END.md` depends on these facts. |
| Tests | No network. A small fixture repository ships as plain files in `tests/fixtures/handbook-repo/`; tests copy it to a temp dir, `git init`, commit, set `origin` to `https://github.com/tka-fixtures/handbook`, and then mutate and re-commit it to test incremental re-index. |
| Platforms | Built and run locally on Windows against a Docker Postgres. CI runs on Linux only (GitHub Actions, `ubuntu-latest`, `pgvector/pgvector:pg17` service container). Paths are built with `pathlib`; no Windows CI job. |

### 4.1 Data model (initial)
- `schema_migrations(version, applied_at)`
- `sources(id, url, owner, name, status, indexed_commit, indexed_at, error, file_count, chunk_count, created_at)`
- `files(id, source_id, path, blob_sha, language)` - unique `(source_id, path)`
- `chunks(id, source_id, file_id, path, start_line, end_line, content, tsv tsvector GENERATED, embedding vector, embedding_model)` - GIN index on `tsv`
- `jobs(id, source_id, kind, status, started_at, finished_at, error)`

The embedding column is untyped `vector` with `embedding_model` stored per chunk; queries only compare chunks embedded with the configured model. No ANN index in this release (exact scan is fine at release scale). Changing the embedding model marks sources as needing re-index.

### 4.2 HTTP API
| Method and path | Purpose |
|---|---|
| `POST /api/sources` `{url}` | Add a source; 202 with the source (`queued`); 409 if it exists; 422 if invalid |
| `GET /api/sources` / `GET /api/sources/{id}` | List / one source with status and counts |
| `POST /api/sources/{id}/reindex` | Queue an incremental re-index; 202; 409 if a job is already running |
| `GET /api/search?q=&source_id=&limit=` | Hybrid search results with citations |
| `POST /api/ask` `{question, source_ids?}` | `{known, answer, citations[]}` |
| `GET /api/sources/{id}/file?path=&start=&end=` | File lines at the indexed commit (used by `fetch_source`) |

**Citation object** (identical in web API and MCP): `{n, source_id, repo, path, start_line, end_line, commit, url, snippet}`.

### 4.3 Model defaults (assumptions, configurable)
- Chat: `TKA_CHAT_MODEL`, default `anthropic/claude-sonnet-5.5`. Embedding: `TKA_EMBEDDING_MODEL`, default `openai/text-embedding-3-small`.
- Fake embeddings are a **feature-hashed, L2-normalised bag of words** (256 dimensions, stable hash), so cosine similarity reflects word overlap and is fully deterministic.
- Relevance bar: a chunk is relevant if it matches the full-text query or its cosine similarity is at least `TKA_MIN_SIMILARITY` (default 0.25 fake, 0.35 real).

## 5. Design standard (applies before any UI work)

The UI is a reading tool. Every decision serves "can I read the answer and verify it fast".

- **Dark-first.** Default dark theme; light theme via `prefers-color-scheme: light`. All colours are CSS custom properties in `web/src/styles/tokens.css`; components never hard-code colours.
  - Dark: background `#0e1116`, surface `#161b22`, raised `#1f2630`, border `#2d3440`, text `#e6edf3`, muted text `#9aa4b2`, accent `#6cb6ff`, success `#3fb950`, warning `#d29922`, danger `#f85149`.
  - Light: background `#ffffff`, surface `#f6f8fa`, border `#d0d7de`, text `#1f2328`, muted `#59636e`, accent `#0969da`.
- **Contrast:** body text and citation text meet WCAG AA (4.5:1) in both themes. Visible focus ring (2px accent) on every interactive element. Full keyboard use: Enter sends, Shift+Enter is a newline.
- **Type:** system UI sans stack at 15px/1.6 for prose; `ui-monospace, "JetBrains Mono", Consolas, monospace` at 13px for paths, line ranges, SHAs and snippets. Answer column max width 72ch.
- **Citations are first-class:** numbered markers `[1]` inline in the answer, styled as small accent chips that scroll to and highlight the matching citation card. Each card: `owner/repo` (muted), `path` (mono, never truncated from the right; long paths truncate in the middle), `L12-L30`, a 3-6 line snippet with line numbers, and an "Open on GitHub" link that opens in a new tab.
- **States are explicit:** loading (skeleton, not a spinner over blank space), "I don't know" (muted card: "I couldn't find this in the indexed sources." plus a hint to add or re-index a source), error (danger-tinted card with the message and a retry), and empty sources ("No sources yet" with the add form).
- **Layout:** left nav with two items (Ask, Sources); content area; usable from 360px wide to desktop. No heavy UI framework: React + CSS modules + the tokens file. Icons inline SVG.
- **Status colours** for sources: `ready` success, `indexing`/`queued` accent with a subtle pulse, `failed` danger. Status is also written as text (never colour alone).

## 6. User journeys

1. **First answer.** Dana opens the app, goes to Sources, pastes `https://github.com/acme/handbook`, and sees it move from `queued` to `ready` with file and chunk counts. She asks "When are deploys frozen?" and gets a short answer with `[1]` pointing at `docs/deploy.md L14-L18`; clicking the link opens GitHub on those lines at the indexed commit.
2. **Honest gap.** Dana asks "What is our parental leave policy?" The handbook repo does not cover it. The app says it couldn't find this in the indexed sources and shows no citations.
3. **Upstream changed.** The team deletes `docs/legacy-deploy.md` and edits `docs/deploy.md`. Sam presses Re-index. The source's commit updates; searching for a phrase only in the deleted file returns nothing; asking about deploys cites the edited lines at the new commit.
4. **From Claude Code.** Sam adds the app's MCP endpoint to Claude Code. Claude calls `search` and `ask` and gets the same citations the web app shows, then calls `fetch_source` to read the surrounding lines before editing code.
5. **Bad input.** Someone pastes a GitLab URL, a GitHub URL to a single file, or a URL that is already indexed. Each gets a clear, specific message, and nothing is queued.
6. **Restart.** The app is restarted. Sources, statuses, chunks and answers are unchanged.

## 7. Build order

Each increment is one shippable PR that keeps `main` green (CI passing on `main` is a Gold rule, so a red `main` is a release blocker, not a nuisance). Every increment adds tests for what it adds.

| # | Increment | Ships | Done when |
|---|---|---|---|
| 1 | **Walking skeleton with CI** | `pyproject.toml` + `uv.lock`, package `app/` with `python -m app serve --port`, startup migrations runner (`CREATE EXTENSION vector`, `schema_migrations`), `GET /api/health`, `GET /api/build-id`, `TKA_DATA_DIR`, minimal Vite+React+TS page in `web/` built by `scripts/build_web.py` into `app/static/` and served at `/`, pytest + vitest smoke tests, ruff + mypy + eslint + tsc behind one entry point `scripts/check.py static|unit`, `.github/workflows/ci.yml` (`name: ci`, jobs `backend`, `frontend`, `smoke`), `.gitignore` additions (`.venv/`, `node_modules/`, `app/static/`, `.tka-data/`), README "Run it" section | A fresh clone with only `DATABASE_URL` set starts with the one command, health and build-id return 200, CI is green on the PR and on `main` |
| 2 | **Repo standards** | Root `CODEOWNERS`, `.github/pull_request_template.md`, README sections (what it is, run, test, env vars) | Port shows all Bronze and Silver rules passing after resync; repo at **Gold** once `main` CI is green |
| 3 | **Design system shell** | `tokens.css` (dark and light), app shell with left nav (Ask, Sources), routing, empty states, focus styles | Shell renders in both themes; component tests cover nav and states |
| 4 | **Model layer and fake mode** | `app/models/` with `Embedder` and `Answerer` interfaces, OpenRouter implementations, fake hashed embedder and quoting answerer, config validation (`TKA_MODELS=fake` needs no key; real mode refuses to start without `OPENROUTER_API_KEY`) | Unit tests prove determinism; no test opens a network socket (enforced by a socket-blocking fixture) |
| 5 | **Fixture repo and source registry** | `tests/fixtures/handbook-repo/`, git test helper, `sources` table, `POST/GET /api/sources`, URL validation, local sources behind `TKA_ALLOW_LOCAL_SOURCES` | Valid, invalid, duplicate and local cases tested |
| 6 | **Clone and file selection** | Clone into `TKA_DATA_DIR` with hooks disabled, file walker with the skip rules, `files` table | Fixture's binary, vendored and oversized files are skipped; selection tested |
| 7 | **Chunking** | Markdown heading-aware chunker and line-window code chunker with exact 1-based inclusive line ranges | Property test: for every chunk, the file's lines `start..end` joined equal the chunk content |
| 8 | **Embed, store, background jobs** | `chunks` and `jobs` tables, ingest pipeline, background worker, `queued/indexing/ready/failed` with error text, counts and indexed commit | Fixture indexes to `ready` with expected counts; a failing clone ends `failed` with a message |
| 9 | **Incremental re-index by commit** | `POST /api/sources/{id}/reindex`, `git diff --name-status` between commits, add/modify/delete/rename handling, no-op on unchanged commit, one job per source | Tests: deleted file's chunks gone, renamed path replaced, unchanged files' chunk ids stable, no-op re-index changes nothing |
| 10 | **Full-text search** | Generated `tsvector`, GIN index, FTS query function | Exact-phrase and multi-word queries rank the right fixture chunk first |
| 11 | **Hybrid search API** | Vector query, RRF merge, `GET /api/search` with source filter, citation object and GitHub URL builder | Search returns citations whose URL, path and lines match the fixture |
| 12 | **`/ask` with grounding** | Retrieval, relevance bar, prompt that allows only numbered chunks, citation validation, `known: false` path | Tests: answerable question cites the right file and lines; unanswerable returns `known: false` with no citations and no model call; a forged citation is stripped |
| 13 | **Sources page** | List with status, short SHA, counts, error, add form with validation messages, Re-index button with job state polling | Component tests plus an API-backed test against the fake backend |
| 14 | **Chat page** | Question box, answer with inline `[n]` chips, citation cards with snippet and GitHub link, loading, "I don't know" and error states | Component tests for every state; citation link format asserted |
| 15 | **MCP server** | `/mcp` streamable HTTP and `python -m app mcp` stdio, tools `search`, `ask`, `fetch_source`, `list_sources`, sharing the API's retrieval and citation code | Test calls each tool through an MCP client and asserts citations equal `/api/search` and `/api/ask` output for the same input |
| 16 | **Limits and resilience** | Max files per repo, clone timeout, model call timeouts and retries, clear errors for repo-not-found and network failures, concurrent re-index guard | Each limit and error path has a test; app stays healthy after a failed ingest |
| 17 | **Observability** | Structured JSON logs (request id, source id, job id, durations), no secrets or chunk content at info level, `/api/health` reports DB and migration version | Log redaction test proves `OPENROUTER_API_KEY` never appears |
| 18 | **End-to-end in CI** | `smoke` job runs the built app in fake mode against the service Postgres and walks the `harness/END-TO-END.md` journeys over HTTP and MCP | Smoke job green on `main` |
| 19 | **Release** | README quickstart (Docker Postgres on Windows, env vars, Claude Code MCP config), real-mode manual check documented, Port scorecard at Gold confirmed, `v0.1.0` tag | Every MISSION definition-of-done gate passes on the release commit; Port shows Gold |

## 8. Production Readiness (from Port)

Pulled from Port on 2026-10-20 (read-only). Scorecard `factory_production_readiness` ("Production Readiness") on blueprint `githubRepository`, levels Basic, Bronze, Silver, Gold. A repo holds a level only when it passes every rule at that level and below, so **Gold means all seven rules pass**.

**Owner:** Port team `factory_team`, "Factory Team" (parent `default-group`; "Owns the AI Software Factory and the North Star game it builds"). One member: Cole Medin, GitHub `coleam00`. No GitHub team is synced (the repo's `githubTeams` relation is empty; it lives on a personal account, which cannot have GitHub teams), so CODEOWNERS names `@coleam00` as the GitHub handle for Factory Team.

**Current state:** level **Bronze**. Passing 3 of 7.

**How Port reads this repo** (the `github-ocean` integration mapping): it pulls exactly these files from the default branch: `README.md`, `CODEOWNERS` (repo root), `.gitignore`, `.github/pull_request_template.md`, `.github/workflows/ci.yml`. A file at any other path, including `.github/CODEOWNERS`, `docs/CODEOWNERS`, `.github/PULL_REQUEST_TEMPLATE.md` or `ci.yaml`, does **not** count. `ci_main_status` is written by the existing `.github/workflows/port-ci-status.yml`, which fires when the workflow **named `ci`** completes on `main`, reports its conclusion to Port, and requests a Port resync (so the file-based rules refresh too). Repo secrets `PORT_CLIENT_ID` and `PORT_CLIENT_SECRET` are present (verified 2026-10-20).

| Level | Rule (`id`) | Port checks | Today | What satisfies it in this repo | Increment |
|---|---|---|---|---|---|
| Bronze | Has a README (`has_readme`) | `readme` not empty, from `README.md` | Pass | Root `README.md`, kept and expanded with what it is, how to run, test and configure | 1, 2, 19 |
| Bronze | Has an owning team (`has_owner`) | entity `$team` not empty | Pass (`factory_team`) | Owned in Port, no repo change. Must stay set; CODEOWNERS mirrors it | - |
| Bronze | Has a .gitignore (`has_gitignore`) | `gitignore` not empty, from `.gitignore` | Pass | Existing root `.gitignore`, extended with `.venv/`, `node_modules/`, `app/static/`, `web/dist/`, `.tka-data/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/` | 1 |
| Silver | Has a CI workflow (`has_ci`) | `ci_workflow` not empty, from `.github/workflows/ci.yml` | **Fail** | `.github/workflows/ci.yml` with `name: ci` (the name `port-ci-status.yml` listens for), triggers `pull_request` and `push` to `main`, jobs: **`backend`** (uv sync, `ruff check`, `ruff format --check`, `mypy app`, `pytest` against a `pgvector/pgvector:pg17` service with `TKA_MODELS=fake`), **`frontend`** (`npm ci`, `lint`, `typecheck`, `test`, `build` in `web/`), **`smoke`** (needs both; builds the web app, starts `python -m app serve --port 8000` in fake mode against the service DB, checks `/api/health` and `/api/build-id`, then walks the end-to-end journeys from increment 18 on) | 1 |
| Silver | Has CODEOWNERS (`has_codeowners`) | `codeowners` not empty, from root `CODEOWNERS` | **Fail** | Root `CODEOWNERS`: a comment naming the Port owner (`# Owner: Port team factory_team (Factory Team)`), then `* @coleam00`, plus explicit lines for `/MISSION.md`, `/FACTORY_RULES.md`, `/harness/`, `/.github/`, `/CODEOWNERS` | 2 |
| Silver | Has a PR template (`has_pr_template`) | `pr_template` not empty, from `.github/pull_request_template.md` | **Fail** | `.github/pull_request_template.md` with sections: Summary, Increment (PRD build-order number), What changed, How it was verified (commands and output), Checklist (tests added, fake mode only, no secrets, `MISSION.md` invariants respected, design standard followed for UI changes, CI green) | 2 |
| Gold | CI passing on main (`ci_passing_on_main`) | `ci_main_status` = `success` | **Fail** (no CI yet) | The latest `ci` run on `main` succeeds; `port-ci-status.yml` reports `success`. Kept true by merging only PRs whose `backend`, `frontend` and `smoke` checks pass, and treating any red `main` as stop-the-line | 1, then every increment |

**Gold path:** increment 1 lands `ci.yml` and the first green `main` run (Silver's `has_ci` and Gold's rule), increment 2 lands `CODEOWNERS` and the PR template. After the next `main` CI run triggers the Port resync, all seven rules pass. Because `ci_main_status` reflects only the **latest** run on `main`, Gold is lost the moment a `main` run fails or is cancelled; it is a property to hold through release, not a box ticked once.

**Owner actions outside the factory's reach** (these files are protected or live in GitHub settings, so a human makes them):
- Enable branch protection on `main` requiring the checks `backend`, `frontend` and `smoke` (currently not protected, verified 2026-10-20).
- Set `harness/harness.config.json`: `static` = `python scripts/check.py static`, `unit` = `python scripts/check.py unit` (the harness runs one argv without a shell, so increment 1 ships `scripts/check.py`, which runs ruff, mypy, eslint and tsc for `static` and pytest and vitest for `unit`), `required_checks` = `backend,frontend,smoke`; add `CODEOWNERS` and `.github/**` to `protected_paths` so the factory cannot weaken its own standards files.
- Done 2026-10-20: `harness/runtime.inputs.json` sets `http.start` to the start argv above, `health_path` to `/api/health`, and `TKA_MODELS=fake` and `TKA_ALLOW_LOCAL_SOURCES=1` in its http environment. The `harness/harness.config.json` commands and `required_checks` above are also set (as `uv run python scripts/check.py ...`).
- Update the mutation copy list in `harness/mutations/defects.json` to `app`, `web`, `tests`, `scripts`, `harness`, `.factory`, `pyproject.toml`, `uv.lock`, and write the defect set.

## 9. Non-goals

Not in this release, and (per `MISSION.md`) never for this product:
- Authentication, user accounts, roles, API keys per user, SSO.
- Multi-tenancy: organisations, workspaces, per-tenant isolation, billing.
- Private repositories, GitHub tokens, SSH keys, GitHub App installs.
- Chat surfaces other than the web app and MCP: Slack, Discord, Teams, email bots.
- Answering from model knowledge or the web; any uncited answer.
- Writing to indexed repos: opening PRs, editing files, running their code.
- Managing infrastructure: starting Postgres, Docker orchestration, cloud deploys.

Deferred, not forbidden (backlog candidates after release): removing a source, scheduled automatic re-index, non-default branches, non-GitHub public hosts, an ANN index, answer streaming, conversation history.

## 10. Assumptions made while writing this

- **CODEOWNERS uses `@coleam00`.** Port's owner is a Port team, and the repo is on a personal GitHub account with no teams, so the sole Factory Team member's handle stands in for the team. If the repo moves to an org, replace it with the org team.
- **CODEOWNERS lives at the repo root**, because that is the only path Port's mapping reads, even though GitHub also accepts `.github/CODEOWNERS`.
- **CI jobs are named `backend`, `frontend`, `smoke`** and these names are the required checks.
- **Backend package is `app/`**, matching the existing mutation scaffold's copy list.
- **Local-path sources exist** (behind `TKA_ALLOW_LOCAL_SOURCES=1`) because tests and factory runtime checks need no network, and the product is otherwise "public GitHub URL only".
- **The fixture ships as plain files** (a nested `.git` cannot be committed); tests build a real Git history from it.
- **Fake embeddings are hashed bag-of-words** so retrieval and the "I don't know" bar behave meaningfully in tests without a model.
- **Citations use the source's indexed commit.** Unchanged files are identical at that commit, so their links stay valid.
- **The MCP server ships an `ask` tool and a `list_sources` tool** in addition to `search` and `fetch_source`, because "Claude Code can ask it questions too" implies `ask`.
- **Model defaults** (`anthropic/claude-sonnet-5.5`, `openai/text-embedding-3-small`) are placeholders; both are environment-configurable and never used in tests.
- **Removing a source is deferred**, because deleting stored data is an irreversible action the factory must not decide on its own.
- **Exact-scan vector search** is enough for release scale (a few repos, tens of thousands of chunks).
