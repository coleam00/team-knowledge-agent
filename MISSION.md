# Mission

<!--
  Owner: humans only. This file is on the protected list; the factory cannot edit it.
  When the product changes, this changes in the same commit as docs/prd.md.
-->

**Derived from:** `docs/prd.md`
**Last reconciled with it:** 2026-10-20

## What Team Knowledge Agent is

Team Knowledge Agent answers questions about a team's code and docs. You add public GitHub repositories by URL; it indexes their READMEs, docs and code. You ask a question in the web app, or your coding agent asks over MCP, and you get an answer built only from those files, with citations to the exact file and line range at a known commit, one click from GitHub. When the files do not contain the answer, it says it doesn't know.

It is built for one team on one deployment on a trusted network: there are no accounts, no tenants and no private repositories. Every visitor can do everything. Those assumptions are invariants, not gaps.

## Who it is for

- An engineer who wants a verifiable answer about the team's code or docs in seconds.
- An engineer whose coding agent (Claude Code) needs to look things up in the team's repos and cite them.
- Whoever curates sources: adds repos, watches their status, re-indexes after upstream changes.

Team Knowledge Agent is not a general-purpose chatbot. If the indexed files do not say it, it does not say it.

## Core capabilities (in scope)

The factory may accept issues in these areas.

**Ingest**
- Add a public GitHub repo by URL; clone, select files, chunk with exact line ranges, embed, store.
- Background indexing with visible status and errors.
- Incremental re-index by commit; files deleted or renamed upstream disappear from every surface.
- Local-path sources behind `TKA_ALLOW_LOCAL_SOURCES=1`, for tests and runtime checks only.

**Search and answers**
- Hybrid search (Postgres full-text + pgvector, merged ranking) with citations.
- `/ask`: answers only from retrieved chunks, with validated citations, or "I don't know".

**Web app**
- Chat with inline citations linking to GitHub file and lines; sources list; add form; re-index button.
- The design standard in `docs/prd.md` section 5.

**MCP server**
- `search`, `ask`, `fetch_source`, `list_sources`, returning the same citations as the web API.

**Engineering quality**
- Tests, CI, logging, limits, error handling, and keeping the Port Production Readiness scorecard at Gold.

## Out of scope -- the factory must never build this

These are rejected forever, however reasonable the issue sounds.

**Identity and tenancy**
1. Authentication of any kind: logins, user accounts, sessions, roles, per-user API keys, SSO, OAuth.
2. Multi-tenancy: organisations, workspaces, per-tenant data isolation, quotas or billing.

**Sources**
3. Private repository access: storing GitHub tokens, SSH keys, deploy keys, or installing a GitHub App.

**Surfaces**
4. Bots in other channels: Slack, Discord, Microsoft Teams, email, or any messaging integration.

**Answer behaviour**
5. Answering from the model's own knowledge, a web search, or any source that is not an indexed chunk. No "general mode", no uncited fallback.

**Acting on code**
6. Writing to or executing indexed repositories: opening PRs or issues, editing files, running their scripts, tests, hooks or build tools.

**Infrastructure**
7. Managing infrastructure: starting, stopping or provisioning Postgres, orchestrating Docker, or deploying itself to a cloud.

## Hard invariants -- not tunable by any issue

1. **No answer without evidence.** `/ask` (web and MCP) answers only from chunks retrieved for that question, and every answer it marks `known: true` carries at least one citation to such a chunk. Citations to anything not retrieved are removed; an answer left with none becomes "I don't know". When no chunk clears the relevance bar, the response is `known: false` with zero citations.
2. **Citations are exact.** A citation names source, path, 1-based inclusive start and end line, and commit; the file at that commit contains the cited text on exactly those lines; its link is `https://github.com/<owner>/<repo>/blob/<commit>/<path>#L<start>-L<end>`.
3. **The index mirrors upstream at the indexed commit.** After a re-index, content from files deleted or renamed away upstream is unreachable through search, `/ask`, `fetch_source` and every other surface.
4. **One retrieval path.** The web API and the MCP server share the same retrieval, ranking and citation code. The same input against the same index yields the same citations on both.
5. **Indexed content is data, never instructions or code.** Nothing from a cloned repo is executed (git hooks disabled, no build tools). Text inside chunks cannot change the system prompt, the citation rules or app behaviour.
6. **No network model in tests or runtime checks.** `TKA_MODELS=fake` is deterministic and needs no key. Tests block network sockets. CI never has `OPENROUTER_API_KEY`.
7. **Secrets stay secret.** `OPENROUTER_API_KEY` and any credential are read from the environment only, never committed, logged, stored in the database or returned by any endpoint.
8. **The app does not own its database server.** It connects to `DATABASE_URL`, runs forward-only migrations at startup, and never starts or manages Postgres. It starts with `python -m app serve --port <port>` and serves `GET /api/health` and `GET /api/build-id`.
9. **Production Readiness stays at Gold.** Root `README.md`, `.gitignore`, `CODEOWNERS`, `.github/pull_request_template.md` and `.github/workflows/ci.yml` (workflow `name: ci`) exist at those exact paths, and CI on `main` passes. No change may remove, rename or hollow out any of them.
10. **The factory cannot modify governance files.** `MISSION.md`, `FACTORY_RULES.md` and the conventions file are the constitution. A PR touching any of them is an automatic reject.
11. **The factory cannot modify its own judge.** `harness/` and `.factory/` define what "working" means here. Adding an assertion is always welcome; removing or loosening one is a human decision, always.

