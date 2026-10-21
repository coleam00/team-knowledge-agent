import json
from collections.abc import Sequence
from typing import Annotated
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import TupleRow, dict_row
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    PositiveInt,
    model_validator,
)


class ChunkInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    start_line: PositiveInt
    end_line: PositiveInt
    content: str
    embedding: Annotated[tuple[FiniteFloat, ...], Field(min_length=1)]
    embedding_model: str

    @model_validator(mode="after")
    def validate_metadata(self) -> "ChunkInput":
        if self.end_line < self.start_line:
            raise ValueError("Chunk end_line must not precede start_line")
        if not self.embedding_model.strip(" "):
            raise ValueError("Chunk embedding_model must contain a non-space character")
        return self


class FullTextMatch(ChunkInput):
    id: UUID
    source_id: UUID
    file_id: UUID
    path: str
    rank: float


def replace_file(
    connection: psycopg.Connection[TupleRow],
    *,
    source_id: UUID,
    path: str,
    blob_sha: str,
    language: str,
    chunks: Sequence[ChunkInput],
) -> UUID:
    """Replace one file atomically, retaining its ID and any caller transaction."""
    with connection.transaction():
        row = connection.execute(
            "INSERT INTO files (id, source_id, path, blob_sha, language) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (source_id, path) DO UPDATE "
            "SET blob_sha = EXCLUDED.blob_sha, language = EXCLUDED.language "
            "RETURNING id",
            (uuid4(), source_id, path, blob_sha, language),
        ).fetchone()
        assert row is not None
        file_id = UUID(str(row[0]))
        connection.execute("DELETE FROM chunks WHERE file_id = %s", (file_id,))
        with connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO chunks "
                "(id, source_id, file_id, path, start_line, end_line, content, "
                "embedding, embedding_model) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector, %s)",
                [
                    (
                        uuid4(),
                        source_id,
                        file_id,
                        path,
                        chunk.start_line,
                        chunk.end_line,
                        chunk.content,
                        json.dumps(chunk.embedding, allow_nan=False),
                        chunk.embedding_model,
                    )
                    for chunk in chunks
                ],
            )
        return file_id


def full_text_search(
    connection: psycopg.Connection[TupleRow],
    query: str,
    *,
    source_id: UUID | None = None,
) -> list[FullTextMatch]:
    """Return up to 50 lexical matches, ordered by rank then stable chunk ID."""
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            "SELECT id, source_id, file_id, path, start_line, end_line, content, "
            "embedding::real[] AS embedding, embedding_model, "
            "ts_rank_cd(tsv, query) AS rank "
            "FROM chunks, websearch_to_tsquery('english', %s) AS query "
            "WHERE tsv @@ query AND (%s::uuid IS NULL OR source_id = %s) "
            "ORDER BY rank DESC, id ASC LIMIT 50",
            (query, source_id, source_id),
        )
        return [FullTextMatch.model_validate(row) for row in cursor.fetchall()]
