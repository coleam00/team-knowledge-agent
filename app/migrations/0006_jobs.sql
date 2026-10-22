CREATE TABLE jobs (
    id uuid PRIMARY KEY,
    source_id uuid NOT NULL REFERENCES sources(id),
    kind text NOT NULL CHECK (kind = 'index'),
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'indexing', 'ready', 'failed')),
    started_at timestamptz,
    finished_at timestamptz,
    error text,
    CHECK (
        (status = 'queued' AND started_at IS NULL
            AND finished_at IS NULL AND error IS NULL)
        OR (status = 'indexing' AND started_at IS NOT NULL
            AND finished_at IS NULL AND error IS NULL)
        OR (status = 'ready' AND started_at IS NOT NULL
            AND finished_at IS NOT NULL AND error IS NULL)
        OR (status = 'failed' AND started_at IS NOT NULL
            AND finished_at IS NOT NULL AND error IS NOT NULL
            AND length(btrim(error)) > 0)
    ),
    CHECK (finished_at >= started_at)
);

CREATE UNIQUE INDEX jobs_active_source ON jobs(source_id)
    WHERE status IN ('queued', 'indexing');

INSERT INTO jobs (id, source_id, kind)
SELECT gen_random_uuid(), id, 'index' FROM sources WHERE status = 'queued';
