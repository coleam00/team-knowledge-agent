CREATE TABLE sources (
    id uuid PRIMARY KEY,
    url text NOT NULL UNIQUE,
    owner text NOT NULL,
    name text NOT NULL,
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'indexing', 'ready', 'failed')),
    indexed_commit text,
    indexed_at timestamptz,
    error text,
    file_count integer NOT NULL DEFAULT 0 CHECK (file_count >= 0),
    chunk_count integer NOT NULL DEFAULT 0 CHECK (chunk_count >= 0),
    created_at timestamptz NOT NULL DEFAULT now()
);
