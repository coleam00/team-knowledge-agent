ALTER TABLE files ADD CONSTRAINT files_chunk_identity UNIQUE (id, source_id, path);

CREATE TABLE chunks (
    id uuid PRIMARY KEY,
    source_id uuid NOT NULL,
    file_id uuid NOT NULL,
    path text NOT NULL,
    start_line integer NOT NULL CHECK (start_line >= 1),
    end_line integer NOT NULL CHECK (end_line >= start_line),
    content text NOT NULL,
    tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    embedding vector NOT NULL,
    embedding_model text NOT NULL CHECK (btrim(embedding_model) <> ''),
    FOREIGN KEY (file_id, source_id, path) REFERENCES files (id, source_id, path)
);

CREATE INDEX chunks_tsv_idx ON chunks USING gin (tsv);
CREATE INDEX chunks_file_id_idx ON chunks (file_id);
