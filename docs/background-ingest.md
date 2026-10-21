# Background ingestion

POST `/api/sources` commits a source and exactly one queued `index` job together.
It returns the captured queued source without waiting for Git or embeddings.
Duplicate URLs return 409 without creating another job. GET source endpoints
show `queued` -> `indexing` -> `ready`, or `failed` with a safe error.

After migrations, each application lifespan owns one worker thread. It polls
every 250 ms when idle and waits one second after database errors. Multiple
processes can index different sources. PostgreSQL's partial unique jobs index
allows only one queued/indexing job per source. A deterministic per-source session
advisory lock owns the job through claim, preparation and publication; all writes
use that same connection. Direct or session-affine PostgreSQL connections are
required; transaction-pooling proxies are incompatible with this ownership.
Every process sharing a database must share the underlying `TKA_DATA_DIR` storage,
including Git caches and filesystem locks. Separate caches can return file 404s
for otherwise ready sources; ready jobs do not rebuild missing caches.

The worker commits the indexing status and job start time before acquisition.
It clones/selects a pinned commit, chunks each selected file, and embeds each
chunk with the selected model's actual ID. A private temporary JSON-lines spool
under the data directory holds validated file/chunk output. No index rows are
published during model calls. One database transaction then inserts every file
and chunk and commits the source's commit, counts, indexed time and ready status
together with the job's finish time. Empty selections succeed with zero counts;
empty eligible files count as files with zero chunks. Unexpected pre-existing
index data is preserved and causes initial indexing to fail.

Clone, embedding or storage failures leave no partial new snapshot. Source/job
errors use safe fixed messages, never provider bodies or Git stderr. A failed job
does not make health unavailable or stop other sources. Database outages affect
the existing health probe. There is no automatic retry, re-index or new jobs API.
Inspect job timestamps and errors directly:

```sql
SELECT id, source_id, kind, status, started_at, finished_at, error
FROM jobs WHERE source_id = '<source UUID>';
```

Shutdown signals the worker, waits for initialization and joins the thread.
Unclaimed jobs stay queued. Owned work interrupted before publication becomes
failed; a publication already in progress finishes atomically. Shutdown waits for
the current synchronous Git/model call and its existing timeout/cleanup; it has
a 600-second join deadline. Process/connection loss releases session ownership; a
surviving worker marks an abandoned indexing job failed when it acquires that
lock. The disconnected worker cannot publish on a replacement connection.

Restart preserves ready metadata, file/chunk IDs and jobs. It processes existing
queued jobs without manufacturing new ones. Migration 6 backfills one job for
each legacy queued source once, leaving ready/failed sources and stored index
data unchanged. Upgrading therefore starts acquisition and model work for those
queued sources. An older binary refuses unknown migrations: after this upgrade,
recovery requires a forward corrective build, not removing the jobs/ledger or
downgrading to a binary that only knows migration 5.

The existing acquisition lock remains conservative: a crash can leave a cache
lock or temporary preparation file. Inspect ownership before manually removing
stale storage; the worker never steals filesystem locks or sweeps directories.
Local registration accepts bare repositories, but acquisition only supports normal
working trees, so bare sources end failed with an unsupported-layout message.

Configuration (positive limits; overlap must be an integer in [0, window)):

| Environment variable | Default | Behavior |
| --- | --- | --- |
| `TKA_INGEST_MAX_FILES` | 5000 | Fail when selected file count exceeds the limit |
| `TKA_INGEST_MAX_TOTAL_BYTES` | 200000000 | Fail when selected text exceeds the limit |
| `TKA_INGEST_MAX_FILE_BYTES` | 1000000 | Skip larger files; cached reads use the same cap |
| `TKA_INGEST_COMMAND_TIMEOUT_SECONDS` | 120 | Fail when a Git command exceeds its deadline |
| `TKA_CHUNK_WINDOW_LINES` | 80 | Maximum lines in a chunk |
| `TKA_CHUNK_OVERLAP_LINES` | 10 | Overlap within a text/Markdown section |

On startup, nonempty ready sources containing embeddings from a different effective
model become `failed` with an explicit re-index-required error. Stored files,
chunks, commits, counts and completed jobs remain intact; no automatic jobs are
created. Unchanged effective models (including ignored model overrides in fake
mode) preserve ready state. Re-index execution remains a later increment.

Claim row-lock timeouts release ownership and continue to independent sources;
shared database outages retain the worker's backoff.
Selected text still resides in acquisition memory; the spool bounds embedding
memory to one file. Limits do not cap total Git disk use or whole-job duration.

On Windows, the complete suite can run concurrently within the unchanged check
deadline: set `PYTEST_ADDOPTS="-n 8"`, then run the documented unit/harness commands.
The development dependency `pytest-xdist` runs every collected case; no tests or
assertions are skipped by this option. CI can continue running serially on Linux.
