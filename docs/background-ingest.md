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
no fixed total deadline. Process/connection loss releases session ownership; a
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

See the README for configurable file-count/aggregate-byte/Git-command limits and
chunk window/overlap. Selection's 1,000,000-byte per-file exclusion is fixed.
Selected text still resides in acquisition memory; the spool bounds embedding
memory to one file. Limits do not cap total Git disk use or whole-job duration.
