# Runtime scenarios

Observable journeys for Team Knowledge Agent. Shared runtime verification owns execution and evidence.

Every journey starts the app with `python -m app serve --port <port>` against a fresh, empty database, with `TKA_MODELS=fake` and `TKA_ALLOW_LOCAL_SOURCES=1`, and no network. "The handbook" means a copy of `tests/fixtures/handbook-repo/` placed in a temporary directory, turned into a Git repository with one commit, and given the remote `origin` = `https://github.com/tka-fixtures/handbook`. Journeys talk to the app over HTTP (`/api/...`) and, where stated, over MCP (`/mcp`).

## A teammate indexes the handbook and gets a cited answer

1. Check `GET /api/health` returns 200 and `GET /api/build-id` returns the candidate being tested.
2. Add the handbook as a source.
3. Watch the source list until the handbook shows `ready`, with a non-zero file count and chunk count and the commit you created.
4. Ask "When are deploys frozen?".
5. The answer is marked known, quotes text from `docs/deploy.md`, and has at least one citation to `docs/deploy.md`.
6. Open that citation's lines in the handbook: the cited sentence about the deploy freeze is on those exact lines.
7. The citation link is `https://github.com/tka-fixtures/handbook/blob/<the commit>/docs/deploy.md#L<start>-L<end>`, with the same numbers as the citation.

**What would make this fail:** the source never reaches `ready`; the answer has no citation; a citation points at a different file or at lines that do not contain the quoted text; the link uses a branch name instead of the commit, or different line numbers.

## A question the handbook cannot answer gets "I don't know"

1. With the handbook indexed, ask "What is the parental leave policy?" (the handbook says nothing about it).
2. The answer is marked not known and has zero citations.
3. Open the web app at `/`, ask the same question, and see the "couldn't find this in the indexed sources" state, with no citation cards.

**What would make this fail:** any citation at all, an answer marked known, a confident made-up policy, or an empty or broken chat bubble.

## Upstream changes are picked up, and deleted files disappear

1. With the handbook indexed, note a sentence that exists only in `docs/legacy-deploy.md`, and search for it: it is found.
2. In the handbook repo, delete `docs/legacy-deploy.md`, change the freeze time in `docs/deploy.md`, and commit.
3. Press re-index for the handbook and wait for `ready`.
4. The source now shows the new commit.
5. Search for the legacy sentence again: nothing comes back. Ask about it: not known, no citations. Fetching `docs/legacy-deploy.md` through MCP `fetch_source` reports it is not found.
6. Ask "When are deploys frozen?": the answer quotes the new freeze time and cites `docs/deploy.md` at the new commit.
7. Press re-index again without changing anything: the commit, file count and chunk count are unchanged, and search results are the same as before.

**What would make this fail:** the old file's text is still reachable anywhere; the answer still quotes the old time; the commit does not move; a no-change re-index duplicates or drops chunks.

## Claude Code asks through MCP and gets the same citations

1. With the handbook indexed, connect an MCP client to `/mcp` and list tools: `search`, `ask`, `fetch_source` and `list_sources` are offered.
2. Call `list_sources`: the handbook is there, `ready`, at the expected commit.
3. Call `search` with "rotate API keys" and call `GET /api/search?q=rotate API keys`: both return the same paths, line ranges, commits and links in the same order.
4. Call `ask` with "When are deploys frozen?" and compare with `POST /api/ask`: same known flag, same citations.
5. Call `fetch_source` for the first cited path and line range: the returned lines contain the cited text.

**What would make this fail:** a missing tool, citations that differ between MCP and HTTP, or fetched lines that do not match the citation.

## Everything survives a restart

1. With the handbook indexed and a cited answer obtained, stop the app.
2. Start it again with the same database and data directory.
3. Health is 200. The handbook is still `ready` with the same commit, file count and chunk count.
4. Asking the same question gives the same answer and the same citations, and it was not re-indexed (no new job ran).

**What would make this fail:** sources or chunks held in memory, a migration that fails on a database it already migrated, or an automatic re-index on startup.
