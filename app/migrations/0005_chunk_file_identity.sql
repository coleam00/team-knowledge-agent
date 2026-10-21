DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'files'::regclass AND conname = 'files_chunk_identity'
    ) THEN
        ALTER TABLE chunks DROP CONSTRAINT chunks_file_id_source_id_path_fkey;
        ALTER TABLE files DROP CONSTRAINT files_chunk_identity;
        ALTER TABLE files ADD CONSTRAINT files_chunk_path UNIQUE (id, path);
        ALTER TABLE files ADD CONSTRAINT files_chunk_source UNIQUE (id, source_id);
        ALTER TABLE chunks ADD FOREIGN KEY (file_id, path) REFERENCES files (id, path);
        ALTER TABLE chunks ADD FOREIGN KEY (file_id, source_id) REFERENCES files (id, source_id);
    END IF;
END
$$;
