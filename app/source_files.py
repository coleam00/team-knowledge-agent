from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from app import db
from app.config import Settings
from app.ingest.contracts import CloneSelectError, SourceSpec
from app.ingest.repository import read_cached_file
from app.ingest.selection import valid_path
from app.text_lines import physical_lines

type SourceFileErrorCode = Literal[
    "not_found", "invalid_path", "invalid_range", "unavailable", "busy"
]

_MESSAGES: dict[SourceFileErrorCode, str] = {
    "not_found": "Source file not found",
    "invalid_path": "Invalid source file path",
    "invalid_range": "Invalid source line range",
    "unavailable": "Source storage is unavailable",
    "busy": "Source storage is busy",
}


class SourceFileError(Exception):
    def __init__(self, code: SourceFileErrorCode) -> None:
        self.code = code
        super().__init__(_MESSAGES[code])


@dataclass(frozen=True)
class SourceFile:
    source_id: UUID
    path: str
    commit: str
    start_line: int | None
    end_line: int | None
    total_lines: int
    content: str = field(repr=False)


def fetch_source_file(
    source_id: UUID,
    *,
    path: str | None,
    start: int | None = None,
    end: int | None = None,
    settings: Settings,
) -> SourceFile:
    if any(
        value is not None and (type(value) is not int or value <= 0)
        for value in (start, end)
    ) or (start is not None and end is not None and end < start):
        raise SourceFileError("invalid_range")
    if path is None or path == "":
        raise SourceFileError("not_found")
    if not valid_path(path):
        raise SourceFileError("invalid_path")
    try:
        with db.connect(settings.database_url) as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    "SELECT id, url, owner, name, indexed_commit FROM sources "
                    "WHERE id = %s",
                    (source_id,),
                )
                row = cursor.fetchone()
    except (psycopg.Error, ValueError):
        raise SourceFileError("unavailable") from None
    if row is None or row["indexed_commit"] is None:
        raise SourceFileError("not_found")
    commit = row["indexed_commit"]
    try:
        file = read_cached_file(
            SourceSpec(row["id"], row["url"]),
            repo_url=f"https://github.com/{row['owner']}/{row['name']}",
            commit=commit,
            path=path,
            settings=settings,
        )
    except CloneSelectError as error:
        code: SourceFileErrorCode = "unavailable"
        if error.code == "not_found":
            code = "not_found"
        elif error.code == "busy":
            code = "busy"
        raise SourceFileError(code) from None
    lines = physical_lines(file.content)
    first = start if start is not None else 1
    last = min(end, len(lines)) if end is not None else len(lines)
    content = "".join(lines[first - 1 : last])
    return SourceFile(
        source_id,
        path,
        commit,
        first if content else None,
        last if content else None,
        len(lines),
        content,
    )
