CREATE TABLE files (
    id uuid PRIMARY KEY,
    source_id uuid NOT NULL,
    path text NOT NULL,
    blob_sha text NOT NULL,
    language text NOT NULL,
    UNIQUE (source_id, path)
);