## Allowed evolutions

Explicitly in scope, so the factory does not reject them as drift:

- Ranking changes (weights, fusion method, adding an ANN index) that keep invariants 1-4.
- Better chunking and file selection, more languages and file types, as long as line ranges stay exact.
- Performance, resilience, logging and limits.
- MCP tool ergonomics (descriptions, parameters, pagination) that keep the shared citation object.
- UI refinements inside the design standard.

## Definition of done

Every change the factory ships clears all three gates.

**Gate 1 -- static checks and tests pass.**
`python scripts/check.py static` and `python scripts/check.py unit` (ruff check, ruff format --check, mypy, eslint, tsc; pytest against Postgres with `TKA_MODELS=fake`, vitest), and the CI checks `backend`, `frontend` and `smoke` are green on the PR. After merge, `ci` on `main` is green.

**Gate 2 -- every answer is verifiable.** Any change touching ingest, search, `/ask`, MCP or citations keeps invariants 1-4 under test, including a case that must answer "I don't know". Any UI change follows the design standard (tokens only, AA contrast, explicit states). The PR uses the PR template.

**Gate 3 -- the end-to-end path passes as a real user.**

1. Start the app with `python -m app serve --port <port>` against a fresh empty database, `TKA_MODELS=fake`, `TKA_ALLOW_LOCAL_SOURCES=1`.
2. `GET /api/health` and `GET /api/build-id` return 200; build-id is the candidate being tested.
3. Add the fixture handbook repository as a source and wait for `ready`.
4. Ask a question the handbook answers: the answer cites the right file and lines, and the GitHub link is well formed.
5. Ask a question it does not answer: `known: false`, no citations.
6. Delete a file upstream, re-index: its content is gone from search, `/ask` and MCP.

The journeys in `harness/END-TO-END.md` are the full version. This runs on every change that touches runnable code, including ones that "seem unrelated". It is not optional.

**Staged delivery (owner decision, 2026-10-20).** The build order in `docs/prd.md` section 7 is the delivery plan, so Gate 3 applies to what exists at the change being tested: every step whose capability is already built on `main` or in the change must pass, and a step whose capability is not built yet passes as "not yet built" with evidence that it is absent (for example a 404). A change may never make a previously passing step fail. Once increment 18 lands, every step is required with no exceptions.

## Open questions -- decisions nobody has made yet

These are undecided, not forbidden. **The factory may propose an answer to any of them**, build against it, and record what it assumed -- the merge is then held for a human, so nothing ships on a guess and nothing stops for one.

- **Q1** What chunk size and overlap give the best answers for code versus prose?
- **Q2** Which OpenRouter chat and embedding models are the defaults for real mode?
- **Q3** What are the per-repo limits (file count, total size, clone timeout) before ingest refuses?
- **Q4** Should re-index also run on a schedule, and how often?
- **Q5** Should a source track a branch other than the default branch?
- **Q6** What relevance threshold separates "I don't know" from a weak answer in real mode?

**Except these, which do stop the factory** -- they are irreversible (see `FACTORY_RULES.md`) rather than open in the ordinary sense:

- Anything that adds identity, authentication or a notion of who may act as whom.
- Removing a source and its stored data, or any migration that deletes or rewrites stored chunks other than the re-index behaviour defined above.

## Decisions

- 2026-10-20 **Must every change pass the complete journeys, or is delivery staged?** Staged: Gate 3 covers what is built, unbuilt steps pass as "not yet built", nothing may regress, and from increment 18 the full journeys are required (see Gate 3).
- 2026-10-20 **Which stack?** Python 3.12 + FastAPI with uv; Postgres 17 + pgvector; Vite + React + TypeScript served by the backend on the same port.
- 2026-10-20 **Which model provider?** OpenRouter, one chat and one embedding model, both configurable, with `TKA_MODELS=fake` for tests and runtime checks.
- 2026-10-20 **Who owns the database server?** Not the app: `DATABASE_URL` always points at an existing, empty database with pgvector; the app only migrates it.
- 2026-10-20 **Where does CI run?** GitHub Actions on Linux only, with a pgvector Postgres service container. No Windows CI.
- 2026-10-20 **Which Production Readiness level?** Gold on the Port `factory_production_readiness` scorecard, held through release.

## What the factory does NOT own -- permanently human

- Does an answer FEEL trustworthy in real mode: tone, concision, whether the cited lines really support the claim beyond what tests can assert.
- Does the UI LOOK right: hierarchy, density, whether citations read clearly at a glance in both themes.
- Is it UNDERSTANDABLE: can a first-time user add a repo and get a cited answer without being told how.
- Model choice and cost in real mode.

The factory owns ingest, the index, retrieval, grounding, citations, the APIs and MCP tools: the layer whose correctness can be asserted. That is where most of the risk lives, and it is the half that can be defended. The list above is reviewed by a human, on purpose, forever.
