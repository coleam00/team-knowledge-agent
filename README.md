# Team Knowledge Agent

Ask questions about your team's code and docs and get answers with citations. Built from scratch by an AI Software Factory, with Port keeping it to standard.

## Run it

The first increment provides the application page, database migrations, health,
and build identity. Sources, indexing, search, answers, and MCP are not built yet.
No models are called, including in `TKA_MODELS=fake` mode.

Install Python 3.12, [uv](https://docs.astral.sh/uv/), Node.js 22 LTS (22.13 or
newer), and Git. Run these commands from the checkout root:

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
| `TKA_DATA_DIR` | Data directory. A nonempty value takes precedence over `FACTORY_RUNTIME_STATE`, then `./.tka-data`. Relative paths use the working directory; `~` is expanded. Startup creates the directory; cloning is not built. |
| `FACTORY_RUNTIME_STATE` | Data-directory fallback when `TKA_DATA_DIR` is unset or empty. An unset or empty fallback uses `./.tka-data`. |
| `FACTORY_RUNTIME_CANDIDATE` | Exact build identity when set, including an empty value; otherwise the Git commit, then `unknown`, as described above. There is no `TKA_BUILD_ID` override. |

The following settings belong to later increments in [the runtime contract](docs/prd.md#4-stack-and-runtime-contract-decided):

| Variable | Planned use and availability |
|---|---|
| `TKA_MODELS=fake` | Deterministic fake embeddings and answers for tests and runtime checks. The check runner already sets it; the current app has no model layer and calls no models in any mode. |
| `OPENROUTER_API_KEY` | Environment-only credential for future real-mode model calls. Not read by the current app; never supply it to CI or runtime checks. |
| `TKA_CHAT_MODEL`, `TKA_EMBEDDING_MODEL` | Future OpenRouter model selection; not read by the current app. |
| `TKA_ALLOW_LOCAL_SOURCES=1` | Future opt-in for local Git sources in tests and runtime checks, off by default. Source ingestion is not built, so this currently enables no source API. |

## Checks

With the locked dependencies installed and `DATABASE_URL` pointing to a dedicated
test database:

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
restart persistence, absent future APIs, and missing-database health. It creates
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
